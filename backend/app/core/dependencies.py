from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services.auth import ACCESS_TOKEN_TYPE, decode_access_token

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
    access_jti = payload.get("jti")
    token_type = payload.get("typ")
    if not user_id or not session_id or not access_jti or token_type != ACCESS_TOKEN_TYPE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    result = await db.execute(select(AuthSession).where(AuthSession.id == session_id))
    session = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if (
        session is None
        or str(session.user_id) != str(user_id)
        or session.access_jti != access_jti
        or session.revoked_at is not None
        or session.refresh_expires_at <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user
