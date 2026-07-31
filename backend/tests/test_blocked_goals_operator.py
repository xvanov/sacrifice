"""The operator path for goals blocked on a verification we could not complete.

Before this existed, ``needs_operator_review`` had no reader: a goal whose
verification ended ``inconclusive`` was skipped by every deadline sweep, forever,
with no way to see it and no way to clear it. Silent, permanent forgiveness of
the pledge, plus a user who was told a human was looking.

What these tests hold in place, in order of how much a regression would cost:

1. **Giving up never charges.** Asserted against the charge boundary itself
   (patched ``process_charge_for_goal``), not a proxy like the goal's status,
   because the whole reason an inconclusive outcome exists is that the fault was
   ours.
2. **Retry actually re-enters the pipeline** — asserted against the
   reconciler's own claim query, so "claimable" cannot drift into a guess about
   what that query looks like.
3. **The reader shows exactly the stranded goals** and no others.
4. **The endpoint is not reachable by an ordinary logged-in user**, and leaks no
   token material.
5. **A blocked recurring goal does not silently end its series.**
"""

import asyncio
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.operator_auth import OPERATOR_TOKEN_HEADER
from app.main import app
from app.models.goal import Goal, GoalCriteria
from app.models.proof import ProofSubmission
from app.models.user import User
from app.services import blocked_goals as bg
from app.services import verification_result as vr
from app.services.auth import create_access_token

# Every binding of the charge boundary. Patching only the definition is not
# enough: ``app.workers.deadline`` does ``from app.workers.payments import
# process_charge_for_goal`` at import time, so it holds its own reference and a
# test that patched only the source would let a REAL Stripe call through (seen
# while writing these: an unpatched deadline sweep reached
# ``PaymentMethod.list`` against the dummy test key). Patching both is also what
# makes ``assert_no_charge`` mean "no path charged", not "one path didn't".
CHARGE_BINDINGS = (
    # The definition — covers the lazy import inside persist_verification_result.
    "app.workers.payments.process_charge_for_goal",
    # The name the deadline sweep actually calls.
    "app.workers.deadline.process_charge_for_goal",
)


@contextmanager
def charge_boundary():
    """Patch every binding of ``process_charge_for_goal``; yield the mocks."""
    # Import both modules BEFORE patching anything. If ``app.workers.deadline``
    # were first imported while ``app.workers.payments.process_charge_for_goal``
    # was already patched, its ``from ... import`` would bind the *mock*, and
    # ``mock.patch`` would then faithfully "restore" that mock on exit — leaving
    # the sweep permanently wired to a dead AsyncMock. Cost of getting this wrong
    # (observed while writing these tests): every later test that runs
    # ``check_deadlines`` silently stops charging, so
    # test_deadline_worker.py::test_deadline_charge_runs_with_real_worker_without_deadlocking
    # failed with zero payments, but only when this file ran first.
    import app.workers.deadline  # noqa: F401
    import app.workers.payments  # noqa: F401

    with ExitStack() as stack:
        yield [
            stack.enter_context(patch(target, new_callable=AsyncMock))
            for target in CHARGE_BINDINGS
        ]


def assert_no_charge(charges):
    for mock in charges:
        mock.assert_not_awaited()
        mock.assert_not_called()


def assert_charged_once(charges):
    assert sum(mock.await_count for mock in charges) == 1


# Long enough to satisfy operator_auth.MIN_TOKEN_LENGTH.
OPERATOR_TOKEN = "test-operator-token-that-is-long-enough-32"


def _session_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    return engine, async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _blocked_details(
    reason: str = vr.REASON_UPSTREAM_UNAVAILABLE,
    *,
    needs_review: bool = True,
    blocked_ago: timedelta = timedelta(hours=6),
    extra: dict | None = None,
) -> dict:
    """The details blob ``verification_result`` writes for an inconclusive run."""
    details = {
        "outcome": vr.INCONCLUSIVE,
        "inconclusive_reason": reason,
        "inconclusive_retryable": reason in vr.TRANSIENT_REASONS,
        "inconclusive_at": (datetime.now(timezone.utc) - blocked_ago).isoformat(),
        "needs_operator_review": needs_review,
        "user_message": "We could not complete the check for this proof.",
    }
    details.update(extra or {})
    return details


async def _make_goal(
    db: AsyncSession,
    *,
    goal_status: str = "active",
    submission_status: str = "pending",
    verification_details: dict | None = None,
    dispatch_attempts: int = 4,
    deadline_offset: timedelta = timedelta(hours=-2),
    submitted_ago: timedelta = timedelta(hours=6),
    recurrence: str = "none",
    email: str | None = None,
    with_submission: bool = True,
    proof_data: dict | None = None,
    dispatch_criteria: dict | None = None,
) -> tuple[User, Goal, ProofSubmission | None]:
    """A user + past-deadline goal + optional submission, built through the ORM.

    Built directly rather than through the API because these are states the API
    refuses to create: a deadline already past, a spent attempt counter, a
    submission stamped hours ago.
    """
    user = User(
        email=email or f"blocked-{uuid.uuid4()}@example.com",
        display_name="Blocked Goal Owner",
        auth_provider="google",
        auth_provider_id=str(uuid.uuid4()),
        stripe_customer_id="cus_test_dummy",
    )
    db.add(user)
    await db.flush()

    goal = Goal(
        user_id=user.id,
        title="Ship the thing",
        goal_type="github_repo",
        pledge_amount=5000,
        currency="usd",
        deadline=datetime.now(timezone.utc) + deadline_offset,
        timezone="UTC",
        recurrence=recurrence,
        status=goal_status,
        charity_id="acct_charity123",
    )
    db.add(goal)
    await db.flush()
    db.add(
        GoalCriteria(
            goal_id=goal.id,
            criteria_type="github_repo",
            criteria_data={"conditions": [{"type": "commits", "min_count": 3}]},
        )
    )

    submission = None
    if with_submission:
        submission = ProofSubmission(
            goal_id=goal.id,
            submitted_at=datetime.now(timezone.utc) - submitted_ago,
            proof_data=proof_data
            or {"repo_url": "https://github.com/octocat/hello-world"},
            verification_status=submission_status,
            verification_details=verification_details,
            dispatched_at=datetime.now(timezone.utc) - submitted_ago,
            dispatch_attempts=dispatch_attempts,
            dispatch_criteria=dispatch_criteria,
        )
        db.add(submission)

    await db.commit()
    await db.refresh(goal)
    if submission is not None:
        await db.refresh(submission)
    return user, goal, submission


# ── The reader ─────────────────────────────────────────────────────────────


async def test_list_shows_blocked_and_nothing_else():
    """Only goals the deadline sweep is skipping appear.

    The three negatives are the states that must never be mistaken for blocked:
    an active goal being verified normally, a goal that passed, and a goal that
    genuinely failed (whose pledge is legitimately collectable).
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, blocked_goal, _ = await _make_goal(
                db, verification_details=_blocked_details()
            )
            # Healthy: pending verification, no inconclusive marker.
            _, healthy, _ = await _make_goal(
                db,
                verification_details={"checked": True},
                deadline_offset=timedelta(days=3),
            )
            _, verified, _ = await _make_goal(
                db,
                goal_status="verified",
                submission_status="verified",
                verification_details={"outcome": "verified"},
            )
            # A real failure. Note the inconclusive marker on the details: even
            # then, a settled verdict is not blocked.
            _, failed, _ = await _make_goal(
                db,
                goal_status="failed",
                submission_status="failed",
                verification_details=_blocked_details(),
            )

            listed = await bg.list_blocked_goals(db)
            ids = {b.goal_id for b in listed}

            assert blocked_goal.id in ids
            assert healthy.id not in ids
            assert verified.id not in ids
            assert failed.id not in ids
    finally:
        await engine.dispose()


async def test_list_agrees_with_the_predicate_the_deadline_sweep_uses():
    """Every listed goal is one ``goal_verification_is_blocked`` also calls blocked.

    The list exists to explain why the sweep is skipping a goal. If the two ever
    disagree, the list is fiction.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            await _make_goal(db, verification_details=_blocked_details())
            await _make_goal(
                db, verification_details=_blocked_details(needs_review=False)
            )
            await _make_goal(db, verification_details={"checked": True})

            listed = await bg.list_blocked_goals(db)
            assert listed
            for b in listed:
                assert await vr.goal_verification_is_blocked(db, b.goal_id) is True
    finally:
        await engine.dispose()


async def test_list_reports_reason_attempts_and_sorts_longest_blocked_first():
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, recent, _ = await _make_goal(
                db,
                verification_details=_blocked_details(
                    vr.REASON_UPSTREAM_RATE_LIMITED, blocked_ago=timedelta(hours=1)
                ),
                dispatch_attempts=4,
            )
            _, oldest, _ = await _make_goal(
                db,
                verification_details=_blocked_details(
                    vr.REASON_CRITERIA_NOT_EVALUABLE, blocked_ago=timedelta(days=9)
                ),
                dispatch_attempts=4,
            )

            listed = await bg.list_blocked_goals(db)
            by_id = {b.goal_id: b for b in listed}
            assert [b.goal_id for b in listed].index(oldest.id) < [
                b.goal_id for b in listed
            ].index(recent.id)

            assert by_id[oldest.id].inconclusive_reason == (
                vr.REASON_CRITERIA_NOT_EVALUABLE
            )
            assert by_id[oldest.id].blocked_for_seconds > 8 * 86400
            assert by_id[oldest.id].dispatch_attempts == 4
            assert by_id[oldest.id].max_attempts == (
                settings.verification_dispatch_max_attempts
            )
            assert by_id[oldest.id].needs_operator_review is True
            assert by_id[recent.id].inconclusive_reason == (
                vr.REASON_UPSTREAM_RATE_LIMITED
            )
    finally:
        await engine.dispose()


async def test_list_needs_review_only_excludes_goals_still_being_retried():
    """The retry budget is the line between "watch it" and "nothing will move it"."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, exhausted, _ = await _make_goal(
                db, verification_details=_blocked_details(needs_review=True)
            )
            _, retrying, _ = await _make_goal(
                db,
                verification_details=_blocked_details(needs_review=False),
                dispatch_attempts=1,
            )

            everything = {b.goal_id for b in await bg.list_blocked_goals(db)}
            assert {exhausted.id, retrying.id} <= everything

            narrowed = {
                b.goal_id
                for b in await bg.list_blocked_goals(db, needs_review_only=True)
            }
            assert exhausted.id in narrowed
            assert retrying.id not in narrowed
    finally:
        await engine.dispose()


async def test_list_never_exposes_proof_data_or_criteria_snapshot():
    """``dispatch_criteria`` and ``proof_data`` can hold an encrypted GitHub PAT."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            await _make_goal(
                db,
                verification_details=_blocked_details(
                    extra={"inconclusive_detail": "gh_secret_in_detail"}
                ),
                proof_data={"github_token": "gh_secret_in_proof_data"},
                dispatch_criteria={"github_token": "gh_secret_in_criteria"},
            )

            payloads = [b.to_public_dict() for b in await bg.list_blocked_goals(db)]
            assert payloads
            blob = repr(payloads)
            for secret in (
                "gh_secret_in_proof_data",
                "gh_secret_in_criteria",
                "gh_secret_in_detail",
            ):
                assert secret not in blob
            for field in ("proof_data", "dispatch_criteria", "inconclusive_detail"):
                assert all(field not in p for p in payloads)
    finally:
        await engine.dispose()


# ── Resolve: give up ───────────────────────────────────────────────────────


async def test_give_up_reaches_a_terminal_state_and_never_charges():
    """THE test. Our fault, so the pledge is not ours to collect — ever.

    Asserted at the charge boundary: ``process_charge_for_goal`` creates a real
    off-session Stripe PaymentIntent, and an operator clearing a stuck queue must
    not be able to trigger one.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, submission = await _make_goal(
                db, verification_details=_blocked_details()
            )

            with charge_boundary() as charges:
                result = await bg.resolve_blocked_goal(db, goal.id, bg.ACTION_GIVE_UP)

            assert_no_charge(charges)

            assert result.new_goal_status == bg.GIVE_UP_STATUS == "cancelled"

            status = (
                await db.execute(
                    text("SELECT status FROM goals WHERE id = :id"), {"id": goal.id}
                )
            ).scalar_one()
            assert status == "cancelled"

            # Terminal in the sense that matters: the deadline sweep only
            # enforces active/pending_review, so this goal can never be charged
            # by a later sweep either.
            from app.workers.deadline import ENFORCEABLE_STATUSES

            assert status not in ENFORCEABLE_STATUSES

            # And no payment row was written by any path.
            payments = (
                await db.execute(
                    text("SELECT COUNT(*) FROM payments WHERE goal_id = :id"),
                    {"id": goal.id},
                )
            ).scalar_one()
            assert payments == 0

            # The submission keeps its honest non-verdict; nothing pretends the
            # user passed or failed.
            row = (
                await db.execute(
                    text(
                        "SELECT verification_status, dispatch_attempts, "
                        "verification_details FROM proof_submissions WHERE id = :id"
                    ),
                    {"id": submission.id},
                )
            ).one()
            assert row.verification_status == "pending"
            assert row.verification_details["operator_action"] == bg.ACTION_GIVE_UP
            assert row.verification_details["needs_operator_review"] is False
    finally:
        await engine.dispose()


async def test_give_up_stops_the_reconciler_from_re_verifying_the_closed_goal():
    """A give-up on a row with retry budget left must not be undone by the sweep.

    The reconciler claims on ``verification_status='pending'`` alone — it never
    looks at the goal's status — so an unsaturated attempt counter would have it
    re-verify a goal we just closed, and a ``failed`` verdict there charges.
    """
    from app.workers.reconcile_dispatch import count_stale_dispatches

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, _ = await _make_goal(
                db,
                verification_details=_blocked_details(needs_review=False),
                dispatch_attempts=1,
            )
            assert await count_stale_dispatches(db) >= 1

            with charge_boundary():
                await bg.resolve_blocked_goal(db, goal.id, bg.ACTION_GIVE_UP)

            assert await count_stale_dispatches(db) == 0
    finally:
        await engine.dispose()


async def test_give_up_notifies_the_owner_it_was_closed_without_a_charge():
    """The user was told a team was looking into it. Closing it silently repeats
    the original failure."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            user, goal, _ = await _make_goal(
                db, verification_details=_blocked_details()
            )

            with charge_boundary():
                await bg.resolve_blocked_goal(db, goal.id, bg.ACTION_GIVE_UP)

            row = (
                await db.execute(
                    text(
                        "SELECT title, body FROM notifications "
                        "WHERE goal_id = :id AND user_id = :uid "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"id": goal.id, "uid": user.id},
                )
            ).one()
            assert "not been charged" in row.body
    finally:
        await engine.dispose()


# ── Resolve: retry ─────────────────────────────────────────────────────────


async def test_retry_makes_the_submission_claimable_by_the_reconciler_again():
    """Asserted against the reconciler's own claim, not a guess about it.

    ``count_stale_dispatches`` is the same predicate ``reconcile_stale_dispatches``
    claims with, so this cannot pass while the real sweep still ignores the row.
    """
    from app.workers.reconcile_dispatch import count_stale_dispatches

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, submission = await _make_goal(
                db,
                verification_details=_blocked_details(),
                dispatch_attempts=settings.verification_dispatch_max_attempts,
            )
            # Precondition: the reconciler has given up on this row.
            assert await count_stale_dispatches(db) == 0

            result = await bg.resolve_blocked_goal(db, goal.id, bg.ACTION_RETRY)

            assert result.reclaimable_by_reconciler is True
            assert await count_stale_dispatches(db) == 1

            row = (
                await db.execute(
                    text(
                        "SELECT dispatch_attempts, dispatched_at, verification_details "
                        "FROM proof_submissions WHERE id = :id"
                    ),
                    {"id": submission.id},
                )
            ).one()
            assert row.dispatch_attempts == 0
            assert row.dispatched_at is None
            assert row.verification_details["needs_operator_review"] is False
            assert row.verification_details["operator_action"] == bg.ACTION_RETRY
    finally:
        await engine.dispose()


async def test_retry_leaves_the_goal_protected_from_the_deadline_charge():
    """Retry must not open a charge window while the re-verification is pending.

    The goal is past its deadline and the sweep runs every 60s, so if the
    inconclusive marker were cleared here the pledge would be collected for our
    own outage before the retry we just scheduled ever ran.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, _ = await _make_goal(db, verification_details=_blocked_details())

            await bg.resolve_blocked_goal(db, goal.id, bg.ACTION_RETRY)

            assert await vr.goal_verification_is_blocked(db, goal.id) is True

            with charge_boundary() as charges:
                from app.workers.deadline import _process_expired_goal

                await _process_expired_goal(
                    db, goal.id, goal.user_id, datetime.now(timezone.utc)
                )
            assert_no_charge(charges)

            status = (
                await db.execute(
                    text("SELECT status FROM goals WHERE id = :id"), {"id": goal.id}
                )
            ).scalar_one()
            assert status == "active"
    finally:
        await engine.dispose()


# ── Resolve: refusals ──────────────────────────────────────────────────────


@pytest.mark.parametrize("action", sorted(bg.ACTIONS))
async def test_resolve_refuses_a_goal_that_is_not_blocked(action):
    """Safe against a live database: it acts only on a goal that is really stuck."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, healthy, _ = await _make_goal(
                db,
                verification_details={"checked": True},
                deadline_offset=timedelta(days=3),
            )

            with charge_boundary() as charges:
                with pytest.raises(bg.GoalNotBlocked):
                    await bg.resolve_blocked_goal(db, healthy.id, action)
            assert_no_charge(charges)

            status = (
                await db.execute(
                    text("SELECT status FROM goals WHERE id = :id"), {"id": healthy.id}
                )
            ).scalar_one()
            assert status == "active"
    finally:
        await engine.dispose()


async def test_resolve_refuses_an_unknown_goal_id():
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            with pytest.raises(bg.GoalNotFound):
                await bg.resolve_blocked_goal(db, uuid.uuid4(), bg.ACTION_RETRY)
    finally:
        await engine.dispose()


async def test_resolve_rejects_an_unknown_action():
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, _ = await _make_goal(db, verification_details=_blocked_details())
            with pytest.raises(ValueError):
                await bg.resolve_blocked_goal(db, goal.id, "charge_anyway")
    finally:
        await engine.dispose()


async def test_resolve_touches_exactly_one_goal():
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, target, _ = await _make_goal(db, verification_details=_blocked_details())
            _, bystander, _ = await _make_goal(
                db, verification_details=_blocked_details()
            )

            with charge_boundary():
                await bg.resolve_blocked_goal(db, target.id, bg.ACTION_GIVE_UP)

            remaining = {b.goal_id for b in await bg.list_blocked_goals(db)}
            assert target.id not in remaining
            assert bystander.id in remaining
    finally:
        await engine.dispose()


# ── The endpoint ───────────────────────────────────────────────────────────


async def _ordinary_user_token(db: AsyncSession) -> str:
    user = User(
        email=f"ordinary-{uuid.uuid4()}@example.com",
        display_name="Ordinary User",
        auth_provider="google",
        auth_provider_id=str(uuid.uuid4()),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return create_access_token(str(user.id), user.auth_session_id)


async def test_endpoint_rejects_an_ordinary_authenticated_user(monkeypatch):
    """A valid login is not operator authorization. This endpoint shows other
    people's pledges and email addresses."""
    monkeypatch.setattr(settings, "operator_api_token", OPERATOR_TOKEN)
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            await _make_goal(db, verification_details=_blocked_details())
            token = await _ordinary_user_token(db)

        async with make_client() as client:
            resp = await client.get(
                "/api/operator/blocked-goals",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403
        assert "blocked_goals" not in resp.text
    finally:
        await engine.dispose()


async def test_endpoint_rejects_anonymous_and_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "operator_api_token", OPERATOR_TOKEN)
    async with make_client() as client:
        assert (await client.get("/api/operator/blocked-goals")).status_code == 403
        wrong = await client.get(
            "/api/operator/blocked-goals",
            headers={OPERATOR_TOKEN_HEADER: "x" * len(OPERATOR_TOKEN)},
        )
        assert wrong.status_code == 403


async def test_endpoint_is_absent_unless_a_strong_token_is_configured(monkeypatch):
    """Off by default, and a placeholder token cannot switch it on.

    404 rather than 403 so an unconfigured deployment does not advertise that an
    operator surface exists.
    """
    for value in ("", "ops", "short-token"):
        monkeypatch.setattr(settings, "operator_api_token", value)
        async with make_client() as client:
            resp = await client.get(
                "/api/operator/blocked-goals",
                headers={OPERATOR_TOKEN_HEADER: value or "anything"},
            )
        assert resp.status_code == 404, value


async def test_endpoint_returns_the_blocked_list_with_no_secrets(monkeypatch):
    monkeypatch.setattr(settings, "operator_api_token", OPERATOR_TOKEN)
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            user, goal, _ = await _make_goal(
                db,
                verification_details=_blocked_details(
                    extra={"inconclusive_detail": "gh_secret_in_detail"}
                ),
                proof_data={"github_token": "gh_secret_in_proof_data"},
                dispatch_criteria={"github_token": "gh_secret_in_criteria"},
            )

        async with make_client() as client:
            resp = await client.get(
                "/api/operator/blocked-goals",
                headers={OPERATOR_TOKEN_HEADER: OPERATOR_TOKEN},
            )

        assert resp.status_code == 200
        body = resp.json()
        entry = next(e for e in body["blocked_goals"] if e["goal_id"] == str(goal.id))
        assert entry["user_email"] == user.email
        assert entry["inconclusive_reason"] == vr.REASON_UPSTREAM_UNAVAILABLE
        assert entry["needs_operator_review"] is True
        assert entry["pledge_amount"] == 5000

        for secret in (
            "gh_secret_in_proof_data",
            "gh_secret_in_criteria",
            "gh_secret_in_detail",
            "github_token",
            "password_hash",
            "auth_session_id",
        ):
            assert secret not in resp.text
    finally:
        await engine.dispose()


async def test_endpoint_is_read_only(monkeypatch):
    """No write verb exists on the operator surface: resolving is CLI-only."""
    monkeypatch.setattr(settings, "operator_api_token", OPERATOR_TOKEN)
    async with make_client() as client:
        for method in ("post", "put", "patch", "delete"):
            resp = await getattr(client, method)(
                "/api/operator/blocked-goals",
                headers={OPERATOR_TOKEN_HEADER: OPERATOR_TOKEN},
            )
            assert resp.status_code == 405, method


# ── The CLI ────────────────────────────────────────────────────────────────
# The operator's actual interface, so it is exercised as an operator would: the
# real click commands, against a real database. Invoked in a worker thread
# because the commands own their event loop (``asyncio.run``), which cannot be
# entered from inside the one pytest-asyncio is already running.


async def _run_cli(*args):
    from click.testing import CliRunner

    from cli.main import cli as cli_root

    return await asyncio.to_thread(CliRunner().invoke, cli_root, list(args))


async def test_cli_list_shows_the_blocked_goal_and_no_secrets():
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            user, goal, _ = await _make_goal(
                db,
                verification_details=_blocked_details(
                    vr.REASON_SANDBOX_INFRASTRUCTURE,
                    extra={"inconclusive_detail": "gh_secret_in_detail"},
                ),
                proof_data={"github_token": "gh_secret_in_proof_data"},
                dispatch_criteria={"github_token": "gh_secret_in_criteria"},
            )

        result = await _run_cli("blocked-goals", "list")
        assert result.exit_code == 0, result.output
        assert str(goal.id) in result.output
        assert user.email in result.output
        assert "sandbox_infrastructure" in result.output
        assert "$50.00 USD" in result.output
        assert "awaiting operator review" in result.output
        for secret in (
            "gh_secret_in_proof_data",
            "gh_secret_in_criteria",
            "gh_secret_in_detail",
        ):
            assert secret not in result.output
    finally:
        await engine.dispose()


async def test_cli_list_is_read_only():
    """``list`` is safe to run against production: it changes nothing."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, submission = await _make_goal(
                db, verification_details=_blocked_details()
            )
            before = (
                await db.execute(
                    text(
                        "SELECT g.status, s.verification_status, s.dispatch_attempts, "
                        "s.verification_details FROM goals g "
                        "JOIN proof_submissions s ON s.goal_id = g.id "
                        "WHERE g.id = :id"
                    ),
                    {"id": goal.id},
                )
            ).one()

        assert (await _run_cli("blocked-goals", "list")).exit_code == 0

        async with factory() as db:
            after = (
                await db.execute(
                    text(
                        "SELECT g.status, s.verification_status, s.dispatch_attempts, "
                        "s.verification_details FROM goals g "
                        "JOIN proof_submissions s ON s.goal_id = g.id "
                        "WHERE g.id = :id"
                    ),
                    {"id": goal.id},
                )
            ).one()
            assert tuple(after) == tuple(before)
            assert submission is not None
    finally:
        await engine.dispose()


async def test_cli_resolve_requires_exactly_one_outcome():
    """No default and no guessing: the operator states the outcome."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, _ = await _make_goal(db, verification_details=_blocked_details())

        for args in ([], ["--retry", "--give-up"]):
            result = await _run_cli("blocked-goals", "resolve", str(goal.id), *args)
            assert result.exit_code == 2, result.output
            assert "exactly one outcome" in result.output

        # And the goal is untouched by the refusal.
        async with factory() as db:
            assert await vr.goal_verification_is_blocked(db, goal.id) is True
    finally:
        await engine.dispose()


async def test_cli_resolve_reports_what_it_changed_and_refuses_a_repeat():
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, _ = await _make_goal(db, verification_details=_blocked_details())

        with charge_boundary() as charges:
            done = await _run_cli("blocked-goals", "resolve", str(goal.id), "--give-up")
            assert done.exit_code == 0, done.output
            assert "active -> cancelled" in done.output
            assert "No charge was made" in done.output

            # Second run: already resolved, so it must refuse rather than
            # re-apply. Non-zero exit so a script notices.
            again = await _run_cli(
                "blocked-goals", "resolve", str(goal.id), "--give-up"
            )
            assert again.exit_code == 1
            assert "Refused" in again.output
        assert_no_charge(charges)
    finally:
        await engine.dispose()


async def test_cli_resolve_rejects_a_malformed_goal_id():
    result = await _run_cli("blocked-goals", "resolve", "not-a-uuid", "--retry")
    assert result.exit_code == 2
    assert "Not a valid goal id" in result.output


# ── The recurring-series bug ───────────────────────────────────────────────


async def test_blocked_recurring_goal_does_not_end_the_series():
    """A verification fault of ours must not cancel a standing commitment.

    ``_process_expired_goal`` returned at the blocked check, before
    ``_create_next_recurring_instance``: one GitHub outage on a daily goal and no
    further instance was ever created.
    """
    from app.workers.deadline import _process_expired_goal

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, _ = await _make_goal(
                db,
                verification_details=_blocked_details(),
                recurrence="daily",
            )

            with charge_boundary() as charges:
                await _process_expired_goal(
                    db, goal.id, goal.user_id, datetime.now(timezone.utc)
                )
            assert_no_charge(charges)

            successors = (
                await db.execute(
                    text(
                        "SELECT id, deadline, status, recurrence FROM goals "
                        "WHERE user_id = :uid AND id != :id"
                    ),
                    {"uid": goal.user_id, "id": goal.id},
                )
            ).all()
            assert len(successors) == 1
            successor = successors[0]
            assert successor.status == "active"
            assert successor.recurrence == "daily"
            assert successor.deadline == goal.deadline + timedelta(days=1)

            # The blocked goal itself is untouched: still active, still skipped,
            # still waiting for an operator.
            status = (
                await db.execute(
                    text("SELECT status FROM goals WHERE id = :id"), {"id": goal.id}
                )
            ).scalar_one()
            assert status == "active"
    finally:
        await engine.dispose()


async def test_blocked_recurring_goal_spawns_one_successor_not_one_per_sweep():
    """The sweep re-selects a blocked goal every 60 seconds, forever.

    Continuing the series from that branch is only safe because
    ``_create_next_recurring_instance`` is idempotent; without the guard this is
    a new goal per minute for as long as the goal stays blocked.
    """
    from app.workers.deadline import _process_expired_goal

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, _ = await _make_goal(
                db,
                verification_details=_blocked_details(),
                recurrence="weekly",
            )

            with charge_boundary():
                for _ in range(3):
                    await _process_expired_goal(
                        db, goal.id, goal.user_id, datetime.now(timezone.utc)
                    )

            count = (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM goals WHERE user_id = :uid AND id != :id"
                    ),
                    {"uid": goal.user_id, "id": goal.id},
                )
            ).scalar_one()
            assert count == 1
    finally:
        await engine.dispose()


async def test_non_recurring_blocked_goal_spawns_nothing():
    from app.workers.deadline import _process_expired_goal

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, _ = await _make_goal(
                db, verification_details=_blocked_details(), recurrence="none"
            )

            with charge_boundary():
                await _process_expired_goal(
                    db, goal.id, goal.user_id, datetime.now(timezone.utc)
                )

            count = (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM goals WHERE user_id = :uid AND id != :id"
                    ),
                    {"uid": goal.user_id, "id": goal.id},
                )
            ).scalar_one()
            assert count == 0
    finally:
        await engine.dispose()


async def test_normal_failure_path_still_creates_one_successor():
    """The idempotency guard must not break the path it was not written for."""
    from app.workers.deadline import _process_expired_goal

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            _, goal, _ = await _make_goal(
                db,
                with_submission=False,
                recurrence="daily",
            )

            with charge_boundary() as charges:
                await _process_expired_goal(
                    db, goal.id, goal.user_id, datetime.now(timezone.utc)
                )
            # Not blocked: this user really did miss the deadline, so the charge
            # is correct here.
            assert_charged_once(charges)

            status = (
                await db.execute(
                    text("SELECT status FROM goals WHERE id = :id"), {"id": goal.id}
                )
            ).scalar_one()
            assert status == "failed"

            count = (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM goals WHERE user_id = :uid AND id != :id"
                    ),
                    {"uid": goal.user_id, "id": goal.id},
                )
            ).scalar_one()
            assert count == 1
    finally:
        await engine.dispose()
