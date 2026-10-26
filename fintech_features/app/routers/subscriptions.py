from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import subscriptions as subs
from ..db import get_db
from ..deps import get_owned_txn
from ..models import Subscription, Transaction, utcnow
from ..schemas import BackfillResult, SubscriptionConfirm, SubscriptionOut, SubscriptionPatch, SubscriptionSummary
from ..security import get_current_user, require_internal_key

router = APIRouter()


# Subscription identification & tracking 
# Records the customer’s Yes or No to “Is this a subscription?”
@router.post("/v1/subscriptions", response_model=SubscriptionOut | None)
def confirm_subscription(body: SubscriptionConfirm, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    txn = get_owned_txn(db, body.transaction_id, user_id)
    if not txn.merchant_key:
        raise HTTPException(422, "This payment has no merchant to track")
    sub = db.scalar(select(Subscription).where(
        Subscription.user_id == user_id, Subscription.merchant_key == txn.merchant_key))

    if not body.is_subscription:  # "No": remember so we never ask about this merchant again
        if not sub:
            db.add(Subscription( user_id=user_id, name=txn.merchant_name or txn.merchant_key, merchant_key=txn.merchant_key, amount_kobo=txn.amount_kobo, currency=txn.currency, last_paid_at=txn.occurred_at, next_due_at=txn.occurred_at, status="dismissed"))
        elif sub.status == "suggested":
            sub.status = "dismissed"
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

# Returns the subscription tracker and monthly total
@router.get("/v1/subscriptions", response_model=SubscriptionSummary)
def my_subscriptions(db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    rows = db.scalars(select(Subscription).where(
        Subscription.user_id == user_id, Subscription.status.in_(("active", "suggested"))
    ).order_by(Subscription.next_due_at)).all()
    active = [r for r in rows if r.status == "active"]
    now = utcnow()
    suggestions = [r for r in rows if r.status == "suggested" and not subs.is_lapsed(r, now)]
    return SubscriptionSummary(subscriptions=active, suggestions=suggestions, active_count=len(active), estimated_monthly_totals=subs.monthly_totals(active))


def _owned_sub(db: Session, sub_id: str, user_id: str, status: str = "active") -> Subscription:
    sub = db.get(Subscription, sub_id)
    if not sub or sub.user_id != user_id or sub.status != status:
        raise HTTPException(404, "Subscription not found")
    return sub

@router.post("/v1/subscriptions/{sub_id}/confirm", response_model=SubscriptionOut)
def confirm_suggestion(sub_id: str, body: SubscriptionPatch, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    sub = _owned_sub(db, sub_id, user_id, status="suggested")
    sub.status = "active"
    return edit_subscription(sub.id, body, db, user_id)


@router.post("/v1/subscriptions/{sub_id}/dismiss", status_code=204)
def dismiss_suggestion(sub_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    _owned_sub(db, sub_id, user_id, status="suggested").status = "dismissed"
    db.commit()

@router.post("/internal/subscriptions/backfill", response_model=BackfillResult, dependencies=[Depends(require_internal_key)])
def backfill_subscriptions(user_id: str | None = None, days: int = Query(365, ge=1, le=730), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=days)
    user_ids = [user_id] if user_id else db.scalars(select(Transaction.user_id).distinct()).all()
    made = 0
    for uid in user_ids:
        made += subs.backfill(db, uid, since)
        db.commit()
    return BackfillResult(users=len(user_ids), suggested=made)

# Edits a subscription’s name, cycle or next date
# Stops tracking a subscription
@router.patch("/v1/subscriptions/{sub_id}", response_model=SubscriptionOut)
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


@router.delete("/v1/subscriptions/{sub_id}", status_code=204)
def stop_tracking(sub_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    _owned_sub(db, sub_id, user_id).status = "stopped"  # keep the row so we don't re-prompt
    db.commit()
