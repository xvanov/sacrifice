import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.csrf import generate_csrf_token
from app.main import app
from app.services.password_reset import (
    MAX_RESET_ATTEMPTS,
    RESET_TOKEN_EXPIRE_MINUTES,
    _hash_token,
    generate_reset_token,
)


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def get_redirect_query_param(location: str, key: str) -> str | None:
    return parse_qs(urlparse(location).query).get(key, [None])[0]


def make_csrf_headers() -> dict[str, str]:
    """Return headers with a valid CSRF token for callback tests."""
    return {"X-CSRF-Token": generate_csrf_token()}


@asynccontextmanager
async def _db() -> AsyncSession:
    """Yield a fresh async session connected to the test database."""
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
    await engine.dispose()



# ─── Server-side OAuth login redirect tests ───


async def test_google_login_redirects_to_google():
    async with make_client() as client:
        resp = await client.get("/api/auth/google/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "client_id=" in resp.headers["location"]
    assert "response_type=code" in resp.headers["location"]
    assert "redirect_uri=" in resp.headers["location"]
    assert "state=" in resp.headers["location"]


async def test_github_login_redirects_to_github():
    async with make_client() as client:
        resp = await client.get("/api/auth/github/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://github.com/login/oauth/authorize")
    assert "client_id=" in resp.headers["location"]
    assert "redirect_uri=" in resp.headers["location"]
    assert "state=" in resp.headers["location"]


# ─── Google OAuth callback tests ───


@patch("app.routes.auth.exchange_google_code")
@patch("app.routes.auth.verify_google_token")
async def test_google_callback_with_valid_code_redirects_to_frontend_with_auth_code(
    mock_verify, mock_exchange
):
    mock_exchange.return_value = {"id_token": "fake-id-token"}
    mock_verify.return_value = {
        "email": "oauth@test.com",
        "name": "OAuth User",
        "sub": "oauth-sub-123",
        "picture": None,
    }
    async with make_client() as client:
        client.cookies.set("oauth_state", "abc")
        resp = await client.get(
            "/api/auth/google/callback?code=valid-code&state=abc",
            headers=make_csrf_headers(),
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("http://localhost:8082?auth_code=")
    assert get_redirect_query_param(resp.headers["location"], "auth_code")
    assert get_redirect_query_param(resp.headers["location"], "access_token") is None


async def test_google_callback_without_state_cookie_returns_400():
    """Browser flow: callback with state but no matching cookie must 400.

    This is the CSRF gate — a missing cookie used to silently pass.
    """
    async with make_client() as client:
        resp = await client.get(
            "/api/auth/google/callback?code=valid-code&state=abc",
            follow_redirects=False,
        )
    assert resp.status_code == 400
    assert "State mismatch" in resp.text


async def test_github_callback_without_state_cookie_returns_400():
    async with make_client() as client:
        resp = await client.get(
            "/api/auth/github/callback?code=valid-code&state=abc",
            follow_redirects=False,
        )
    assert resp.status_code == 400
    assert "State mismatch" in resp.text


async def test_google_callback_without_code_returns_400():
    async with make_client() as client:
        resp = await client.get("/api/auth/google/callback")
    assert resp.status_code == 400
    assert "Missing authorization code" in resp.text


@patch("app.routes.auth.exchange_google_code")
async def test_google_callback_with_state_mismatch_returns_400(mock_exchange):
    async with make_client() as client:
        client.cookies.set("oauth_state", "real-state")
        resp = await client.get(
            "/api/auth/google/callback?code=code&state=wrong-state"
        )
    assert resp.status_code == 400
    assert "State mismatch" in resp.text


async def test_google_callback_with_error_redirects_to_frontend():
    async with make_client() as client:
        resp = await client.get(
            "/api/auth/google/callback?error=access_denied",
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:8082?error=access_denied"


@patch("app.routes.auth.exchange_google_code")
async def test_google_callback_when_code_exchange_fails_redirects_with_error(mock_exchange):
    mock_exchange.side_effect = ValueError("Bad code")
    async with make_client() as client:
        client.cookies.set("oauth_state", "abc")
        resp = await client.get(
            "/api/auth/google/callback?code=bad-code&state=abc",
            headers=make_csrf_headers(),
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:8082?error=invalid_code"


# ─── GitHub OAuth callback tests ───


@patch("app.routes.auth.exchange_github_code")
async def test_github_callback_with_valid_code_redirects_to_frontend_with_auth_code(
    mock_exchange,
):
    mock_exchange.return_value = {
        "email": "gh@test.com",
        "login": "ghuser",
        "name": "GH User",
        "id": "67890",
        "avatar_url": None,
    }
    async with make_client() as client:
        client.cookies.set("oauth_state", "abc")
        resp = await client.get(
            "/api/auth/github/callback?code=valid-code&state=abc",
            headers=make_csrf_headers(),
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("http://localhost:8082?auth_code=")
    assert get_redirect_query_param(resp.headers["location"], "auth_code")
    assert get_redirect_query_param(resp.headers["location"], "access_token") is None


async def test_github_callback_without_code_returns_400():
    async with make_client() as client:
        resp = await client.get("/api/auth/github/callback")
    assert resp.status_code == 400
    assert "Missing authorization code" in resp.text


async def test_github_callback_with_error_redirects_to_frontend():
    async with make_client() as client:
        resp = await client.get(
            "/api/auth/github/callback?error=access_denied",
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:8082?error=access_denied"


# ─── Legacy GitHub callback redirect ───


async def test_legacy_github_callback_redirects_to_new_endpoint():
    async with make_client() as client:
        resp = await client.get(
            "/auth/github/callback?code=some-code&state=some-state",
            follow_redirects=False,
        )
    assert resp.status_code == 307
    assert "/api/auth/github/callback?code=some-code&state=some-state" in resp.headers["location"]


@patch("app.routes.auth.verify_google_token")
async def test_auth_google_valid_token_returns_200_and_tokens(mock_verify):
    mock_verify.return_value = {
        "email": "test@google.com",
        "name": "Test Google User",
        "sub": "google-sub-123",
        "picture": "https://example.com/avatar.png",
    }
    async with make_client() as client:
        response = await client.post(
            "/api/auth/google",
            json={"token": "valid-google-token"},
        )
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "user" in body
    assert body["user"]["email"] == "test@google.com"
    assert body["user"]["display_name"] == "Test Google User"
    assert body["user"]["auth_provider"] == "google"


@patch("app.routes.auth.verify_google_token")
async def test_auth_google_invalid_token_returns_401(mock_verify):
    mock_verify.side_effect = ValueError("Invalid token")
    async with make_client() as client:
        response = await client.post(
            "/api/auth/google",
            json={"token": "bad-token"},
        )
    assert response.status_code == 401


@patch("app.routes.auth.exchange_github_code")
async def test_auth_github_valid_code_returns_200_and_tokens(mock_exchange):
    mock_exchange.return_value = {
        "email": "test@github.com",
        "login": "testghuser",
        "name": "Test GitHub User",
        "id": "12345",
        "avatar_url": "https://avatars.githubusercontent.com/u/12345",
    }
    async with make_client() as client:
        response = await client.post(
            "/api/auth/github",
            json={"code": "valid-github-code"},
        )
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "user" in body
    assert body["user"]["email"] == "test@github.com"
    assert body["user"]["auth_provider"] == "github"


@patch("app.routes.auth.exchange_github_code")
async def test_auth_github_invalid_code_returns_401(mock_exchange):
    mock_exchange.side_effect = ValueError("Invalid code")
    async with make_client() as client:
        response = await client.post(
            "/api/auth/github",
            json={"code": "bad-code"},
        )
    assert response.status_code == 401


@patch("app.routes.auth.verify_google_token")
async def test_auth_me_with_valid_jwt_returns_user(mock_verify):
    mock_verify.return_value = {
        "email": "me@test.com",
        "name": "Me User",
        "sub": "me-sub",
        "picture": None,
    }
    async with make_client() as client:
        login_resp = await client.post(
            "/api/auth/google", json={"token": "valid-token"}
        )
        assert login_resp.status_code == 200
        access_token = login_resp.json()["access_token"]

        resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    assert resp.status_code == 200
    user = resp.json()
    assert user["email"] == "me@test.com"
    assert user["display_name"] == "Me User"


async def test_auth_me_without_jwt_returns_401():
    async with make_client() as client:
        response = await client.get("/api/auth/me")
    assert response.status_code == 401


@patch("app.routes.auth.verify_google_token")
async def test_auth_refresh_returns_new_jwt(mock_verify):
    mock_verify.return_value = {
        "email": "refresh@test.com",
        "name": "Refresh User",
        "sub": "refresh-sub",
        "picture": None,
    }
    async with make_client() as client:
        login_resp = await client.post(
            "/api/auth/google", json={"token": "valid-token"}
        )
        access_token = login_resp.json()["access_token"]

        resp = await client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["access_token"] != access_token


@patch("app.routes.auth.verify_google_token")
async def test_auth_refresh_revokes_previous_jwt(mock_verify):
    mock_verify.return_value = {
        "email": "refresh-revoke@test.com",
        "name": "Refresh Rotate User",
        "sub": "refresh-rotate-sub",
        "picture": None,
    }
    async with make_client() as client:
        login_resp = await client.post(
            "/api/auth/google", json={"token": "valid-token"}
        )
        old_access_token = login_resp.json()["access_token"]

        refresh_resp = await client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {old_access_token}"},
        )
        assert refresh_resp.status_code == 200
        new_access_token = refresh_resp.json()["access_token"]

        replay_resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {old_access_token}"},
        )
        assert replay_resp.status_code == 401

        current_resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {new_access_token}"},
        )
    assert current_resp.status_code == 200


@patch("app.routes.auth.verify_google_token")
async def test_auth_logout_revokes_current_jwt(mock_verify):
    mock_verify.return_value = {
        "email": "logout@test.com",
        "name": "Logout User",
        "sub": "logout-sub",
        "picture": None,
    }
    async with make_client() as client:
        login_resp = await client.post(
            "/api/auth/google", json={"token": "valid-token"}
        )
        access_token = login_resp.json()["access_token"]

        logout_resp = await client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert logout_resp.status_code == 200
        assert logout_resp.json() == {"detail": "Logged out"}

        replay_resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    assert replay_resp.status_code == 401


@patch("app.routes.auth.exchange_google_code")
@patch("app.routes.auth.verify_google_token")
async def test_auth_exchange_code_is_single_use(mock_verify, mock_exchange):
    mock_exchange.return_value = {"id_token": "fake-id-token"}
    mock_verify.return_value = {
        "email": "exchange@test.com",
        "name": "Exchange User",
        "sub": "exchange-sub",
        "picture": None,
    }
    async with make_client() as client:
        client.cookies.set("oauth_state", "abc")
        callback_resp = await client.get(
            "/api/auth/google/callback?code=valid-code&state=abc",
            headers=make_csrf_headers(),
            follow_redirects=False,
        )
        auth_code = get_redirect_query_param(callback_resp.headers["location"], "auth_code")
        assert auth_code

        exchange_resp = await client.post(
            "/api/auth/exchange",
            json={"code": auth_code},
        )
        assert exchange_resp.status_code == 200
        body = exchange_resp.json()
        assert "access_token" in body
        assert body["user"]["email"] == "exchange@test.com"

        replay_resp = await client.post(
            "/api/auth/exchange",
            json={"code": auth_code},
        )
    assert replay_resp.status_code == 401


@patch("app.routes.auth.verify_google_token")
async def test_auth_google_repeated_login_returns_same_user(mock_verify):
    mock_verify.return_value = {
        "email": "repeat@test.com",
        "name": "Repeat User",
        "sub": "repeat-sub-456",
        "picture": None,
    }
    async with make_client() as client:
        resp1 = await client.post(
            "/api/auth/google", json={"token": "valid-token"}
        )
        resp2 = await client.post(
            "/api/auth/google", json={"token": "valid-token"}
        )
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["user"]["id"] == resp2.json()["user"]["id"]
    assert resp1.json()["user"]["email"] == "repeat@test.com"


# ─── Email-claim takeover regression tests ───
#
# These guard the fix in services.auth.get_or_create_user: when an
# OAuth login arrives with an email already registered under a
# DIFFERENT provider, we MUST refuse rather than silently relink.


@patch("app.routes.auth.exchange_github_code")
@patch("app.routes.auth.verify_google_token")
async def test_github_login_with_email_owned_by_google_returns_409(
    mock_verify, mock_github_exchange
):
    # User A signs in with Google first.
    mock_verify.return_value = {
        "email": "shared@test.com",
        "name": "User A",
        "sub": "google-sub-A",
        "picture": None,
    }
    async with make_client() as client:
        first = await client.post(
            "/api/auth/google", json={"token": "valid-google-token"}
        )
        assert first.status_code == 200
        original_user_id = first.json()["user"]["id"]

        # Impostor signs in with GitHub using the same email.
        mock_github_exchange.return_value = {
            "email": "shared@test.com",
            "login": "impostor",
            "name": "Impostor",
            "id": "github-id-B",
            "avatar_url": None,
        }
        second = await client.post(
            "/api/auth/github", json={"code": "valid-github-code"}
        )
    assert second.status_code == 409
    body = second.json()
    assert body == {"error": "account_exists", "provider": "google"}

    # Original Google account must still be intact.
    async with make_client() as client:
        again = await client.post(
            "/api/auth/google", json={"token": "valid-google-token"}
        )
    assert again.status_code == 200
    assert again.json()["user"]["id"] == original_user_id
    assert again.json()["user"]["auth_provider"] == "google"


@patch("app.routes.auth.exchange_github_code")
@patch("app.routes.auth.verify_google_token")
async def test_github_login_with_verified_email_links_to_google_account(
    mock_verify, mock_github_exchange
):
    """A VERIFIED-email GitHub login signs in to the existing Google account.

    Both providers prove email ownership, so this is the same person —
    cross-provider linking, not takeover (takeover requires email_verified
    to be absent/false and is covered by the test above).
    """
    mock_verify.return_value = {
        "email": "linked@test.com",
        "name": "User A",
        "sub": "google-sub-link",
        "picture": None,
        "email_verified": True,
    }
    async with make_client() as client:
        first = await client.post(
            "/api/auth/google", json={"token": "valid-google-token"}
        )
        assert first.status_code == 200
        original_user_id = first.json()["user"]["id"]

        mock_github_exchange.return_value = {
            "email": "linked@test.com",
            "login": "sameperson",
            "name": "User A",
            "id": "github-id-link",
            "avatar_url": None,
            "email_verified": True,
        }
        second = await client.post(
            "/api/auth/github", json={"code": "valid-github-code"}
        )
    assert second.status_code == 200
    body = second.json()
    assert body["user"]["id"] == original_user_id
    # The account keeps its original provider identity.
    assert body["user"]["auth_provider"] == "google"


@patch("app.routes.auth.verify_google_token")
async def test_verified_google_login_links_to_email_password_account(mock_verify):
    """Google login with a verified email signs in to an email/password account
    with that address (the OAuth provider proved ownership); password login
    keeps working because the row keeps auth_provider='email'."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "both@test.com", "password": "Passw0rd!23"},
        )
        assert reg.status_code == 200
        original_user_id = reg.json()["user"]["id"]

        mock_verify.return_value = {
            "email": "both@test.com",
            "name": "Both Ways",
            "sub": "google-sub-both",
            "picture": None,
            "email_verified": True,
        }
        oauth = await client.post(
            "/api/auth/google", json={"token": "valid-google-token"}
        )
        assert oauth.status_code == 200
        assert oauth.json()["user"]["id"] == original_user_id

        # Password login still works afterwards.
        pw = await client.post(
            "/api/auth/email/login",
            json={"email": "both@test.com", "password": "Passw0rd!23"},
        )
    assert pw.status_code == 200
    assert pw.json()["user"]["id"] == original_user_id


@patch("app.routes.auth.exchange_github_code")
@patch("app.routes.auth.verify_google_token")
async def test_google_oauth_callback_with_email_owned_by_github_redirects_with_error(
    mock_verify, mock_github_exchange
):
    # Seed: a GitHub-backed account exists for shared2@test.com.
    mock_github_exchange.return_value = {
        "email": "shared2@test.com",
        "login": "ghuser2",
        "name": "GH User 2",
        "id": "gh-id-2",
        "avatar_url": None,
    }
    async with make_client() as client:
        seed = await client.post(
            "/api/auth/github", json={"code": "valid-github-code"}
        )
        assert seed.status_code == 200

    # Now an OAuth browser-flow Google callback arrives for the same email.
    mock_verify.return_value = {
        "email": "shared2@test.com",
        "name": "Sneaky",
        "sub": "google-sub-sneaky",
        "picture": None,
    }
    with patch("app.routes.auth.exchange_google_code") as mock_exchange:
        mock_exchange.return_value = {"id_token": "fake-id-token"}
        async with make_client() as client:
            client.cookies.set("oauth_state", "abc")
            resp = await client.get(
                "/api/auth/google/callback?code=valid-code&state=abc",
                headers=make_csrf_headers(),
                follow_redirects=False,
            )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "error=account_exists" in location
    assert "provider=github" in location


# ─── Password-reset tests (AC1.1 – AC3.1) ───

ENUMERATION_SAFE_MESSAGE = (
    "If an account with that email exists, a reset link has been sent."
)


# ─── AC1.1: Token expiry ───


async def test_reset_token_has_expiry():
    """AC1.1 — generated reset tokens have expiry metadata and fail after expiry."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "expiry@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200

    from datetime import datetime, timedelta, timezone
    from app.models.password_reset_token import PasswordResetToken
    from app.models.user import User
    from app.services.password_reset import ResetTokenError, validate_reset_token

    async with _db() as db:
        result = await db.execute(select(User).where(User.email == "expiry@test.com"))
        user = result.scalar_one()
        plaintext = await generate_reset_token(db, user)

        token_hash = _hash_token(plaintext)
        tok_result = await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        token_record = tok_result.scalar_one()

        now = datetime.now(timezone.utc)
        assert token_record.expires_at > now
        assert token_record.expires_at <= now + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES + 1)

        token_record.expires_at = now - timedelta(seconds=1)
        await db.commit()

        with pytest.raises(ResetTokenError):
            await validate_reset_token(db, plaintext)


# ─── AC1.2: Single-use ───


async def test_reset_token_is_single_use():
    """AC1.2 — a consumed token cannot be used a second time."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "singleuse@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200

    async with _db() as db:
        from app.models.user import User
        result = await db.execute(select(User).where(User.email == "singleuse@test.com"))
        user = result.scalar_one()
        plaintext = await generate_reset_token(db, user)

    async with make_client() as client:
        first = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "firstnewpw123"},
        )
        assert first.status_code == 200

        second = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "secondnewpw456"},
        )
    assert second.status_code == 400


# ─── AC1.3: Enumeration safety ───


async def test_forgot_password_nonexistent_account_returns_same_response():
    """AC1.3 — nonexistent account gets same outward response as existent."""
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/forgot-password",
            json={"email": "ghost@test.com"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"detail": ENUMERATION_SAFE_MESSAGE}


async def test_forgot_password_existent_account_returns_same_response():
    """AC1.3 — existent account gets the same outward response."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "real@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200

        resp = await client.post(
            "/api/auth/forgot-password",
            json={"email": "real@test.com"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"detail": ENUMERATION_SAFE_MESSAGE}


async def test_forgot_password_oauth_account_same_response():
    """AC1.3 — OAuth-only account gets same outward response (no token issued)."""
    with patch("app.routes.auth.verify_google_token") as mock_verify:
        mock_verify.return_value = {
            "email": "oauthonly@test.com",
            "name": "OAuth Only",
            "sub": "oauth-sub-only",
            "picture": None,
        }
        async with make_client() as client:
            google_resp = await client.post(
                "/api/auth/google", json={"token": "valid"}
            )
            assert google_resp.status_code == 200

            resp = await client.post(
                "/api/auth/forgot-password",
                json={"email": "oauthonly@test.com"},
            )
    assert resp.status_code == 200
    assert resp.json() == {"detail": ENUMERATION_SAFE_MESSAGE}


# ─── AC2.1: Token validity enforcement ───


async def test_reset_with_invalid_token_returns_400():
    """AC2.1 — totally bogus token is rejected."""
    async with make_client() as client:
        resp = await client.post(
            "/api/auth/reset-password",
            json={"token": "not-a-real-token-at-all", "new_password": "newpassword123"},
        )
    assert resp.status_code == 400


async def test_reset_with_expired_token_returns_400():
    """AC2.1 — expired token is rejected."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "expired@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200

    async with _db() as db:
        from app.models.user import User
        result = await db.execute(select(User).where(User.email == "expired@test.com"))
        user = result.scalar_one()
        plaintext = await generate_reset_token(db, user)

        # Force-expire the token
        from datetime import datetime, timedelta, timezone
        from app.models.password_reset_token import PasswordResetToken
        token_hash = _hash_token(plaintext)
        tok_result = await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        token_record = tok_result.scalar_one()
        token_record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()

    async with make_client() as client:
        resp = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "newpassword123"},
        )
    assert resp.status_code == 400


async def test_reset_with_reused_token_returns_400():
    """AC2.1 — reused (already consumed) token is rejected."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "reused@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200

    async with _db() as db:
        from app.models.user import User
        result = await db.execute(select(User).where(User.email == "reused@test.com"))
        user = result.scalar_one()
        plaintext = await generate_reset_token(db, user)

    async with make_client() as client:
        first = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "firstpw12345"},
        )
        assert first.status_code == 200

        second = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "secondpw12345"},
        )
    assert second.status_code == 400


# ─── AC2.2: Password complexity ───


async def test_reset_password_rejects_short_password():
    """AC2.2 — new password below min length (8) is rejected by schema validation."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "complex@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200

    async with _db() as db:
        from app.models.user import User

        result = await db.execute(select(User).where(User.email == "complex@test.com"))
        user = result.scalar_one()
        plaintext = await generate_reset_token(db, user)

    async with make_client() as client:
        resp = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "short"},
        )
    assert resp.status_code == 422


async def test_reset_password_rejects_password_without_letters():
    """AC2.2 — reset uses same password policy as email registration."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "complexpolicy@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200

    async with _db() as db:
        from app.models.user import User

        result = await db.execute(select(User).where(User.email == "complexpolicy@test.com"))
        user = result.scalar_one()
        plaintext = await generate_reset_token(db, user)

    async with make_client() as client:
        resp = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "12345678"},
        )
    assert resp.status_code == 400
    assert resp.json() == {"detail": "Password must contain at least one letter"}


# ─── AC2.3: Throttling ───


async def test_reset_token_throttling_after_max_attempts():
    """AC2.3 — lockout occurs after MAX_RESET_ATTEMPTS endpoint failures."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "throttle@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200

    async with _db() as db:
        from app.models.user import User

        result = await db.execute(select(User).where(User.email == "throttle@test.com"))
        user = result.scalar_one()
        plaintext = await generate_reset_token(db, user)

    async with make_client() as client:
        consume = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "newpassword123"},
        )
        assert consume.status_code == 200

        for _ in range(MAX_RESET_ATTEMPTS):
            failed = await client.post(
                "/api/auth/reset-password",
                json={"token": plaintext, "new_password": "anothernewpw123"},
            )
            assert failed.status_code == 400

        locked = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "thirdnewpassword"},
        )

    assert locked.status_code == 400
    assert locked.json() == {"detail": "Invalid or expired reset token"}

    from app.models.password_reset_token import PasswordResetToken as PRT

    token_hash = _hash_token(plaintext)
    async with _db() as db:
        tok_result = await db.execute(select(PRT).where(PRT.token_hash == token_hash))
        token_record = tok_result.scalar_one()
        assert token_record.attempts == MAX_RESET_ATTEMPTS


async def test_reset_token_attempt_counter_increments():
    """AC2.3 — failed reset attempts increment the counter (observable path)."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "incrcounter@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200

    async with _db() as db:
        from app.models.user import User
        result = await db.execute(select(User).where(User.email == "incrcounter@test.com"))
        user = result.scalar_one()
        plaintext = await generate_reset_token(db, user)

    # Submit an invalid token with a hash that doesn't match —
    # the attempt counter only increments when validate_reset_token finds
    # the record but the token is wrong. So we use a fake token that doesn't
    # hash to anything, which means no counter increment.
    #
    # To actually observe the counter increment, we need to call the endpoint
    # with a token that maps to a real record but fails some other check.
    # The simplest: set the token to consumed, then try — consumed raises
    # ResetTokenError, and the except block increments.

    from app.models.password_reset_token import PasswordResetToken as PRT
    token_hash = _hash_token(plaintext)
    async with _db() as db:
        tok_result = await db.execute(select(PRT).where(PRT.token_hash == token_hash))
        token_record = tok_result.scalar_one()
        token_record.consumed = True  # make it fail validation
        await db.commit()

    async with make_client() as client:
        resp = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "newpassword123"},
        )
    assert resp.status_code == 400

    # Verify attempt counter was incremented
    async with _db() as db:
        tok_result = await db.execute(select(PRT).where(PRT.token_hash == token_hash))
        updated = tok_result.scalar_one()
        assert updated.attempts == 1


# ─── AC3.1: Session revocation ───


async def test_password_reset_revokes_pre_reset_token():
    """AC3.1 — the pre-reset bearer token is rejected after password reset."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "revoke@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200
        pre_reset_token = reg.json()["access_token"]

        # Log in again to get a second bearer token
        login = await client.post(
            "/api/auth/email/login",
            json={"email": "revoke@test.com", "password": "longenoughpw"},
        )
        assert login.status_code == 200
        second_token = login.json()["access_token"]

    # Get a reset token and use it
    async with _db() as db:
        from app.models.user import User
        result = await db.execute(select(User).where(User.email == "revoke@test.com"))
        user = result.scalar_one()
        plaintext = await generate_reset_token(db, user)

    async with make_client() as client:
        reset = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "newpassword123"},
        )
        assert reset.status_code == 200

        # Pre-reset token should be rejected
        me_resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {pre_reset_token}"},
        )
        assert me_resp.status_code == 401

        # Second pre-reset token should also be rejected
        me2_resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {second_token}"},
        )
    assert me2_resp.status_code == 401


async def test_password_reset_allows_new_login():
    """AC3.1 supplementary — new password works after reset, new token works."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "newlogin@test.com", "password": "oldpassword123"},
        )
        assert reg.status_code == 200
        old_token = reg.json()["access_token"]

    async with _db() as db:
        from app.models.user import User
        result = await db.execute(select(User).where(User.email == "newlogin@test.com"))
        user = result.scalar_one()
        plaintext = await generate_reset_token(db, user)

    async with make_client() as client:
        reset = await client.post(
            "/api/auth/reset-password",
            json={"token": plaintext, "new_password": "brandnewpassword"},
        )
        assert reset.status_code == 200

        # Old token is dead
        old_me = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert old_me.status_code == 401

        # New login with new password works
        new_login = await client.post(
            "/api/auth/email/login",
            json={"email": "newlogin@test.com", "password": "brandnewpassword"},
        )
        assert new_login.status_code == 200
        new_token = new_login.json()["access_token"]

        me_resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {new_token}"},
        )
    assert me_resp.status_code == 200


# ─── Token lifecycle via service layer ───


async def test_reset_token_lifecycle_created_expires_consumed():
    """Verify the service-level token lifecycle: create, validate, consume, reject."""
    async with make_client() as client:
        reg = await client.post(
            "/api/auth/email/register",
            json={"email": "lifecycle@test.com", "password": "longenoughpw"},
        )
        assert reg.status_code == 200

    async with _db() as db:
        from app.models.user import User
        result = await db.execute(select(User).where(User.email == "lifecycle@test.com"))
        user = result.scalar_one()

        from app.services.password_reset import (
            consume_reset_token,
            validate_reset_token,
        )

        plaintext = await generate_reset_token(db, user)

        # Token validates
        record = await validate_reset_token(db, plaintext)
        assert record is not None
        assert record.user_id == user.id

        # Consume it
        await consume_reset_token(db, record)

        # Re-validation fails
        from app.services.password_reset import ResetTokenError

        with pytest.raises(ResetTokenError):
            await validate_reset_token(db, plaintext)
