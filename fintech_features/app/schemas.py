from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Cycle = Literal["weekly", "monthly", "quarterly", "annual"]


class BankOut(BaseModel):
    code: str
    name: str


class OcrCandidate(BaseModel):
    account_number: str
    possible_banks: list[BankOut]
    bank_hint: str | None
    score: int


class ScanResponse(BaseModel):
    candidates: list[OcrCandidate]
    message: str


class VerifyRequest(BaseModel):
    account_number: str = Field(pattern=r"^\d{10}$")
    bank_code: str = Field(pattern=r"^\d{3,6}$")


class VerifyResponse(BaseModel):
    verification_id: str
    account_name: str
    first_time_recipient: bool
    flagged: bool
    warning: str | None
    expires_in_seconds: int


class VerificationInternal(BaseModel):
    user_id: str
    account_number: str
    bank_code: str
    account_name: str


class TransactionWebhook(BaseModel):
    external_ref: str
    user_id: str
    direction: Literal["debit", "credit"]
    amount_kobo: int = Field(gt=0)
    currency: str = "NGN"
    channel: str = "transfer"
    merchant_name: str | None = None
    counterparty_account: str | None = None
    counterparty_bank_code: str | None = None
    occurred_at: datetime


class PostPaymentPrompts(BaseModel):
    transaction_id: str
    current_category: str | None
    category_suggestion: str | None
    ask_subscription: bool
    subscription_reason: str | None


class CategoryUpdate(BaseModel):
    category: str


class SubscriptionConfirm(BaseModel):
    transaction_id: str
    is_subscription: bool
    name: str | None = Field(default=None, max_length=120)
    cycle: Cycle | None = None


class SubscriptionPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    cycle: Cycle | None = None
    next_due_at: datetime | None = None


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    amount_kobo: int
    currency: str
    cycle: str
    last_paid_at: datetime
    next_due_at: datetime
    price_changed: bool


class SubscriptionSummary(BaseModel):
    subscriptions: list[SubscriptionOut]
    active_count: int
    estimated_monthly_total_kobo: int
