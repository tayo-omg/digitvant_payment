import hashlib
import hmac

import jwt
from fastapi import Header, HTTPException, Request

from .config import settings


def get_current_user(authorization: str = Header(...)) -> str:
    """Validates the access token issued by your existing bank app and returns the user id.
    ADAPT: match your token's signing key, algorithm and the claim holding the customer id."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Missing bearer token")
    try:
        payload = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            options={"verify_aud": settings.jwt_audience is not None},
        )
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "Invalid token")
    return str(user_id)


async def verify_webhook(request: Request, x_signature: str = Header(...)) -> None:
    """HMAC-SHA256 of the raw body, hex-encoded, sent by the core bank system."""
    body = await request.body()
    expected = hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_signature):
        raise HTTPException(401, "Bad signature")


def require_internal_key(x_api_key: str = Header(...)) -> None:
    if not hmac.compare_digest(x_api_key, settings.internal_api_key):
        raise HTTPException(401, "Bad API key")
