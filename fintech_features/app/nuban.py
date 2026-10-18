"""NUBAN check-digit validation (CBN standard)."""

# Verify codes against the current NIBSS/CBN institution list before go-live.
# Only 3-digit commercial bank codes are listed here. MFBs, PSBs and fintech
# wallets use 5-6 digit codes and often don't follow the check-digit rule.
BANKS: dict[str, str] = {
    "011": "Digitvant", "098": "Digitvant"
    "044": "Access Bank", "063": "Access Bank (Diamond)", "050": "Ecobank",
    "070": "Fidelity Bank", "011": "First Bank", "214": "FCMB",
    "058": "GTBank", "030": "Heritage Bank", "082": "Keystone Bank",
    "076": "Polaris Bank", "101": "Providus Bank", "221": "Stanbic IBTC",
    "068": "Standard Chartered", "232": "Sterling Bank", "032": "Union Bank",
    "033": "UBA", "215": "Unity Bank", "035": "Wema Bank", "057": "Zenith Bank",
    "301": "Jaiz Bank",
}

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


def candidate_banks(account_number: str) -> list[dict]:
    """Banks for which this account number passes the check digit.
    A hint only: it narrows the list, it does not prove the bank."""
    return [{"code": c, "name": n} for c, n in BANKS.items() if is_valid(account_number, c)]
