"""Tests for CSRF protection primitives and cookie hardening."""

from unittest.mock import patch

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