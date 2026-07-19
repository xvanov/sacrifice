"""CSRF protection primitives for cookie-authenticated requests.

Provides token generation, validation, and a FastAPI dependency that rejects
requests missing a valid CSRF token. Intended as a reusable guard that can be
wired into routes when cookie-based authentication is added.

The token is a signed JWT with a short lifetime, carried in the ``X-CSRF-Token``
request header. The signing key is derived from ``settings.jwt_secret`` so no
additional secret configuration is required.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Header, HTTPException, status
from jose import JWTError, jwt

from app.config import settings

CSRF_TOKEN_PURPOSE = "csrf"
_CSRF_EXPIRE_MINUTES = 30


def generate_csrf_token() -> str:
    """Produce a signed CSRF token suitable for embedding in a cookie or
    returning to the frontend via a dedicated endpoint.

    The token is self-contained: the signing key and purpose claim prevent
    tampering, and the short expiry limits the window for replay.
    """
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=_CSRF_EXPIRE_MINUTES)
    claims = {
        "sub": "csrf",
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "purpose": CSRF_TOKEN_PURPOSE,
        "nonce": secrets.token_urlsafe(20),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def validate_csrf_token(token: str) -> bool:
    """Return ``True`` if ``token`` is a valid, unexpired CSRF token."""
    if not token:
        return False
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return False
    return payload.get("purpose") == CSRF_TOKEN_PURPOSE


async def require_csrf(
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> str:
    """FastAPI dependency that rejects requests without a valid CSRF token.

    Returns the raw token on success so routes can inspect it if needed.
    Raises ``403 Forbidden`` when the header is missing or the token is
    invalid/expired.
    """
    if not x_csrf_token or not validate_csrf_token(x_csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid",
        )
    return x_csrf_token
