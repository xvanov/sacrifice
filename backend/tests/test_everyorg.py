"""Tests for the Every.org integration: donate-link building, charity search
merging, and the payments worker's everyorg branch (charge without transfer,
donation link in the receipt)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.services import everyorg
from tests.test_api_endpoint_verification import _auth, make_client

pytestmark = pytest.mark.asyncio


def test_is_everyorg_id():
    assert everyorg.is_everyorg_id("everyorg:red-cross")
    assert not everyorg.is_everyorg_id("acct_123")
    assert not everyorg.is_everyorg_id(None)
    assert not everyorg.is_everyorg_id("")


def test_build_donate_url_includes_amount_and_tracking():
    url = everyorg.build_donate_url("everyorg:red-cross", 4550, "pay-1")
    assert url.startswith("https://www.every.org/red-cross?")
    assert "amount=45.50" in url
    assert "partner_donation_id=pay-1" in url
    assert url.endswith("#donate")


async def test_search_unconfigured_returns_empty():
    with patch.object(settings, "every_org_api_key", ""):
        assert await everyorg.search_nonprofits("red cross") == []


async def test_charity_search_merges_everyorg_results():
    async with make_client() as client:
        token, _ = await _auth(client)
        with (
            patch("app.routes.payment.stripe.Account.list") as mock_list,
            # Pledge.to is the preferred public source; force the everyorg
            # fallback (and keep the test off the network).
            patch(
                "app.services.pledge.search_organizations",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.services.everyorg.search_nonprofits",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "id": "everyorg:red-cross",
                        "name": "American Red Cross",
                        "description": "Disaster relief",
                        "location": "WASHINGTON, DC",
                        "source": "everyorg",
                    }
                ],
            ),
        ):
            mock_list.return_value = MagicMock(data=[])
            resp = await client.get(
                "/api/charities/search?q=red cross",
                headers={"Authorization": f"Bearer {token}"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert any(
        c["id"] == "everyorg:red-cross" and c["source"] == "everyorg" for c in body
    )


async def test_charity_lookup_resolves_everyorg_name():
    async with make_client() as client:
        token, _ = await _auth(client)
        with patch(
            "app.services.everyorg.get_nonprofit_name",
            new_callable=AsyncMock,
            return_value="American Red Cross",
        ):
            resp = await client.get(
                "/api/charities/lookup?id=everyorg:red-cross",
                headers={"Authorization": f"Bearer {token}"},
            )
    assert resp.status_code == 200
    assert resp.json()["name"] == "American Red Cross"
    assert resp.json()["source"] == "everyorg"


async def test_everyorg_charge_skips_transfer_and_links_donation():
    """A failed goal with an everyorg recipient charges the card, creates NO
    Stripe transfer, and the receipt notification carries the donate link."""
    from app.workers.payments import process_charge_for_goal

    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    async with make_client() as client:
        token, user = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Everyorg pledge",
                "deadline": deadline,
                "pledge_amount": 1000,
                "goal_type": "api_endpoint",
                "criteria": {"url": "https://example.com", "method": "GET", "expected_status": 200},
                "charity_id": "everyorg:red-cross",
            },
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        await db.execute(
            text("UPDATE users SET stripe_customer_id = 'cus_test' WHERE id = :uid"),
            {"uid": user["id"]},
        )
        await db.commit()

    pi = MagicMock()
    pi.id = "pi_everyorg_test"
    pi.status = "succeeded"
    with (
        patch(
            "app.workers.payments._resolve_payment_method", return_value="pm_test"
        ),
        patch("app.workers.payments.stripe.PaymentIntent.create", return_value=pi),
        patch("app.workers.payments.stripe.PaymentIntent.retrieve", return_value=pi),
        patch("app.workers.payments.stripe.Transfer.create") as mock_transfer,
    ):
        result = await process_charge_for_goal(goal_id, user["id"])

    assert result["status"] == "succeeded"
    assert result["transfer_id"] is None
    mock_transfer.assert_not_called()

    async with session_factory() as db:
        row = (
            await db.execute(
                text("SELECT status, stripe_transfer_id FROM payments WHERE goal_id = :g"),
                {"g": goal_id},
            )
        ).one()
        assert row.status == "succeeded"
        assert row.stripe_transfer_id is None

        note = (
            await db.execute(
                text(
                    "SELECT title, body FROM notifications WHERE goal_id = :g "
                    "AND type = 'donation_receipt'"
                ),
                {"g": goal_id},
            )
        ).one()
        assert "Donation Pending" in note.title
        assert "https://www.every.org/red-cross?" in note.body
        # 10% platform fee: $10 pledge → $9 donation
        assert "amount=9.00" in note.body
    await engine.dispose()


async def test_update_goal_sets_and_clears_recipient():
    """PUT /api/goals/{id} with charity_id sets an everyorg recipient; an
    explicit null clears it (recipient removal), while omitting the field
    leaves it untouched."""
    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Recipient lifecycle",
                "deadline": deadline,
                "pledge_amount": 500,
                "goal_type": "api_endpoint",
                "criteria": {"url": "https://example.com", "method": "GET", "expected_status": 200},
            },
        )
        goal_id = resp.json()["id"]
        auth_hdr = {"Authorization": f"Bearer {token}"}

        resp = await client.put(
            f"/api/goals/{goal_id}", headers=auth_hdr,
            json={"charity_id": "everyorg:red-cross"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["charity_id"] == "everyorg:red-cross"

        # Unrelated update leaves the recipient alone.
        resp = await client.put(
            f"/api/goals/{goal_id}", headers=auth_hdr, json={"title": "Renamed"}
        )
        assert resp.json()["charity_id"] == "everyorg:red-cross"

        # Explicit null clears it.
        resp = await client.put(
            f"/api/goals/{goal_id}", headers=auth_hdr, json={"charity_id": None}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["charity_id"] is None


async def test_pledge_charge_creates_automatic_donation():
    """A failed goal with a pledge: recipient charges the card and creates a
    Pledge.to donation automatically; the receipt reflects it."""
    from app.workers.payments import process_charge_for_goal

    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    async with make_client() as client:
        token, user = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Pledge.to pledge",
                "deadline": deadline,
                "pledge_amount": 1000,
                "goal_type": "api_endpoint",
                "criteria": {"url": "https://example.com", "method": "GET", "expected_status": 200},
                "charity_id": "pledge:3685b542-61d5-45da-9580-162dca725966",
            },
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        await db.execute(
            text("UPDATE users SET stripe_customer_id = 'cus_test' WHERE id = :uid"),
            {"uid": user["id"]},
        )
        await db.commit()

    pi = MagicMock()
    pi.id = "pi_pledge_test"
    pi.status = "succeeded"
    with (
        patch("app.workers.payments._resolve_payment_method", return_value="pm_test"),
        patch("app.workers.payments.stripe.PaymentIntent.create", return_value=pi),
        patch("app.workers.payments.stripe.PaymentIntent.retrieve", return_value=pi),
        patch("app.workers.payments.stripe.Transfer.create") as mock_transfer,
        patch(
            "app.workers.payments.pledge.create_donation",
            new_callable=AsyncMock,
            return_value={"id": "don_123", "status": "processing"},
        ) as mock_donate,
    ):
        result = await process_charge_for_goal(goal_id, user["id"])

    assert result["status"] == "succeeded"
    mock_transfer.assert_not_called()
    mock_donate.assert_awaited_once()
    args, kwargs = mock_donate.await_args
    assert args[0] == "pledge:3685b542-61d5-45da-9580-162dca725966"
    assert args[1] == 900  # $10 pledge minus 10% platform fee
    assert kwargs["email"] == "test@example.com"

    async with session_factory() as db:
        note = (
            await db.execute(
                text(
                    "SELECT title, body FROM notifications WHERE goal_id = :g "
                    "AND type = 'donation_receipt'"
                ),
                {"g": goal_id},
            )
        ).one()
        assert note.title == "Donation Receipt"
        assert "donated to your chosen charity automatically" in note.body
    await engine.dispose()


async def test_pledge_donation_failure_keeps_charge_and_notifies():
    """If the Pledge.to donation call errors, the charge is still recorded
    and the user is told the donation is delayed — never silently lost."""
    from app.workers.payments import process_charge_for_goal

    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    async with make_client() as client:
        token, user = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Pledge.to failing donation",
                "deadline": deadline,
                "pledge_amount": 1000,
                "goal_type": "api_endpoint",
                "criteria": {"url": "https://example.com", "method": "GET", "expected_status": 200},
                "charity_id": "pledge:3685b542-61d5-45da-9580-162dca725966",
            },
        )
        goal_id = resp.json()["id"]

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        await db.execute(
            text("UPDATE users SET stripe_customer_id = 'cus_test' WHERE id = :uid"),
            {"uid": user["id"]},
        )
        await db.commit()

    pi = MagicMock()
    pi.id = "pi_pledge_fail_test"
    pi.status = "succeeded"
    with (
        patch("app.workers.payments._resolve_payment_method", return_value="pm_test"),
        patch("app.workers.payments.stripe.PaymentIntent.create", return_value=pi),
        patch("app.workers.payments.stripe.PaymentIntent.retrieve", return_value=pi),
        patch(
            "app.workers.payments.pledge.create_donation",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Pledge.to donation failed (402): no funding"),
        ),
    ):
        result = await process_charge_for_goal(goal_id, user["id"])

    assert result["status"] == "succeeded"

    async with session_factory() as db:
        row = (
            await db.execute(
                text("SELECT status FROM payments WHERE goal_id = :g"), {"g": goal_id}
            )
        ).one()
        assert row.status == "succeeded"
        note = (
            await db.execute(
                text(
                    "SELECT title FROM notifications WHERE goal_id = :g "
                    "AND type = 'donation_receipt'"
                ),
                {"g": goal_id},
            )
        ).one()
        assert note.title == "Pledge Charged — Donation Delayed"
    await engine.dispose()
