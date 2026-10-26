"""Name lookups and the one-time verification that a transfer must use."""
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from .bank_client import NameEnquiryError, bank_client
from .config import settings
from .models import Transaction, VerificationSession, utcnow
from .schemas import VerifyResponse


async def lookup(account_number: str, bank_code: str) -> tuple[str, bool] | None:
    """Account name and fraud flag, or None if the account isn't at this bank."""
    try:
        name = await bank_client.name_enquiry(account_number, bank_code)
    except NameEnquiryError:
        return None
    return name, await bank_client.is_account_flagged(account_number, bank_code)


def record(db: Session, user_id: str, account_number: str, bank_code: str,
           name: str, flagged: bool) -> VerifyResponse:
    seen = db.scalar(select(Transaction.id).where(
        Transaction.user_id == user_id, Transaction.direction == "debit",
        Transaction.counterparty_account == account_number,
        Transaction.counterparty_bank_code == bank_code).limit(1))
    first_time = seen is None  # also check your core beneficiary list here

    warning = None
    if flagged:
        warning = "This account has been reported. Do not send money unless you are certain."
    elif first_time:
        warning = f"You haven't paid {name} before. Confirm the name matches who you intend to pay."

    session = VerificationSession(
        user_id=user_id, account_number=account_number, bank_code=bank_code,
        account_name=name, first_time=first_time,
        expires_at=utcnow() + timedelta(seconds=settings.verification_ttl_seconds))
    db.add(session)
    db.flush()
    return VerifyResponse( verification_id=session.id, account_name=name, first_time_recipient=first_time, flagged=flagged, warning=warning, expires_in_seconds=settings.verification_ttl_seconds)


def consume(db: Session, verification_id: str) -> VerificationSession | None:
    s = db.get(VerificationSession, verification_id)
    if not s or s.consumed or s.expires_at < utcnow():
        return None
    s.consumed = True
    db.commit()
    return s
