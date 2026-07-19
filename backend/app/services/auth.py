import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.email_verification_token import EmailVerificationToken
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


ACCESS_TOKEN_PURPOSE = "access"
AUTH_CODE_PURPOSE = "auth_exchange"
AUTH_CODE_EXPIRE_SECONDS = 300


def _create_signed_token(
    user_id: str,
    *,
    purpose: str,
    expires_in: timedelta,
    extra_claims: dict | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + expires_in
    to_encode = {
        "sub": user_id,
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "purpose": purpose,
    }
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)



def create_access_token(user_id: str, session_id: str) -> str:
    return _create_signed_token(
        user_id,
        purpose=ACCESS_TOKEN_PURPOSE,
        expires_in=timedelta(minutes=settings.jwt_expire_minutes),
        extra_claims={"sid": session_id},
    )



def create_auth_code(user_id: str, code_id: str) -> str:
    return _create_signed_token(
        user_id,
        purpose=AUTH_CODE_PURPOSE,
        expires_in=timedelta(seconds=AUTH_CODE_EXPIRE_SECONDS),
        extra_claims={"code_id": code_id},
    )



def _decode_signed_token(token: str, *, purpose: str) -> dict | None:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None
    if payload.get("purpose") != purpose:
        return None
    return payload



def decode_access_token(token: str) -> dict | None:
    return _decode_signed_token(token, purpose=ACCESS_TOKEN_PURPOSE)



def decode_auth_code(token: str) -> dict | None:
    return _decode_signed_token(token, purpose=AUTH_CODE_PURPOSE)


async def rotate_auth_session(
    db: AsyncSession,
    user: User,
    *,
    clear_pending_auth_code: bool = True,
) -> User:
    user.auth_session_id = str(uuid.uuid4())
    if clear_pending_auth_code:
        user.pending_auth_code_id = None
    await db.commit()
    await db.refresh(user)
    return user


async def store_pending_auth_code(db: AsyncSession, user: User) -> tuple[User, str]:
    code_id = str(uuid.uuid4())
    user.pending_auth_code_id = code_id
    await db.commit()
    await db.refresh(user)
    return user, code_id


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
        # tokeninfo returns the claim as the string "true"/"false"
        "email_verified": data.get("email_verified") in (True, "true"),
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

    # Always consult /user/emails: it is the only source of the `verified`
    # flag, which gates cross-provider account linking (an unverified email
    # must never let a GitHub login into an account owned by that address).
    email = user_data.get("email")
    email_verified = False
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
        if email:
            match = next((e for e in emails if e.get("email") == email), None)
            email_verified = bool(match and match.get("verified"))
        else:
            primary = next((e for e in emails if e.get("primary")), None)
            if primary:
                email = primary["email"]
                email_verified = bool(primary.get("verified"))

    return {
        "email": email or f"{user_data['login']}@github.com",
        "email_verified": email_verified if email else False,
        "login": user_data["login"],
        "name": user_data.get("name") or user_data["login"],
        "id": str(user_data["id"]),
        "avatar_url": user_data.get("avatar_url"),
    }


# OAuth providers whose login proves ownership of a VERIFIED email address.
# A verified-email login from one of these may sign in to an existing account
# registered under a different provider with the same email (standard
# cross-provider linking). "dev" and unverified emails never qualify.
_LINKABLE_OAUTH_PROVIDERS = frozenset({"google", "github"})


async def get_or_create_user(
    db: AsyncSession,
    provider: str,
    provider_id: str,
    email: str,
    display_name: str,
    avatar_url: str | None = None,
    email_verified: bool = False,
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

    # No match on (provider, provider_id). If a row already exists with this
    # email under a DIFFERENT auth provider:
    #   - a trusted OAuth login with a provider-VERIFIED email is the same
    #     person proving they own the address — sign them in to the existing
    #     account (cross-provider linking; the row keeps its original
    #     provider so e.g. password login continues to work);
    #   - anything else (unverified email, dev bypass) is refused, since
    #     silently relinking would let an impostor who merely claims the
    #     email take over the account.
    result = await db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing:
        if provider in _LINKABLE_OAUTH_PROVIDERS and email_verified:
            if avatar_url and not existing.avatar_url:
                existing.avatar_url = avatar_url
                await db.commit()
                await db.refresh(existing)
            return existing
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


# ── Email verification tokens ───────────────────────────────────────────

_VERIFY_TOKEN_BYTES = 32
_VERIFY_TOKEN_EXPIRE_MINUTES = 60
_RESEND_COOLDOWN_SECONDS = 60


def _hash_verify_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_verification_token(db: AsyncSession, user: User) -> str:
    """Issue a new verification token for ``user`` and return the raw token.

    The raw token is stored only as a SHA-256 hash in the database.
    """
    raw = secrets.token_urlsafe(_VERIFY_TOKEN_BYTES)
    token_hash = _hash_verify_token(raw)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_VERIFY_TOKEN_EXPIRE_MINUTES)
    row = EmailVerificationToken(
        token_hash=token_hash,
        user_id=user.id,
        expires_at=expires_at,
    )
    db.add(row)
    await db.commit()
    return raw


class VerificationError(Exception):
    """Raised when a verification token is invalid, expired, or already used."""


async def redeem_verification_token(db: AsyncSession, user: User, raw_token: str) -> None:
    """Redeem a verification token, marking the user as verified.

    Raises :class:`VerificationError` if the token is not found, has expired,
    or has already been used.
    """
    token_hash = _hash_verify_token(raw_token)
    result = await db.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == token_hash,
            EmailVerificationToken.user_id == user.id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise VerificationError("Invalid verification token")
    if row.used:
        raise VerificationError("Verification token has already been used")
    if datetime.now(timezone.utc) > row.expires_at:
        raise VerificationError("Verification token has expired")

    row.used = True
    user.is_verified = True
    await db.commit()


async def can_resend_verification(db: AsyncSession, user: User) -> bool:
    """Check whether the resend cooldown has elapsed for ``user``."""
    result = await db.execute(
        select(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user.id)
        .order_by(EmailVerificationToken.created_at.desc())
        .limit(1)
    )
    last = result.scalar_one_or_none()
    if last is None:
        return True
    cooldown_until = last.created_at + timedelta(seconds=_RESEND_COOLDOWN_SECONDS)
    return datetime.now(timezone.utc) >= cooldown_until
