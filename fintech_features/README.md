# Digitvant New features (FastAPI)

Scan-to-Pay, payment categorisation and subscription tracking as a service beside your bank app.

## Integration checklist
1. `app/security.py`: set the JWT public key, algorithm, and the claim holding the customer ID.
2. `app/bank_client.py`: point name enquiry and fraud check at your real endpoints.
3. Core banking -> `POST /v1/webhooks/transactions` for every debit/credit
   (HMAC-SHA256 of raw body in `X-Signature`, secret = WEBHOOK_SECRET).
4. Your transfer service -> `POST /internal/verifications/{id}/consume` (header `X-API-Key`)
   before debiting, and refuse the transfer if it fails.
5. Bank list: load the full NIBSS/provider institution list with `python -m app.banks <file.json>`
   (same shape as `app/banks_seed.json`, including NIP codes and other names). Re-run to refresh.
6. Subscriptions at launch: replay the last 12 months of card payments through the transactions
   webhook, oldest first, then call `POST /internal/subscriptions/backfill` once (header `X-API-Key`).
   Safe to re-run. See `SUBSCRIPTION_CHANGES.txt`.

## Mobile flow
Scan:   camera -> (on-device OCR, or POST /v1/scan/account) -> user taps/edits number
        -> if suggested_bank is set: POST /v1/scan/verify with it
           else: POST /v1/scan/resolve (looks up the likeliest banks at once) -> user taps the name
           (no match: bank picker from GET /v1/banks?account_number=..., likely banks first)
        -> show name + warning -> transfer with verification_id
Success screen: GET /v1/transactions/{id}/post-payment-prompts
        -> PUT /v1/transactions/{id}/category and/or POST /v1/subscriptions
        (ask_subscription is also true when the merchant has a suggestion waiting)
Tracker: GET /v1/subscriptions -> tracked subscriptions, suggestions, monthly totals per currency
        -> suggestion: POST /v1/subscriptions/{id}/confirm (Yes) or /v1/subscriptions/{id}/dismiss (No)
        -> tracked: PATCH /v1/subscriptions/{id} to edit, DELETE /v1/subscriptions/{id} to stop tracking

## Before production
Alembic migrations, Postgres, rate limiting (slowapi) on /v1/scan/*, structured audit logs,
NDPA consent + retention policy for transaction categorisation data, and tests.
