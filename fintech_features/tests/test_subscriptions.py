from datetime import timedelta

from app.db import SessionLocal
from app.models import Transaction
from tests.conftest import NOW


def _names(items):
    return sorted(s["name"] for s in items)


def test_automatic_charges_become_suggestions(api):
    api.pay("NETFLIX.COM LAGOS", 450000, 3)
    api.pay("GYMPRO FITNESS", 2000000, 33)
    api.pay("GYMPRO FITNESS", 2000000, 3)
    api.pay("SPOTIFY AB", 90000, 200)
    api.pay("KILIMANJARO RESTAURANT", 800000, 2)

    tracker = api.tracker()
    assert _names(tracker["suggestions"]) == ["GYMPRO FITNESS", "NETFLIX.COM LAGOS"]
    assert tracker["active_count"] == 0


def test_confirm_and_dismiss_suggestions(api):
    api.pay("NETFLIX.COM LAGOS", 450000, 3)
    api.pay("DROPBOX INC", 600000, 3)
    ids = {s["name"]: s["id"] for s in api.tracker()["suggestions"]}

    assert api.client.post(f"/v1/subscriptions/{ids['NETFLIX.COM LAGOS']}/confirm", json={"name": "Netflix"}).status_code == 200
    assert api.client.post(f"/v1/subscriptions/{ids['DROPBOX INC']}/dismiss").status_code == 204
    api.pay("DROPBOX INC", 600000, 1)

    tracker = api.tracker()
    assert _names(tracker["subscriptions"]) == ["Netflix"]
    assert tracker["suggestions"] == []


def test_no_on_success_screen_dismisses_suggestion(api):
    txn_id = api.pay("NETFLIX.COM LAGOS", 450000, 3)
    prompt = api.client.get(f"/v1/transactions/{txn_id}/post-payment-prompts").json()
    assert prompt["ask_subscription"] and prompt["subscription_reason"] == "suggested"

    api.client.post("/v1/subscriptions", json={"transaction_id": txn_id, "is_subscription": False})
    assert api.tracker()["suggestions"] == []


def test_monthly_totals_are_per_currency(api):
    api.pay("NETFLIX.COM LAGOS", 450000, 3)
    api.pay("OPENAI CHATGPT", 2000, 5, currency="USD")
    for s in api.tracker()["suggestions"]:
        api.client.post(f"/v1/subscriptions/{s['id']}/confirm", json={})

    assert api.tracker()["estimated_monthly_totals"] == {"NGN": 450000, "USD": 2000}


def test_backfill_finds_stored_history_once(api):
    with SessionLocal() as db:
        for i, days in enumerate((95, 65, 35, 5)):
            db.add(Transaction(external_ref=f"old-{i}", user_id="u2", direction="debit", amount_kobo=150000,
                               channel="card", merchant_name="SHOWMAX AFRICA", merchant_key="showmax africa",
                               occurred_at=(NOW - timedelta(days=days)).replace(tzinfo=None)))
        db.commit()
    headers = {"X-API-Key": "test-key"}

    assert api.client.post("/internal/subscriptions/backfill").status_code == 422
    assert api.client.post("/internal/subscriptions/backfill", headers=headers).json() == {"users": 1, "suggested": 1}
    assert api.client.post("/internal/subscriptions/backfill", headers=headers).json()["suggested"] == 0

    api.user = "u2"
    [suggestion] = api.tracker()["suggestions"]
    assert suggestion["last_paid_at"][:10] == (NOW - timedelta(days=5)).date().isoformat()
