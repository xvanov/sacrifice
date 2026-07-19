import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models.goal import Goal
from app.models.payment import Payment
from app.models.notification import Notification
from app.config import settings
from app.database import get_db


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


async def _set_goal_status(goal_id: str, status_value: str):
    """Set a goal's status directly (bypassing the user-facing PUT guard).

    Users can no longer self-transition an active goal to resolution states;
    tests that need a goal in verified/pending_review must set it as the
    verification pipeline would — directly in the DB.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        await db.execute(
            text("UPDATE goals SET status = :s WHERE id = :g"),
            {"s": status_value, "g": goal_id},
        )
        await db.commit()
    await engine.dispose()


async def _create_active_goal(client, token, deadline_delta_days=1, with_customer=True):
    deadline = (datetime.now(timezone.utc) - timedelta(days=deadline_delta_days)).isoformat()
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Test Goal for Payment",
            "description": "A test goal past deadline",
            "deadline": deadline,
            "pledge_amount": 5000,
            "goal_type": "youtube_video",
            "criteria": {"min_duration_seconds": 300, "video_description": "Test"},
            "charity_id": "acct_charity_connect_123",
        },
    )
    goal_id = resp.json()["id"]

    await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )

    # The corrected charge worker refuses to bill a customer with no saved
    # payment method. Default tests to a user who added a card so the charge
    # path is exercised; pass with_customer=False to test the no-card path.
    if with_customer:
        engine = create_async_engine(settings.database_url, echo=False)
        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with session_factory() as db:
            await db.execute(
                text(
                    "UPDATE users SET stripe_customer_id = 'cus_test_123' "
                    "WHERE id = (SELECT user_id FROM goals WHERE id = :g)"
                ),
                {"g": goal_id},
            )
            await db.commit()
        await engine.dispose()
    return goal_id


async def _query_goal_status(goal_id: str):
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        result = await db.execute(select(Goal).where(Goal.id == goal_id))
        goal = result.scalar_one()
        status = goal.status
    await engine.dispose()
    return status


async def _query_payments(goal_id: str):
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        result = await db.execute(select(Payment).where(Payment.goal_id == goal_id))
        payments = list(result.scalars().all())
    await engine.dispose()
    return payments


async def _query_notifications(user_id: str):
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        result = await db.execute(select(Notification).where(Notification.user_id == user_id))
        notifications = list(result.scalars().all())
    await engine.dispose()
    return notifications


# --- Acceptance Criterion 1: Expired goal auto-transitions to failed ---

async def test_expired_active_goal_transitions_to_failed():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token)

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.return_value = None
            await check_deadlines()

        status = await _query_goal_status(goal_id)
        assert status == "failed"


# --- Acceptance Criterion 2: Stripe PaymentIntent created for correct amount ---

async def test_charge_creates_payment_intent_for_pledge_amount():
    from app.workers.payments import process_charge_for_goal

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token)

        with patch("app.workers.payments.stripe") as mock_stripe:
            mock_stripe.PaymentIntent.create.return_value = MagicMock(
                id="pi_test_123",
                amount=5000,
                currency="usd",
                status="succeeded",
            )
            mock_stripe.PaymentIntent.retrieve.return_value = MagicMock(
                id="pi_test_123",
                amount=5000,
                currency="usd",
                status="succeeded",
            )
            mock_stripe.Transfer.create.return_value = MagicMock(
                id="tr_test_123", amount=4500
            )

            await process_charge_for_goal(goal_id, user["id"])

        mock_stripe.PaymentIntent.create.assert_called_once()
        _, kwargs = mock_stripe.PaymentIntent.create.call_args
        assert kwargs["amount"] == 5000
        assert kwargs["currency"] == "usd"

        payments = await _query_payments(goal_id)
        assert len(payments) == 1
        assert payments[0].stripe_payment_intent_id == "pi_test_123"
        assert payments[0].amount == 5000


# --- Acceptance Criterion 3: Successful charge triggers Stripe Transfer ---

async def test_successful_charge_triggers_stripe_transfer():
    from app.workers.payments import process_charge_for_goal

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token)
        charity_id = "acct_charity_connect_123"

        with patch("app.workers.payments.stripe") as mock_stripe:
            mock_stripe.PaymentIntent.create.return_value = MagicMock(
                id="pi_test_transfer", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.PaymentIntent.retrieve.return_value = MagicMock(
                id="pi_test_transfer", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.Transfer.create.return_value = MagicMock(
                id="tr_test_123", amount=4500
            )

            await process_charge_for_goal(goal_id, user["id"])

        mock_stripe.Transfer.create.assert_called_once()
        _, kwargs = mock_stripe.Transfer.create.call_args
        assert kwargs["amount"] == 4500
        assert kwargs["destination"] == charity_id

        payments = await _query_payments(goal_id)
        assert len(payments) == 1
        assert payments[0].stripe_transfer_id == "tr_test_123"
        assert payments[0].status == "succeeded"


# --- Acceptance Criterion 4: Failed charge retried 3 times with exponential backoff ---

@patch("asyncio.sleep", return_value=None)
async def test_failed_charge_is_retried_three_times(mock_sleep):
    from app.workers.payments import process_charge_for_goal

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token)

        attempt = 0

        def failing_create(**kwargs):
            nonlocal attempt
            attempt += 1
            raise Exception("card_declined: Your card was declined.")

        with patch("app.workers.payments.stripe") as mock_stripe:
            mock_stripe.PaymentIntent.create.side_effect = failing_create

            with pytest.raises(Exception) as excinfo:
                await process_charge_for_goal(goal_id, user["id"])

        assert attempt == 3, f"Expected 3 retries, got {attempt}"


# --- Acceptance Criterion 5: After 3 failed retries, goal status = payment_failed ---

@patch("asyncio.sleep", return_value=None)
async def test_after_three_failed_retries_goal_status_is_payment_failed(mock_sleep):
    from app.workers.payments import process_charge_for_goal

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token)

        with patch("app.workers.payments.stripe") as mock_stripe:
            mock_stripe.PaymentIntent.create.side_effect = \
                Exception("card_declined: Declined")

            with pytest.raises(Exception):
                await process_charge_for_goal(goal_id, user["id"])

        status = await _query_goal_status(goal_id)
        assert status == "payment_failed"
        payments = await _query_payments(goal_id)
        assert len(payments) == 1
        assert payments[0].status == "failed"


# --- Acceptance Criterion 6: Donation receipt created ---

async def test_successful_charge_creates_donation_receipt():
    from app.workers.payments import process_charge_for_goal

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token)

        with patch("app.workers.payments.stripe") as mock_stripe:
            mock_stripe.PaymentIntent.create.return_value = MagicMock(
                id="pi_test_receipt", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.PaymentIntent.retrieve.return_value = MagicMock(
                id="pi_test_receipt", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.Transfer.create.return_value = MagicMock(id="tr_456", amount=4500)

            await process_charge_for_goal(goal_id, user["id"])

        notifications = await _query_notifications(user["id"])
        donation_notifications = [
            n for n in notifications if n.type == "donation_receipt"
        ]
        assert len(donation_notifications) >= 1
        assert "$50" in donation_notifications[0].body or "5000" in donation_notifications[0].body


async def test_payment_history_shows_donation_receipt():
    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token)

        with patch("app.workers.payments.stripe") as mock_stripe:
            mock_stripe.PaymentIntent.create.return_value = MagicMock(
                id="pi_test_history", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.PaymentIntent.retrieve.return_value = MagicMock(
                id="pi_test_history", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.Transfer.create.return_value = MagicMock(id="tr_789", amount=4500)

            from app.workers.payments import process_charge_for_goal
            await process_charge_for_goal(goal_id, user["id"])

        resp = await client.get(
            "/api/payments",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        payment = data[0]
        assert payment["goal_id"] == goal_id


# --- Acceptance Criterion 7: Verified goal is never charged ---

async def test_verified_goal_is_never_charged():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Verified Goal",
                "description": "Already verified",
                "deadline": deadline,
                "pledge_amount": 5000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 300, "video_description": "Test"},
                "charity_id": "acct_charity_connect_123",
            },
        )
        goal_id = resp.json()["id"]
        await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        # The verification pipeline marks the goal verified (users can't).
        await _set_goal_status(goal_id, "verified")

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            await check_deadlines()
            mock_charge.assert_not_called()


# --- Idempotency: second invocation of charge worker is a no-op ---

async def test_process_charge_is_idempotent_on_re_fire():
    from app.workers.payments import process_charge_for_goal

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token)

        with patch("app.workers.payments.stripe") as mock_stripe:
            mock_stripe.PaymentIntent.create.return_value = MagicMock(
                id="pi_idem_1", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.PaymentIntent.retrieve.return_value = MagicMock(
                id="pi_idem_1", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.Transfer.create.return_value = MagicMock(
                id="tr_idem_1", amount=4500
            )

            await process_charge_for_goal(goal_id, user["id"])
            # Second invocation should be skipped — no new PaymentIntent.
            result = await process_charge_for_goal(goal_id, user["id"])

        assert mock_stripe.PaymentIntent.create.call_count == 1
        assert result == {"status": "skipped", "reason": "already_processed"}

        payments = await _query_payments(goal_id)
        assert len(payments) == 1


async def test_process_charge_passes_idempotency_key_to_stripe():
    from app.workers.payments import process_charge_for_goal

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token)

        with patch("app.workers.payments.stripe") as mock_stripe:
            mock_stripe.PaymentIntent.create.return_value = MagicMock(
                id="pi_idem_key", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.PaymentIntent.retrieve.return_value = MagicMock(
                id="pi_idem_key", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.Transfer.create.return_value = MagicMock(
                id="tr_idem_key", amount=4500
            )

            await process_charge_for_goal(goal_id, user["id"])

        _, kwargs = mock_stripe.PaymentIntent.create.call_args
        assert kwargs.get("idempotency_key") == f"goal-charge-{goal_id}"


# --- Edge Case: Goal in failed status not charged again ---

async def test_already_failed_goal_not_charged_again():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token)

        already_called = False

        async def charge_once(goal_id_str, user_id_str):
            nonlocal already_called
            if already_called:
                raise AssertionError("Charge called twice on same goal!")
            already_called = True

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            mock_charge.side_effect = charge_once
            await check_deadlines()

        status = await _query_goal_status(goal_id)
        assert status == "failed"


# --- Edge Case: Goal past deadline with pending_review gets grace period ---

async def test_goal_past_deadline_with_pending_review_gets_grace_period():
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)
        deadline = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Grace Period Goal",
                "description": "In grace period",
                "deadline": deadline,
                "pledge_amount": 5000,
                "goal_type": "youtube_video",
                "criteria": {"min_duration_seconds": 300, "video_description": "Test"},
                "charity_id": "acct_charity_connect_123",
            },
        )
        goal_id = resp.json()["id"]
        await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        # Proof submission moves the goal to pending_review (system-driven).
        await _set_goal_status(goal_id, "pending_review")

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            await check_deadlines()
            mock_charge.assert_not_called()


# --- No saved card: charge cannot proceed, recorded as failed ---

async def test_charge_without_payment_method_records_failure():
    """A user with no saved card cannot be charged off-session.

    Regression for the original bug where an unconfirmed PaymentIntent was
    created and its never-"succeeded" status silently dropped the charge. Now
    the worker records payment_failed + a notification and never calls Stripe.
    """
    from app.workers.payments import process_charge_for_goal

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token, with_customer=False)

        with patch("app.workers.payments.stripe") as mock_stripe:
            result = await process_charge_for_goal(goal_id, user["id"])

        assert result["reason"] == "no_payment_method"
        mock_stripe.PaymentIntent.create.assert_not_called()
        status = await _query_goal_status(goal_id)
        assert status == "payment_failed"
        payments = await _query_payments(goal_id)
        assert len(payments) == 1 and payments[0].status == "failed"


async def test_successful_charge_confirms_off_session():
    """The charge must actually capture: confirm + off_session on a saved card."""
    from app.workers.payments import process_charge_for_goal

    async with make_client() as client:
        token, user = await _auth(client)
        goal_id = await _create_active_goal(client, token)

        with patch("app.workers.payments.stripe") as mock_stripe:
            mock_stripe.PaymentIntent.create.return_value = MagicMock(
                id="pi_offsession", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.PaymentIntent.retrieve.return_value = MagicMock(
                id="pi_offsession", amount=5000, currency="usd", status="succeeded",
            )
            mock_stripe.Transfer.create.return_value = MagicMock(id="tr_off", amount=4500)

            await process_charge_for_goal(goal_id, user["id"])

        _, kwargs = mock_stripe.PaymentIntent.create.call_args
        assert kwargs["confirm"] is True
        assert kwargs["off_session"] is True
        assert kwargs["payment_method"]
