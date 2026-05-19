from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


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
