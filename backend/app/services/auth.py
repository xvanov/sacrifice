import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.auth_session import AuthSession
from app.models.user import User


class AuthConflictError(Exception):
    """Raised when a sign-in attempt collides with an existing account
    that was created under a different auth provider.

    Routes should translate this into a 409 (or, for the OAuth browser
    flow, a redirect with ``?error=account_exists&provider=<other>``)
    so the frontend can point the user at the correct sign-in button.
    """

    def __init__(self, email: str, existing_provider: str):
        super().__init__(
            f"Email {email!r} is already registered with provider "
            f"{existing_provider!r}"
        )
        self.email = email
        self.existing_provider = existing_provider


class RefreshTokenReplayError(Exception):
    """Raised when a rotated refresh token is presented again."""


@dataclass
class AuthTokens:
    access_token: str
    refresh_token: str


ACCESS_TOKEN_TYPE = "access"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _make_refresh_secret() -> str:
    return secrets.token_urlsafe(32)


def _make_access_jti() -> str:
    return str(uuid.uuid4())


def _build_refresh_token(session_id: uuid.UUID, refresh_secret: str) -> str:
    return f"{session_id}.{refresh_secret}"


def _split_refresh_token(refresh_token: str) -> tuple[uuid.UUID, str] | None:
    if not refresh_token or "." not in refresh_token:
        return None
    session_id_raw, refresh_secret = refresh_token.split(".", 1)
    try:
        return uuid.UUID(session_id_raw), refresh_secret
    except ValueError:
        return None


def create_access_token(user_id: str, session_id: str, access_jti: str) -> str:
    now = _utcnow()
    expire = now + timedelta(minutes=settings.jwt_expire_minutes)
    to_encode = {
        "sub": user_id,
        "sid": session_id,
        "jti": access_jti,
        "typ": ACCESS_TOKEN_TYPE,
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def issue_auth_tokens(db: AsyncSession, user: User) -> AuthTokens:
    now = _utcnow()
    refresh_secret = _make_refresh_secret()
    session = AuthSession(
        user_id=user.id,
        access_jti=_make_access_jti(),
        refresh_token_hash=_hash_token(refresh_secret),
        refresh_expires_at=now + timedelta(days=settings.refresh_token_expire_days),
        last_seen_at=now,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return AuthTokens(
        access_token=create_access_token(
            str(user.id), str(session.id), session.access_jti
        ),
        refresh_token=_build_refresh_token(session.id, refresh_secret),
    )


async def _revoke_session_chain(db: AsyncSession, session: AuthSession) -> None:
    now = _utcnow()
    current = session
    seen: set[uuid.UUID] = set()
    while current and current.id not in seen:
        seen.add(current.id)
        if current.revoked_at is None:
            current.revoked_at = now
        if current.replaced_by_session_id is None:
            break
        current = await db.get(AuthSession, current.replaced_by_session_id)
    await db.commit()


async def rotate_refresh_token(db: AsyncSession, refresh_token: str) -> AuthTokens | None:
    parsed = _split_refresh_token(refresh_token)
    if parsed is None:
        return None

    session_id, refresh_secret = parsed
    session = await db.get(AuthSession, session_id)
    if session is None:
        return None

    refresh_hash = _hash_token(refresh_secret)
    if session.refresh_token_hash != refresh_hash:
        return None

    now = _utcnow()
    if session.revoked_at is not None:
        await _revoke_session_chain(db, session)
        raise RefreshTokenReplayError("rotated refresh token replayed")

    if session.refresh_expires_at <= now:
        session.revoked_at = now
        await db.commit()
        return None

    user = await db.get(User, session.user_id)
    if user is None:
        session.revoked_at = now
        await db.commit()
        return None

    next_refresh_secret = _make_refresh_secret()
    replacement = AuthSession(
        user_id=session.user_id,
        access_jti=_make_access_jti(),
        refresh_token_hash=_hash_token(next_refresh_secret),
        refresh_expires_at=now + timedelta(days=settings.refresh_token_expire_days),
        last_seen_at=now,
    )
    db.add(replacement)
    await db.flush()

    session.revoked_at = now
    session.replaced_by_session_id = replacement.id
    session.last_seen_at = now

    await db.commit()

    return AuthTokens(
        access_token=create_access_token(
            str(user.id), str(replacement.id), replacement.access_jti
        ),
        refresh_token=_build_refresh_token(replacement.id, next_refresh_secret),
    )


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError:
        return None


async def verify_google_token(token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": token},
            timeout=10,
        )
    if resp.status_code != 200:
        raise ValueError("Invalid Google token")
    data = resp.json()
    if data.get("aud") != settings.google_client_id:
        raise ValueError("Token audience mismatch")
    return {
        "email": data["email"],
        "name": data.get("name", ""),
        "sub": data["sub"],
        "picture": data.get("picture"),
    }


async def exchange_google_code(code: str, redirect_uri: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
    if resp.status_code != 200:
        raise ValueError("Failed to exchange Google code")
    return resp.json()


async def exchange_github_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
    if token_resp.status_code != 200:
        raise ValueError("Failed to exchange GitHub code")
    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError("Invalid GitHub code")

    async with httpx.AsyncClient() as client:
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=10,
        )
    if user_resp.status_code != 200:
        raise ValueError("Failed to fetch GitHub user")
    user_data = user_resp.json()

    email = user_data.get("email")
    if not email:
        async with httpx.AsyncClient() as client:
            emails_resp = await client.get(
                "https://api.github.com/user/emails",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                timeout=10,
            )
        if emails_resp.status_code == 200:
            emails = emails_resp.json()
            primary = next((e for e in emails if e.get("primary")), None)
            if primary:
                email = primary["email"]

    return {
        "email": email or f"{user_data['login']}@github.com",
        "login": user_data["login"],
        "name": user_data.get("name") or user_data["login"],
        "id": str(user_data["id"]),
        "avatar_url": user_data.get("avatar_url"),
    }


async def get_or_create_user(
    db: AsyncSession,
    provider: str,
    provider_id: str,
    email: str,
    display_name: str,
    avatar_url: str | None = None,
) -> User:
    result = await db.execute(
        select(User).where(
            User.auth_provider == provider,
            User.auth_provider_id == provider_id,
        )
    )
    user = result.scalar_one_or_none()

    if user:
        user.email = email
        user.display_name = display_name
        if avatar_url:
            user.avatar_url = avatar_url
        await db.commit()
        await db.refresh(user)
        return user

    result = await db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing:
        raise AuthConflictError(email=email, existing_provider=existing.auth_provider)

    user = User(
        email=email,
        display_name=display_name,
        avatar_url=avatar_url,
        auth_provider=provider,
        auth_provider_id=provider_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
