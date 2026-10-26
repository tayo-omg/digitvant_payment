"""Change to the company's existing bank backend.
Change the endpoint paths and response field names to match
the core banking / NIBSS name-enquiry integration.
"""
import httpx
from . import banks
from .config import settings

class NameEnquiryError(Exception):
    pass

class BankClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(base_url=settings.bank_api_base_url,  headers={"Authorization": f"Bearer {settings.bank_api_key}"}, timeout=httpx.Timeout(10.0))

    async def name_enquiry(self, account_number: str, bank_code: str) -> str:
        if not settings.bank_api_base_url:  # dev mode
            return "MOCK ACCOUNT NAME"
        bank = banks.get(bank_code)
        try:
            resp = await self._http.post(
                "/transfers/name-enquiry",                       # <- your endpoint
                # use the NIP code when we have it
                json={"account_number": account_number,
                      "bank_code": (bank.nip_code if bank and bank.nip_code else bank_code)},
            )
            resp.raise_for_status()
            name = resp.json().get("account_name")               # <- your field
        except (httpx.HTTPError, ValueError) as exc:
            raise NameEnquiryError("Name enquiry failed") from exc
        if not name:
            raise NameEnquiryError("Account not found")
        return name

    async def is_account_flagged(self, account_number: str, bank_code: str) -> bool:
        if not settings.bank_api_base_url:
            return False
        try:
            resp = await self._http.get(  "/fraud/accounts/check",      # <- the endpoint
                params={"account_number": account_number, "bank_code": bank_code} )
            resp.raise_for_status()
            return bool(resp.json().get("flagged"))
        except (httpx.HTTPError, ValueError):
            return False  # fail open on the advisory check; name enquiry stays mandatory

    async def aclose(self) -> None:
        await self._http.aclose()

bank_client = BankClient()
