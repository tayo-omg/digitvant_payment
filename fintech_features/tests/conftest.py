import hashlib
import hmac
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

_db_dir = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{_db_dir}/test.db", WEBHOOK_SECRET="test-secret",
                  INTERNAL_API_KEY="test-key", BANK_API_BASE_URL="")

from fastapi.testclient import TestClient  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import get_current_user  # noqa: E402

NOW = datetime.now(timezone.utc)


class Api:
    def __init__(self, client: TestClient):
        self.client = client
        self.user = "u1"
        self._ref = 0
        app.dependency_overrides[get_current_user] = lambda: self.user

    def pay(self, merchant, amount, days_ago, currency="NGN", user=None):
        self._ref += 1
        body = json.dumps({
            "external_ref": f"ref-{self._ref}", "user_id": user or self.user, "direction": "debit",
            "amount_kobo": amount, "currency": currency, "channel": "card", "merchant_name": merchant,
            "occurred_at": (NOW - timedelta(days=days_ago)).isoformat(),
        }).encode()
        sig = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        r = self.client.post("/v1/webhooks/transactions", content=body,
                             headers={"X-Signature": sig, "Content-Type": "application/json"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def tracker(self):
        return self.client.get("/v1/subscriptions").json()


@pytest.fixture
def api():
    Base.metadata.drop_all(engine)
    with TestClient(app) as client:
        yield Api(client)
    app.dependency_overrides.clear()
