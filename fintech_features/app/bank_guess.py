"""Guess which bank an account number belongs to, so the user doesn't have to pick one."""
import time
from difflib import get_close_matches
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from . import banks
from .models import Transaction
from .nuban import is_valid

_PHONE_PREFIXES = ("70", "80", "81", "90", "91")  # wallet numbers are often a phone number
_FUZZY_CUTOFF = 0.8
_POPULAR_TTL = 3600
_popular_cache: tuple[float, list[str]] = (0.0, [])


# Number rules

def looks_like_phone(account_number: str) -> bool:
    return account_number[:2] in _PHONE_PREFIXES


def passing_banks(account_number: str) -> list[dict]:
    """Banks whose check digit this number passes. A hint, not proof."""
    return [banks.out(b) for b in banks.all_banks()
            if b.check_digit and is_valid(account_number, b.code)]


def _wallets() -> list[banks.BankInfo]:
    return [b for b in banks.all_banks() if b.kind == "wallet"]


def picker_banks(account_number: str) -> list[dict]:
    wallets = [banks.out(b) for b in _wallets()] if looks_like_phone(account_number) else []
    return wallets + passing_banks(account_number)


def _possible_codes(account_number: str) -> list[str]:
    no_rule = [b.code for b in banks.all_banks() if not b.check_digit]
    return list(dict.fromkeys([b["code"] for b in picker_banks(account_number)] + no_rule))


# Bank names in the photo

def _lookup_names(words: list[str]) -> list[str]:
    return [c for i in range(len(words)) for n in range(1, banks.longest_name + 1)
            if i + n <= len(words)
            for c in banks.name_index.get(" ".join(words[i:i + n]), ())]


def banks_in_text(text: str) -> list[str]:
    words = banks.normalise(text).split()
    found = _lookup_names(words)
    if not found:  # try again with misread words fixed, e.g. "zenlth" -> "zenith"
        known = set(banks.name_words)
        fixes = {}
        for w in set(words):
            if len(w) >= 5 and w not in known:
                close = get_close_matches(w, banks.name_words, n=1, cutoff=_FUZZY_CUTOFF)
                if close:
                    fixes[w] = close[0]
        if fixes:
            found = _lookup_names([fixes.get(w, w) for w in words])
    return list(dict.fromkeys(found))


# Payment history

def past_payment_bank(db: Session, user_id: str, account_number: str) -> banks.BankInfo | None:
    code = db.scalar(select(Transaction.counterparty_bank_code).where(
        Transaction.user_id == user_id, Transaction.direction == "debit",
        Transaction.counterparty_account == account_number,
        Transaction.counterparty_bank_code.is_not(None),
    ).order_by(Transaction.occurred_at.desc()).limit(1))
    return banks.get(code)


def _bank_counts(db: Session, user_id: str | None) -> list[str]:
    q = select(Transaction.counterparty_bank_code).where(
        Transaction.direction == "debit", Transaction.counterparty_bank_code.is_not(None))
    if user_id:
        q = q.where(Transaction.user_id == user_id)
    q = (q.group_by(Transaction.counterparty_bank_code)
         .order_by(func.count().desc()).limit(50))
    return [b.code for b in map(banks.get, db.scalars(q)) if b]


def _popular_banks(db: Session) -> list[str]:
    global _popular_cache
    if time.monotonic() - _popular_cache[0] > _POPULAR_TTL:
        _popular_cache = (time.monotonic(), _bank_counts(db, None))
    return _popular_cache[1]


# Putting it together

def suggest_bank(db: Session, user_id: str, account_number: str,
                 text_codes: list[str]) -> tuple[dict | None, str | None]:
    past = past_payment_bank(db, user_id, account_number)
    if past:
        return banks.out(past), "past_payment"
    possible = set(_possible_codes(account_number))
    in_text = [c for c in text_codes if c in possible]
    if len(in_text) == 1:
        return banks.out(banks.get(in_text[0])), "photo_text"
    passing = passing_banks(account_number)
    if len(passing) == 1 and not looks_like_phone(account_number):
        return passing[0], "only_match"
    return None, None


def is_clear_read(db: Session, user_id: str, hits: dict[str, int], text: str) -> bool:
    """One number, and either the user paid it before or it fits a bank named in the photo.
    A misread digit almost never still fits the named bank's check digit."""
    if len(hits) != 1:
        return False
    number = next(iter(hits))
    if past_payment_bank(db, user_id, number):
        return True
    passing = {b["code"] for b in passing_banks(number)}
    return any(c in passing for c in banks_in_text(text))


def build_candidates(db: Session, user_id: str, hits: dict[str, int], text: str) -> list[dict]:
    """Turn OCR reads into the top 5 candidates, each with its likely banks."""
    text_codes = banks_in_text(text)
    results = []
    for number, count in hits.items():
        picker = picker_banks(number)
        hint = next((b for b in picker if b["code"] in text_codes), None)
        bank, reason = suggest_bank(db, user_id, number, text_codes)
        score = (count + (2 if passing_banks(number) else 0) + (2 if hint else 0)
                 + (3 if reason == "past_payment" else 0))
        results.append({
            "account_number": number,
            "possible_banks": picker,
            "bank_hint": hint["name"] if hint else None,
            "score": score,
            "suggested_bank": bank,
            "suggestion_reason": reason,
        })
    return sorted(results, key=lambda r: r["score"], reverse=True)[:5]


def rank_banks(db: Session, user_id: str, account_number: str,
               preferred_codes: list[str]) -> list[dict]:
    """Banks to try a name lookup at, most likely first."""
    past = past_payment_bank(db, user_id, account_number)
    if past:
        return [banks.out(past)]
    possible = _possible_codes(account_number)
    allowed = set(possible)
    preferred = [b.code for b in map(banks.get, preferred_codes) if b]
    wallets = [b.code for b in _wallets()] if looks_like_phone(account_number) else []
    order: dict[str, None] = {}
    for source in (preferred, wallets, _bank_counts(db, user_id), _popular_banks(db), possible):
        order.update((c, None) for c in source if c in allowed)
    return [banks.out(banks.get(c)) for c in order]
