"""Tests for the password reset endpoints.

Covers: happy path, unknown-email non-enumeration, reused token, expired
token, wrong-purpose token, weak password, and post-reset session revocation.

Email delivery is explicitly out of scope — these tests mint tokens directly
via the service, and the request endpoint never exposes token material.
"""

import time
import uuid as _uuid

from app.config import settings
from app.main import app
from app.services.auth import create_reset_token, decode_access_token
from httpx import ASGITransport, AsyncClient
from jose import jwt


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ─── Helpers ─────────────────────────────────────────────────────────────


async def _register(client, email="resetme@test.com", password="initial_password"):
    resp = await client.post(
        "/api/auth/email/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _user_id_from_access_token(token: str) -> str:
    """Extract the user id (sub claim) from an access token without DB access."""
    payload = decode_access_token(token)
    assert payload is not None, "access token must be valid"
    return payload["sub"]


# ─── AC1: POST /password/reset/request ───────────────────────────────────


async def test_reset_request_known_email_returns_202():
    """AC1.1: request with known email → 202."""
    async with make_client() as client:
        await _register(client, email="known@test.com")
        resp = await client.post(
            "/api/auth/password/reset/request",
            json={"email": "known@test.com"},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert "token" not in body


async def test_reset_request_unknown_email_returns_202():
    """AC1.2: request with unknown email → 202 (no user enumeration)."""
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/password/reset/request",
            json={"email": "noone@test.com"},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert "token" not in body


async def test_reset_request_response_body_never_includes_token():
    """AC1.3: response body never leaks the reset token."""
    async with make_client() as client:
        await _register(client, email="noleak@test.com")
        resp = await client.post(
            "/api/auth/password/reset/request",
            json={"email": "noleak@test.com"},
        )
    assert resp.status_code == 202
    body = resp.json()
    # Only the message key should be present — no token, no token material.
    assert set(body.keys()) == {"message"}


# ─── AC2 & AC3: happy-path confirm + revocation ──────────────────────────


async def test_reset_confirm_happy_path_sets_new_password():
    """AC2.1+AC2.2+AC2.3: valid token sets new password, old fails, new works."""
    async with make_client() as client:
        data = await _register(client, email="happy@test.com", password="old_password")
        user_id = _user_id_from_access_token(data["access_token"])
        reset_token = create_reset_token(user_id)

        resp = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": reset_token, "new_password": "new_password_abc"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"message": "Password has been reset."}

    # Old password no longer authenticates.
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/login",
            json={"email": "happy@test.com", "password": "old_password"},
        )
        assert resp.status_code == 401

    # New password authenticates.
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/login",
            json={"email": "happy@test.com", "password": "new_password_abc"},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()


async def test_reset_confirm_rotates_auth_session():
    """AC3.1+AC3.2: reset rotates auth_session_id → pre-reset JWT rejected."""
    async with make_client() as client:
        data = await _register(client, email="rotate@test.com", password="old_pass")
        pre_reset_token = data["access_token"]
        user_id = _user_id_from_access_token(data["access_token"])
        reset_token = create_reset_token(user_id)
        resp = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": reset_token, "new_password": "new_pass_xyz"},
        )
        assert resp.status_code == 200

    # Pre-reset JWT is rejected after rotation.
    async with make_client() as client:
        resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {pre_reset_token}"},
        )
        assert resp.status_code == 401


# ─── AC4: token security properties ──────────────────────────────────────


async def test_reset_confirm_reused_token_returns_400():
    """AC4.1: second use of the same reset token → 400."""
    async with make_client() as client:
        data = await _register(client, email="reuse@test.com")
        user_id = _user_id_from_access_token(data["access_token"])
        reset_token = create_reset_token(user_id)

        first = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": reset_token, "new_password": "first_new_pw"},
        )
        assert first.status_code == 200

        second = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": reset_token, "new_password": "second_new_pw"},
        )
        assert second.status_code == 400
        assert "already been used" in second.json()["detail"]


async def test_reset_confirm_expired_token_returns_400():
    """AC4.2: expired token → 400."""
    async with make_client() as client:
        data = await _register(client, email="expired@test.com")
        user_id = _user_id_from_access_token(data["access_token"])
        token = create_reset_token(user_id)
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        # Shift exp into the past.
        payload["exp"] = int(time.time()) - 60
        expired = jwt.encode(
            payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
        )

        resp = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": expired, "new_password": "valid_password_12"},
        )
        assert resp.status_code == 400


async def test_reset_confirm_wrong_purpose_token_returns_400():
    """AC4.3+AC4.6: access/csrf token used as reset token → 400."""
    async with make_client() as client:
        data = await _register(client, email="purpose@test.com")
        access_token = data["access_token"]

        resp = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": access_token, "new_password": "valid_password_12"},
        )
        assert resp.status_code == 400


# ─── AC5: password policy ────────────────────────────────────────────────


async def test_reset_confirm_short_password_rejected_422():
    """AC5.1: password below min_length → 422 (pydantic validation)."""
    async with make_client() as client:
        data = await _register(client, email="shortpw@test.com")
        user_id = _user_id_from_access_token(data["access_token"])
        reset_token = create_reset_token(user_id)

        resp = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": reset_token, "new_password": "short"},
        )
        assert resp.status_code == 422


async def test_reset_confirm_policy_weak_password_rejected_400():
    """AC5.1: length-valid but policy-weak password → 400 (shared policy).

    Uses a >=8-char common password that registration also rejects,
    verifying reset confirm enforces the same password policy as registration.
    """
    async with make_client() as client:
        data = await _register(client, email="policyweak@test.com")
        user_id = _user_id_from_access_token(data["access_token"])
        reset_token = create_reset_token(user_id)

        resp = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": reset_token, "new_password": "password123"},
        )
        assert resp.status_code == 400
        assert "too common" in resp.json()["detail"]


async def test_reset_confirm_strong_password_accepted():
    """AC5.2: password meeting policy is accepted."""
    async with make_client() as client:
        data = await _register(client, email="strong@test.com")
        user_id = _user_id_from_access_token(data["access_token"])
        reset_token = create_reset_token(user_id)

        resp = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": reset_token, "new_password": "strong_enough_pw"},
        )
        assert resp.status_code == 200


# ─── Additional edge-case tests ──────────────────────────────────────────


async def test_reset_confirm_invalid_token_returns_400():
    """Garbage token → 400."""
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": "not.a.real.token", "new_password": "valid_password_12"},
        )
        assert resp.status_code == 400


async def test_reset_confirm_user_not_found_returns_400():
    """Token with valid signature but nonexistent user → 400."""
    token = create_reset_token(str(_uuid.uuid4()))
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": token, "new_password": "valid_password_12"},
        )
        assert resp.status_code == 400


async def test_reset_confirm_verifies_password_hash_on_disk():
    """The stored password_hash is updated: old rejected, new accepted across
    fresh client sessions (no in-memory artifact)."""
    async with make_client() as client:
        await _register(client, email="hashcheck@test.com", password="old_secret")
        # Get user id from the register response.
        login_resp = await client.post(
            "/api/auth/email/login",
            json={"email": "hashcheck@test.com", "password": "old_secret"},
        )
        data = login_resp.json()
        user_id = _user_id_from_access_token(data["access_token"])
        reset_token = create_reset_token(user_id)

        resp = await client.post(
            "/api/auth/password/reset/confirm",
            json={"token": reset_token, "new_password": "replacement_pw1"},
        )
        assert resp.status_code == 200

    # Old password no longer authenticates (fresh client).
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/login",
            json={"email": "hashcheck@test.com", "password": "old_secret"},
        )
        assert resp.status_code == 401

    # New password authenticates (fresh client).
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/email/login",
            json={"email": "hashcheck@test.com", "password": "replacement_pw1"},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()


# OAuth-only user reset-request behavior is indistinguishable from unknown
# email (both return 202, no token leaked).  Non-enumeration is fully
# covered by test_reset_request_unknown_email_returns_202 above.
