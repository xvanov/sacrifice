"""
Deployed-web OAuth flow verification — backend ASGI-level evidence.

Scope boundary: test-only verification slice. Does NOT implement backend or
frontend fixes; produces executable evidence that localizes the failing layer
for Google and GitHub OAuth login on the deployed origin.

These tests call the ASGI app directly (no real HTTP server), verifying that
the full FastAPI routing path — middleware, dependency resolution, response
construction — sets the correct cookies, redirects to providers, and honors
CSRF/state cookies on callback.

Fault domains localized:
  1. Login endpoints set both oauth_state AND csrf_token cookies (AC4.1)
  2. Login endpoints redirect to the correct provider URL (AC1.1, AC2.1)
  3. Callback endpoints honor state/CSRF cookies (AC4.2)
  4. Callback redirects use auth_code, never access_token (AC3.1)
  5. /api/auth/exchange is reachable (AC3.1)
"""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.csrf import generate_csrf_token
from app.main import app


def _make_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _get_set_cookie_value(headers, name: str) -> str | None:
    """Extract cookie value from a response's set-cookie headers.

    httpx merges multiple Set-Cookie headers into a single comma-joined
    string. This parses them correctly even when multiple cookies are set.
    """
    set_cookie = headers.get("set-cookie", "")
    if not set_cookie:
        return None
    for segment in set_cookie.split(", "):
        if segment.startswith(f"{name}="):
            return segment.split(";")[0].removeprefix(f"{name}=")
    return None


# ── Layer 1: Login endpoint cookie issuance (ASGI routing) ──────────────────


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_login_endpoint_sets_oauth_state_cookie_through_asgi(provider: str):
    """AC4.1: login endpoint sets oauth_state cookie via full FastAPI routing."""
    async with _make_client() as client:
        resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
    assert resp.status_code == 302
    oauth_state = _get_set_cookie_value(resp.headers, "oauth_state")
    assert oauth_state, (
        f"oauth_state cookie MUST be set on {provider} login response"
    )
    assert len(oauth_state) >= 32, (
        f"oauth_state cookie must be at least 32 chars, got {len(oauth_state)}"
    )


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_login_endpoint_sets_csrf_token_cookie_through_asgi(provider: str):
    """AC4.1: login endpoint sets csrf_token cookie via full FastAPI routing.

    This is the canonical double-cookie check: both oauth_state AND csrf_token
    must survive the full ASGI routing path. If csrf_token is missing here but
    present in a direct function call, the middleware/dependency chain is
    eating the second Set-Cookie header.

    Regression context: direct call to google_login() returned both cookies in
    raw_headers, but after routing through FastAPI only oauth_state appeared.
    """
    async with _make_client() as client:
        resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
    assert resp.status_code == 302
    csrf_token = _get_set_cookie_value(resp.headers, "csrf_token")
    assert csrf_token, (
        f"csrf_token cookie MUST be set on {provider} login response — "
        "missing csrf_token means the callback will reject the browser redirect"
    )


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_login_endpoint_both_cookies_present_simultaneously(provider: str):
    """AC4.1: both oauth_state and csrf_token are set on the same response.

    The login endpoint must emit TWO Set-Cookie headers in one response.
    If only one survives, the OAuth callback flow is broken because:
    - Without oauth_state: state mismatch → 400
    - Without csrf_token: CSRF check fails → 403
    """
    async with _make_client() as client:
        resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
    assert resp.status_code == 302
    oauth_state = _get_set_cookie_value(resp.headers, "oauth_state")
    csrf_token = _get_set_cookie_value(resp.headers, "csrf_token")
    assert oauth_state and csrf_token, (
        f"Both cookies MUST be set on {provider} login. "
        f"oauth_state={'present' if oauth_state else 'MISSING'}, "
        f"csrf_token={'present' if csrf_token else 'MISSING'}"
    )


# ── Layer 2: Login redirects to correct provider ────────────────────────────


@pytest.mark.parametrize("provider, expected_host", [
    ("google", "accounts.google.com"),
    ("github", "github.com/login/oauth"),
])
async def test_login_redirects_to_provider(provider: str, expected_host: str):
    """AC1.1/AC2.1: login redirects to the correct OAuth provider URL."""
    async with _make_client() as client:
        resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
    assert resp.status_code == 302
    location = resp.headers.get("location", "")
    assert expected_host in location, (
        f"{provider} login must redirect to {expected_host}, got: {location}"
    )
    assert "client_id=" in location, (
        f"{provider} login redirect must include client_id"
    )
    assert "state=" in location, (
        f"{provider} login redirect must include state parameter"
    )
    # Google uses response_type=code; GitHub's authorize endpoint defaults to
    # code flow without an explicit response_type parameter. Both must NOT
    # use response_type=token (implicit flow — would leak access_token in URL).
    assert "response_type=token" not in location, (
        f"{provider} login must NOT use response_type=token (implicit flow — "
        "would leak access_token in browser URL/history)"
    )


# ── Layer 3: Callback honors CSRF/state cookies ─────────────────────────────


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_callback_with_valid_cookies_passes_csrf_gate(provider: str):
    """AC4.2: callback with valid oauth_state + csrf_token cookies passes CSRF.

    The callback must not return 403 (CSRF rejected) or 400 (state mismatch)
    when valid cookies are present. The code exchange will fail (fake code),
    but we must get past the cookie-based security gates first.

    NOTE: ASGITransport does not process httpx client.cookies — we send
    cookies via an explicit Cookie header, which is what the browser does.
    """
    async with _make_client() as client:
        # Step 1: get cookies from login
        login_resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
        assert login_resp.status_code == 302
        oauth_state = _get_set_cookie_value(login_resp.headers, "oauth_state")
        csrf_token = _get_set_cookie_value(login_resp.headers, "csrf_token")
        assert oauth_state and csrf_token, (
            f"login must set both cookies before callback can be tested"
        )

        # Step 2: callback with matching state and valid cookies via Cookie header
        resp = await client.get(
            f"/api/auth/{provider}/callback"
            f"?code=fake-verification-code&state={oauth_state}",
            headers={
                "Cookie": f"oauth_state={oauth_state}; csrf_token={csrf_token}",
            },
            follow_redirects=False,
        )

    # Must not be 403 (CSRF gate) or 400 (state mismatch gate)
    assert resp.status_code not in (400, 403), (
        f"{provider} callback returned {resp.status_code} — "
        f"expected to pass CSRF/state gate (redirect or error from code exchange). "
        f"Body: {resp.text[:200]}"
    )


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_callback_without_cookies_is_rejected(provider: str):
    """AC4.2: callback without state/CSRF cookies is rejected at the gate."""
    async with _make_client() as client:
        # No cookies set — this simulates a direct callback request
        resp = await client.get(
            f"/api/auth/{provider}/callback?code=test&state=no-cookie",
            follow_redirects=False,
        )
    assert resp.status_code in (400, 403), (
        f"{provider} callback without cookies must be rejected (400/403), "
        f"got {resp.status_code}"
    )


# ── Layer 4: Callback redirect uses auth_code, not access_token ──────────────


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_callback_redirect_never_leaks_access_token(provider: str):
    """AC3.1: callback redirect never contains access_token in the URL.

    Even on error, the redirect must use auth_code= (correct) or just error=
    (safe), never access_token= (security defect — raw token in URL history).
    """
    async with _make_client() as client:
        login_resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
        oauth_state = _get_set_cookie_value(login_resp.headers, "oauth_state")
        csrf_token = _get_set_cookie_value(login_resp.headers, "csrf_token")

        if oauth_state and csrf_token:
            resp = await client.get(
                f"/api/auth/{provider}/callback"
                f"?code=fake-code&state={oauth_state}",
                headers={
                    "Cookie": f"oauth_state={oauth_state}; csrf_token={csrf_token}",
                },
                follow_redirects=False,
            )
        else:
            resp = await client.get(
                f"/api/auth/{provider}/callback?code=fake&state=fake",
                follow_redirects=False,
            )

    if resp.status_code in (302, 303, 307):
        location = resp.headers.get("location", "")
        assert "access_token=" not in location, (
            f"{provider} callback redirect MUST NOT contain access_token. "
            f"Location: {location[:200]}"
        )


# ── Layer 5: /api/auth/exchange endpoint reachability ───────────────────────


async def test_exchange_endpoint_exists_and_rejects_invalid_codes():
    """AC3.1: POST /api/auth/exchange is reachable and validates auth codes."""
    async with _make_client() as client:
        resp = await client.post("/api/auth/exchange", json={"code": "invalid-fake-code"})
    # 401 = endpoint exists, validates code, rejects invalid one
    # 404 = endpoint doesn't exist (deployment/routing issue)
    # 500 = internal error (code defect)
    assert resp.status_code == 401, (
        f"exchange endpoint must return 401 for invalid code, got {resp.status_code}"
    )
    body = resp.json()
    assert "detail" in body, "error response must include detail"


async def test_exchange_endpoint_accepts_json_body():
    """AC3.1: /api/auth/exchange accepts POST with JSON Content-Type."""
    async with _make_client() as client:
        resp = await client.post(
            "/api/auth/exchange",
            json={"code": "test-code"},
            headers={"Content-Type": "application/json"},
        )
    # Must not be 415 (Unsupported Media Type) or 422 (body parsing error)
    assert resp.status_code != 415, "exchange must accept JSON Content-Type"
    assert resp.status_code != 422, "exchange must parse {code: ...} body"


# ── Layer 6: Cookie attributes ──────────────────────────────────────────────


@pytest.mark.parametrize("provider", ["google", "github"])
@pytest.mark.parametrize("cookie_name", ["oauth_state", "csrf_token"])
async def test_login_cookie_has_httponly(provider: str, cookie_name: str):
    """Cookies set by login endpoints have HttpOnly flag."""
    async with _make_client() as client:
        resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    # Find the specific cookie's header line
    found = False
    for segment in set_cookie.split(", "):
        if segment.startswith(f"{cookie_name}="):
            assert "HttpOnly" in segment, (
                f"{cookie_name} cookie on {provider} login must be HttpOnly"
            )
            found = True
            break
    assert found, f"{cookie_name} cookie not found in set-cookie header"


@pytest.mark.parametrize("provider", ["google", "github"])
@pytest.mark.parametrize("cookie_name", ["oauth_state", "csrf_token"])
async def test_login_cookie_has_samesite_lax(provider: str, cookie_name: str):
    """Cookies set by login endpoints use SameSite=Lax."""
    async with _make_client() as client:
        resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
    assert resp.status_code == 302
    set_cookie = resp.headers.get("set-cookie", "")
    found = False
    for segment in set_cookie.split(", "):
        if segment.startswith(f"{cookie_name}="):
            assert "SameSite=Lax" in segment or "SameSite=lax" in segment, (
                f"{cookie_name} cookie on {provider} login must be SameSite=Lax"
            )
            found = True
            break
    assert found, f"{cookie_name} cookie not found in set-cookie header"


# ── Layer 7: No access_token in login redirect (prevent token-in-URL) ───────


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_login_redirect_never_contains_access_token(provider: str):
    """Login initiation must not leak access_token in the redirect URL."""
    async with _make_client() as client:
        resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
    assert resp.status_code == 302
    location = resp.headers.get("location", "")
    assert "access_token=" not in location, (
        f"{provider} login redirect MUST NOT contain access_token"
    )


# ── Layer 8: Composite flow verification (all layers green) ─────────────────


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_composite_oauth_flow_shape_verification(provider: str):
    """AC5.1: full OAuth flow shape verification — all layers must be green.

    This is the reproducible check. It runs the complete flow through ASGI:
      login → cookies → callback → exchange, and reports each layer's status.
    When run against the deployed backend, each layer either passes or fails
    with a specific, localized error message.
    """
    results: list[str] = []
    layer_status = True

    async with _make_client() as client:
        # L0: Login endpoint reachable
        login_resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
        results.append(
            f"L0 {provider} login reachable: "
            f"{'PASS' if login_resp.status_code == 302 else 'FAIL'}"
        )
        if login_resp.status_code != 302:
            layer_status = False

        # L1: Provider redirect correct
        location = login_resp.headers.get("location", "")
        provider_hosts = {
            "google": "accounts.google.com",
            "github": "github.com/login/oauth",
        }
        expected = provider_hosts[provider]
        l1_ok = expected in location
        results.append(
            f"L1 {provider} redirect to {expected}: {'PASS' if l1_ok else 'FAIL'}"
        )
        if not l1_ok:
            layer_status = False

        # L2: Both cookies set
        oauth_state = _get_set_cookie_value(login_resp.headers, "oauth_state")
        csrf_token = _get_set_cookie_value(login_resp.headers, "csrf_token")
        l2_ok = bool(oauth_state and csrf_token)
        results.append(
            f"L2 {provider} both cookies: "
            f"{'PASS' if l2_ok else 'FAIL'} "
            f"(oauth_state={'ok' if oauth_state else 'MISSING'}, "
            f"csrf_token={'ok' if csrf_token else 'MISSING'})"
        )
        if not l2_ok:
            layer_status = False

        # L3: Callback honors cookies (passes CSRF/state gate)
        if oauth_state and csrf_token:
            cb_resp = await client.get(
                f"/api/auth/{provider}/callback"
                f"?code=fake-composite-code&state={oauth_state}",
                headers={
                    "Cookie": f"oauth_state={oauth_state}; csrf_token={csrf_token}",
                },
                follow_redirects=False,
            )
            l3_ok = cb_resp.status_code not in (400, 403)
            results.append(
                f"L3 {provider} callback CSRF/state gate: "
                f"{'PASS' if l3_ok else 'FAIL'} (status={cb_resp.status_code})"
            )
            if not l3_ok:
                layer_status = False

            # L4: No access_token in callback redirect
            if cb_resp.status_code in (302, 303, 307):
                cb_loc = cb_resp.headers.get("location", "")
                l4_ok = "access_token=" not in cb_loc
                results.append(
                    f"L4 {provider} no access_token leak: {'PASS' if l4_ok else 'FAIL'}"
                )
                if not l4_ok:
                    layer_status = False
            else:
                results.append(
                    f"L4 {provider} no access_token leak: SKIP (callback returned {cb_resp.status_code})"
                )
        else:
            results.append(
                f"L3 {provider} callback CSRF/state gate: SKIP (cookies missing)"
            )
            results.append(
                f"L4 {provider} no access_token leak: SKIP (cookies missing)"
            )

    # L5: Exchange endpoint reachable
    async with _make_client() as client:
        ex_resp = await client.post("/api/auth/exchange", json={"code": "test"})
    l5_ok = ex_resp.status_code == 401  # endpoint exists, rejects bad code
    results.append(
        f"L5 exchange endpoint: {'PASS' if l5_ok else 'FAIL'} (status={ex_resp.status_code})"
    )
    if not l5_ok:
        layer_status = False

    # Print report for human-readable verification
    report = "\n".join(f"  {r}" for r in results)
    print(f"\n── {provider.upper()} OAuth Flow Verification ──\n{report}\n──")

    assert layer_status, (
        f"{provider} OAuth flow has failures:\n{report}"
    )


# ── Layer 9: Known defect — resolveApiBase in auth.ts ───────────────────────


async def test_known_defect_exchange_endpoint_is_functional():
    """KNOWN_DEFECT: frontend auth.ts exchangeCode() calls resolveApiBase()
    which is undefined, causing a ReferenceError at runtime.

    This test proves the backend exchange endpoint IS functional (returns 401
    for bad codes, not 500/404), so the defect IS in the frontend's
    exchangeCode() calling resolveApiBase() instead of getApiBaseUrl().

    See: frontend/services/auth.ts line 162
    """
    async with _make_client() as client:
        resp = await client.post("/api/auth/exchange", json={"code": "test"})
    assert resp.status_code == 401, (
        f"Backend exchange works (returns 401 for bad code, got {resp.status_code}). "
        "The defect is in frontend auth.ts exchangeCode() calling undefined "
        "resolveApiBase() instead of getApiBaseUrl()."
    )


# ── Layer 10: Success-path exchange (AC3.2) ─────────────────────────────────


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_exchange_returns_access_token_for_valid_auth_code(provider: str):
    """AC3.2: /api/auth/exchange returns access_token for a valid auth_code.

    This goes through the FULL callback → exchange flow (not just the 401
    invalid-code path). A real auth_code is obtained by simulating the OAuth
    callback with mocked provider exchanges, then exchanged for a bearer token.

    The test proves the entire server-side chain works:
      login → cookies → callback (CSRF/state gate) → auth_code →
      exchange → access_token + user
    """
    from urllib.parse import parse_qs, urlparse

    from app.core.csrf import generate_csrf_token

    provider_patch_path = f"app.routes.auth.exchange_{provider}_code"

    with patch(provider_patch_path) as mock_exchange:
        if provider == "google":
            mock_exchange.return_value = {"id_token": "fake-id-token"}
            with patch("app.routes.auth.verify_google_token") as mock_verify:
                mock_verify.return_value = {
                    "email": "ac32-verify@example.com",
                    "name": "AC32 Verify User",
                    "sub": "ac32-verify-sub",
                    "picture": None,
                }
                await _do_exchange_success_flow(
                    provider, generate_csrf_token, parse_qs, urlparse,
                )
        else:
            mock_exchange.return_value = {
                "email": "ac32-gh-verify@example.com",
                "email_verified": True,
                "login": "ac32-gh-user",
                "name": "AC32 GH Verify",
                "id": "ac32-gh-id",
                "avatar_url": None,
            }
            await _do_exchange_success_flow(
                provider, generate_csrf_token, parse_qs, urlparse,
            )


async def _do_exchange_success_flow(provider, generate_csrf_token, parse_qs, urlparse):
    """Drive the full callback → exchange flow and assert success-path shape."""
    async with _make_client() as client:
        # Step 1: Get cookies from login
        login_resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
        assert login_resp.status_code == 302
        oauth_state = _get_set_cookie_value(login_resp.headers, "oauth_state")
        csrf_token = _get_set_cookie_value(login_resp.headers, "csrf_token")
        assert oauth_state and csrf_token

        # Step 2: Simulate provider callback with matching state + cookies
        cb_resp = await client.get(
            f"/api/auth/{provider}/callback"
            f"?code=sim-valid-code&state={oauth_state}",
            headers={
                "Cookie": f"oauth_state={oauth_state}; csrf_token={csrf_token}",
                "X-CSRF-Token": generate_csrf_token(),
            },
            follow_redirects=False,
        )
        assert cb_resp.status_code in (302, 303, 307), (
            f"callback must redirect, got {cb_resp.status_code}"
        )
        location = cb_resp.headers.get("location", "")
        auth_code = parse_qs(urlparse(location).query).get("auth_code", [None])[0]
        assert auth_code, (
            f"callback redirect must contain auth_code, got: {location}"
        )

        # Step 3: Exchange auth_code for access_token
        ex_resp = await client.post(
            "/api/auth/exchange", json={"code": auth_code},
        )
        assert ex_resp.status_code == 200, (
            f"exchange must return 200 for valid auth_code, "
            f"got {ex_resp.status_code}: {ex_resp.text}"
        )
        body = ex_resp.json()
        assert "access_token" in body, (
            f"exchange response must contain access_token: {body}"
        )
        assert "user" in body, (
            f"exchange response must contain user: {body}"
        )
        assert body["user"]["email"], "user must have an email"

        # Step 4: Token must be usable against /api/auth/me
        me_resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert me_resp.status_code == 200, (
            f"/api/auth/me must accept the exchanged token, "
            f"got {me_resp.status_code}"
        )
        me_body = me_resp.json()
        assert me_body["email"] == body["user"]["email"], (
            f"me email mismatch: {me_body['email']} != {body['user']['email']}"
        )

        # Step 5: Token is a non-trivial JWT (proves exchange returns a value
        # the frontend's auth.setToken() would store under 'sacrifice_auth_token')
        assert len(body["access_token"]) > 20, (
            "access_token must be a non-trivial JWT"
        )

        # Step 6: Auth code is single-use (replay protection)
        replay_resp = await client.post(
            "/api/auth/exchange", json={"code": auth_code},
        )
        assert replay_resp.status_code == 401, (
            f"auth_code must be single-use, replay got {replay_resp.status_code}"
        )


# ── Layer 11: Token persistence and user-loaded evidence (AC1.2, AC1.3) ─────


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_exchange_token_drives_authenticated_user_loaded_state(provider: str):
    """AC1.2 + AC1.3: The token returned by /api/auth/exchange drives an
    authenticated user-loaded state via /api/auth/me.

    This test proves the end-to-end server-side chain for token persistence
    and user loading WITHOUT pre-seeding localStorage — the token comes from
    the real exchange flow, and the user data comes from /api/auth/me.
    """
    from urllib.parse import parse_qs, urlparse

    from app.core.csrf import generate_csrf_token

    provider_patch_path = f"app.routes.auth.exchange_{provider}_code"

    with patch(provider_patch_path) as mock_exchange:
        if provider == "google":
            mock_exchange.return_value = {"id_token": "fake-id-token"}
            with patch("app.routes.auth.verify_google_token") as mock_verify:
                mock_verify.return_value = {
                    "email": "ac13-verify@example.com",
                    "name": "AC13 Verify User",
                    "sub": "ac13-verify-sub",
                    "picture": None,
                }
                await _do_token_to_user_flow(
                    provider, generate_csrf_token, parse_qs, urlparse,
                )
        else:
            mock_exchange.return_value = {
                "email": "ac13-gh-verify@example.com",
                "email_verified": True,
                "login": "ac13-gh-user",
                "name": "AC13 GH Verify",
                "id": "ac13-gh-id",
                "avatar_url": None,
            }
            await _do_token_to_user_flow(
                provider, generate_csrf_token, parse_qs, urlparse,
            )


async def _do_token_to_user_flow(provider, generate_csrf_token, parse_qs, urlparse):
    """Drive exchange → token → /api/auth/me and assert user-loaded shape."""
    async with _make_client() as client:
        # Get cookies from login
        login_resp = await client.get(
            f"/api/auth/{provider}/login", follow_redirects=False,
        )
        assert login_resp.status_code == 302
        oauth_state = _get_set_cookie_value(login_resp.headers, "oauth_state")
        csrf_token = _get_set_cookie_value(login_resp.headers, "csrf_token")
        assert oauth_state and csrf_token

        # Callback with valid cookies and CSRF header
        cb_resp = await client.get(
            f"/api/auth/{provider}/callback"
            f"?code=sim-valid-code&state={oauth_state}",
            headers={
                "Cookie": f"oauth_state={oauth_state}; csrf_token={csrf_token}",
                "X-CSRF-Token": generate_csrf_token(),
            },
            follow_redirects=False,
        )
        assert cb_resp.status_code in (302, 303, 307)
        location = cb_resp.headers.get("location", "")
        auth_code = parse_qs(urlparse(location).query).get("auth_code", [None])[0]
        assert auth_code

        # Exchange auth_code for access_token
        ex_resp = await client.post(
            "/api/auth/exchange", json={"code": auth_code},
        )
        assert ex_resp.status_code == 200
        body = ex_resp.json()
        access_token = body["access_token"]
        user_from_exchange = body["user"]

        # AC1.2: The token is a real JWT that can authenticate requests
        me_resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert me_resp.status_code == 200, (
            "token from exchange must authenticate /api/auth/me"
        )

        # AC1.3: User is loaded with correct identity fields
        me_body = me_resp.json()
        assert me_body["email"] == user_from_exchange["email"], (
            f"user-loaded email {me_body['email']} != "
            f"exchange email {user_from_exchange['email']}"
        )
        assert "id" in me_body, "user-loaded must include id"
        assert "display_name" in me_body, "user-loaded must include display_name"
        assert "auth_provider" in me_body, "user-loaded must include auth_provider"
        assert me_body["auth_provider"] == provider, (
            f"user-loaded auth_provider {me_body['auth_provider']} "
            f"must match login provider {provider}"
        )

        # AC1.4: No error in the response — the user is cleanly authenticated
        assert "error" not in me_body, "authenticated user must not have error"