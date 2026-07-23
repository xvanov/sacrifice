"""Email verification token service.

Issues single-use, time-bounded verification tokens and consumes them to
mark an email/password account as verified.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.verification_token import VerificationToken

VERIFICATION_TOKEN_EXPIRE_HOURS = 24


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def issue_verification_token(db: AsyncSession, user: User) -> str:
    """Issue a new verification token for *user*.

    Returns the raw token value (only shown once).  The database stores only
    its SHA-256 hash.
    """
    raw = secrets.token_urlsafe(32)
    token = VerificationToken(
        user_id=user.id,
        token_hash=_hash_token(raw),
        expires_at=datetime.now(timezone.utc)
        + timedelta(hours=VERIFICATION_TOKEN_EXPIRE_HOURS),
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return raw


async def consume_verification_token(db: AsyncSession, raw_token: str) -> User | None:
    """Consume a verification token, mark the user verified, and return the user.

    Returns ``None`` if the token is invalid, already consumed, or expired.
    """
    token_hash = _hash_token(raw_token)
    result = await db.execute(
        select(VerificationToken).where(
            VerificationToken.token_hash == token_hash,
        )
    )
    token = result.scalar_one_or_none()

    if token is None:
        return None
    if token.consumed:
        return None
    if datetime.now(timezone.utc) > token.expires_at:
        return None

    token.consumed = True
    token.consumed_at = datetime.now(timezone.utc)

    result = await db.execute(select(User).where(User.id == token.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return None

    user.email_verified = True
    user.email_verified_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(user)
    return user


async def get_pending_token_for_user(
    db: AsyncSession, user_id: uuid.UUID
) -> VerificationToken | None:
    """Return the most recent unconsumed, unexpired token for *user_id*, or None."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(VerificationToken)
        .where(
            VerificationToken.user_id == user_id,
            VerificationToken.consumed == False,
            VerificationToken.expires_at > now,
        )
        .order_by(VerificationToken.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
