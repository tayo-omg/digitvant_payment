import random
import shutil
import string
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from app import bank_guess, banks, nuban, ocr, rate_limit
from app.db import SessionLocal
from app.models import Transaction
from tests.conftest import NOW

@pytest.fixture(autouse=True)
def _fresh_limits():
    rate_limit._calls.clear()


needs_tesseract = pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract not installed")


def _number_for(code):
    while True:
        s = "".join(random.choices(string.digits, k=9))
        n = s + str(nuban.check_digit(code, s))
        if not bank_guess.looks_like_phone(n):
            return n


def _paid(user, number, code):
    with SessionLocal() as db:
        db.add(Transaction(external_ref=f"p-{number}", user_id=user, direction="debit", amount_kobo=100,
                           counterparty_account=number, counterparty_bank_code=code,
                           occurred_at=NOW.replace(tzinfo=None)))
        db.commit()


def _image(*lines):
    img = np.full((300 + 100 * len(lines), 1000, 3), 255, np.uint8)
    for i, line in enumerate(lines):
        cv2.putText(img, line, (40, 100 + 110 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3)
    return cv2.imencode(".png", img)[1].tobytes()


def _scan(api, data):
    return api.client.post("/v1/scan/account", files={"image": ("a.png", data, "image/png")}).json()


def test_bank_names_in_text(api):
    assert bank_guess.banks_in_text("Guaranty Trust Bank") == ["058"]
    assert bank_guess.banks_in_text("GTCO plc") == ["058"]
    assert bank_guess.banks_in_text("SUBAQUA dive shop") == []
    assert bank_guess.banks_in_text("Zenlth Bank") == ["057"]


def test_wallets_first_for_phone_shaped_numbers(api):
    names = [b["name"] for b in api.client.get("/v1/banks", params={"account_number": "8031234567"}).json()]
    assert set(names[:4]) == {"OPay", "PalmPay", "Moniepoint MFB", "Kuda MFB"}
    assert len(names) == len(banks.all_banks())


def test_verify_checks_the_bank(api):
    assert api.client.post("/v1/scan/verify", json={"account_number": "8031234567", "bank_code": "999999"}).status_code == 422
    vid = api.client.post("/v1/scan/verify", json={"account_number": "8031234567", "bank_code": "100004"}).json()["verification_id"]
    use = lambda: api.client.post(f"/internal/verifications/{vid}/consume", headers={"X-API-Key": "test-key"})
    assert use().status_code == 200
    assert use().status_code == 410


def test_resolve_is_capped_and_prefers_past_payments(api):
    r = api.client.post("/v1/scan/resolve", json={"account_number": "8031234567"}).json()
    assert len(r["tried_banks"]) == 3
    assert all(banks.get(b["code"]).kind == "wallet" for b in r["tried_banks"])

    number = _number_for("057")
    _paid(api.user, number, "057")
    r = api.client.post("/v1/scan/resolve", json={"account_number": number}).json()
    assert [b["code"] for b in r["tried_banks"]] == ["057"]


@needs_tesseract
def test_scan_stops_after_one_pass_when_clear(api):
    number = _number_for("058")
    with patch.object(ocr, "_read", wraps=ocr._read) as read:
        top = _scan(api, _image("GTBank", number))["candidates"][0]
    assert top["account_number"] == number
    assert top["suggested_bank"]["code"] == "058"
    assert read.call_count == 1


@needs_tesseract
def test_scan_runs_all_passes_when_unclear(api):
    number = _number_for("058")
    with patch.object(ocr, "_read", wraps=ocr._read) as read:
        top = _scan(api, _image("Account number", number))["candidates"][0]
    assert top["account_number"] == number
    assert read.call_count == 4


@needs_tesseract
def test_scan_suggests_bank_from_past_payment(api):
    number = _number_for("058")
    _paid(api.user, number, "058")
    top = _scan(api, _image("Account number", number))["candidates"][0]
    assert top["suggestion_reason"] == "past_payment"


def test_resolve_is_rate_limited(api):
    codes = [api.client.post("/v1/scan/resolve", json={"account_number": "8031234567"}).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
