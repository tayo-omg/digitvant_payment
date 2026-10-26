"""Scan-to-Pay endpoints."""
import asyncio
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from .. import bank_guess, banks, nuban, ocr, verification
from ..bank_client import NameEnquiryError, bank_client
from ..config import settings
from ..db import get_db
from ..schemas import (BankOut, OcrCandidate, ResolveMatch, ResolveRequest, ResolveResponse,
                       ScanResponse, VerificationInternal, VerifyRequest, VerifyResponse)
from ..rate_limit import limit
from ..security import get_current_user, require_internal_key

router = APIRouter()

ALLOWED_IMAGES = {"image/jpeg", "image/png", "image/webp"}


@router.get("/v1/banks", response_model=list[BankOut])
def list_banks(account_number: str | None = None, _: str = Depends(get_current_user)):
    """All banks, with the likely ones for account_number first."""
    first = bank_guess.picker_banks(account_number) if account_number and len(account_number) == 10 else []
    first_codes = {b["code"] for b in first}
    return first + [banks.out(b) for b in banks.all_banks() if b.code not in first_codes]


@router.post("/v1/scan/account", response_model=ScanResponse)
async def scan_account(image: UploadFile = File(...), db: Session = Depends(get_db), user_id: str = Depends(limit("scan", 10))):
    """Step 1: image -> candidate account numbers. The image is never stored."""
    if image.content_type not in ALLOWED_IMAGES:
        raise HTTPException(415, "Upload a JPEG, PNG or WebP image")
    data = await image.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "Image too large")
    try:
        hits, text = await run_in_threadpool(  # CPU-bound, keep off the event loop
            ocr.scan_image, data, lambda h, t: bank_guess.is_clear_read(db, user_id, h, t))
    except ValueError:
        raise HTTPException(422, "Could not read the image")
    results = bank_guess.build_candidates(db, user_id, hits, text)
    msg = ("Tap the correct account number" if results
           else "No account number found. Try again in better light or type it manually")
    return ScanResponse(candidates=[OcrCandidate(**r) for r in results], message=msg)


@router.post("/v1/scan/verify", response_model=VerifyResponse)
async def verify_recipient(body: VerifyRequest, db: Session = Depends(get_db), user_id: str = Depends(limit("verify", 10))):
    """Step 2: user confirmed number + bank (possibly after editing). Run name enquiry."""
    bank = banks.get(body.bank_code)
    if not bank:
        raise HTTPException(422, "Unknown bank")
    if bank.check_digit and not nuban.is_valid(body.account_number, bank.code):
        raise HTTPException(422, "Account number doesn't match the selected bank. Check the digits.")
    try:
        name = await bank_client.name_enquiry(body.account_number, bank.code)
    except NameEnquiryError as exc:
        raise HTTPException(422, str(exc))
    flagged = await bank_client.is_account_flagged(body.account_number, bank.code)
    resp = verification.record(db, user_id, body.account_number, bank.code, name, flagged)
    db.commit()
    return resp


@router.post("/v1/scan/resolve", response_model=ResolveResponse)
async def resolve_recipient(body: ResolveRequest, db: Session = Depends(get_db), user_id: str = Depends(limit("resolve", 5))):
    """Step 2 without a bank: try the likeliest banks at once and let the user tap the name."""
    tried = bank_guess.rank_banks(db, user_id, body.account_number, body.preferred_bank_codes)
    tried = tried[:settings.resolve_max_lookups]
    found = await asyncio.gather(*(verification.lookup(body.account_number, b["code"]) for b in tried))
    matches = [
        ResolveMatch(bank=BankOut(**b), **verification.record(
            db, user_id, body.account_number, b["code"], hit[0], hit[1]).model_dump())
        for b, hit in zip(tried, found) if hit]
    db.commit()
    if not matches:
        msg = "We couldn't find this account at the likely banks. Pick the bank yourself."
    elif len(matches) == 1:
        msg = "Confirm this is who you want to pay"
    else:
        msg = "Tap the person you want to pay"
    return ResolveResponse(matches=matches, tried_banks=[BankOut(**b) for b in tried], message=msg)


@router.post("/internal/verifications/{verification_id}/consume",
             response_model=VerificationInternal, dependencies=[Depends(require_internal_key)])
def consume_verification(verification_id: str, db: Session = Depends(get_db)):
    """Called by the existing transfer service right before debiting. One-time use, so the
    verified name/account can't be swapped for a different one after the user confirmed."""
    s = verification.consume(db, verification_id)
    if not s:
        raise HTTPException(410, "Verification expired or already used")
    return VerificationInternal(user_id=s.user_id, account_number=s.account_number, bank_code=s.bank_code, account_name=s.account_name)
