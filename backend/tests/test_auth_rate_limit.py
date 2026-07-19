"""Tests for rate limiting on public-facing auth/OAuth routes.

Covers:
- AC3.1: Public-facing API routes SHALL be protected by rate limiting or
  equivalent abuse controls

The conftest autouse fixture clears the rate-limit store before every test
so each test starts with a clean state.
"""

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ─── AC3.1: Rate limiting on email auth routes ────────────────────────


@pytest.mark.asyncio
async def test_email_register_rate_limit_rejects_after_limit():
    """AC3.1: Client IP exceeding rate limit on /email/register gets 429.

    Uses max_requests=10, window=60s.  Sending 11 requests from the same
    client should result in the 11th being 429.
    """

    async with make_client() as client:
        # Send requests up to the limit (10)
        for i in range(10):
            resp = await client.post(
                "/api/auth/email/register",
                json={
                    "email": f"test_{i}@example.com",
                    "password": "secret123!",
                    "display_name": f"Test{i}",
                },
            )
            # Accepts any non-429 (may be 200, 409 conflict, etc.)
            assert resp.status_code != 429, f"request {i} was rate-limited too early"

        # 11th request should be rate-limited
        resp = await client.post(
            "/api/auth/email/register",
            json={
                "email": "test_over_limit@example.com",
                "password": "secret123!",
                "display_name": "TestOver",
            },
        )
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers


@pytest.mark.asyncio
async def test_email_login_rate_limit_rejects_after_limit():
    """AC3.1: Client IP exceeding rate limit on /email/login gets 429."""

    async with make_client() as client:
        for i in range(10):
            resp = await client.post(
                "/api/auth/email/login",
                json={"email": f"test_{i}@example.com", "password": "secret123!"},
            )
            assert resp.status_code != 429, f"request {i} was rate-limited too early"

        resp = await client.post(
            "/api/auth/email/login",
            json={"email": "test_over@example.com", "password": "secret123!"},
        )
        assert resp.status_code == 429


# ─── AC3.1: Rate limiting on OAuth entry/exchange routes ──────────────


@pytest.mark.asyncio
async def test_google_post_rate_limit_rejects_after_limit():
    """AC3.1: Client IP exceeding rate limit on POST /google gets 429."""

    async with make_client() as client:
        for _ in range(10):
            resp = await client.post(
                "/api/auth/google",
                json={"token": "fake-google-token"},
            )
            # 401 because token is invalid, not 429
            assert resp.status_code != 429, "rate-limited too early"

        resp = await client.post(
            "/api/auth/google",
            json={"token": "fake-google-token"},
        )
        assert resp.status_code == 429


@pytest.mark.asyncio
async def test_github_post_rate_limit_rejects_after_limit():
    """AC3.1: Client IP exceeding rate limit on POST /github gets 429."""

    async with make_client() as client:
        for _ in range(10):
            resp = await client.post(
                "/api/auth/github",
                json={"code": "fake-github-code"},
            )
            assert resp.status_code != 429, "rate-limited too early"

        resp = await client.post(
            "/api/auth/github",
            json={"code": "fake-github-code"},
        )
        assert resp.status_code == 429


@pytest.mark.asyncio
async def test_exchange_post_rate_limit_rejects_after_limit():
    """AC3.1: Client IP exceeding rate limit on POST /exchange gets 429."""

    async with make_client() as client:
        for _ in range(10):
            resp = await client.post(
                "/api/auth/exchange",
                json={"code": "fake-auth-code"},
            )
            # 401 because code is invalid, not 429
            assert resp.status_code != 429, "rate-limited too early"

        resp = await client.post(
            "/api/auth/exchange",
            json={"code": "fake-auth-code"},
        )
        assert resp.status_code == 429


@pytest.mark.asyncio
async def test_oauth_get_google_login_rate_limit_rejects_after_limit():
    """AC3.1: Client IP exceeding rate limit on GET /google/login gets 429."""

    async with make_client() as client:
        for _ in range(10):
            resp = await client.get("/api/auth/google/login")
            # 307 redirect because oauth flow, not 429
            assert resp.status_code != 429, "rate-limited too early"

        resp = await client.get("/api/auth/google/login")
        assert resp.status_code == 429


@pytest.mark.asyncio
async def test_oauth_get_github_login_rate_limit_rejects_after_limit():
    """AC3.1: Client IP exceeding rate limit on GET /github/login gets 429."""

    async with make_client() as client:
        for _ in range(10):
            resp = await client.get("/api/auth/github/login")
            assert resp.status_code != 429, "rate-limited too early"

        resp = await client.get("/api/auth/github/login")
        assert resp.status_code == 429


@pytest.mark.asyncio
async def test_cli_login_rate_limit_rejects_after_limit():
    """AC3.1: Client IP exceeding rate limit on GET /cli/login/{provider} gets 429."""

    async with make_client() as client:
        for _ in range(10):
            resp = await client.get("/api/auth/cli/login/google", params={"port": 9876})
            assert resp.status_code != 429, "rate-limited too early"

        resp = await client.get("/api/auth/cli/login/google", params={"port": 9876})
        assert resp.status_code == 429


# ─── Auth routes that should NOT be rate-limited ──────────────────────


@pytest.mark.asyncio
async def test_auth_me_is_not_rate_limited():
    """Authenticated /me route should not be affected by public-route rate limiter."""
    from unittest.mock import patch

    async with make_client() as client:
        # Set up an authenticated user
        with patch("app.routes.auth.verify_google_token") as mock:
            mock.return_value = {
                "email": "test@example.com",
                "name": "Test User",
                "sub": "test-sub-me",
                "picture": None,
            }
            resp = await client.post("/api/auth/google", json={"token": "valid"})
            token = resp.json()["access_token"]

        # Make many authenticated requests — they should not be rate-limited
        for _ in range(15):
            resp = await client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
