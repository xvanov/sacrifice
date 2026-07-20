"""Tests for email verification token lifecycle and verified-vs-unverified auth.

These tests exercise:
* Token issuance and redemption (happy path)
* Expired token rejection
* Single-use rejection (replay after successful redemption)
* Verified vs unverified authorization on the selected sensitive path (POST /api/goals)
* Unchanged behavior for previously-verified OAuth accounts
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.auth import (
    EMAIL_VERIFY_PURPOSE,
    _create_signed_token,
)


def _make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Helpers ────────────────────────────────────────────────────────────


async def _register(client, email="verify@example.com", password="s3cret!Test"):
    """Register a new email/password account and return (token, user)."""
    resp = await client.post(
        "/api/auth/email/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return data["access_token"], data["user"]


async def _request_verification_token(client, auth_token):
    """Request a verification token for the authenticated user."""
    resp = await client.post(
        "/api/auth/email/verify/request",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    return resp


async def _verify_token(client, token):
    """Redeem a verification token (unauthenticated endpoint)."""
    resp = await client.post(
        "/api/auth/email/verify",
        json={"token": token},
    )
    return resp


async def _create_goal(client, auth_token, title="Ship the MVP"):
    """Attempt to create a goal; returns the response."""
    return await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={
            "title": title,
            "description": "Test goal for verification tests",
            "deadline": "2026-06-01T00:00:00Z",
            "pledge_amount": 5000,
            "goal_type": "youtube_video",
            "criteria": {"min_duration_seconds": 300, "video_description": "Test"},
            "charity_id": "acct_charity123",
        },
    )


# ── Token issuance & redemption (happy path) ──────────────────────────


async def test_issue_and_redeem_verification_token_marks_account_verified():
    """AC1.1, AC2.1, AC2.2: new email/password account restricted until token redeemed."""
    async with _make_client() as client:
        token, user = await _register(client)

        # Unverified — cannot create a goal
        resp = await _create_goal(client, token)
        assert resp.status_code == 403, resp.text
        assert "Email verification required" in resp.json()["detail"]

        # Request a verification token
        req_resp = await _request_verification_token(client, token)
        assert req_resp.status_code == 200, req_resp.text
        verify_token_str = req_resp.json()["verification_token"]

        # Redeem it
        redeem_resp = await _verify_token(client, verify_token_str)
        assert redeem_resp.status_code == 200, redeem_resp.text
        assert redeem_resp.json()["detail"] == "Email verified"

        # Now goal creation succeeds (verified authorization path, AC3.2)
        goal_resp = await _create_goal(client, token)
        assert goal_resp.status_code == 201, goal_resp.text


async def test_verification_token_is_signed_expiring_jwt():
    """AC2.1, AC2.2: tokens are cryptographically signed and expiring."""
    async with _make_client() as client:
        token, user = await _register(client)
        req_resp = await _request_verification_token(client, token)
        verify_token_str = req_resp.json()["verification_token"]

        from app.services.auth import decode_email_verification_token

        payload = decode_email_verification_token(verify_token_str)
        assert payload is not None, "Token must decode"
        assert payload.get("sub") == user["id"]
        assert payload.get("purpose") == "email_verify"
        assert payload.get("jti") is not None
        assert "exp" in payload, "Token must have an expiry claim"


async def test_request_verification_token_for_already_verified_returns_409():
    async with _make_client() as client:
        token, user = await _register(client)
        # Issue and redeem
        req_resp = await _request_verification_token(client, token)
        verify_token_str = req_resp.json()["verification_token"]
        await _verify_token(client, verify_token_str)

        # Request again — should be rejected
        req_resp2 = await _request_verification_token(client, token)
        assert req_resp2.status_code == 409, req_resp2.text
        assert "already verified" in req_resp2.json()["detail"]


# ── Expired token rejection (AC2.5) ────────────────────────────────────


async def test_expired_verification_token_is_rejected():
    """AC2.5: expired verification token is rejected."""
    async with _make_client() as client:
        token, user = await _register(client, email="expired-test@example.com")
        req_resp = await _request_verification_token(client, token)
        verify_token_str = req_resp.json()["verification_token"]

        # Verify the fresh token works
        redeem_resp = await _verify_token(client, verify_token_str)
        assert redeem_resp.status_code == 200

        # Craft an already-expired token for the same user (with a fresh jti
        # that doesn't match the stored one) and verify it is rejected at
        # decode time — before any jti check — because the exp claim is in
        # the past.
        expired_token = _create_signed_token(
            user["id"],
            purpose=EMAIL_VERIFY_PURPOSE,
            expires_in=timedelta(seconds=-1),
            extra_claims={"jti": str(uuid.uuid4())},
        )

        expired_resp = await _verify_token(client, expired_token)
        assert expired_resp.status_code == 401, expired_resp.text
        assert "expired" in expired_resp.json()["detail"].lower()


# ── Single-use enforcement (AC2.3, AC2.4) ──────────────────────────────


async def test_reusing_redeemed_verification_token_is_rejected():
    """AC2.3, AC2.4: token consumed after first redemption; replay rejected."""
    async with _make_client() as client:
        token, user = await _register(client)
        req_resp = await _request_verification_token(client, token)
        verify_token_str = req_resp.json()["verification_token"]

        # First redemption succeeds
        redeem1 = await _verify_token(client, verify_token_str)
        assert redeem1.status_code == 200, redeem1.text

        # Second redemption with the same token fails
        redeem2 = await _verify_token(client, verify_token_str)
        assert redeem2.status_code == 401, redeem2.text
        assert "already been used" in redeem2.json()["detail"]


# ── Verified vs unverified authorization on selected path (AC3.1, AC3.2) ─


async def test_unverified_account_cannot_create_goal_ac3_1():
    """AC3.1: unverified account gets 403 when creating a goal."""
    async with _make_client() as client:
        token, user = await _register(client, email="unver@example.com")

        resp = await _create_goal(client, token)
        assert resp.status_code == 403, resp.text
        assert "Email verification required" in resp.json()["detail"]


async def test_verified_account_can_create_goal_ac3_2():
    """AC3.2: verified account can create a goal (201)."""
    async with _make_client() as client:
        token, user = await _register(client, email="verified@example.com")

        # Verify first
        req_resp = await _request_verification_token(client, token)
        await _verify_token(client, req_resp.json()["verification_token"])

        resp = await _create_goal(client, token)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "id" in body
        assert body["title"] == "Ship the MVP"


# ── Unchanged behavior for already-verified OAuth accounts ─────────────


async def test_oauth_account_can_create_goal():
    """OAuth users default to email_verified=True and can create goals."""
    async with _make_client() as client:
        # Create an OAuth-authenticated user (Google mock)
        with patch("app.routes.auth.verify_google_token") as mock:
            mock.return_value = {
                "email": "oauthuser@example.com",
                "name": "OAuth User",
                "sub": "google-sub-999",
                "picture": None,
                "email_verified": True,
            }
            resp = await client.post("/api/auth/google", json={"token": "valid-token"})
            data = resp.json()
            oauth_token = data["access_token"]

        resp = await _create_goal(client, oauth_token, title="OAuth Goal")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "OAuth Goal"