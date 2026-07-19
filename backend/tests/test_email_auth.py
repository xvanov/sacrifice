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
