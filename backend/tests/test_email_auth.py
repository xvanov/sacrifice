"""Tests for the email + password auth endpoints.

These cover both the happy paths and the cross-provider 409 behavior
that lets the frontend point users at the right sign-in button instead
of creating duplicates or hijacking accounts.
"""

from unittest.mock import patch

from app.main import app
from httpx import ASGITransport, AsyncClient


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


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
    assert body["user"]["email_verified"] is False


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


# ─── Email verification lifecycle ───────────────────────────────────────────
#
# These tests cover the full email verification flow (AC1, AC2, AC3):
#   AC1 — unverified accounts are gated from sensitive operations
#   AC2 — tokens are single-use, short-lived, and invalidated after use
#   AC3 — OAuth users pass through the gate


async def _register_unverified(client, email="fresh@test.com", password="longenoughpw"):
    resp = await client.post(
        "/api/auth/email/register",
        json={"email": email, "password": password, "display_name": "Fresh"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["access_token"], body["user"]


# ── AC1.1: register returns email_verified: false ──────────────────────


async def test_email_register_returns_email_verified_false():
    """AC1.1: new email/password account starts unverified."""
    async with make_client() as client:
        _, user = await _register_unverified(client)
    assert user["email_verified"] is False


# ── AC1.2: unverified account cannot create goals ─────────────────────


async def test_unverified_account_cannot_create_goal():
    """AC1.2: POST /api/goals rejects unverified accounts with 403."""
    async with make_client() as client:
        token, user = await _register_unverified(client)
        assert user["email_verified"] is False

        goal_body = {
            "title": "Blocked goal",
            "description": "Should be refused",
            "deadline": "2030-01-01T00:00:00Z",
            "pledge_amount": 5000,
            "goal_type": "youtube_video",
            "criteria": {"min_duration_seconds": 300, "video_description": "test"},
        }
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=goal_body,
        )
    assert resp.status_code == 403, resp.text


# ── AC1.3: full verification flow — register, verify-request, verify,
#          login, then create goal succeeds ────────────────────────────


async def test_full_verification_oracle_flow():
    """AC1.3: register → verify-request → verify → login → create goal (2xx).

    This is the primary observable for the acceptance oracle.
    """
    async with make_client() as client:
        # 1. Register
        token, user = await _register_unverified(client, email="oracle-flow@test.com")
        assert user["email_verified"] is False

        # 2. Create goal BEFORE verification → 403 (the gate)
        goal_body = {
            "title": "Oracle goal",
            "description": "Must pass after verification",
            "deadline": "2030-01-01T00:00:00Z",
            "pledge_amount": 5000,
            "goal_type": "youtube_video",
            "criteria": {"min_duration_seconds": 300, "video_description": "test"},
        }
        blocked = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=goal_body,
        )
        assert blocked.status_code == 403, blocked.text

        # 3. Request verification token
        vreq = await client.post(
            "/api/auth/email/verify-request",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert vreq.status_code == 200, vreq.text
        verify_token = vreq.json()["verification_token"]
        assert verify_token is not None

        # 4. Consume the token
        vresp = await client.post(
            "/api/auth/email/verify",
            json={"verification_token": verify_token},
        )
        assert vresp.status_code == 200, vresp.text
        assert vresp.json() == {"message": "email_verified"}

        # 5. Re-authenticate to get a fresh token reflecting email_verified
        login = await client.post(
            "/api/auth/email/login",
            json={"email": "oracle-flow@test.com", "password": "longenoughpw"},
        )
        assert login.status_code == 200, login.text
        new_token = login.json()["access_token"]
        assert login.json()["user"]["email_verified"] is True

        # 6. Create goal now succeeds
        allowed = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {new_token}"},
            json=goal_body,
        )
        assert allowed.status_code == 201, allowed.text
        assert "id" in allowed.json()


# ── AC1.4: /me reflects email_verified state ──────────────────────────


async def test_auth_me_includes_email_verified():
    """AC1.4: GET /api/auth/me includes email_verified field."""
    async with make_client() as client:
        token, user = await _register_unverified(client)
        me = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me.status_code == 200
        assert me.json()["email_verified"] is False

        # Verify the account
        vreq = await client.post(
            "/api/auth/email/verify-request",
            headers={"Authorization": f"Bearer {token}"},
        )
        verify_token = vreq.json()["verification_token"]
        await client.post(
            "/api/auth/email/verify",
            json={"verification_token": verify_token},
        )
        # Re-authenticate
        login = await client.post(
            "/api/auth/email/login",
            json={"email": user["email"], "password": "longenoughpw"},
        )
        new_token = login.json()["access_token"]
        me2 = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert me2.status_code == 200
        assert me2.json()["email_verified"] is True


# ── AC2.1: double-spend fails with invalid_token ──────────────────────


async def test_verification_token_double_spend_fails():
    """AC2.1: consuming a token twice returns 400 with invalid_token."""
    async with make_client() as client:
        token, _ = await _register_unverified(client, email="double-spend@test.com")

        vreq = await client.post(
            "/api/auth/email/verify-request",
            headers={"Authorization": f"Bearer {token}"},
        )
        verify_token = vreq.json()["verification_token"]

        # First use — succeeds
        first = await client.post(
            "/api/auth/email/verify",
            json={"verification_token": verify_token},
        )
        assert first.status_code == 200, first.text

        # Second use — must fail
        second = await client.post(
            "/api/auth/email/verify",
            json={"verification_token": verify_token},
        )
        assert second.status_code == 400, second.text
        assert second.json() == {"error": "invalid_token"}


# ── AC2.2: force-expired token fails with token_expired ───────────────


async def test_verification_token_force_expired_fails():
    """AC2.2: force-expired token returns 400 with token_expired."""
    async with make_client() as client:
        token, _ = await _register_unverified(client, email="force-expire@test.com")

        vreq = await client.post(
            "/api/auth/email/verify-request",
            headers={"Authorization": f"Bearer {token}"},
        )
        verify_token = vreq.json()["verification_token"]

        # Force-expire the token
        del_resp = await client.delete(
            "/api/auth/email/verify-token",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert del_resp.status_code == 200, del_resp.text
        assert del_resp.json() == {"message": "token_invalidated"}

        # Attempt to use the expired token
        use = await client.post(
            "/api/auth/email/verify",
            json={"verification_token": verify_token},
        )
        assert use.status_code == 400, use.text
        assert use.json() == {"error": "token_expired"}


# ── AC3.1: OAuth users are not gated (pre-verified) ───────────────────


async def test_oauth_user_is_not_gated_by_require_verified_email():
    """AC3.1: OAuth accounts have email_verified=True and pass the gate."""
    from unittest.mock import patch

    async with make_client() as client:
        with patch("app.routes.auth.verify_google_token") as mock:
            mock.return_value = {
                "email": "oauth-verified@test.com",
                "name": "OAuth User",
                "sub": "oauth-sub-verified",
                "picture": None,
            }
            oauth_resp = await client.post(
                "/api/auth/google", json={"token": "valid-google-token"}
            )
        assert oauth_resp.status_code == 200
        token = oauth_resp.json()["access_token"]
        assert oauth_resp.json()["user"]["email_verified"] is True

        # OAuth user can create a goal without verification
        goal_body = {
            "title": "OAuth goal",
            "description": "Should succeed",
            "deadline": "2030-01-01T00:00:00Z",
            "pledge_amount": 5000,
            "goal_type": "youtube_video",
            "criteria": {"min_duration_seconds": 300, "video_description": "test"},
        }
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=goal_body,
        )
        assert resp.status_code == 201, resp.text
        assert "id" in resp.json()


# ── Verify-request: 409 for already-verified account ──────────────────


async def test_verify_request_rejects_already_verified_account():
    """POST /api/auth/email/verify-request returns 409 for verified accounts."""
    async with make_client() as client:
        token, _ = await _register_unverified(client)

        # Verify the account
        vreq = await client.post(
            "/api/auth/email/verify-request",
            headers={"Authorization": f"Bearer {token}"},
        )
        await client.post(
            "/api/auth/email/verify",
            json={"verification_token": vreq.json()["verification_token"]},
        )
        # Re-authenticate
        login = await client.post(
            "/api/auth/email/login",
            json={"email": "fresh@test.com", "password": "longenoughpw"},
        )
        new_token = login.json()["access_token"]

        # Request again — should be rejected
        second = await client.post(
            "/api/auth/email/verify-request",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert second.status_code == 409, second.text
        assert second.json() == {"error": "already_verified"}


# ── Verify-token: 404 when no token exists ────────────────────────────


async def test_verify_token_invalidate_returns_404_when_no_token():
    """DELETE /api/auth/email/verify-token returns 404 when no token exists."""
    async with make_client() as client:
        token, _ = await _register_unverified(client, email="no-token@test.com")
        # No verify-request called — no token exists
        resp = await client.delete(
            "/api/auth/email/verify-token",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, resp.text


# ── Verify-token: cannot invalidate another user's token ──────────────


async def test_verify_token_invalidate_is_scoped_to_caller():
    """DELETE /api/auth/email/verify-token only affects the caller's tokens."""
    async with make_client() as client:
        # User A registers and requests a token
        token_a, _ = await _register_unverified(
            client, email="user-a@test.com", password="pwA123456!"
        )
        vreq_a = await client.post(
            "/api/auth/email/verify-request",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert vreq_a.status_code == 200

        # User B registers
        token_b, _ = await _register_unverified(
            client, email="user-b@test.com", password="pwB123456!"
        )

        # User B tries to delete User A's token — should be 404 (scoped to caller)
        resp = await client.delete(
            "/api/auth/email/verify-token",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404, resp.text

        # User A's token should still be usable
        use = await client.post(
            "/api/auth/email/verify",
            json={"verification_token": vreq_a.json()["verification_token"]},
        )
        assert use.status_code == 200, use.text


# ── Token response-body leak guarded by environment ───────────────────


async def test_verify_request_hides_token_in_production():
    """In production env, verify-request must not expose the plaintext token."""
    from app.config import settings as _root_settings

    # All modules import the same settings singleton, so patching
    # ``environment`` on the root config object affects every consumer.
    with patch.object(_root_settings, "environment", "production"):
        async with make_client() as client:
            token, _ = await _register_unverified(client)
            vreq = await client.post(
                "/api/auth/email/verify-request",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert vreq.status_code == 200, vreq.text
            body = vreq.json()
            # In production the token must not appear in the response body.
            assert "verification_token" not in body
