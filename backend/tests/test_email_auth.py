"""Tests for the email + password auth endpoints.

These cover both the happy paths and the cross-provider 409 behavior
that lets the frontend point users at the right sign-in button instead
of creating duplicates or hijacking accounts.
"""

import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.rate_limiter import _store as _rate_limit_store
from app.main import app
from app.models.user import User, VerificationToken
from app.services.auth import _hash_token, generate_verification_token

pytestmark = pytest.mark.asyncio


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _clear_rate_limits():
    """Clear the rate-limiter store so rate-limited calls within a single
    test don't collide across different auth endpoints that share the same
    per-IP bucket."""
    _rate_limit_store.clear()


# ─── /api/auth/email/register ───


async def test_email_register_creates_user_and_returns_token():
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/register",
            json={
                "email": "newbie@test.com",
                "password": "correct horse battery",
                "display_name": "Newbie",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["user"]["email"] == "newbie@test.com"
    assert body["user"]["auth_provider"] == "email"
    assert body["user"]["display_name"] == "Newbie"


async def test_email_register_uses_email_prefix_when_display_name_omitted():
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/register",
            json={"email": "alice@test.com", "password": "longenoughpw"},
        )
    assert resp.status_code == 200
    assert resp.json()["user"]["display_name"] == "alice"


async def test_email_register_rejects_short_password():
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/register",
            json={"email": "x@test.com", "password": "short"},
        )
    assert resp.status_code == 422


async def test_email_register_rejects_policy_weak_password():
    """Length-valid but policy-weak password → 400 (shared policy)."""
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/register",
            json={"email": "weakpol@test.com", "password": "password123"},
        )
    assert resp.status_code == 400
    assert "too common" in resp.json()["detail"]


async def test_email_register_when_email_already_email_provider_returns_409():
    async with make_client() as client:
        first = await client.post(
            "/api/auth/email/register",
            json={"email": "dup@test.com", "password": "longenoughpw"},
        )
        assert first.status_code == 200
        second = await client.post(
            "/api/auth/email/register",
            json={"email": "dup@test.com", "password": "anotherlongpw"},
        )
    assert second.status_code == 409
    assert second.json() == {"error": "account_exists", "provider": "email"}


@patch("app.routes.auth.verify_google_token")
async def test_email_register_when_email_owned_by_google_returns_409_with_google(
    mock_verify,
):
    mock_verify.return_value = {
        "email": "owned@test.com",
        "name": "Owned",
        "sub": "google-sub-owned",
        "picture": None,
    }
    async with make_client() as client:
        google_resp = await client.post("/api/auth/google", json={"token": "valid"})
        assert google_resp.status_code == 200

        register_resp = await client.post(
            "/api/auth/email/register",
            json={"email": "owned@test.com", "password": "longenoughpw"},
        )
    assert register_resp.status_code == 409
    assert register_resp.json() == {
        "error": "account_exists",
        "provider": "google",
    }


# ─── /api/auth/email/login ───


async def test_email_login_happy_path():
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "login@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200

        resp = await client.post(
            "/api/auth/email/login",
            json={"email": "login@test.com", "password": "longenoughpw"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["user"]["email"] == "login@test.com"
    assert body["user"]["auth_provider"] == "email"


async def test_email_login_wrong_password_returns_401():
    async with make_client() as client:
        await client.post(
            "/api/auth/email/register",
            json={"email": "wpw@test.com", "password": "correctpw1234"},
        )
        resp = await client.post(
            "/api/auth/email/login",
            json={"email": "wpw@test.com", "password": "wrongpw99999"},
        )
    assert resp.status_code == 401
    assert resp.json() == {"error": "invalid_credentials"}


async def test_email_login_unknown_email_returns_401():
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/login",
            json={"email": "ghost@test.com", "password": "anypassword"},
        )
    assert resp.status_code == 401
    assert resp.json() == {"error": "invalid_credentials"}


@patch("app.routes.auth.verify_google_token")
async def test_email_login_for_google_account_returns_409_not_401(mock_verify):
    """KEY UX BEHAVIOR — distinguish 'wrong password' from 'use Google'."""
    mock_verify.return_value = {
        "email": "g@test.com",
        "name": "G User",
        "sub": "g-sub",
        "picture": None,
    }
    async with make_client() as client:
        google_resp = await client.post("/api/auth/google", json={"token": "valid"})
        assert google_resp.status_code == 200

        resp = await client.post(
            "/api/auth/email/login",
            json={"email": "g@test.com", "password": "anyguess1234"},
        )
    assert resp.status_code == 409
    assert resp.json() == {"error": "account_exists", "provider": "google"}


@patch("app.routes.auth.exchange_github_code")
async def test_email_login_for_github_account_returns_409_with_github(
    mock_exchange,
):
    mock_exchange.return_value = {
        "email": "gh@test.com",
        "login": "ghu",
        "name": "GH User",
        "id": "ghid",
        "avatar_url": None,
    }
    async with make_client() as client:
        github_resp = await client.post("/api/auth/github", json={"code": "valid"})
        assert github_resp.status_code == 200

        resp = await client.post(
            "/api/auth/email/login",
            json={"email": "gh@test.com", "password": "anyguess1234"},
        )
    assert resp.status_code == 409
    assert resp.json() == {"error": "account_exists", "provider": "github"}


async def test_email_login_is_case_insensitive_on_email():
    async with make_client() as client:
        await client.post(
            "/api/auth/email/register",
            json={"email": "MixedCase@Test.com", "password": "longenoughpw"},
        )
        resp = await client.post(
            "/api/auth/email/login",
            json={"email": "mixedcase@test.com", "password": "longenoughpw"},
        )
    assert resp.status_code == 200


# ─── AC1.1 / Task 3.5: email_verified in register response ───


async def test_email_register_returns_email_verified_false():
    """AC1.1: New email/password accounts are created with email_verified=false."""
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/register",
            json={
                "email": "unverified@test.com",
                "password": "longenoughpw",
                "display_name": "Unverified",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email_verified"] is False


@patch("app.routes.auth.verify_google_token")
async def test_oauth_user_is_preverified(mock_verify):
    """AC1.5 / Task 3.6: OAuth users have email_verified=true."""
    mock_verify.return_value = {
        "email": "oauth-verified@test.com",
        "name": "OAuth User",
        "sub": "oauth-sub-preverified",
        "picture": None,
    }
    async with make_client() as client:
        resp = await client.post("/api/auth/google", json={"token": "valid"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email_verified"] is True


# ─── AC1.6 / Task 4: GET /me email_verified ───


async def test_get_me_email_verified_before_and_after_verification():
    """AC1.6: GET /me returns email_verified reflecting current state."""
    async with make_client() as client:
        # Register
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": "meverify@test.com",
                "password": "longenoughpw",
                "display_name": "Me",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        # Before verification: email_verified=false
        me = await client.get("/api/auth/me", headers=hdr)
        assert me.status_code == 200
        assert me.json()["email_verified"] is False

        # Verify
        vr = await client.post("/api/auth/email/verify-request", headers=hdr)
        assert vr.status_code == 200
        verify_token = vr.json()["verification_token"]

        v = await client.post("/api/auth/email/verify", json={"verification_token": verify_token})
        assert v.status_code == 200

        # Re-auth to get fresh token with email_verified=True
        login = await client.post(
            "/api/auth/email/login",
            json={"email": "meverify@test.com", "password": "longenoughpw"},
        )
        assert login.status_code == 200
        new_token = login.json()["access_token"]
        new_hdr = {"Authorization": f"Bearer {new_token}"}

        # After verification: email_verified=true
        me2 = await client.get("/api/auth/me", headers=new_hdr)
        assert me2.status_code == 200
        assert me2.json()["email_verified"] is True


# ─── verify-request (task 5) ───


async def test_verify_request_unverified_user_returns_token():
    """Task 5.5: Unverified user → 200 with token + VerificationToken row."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": "vr-ok@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        resp = await client.post("/api/auth/email/verify-request", headers=hdr)
    assert resp.status_code == 200
    body = resp.json()
    assert "verification_token" in body
    assert len(body["verification_token"]) > 0


async def test_verify_request_already_verified_returns_409():
    """AC2.5 / Task 5.6: Already-verified account → 409."""
    async with make_client() as client:
        # Register
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": "already-done@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        # Verify once
        vr = await client.post("/api/auth/email/verify-request", headers=hdr)
        assert vr.status_code == 200
        verify_token = vr.json()["verification_token"]
        v = await client.post("/api/auth/email/verify", json={"verification_token": verify_token})
        assert v.status_code == 200

        # Clear shared rate-limiter bucket so the next verify-request doesn't
        # hit the 3/min limit from register+vr+verify above.
        _clear_rate_limits()

        # Same token still works — verify didn't rotate session.
        resp = await client.post("/api/auth/email/verify-request", headers=hdr)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "already_verified"


async def test_verify_request_no_auth_returns_401():
    """Task 5.7: No auth header → 401."""
    async with make_client() as client:
        resp = await client.post("/api/auth/email/verify-request")
    assert resp.status_code == 401


async def test_verify_request_cooldown_prevents_spam():
    """Task 5.8: Cooldown — cannot request a second token while one is outstanding."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": "cooldown-test@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        # First request succeeds
        first = await client.post("/api/auth/email/verify-request", headers=hdr)
        assert first.status_code == 200

        # Second request while first is outstanding → 429
        second = await client.post("/api/auth/email/verify-request", headers=hdr)
    assert second.status_code == 429


# ─── verify (task 6) ───


async def test_verify_valid_token_marks_user_verified():
    """Task 6.5: Valid token → 200, email_verified becomes true."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": "verify-me@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        vr = await client.post("/api/auth/email/verify-request", headers=hdr)
        assert vr.status_code == 200
        verify_token = vr.json()["verification_token"]

        v = await client.post("/api/auth/email/verify", json={"verification_token": verify_token})
    assert v.status_code == 200
    assert v.json() == {"message": "email_verified"}


async def test_verify_double_spend_returns_invalid_token():
    """AC2.2 / Task 6.6: Double-spend → 400 invalid_token."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": "double-spend@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        vr = await client.post("/api/auth/email/verify-request", headers=hdr)
        assert vr.status_code == 200
        verify_token = vr.json()["verification_token"]

        # First use succeeds
        v1 = await client.post("/api/auth/email/verify", json={"verification_token": verify_token})
        assert v1.status_code == 200

        # Second use fails
        v2 = await client.post("/api/auth/email/verify", json={"verification_token": verify_token})
    assert v2.status_code == 400
    assert v2.json()["detail"]["error"] == "invalid_token"


async def test_verify_expired_token_returns_token_expired():
    """Task 6.7: Expired token → 400 token_expired."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": "expired-tkn@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        user_id = reg.json()["user"]["id"]

        # Directly insert an expired token
        from app.database import get_db
        from app.main import app
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        # Find the DB session from the override
        test_engine = create_async_engine(settings.database_url, echo=False)
        test_async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        async with test_async_session() as db:
            plaintext = generate_verification_token()
            expired_token = VerificationToken(
                user_id=user_id,
                token_hash=_hash_token(plaintext),
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                used=False,
            )
            db.add(expired_token)
            await db.commit()

            # Attempt verify with the expired token
            v = await client.post("/api/auth/email/verify", json={"verification_token": plaintext})
        assert v.status_code == 400
        assert v.json()["detail"]["error"] == "token_expired"

        # Cleanup
        async with test_async_session() as db:
            await db.execute(text("DELETE FROM verification_tokens WHERE user_id = :uid"), {"uid": user_id})
            await db.commit()

    await test_engine.dispose()


async def test_verify_unknown_token_returns_invalid_token():
    """Task 6.8: Unknown token → 400 invalid_token."""
    async with make_client() as client:
        fake_token = generate_verification_token()
        v = await client.post("/api/auth/email/verify", json={"verification_token": fake_token})
    assert v.status_code == 400
    assert v.json()["detail"]["error"] == "invalid_token"


# ─── force-expire (task 7) ───


async def test_force_expire_then_reuse_returns_token_expired():
    """Task 7.5 / AC2.3+AC2.4: Force-expire → 200, subsequent verify → 400 token_expired."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": "force-exp-me@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        vr = await client.post("/api/auth/email/verify-request", headers=hdr)
        assert vr.status_code == 200
        verify_token = vr.json()["verification_token"]

        # Force-expire
        fe = await client.delete("/api/auth/email/verify-token", headers=hdr)
        assert fe.status_code == 200
        assert fe.json() == {"message": "token_invalidated"}

        # Attempt to use the force-expired token → should be token_expired
        v = await client.post("/api/auth/email/verify", json={"verification_token": verify_token})
    assert v.status_code == 400
    assert v.json()["detail"]["error"] == "token_expired"


async def test_force_expire_no_outstanding_token_returns_404():
    """Task 7.6: No outstanding token → 404."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": "no-token-me@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        fe = await client.delete("/api/auth/email/verify-token", headers=hdr)
    assert fe.status_code == 404


async def test_force_expire_cross_user_isolation():
    """Task 7.7: Cannot expire another user's token + User A's token still works."""
    async with make_client() as client:
        # User A: register + get token
        ra = await client.post(
            "/api/auth/email/register",
            json={
                "email": f"user-a-{uuid_mod.uuid4().hex[:8]}@test.com",
                "password": "longenoughpw",
            },
        )
        assert ra.status_code == 200
        ta = ra.json()["access_token"]
        ha = {"Authorization": f"Bearer {ta}"}

        # User A requests verification token
        vr = await client.post("/api/auth/email/verify-request", headers=ha)
        assert vr.status_code == 200
        user_a_verify_token = vr.json()["verification_token"]

        # User B: register
        rb = await client.post(
            "/api/auth/email/register",
            json={
                "email": f"user-b-{uuid_mod.uuid4().hex[:8]}@test.com",
                "password": "longenoughpw",
            },
        )
        assert rb.status_code == 200
        tb = rb.json()["access_token"]
        hb = {"Authorization": f"Bearer {tb}"}

        # User B tries to force-expire User A's token — should be 404 because
        # the endpoint only looks at the caller's own tokens
        fe = await client.delete("/api/auth/email/verify-token", headers=hb)
        assert fe.status_code == 404

        # User A's token still works (was NOT force-expired by B)
        v = await client.post(
            "/api/auth/email/verify",
            json={"verification_token": user_a_verify_token},
        )
        assert v.status_code == 200

        # And confirm User A's token is now consumed
        again = await client.post(
            "/api/auth/email/verify",
            json={"verification_token": user_a_verify_token},
        )
        assert again.status_code == 400
        assert again.json()["detail"]["error"] == "invalid_token"


# ─── Gating dependency (task 8) ───


async def test_unverified_user_cannot_create_goal():
    """AC1.2 / Task 8.5: Unverified user → POST /goals → 403."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": f"unv-gate-{uuid_mod.uuid4().hex[:8]}@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        # Confirm unverified
        assert reg.json()["user"]["email_verified"] is False

        future_deadline = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        goal_body = {
            "title": "Should be gated",
            "deadline": future_deadline,
            "pledge_amount": 500,
            "goal_type": "geolocation",
            "criteria": {"target_latitude": 40.7128, "target_longitude": -74.0060},
        }

        resp = await client.post("/api/goals", json=goal_body, headers=hdr)
    assert resp.status_code == 403


async def test_verified_user_can_create_goal():
    """AC1.4 / Task 8.6: Verified user → POST /goals → 2xx with goal id."""
    async with make_client() as client:
        # Register
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": f"v-gate-{uuid_mod.uuid4().hex[:8]}@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        # Verify
        vr = await client.post("/api/auth/email/verify-request", headers=hdr)
        assert vr.status_code == 200
        verify_token = vr.json()["verification_token"]
        v = await client.post("/api/auth/email/verify", json={"verification_token": verify_token})
        assert v.status_code == 200

        # Clear rate-limiter bucket before login to avoid 429 from shared bucket
        _clear_rate_limits()

        # Re-auth
        login = await client.post(
            "/api/auth/email/login",
            json={"email": reg.json()["user"]["email"], "password": "longenoughpw"},
        )
        assert login.status_code == 200
        new_token = login.json()["access_token"]
        new_hdr = {"Authorization": f"Bearer {new_token}"}

        future_deadline = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        goal_body = {
            "title": "Should work",
            "deadline": future_deadline,
            "pledge_amount": 500,
            "goal_type": "geolocation",
            "criteria": {"target_latitude": 40.7128, "target_longitude": -74.0060},
        }

        resp = await client.post("/api/goals", json=goal_body, headers=new_hdr)
    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body


@patch("app.routes.auth.verify_google_token")
async def test_oauth_user_not_gated_on_goal_creation(mock_verify):
    """AC1.5 / Task 8.7: OAuth user → POST /goals → 2xx (no gating)."""
    mock_verify.return_value = {
        "email": f"oauth-goal-{uuid_mod.uuid4().hex[:8]}@test.com",
        "name": "OAuth Goal",
        "sub": "oauth-sub-goal",
        "picture": None,
    }
    async with make_client() as client:
        ga = await client.post("/api/auth/google", json={"token": "valid"})
        assert ga.status_code == 200
        token = ga.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        # Confirm verified
        assert ga.json()["user"]["email_verified"] is True

        future_deadline = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        goal_body = {
            "title": "OAuth goal",
            "deadline": future_deadline,
            "pledge_amount": 500,
            "goal_type": "geolocation",
            "criteria": {"target_latitude": 40.7128, "target_longitude": -74.0060},
        }

        resp = await client.post("/api/goals", json=goal_body, headers=hdr)
    assert resp.status_code == 201
    assert "id" in resp.json()


# ─── AC1 Oracle flow (task 11) ───


async def test_ac1_oracle_flow_register_then_goal_403_then_verify_then_goal_2xx():
    """AC1: Full oracle flow — register → POST /goals 403 → verify →
    re-auth → POST /goals 2xx with created goal id."""
    async with make_client() as client:
        email = f"ac1-oracle-{uuid_mod.uuid4().hex[:8]}@test.com"
        password = "oracle-pass-1234"

        # Step 1: Register
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": email, "password": password},
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}
        assert reg.json()["user"]["email_verified"] is False

        # Step 2: POST /goals → 403 (gate active)
        future_deadline = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        goal_body = {
            "title": "AC1 Oracle",
            "deadline": future_deadline,
            "pledge_amount": 500,
            "goal_type": "geolocation",
            "criteria": {"target_latitude": 40.7128, "target_longitude": -74.0060},
        }
        g1 = await client.post("/api/goals", json=goal_body, headers=hdr)
        assert g1.status_code == 403

        # Step 3: verify-request
        vr = await client.post("/api/auth/email/verify-request", headers=hdr)
        assert vr.status_code == 200
        verify_token = vr.json()["verification_token"]

        # Step 4: verify
        v = await client.post("/api/auth/email/verify", json={"verification_token": verify_token})
        assert v.status_code == 200
        assert v.json() == {"message": "email_verified"}

        # Clear rate-limiter bucket before login
        _clear_rate_limits()

        # Step 5: Re-auth to get fresh token reflecting email_verified=True
        login = await client.post(
            "/api/auth/email/login",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200
        new_token = login.json()["access_token"]
        new_hdr = {"Authorization": f"Bearer {new_token}"}
        assert login.json()["user"]["email_verified"] is True

        # Step 6: POST /goals → 2xx with goal id (gate passed)
        g2 = await client.post("/api/goals", json=goal_body, headers=new_hdr)
        assert g2.status_code == 201
        body = g2.json()
        assert "id" in body
        assert body["id"] is not None


async def test_ac2_single_use_double_spend_returns_invalid_token():
    """AC2 single-use: Double-spend → 400 invalid_token."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": f"ac2-single-{uuid_mod.uuid4().hex[:8]}@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        vr = await client.post("/api/auth/email/verify-request", headers=hdr)
        assert vr.status_code == 200
        vt = vr.json()["verification_token"]

        # First use succeeds
        v1 = await client.post("/api/auth/email/verify", json={"verification_token": vt})
        assert v1.status_code == 200

        # Second use fails with invalid_token
        v2 = await client.post("/api/auth/email/verify", json={"verification_token": vt})
    assert v2.status_code == 400
    assert v2.json()["detail"]["error"] == "invalid_token"


async def test_ac2_short_lived_force_expire_then_reuse_returns_token_expired():
    """AC2 short-lived: Force-expire → 200, reuse → 400 token_expired."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={
                "email": f"ac2-short-{uuid_mod.uuid4().hex[:8]}@test.com",
                "password": "longenoughpw",
            },
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        vr = await client.post("/api/auth/email/verify-request", headers=hdr)
        assert vr.status_code == 200
        vt = vr.json()["verification_token"]

        # Force-expire
        fe = await client.delete("/api/auth/email/verify-token", headers=hdr)
        assert fe.status_code == 200
        assert fe.json() == {"message": "token_invalidated"}

        # Reuse fails with token_expired
        v = await client.post("/api/auth/email/verify", json={"verification_token": vt})
    assert v.status_code == 400
    assert v.json()["detail"]["error"] == "token_expired"


@patch("app.routes.auth.verify_google_token")
async def test_ac3_oauth_user_not_gated_on_goals(mock_verify):
    """AC3: OAuth user bypass of verification gate on POST /goals."""
    mock_verify.return_value = {
        "email": f"ac3-oauth-{uuid_mod.uuid4().hex[:8]}@test.com",
        "name": "AC3 OAuth",
        "sub": "ac3-oauth-sub",
        "picture": None,
    }
    async with make_client() as client:
        ga = await client.post("/api/auth/google", json={"token": "valid"})
        assert ga.status_code == 200
        token = ga.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        # OAuth user is pre-verified
        assert ga.json()["user"]["email_verified"] is True

        future_deadline = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        goal_body = {
            "title": "AC3 OAuth Goal",
            "deadline": future_deadline,
            "pledge_amount": 500,
            "goal_type": "geolocation",
            "criteria": {"target_latitude": 40.7128, "target_longitude": -74.0060},
        }

        resp = await client.post("/api/goals", json=goal_body, headers=hdr)
        assert resp.status_code == 201
        assert "id" in resp.json()


# ─── Token cleanup (task 10) ──────────────────────────────────────────


async def test_cleanup_expired_verification_tokens_removes_old_entries():
    """Task 10.3: Expired tokens older than 24h are removed by cleanup."""
    import uuid as uuid_mod

    from sqlalchemy import text

    email = f"cleanup-{uuid_mod.uuid4().hex[:8]}@test.com"
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": email, "password": "longenoughpw"},
        )
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        vr = await client.post("/api/auth/email/verify-request", headers=hdr)
        assert vr.status_code == 200
        verify_token = vr.json()["verification_token"]

    # Manually expire the token and run cleanup
    from app.services.auth import _hash_token, cleanup_expired_verification_tokens
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.models.user import VerificationToken

    test_engine = create_async_engine(settings.database_url, echo=False)
    test_async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    th = _hash_token(verify_token)
    async with test_async_session() as db:
        # Set the token's expires_at to 25 hours ago
        await db.execute(
            text("UPDATE verification_tokens SET expires_at = :exp WHERE token_hash = :th"),
            {"exp": datetime.now(timezone.utc) - timedelta(hours=25), "th": th},
        )
        await db.commit()

        deleted = await cleanup_expired_verification_tokens(db, older_than_hours=24)
        assert deleted >= 1

        # The token should now be gone
        from sqlalchemy import select

        row2 = (
            await db.execute(
                select(VerificationToken).where(VerificationToken.token_hash == th)
            )
        ).scalar_one_or_none()
        assert row2 is None

    await test_engine.dispose()
