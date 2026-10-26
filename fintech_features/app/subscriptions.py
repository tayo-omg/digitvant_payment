import re
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import settings
from .models import Subscription, Transaction, utcnow

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

def _eligible(txn: Transaction) -> bool:
    return txn.direction == "debit" and txn.channel == "card" and bool(txn.merchant_key)

def _existing(db: Session, txn: Transaction) -> Subscription | None:
    return db.scalar(select(Subscription).where(
        Subscription.user_id == txn.user_id, Subscription.merchant_key == txn.merchant_key))

def detect(db: Session, txn: Transaction) -> str | None:
    if not _eligible(txn):
        return None
    if any(k in txn.merchant_key for k in KNOWN_SUBSCRIPTION_KEYS):
        return "known_merchant"
    prior = db.scalars(select(Transaction).where(
        Transaction.user_id == txn.user_id,
        Transaction.merchant_key == txn.merchant_key,
        Transaction.direction == "debit",
        Transaction.id != txn.id,
        Transaction.occurred_at < txn.occurred_at,
    ).order_by(Transaction.occurred_at.desc())).all()
    for p in prior:
        if _within_tolerance(txn.amount_kobo, p.amount_kobo) and detect_cycle(p.occurred_at, txn.occurred_at):
            return "recurring_pattern"
    return None

def should_prompt(db: Session, txn: Transaction) -> tuple[bool, str | None]:
    """Decide whether to show "Is this a subscription?". Avoids nagging on every card payment."""
    if not _eligible(txn):
        return False, None
    existing = _existing(db, txn)
    if existing:
        return (True, "suggested") if existing.status == "suggested" else (False, None)
    reason = detect(db, txn)
    return reason is not None, reason

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
        Subscription.status.in_(("active", "suggested"))))
    if not sub or txn.occurred_at <= sub.last_paid_at:
        return
    sub.price_changed = not _within_tolerance(txn.amount_kobo, sub.amount_kobo)
    sub.amount_kobo = txn.amount_kobo
    sub.last_paid_at = txn.occurred_at
    sub.next_due_at = add_cycle(txn.occurred_at, sub.cycle)


def is_lapsed(sub: Subscription, now: datetime) -> bool:
    return sub.next_due_at + timedelta(days=_CYCLE_DAYS[sub.cycle][1]) < now


def suggest(db: Session, txn: Transaction) -> Subscription | None:
    if _existing(db, txn):
        return None
    reason = detect(db, txn)
    if not reason:
        return None
    cycle = guess_cycle(db, txn)
    sub = Subscription(
        user_id=txn.user_id, name=txn.merchant_name or txn.merchant_key, merchant_key=txn.merchant_key,
        amount_kobo=txn.amount_kobo, currency=txn.currency, cycle=cycle, last_paid_at=txn.occurred_at,
        next_due_at=add_cycle(txn.occurred_at, cycle), status="suggested")
    if is_lapsed(sub, utcnow()):
        return None
    db.add(sub)
    db.flush()
    return sub


def on_debit(db: Session, txn: Transaction) -> None:
    apply_payment(db, txn)
    suggest(db, txn)


def backfill(db: Session, user_id: str, since: datetime) -> int:
    txns = db.scalars(select(Transaction).where(
        Transaction.user_id == user_id, Transaction.direction == "debit",
        Transaction.merchant_key.is_not(None), Transaction.occurred_at >= since,
    ).order_by(Transaction.occurred_at)).all()
    made = 0
    for txn in txns:
        apply_payment(db, txn)
        made += suggest(db, txn) is not None
    return made


def monthly_totals(rows: list[Subscription]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for r in rows:
        totals[r.currency] = totals.get(r.currency, 0) + monthly_equivalent(r.amount_kobo, r.cycle)
    return totals
