# Fintech engagement features (FastAPI)

Scan-to-Pay, payment categorisation and subscription tracking as a service beside your bank app.

## Run
    sudo apt install tesseract-ocr        # server-side OCR needs the Tesseract binary
    pip install -r requirements.txt
    cp .env.example .env                  # fill in values
    uvicorn app.main:app --reload         # docs at /docs

## Integration checklist
1. `app/security.py`: set the JWT public key, algorithm, and the claim holding the customer ID.
2. `app/bank_client.py`: point name enquiry and fraud check at your real endpoints.
3. Core banking -> `POST /v1/webhooks/transactions` for every debit/credit
   (HMAC-SHA256 of raw body in `X-Signature`, secret = WEBHOOK_SECRET).
4. Your transfer service -> `POST /internal/verifications/{id}/consume` (header `X-API-Key`)
   before debiting, and refuse the transfer if it fails.
5. `app/nuban.py`: replace the bank list with the current NIBSS/CBN institution list.

## Mobile flow
Scan:   camera -> (on-device OCR, or POST /v1/scan/account) -> user taps/edits number and bank
        -> POST /v1/scan/verify -> show name + warning -> transfer with verification_id
Success screen: GET /v1/transactions/{id}/post-payment-prompts
        -> PUT /v1/transactions/{id}/category and/or POST /v1/subscriptions
Tracker: GET /v1/subscriptions, PATCH/DELETE /v1/subscriptions/{id}

## Before production
Alembic migrations, Postgres, rate limiting (slowapi) on /v1/scan/*, structured audit logs,
NDPA consent + retention policy for transaction categorisation data, and tests.
