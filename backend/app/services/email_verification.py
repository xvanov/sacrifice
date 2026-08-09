"""Email verification token lifecycle.

Tokens are cryptographically random, stored as SHA-256 hashes, and are
single-use with a configurable TTL.  The plaintext token is only returned in
the response body when the environment is not production — in production it
would be sent via email (out of scope for this story).
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.models.verification_token import VerificationToken


class VerificationError(Exception):
    """Raised when verification fails for a recoverable reason."""

    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code: str = error_code


def _plaintext_token() -> str:
    """Generate a cryptographically random URL-safe token."""
    return secrets.token_urlsafe(48)


def _sha256_hex(plaintext: str) -> str:
    """Return the SHA-256 hex digest of *plaintext*."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(
        minutes=settings.verification_token_ttl_minutes
    )


async def create_verification_token(db: AsyncSession, user: User) -> str:
    """Generate a new verification token for *user*.

    Returns the plaintext token.  Caller is responsible for deciding whether
    to expose it in the response body (gated by
    ``settings.email_verify_token_response_body_allowed``).
    """
    plaintext = _plaintext_token()
    token_hash = _sha256_hex(plaintext)

    vt = VerificationToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=_token_expiry(),
    )
    db.add(vt)
    await db.commit()
    return plaintext


async def consume_verification_token(db: AsyncSession, plaintext: str) -> User:
    """Look up, validate, and consume a verification token.

    Returns the verified User on success.

    Raises ``VerificationError`` with:
    * ``token_expired`` when the token exists but has expired.
    * ``invalid_token`` when the token does not exist or has already been used.
    """
    token_hash = _sha256_hex(plaintext)

    result = await db.execute(
        select(VerificationToken).where(VerificationToken.token_hash == token_hash)
    )
    vt = result.scalar_one_or_none()

    if vt is None:
        raise VerificationError("invalid_token")

    # Check expiry first so an expired token always surfaces as expired.
    if vt.expires_at < datetime.now(timezone.utc):
        raise VerificationError("token_expired")

    if vt.used:
        raise VerificationError("invalid_token")

    # Atomically mark token used and user verified.
    vt.used = True

    result = await db.execute(
        select(User).where(
            User.id == vt.user_id,
            User.email_verified == False,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Token's target user is already verified — shouldn't happen with
        # normal flow, but log and raise a distinct error.
        import logging

        logging.getLogger(__name__).warning(
            "Verification token %s targets already-verified user %s", vt.id, vt.user_id
        )
        raise VerificationError("invalid_token")

    user.email_verified = True
    await db.commit()
    await db.refresh(user)
    return user


async def invalidate_tokens_for_user(db: AsyncSession, user: User) -> bool:
    """Force-expire all outstanding verification tokens for *user*.

    Returns ``True`` if at least one token was invalidated, ``False`` otherwise.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(VerificationToken).where(
            VerificationToken.user_id == user.id,
            VerificationToken.used == False,
            VerificationToken.expires_at > now,
        )
    )
    tokens = result.scalars().all()
    if not tokens:
        return False

    for vt in tokens:
        vt.expires_at = now
    await db.commit()
    return True
