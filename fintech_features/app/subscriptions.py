import re
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Subscription, Transaction

KNOWN_SUBSCRIPTION_KEYS = (
    "netflix", "spotify", "apple com bill", "icloud", "youtube", "showmax", "canva",
    "adobe", "microsoft", "google one", "openai", "chatgpt", "amazon prime", "disney",
    "audiomack", "apple music", "dropbox", "notion", "zoom", "linkedin",
)
_NOISE = {"com", "www", "pos", "web", "payment", "ng", "ngr", "lagos", "ltd", "limited", "inc"}
# cycle -> (expected days, tolerance in days)
_CYCLE_DAYS = {"weekly": (7, 1), "monthly": (30, 4), "quarterly": (91, 6), "annual": (365, 10)}


def normalize_merchant(name: str | None) -> str | None:
    if not name:
        return None
    tokens = [t for t in re.sub(r"[^a-z ]", " ", name.lower()).split() if t not in _NOISE]
    return " ".join(tokens[:3]) or None


def add_cycle(dt: datetime, cycle: str) -> datetime:
    return dt + {
        "weekly": timedelta(days=7),
        "monthly": relativedelta(months=1),
        "quarterly": relativedelta(months=3),
        "annual": relativedelta(years=1),
    }[cycle]


def monthly_equivalent(amount_kobo: int, cycle: str) -> int:
    factor = {"weekly": 52 / 12, "monthly": 1, "quarterly": 1 / 3, "annual": 1 / 12}[cycle]
    return round(amount_kobo * factor)


def detect_cycle(earlier: datetime, later: datetime) -> str | None:
    gap = (later - earlier).days
    for cycle, (days, tol) in _CYCLE_DAYS.items():
        if abs(gap - days) <= tol:
            return cycle
    return None


def _within_tolerance(a: int, b: int) -> bool:
    return b > 0 and abs(a - b) / b <= settings.subscription_amount_tolerance


def should_prompt(db: Session, txn: Transaction) -> tuple[bool, str | None]:
    """Decide whether to show "Is this a subscription?". Avoids nagging on every card payment."""
    if txn.direction != "debit" or txn.channel != "card" or not txn.merchant_key:
        return False, None
    existing = db.scalar(select(Subscription).where(
        Subscription.user_id == txn.user_id, Subscription.merchant_key == txn.merchant_key))
    if existing:  # already tracked, stopped, or answered "No"
        return False, None
    if any(k in txn.merchant_key for k in KNOWN_SUBSCRIPTION_KEYS):
        return True, "known_merchant"
    prior = db.scalars(select(Transaction).where(
        Transaction.user_id == txn.user_id,
        Transaction.merchant_key == txn.merchant_key,
        Transaction.direction == "debit",
        Transaction.id != txn.id,
        Transaction.occurred_at < txn.occurred_at,
    ).order_by(Transaction.occurred_at.desc())).all()
    for p in prior:
        if _within_tolerance(txn.amount_kobo, p.amount_kobo) and detect_cycle(p.occurred_at, txn.occurred_at):
            return True, "recurring_pattern"
    return False, None


def guess_cycle(db: Session, txn: Transaction) -> str:
    prev = db.scalar(select(Transaction).where(
        Transaction.user_id == txn.user_id, Transaction.merchant_key == txn.merchant_key,
        Transaction.id != txn.id, Transaction.occurred_at < txn.occurred_at,
    ).order_by(Transaction.occurred_at.desc()))
    return (detect_cycle(prev.occurred_at, txn.occurred_at) if prev else None) or "monthly"


def apply_payment(db: Session, txn: Transaction) -> None:
    """Called for each new debit: roll an active subscription forward and flag price changes."""
    if txn.direction != "debit" or not txn.merchant_key:
        return
    sub = db.scalar(select(Subscription).where(
        Subscription.user_id == txn.user_id, Subscription.merchant_key == txn.merchant_key,
        Subscription.status == "active"))
    if not sub or txn.occurred_at <= sub.last_paid_at:
        return
    sub.price_changed = not _within_tolerance(txn.amount_kobo, sub.amount_kobo)
    sub.amount_kobo = txn.amount_kobo
    sub.last_paid_at = txn.occurred_at
    sub.next_due_at = add_cycle(txn.occurred_at, sub.cycle)
