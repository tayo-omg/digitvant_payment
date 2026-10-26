from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import categories as cat
from . import banks, subscriptions as subs
from .bank_client import bank_client
from .deps import get_owned_txn
from .db import Base, SessionLocal, engine, get_db
from .models import Transaction
from .schemas import CategoryUpdate, PostPaymentPrompts, TransactionWebhook
from .routers import scan as scan_router
from .routers import subscriptions as subscriptions_router
from .security import get_current_user, verify_webhook


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        banks.ensure_loaded(db)
    yield
    await bank_client.aclose()


app = FastAPI(title="Digitvant New Features", version="1.0.0", lifespan=lifespan)
app.include_router(scan_router.router)
app.include_router(subscriptions_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# Transaction ingestion (feeds features 2 and 3)
# Receives each completed transaction from the bank
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
    subs.on_debit(db, txn)
    db.commit()
    return {"id": txn.id, "duplicate": False}


# Payment description & categorisation
# Returns the category list for the picker
@app.get("/v1/categories")
def list_categories(_: str = Depends(get_current_user)):
    return {"expense": cat.EXPENSE_CATEGORIES, "income": cat.INCOME_CATEGORIES}

# Returns the suggested category and whether to ask “Is this a subscription?”
@app.get("/v1/transactions/{txn_id}/post-payment-prompts", response_model=PostPaymentPrompts)
def post_payment_prompts(txn_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    """Call from the payment-success screen: one request returns both prompts."""
    txn = get_owned_txn(db, txn_id, user_id)
    ask, reason = subs.should_prompt(db, txn)
    return PostPaymentPrompts( transaction_id=txn.id, current_category=txn.category,category_suggestion=cat.suggest_category(db, txn), ask_subscription=ask, subscription_reason=reason)

# Saves the category the customer chose
@app.put("/v1/transactions/{txn_id}/category")
def set_category(txn_id: str, body: CategoryUpdate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    if body.category not in cat.ALL_CATEGORIES:
        raise HTTPException(422, "Unknown category")
    txn = get_owned_txn(db, txn_id, user_id)
    txn.category = body.category
    cat.remember_category(db, txn, body.category)
    db.commit()
    return {"id": txn.id, "category": txn.category}
