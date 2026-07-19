from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None, "email_verified": True}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


@patch("app.routes.payment.stripe")
async def test_setup_intent_returns_client_secret(mock_stripe):
    mock_stripe.Customer.create.return_value = type(
        "obj", (), {"id": "cus_mock_setup"}
    )()
    mock_stripe.SetupIntent.create.return_value = type(
        "obj", (),
        {"client_secret": "seti_1_test_secret_abc123"}
    )()

    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            "/api/payment/setup-intent",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert "client_secret" in body
    assert body["client_secret"] == "seti_1_test_secret_abc123"


async def test_setup_intent_requires_auth():
    async with make_client() as client:
        response = await client.post("/api/payment/setup-intent")
    assert response.status_code == 401


@patch("app.routes.payment.stripe")
async def test_list_payment_methods_returns_cards(mock_stripe):
    mock_stripe.Customer.create.return_value = type(
        "obj", (), {"id": "cus_mock123"}
    )()
    mock_pm = type("obj", (), {
        "id": "pm_123",
        "card": type("obj", (), {"last4": "4242", "brand": "visa", "exp_month": 12, "exp_year": 2028})(),
        "billing_details": type("obj", (), {"name": "Test User"})(),
    })
    mock_stripe.PaymentMethod.list.return_value = type(
        "obj", (), {"data": [mock_pm]}
    )()

    async with make_client() as client:
        token, user = await _auth(client)
        response = await client.get(
            "/api/payment/methods",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "pm_123"
    assert body[0]["card"]["last4"] == "4242"
    assert body[0]["card"]["brand"] == "visa"


async def test_list_payment_methods_requires_auth():
    async with make_client() as client:
        response = await client.get("/api/payment/methods")
    assert response.status_code == 401


@patch("app.routes.payment.stripe")
async def test_delete_payment_method_removes_card(mock_stripe):
    mock_stripe.Customer.create.return_value = type(
        "obj", (), {"id": "cus_owner"}
    )()
    mock_stripe.SetupIntent.create.return_value = type(
        "obj", (), {"client_secret": "seti_secret"}
    )()
    mock_stripe.PaymentMethod.retrieve.return_value = type(
        "obj", (), {"id": "pm_123", "customer": "cus_owner"}
    )()
    mock_stripe.PaymentMethod.detach.return_value = type(
        "obj", (), {"id": "pm_123", "detached": True}
    )()

    async with make_client() as client:
        token, _ = await _auth(client)
        # Trigger setup-intent so the user's stripe_customer_id is persisted.
        await client.post(
            "/api/payment/setup-intent",
            headers={"Authorization": f"Bearer {token}"},
        )
        response = await client.delete(
            "/api/payment/methods/pm_123",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["detached"] is True
    mock_stripe.PaymentMethod.retrieve.assert_called_once_with("pm_123")
    mock_stripe.PaymentMethod.detach.assert_called_once_with("pm_123")


@patch("app.routes.payment.stripe")
async def test_delete_payment_method_rejects_other_users_method(mock_stripe):
    mock_stripe.Customer.create.return_value = type(
        "obj", (), {"id": "cus_owner"}
    )()
    mock_stripe.SetupIntent.create.return_value = type(
        "obj", (), {"client_secret": "seti_secret"}
    )()
    # Payment method belongs to a different customer.
    mock_stripe.PaymentMethod.retrieve.return_value = type(
        "obj", (), {"id": "pm_other", "customer": "cus_someone_else"}
    )()

    async with make_client() as client:
        token, _ = await _auth(client)
        await client.post(
            "/api/payment/setup-intent",
            headers={"Authorization": f"Bearer {token}"},
        )
        response = await client.delete(
            "/api/payment/methods/pm_other",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 404
    mock_stripe.PaymentMethod.detach.assert_not_called()


async def test_delete_payment_method_requires_auth():
    async with make_client() as client:
        response = await client.delete("/api/payment/methods/pm_123")
    assert response.status_code == 401


@patch("app.routes.payment.settings")
@patch("app.routes.payment.stripe")
async def test_charities_search_with_query_returns_results(mock_stripe, mock_settings):
    mock_settings.stripe_secret_key = "sk_test_mock"
    mock_account = type("obj", (), {
        "id": "acct_connect_123",
        "business_profile": type("obj", (), {"name": "Red Cross America"})(),
    })
    mock_stripe.Account.list.return_value = type(
        "obj", (), {"data": [mock_account]}
    )()

    async with make_client() as client:
        token, _ = await _auth(client)
        # Keep the test hermetic: real EVERY_ORG/PLEDGE keys in .env would
        # make the route merge live public-charity results into the response.
        with (
            patch(
                "app.routes.payment.pledge.search_organizations",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.routes.payment.everyorg.search_nonprofits",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            response = await client.get(
                "/api/charities/search?q=red+cross",
                headers={"Authorization": f"Bearer {token}"},
            )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "acct_connect_123"
    assert body[0]["name"] == "Red Cross America"


@patch("app.routes.payment.settings")
@patch("app.routes.payment.stripe")
async def test_charities_search_without_query_returns_all(mock_stripe, mock_settings):
    mock_settings.stripe_secret_key = "sk_test_mock"
    mock_account = type("obj", (), {
        "id": "acct_connect_123",
        "business_profile": type("obj", (), {"name": "Red Cross America"})(),
    })
    mock_stripe.Account.list.return_value = type(
        "obj", (), {"data": [mock_account]}
    )()

    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.get(
            "/api/charities/search",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "acct_connect_123"
    assert body[0]["name"] == "Red Cross America"
