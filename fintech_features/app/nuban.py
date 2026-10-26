"""NUBAN check-digit validation (CBN standard)."""

_WEIGHTS = (3, 7, 3, 3, 7, 3, 3, 7, 3, 3, 7, 3)


def check_digit(bank_code: str, serial: str) -> int:
    digits = bank_code + serial
    total = sum(int(d) * w for d, w in zip(digits, _WEIGHTS))
    return (10 - total % 10) % 10


def is_valid(account_number: str, bank_code: str) -> bool:
    if not (account_number.isdigit() and len(account_number) == 10):
        return False
    if not (bank_code.isdigit() and len(bank_code) == 3):
        return False
    return check_digit(bank_code, account_number[:9]) == int(account_number[9])
