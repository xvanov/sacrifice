from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import RateLimitExceeded, check_rate_limit
from app.database import get_db
from app.models.user import User
from app.services.auth import decode_access_token

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user_id = payload.get("sub")
    session_id = payload.get("sid")
    if not user_id or not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if user.auth_session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )
    return user


async def require_verified_email(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require a verified email for sensitive operations.

    Returns the authenticated user if their email is verified, otherwise
    raises 403 Forbidden.  Designed as a narrow gate for one representative
    sensitive path (goal creation); broader rollout belongs to a follow-up
    story.
    """
    if not current_user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required",
        )
    return current_user


# ── Public-route rate limiting ─────────────────────────────────────────


_AUTH_RATE_LIMIT = 10  # requests per window
_AUTH_RATE_WINDOW = 60.0  # seconds


async def check_auth_rate_limit(request: Request) -> None:
    """Rate-limit public auth routes (login, register, OAuth entry/exchange).

    Raises HTTPException 429 when the client exceeds the allowed rate.
    """
    try:
        await check_rate_limit(
            request,
            max_requests=_AUTH_RATE_LIMIT,
            window_seconds=_AUTH_RATE_WINDOW,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(int(exc.retry_after + 1))},
        )
