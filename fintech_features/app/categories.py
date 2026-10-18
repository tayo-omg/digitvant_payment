import re

from sqlalchemy.orm import Session

from .models import CategoryMemory, Transaction

EXPENSE_CATEGORIES = [
    "groceries", "food_dining", "transport", "fuel", "shopping", "education",
    "health", "bills_utilities", "airtime_data", "rent", "family_support",
    "giving", "entertainment", "subscription", "savings_investment", "other",
]
INCOME_CATEGORIES = ["salary", "business_income", "gifts_received", "other_income"]
ALL_CATEGORIES = set(EXPENSE_CATEGORIES + INCOME_CATEGORIES)

_KEYWORDS = {
    "groceries": ["supermarket", "shoprite", "spar", "market", "grocer", "justrite"],
    "transport": ["uber", "bolt", "taxify", "ridesharing", "brt", "lagos ferry"],
    "fuel": ["filling station", "petrol", "oando", "conoil", "total energies", "mobil"],
    "airtime_data": ["mtn", "airtel", "glo ", "9mobile", "airtime", "data bundle"],
    "bills_utilities": ["electric", "disco", "ikeja", "eko dist", "ibedc", "dstv", "gotv", "water"],
    "food_dining": ["restaurant", "chicken republic", "dominos", "kfc", "cafe", "eatery", "jumia food"],
    "health": ["pharmacy", "hospital", "clinic", "medplus", "healthplus"],
    "education": ["school", "university", "tuition", "waec", "jamb"],
    "entertainment": ["cinema", "filmhouse", "genesis", "betting", "bet9ja"],
}


def recipient_key(txn: Transaction) -> str | None:
    if txn.counterparty_account:
        return f"acct:{txn.counterparty_bank_code}:{txn.counterparty_account}"
    if txn.merchant_key:
        return f"m:{txn.merchant_key}"
    return None


def suggest_category(db: Session, txn: Transaction) -> str | None:
    key = recipient_key(txn)
    if key:
        mem = db.get(CategoryMemory, (txn.user_id, key))
        if mem:
            return mem.category
    name = (txn.merchant_name or "").lower()
    for category, words in _KEYWORDS.items():
        if any(w in name for w in words):
            return category
    return None


def remember_category(db: Session, txn: Transaction, category: str) -> None:
    key = recipient_key(txn)
    if not key:
        return
    mem = db.get(CategoryMemory, (txn.user_id, key))
    if mem:
        mem.category = category
    else:
        db.add(CategoryMemory(user_id=txn.user_id, recipient_key=key, category=category))
