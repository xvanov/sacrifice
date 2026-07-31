import asyncio
import logging
import uuid
from datetime import datetime, timezone

import stripe
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.goal import Goal
from app.services import everyorg, pledge

stripe.api_key = settings.stripe_secret_key

logger = logging.getLogger(__name__)

PLATFORM_FEE_PERCENT = 10
MAX_RETRIES = 3
BASE_DELAY_SECONDS = 2


def _get_session():
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return engine, session_factory


def _resolve_payment_method(customer_id: str | None) -> str | None:
    """Return a chargeable payment-method id for ``customer_id``, or None.

    Prefers the customer's invoice-settings default; falls back to the first
    saved card. Any Stripe error or absence of a card yields None so the
    caller records a clean "no payment method" failure rather than crashing.
    """
    if not customer_id:
        return None
    # NB: StripeObject in the pinned stripe version is NOT a dict subclass:
    # .get() raises AttributeError and dict(obj) raises KeyError(0). Only
    # plain ["key"] subscripting is safe — anything else silently turned
    # every charge into "no payment method" via the broad except below.
    try:
        customer = stripe.Customer.retrieve(customer_id)
        try:
            default_pm = customer["invoice_settings"]["default_payment_method"]
        except (KeyError, TypeError):
            default_pm = None
        if default_pm:
            return default_pm
        methods = stripe.PaymentMethod.list(customer=customer_id, type="card")
        try:
            data = methods["data"] or []
        except (KeyError, TypeError):
            data = []
        if data:
            return data[0]["id"]
    except Exception as e:  # noqa: BLE001 — never let billing lookup crash the worker
        logger.warning("Payment-method lookup failed for %s: %s", customer_id, e)
    return None


async def _record_charge_failure(
    db: AsyncSession, *, goal, user_id_str: str, amount: int, body: str
) -> None:
    """Mark the goal payment_failed and record a failed payment + notification.

    Shared by the no-payment-method path and the retry-exhausted path so the
    two failure modes stay consistent.
    """
    now = datetime.now(timezone.utc)
    await db.execute(
        text("UPDATE goals SET status = :status WHERE id = :goal_id"),
        {"goal_id": goal.id, "status": "payment_failed"},
    )
    await db.execute(
        text(
            """
            INSERT INTO payments
                (id, goal_id, user_id, amount, currency,
                 stripe_payment_intent_id, stripe_transfer_id, status, created_at)
            VALUES
                (:id, :goal_id, :user_id, :amount, :currency,
                 :pi_id, :transfer_id, :status, :created_at)
            """
        ),
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
        text(
            """
            INSERT INTO notifications
                (id, user_id, goal_id, type, title, body, read, created_at)
            VALUES
                (:id, :user_id, :goal_id, :type, :title, :body, :read, :created_at)
            """
        ),
        {
            "id": uuid.uuid4(),
            "user_id": uuid.UUID(user_id_str),
            "goal_id": goal.id,
            "type": "goal_failed",
            "title": "Payment Failed",
            "body": body,
            "read": False,
            "created_at": now,
        },
    )


async def process_charge_for_goal(goal_id_str: str, user_id_str: str) -> dict:
    engine, session_factory = _get_session()
    async with session_factory() as db:
        try:
            result = await db.execute(select(Goal).where(Goal.id == goal_id_str))
            goal = result.scalar_one_or_none()
            if not goal:
                raise ValueError(f"Goal {goal_id_str} not found")

            if goal.status == "verified":
                logger.info("Goal %s is verified, skipping charge", goal_id_str)
                return {"status": "skipped", "reason": "verified"}

            # Only a collected (or in-flight) payment blocks another attempt.
            #
            # This used to skip on ANY existing row, which made a single failed
            # attempt permanent forgiveness: both failure paths below write
            # status='failed' (no payment method, and retries exhausted), so a
            # user who removed their card had every future attempt
            # short-circuit and never paid the pledge. `succeeded` is protected
            # by uq_payments_goal_id_succeeded at the DB level; `pending` is
            # included because a row mid-confirmation may yet succeed and we
            # must not race it.
            existing = await db.execute(
                text(
                    "SELECT id, status FROM payments "
                    "WHERE goal_id = :goal_id AND status IN ('succeeded', 'pending')"
                ),
                {"goal_id": goal.id},
            )
            existing_row = existing.first()
            if existing_row is not None:
                logger.info(
                    "Skipping charge for goal %s — payment row already exists with status %s",
                    goal.id,
                    existing_row.status,
                )
                return {"status": "skipped", "reason": "already_processed"}

            amount = goal.pledge_amount
            fee = int(amount * PLATFORM_FEE_PERCENT / 100)
            transfer_amount = amount - fee

            result = await db.execute(
                text("SELECT stripe_customer_id FROM users WHERE id = :uid"),
                {"uid": user_id_str},
            )
            row = result.one_or_none()
            customer_id = row[0] if row else None

            # An off-session charge needs a saved payment method. The card is
            # collected up front via the SetupIntent flow (POST
            # /api/payment/setup-intent); here we charge the customer's saved
            # card WITHOUT them present. Missing customer/card = we can never
            # capture the pledge, so record the failure and prompt them to add
            # one — instead of the old bug where an unconfirmed PaymentIntent
            # was created and its never-"succeeded" status silently dropped the
            # charge.
            payment_method_id = _resolve_payment_method(customer_id)
            if not payment_method_id:
                await _record_charge_failure(
                    db,
                    goal=goal,
                    user_id_str=user_id_str,
                    amount=amount,
                    body=(
                        f"Your pledge of ${amount / 100:.2f} could not be charged "
                        "because no payment method is on file. Add a card in "
                        "settings so future pledges can be honored."
                    ),
                )
                await db.commit()
                logger.warning(
                    "No usable payment method for user %s / goal %s; charge skipped",
                    user_id_str,
                    goal_id_str,
                )
                return {"status": "failed", "reason": "no_payment_method"}

            payment_intent = None
            last_error = None

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    payment_intent = stripe.PaymentIntent.create(
                        amount=amount,
                        currency="usd",
                        customer=customer_id,
                        payment_method=payment_method_id,
                        # Charge the saved card now, with the user not present.
                        # This is what actually captures the money — the old
                        # code omitted these and the intent stayed in
                        # requires_payment_method forever.
                        confirm=True,
                        off_session=True,
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
                        attempt,
                        MAX_RETRIES,
                        goal_id_str,
                        str(e),
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
                        "body": f"Your pledge of ${amount / 100:.2f} could not be charged after {MAX_RETRIES} attempts. Please update your payment method.",
                        "read": False,
                        "created_at": now,
                    },
                )
                await db.commit()
                raise last_error or Exception("Payment creation failed after retries")

            payment_intent_id = payment_intent.id

            pi_retrieved = stripe.PaymentIntent.retrieve(payment_intent_id)
            payment_status = (
                "succeeded" if pi_retrieved.status == "succeeded" else "failed"
            )

            transfer_id = None
            donate_url = None
            pledge_donation = None
            if payment_status == "succeeded" and (
                everyorg.is_everyorg_id(goal.charity_id)
                or pledge.is_pledge_id(goal.charity_id)
            ):
                # Public-charity recipients don't take Stripe transfers.
                # Pledge.to orgs are donated to automatically after the
                # payment row is minted below; Every.org has no server-side
                # donation API, so those get a prefilled checkout link.
                pass
            elif payment_status == "succeeded" and goal.charity_id:
                # The charge has already been captured; a transfer failure
                # (typically: recipient account hasn't completed Connect
                # onboarding) must not blow up the task, or the payment row
                # below is never written and the money captured above becomes
                # invisible to the app. Record the payment without a transfer
                # and leave the payout to be retried/handled manually.
                try:
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
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "Transfer to %s failed for goal %s (charge %s kept): %s",
                        goal.charity_id,
                        goal_id_str,
                        payment_intent_id,
                        e,
                    )

            now = datetime.now(timezone.utc)

            goal_status = (
                "failed" if payment_status == "succeeded" else "payment_failed"
            )

            await db.execute(
                text("UPDATE goals SET status = :status WHERE id = :goal_id"),
                {"goal_id": goal.id, "status": goal_status},
            )

            payment_id = uuid.uuid4()
            if payment_status == "succeeded" and everyorg.is_everyorg_id(
                goal.charity_id
            ):
                donate_url = everyorg.build_donate_url(
                    goal.charity_id, transfer_amount, str(payment_id)
                )
            elif payment_status == "succeeded" and pledge.is_pledge_id(goal.charity_id):
                # Automatic disbursement. A donation failure must never mask
                # the already-captured charge — record and move on.
                user_row = (
                    await db.execute(
                        text("SELECT email, display_name FROM users WHERE id = :uid"),
                        {"uid": user_id_str},
                    )
                ).one_or_none()
                email = user_row[0] if user_row else "pledges@sacrifice.app"
                display = (user_row[1] if user_row else "") or "Sacrifice Pledger"
                first, _, last = display.partition(" ")
                try:
                    pledge_donation = await pledge.create_donation(
                        goal.charity_id,
                        transfer_amount,
                        email=email,
                        first_name=first,
                        last_name=last or "Pledger",
                        external_id=str(payment_id),
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "Pledge.to donation failed for goal %s payment %s: %s",
                        goal_id_str,
                        payment_id,
                        e,
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
                    "id": payment_id,
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
                if transfer_id:
                    receipt_title = "Donation Receipt"
                    receipt_body = (
                        f"Your pledge of ${amount / 100:.2f} has been charged and "
                        "donated to your selected charity."
                    )
                elif pledge_donation is not None:
                    receipt_title = "Donation Receipt"
                    receipt_body = (
                        f"Your pledge of ${amount / 100:.2f} has been charged and "
                        f"${transfer_amount / 100:.2f} was donated to your chosen "
                        "charity automatically."
                    )
                elif pledge.is_pledge_id(goal.charity_id):
                    # Charge captured but the automatic donation errored.
                    receipt_title = "Pledge Charged — Donation Delayed"
                    receipt_body = (
                        f"Your pledge of ${amount / 100:.2f} has been charged. The "
                        "donation to your chosen charity hit an error and will "
                        "be retried."
                    )
                elif donate_url:
                    receipt_title = "Pledge Charged — Donation Pending"
                    receipt_body = (
                        f"Your pledge of ${amount / 100:.2f} has been charged. "
                        f"Complete the ${transfer_amount / 100:.2f} donation to "
                        f"your chosen nonprofit here: {donate_url}"
                    )
                    logger.info(
                        "Every.org donation pending for goal %s payment %s: %s",
                        goal_id_str,
                        payment_id,
                        donate_url,
                    )
                else:
                    # No recipient (or transfer couldn't run): the pledge is
                    # still charged — that's the accountability contract.
                    receipt_title = "Pledge Charged"
                    receipt_body = (
                        f"Your pledge of ${amount / 100:.2f} has been charged "
                        "because the goal failed."
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
                        "type": "donation_receipt",
                        "title": receipt_title,
                        "body": receipt_body,
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
            await engine.dispose()
