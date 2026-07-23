"""Tests for password-reset and post-reset session revocation.

Covers:
- Non-enumerating reset-request responses (AC1.1)
- Single-use token enforcement (AC2.1)
- Expired-token rejection (AC2.2)
- Token invalidation on successful reset (AC2.3)
- Active session revocation after password reset (AC3.1)
"""
import hashlib
from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models.user import User
from app.services.auth import create_password_reset_token


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


_NON_ENUMERATING_MESSAGE = (
    "If an account with that email exists, a password reset link has been sent."
)


# ── Helpers ────────────────────────────────────────────────────────────


async def _register_user(client, email="reset@test.com", password="OldP@ssw0rd!") -> dict:
    """Register a user via the email endpoint and return the parsed JSON body."""
    resp = await client.post(
        "/api/auth/email/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()


async def _request_reset(client, email="reset@test.com") -> dict:
    """Request a password reset and return the parsed JSON body."""
    resp = await client.post(
        "/api/auth/password/reset-request",
        json={"email": email},
    )
    assert resp.status_code == 200
    return resp.json()


async def _create_reset_token_for_email(
    client, email="reset@test.com", password="OldP@ssw0rd!"
) -> str:
    """Create a real password-reset token for a user via the service layer.

    Registers the user (if not already), then calls
    ``create_password_reset_token`` directly so tests can exercise the
    confirm path without the reset-request endpoint leaking a token.
    Returns the raw token string.
    """
    # Ensure the user exists.
    await _register_user(client, email=email, password=password)

    from tests.conftest import TEST_DB_URL

    engine = create_async_engine(TEST_DB_URL, echo=False)
    sesh = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sesh() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        raw_token = await create_password_reset_token(db, user)
    return raw_token


async def _login(client, email="reset@test.com", password="OldP@ssw0rd!") -> dict:
    resp = await client.post(
        "/api/auth/email/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()


# ─── AC1.1: Non-enumerating reset-request responses ────────────────────


async def test_reset_request_known_email_returns_non_enumerating_response():
    """AC1.1: Requesting a reset for a registered email returns 200 with
    only the message key — no token is leaked in the API response."""
    async with make_client() as client:
        await _register_user(client)
        resp = await client.post(
            "/api/auth/password/reset-request",
            json={"email": "reset@test.com"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"message": _NON_ENUMERATING_MESSAGE}
    assert "token" not in body


async def test_reset_request_unknown_email_returns_same_shape():
    """AC1.1: Requesting a reset for an unknown email returns the exact
    same response body as a known-email request — no token, no account
    hint."""
    async with make_client() as client:
        await _register_user(client)
        known_resp = await client.post(
            "/api/auth/password/reset-request",
            json={"email": "reset@test.com"},
        )
        assert known_resp.status_code == 200

        unknown_resp = await client.post(
            "/api/auth/password/reset-request",
            json={"email": "nobody@test.com"},
        )
    assert unknown_resp.status_code == 200
    assert unknown_resp.json() == known_resp.json()
    assert "token" not in unknown_resp.json()


async def test_reset_request_oauth_only_user_returns_same_shape():
    """AC1.1: A Google/OAuth account cannot use password reset (no
    password to reset), but the response is indistinguishable from a
    real email-account reset request."""
    async with make_client() as client:
        # Create an OAuth-only user and an email user so we can compare
        # response shapes.
        from tests.conftest import TEST_DB_URL

        engine = create_async_engine(TEST_DB_URL, echo=False)
        sesh = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sesh() as db:
            user = User(
                email="oauth@test.com",
                display_name="OAuth User",
                auth_provider="google",
                auth_provider_id="google-12345",
                email_verified=True,
            )
            db.add(user)
            await db.commit()

        # Also register an email user so we have a baseline response shape.
        await _register_user(client, email="emailuser@test.com")
        email_resp = await client.post(
            "/api/auth/password/reset-request",
            json={"email": "emailuser@test.com"},
        )
        assert email_resp.status_code == 200

        oauth_resp = await client.post(
            "/api/auth/password/reset-request",
            json={"email": "oauth@test.com"},
        )
        assert oauth_resp.status_code == 200
        assert oauth_resp.json() == email_resp.json()
        assert "token" not in oauth_resp.json()


# ─── AC2.2: Expired-token rejection ────────────────────────────────────


async def test_expired_reset_token_is_rejected():
    """AC2.2: An expired token must not allow password reset."""
    async with make_client() as client:
        token = await _create_reset_token_for_email(client)

        # Expire the token manually in the DB.
        from tests.conftest import TEST_DB_URL

        engine = create_async_engine(TEST_DB_URL, echo=False)
        sesh = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sesh() as db:
            digest = _sha256(token)
            await db.execute(
                text(
                    "UPDATE password_reset_tokens SET expires_at = :exp "
                    "WHERE token_hash = :hash"
                ),
                {
                    "exp": datetime.now(timezone.utc) - timedelta(hours=1),
                    "hash": digest,
                },
            )
            await db.commit()

        resp = await client.post(
            "/api/auth/password/reset-confirm",
            json={"token": token, "new_password": "NewP@ssw0rd!"},
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired reset token"


# ─── AC2.1 & AC2.3: Single-use token enforcement ───────────────────────


async def test_reset_token_is_single_use():
    """AC2.1: A reset token cannot be used more than once."""
    async with make_client() as client:
        token = await _create_reset_token_for_email(client)

        # First use succeeds.
        resp1 = await client.post(
            "/api/auth/password/reset-confirm",
            json={"token": token, "new_password": "NewP@ssw0rd!"},
        )
        assert resp1.status_code == 200
        assert resp1.json()["message"] == "Password has been reset successfully."

        # Second use with the same token fails.
        resp2 = await client.post(
            "/api/auth/password/reset-confirm",
            json={"token": token, "new_password": "AnotherP@ss1!"},
        )
    assert resp2.status_code == 401
    assert resp2.json()["detail"] == "Invalid or expired reset token"


async def test_reset_token_invalidated_after_success():
    """AC2.3: After a successful reset, the consumed token is marked
    used in the database."""
    async with make_client() as client:
        token = await _create_reset_token_for_email(client)

        resp = await client.post(
            "/api/auth/password/reset-confirm",
            json={"token": token, "new_password": "NewP@ssw0rd!"},
        )
        assert resp.status_code == 200

        # Verify the DB row is marked used.
        from tests.conftest import TEST_DB_URL

        engine = create_async_engine(TEST_DB_URL, echo=False)
        sesh = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sesh() as db:
            digest = _sha256(token)
            result = await db.execute(
                text("SELECT consumed FROM password_reset_tokens WHERE token_hash = :hash"),
                {"hash": digest},
            )
            row = result.fetchone()
    assert row is not None
    assert row[0] is True


async def test_unknown_token_is_rejected():
    """A token that was never issued returns 401."""
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/password/reset-confirm",
            json={"token": "this-is-a-made-up-token", "new_password": "NewP@ssw0rd!"},
        )
    assert resp.status_code == 401


# ─── AC3.1: Active session revocation after password reset ─────────────


async def test_password_reset_revokes_existing_sessions():
    """AC3.1: After a password reset, previously-issued bearer tokens
    are rejected by the backend."""
    async with make_client() as client:
        token = await _create_reset_token_for_email(client)

        # Log in to get a bearer token.
        login_body = await _login(client)
        old_token = login_body["access_token"]

        # Verify the old token works.
        me_resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert me_resp.status_code == 200

        # Reset the password.
        confirm_resp = await client.post(
            "/api/auth/password/reset-confirm",
            json={"token": token, "new_password": "NewP@ssw0rd!"},
        )
        assert confirm_resp.status_code == 200

        # The old bearer token is now revoked.
        me_resp2 = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {old_token}"},
        )
    assert me_resp2.status_code == 401
    assert me_resp2.json()["detail"] == "Token has been revoked"


async def test_password_reset_old_token_revoked_new_login_works():
    """AC3.1: After reset, old tokens are dead but a fresh login with
    the new password succeeds."""
    async with make_client() as client:
        token = await _create_reset_token_for_email(client)

        login_body = await _login(client)
        old_token = login_body["access_token"]

        await client.post(
            "/api/auth/password/reset-confirm",
            json={"token": token, "new_password": "NewP@ssw0rd!"},
        )

        # Old token rejected.
        me_resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert me_resp.status_code == 401

        # Login with new password works.
        new_login = await client.post(
            "/api/auth/email/login",
            json={"email": "reset@test.com", "password": "NewP@ssw0rd!"},
        )
        assert new_login.status_code == 200
        new_token = new_login.json()["access_token"]

        me_resp2 = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {new_token}"},
        )
    assert me_resp2.status_code == 200


async def test_password_reset_only_revokes_target_user_sessions():
    """AC3.1: Resetting user A's password does NOT revoke user B's
    bearer token."""
    async with make_client() as client:
        # Create reset tokens for Alice via the service layer first (this
        # registers Alice too).
        alice_reset = await _create_reset_token_for_email(
            client, email="alice@test.com", password="AliceP@ss1!"
        )
        await _register_user(client, email="bob@test.com", password="BobP@ssword1!")

        # Both log in.
        alice_login = await _login(client, email="alice@test.com", password="AliceP@ss1!")
        bob_login = await _login(client, email="bob@test.com", password="BobP@ssword1!")

        alice_token = alice_login["access_token"]
        bob_token = bob_login["access_token"]

        # Alice resets her password.
        await client.post(
            "/api/auth/password/reset-confirm",
            json={"token": alice_reset, "new_password": "AliceNewP@ss!"},
        )

        # Alice's old token is revoked.
        me_alice = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert me_alice.status_code == 401

        # Bob's token still works.
        me_bob = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
    assert me_bob.status_code == 200


async def test_reset_confirm_rejects_short_password():
    """The new-password validation (min 8 chars) is enforced at the
    reset-confirm endpoint."""
    async with make_client() as client:
        token = await _create_reset_token_for_email(client)

        resp = await client.post(
            "/api/auth/password/reset-confirm",
            json={"token": token, "new_password": "short"},
        )
    assert resp.status_code == 422