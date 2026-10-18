from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import BigInteger, Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

def utcnow() -> datetime:
    """Naive UTC, so SQLite and Postgres behave the same."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

def new_id() -> str:
    return str(uuid4())

class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    external_ref: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(6))            # debit | credit
    amount_kobo: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="NGN")
    channel: Mapped[str] = mapped_column(String(20), default="transfer")  # card | transfer | pos | ...
    merchant_name: Mapped[str | None] = mapped_column(String(200))
    merchant_key: Mapped[str | None] = mapped_column(String(100), index=True)
    counterparty_account: Mapped[str | None] = mapped_column(String(10))
    counterparty_bank_code: Mapped[str | None] = mapped_column(String(10))
    category: Mapped[str | None] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CategoryMemory(Base):
    """Remembers the category a user picked for a recipient, so repeats are one tap."""
    __tablename__ = "category_memory"
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recipient_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    category: Mapped[str] = mapped_column(String(40))

class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "merchant_key"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120))
    merchant_key: Mapped[str] = mapped_column(String(100))
    amount_kobo: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="NGN")
    cycle: Mapped[str] = mapped_column(String(10), default="monthly")
    last_paid_at: Mapped[datetime] = mapped_column(DateTime)
    next_due_at: Mapped[datetime] = mapped_column(DateTime)
    # active | stopped (user stopped tracking) | dismissed (user answered "No", don't ask again)
    status: Mapped[str] = mapped_column(String(10), default="active")
    price_changed: Mapped[bool] = mapped_column(Boolean, default=False)


class VerificationSession(Base):
    """Binds a verified recipient to the transfer so the account can't be swapped after confirmation."""
    __tablename__ = "verification_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    account_number: Mapped[str] = mapped_column(String(10))
    bank_code: Mapped[str] = mapped_column(String(10))
    account_name: Mapped[str] = mapped_column(String(200))
    first_time: Mapped[bool] = mapped_column(Boolean)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
