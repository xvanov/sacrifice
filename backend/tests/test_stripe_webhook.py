"""Tests for the Stripe webhook endpoint (Direction 019).

Stripe is the source of truth for whether money moved. These tests verify the
endpoint (a) rejects unsigned/forged events, (b) reconciles a succeeded charge
to a failed(charged) goal, and (c) never overrides a verified goal.
"""

import uuid
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models.goal import Goal


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="wh@example.com", name="WH", sub="wh-sub", token="t"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None, "email_verified": True}
        resp = await client.post("/api/auth/google", json={"token": token})
        return resp.json()["access_token"], resp.json()["user"]


async def _mk_goal(client, token, status_value="active"):
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "WH goal",
            "deadline": "2999-01-01T00:00:00Z",
            "pledge_amount": 5000,
            "goal_type": "youtube_video",
            "criteria": {"min_duration_seconds": 60, "video_description": "x"},
        },
    )
    goal_id = resp.json()["id"]
    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        await db.execute(
            text("UPDATE goals SET status = :s WHERE id = :g"),
            {"s": status_value, "g": goal_id},
        )
        # A pending payment row awaiting reconciliation.
        await db.execute(
            text(
                """INSERT INTO payments
                   (id, goal_id, user_id, amount, currency,
                    stripe_payment_intent_id, stripe_transfer_id, status, created_at)
                   VALUES (:id, :g, (SELECT user_id FROM goals WHERE id = :g),
                    5000, 'usd', :pi, NULL, 'pending', NOW())"""
            ),
            {"id": uuid.uuid4(), "g": goal_id, "pi": "pi_wh_1"},
        )
        await db.commit()
    await engine.dispose()
    return goal_id


async def _goal_status(goal_id):
    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        row = (await db.execute(select(Goal).where(Goal.id == goal_id))).scalar_one()
        s = row.status
    await engine.dispose()
    return s


async def test_webhook_rejects_invalid_signature():
    async with make_client() as client:
        with patch.object(settings, "stripe_webhook_secret", "whsec_test"):
            resp = await client.post(
                "/api/webhooks/stripe",
                content=b"{}",
                headers={"stripe-signature": "t=1,v1=forged"},
            )
        assert resp.status_code == 400


async def test_webhook_succeeded_marks_goal_charged():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _mk_goal(client, token, status_value="active")

        event = {
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_wh_1", "metadata": {"goal_id": goal_id}}},
        }
        with patch.object(settings, "stripe_webhook_secret", "whsec_test"), patch(
            "app.routes.webhooks.stripe.Webhook.construct_event", return_value=event
        ):
            resp = await client.post(
                "/api/webhooks/stripe",
                content=b"{}",
                headers={"stripe-signature": "sig"},
            )
        assert resp.status_code == 200
        assert await _goal_status(goal_id) == "failed"  # failed goal, pledge charged


async def test_webhook_never_overrides_verified_goal():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _mk_goal(client, token, status_value="verified")

        event = {
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_wh_1", "metadata": {"goal_id": goal_id}}},
        }
        with patch.object(settings, "stripe_webhook_secret", "whsec_test"), patch(
            "app.routes.webhooks.stripe.Webhook.construct_event", return_value=event
        ):
            resp = await client.post(
                "/api/webhooks/stripe",
                content=b"{}",
                headers={"stripe-signature": "sig"},
            )
        assert resp.status_code == 200
        assert await _goal_status(goal_id) == "verified"


async def test_webhook_missing_secret_fails_closed():
    async with make_client() as client:
        with patch.object(settings, "stripe_webhook_secret", ""):
            resp = await client.post(
                "/api/webhooks/stripe",
                content=b"{}",
                headers={"stripe-signature": "sig"},
            )
        assert resp.status_code == 503
