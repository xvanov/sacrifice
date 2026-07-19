"""Password reset token service.

Generates single-use, expiring reset tokens whose plaintext is returned
to the caller exactly once (at creation time). Only a SHA-256 hash of the
token is persisted, so a compromised database never leaks usable tokens.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset_token import PasswordResetToken
from app.models.user import User

RESET_TOKEN_EXPIRE_MINUTES = 60
MAX_RESET_ATTEMPTS = 5


def _hash_token(plaintext: str) -> str:
    """Return a SHA-256 hex digest of *plaintext*."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


async def generate_reset_token(db: AsyncSession, user: User) -> str:
    """Create a new password-reset token for *user*.

    Returns the plaintext token that should be delivered to the user
    (e.g. via email). Only the SHA-256 hash is stored in the database.
    """
    plaintext = secrets.token_urlsafe(32)
    token_hash = _hash_token(plaintext)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)

    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(reset_token)
    await db.commit()
    return plaintext


class ResetTokenError(Exception):
    """Raised when a reset token is invalid, expired, consumed, or exhausted."""


async def validate_reset_token(db: AsyncSession, plaintext: str) -> PasswordResetToken:
    """Validate *plaintext* and return the corresponding :class:`PasswordResetToken`.

    Raises :class:`ResetTokenError` when the token is unknown, expired,
    already consumed, or has exceeded the allowed number of attempts.
    Each failed validation increments the attempt counter on the stored row.
    """
    token_hash = _hash_token(plaintext)
    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    record = result.scalar_one_or_none()

    if record is None:
        raise ResetTokenError("Invalid reset token")

    if record.consumed:
        raise ResetTokenError("Reset token has already been used")

    if record.expires_at < datetime.now(timezone.utc):
        raise ResetTokenError("Reset token has expired")

    if record.attempts >= MAX_RESET_ATTEMPTS:
        raise ResetTokenError("Too many attempts — reset token is locked")

    return record


async def consume_reset_token(db: AsyncSession, record: PasswordResetToken) -> None:
    """Mark *record* as consumed so it cannot be reused."""
    record.consumed = True
    await db.commit()


async def record_reset_attempt(db: AsyncSession, record: PasswordResetToken) -> None:
    """Increment the attempt counter on *record* after a failed validation."""
    record.attempts += 1
    await db.commit()