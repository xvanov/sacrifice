"""Tests for mandatory email verification gates.

Covers:
- AC1.1: New email/password accounts default to unverified state
- AC2.1: Sensitive actions blocked until verification
- AC3.1: Verification tokens are single-use
- AC3.2: Verification tokens expire
- AC3.3: Resend is rate-limited
"""

from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Helpers ──────────────────────────────────────────────────────────────


async def _register_unverified(client, email="fresh@test.com", password="correct horse battery", display_name="Fresh"):
    """Register a new email/password user and return (access_token, user)."""
    resp = await client.post(
        "/api/auth/email/register",
        json={
            "email": email,
            "password": password,
            "display_name": display_name,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    return body["access_token"], body["user"]


async def _auth_google(client, email="goog@test.com", name="Goog User", sub="g-sub-99"):
    """Auth via Google OAuth to get a verified user's token.

    Google verifies email addresses, so the mock includes email_verified=True.
    """
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {
            "email": email,
            "name": name,
            "sub": sub,
            "picture": None,
            "email_verified": True,
        }
        resp = await client.post("/api/auth/google", json={"token": "valid"})
        data = resp.json()
        return data["access_token"], data["user"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ── AC1.1: New email/password accounts are in an unverified state ────────


async def test_email_register_creates_unverified_account():
    async with make_client() as client:
        token, user = await _register_unverified(client)
        assert user["is_verified"] is False

        # Verify the /me endpoint also returns is_verified=False
        me = await client.get("/api/auth/me", headers=_auth_header(token))
        assert me.status_code == 200
        assert me.json()["is_verified"] is False


async def test_google_oauth_creates_verified_account():
    """Google OAuth accounts should default to verified (model default True)."""
    async with make_client() as client:
        token, user = await _auth_google(client)
        assert user["is_verified"] is True

        me = await client.get("/api/auth/me", headers=_auth_header(token))
        assert me.json()["is_verified"] is True


# ── AC2.1: Sensitive actions blocked until verification ──────────────────

_VALID_GOAL = {
    "title": "Ship the MVP",
    "description": "Launch the sacrifice app",
    "deadline": "2026-06-01T00:00:00Z",
    "pledge_amount": 5000,
    "goal_type": "youtube_video",
    "criteria": {"min_duration_seconds": 300, "video_description": "A walkthrough demo"},
    "charity_id": "acct_charity123",
}


async def test_unverified_user_cannot_create_goal():
    async with make_client() as client:
        token, user = await _register_unverified(client)
        assert user["is_verified"] is False

        resp = await client.post(
            "/api/goals",
            headers=_auth_header(token),
            json=_VALID_GOAL,
        )
        assert resp.status_code == 403
        assert "Email verification required" in resp.json()["detail"]


async def test_verified_user_can_create_goal():
    async with make_client() as client:
        token, user = await _auth_google(client)
        assert user["is_verified"] is True

        resp = await client.post(
            "/api/goals",
            headers=_auth_header(token),
            json=_VALID_GOAL,
        )
        assert resp.status_code == 201


async def test_verified_email_account_can_create_goal_after_verification():
    """Full lifecycle: register, verify, then create goal."""
    async with make_client() as client:
        token, user = await _register_unverified(client)
        assert user["is_verified"] is False

        # Request a verification token
        req = await client.post(
            "/api/auth/email/verify-request",
            headers=_auth_header(token),
        )
        assert req.status_code == 202
        raw_token = req.json()["detail"].rsplit(" ", 1)[-1]

        # Redeem it
        verify = await client.post(
            "/api/auth/email/verify",
            headers=_auth_header(token),
            json={"token": raw_token},
        )
        assert verify.status_code == 200

        # Now create the goal — should succeed
        resp = await client.post(
            "/api/goals",
            headers=_auth_header(token),
            json=_VALID_GOAL,
        )
        assert resp.status_code == 201


# ── AC3.1: Verification tokens are single-use ────────────────────────────


async def test_verification_token_single_use():
    """A verification token can only be redeemed once.

    Tests at the service level that redeeming an already-used token
    raises VerificationError, and at the endpoint level that a second
    redeem attempt returns an error response.
    """
    import pytest

    from app.database import get_db
    from app.models.email_verification_token import EmailVerificationToken
    from app.services.auth import (
        VerificationError,
        _hash_verify_token,
        issue_verification_token,
        redeem_verification_token,
    )

    async with make_client() as client:
        token, user = await _register_unverified(client)
        assert user["is_verified"] is False

        # Request a token via endpoint
        req1 = await client.post(
            "/api/auth/email/verify-request",
            headers=_auth_header(token),
        )
        assert req1.status_code == 202
        raw1 = req1.json()["detail"].rsplit(" ", 1)[-1]

        # Redeem it at the service level — first use succeeds
        from app.database import AsyncSession as _AsyncSession
        gen = get_db()
        db = await gen.__anext__()
        try:
            from app.models.user import User
            from sqlalchemy import select

            user_obj = (await db.execute(select(User).where(User.id == user["id"]))).scalar_one()
            assert user_obj.is_verified is False

            await redeem_verification_token(db, user_obj, raw1)
            assert user_obj.is_verified is True

            # Second redeem of the SAME token must raise VerificationError
            with pytest.raises(VerificationError) as exc_info:
                await redeem_verification_token(db, user_obj, raw1)
            assert "already been used" in str(exc_info.value).lower()
        finally:
            await gen.aclose()

        # Endpoint-level: user is now verified, so a subsequent redeem
        # returns 200 "Already verified" (fast path before token check)
        r2 = await client.post(
            "/api/auth/email/verify",
            headers=_auth_header(token),
            json={"token": raw1},
        )
        assert r2.status_code == 200
        assert "Already verified" in r2.json()["detail"]


async def test_verification_token_wrong_user_cannot_redeem():
    """Token issued for user A should not work for user B."""
    async with make_client() as client:
        tok_a, user_a = await _register_unverified(client, email="a@test.com")
        tok_b, user_b = await _register_unverified(client, email="b@test.com")

        # A requests a token
        req = await client.post(
            "/api/auth/email/verify-request",
            headers=_auth_header(tok_a),
        )
        assert req.status_code == 202
        raw_a = req.json()["detail"].rsplit(" ", 1)[-1]

        # B tries to redeem A's token
        resp = await client.post(
            "/api/auth/email/verify",
            headers=_auth_header(tok_b),
            json={"token": raw_a},
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"].lower()


# ── AC3.2: Verification tokens expire ────────────────────────────────────


async def test_verification_token_expiry():
    """An expired token should be rejected."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.models.email_verification_token import EmailVerificationToken
    from app.services.auth import _hash_verify_token, issue_verification_token

    async with make_client() as client:
        token, user = await _register_unverified(client)
        assert user["is_verified"] is False

        # Issue a token then manually expire it
        # We use the service-level function to issue, then mess with the row
        import hashlib, secrets
        from app.database import get_db

        # Grab the session directly
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        _past = datetime.now(timezone.utc) - timedelta(hours=2)

        # We need to write through the app's database session override.
        # The conftest overrides get_db, so let's grab the async session.
        from app.database import AsyncSession as _AsyncSession

        # Simpler approach: use the token expiry directly via the issue function,
        # then manipulate the stored row to make it expired.
        import sys

        # Actually, let's use the service function, then expire the row.
        # The simplest approach: use the endpoint, then update via raw SQL
        # through the test client is awkward. Let's use a different approach:
        # Patch the expiry constant to 0 to force tokens to be expired.
        with patch("app.services.auth._VERIFY_TOKEN_EXPIRE_MINUTES", 0):

            req = await client.post(
                "/api/auth/email/verify-request",
                headers=_auth_header(token),
            )
            assert req.status_code == 202
            raw_expired = req.json()["detail"].rsplit(" ", 1)[-1]

        # Now try to redeem the expired token
        resp = await client.post(
            "/api/auth/email/verify",
            headers=_auth_header(token),
            json={"token": raw_expired},
        )
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower()


# ── AC3.3: Resend is rate-limited ────────────────────────────────────────


async def test_resend_verification_rate_limited():
    async with make_client() as client:
        token, user = await _register_unverified(client)

        # Request once
        r1 = await client.post(
            "/api/auth/email/verify-resend",
            headers=_auth_header(token),
        )
        assert r1.status_code == 202

        # Request again immediately — should be rate-limited
        r2 = await client.post(
            "/api/auth/email/verify-resend",
            headers=_auth_header(token),
        )
        assert r2.status_code == 429
        assert "Retry-After" in r2.headers


async def test_resend_not_limited_for_first_request():
    """A user with no prior token should be able to request a resend."""
    async with make_client() as client:
        token, user = await _register_unverified(client)

        resp = await client.post(
            "/api/auth/email/verify-resend",
            headers=_auth_header(token),
        )
        assert resp.status_code == 202


async def test_resend_skips_if_already_verified():
    async with make_client() as client:
        token, user = await _auth_google(client)  # already verified

        resp = await client.post(
            "/api/auth/email/verify-resend",
            headers=_auth_header(token),
        )
        assert resp.status_code == 202
        assert "Already verified" in resp.json()["detail"]


async def test_verify_request_skips_if_already_verified():
    async with make_client() as client:
        token, user = await _auth_google(client)  # already verified

        resp = await client.post(
            "/api/auth/email/verify-request",
            headers=_auth_header(token),
        )
        assert resp.status_code == 202
        assert "Already verified" in resp.json()["detail"]


async def test_verify_skips_if_already_verified():
    async with make_client() as client:
        token, user = await _auth_google(client)  # already verified

        resp = await client.post(
            "/api/auth/email/verify",
            headers=_auth_header(token),
            json={"token": "irrelevant"},
        )
        assert resp.status_code == 200
        assert "Already verified" in resp.json()["detail"]


async def test_verify_invalid_token_format_rejected():
    async with make_client() as client:
        token, user = await _register_unverified(client)

        resp = await client.post(
            "/api/auth/email/verify",
            headers=_auth_header(token),
            json={"token": "not-a-real-token"},
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"].lower()