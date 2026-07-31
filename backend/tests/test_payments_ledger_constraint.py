"""Ledger-level backstop against collecting the same pledge twice.

``process_charge_for_goal`` guards against double-charging with a read-then-write
(``SELECT ... FROM payments WHERE goal_id``, skip if a row exists). Two
concurrent attempts can both pass that guard before either commits, so the
guarantee cannot live in application code alone. The partial unique index
``uq_payments_goal_id_succeeded`` — ``UNIQUE (goal_id) WHERE status =
'succeeded'`` — makes the database the authority.

The index is partial on purpose: a goal may accumulate several *non*-succeeded
payment rows (the no-payment-method path and the retry-exhausted path both
insert ``status='failed'``) and must still be collectible afterwards. These
tests pin both halves of that contract — the duplicate is refused, the retry is
not.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.payment import Payment

INDEX_NAME = "uq_payments_goal_id_succeeded"


def _sessions():
    engine = create_async_engine(settings.database_url, echo=False)
    return engine, async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


async def _seed_user_and_goal(*, with_customer: bool = True) -> tuple[str, str]:
    """Insert a user + past-deadline active goal directly, returning (goal_id, user_id).

    Bypasses the HTTP/auth path: these tests are about the storage contract, and
    the API deliberately refuses to create a goal whose deadline has passed.
    """
    user_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    # auth_provider_id / auth_session_id are varchar(36) — keep tokens short.
    token = uuid.uuid4().hex
    engine, session_factory = _sessions()
    async with session_factory() as db:
        await db.execute(
            text(
                """
                INSERT INTO users
                    (id, email, display_name, auth_provider, auth_provider_id,
                     auth_session_id, stripe_customer_id)
                VALUES (:id, :email, :name, 'google', :sub, :sess, :cus)
                """
            ),
            {
                "id": user_id,
                "email": f"ledger-{token}@example.com",
                "name": "Ledger Test User",
                "sub": token,
                "sess": token,
                "cus": "cus_test_ledger" if with_customer else None,
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO goals
                    (id, user_id, title, goal_type, pledge_amount, currency,
                     deadline, timezone, status, charity_id)
                VALUES (:id, :uid, 'Ledger Goal', 'youtube_video', 5000, 'usd',
                        :deadline, 'UTC', 'active', 'acct_charity_connect_123')
                """
            ),
            {
                "id": goal_id,
                "uid": user_id,
                "deadline": datetime.now(timezone.utc) - timedelta(days=1),
            },
        )
        await db.commit()
    await engine.dispose()
    return str(goal_id), str(user_id)


async def _insert_payment(goal_id: str, user_id: str, status: str, pi_id: str | None):
    """Insert one payments row, mirroring what the charge worker writes."""
    engine, session_factory = _sessions()
    try:
        async with session_factory() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO payments
                        (id, goal_id, user_id, amount, currency,
                         stripe_payment_intent_id, stripe_transfer_id, status, created_at)
                    VALUES (:id, :goal_id, :user_id, 5000, 'usd', :pi, NULL, :status, :now)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "goal_id": uuid.UUID(goal_id),
                    "user_id": uuid.UUID(user_id),
                    "pi": pi_id,
                    "status": status,
                    "now": datetime.now(timezone.utc),
                },
            )
            await db.commit()
    finally:
        await engine.dispose()


async def _payments_for(goal_id: str) -> list[Payment]:
    engine, session_factory = _sessions()
    async with session_factory() as db:
        result = await db.execute(
            select(Payment).where(Payment.goal_id == uuid.UUID(goal_id))
        )
        rows = list(result.scalars().all())
    await engine.dispose()
    return rows


def _stripe_mock(mock_stripe, pi_id="pi_ledger_test"):
    """Wire a fully-succeeding Stripe stub. No real API call is ever made."""
    intent = MagicMock(id=pi_id, amount=5000, currency="usd", status="succeeded")
    # Stripe returns the SAME PaymentIntent for a repeated idempotency_key —
    # within its 24h window. Modelling that is the point: beyond the window
    # Stripe stops deduping and only the ledger constraint remains.
    mock_stripe.PaymentIntent.create.return_value = intent
    mock_stripe.PaymentIntent.retrieve.return_value = intent
    mock_stripe.Transfer.create.return_value = MagicMock(id="tr_ledger", amount=4500)
    return intent


# --- The index exists at all (guards against a create_all/migration drift) ---


async def test_partial_unique_index_exists_with_expected_predicate():
    engine, session_factory = _sessions()
    async with session_factory() as db:
        indexdef = (
            await db.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
                {"n": INDEX_NAME},
            )
        ).scalar_one_or_none()
    await engine.dispose()

    assert indexdef is not None, f"{INDEX_NAME} is missing from the payments table"
    assert "UNIQUE" in indexdef
    assert "goal_id" in indexdef
    # Partial, not a blanket unique constraint.
    assert "WHERE" in indexdef and "succeeded" in indexdef


# --- The backstop actually bites at the DB level ---


async def test_duplicate_succeeded_payment_is_rejected_by_the_database():
    goal_id, user_id = await _seed_user_and_goal()
    await _insert_payment(goal_id, user_id, "succeeded", "pi_first")

    with pytest.raises(IntegrityError) as excinfo:
        await _insert_payment(goal_id, user_id, "succeeded", "pi_second")

    assert INDEX_NAME in str(excinfo.value)
    assert len(await _payments_for(goal_id)) == 1


async def test_webhook_style_update_cannot_promote_a_second_row_to_succeeded():
    """A partial unique index is enforced on UPDATE, not just INSERT.

    ``_reconcile_payment_intent`` promotes a row with
    ``UPDATE payments SET status = 'succeeded' WHERE stripe_payment_intent_id``,
    so a redelivered/misrouted webhook is a second way into double-collection.
    """
    goal_id, user_id = await _seed_user_and_goal()
    await _insert_payment(goal_id, user_id, "succeeded", "pi_already_collected")
    await _insert_payment(goal_id, user_id, "failed", "pi_earlier_attempt")

    engine, session_factory = _sessions()
    try:
        with pytest.raises(IntegrityError) as excinfo:
            async with session_factory() as db:
                await db.execute(
                    text(
                        "UPDATE payments SET status = 'succeeded' "
                        "WHERE stripe_payment_intent_id = :pi"
                    ),
                    {"pi": "pi_earlier_attempt"},
                )
                await db.commit()
    finally:
        await engine.dispose()

    assert INDEX_NAME in str(excinfo.value)
    succeeded = [p for p in await _payments_for(goal_id) if p.status == "succeeded"]
    assert len(succeeded) == 1


# --- ...without cementing "one attempt ever per goal" ---


async def test_multiple_failed_payments_for_one_goal_are_allowed():
    """A plain UNIQUE (goal_id) would reject this; the partial index must not."""
    goal_id, user_id = await _seed_user_and_goal()
    await _insert_payment(goal_id, user_id, "failed", None)
    await _insert_payment(goal_id, user_id, "failed", None)

    assert len(await _payments_for(goal_id)) == 2


async def test_retry_after_a_genuine_payment_failure_can_still_succeed():
    """The constraint must not block collecting a pledge after failed attempts.

    NB: this is a *schema*-level assertion — it inserts rows directly. That the
    application can actually reach this state is pinned separately by
    ``test_a_failed_payment_row_does_not_forgive_the_pledge`` below, which used
    to be false: the worker's guard skipped on any existing row, so the retry
    this test leaves room for was unreachable in practice.
    """
    goal_id, user_id = await _seed_user_and_goal()
    await _insert_payment(goal_id, user_id, "failed", None)
    await _insert_payment(goal_id, user_id, "failed", None)

    # The retry that finally captures the money.
    await _insert_payment(goal_id, user_id, "succeeded", "pi_retry_worked")

    rows = await _payments_for(goal_id)
    assert len(rows) == 3
    assert len([p for p in rows if p.status == "succeeded"]) == 1


async def test_a_failed_payment_row_does_not_forgive_the_pledge():
    """A goal whose first charge attempt failed must still be chargeable.

    Charge evasion, not tidiness: both failure paths in the worker write
    ``status='failed'``, and the guard used to skip on ANY existing row. So a
    user who removed their card had a `failed` row written once and then every
    future attempt short-circuited as "already_processed" — the pledge was
    silently forgiven, permanently.
    """
    from app.workers.payments import process_charge_for_goal

    goal_id, user_id = await _seed_user_and_goal()
    await _insert_payment(goal_id, user_id, "failed", None)

    with patch("app.workers.payments.stripe") as mock_stripe:
        _stripe_mock(mock_stripe, pi_id="pi_after_earlier_failure")
        result = await process_charge_for_goal(goal_id, user_id)

    assert result.get("reason") != "already_processed", (
        "a previously failed attempt must not block collection"
    )
    rows = await _payments_for(goal_id)
    succeeded = [p for p in rows if p.status == "succeeded"]
    assert len(succeeded) == 1
    assert succeeded[0].stripe_payment_intent_id == "pi_after_earlier_failure"


async def test_an_in_flight_pending_payment_still_blocks_a_second_attempt():
    """The narrowed guard must not open a double-collection window.

    `pending` stays in the skip set: a row mid-confirmation may yet succeed, and
    racing it is how you charge twice.
    """
    from app.workers.payments import process_charge_for_goal

    goal_id, user_id = await _seed_user_and_goal()
    await _insert_payment(goal_id, user_id, "pending", "pi_in_flight")

    with patch("app.workers.payments.stripe") as mock_stripe:
        _stripe_mock(mock_stripe, pi_id="pi_should_not_be_created")
        result = await process_charge_for_goal(goal_id, user_id)

    assert result == {"status": "skipped", "reason": "already_processed"}
    mock_stripe.PaymentIntent.create.assert_not_called()


# --- The race the constraint exists for ---


async def test_concurrent_charge_attempts_collect_the_pledge_only_once():
    """Two overlapping charge attempts must leave exactly one succeeded row.

    Both attempts open their own session, so both can pass the worker's
    read-then-write guard; the ledger constraint is what makes the outcome
    single-valued.
    """
    from app.workers.payments import process_charge_for_goal

    goal_id, user_id = await _seed_user_and_goal()

    with patch("app.workers.payments.stripe") as mock_stripe:
        _stripe_mock(mock_stripe, pi_id="pi_concurrent")
        results = await asyncio.gather(
            process_charge_for_goal(goal_id, user_id),
            process_charge_for_goal(goal_id, user_id),
            return_exceptions=True,
        )

    succeeded = [p for p in await _payments_for(goal_id) if p.status == "succeeded"]
    assert len(succeeded) == 1, f"pledge collected {len(succeeded)} times: {results}"

    # Exactly one attempt lost, and it lost to the index rather than to luck.
    failures = [r for r in results if isinstance(r, IntegrityError)]
    assert len(failures) == 1, f"expected one rejected attempt, got: {results}"
    assert INDEX_NAME in str(failures[0])

    # Boundary worth stating precisely: the ledger constraint dedupes the ROW,
    # not the API CALL. Both attempts pass the worker's read-then-write guard
    # (app/workers/payments.py:135-145) and both reach Stripe, so create() is
    # called twice. What keeps that from being two charges is the shared
    # idempotency key -- and Stripe expires those after 24h, which is exactly
    # why the row-level backstop above has to exist. Collapsing the second API
    # call as well means changing the worker's guard (insert-first / SELECT FOR
    # UPDATE), not the schema.
    assert mock_stripe.PaymentIntent.create.call_count == 2
    keys = {
        call.kwargs["idempotency_key"]
        for call in mock_stripe.PaymentIntent.create.call_args_list
    }
    assert keys == {f"goal-charge-{goal_id}"}
