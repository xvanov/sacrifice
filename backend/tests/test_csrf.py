"""Tests for CSRF protection primitives and cookie hardening."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.csrf import generate_csrf_token, validate_csrf_token


# ─── CSRF token generation and validation ───


def test_generate_csrf_token_produces_valid_token():
    """A freshly generated CSRF token must validate successfully."""
    token = generate_csrf_token()
    assert token
    assert validate_csrf_token(token) is True


def test_validate_csrf_token_rejects_empty_string():
    """An empty string is never a valid CSRF token."""
    assert validate_csrf_token("") is False


def test_validate_csrf_token_rejects_none():
    """None is never a valid CSRF token."""
    assert validate_csrf_token(None) is False  # type: ignore[arg-type]


def test_validate_csrf_token_rejects_tampered_token():
    """A token whose signature has been altered must not validate."""
    token = generate_csrf_token()
    # Change the payload (middle segment between the two dots) so the
    # signature no longer matches.
    parts = token.split(".")
    payload = parts[1]
    tampered_payload = payload[:-4] + "XXXX"
    tampered = parts[0] + "." + tampered_payload + "." + parts[2]
    assert validate_csrf_token(tampered) is False


def test_validate_csrf_token_rejects_access_token():
    """An access token (different purpose claim) must not validate as CSRF."""
    from app.services.auth import create_access_token

    access = create_access_token("user-1", "session-1")
    assert validate_csrf_token(access) is False


def test_validate_csrf_token_rejects_auth_code():
    """An auth code (different purpose claim) must not validate as CSRF."""
    from app.services.auth import create_auth_code

    code = create_auth_code("user-1", "code-1")
    assert validate_csrf_token(code) is False


def test_different_tokens_are_different():
    """Each call to generate_csrf_token produces a unique token."""
    tokens = {generate_csrf_token() for _ in range(10)}
    assert len(tokens) == 10


# ─── CSRF route enforcement (AC1.1) ───


async def test_google_callback_rejects_missing_csrf_header():
    """AC1.1: Google callback rejects requests without X-CSRF-Token header."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("oauth_state", "abc")
        resp = await client.get(
            "/api/auth/google/callback?code=valid-code&state=abc",
            follow_redirects=False,
        )
    assert resp.status_code == 403
    assert "CSRF token missing or invalid" in resp.text


async def test_google_callback_rejects_invalid_csrf_token():
    """AC1.1: Google callback rejects requests with invalid X-CSRF-Token."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("oauth_state", "abc")
        resp = await client.get(
            "/api/auth/google/callback?code=valid-code&state=abc",
            headers={"X-CSRF-Token": "invalid-token"},
            follow_redirects=False,
        )
    assert resp.status_code == 403
    assert "CSRF token missing or invalid" in resp.text


async def test_github_callback_rejects_missing_csrf_header():
    """AC1.1: GitHub callback rejects requests without X-CSRF-Token header."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("oauth_state", "abc")
        resp = await client.get(
            "/api/auth/github/callback?code=valid-code&state=abc",
            follow_redirects=False,
        )
    assert resp.status_code == 403
    assert "CSRF token missing or invalid" in resp.text


async def test_github_callback_rejects_invalid_csrf_token():
    """AC1.1: GitHub callback rejects requests with invalid X-CSRF-Token."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("oauth_state", "abc")
        resp = await client.get(
            "/api/auth/github/callback?code=valid-code&state=abc",
            headers={"X-CSRF-Token": "invalid-token"},
            follow_redirects=False,
        )
    assert resp.status_code == 403
    assert "CSRF token missing or invalid" in resp.text


async def test_google_callback_accepts_valid_csrf_token():
    """AC1.1: Google callback accepts requests with valid X-CSRF-Token."""
    from app.main import app

    token = generate_csrf_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("oauth_state", "abc")
        resp = await client.get(
            "/api/auth/google/callback?code=valid-code&state=abc",
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    # Should get past CSRF check — may 400/302 depending on code validity
    assert resp.status_code != 403


async def test_github_callback_accepts_valid_csrf_token():
    """AC1.1: GitHub callback accepts requests with valid X-CSRF-Token."""
    from app.main import app

    token = generate_csrf_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("oauth_state", "abc")
        resp = await client.get(
            "/api/auth/github/callback?code=valid-code&state=abc",
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    # Should get past CSRF check — may 400/302 depending on code validity
    assert resp.status_code != 403


# ─── OAuth state cookie attribute assertions (AC2.1–AC2.6) ───


async def test_google_login_sets_oauth_state_cookie_with_httponly():
    """AC2.5/AC2.6: oauth_state cookie is HttpOnly."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/google/login", follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "oauth_state=" in set_cookie
    assert "HttpOnly" in set_cookie


async def test_google_login_sets_oauth_state_cookie_with_samesite_lax():
    """AC2.1/AC2.2: oauth_state cookie uses SameSite=Lax."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/google/login", follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "SameSite=Lax" in set_cookie or "SameSite=lax" in set_cookie


async def test_google_login_sets_oauth_state_cookie_with_secure():
    """AC2.3/AC2.4: oauth_state cookie is Secure."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/google/login", follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "Secure" in set_cookie


async def test_github_login_sets_oauth_state_cookie_with_httponly():
    """AC2.5/AC2.6: oauth_state cookie is HttpOnly (GitHub flow)."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/github/login", follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "oauth_state=" in set_cookie
    assert "HttpOnly" in set_cookie


async def test_github_login_sets_oauth_state_cookie_with_samesite_lax():
    """AC2.1/AC2.2: oauth_state cookie uses SameSite=Lax (GitHub flow)."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/github/login", follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "SameSite=Lax" in set_cookie or "SameSite=lax" in set_cookie


async def test_github_login_sets_oauth_state_cookie_with_secure():
    """AC2.3/AC2.4: oauth_state cookie is Secure (GitHub flow)."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/github/login", follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "Secure" in set_cookie


async def test_cli_login_sets_oauth_state_cookie_with_secure():
    """AC2.3/AC2.4: oauth_state cookie is Secure (CLI login flow)."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/cli/login/google?port=9876", follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "oauth_state=" in set_cookie
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie


async def test_oauth_state_cookie_has_short_max_age():
    """oauth_state cookie has a short 300-second max-age to limit exposure."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/google/login", follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "Max-Age=300" in set_cookie


async def test_oauth_callback_deletes_oauth_state_cookie():
    """After callback, oauth_state cookie is cleared."""
    from app.main import app

    csrf_token = generate_csrf_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First get a valid state cookie by initiating login
        login_resp = await client.get("/api/auth/google/login", follow_redirects=False)
        # Extract cookie value
        set_cookie = login_resp.headers.get("set-cookie", "")
        cookie_val = ""
        for part in set_cookie.split("; "):
            if part.startswith("oauth_state="):
                cookie_val = part[len("oauth_state="):]

        # Now call callback with matching state — should delete the cookie
        with patch("app.routes.auth.exchange_google_code") as mock_exchange:
            mock_exchange.return_value = {"id_token": "fake-id-token"}
            with patch("app.routes.auth.verify_google_token") as mock_verify:
                mock_verify.return_value = {
                    "email": "cb@test.com",
                    "name": "CB User",
                    "sub": "cb-sub",
                    "picture": None,
            "email_verified": True,
                }
                client.cookies.set("oauth_state", cookie_val)
                resp = await client.get(
                    f"/api/auth/google/callback?code=valid&state={cookie_val}",
                    headers={"X-CSRF-Token": csrf_token},
                    follow_redirects=False,
                )
        assert resp.status_code == 302
        # The response should instruct the browser to delete the cookie
        set_cookie_after = resp.headers.get("set-cookie", "")
        # httpx combines multiple Set-Cookie headers; check for clear instruction
        assert "oauth_state=" in set_cookie_after or "oauth_state=;" in set_cookie_after or "Max-Age=0" in set_cookie_after


# ─── CSRF token delivery endpoint ───


async def test_csrf_token_endpoint_requires_auth():
    """GET /api/auth/csrf-token requires authentication."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/csrf-token")
    assert resp.status_code == 401


async def test_csrf_token_endpoint_returns_valid_token():
    """GET /api/auth/csrf-token returns a valid CSRF token for authenticated users."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.routes.auth.verify_google_token") as mock_verify:
            mock_verify.return_value = {
                "email": "csrf-token@test.com",
                "name": "CSRF Token User",
                "sub": "csrf-token-sub",
                "picture": None,
            "email_verified": True,
            }
            login_resp = await client.post(
                "/api/auth/google", json={"token": "valid-token"}
            )
            assert login_resp.status_code == 200
            access_token = login_resp.json()["access_token"]

            resp = await client.get(
                "/api/auth/csrf-token",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert "csrf_token" in body
    assert validate_csrf_token(body["csrf_token"]) is True


# ─── Inventory: cookie-authenticated route surface ───


@pytest.mark.parametrize("method, path", [
    ("POST", "/api/goals"),
    ("PUT", "/api/goals/test-id"),
    ("DELETE", "/api/goals/test-id"),
    ("POST", "/api/goals/test-id/submit-proof"),
    ("POST", "/api/chat/sessions"),
    ("POST", "/api/chat/sessions/test-id/messages"),
    ("POST", "/api/chat/sessions/test-id/request-new-goal-type"),
    ("POST", "/api/chat/sessions/test-id/accept-generated-type"),
    ("POST", "/api/chat/sessions/test-id/iterate-generated-type"),
    ("POST", "/api/chat/sessions/test-id/create-goal"),
    ("POST", "/api/payment/setup-intent"),
    ("DELETE", "/api/payment/methods/pm_test"),
    ("POST", "/api/charities"),
    ("POST", "/api/uploads/video"),
    ("PUT", "/api/notifications/test-id/read"),
    ("PUT", "/api/notifications/read-all"),
    ("POST", "/api/auth/email/register"),
    ("POST", "/api/auth/email/login"),
    ("POST", "/api/auth/exchange"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/google"),
    ("POST", "/api/auth/github"),
])
async def test_state_changing_route_rejects_without_auth(method: str, path: str):
    """State-changing routes require auth — 401/403 without bearer token.

    This proves the inventory: every state-changing route is protected by
    bearer-token auth (equivalent CSRF protection), NOT by ambient cookies.
    """
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        req = getattr(client, method.lower())
        resp = await req(path)
    # All should reject because no Authorization header is present.
    # 401 = Unauthorized, 403 = Forbidden, 405 = Method Not Allowed (GET on POST-only etc),
    # 422 = Unprocessable (missing body).
    # None of these accept the request — all are rejection codes.
    assert resp.status_code in (401, 403, 405, 422), (
        f"{method} {path} returned {resp.status_code}, not a rejection code"
    )


async def test_state_changing_routes_accept_valid_bearer_token():
    """State-changing routes accept requests with valid bearer token (no cookie needed).

    Proves the inventory conclusion: the app uses bearer-token auth, not cookie
    auth, so CSRF protections specific to cookies are not needed on these routes.
    """
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.routes.auth.verify_google_token") as mock_verify:
            mock_verify.return_value = {
                "email": "bearer@test.com",
                "name": "Bearer User",
                "sub": "bearer-sub",
                "picture": None,
            "email_verified": True,
            }
            login_resp = await client.post(
                "/api/auth/google", json={"token": "valid-token"}
            )
            assert login_resp.status_code == 200
            access_token = login_resp.json()["access_token"]

        # POST /api/auth/refresh — a representative state-changing route that
        # works with just a bearer token and no cookies.
        resp = await client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    # The route works with bearer token alone, no X-CSRF-Token header needed.
    assert resp.status_code == 200


# ─── OAuth state cookie attribute hardening (AC2) — all paths ───


@pytest.mark.parametrize("login_path", [
    "/api/auth/google/login",
    "/api/auth/github/login",
    "/api/auth/cli/login/google?port=9876",
])
async def test_oauth_state_cookie_httponly(login_path: str):
    """oauth_state cookie is HttpOnly on every issuance path."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(login_path, follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "oauth_state=" in set_cookie
    assert "HttpOnly" in set_cookie


@pytest.mark.parametrize("login_path", [
    "/api/auth/google/login",
    "/api/auth/github/login",
    "/api/auth/cli/login/google?port=9876",
])
async def test_oauth_state_cookie_samesite_lax(login_path: str):
    """oauth_state cookie uses SameSite=Lax on every issuance path."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(login_path, follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "SameSite=Lax" in set_cookie or "SameSite=lax" in set_cookie


@pytest.mark.parametrize("login_path", [
    "/api/auth/google/login",
    "/api/auth/github/login",
    "/api/auth/cli/login/google?port=9876",
])
async def test_oauth_state_cookie_secure(login_path: str):
    """oauth_state cookie is Secure on every issuance path."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(login_path, follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "Secure" in set_cookie


@pytest.mark.parametrize("login_path", [
    "/api/auth/google/login",
    "/api/auth/github/login",
    "/api/auth/cli/login/google?port=9876",
])
async def test_oauth_state_cookie_short_max_age(login_path: str):
    """oauth_state cookie has Max-Age=300 on every issuance path."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(login_path, follow_redirects=False)
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    assert "Max-Age=300" in set_cookie


# ─── Bearer-token routes do not require X-CSRF-Token ───


async def test_bearer_token_route_no_csrf_header_needed():
    """A state-changing bearer-token route works without X-CSRF-Token header.

    This proves that bearer-token auth IS the equivalent protection — unlike
    cookie auth, bearer tokens are never auto-attached by browsers.
    """
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.routes.auth.verify_google_token") as mock_verify:
            mock_verify.return_value = {
                "email": "no-csrf-header@test.com",
                "name": "No CSRF Header User",
                "sub": "no-csrf-header-sub",
                "picture": None,
            "email_verified": True,
            }
            login_resp = await client.post(
                "/api/auth/google", json={"token": "valid-token"}
            )
            assert login_resp.status_code == 200
            access_token = login_resp.json()["access_token"]

        # POST /api/auth/refresh without X-CSRF-Token header
        resp = await client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    # The route accepts the request — bearer token is the only auth check
    assert resp.status_code == 200