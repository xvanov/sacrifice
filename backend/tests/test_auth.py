from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


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
async def test_google_callback_with_valid_code_redirects_to_frontend(
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
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("http://localhost:8082?access_token=")
    assert "access_token=" in resp.headers["location"]


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
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:8082?error=invalid_code"


# ─── GitHub OAuth callback tests ───


@patch("app.routes.auth.exchange_github_code")
async def test_github_callback_with_valid_code_redirects_to_frontend(
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
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("http://localhost:8082?access_token=")
    assert "access_token=" in resp.headers["location"]


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
async def test_auth_refresh_rotates_session_and_invalidates_old_access_token(mock_verify):
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
        assert login_resp.status_code == 200
        login_body = login_resp.json()
        access_token = login_body["access_token"]
        refresh_token = login_body["refresh_token"]

        resp = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] != access_token
        assert body["refresh_token"] != refresh_token

        old_me = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        new_me = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )

    assert old_me.status_code == 401
    assert new_me.status_code == 200


@patch("app.routes.auth.verify_google_token")
async def test_auth_refresh_replay_revokes_rotated_session_chain(mock_verify):
    mock_verify.return_value = {
        "email": "refresh-replay@test.com",
        "name": "Refresh Replay User",
        "sub": "refresh-replay-sub",
        "picture": None,
    }
    async with make_client() as client:
        login_resp = await client.post(
            "/api/auth/google", json={"token": "valid-token"}
        )
        assert login_resp.status_code == 200
        login_body = login_resp.json()

        rotate_resp = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": login_body["refresh_token"]},
        )
        assert rotate_resp.status_code == 200
        rotated = rotate_resp.json()

        replay_resp = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": login_body["refresh_token"]},
        )
        rotated_me = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {rotated['access_token']}"},
        )
        rotated_refresh_resp = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": rotated["refresh_token"]},
        )

    assert replay_resp.status_code == 401
    assert replay_resp.json() == {"detail": "Refresh token replay detected"}
    assert rotated_me.status_code == 401
    assert rotated_refresh_resp.status_code == 401
    assert rotated_refresh_resp.json() == {"detail": "Refresh token replay detected"}


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
                follow_redirects=False,
            )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "error=account_exists" in location
    assert "provider=github" in location
