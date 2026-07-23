"""Tests for mandatory email verification before full session issuance.

Covers the acceptance criteria:
- AC1.1: New email/password accounts remain restricted until verification
- AC2.1: Verification token single-use enforcement
- AC2.2: Verification token time-bounded validity
- AC2.3: Auditable evidence of token lifecycle actions
- AC3.1: Protected routes reject unverified sessions with clear error semantics
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.main import app
from app.models.user import User
from app.models.verification_token import VerificationToken
from app.services.verification import _hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Direct-DB helper ───────────────────────────────────────────────────
# Tests that need to inspect DB state directly (for audit assertions) use
# their own ephemeral engine + session.  The conftest.py override is for
# the app's request-scoped sessions; it is not designed for test-to-DB
# inspection outside a request lifecycle.


async def _db_session():
    """Create an ephemeral DB session for direct state inspection."""
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ── Helpers ────────────────────────────────────────────────────────────


async def _register_unverified(
    client, email="unverified@test.com", password="longenoughpw"
):
    """Register a new email/password account and return the auth response."""
    resp = await client.post(
        "/api/auth/email/register",
        json={"email": email, "password": password},
    )
    return resp


async def _get_verification_token(client, access_token):
    """Request a verification token for the authenticated user."""
    resp = await client.post(
        "/api/auth/email/resend-verification",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return resp


# ═══════════════════════════════════════════════════════════════════════
# AC1.1: New email/password accounts remain restricted until verification
# ═══════════════════════════════════════════════════════════════════════


async def test_email_register_creates_unverified_account():
    """AC1.1: Registration creates an account with email_verified=False."""
    async with make_client() as client:
        resp = await _register_unverified(client)
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        # The returned user data does NOT include email_verified in the
        # serialized response, but the session is restricted — protected
        # routes will reject it (tested in AC3.1).


async def test_email_login_issues_token_even_when_unverified():
    """AC1.1: Login succeeds for unverified accounts — they need a bearer
    token to call /email/verify and /email/resend-verification."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client)
        assert reg_resp.status_code == 200

        login_resp = await client.post(
            "/api/auth/email/login",
            json={"email": "unverified@test.com", "password": "longenoughpw"},
        )
        assert login_resp.status_code == 200
        assert "access_token" in login_resp.json()


async def test_email_register_persists_unverified_state():
    """AC1.1: The database row has email_verified=False after registration."""
    async with make_client() as client:
        resp = await _register_unverified(client, email="dbcheck@test.com")
        assert resp.status_code == 200
        user_id = resp.json()["user"]["id"]

    async for session in _db_session():
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()
        assert user.email_verified is False
        assert user.email_verified_at is None
        assert user.auth_provider == "email"
        break


# ═══════════════════════════════════════════════════════════════════════
# AC2.1: Single-use enforcement (replay rejection)
# ═══════════════════════════════════════════════════════════════════════


async def test_verification_token_cannot_be_reused():
    """AC2.1: After a token is consumed, it cannot be used again."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="replay@test.com")
        token = reg_resp.json()["access_token"]

        # Get a verification token
        vt_resp = await _get_verification_token(client, token)
        assert vt_resp.status_code == 200
        raw_token = vt_resp.json()["verification_token"]

        # First use — succeeds
        verify1 = await client.post(
            "/api/auth/email/verify",
            json={"token": raw_token},
        )
        assert verify1.status_code == 200
        assert verify1.json()["detail"] == "Email verified successfully"

        # Second use — rejected
        verify2 = await client.post(
            "/api/auth/email/verify",
            json={"token": raw_token},
        )
        assert verify2.status_code == 400
        assert verify2.json()["error"] == "invalid_or_expired_token"


async def test_verification_token_consumed_flag_is_persisted():
    """AC2.1: The consumed flag is persisted to the database."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="consumed@test.com")
        token = reg_resp.json()["access_token"]

        vt_resp = await _get_verification_token(client, token)
        raw_token = vt_resp.json()["verification_token"]

        await client.post("/api/auth/email/verify", json={"token": raw_token})

    async for session in _db_session():
        result = await session.execute(
            select(VerificationToken).where(
                VerificationToken.token_hash == _hash_token(raw_token)
            )
        )
        vt = result.scalar_one()
        assert vt.consumed is True
        assert vt.consumed_at is not None
        break


async def test_nonexistent_token_is_rejected():
    """AC2.1: A made-up token is rejected."""
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/verify",
            json={"token": "this-is-totally-fake-and-not-real"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_or_expired_token"


# ═══════════════════════════════════════════════════════════════════════
# AC2.2: Time-bounded validity
# ═══════════════════════════════════════════════════════════════════════


async def test_expired_verification_token_is_rejected():
    """AC2.2: An expired token is rejected."""
    import secrets

    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="expiring@test.com")
        user_id = reg_resp.json()["user"]["id"]

    raw = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw)

    async for session in _db_session():
        vt = VerificationToken(
            user_id=uuid.UUID(user_id),
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        session.add(vt)
        await session.commit()
        break

    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/verify",
            json={"token": raw},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_or_expired_token"


async def test_valid_token_within_window_is_accepted():
    """AC2.2: A non-expired, unconsumed token is accepted."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="validwindow@test.com")
        access_token = reg_resp.json()["access_token"]

        vt_resp = await _get_verification_token(client, access_token)
        assert vt_resp.status_code == 200
        raw_token = vt_resp.json()["verification_token"]

        verify_resp = await client.post(
            "/api/auth/email/verify",
            json={"token": raw_token},
        )
        assert verify_resp.status_code == 200
        assert verify_resp.json()["detail"] == "Email verified successfully"


async def test_verification_token_has_future_expiry():
    """AC2.2: Issued tokens have an expiry in the future."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="futureexp@test.com")
        access_token = reg_resp.json()["access_token"]

        vt_resp = await _get_verification_token(client, access_token)
        raw_token = vt_resp.json()["verification_token"]

    async for session in _db_session():
        result = await session.execute(
            select(VerificationToken).where(
                VerificationToken.token_hash == _hash_token(raw_token)
            )
        )
        vt = result.scalar_one()
        assert vt.expires_at > datetime.now(timezone.utc)
        assert vt.created_at <= datetime.now(timezone.utc)
        break


# ═══════════════════════════════════════════════════════════════════════
# AC2.3: Auditable evidence
# ═══════════════════════════════════════════════════════════════════════


async def test_verification_token_issuance_is_auditable():
    """AC2.3: Token issuance records created_at, user_id, and token_hash."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="audit-issue@test.com")
        access_token = reg_resp.json()["access_token"]
        user_id = reg_resp.json()["user"]["id"]

        vt_resp = await _get_verification_token(client, access_token)
        raw_token = vt_resp.json()["verification_token"]

    async for session in _db_session():
        result = await session.execute(
            select(VerificationToken).where(
                VerificationToken.token_hash == _hash_token(raw_token)
            )
        )
        vt = result.scalar_one()
        # Issuance is recorded
        assert str(vt.user_id) == user_id
        assert vt.created_at is not None
        assert vt.consumed is False
        assert vt.consumed_at is None
        break


async def test_verification_token_consumption_is_auditable():
    """AC2.3: Token consumption records consumed_at timestamp."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="audit-consume@test.com")
        access_token = reg_resp.json()["access_token"]

        vt_resp = await _get_verification_token(client, access_token)
        raw_token = vt_resp.json()["verification_token"]

        await client.post("/api/auth/email/verify", json={"token": raw_token})

    async for session in _db_session():
        result = await session.execute(
            select(VerificationToken).where(
                VerificationToken.token_hash == _hash_token(raw_token)
            )
        )
        vt = result.scalar_one()
        assert vt.consumed is True
        assert vt.consumed_at is not None
        # consumption happened after issuance
        assert vt.consumed_at >= vt.created_at
        break


async def test_user_verified_at_is_recorded():
    """AC2.3: The user row records email_verified_at when verified."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="user-vt@test.com")
        access_token = reg_resp.json()["access_token"]
        user_id = reg_resp.json()["user"]["id"]

        vt_resp = await _get_verification_token(client, access_token)
        raw_token = vt_resp.json()["verification_token"]

        await client.post("/api/auth/email/verify", json={"token": raw_token})

    async for session in _db_session():
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()
        assert user.email_verified is True
        assert user.email_verified_at is not None
        break


# ═══════════════════════════════════════════════════════════════════════
# AC3.1: Protected routes reject unverified sessions
# ═══════════════════════════════════════════════════════════════════════


async def test_unverified_user_cannot_access_goals():
    """AC3.1: GET /api/goals rejects unverified email/password session."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="nogoals@test.com")
        access_token = reg_resp.json()["access_token"]

        resp = await client.get(
            "/api/goals",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Email verification required"


async def test_unverified_user_cannot_access_dashboard():
    """AC3.1: GET /api/dashboard/stats rejects unverified session."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="nodash@test.com")
        access_token = reg_resp.json()["access_token"]

        resp = await client.get(
            "/api/dashboard/stats",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Email verification required"


async def test_unverified_user_cannot_access_notifications():
    """AC3.1: GET /api/notifications rejects unverified session."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="nonotif@test.com")
        access_token = reg_resp.json()["access_token"]

        resp = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Email verification required"


async def test_unverified_user_cannot_access_payment_config():
    """AC3.1: GET /api/payment/config rejects unverified session."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="nopay@test.com")
        access_token = reg_resp.json()["access_token"]

        resp = await client.get(
            "/api/payment/config",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Email verification required"


async def test_verified_user_can_access_protected_routes():
    """AC3.1: After verification, protected routes accept the session."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="verifiedok@test.com")
        access_token = reg_resp.json()["access_token"]

        # Get and consume a verification token
        vt_resp = await _get_verification_token(client, access_token)
        raw_token = vt_resp.json()["verification_token"]
        await client.post("/api/auth/email/verify", json={"token": raw_token})

        # Now protected routes should work
        resp = await client.get(
            "/api/goals",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200


async def test_unverified_user_can_access_auth_me():
    """AC3.1: /api/auth/me is NOT gated by require_verified_email —
    it only needs a valid bearer token (get_current_user)."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="authme@test.com")
        access_token = reg_resp.json()["access_token"]

        resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "authme@test.com"


async def test_unverified_user_can_refresh_token():
    """AC3.1: /api/auth/refresh is NOT gated — unverified sessions
    can refresh their bearer token."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="refreshme@test.com")
        access_token = reg_resp.json()["access_token"]

        resp = await client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()


async def test_unverified_user_can_logout():
    """AC3.1: /api/auth/logout is NOT gated — unverified sessions
    can still log out."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="logoutme@test.com")
        access_token = reg_resp.json()["access_token"]

        resp = await client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Logged out"


# ═══════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════


async def test_resend_verification_for_already_verified_returns_409():
    """Resend should refuse when the account is already verified."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="already@test.com")
        access_token = reg_resp.json()["access_token"]

        vt_resp = await _get_verification_token(client, access_token)
        raw_token = vt_resp.json()["verification_token"]
        await client.post("/api/auth/email/verify", json={"token": raw_token})

        # Now resend should 409
        resp = await client.post(
            "/api/auth/email/resend-verification",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "already_verified"


async def test_resend_verification_returns_existing_pending_token():
    """Resend returns existing valid token instead of creating a new one."""
    async with make_client() as client:
        reg_resp = await _register_unverified(client, email="pending@test.com")
        access_token = reg_resp.json()["access_token"]

        first = await _get_verification_token(client, access_token)
        assert first.status_code == 200
        first_token = first.json()["verification_token"]

        second = await _get_verification_token(client, access_token)
        assert second.status_code == 200
        # Should return the same token info, not a new one
        assert "verification_token" not in second.json()
        assert second.json()["detail"] == "A valid verification token already exists"


async def test_verify_endpoint_does_not_require_auth():
    """The /email/verify endpoint is not behind a bearer-token gate —
    the token itself is the proof of ownership."""
    async with make_client() as client:
        # No auth header — should still process the request
        resp = await client.post(
            "/api/auth/email/verify",
            json={"token": "some-fake-token"},
        )
        # It should be a 400 (bad token), not 401/403 (auth required)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_or_expired_token"


async def test_oauth_accounts_bypass_verification_gate():
    """AC3.1: OAuth (Google) accounts get email_verified=True, so they
    are never blocked by require_verified_email."""
    from unittest.mock import patch

    async with make_client() as client:
        with patch("app.routes.auth.verify_google_token") as mock:
            mock.return_value = {
                "email": "oauthbypass@test.com",
                "name": "OAuth User",
                "sub": "oauth-bypass-sub",
                "picture": None,
                "email_verified": True,
            }
            google_resp = await client.post("/api/auth/google", json={"token": "valid"})
            assert google_resp.status_code == 200
            access_token = google_resp.json()["access_token"]

        # OAuth user should access protected routes immediately
        resp = await client.get(
            "/api/goals",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200
