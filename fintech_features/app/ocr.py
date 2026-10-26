"""Server-side OCR for account numbers

Prefer on-device OCR (ML Kit / Apple Vision) in the mobile app and send only the
extracted text to /v1/scan/verify. This module is the fallback for web, old
devices, or when the app uploads a photo. Images are processed in memory only.
"""
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import cv2
import numpy as np
import pytesseract

# 10 chars made of digits or common OCR look-alikes, optionally separated by space/dash.
_PATTERN = re.compile(r"(?<![\dA-Za-z])(?:[\dOoIl|SB][ \-]?){9}[\dOoIl|SB](?![\dA-Za-z])")
_FIXES = str.maketrans("OoIl|SB", "0011158")
# shared, so a busy server doesn't start too many Tesseract runs at once
_pool = ThreadPoolExecutor(max_workers=os.cpu_count() or 4)

def _preprocess(image_bytes: bytes) -> list[np.ndarray]:
    # pylint: disable=no-member
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


def _read(image: np.ndarray, psm: int) -> str:
    return pytesseract.image_to_string(image, config=f"--oem 3 --psm {psm}")


def _count(texts: list[str]) -> tuple[dict[str, int], str]:
    hits: dict[str, int] = {}
    for t in texts:
        for n in set(_extract_numbers(t)):
            hits[n] = hits.get(n, 0) + 1
    return hits, " ".join(texts)


def scan_image(image_bytes: bytes,
               good_enough: Callable[[dict[str, int], str], bool] = lambda h, t: False,
               ) -> tuple[dict[str, int], str]:
    """Returns each account number found with how many passes read it, and all the text.
    Stops after the first pass if good_enough says its result is clear."""
    gray, thresh = _preprocess(image_bytes)
    texts = [_read(gray, 6)]  # block of text
    first = _count(texts)
    if good_enough(*first):
        return first
    rest = [(gray, 11), (thresh, 6), (thresh, 11)]  # sparse text, and the black-and-white copy
    texts += _pool.map(lambda p: _read(*p), rest)
    return _count(texts)
