"""Server-side OCR for account numbers (Tesseract + OpenCV).

Prefer on-device OCR (ML Kit / Apple Vision) in the mobile app and send only the
extracted text to /v1/scan/verify. This module is the fallback for web, old
devices, or when the app uploads a photo. Images are processed in memory only.
"""
import re

import cv2
import numpy as np
import pytesseract

from .nuban import BANKS, candidate_banks

# 10 chars made of digits or common OCR look-alikes, optionally separated by space/dash.
_PATTERN = re.compile(r"(?<![\dA-Za-z])(?:[\dOoIl|SB][ \-]?){9}[\dOoIl|SB](?![\dA-Za-z])")
_FIXES = str.maketrans("OoIl|SB", "0011158")


def _preprocess(image_bytes: bytes) -> list[np.ndarray]:
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Unreadable image")
    h, w = img.shape[:2]
    if max(h, w) < 1400:  # upscale small photos
        s = 1400 / max(h, w)
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    elif max(h, w) > 3000:
        s = 3000 / max(h, w)
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    gray = cv2.fastNlMeansDenoising(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None, 10)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
    )
    return [gray, thresh]


def _extract_numbers(text: str) -> list[str]:
    found = []
    for m in _PATTERN.finditer(text):
        raw = re.sub(r"[ \-]", "", m.group())
        if sum(c.isdigit() for c in raw) < 7:  # avoid matching words like "BOSSIBLISS"
            continue
        fixed = raw.translate(_FIXES)
        if fixed.isdigit() and len(fixed) == 10:
            found.append(fixed)
    return found


def scan_image(image_bytes: bytes) -> list[dict]:
    texts = []
    for variant in _preprocess(image_bytes):
        for psm in (6, 11):  # block of text, sparse text
            texts.append(pytesseract.image_to_string(variant, config=f"--oem 3 --psm {psm}"))

    hits: dict[str, int] = {}
    for t in texts:
        for n in set(_extract_numbers(t)):
            hits[n] = hits.get(n, 0) + 1

    joined = " ".join(texts).lower()
    bank_hints = [code for code, name in BANKS.items() if name.lower() in joined]

    results = []
    for number, count in hits.items():
        banks = candidate_banks(number)
        hint = next((b for b in banks if b["code"] in bank_hints), None)
        score = count + (2 if banks else 0) + (2 if hint else 0)
        results.append({
            "account_number": number,
            "possible_banks": banks,
            "bank_hint": hint["name"] if hint else None,
            "score": score,
        })
    return sorted(results, key=lambda r: r["score"], reverse=True)[:5]
