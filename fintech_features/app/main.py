from contextlib import asynccontextmanager
from datetime import timedelta
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import categories as cat
from . import nuban, ocr, subscriptions as subs
from .bank_client import NameEnquiryError, bank_client
from .config import settings
from .db import Base, engine, get_db
from .models import Subscription, Transaction, VerificationSession, utcnow
from .schemas import ( CategoryUpdate, OcrCandidate, PostPaymentPrompts, ScanResponse, SubscriptionConfirm, SubscriptionOut, SubscriptionPatch, SubscriptionSummary, TransactionWebhook, VerificationInternal, VerifyRequest, VerifyResponse)
from .security import get_current_user, require_internal_key, verify_webhook


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield
    await bank_client.aclose()


app = FastAPI(title="Digitvant New Features", version="1.0.0", lifespan=lifespan)

ALLOWED_IMAGES = {"image/jpeg", "image/png", "image/webp"}


def _get_txn(db: Session, txn_id: str, user_id: str) -> Transaction:
    txn = db.get(Transaction, txn_id)
    if not txn or txn.user_id != user_id:  # same 404 either way, no ID probing
        raise HTTPException(404, "Transaction not found")
    return txn


@app.get("/health")
def health():
    return {"status": "ok"}


# Scan-to-Pay 

@app.post("/v1/scan/account", response_model=ScanResponse)
async def scan_account(image: UploadFile = File(...), user_id: str = Depends(get_current_user)):
    """Step 1: image -> candidate account numbers. The image is never stored."""
    if image.content_type not in ALLOWED_IMAGES:
        raise HTTPException(415, "Upload a JPEG, PNG or WebP image")
    data = await image.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "Image too large")
    try:
        results = await run_in_threadpool(ocr.scan_image, data)  # CPU-bound, keep off the event loop
    except ValueError:
        raise HTTPException(422, "Could not read the image")
    msg = ("Tap the correct account number" if results
           else "No account number found. Try again in better light or type it manually")
    return ScanResponse(candidates=[OcrCandidate(**r) for r in results], message=msg)


@app.post("/v1/scan/verify", response_model=VerifyResponse)
async def verify_recipient(body: VerifyRequest, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    """Step 2: user confirmed number + bank (possibly after editing). Run name enquiry."""
    if len(body.bank_code) == 3 and not nuban.is_valid(body.account_number, body.bank_code):
        raise HTTPException(422, "Account number doesn't match the selected bank. Check the digits.")
    try:
        name = await bank_client.name_enquiry(body.account_number, body.bank_code)
    except NameEnquiryError as exc:
        raise HTTPException(422, str(exc))

    flagged = await bank_client.is_account_flagged(body.account_number, body.bank_code)
    seen = db.scalar(select(Transaction.id).where(
        Transaction.user_id == user_id, Transaction.direction == "debit",
        Transaction.counterparty_account == body.account_number,
        Transaction.counterparty_bank_code == body.bank_code).limit(1))
    first_time = seen is None  # also check your core beneficiary list here

    warning = None
    if flagged:
        warning = "This account has been reported. Do not send money unless you are certain."
    elif first_time:
        warning = f"You haven't paid {name} before. Confirm the name matches who you intend to pay."

    session = VerificationSession(
        user_id=user_id, account_number=body.account_number, bank_code=body.bank_code,
        account_name=name, first_time=first_time,
        expires_at=utcnow() + timedelta(seconds=settings.verification_ttl_seconds))
    db.add(session)
    db.commit()
    return VerifyResponse( verification_id=session.id, account_name=name, first_time_recipient=first_time, flagged=flagged, warning=warning, expires_in_seconds=settings.verification_ttl_seconds)


@app.post("/internal/verifications/{verification_id}/consume",
          response_model=VerificationInternal, dependencies=[Depends(require_internal_key)])
def consume_verification(verification_id: str, db: Session = Depends(get_db)):
    """Called by the existing transfer service right before debiting. One-time use, so the
    verified name/account can't be swapped for a different one after the user confirmed."""
    s = db.get(VerificationSession, verification_id)
    if not s or s.consumed or s.expires_at < utcnow():
        raise HTTPException(410, "Verification expired or already used")
    s.consumed = True
    db.commit()
    return VerificationInternal(user_id=s.user_id, account_number=s.account_number, bank_code=s.bank_code, account_name=s.account_name)


# Transaction ingestion (feeds features 2 and 3)

@app.post("/v1/webhooks/transactions", dependencies=[Depends(verify_webhook)], status_code=201)
def ingest_transaction(payload: TransactionWebhook, db: Session = Depends(get_db)):
    existing = db.scalar(select(Transaction).where(Transaction.external_ref == payload.external_ref))
    if existing:  # idempotent: the core system may retry
        return {"id": existing.id, "duplicate": True}
    txn = Transaction(
        external_ref=payload.external_ref, user_id=payload.user_id, direction=payload.direction,
        amount_kobo=payload.amount_kobo, currency=payload.currency, channel=payload.channel,
        merchant_name=payload.merchant_name,
        merchant_key=subs.normalize_merchant(payload.merchant_name),
        counterparty_account=payload.counterparty_account,
        counterparty_bank_code=payload.counterparty_bank_code,
        occurred_at=payload.occurred_at.replace(tzinfo=None) if payload.occurred_at.tzinfo is None
        else payload.occurred_at.astimezone(None).replace(tzinfo=None))
    db.add(txn)
    db.flush()
    subs.apply_payment(db, txn)
    db.commit()
    return {"id": txn.id, "duplicate": False}


# Payment description & categorisation

@app.get("/v1/categories")
def list_categories(_: str = Depends(get_current_user)):
    return {"expense": cat.EXPENSE_CATEGORIES, "income": cat.INCOME_CATEGORIES}


@app.get("/v1/transactions/{txn_id}/post-payment-prompts", response_model=PostPaymentPrompts)
def post_payment_prompts(txn_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    """Call from the payment-success screen: one request returns both prompts."""
    txn = _get_txn(db, txn_id, user_id)
    ask, reason = subs.should_prompt(db, txn)
    return PostPaymentPrompts( transaction_id=txn.id, current_category=txn.category,category_suggestion=cat.suggest_category(db, txn), ask_subscription=ask, subscription_reason=reason)


@app.put("/v1/transactions/{txn_id}/category")
def set_category(txn_id: str, body: CategoryUpdate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    if body.category not in cat.ALL_CATEGORIES:
        raise HTTPException(422, "Unknown category")
    txn = _get_txn(db, txn_id, user_id)
    txn.category = body.category
    cat.remember_category(db, txn, body.category)
    db.commit()
    return {"id": txn.id, "category": txn.category}


# Subscription identification & tracking 

@app.post("/v1/subscriptions", response_model=SubscriptionOut | None)
def confirm_subscription(body: SubscriptionConfirm, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    txn = _get_txn(db, body.transaction_id, user_id)
    if not txn.merchant_key:
        raise HTTPException(422, "This payment has no merchant to track")
    sub = db.scalar(select(Subscription).where(
        Subscription.user_id == user_id, Subscription.merchant_key == txn.merchant_key))

    if not body.is_subscription:  # "No": remember so we never ask about this merchant again
        if not sub:
            db.add(Subscription( user_id=user_id, name=txn.merchant_name or txn.merchant_key, merchant_key=txn.merchant_key, amount_kobo=txn.amount_kobo, currency=txn.currency, last_paid_at=txn.occurred_at, next_due_at=txn.occurred_at, status="dismissed"))
            db.commit()
        return None

    cycle = body.cycle or subs.guess_cycle(db, txn)
    fields = dict( name=body.name or txn.merchant_name or txn.merchant_key, amount_kobo=txn.amount_kobo, currency=txn.currency, cycle=cycle, last_paid_at=txn.occurred_at, next_due_at=subs.add_cycle(txn.occurred_at, cycle), status="active", price_changed=False)
    if sub:
        for k, v in fields.items():
            setattr(sub, k, v)
    else:
        sub = Subscription(user_id=user_id, merchant_key=txn.merchant_key, **fields)
        db.add(sub)
    if not txn.category:
        txn.category = "subscription"
    db.commit()
    return sub


@app.get("/v1/subscriptions", response_model=SubscriptionSummary)
def my_subscriptions(db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    rows = db.scalars(select(Subscription).where(
        Subscription.user_id == user_id, Subscription.status == "active"
    ).order_by(Subscription.next_due_at)).all()
    total = sum(subs.monthly_equivalent(r.amount_kobo, r.cycle) for r in rows)
    return SubscriptionSummary(subscriptions=rows, active_count=len(rows), estimated_monthly_total_kobo=total)


def _owned_sub(db: Session, sub_id: str, user_id: str) -> Subscription:
    sub = db.get(Subscription, sub_id)
    if not sub or sub.user_id != user_id or sub.status != "active":
        raise HTTPException(404, "Subscription not found")
    return sub


@app.patch("/v1/subscriptions/{sub_id}", response_model=SubscriptionOut)
def edit_subscription(sub_id: str, body: SubscriptionPatch, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    sub = _owned_sub(db, sub_id, user_id)
    if body.name:
        sub.name = body.name
    if body.cycle:
        sub.cycle = body.cycle
        sub.next_due_at = subs.add_cycle(sub.last_paid_at, body.cycle)
    if body.next_due_at:
        sub.next_due_at = body.next_due_at.replace(tzinfo=None)
    db.commit()
    return sub


@app.delete("/v1/subscriptions/{sub_id}", status_code=204)
def stop_tracking(sub_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    _owned_sub(db, sub_id, user_id).status = "stopped"  # keep the row so we don't re-prompt
    db.commit()
