import asyncio
import logging
import uuid
from datetime import datetime, timezone

import stripe
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.goal import Goal

stripe.api_key = settings.stripe_secret_key

logger = logging.getLogger(__name__)

PLATFORM_FEE_PERCENT = 10
MAX_RETRIES = 3
BASE_DELAY_SECONDS = 2


def _get_session():
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory


async def process_charge_for_goal(goal_id_str: str, user_id_str: str, _session_factory=None) -> dict:
    if not settings.stripe_secret_key:
        raise RuntimeError("Stripe secret key is not configured — cannot process charges")

    if _session_factory is not None:
        engine = None
        session_factory = _session_factory
    else:
        from app.database import async_session as app_async_session
        engine = None
        session_factory = app_async_session
    async with session_factory() as db:
        try:
            result = await db.execute(
                select(Goal).where(Goal.id == goal_id_str)
            )
            goal = result.scalar_one_or_none()
            if not goal:
                raise ValueError(f"Goal {goal_id_str} not found")

            if goal.status == "verified":
                logger.info("Goal %s is verified, skipping charge", goal_id_str)
                return {"status": "skipped", "reason": "verified"}

            existing = await db.execute(
                text("SELECT id, status FROM payments WHERE goal_id = :goal_id"),
                {"goal_id": goal.id},
            )
            existing_row = existing.first()
            if existing_row is not None:
                logger.info(
                    "Skipping charge for goal %s — payment row already exists with status %s",
                    goal.id, existing_row.status,
                )
                return {"status": "skipped", "reason": "already_processed"}

            amount = goal.pledge_amount
            fee = int(amount * PLATFORM_FEE_PERCENT / 100)
            transfer_amount = amount - fee

            result = await db.execute(
                select(text("stripe_customer_id FROM users WHERE id = :uid")),
                {"uid": user_id_str},
            )
            row = result.one_or_none()
            customer_id = row[0] if row else None

            payment_intent = None
            last_error = None

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    payment_intent = stripe.PaymentIntent.create(
                        amount=amount,
                        currency="usd",
                        customer=customer_id,
                        metadata={
                            "goal_id": goal_id_str,
                            "user_id": user_id_str,
                            "attempt": str(attempt),
                        },
                        idempotency_key=f"goal-charge-{goal_id_str}",
                    )
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    logger.warning(
                        "Payment attempt %d/%d failed for goal %s: %s",
                        attempt, MAX_RETRIES, goal_id_str, str(e),
                    )
                    if attempt < MAX_RETRIES:
                        delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                        await asyncio.sleep(delay)

            if last_error or not payment_intent:
                now = datetime.now(timezone.utc)
                await db.execute(
                    text("""
                        UPDATE goals SET status = :status
                        WHERE id = :goal_id
                    """),
                    {"goal_id": goal.id, "status": "payment_failed"},
                )
                await db.execute(
                    text("""
                        INSERT INTO payments
                            (id, goal_id, user_id, amount, currency,
                             stripe_payment_intent_id, stripe_transfer_id, status, created_at)
                        VALUES
                            (:id, :goal_id, :user_id, :amount, :currency,
                             :pi_id, :transfer_id, :status, :created_at)
                    """),
                    {
                        "id": uuid.uuid4(),
                        "goal_id": goal.id,
                        "user_id": uuid.UUID(user_id_str),
                        "amount": amount,
                        "currency": "usd",
                        "pi_id": None,
                        "transfer_id": None,
                        "status": "failed",
                        "created_at": now,
                    },
                )
                await db.execute(
                    text("""
                        INSERT INTO notifications
                            (id, user_id, goal_id, type, title, body, read, created_at)
                        VALUES
                            (:id, :user_id, :goal_id, :type, :title, :body, :read, :created_at)
                    """),
                    {
                        "id": uuid.uuid4(),
                        "user_id": uuid.UUID(user_id_str),
                        "goal_id": goal.id,
                        "type": "goal_failed",
                        "title": "Payment Failed",
                        "body": f"Your pledge of ${amount/100:.2f} could not be charged after {MAX_RETRIES} attempts. Please update your payment method.",
                        "read": False,
                        "created_at": now,
                    },
                )
                await db.commit()
                raise last_error or Exception("Payment creation failed after retries")

            payment_intent_id = payment_intent.id

            pi_retrieved = stripe.PaymentIntent.retrieve(payment_intent_id)
            payment_status = "succeeded" if pi_retrieved.status == "succeeded" else "failed"

            transfer_id = None
            if payment_status == "succeeded" and goal.charity_id:
                transfer = stripe.Transfer.create(
                    amount=transfer_amount,
                    currency="usd",
                    destination=goal.charity_id,
                    transfer_group=f"goal_{goal_id_str}",
                    metadata={
                        "goal_id": goal_id_str,
                        "payment_intent_id": payment_intent_id,
                        "platform_fee": str(fee),
                    },
                )
                transfer_id = transfer.id

            now = datetime.now(timezone.utc)

            goal_status = "failed" if payment_status == "succeeded" else "payment_failed"

            await db.execute(
                text("UPDATE goals SET status = :status WHERE id = :goal_id"),
                {"goal_id": goal.id, "status": goal_status},
            )

            await db.execute(
                text("""
                    INSERT INTO payments
                        (id, goal_id, user_id, amount, currency,
                         stripe_payment_intent_id, stripe_transfer_id, status, created_at)
                    VALUES
                        (:id, :goal_id, :user_id, :amount, :currency,
                         :pi_id, :transfer_id, :status, :created_at)
                """),
                {
                    "id": uuid.uuid4(),
                    "goal_id": goal.id,
                    "user_id": uuid.UUID(user_id_str),
                    "amount": amount,
                    "currency": "usd",
                    "pi_id": payment_intent_id,
                    "transfer_id": transfer_id,
                    "status": payment_status,
                    "created_at": now,
                },
            )

            if payment_status == "succeeded":
                await db.execute(
                    text("""
                        INSERT INTO notifications
                            (id, user_id, goal_id, type, title, body, read, created_at)
                        VALUES
                            (:id, :user_id, :goal_id, :type, :title, :body, :read, :created_at)
                    """),
                    {
                        "id": uuid.uuid4(),
                        "user_id": uuid.UUID(user_id_str),
                        "goal_id": goal.id,
                        "type": "donation_receipt",
                        "title": "Donation Receipt",
                        "body": f"Your pledge of ${amount/100:.2f} has been charged and donated to your selected charity.",
                        "read": False,
                        "created_at": now,
                    },
                )

            await db.commit()

            return {
                "status": payment_status,
                "payment_intent_id": payment_intent_id,
                "transfer_id": transfer_id,
                "amount": amount,
            }

        finally:
            await db.close()
            if engine is not None:
                await engine.dispose()
