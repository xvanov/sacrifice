import uuid
from datetime import datetime, timedelta, timezone

import httpx
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_expire_minutes)
    to_encode = {"sub": user_id, "exp": expire, "iat": now, "jti": str(uuid.uuid4())}
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


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


async def get_or_create_user(db: AsyncSession, provider: str, provider_id: str, email: str, display_name: str, avatar_url: str | None = None) -> User:
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
