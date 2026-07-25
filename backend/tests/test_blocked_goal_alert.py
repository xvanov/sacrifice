"""Something has to actually run the blocked-goals reader.

``app/services/blocked_goals.py`` and its two entry points (``sacrifice
blocked-goals list``, ``GET /api/operator/blocked-goals``) are both pull-only.
Nothing invoked either, so a goal stranded on an inconclusive verification stayed
exactly as silent as it had been before the reader existed: skipped by every
deadline sweep, never charged, never resolved, with the owner holding a
notification that says a human is looking into it.

These pin the scheduled half:

* the beat schedule really contains the task, and points at a name Celery can
  resolve — the deadline sweep shipped broken for exactly this reason once
  (``app/core/celery_app.py`` documents the unregistered-task bug);
* a run with goals past their retry budget reports them, at a severity that
  survives an INFO/WARNING filter, and names the pledge and the resolving
  command;
* a run with nothing to report still logs, so a dead sweep is distinguishable
  from a quiet one;
* the alert is read-only and cannot charge — it is a monitor for pledges we have
  decided not to collect, and the one thing it must never do is decide otherwise.

The goal fixtures mirror ``tests/test_blocked_goals_operator.py``: these states
(a past deadline, a spent attempt counter, an hours-old submission) are ones the
API refuses to create.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.goal import Goal, GoalCriteria
from app.models.proof import ProofSubmission
from app.models.user import User
from app.services import verification_result as vr
from app.workers.blocked_goal_alert import (
    RESOLVE_HINT,
    alert_on_blocked_goals,
)


def _session_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    return engine, async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


def _blocked_details(*, needs_review: bool, blocked_ago=timedelta(hours=30)) -> dict:
    return {
        "outcome": vr.INCONCLUSIVE,
        "inconclusive_reason": vr.REASON_UPSTREAM_UNAVAILABLE,
        "inconclusive_retryable": True,
        "inconclusive_at": (datetime.now(timezone.utc) - blocked_ago).isoformat(),
        "needs_operator_review": needs_review,
        "user_message": "We could not complete the check for this proof.",
    }


async def _make_blocked_goal(
    db: AsyncSession, *, needs_review: bool, pledge_amount: int = 5000
) -> Goal:
    user = User(
        email=f"alert-{uuid.uuid4()}@example.com",
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
        pledge_amount=pledge_amount,
        currency="usd",
        deadline=datetime.now(timezone.utc) - timedelta(hours=2),
        timezone="UTC",
        recurrence="none",
        status="active",
        charity_id="acct_charity123",
    )
    db.add(goal)
    await db.flush()
    db.add(
        GoalCriteria(
            goal_id=goal.id,
            criteria_type="github_repo",
            criteria_data={
                "repo_owner": "octocat",
                "repo_name": "hello",
                "min_commits": 3,
            },
        )
    )
    db.add(
        ProofSubmission(
            goal_id=goal.id,
            submitted_at=datetime.now(timezone.utc) - timedelta(hours=30),
            proof_data={"repo_url": "https://github.com/octocat/hello"},
            verification_status="pending",
            verification_details=_blocked_details(needs_review=needs_review),
            dispatched_at=datetime.now(timezone.utc) - timedelta(hours=30),
            dispatch_attempts=4 if needs_review else 1,
        )
    )
    await db.commit()
    await db.refresh(goal)
    return goal


# ── It is actually scheduled ───────────────────────────────────────────────


def test_the_alert_is_on_the_beat_schedule():
    """The whole point of this change: it runs without anyone asking.

    A reader nobody invokes is what was already there.
    """
    from app.core.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["alert-blocked-goals"]

    assert entry["task"] == (
        "app.workers.blocked_goal_alert.alert_on_blocked_goals_task"
    )
    assert entry["schedule"] > 0


def test_the_scheduled_task_name_resolves_to_a_registered_task():
    """A beat entry naming a task Celery cannot resolve dispatches to nothing.

    ``check-deadlines`` shipped that way: beat logged "unregistered task" every
    60 seconds and no goal was ever auto-failed. The bare coroutine's name is not
    the registered one, so the schedule must point at the ``@task``-decorated
    wrapper.
    """
    from app.core.celery_app import celery_app

    # Importing the module is what registers the task; the worker does this via
    # ``include`` (get_celery_include_modules enumerates app.workers.*).
    import app.workers.blocked_goal_alert  # noqa: F401

    name = celery_app.conf.beat_schedule["alert-blocked-goals"]["task"]
    assert name in celery_app.tasks, (
        f"{name} is not registered; beat would dispatch this schedule to nothing"
    )


# ── What it reports ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_goal_past_its_retry_budget_is_reported(caplog):
    """One ERROR line, naming the goal, the pledge and how to resolve it.

    ERROR rather than WARNING because every entry is an uncollected pledge and a
    user promised a human; the severity is what makes it findable in an aggregator
    that drops INFO and WARNING.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal = await _make_blocked_goal(db, needs_review=True, pledge_amount=7500)

        with caplog.at_level(logging.INFO, logger="app.workers.blocked_goal_alert"):
            async with factory() as db:
                summary = await alert_on_blocked_goals(db)
    finally:
        await engine.dispose()

    assert summary["needs_review"] >= 1
    assert str(goal.id) in summary["goal_ids"]

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "a goal nothing will move again must produce an ERROR record"
    message = errors[0].getMessage()
    assert str(goal.id) in message, "the alert must name the goal to be actionable"
    assert "75.00" in message, "the pledge at stake is the reason to act"
    assert RESOLVE_HINT in message, (
        "a reader who has never heard of this subsystem needs the command"
    )


@pytest.mark.asyncio
async def test_a_goal_still_inside_its_retry_budget_is_not_alerted(caplog):
    """The documented gap, pinned so it stays deliberate.

    These goals are being retried automatically and usually resolve themselves;
    alerting on them is how an operator learns to ignore the alert. They remain
    visible to ``sacrifice blocked-goals list`` with no flag.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal = await _make_blocked_goal(db, needs_review=False)

        with caplog.at_level(logging.INFO, logger="app.workers.blocked_goal_alert"):
            async with factory() as db:
                summary = await alert_on_blocked_goals(db)
    finally:
        await engine.dispose()

    assert str(goal.id) not in summary["goal_ids"]


@pytest.mark.asyncio
async def test_a_clean_run_still_logs(caplog):
    """Silence from a crashed sweep looks like silence from a healthy one.

    The heartbeat is what tells an operator the check is running at all, which is
    the failure this whole module exists to fix — one layer up.
    """
    engine, factory = _session_factory()
    try:
        with caplog.at_level(logging.INFO, logger="app.workers.blocked_goal_alert"):
            async with factory() as db:
                summary = await alert_on_blocked_goals(db)
    finally:
        await engine.dispose()

    assert summary == {
        "needs_review": 0,
        "goal_ids": [],
        "pledge_total": 0,
        "webhook_delivered": False,
    }
    assert any(
        r.levelno == logging.INFO and "No blocked goals" in r.getMessage()
        for r in caplog.records
    )
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "a quiet run must not raise the severity, or the alert becomes noise"
    )


# ── Reaching a person ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_configured_webhook_receives_the_alert(monkeypatch):
    """The log line does not reach anybody; this is what does.

    No aggregator is deployed in this repo, so an ERROR in the worker log reaches
    whoever runs ``docker compose logs worker`` — nobody, on a quiet week. A
    webhook turns the same event into a notification on a phone.
    """
    posted = {}

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            posted["url"] = url
            posted["json"] = json
            return _Resp()

    monkeypatch.setattr(
        "app.workers.blocked_goal_alert.settings.blocked_goal_alert_webhook_url",
        "https://hooks.example.com/T/B/XXX",
    )
    monkeypatch.setattr("app.workers.blocked_goal_alert.httpx.AsyncClient", _Client)

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal = await _make_blocked_goal(db, needs_review=True, pledge_amount=7500)
        async with factory() as db:
            summary = await alert_on_blocked_goals(db)
    finally:
        await engine.dispose()

    assert summary["webhook_delivered"] is True
    assert posted["url"] == "https://hooks.example.com/T/B/XXX"
    assert "75.00" in posted["json"]["text"]
    assert RESOLVE_HINT in posted["json"]["text"]
    assert posted["json"]["goals"][0]["goal_id"] == str(goal.id)


@pytest.mark.asyncio
async def test_the_webhook_payload_carries_no_proof_or_criteria(monkeypatch):
    """``proof_data`` and ``dispatch_criteria`` can hold an encrypted GitHub PAT.

    ``BlockedGoal`` is the exposure allowlist precisely so a new consumer cannot
    leak them by forgetting to filter — and a webhook posts to a third party, which
    is the worst place to find out that guarantee was only about the API.
    """
    import json as _json

    posted = {}

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            posted["json"] = json
            return _Resp()

    monkeypatch.setattr(
        "app.workers.blocked_goal_alert.settings.blocked_goal_alert_webhook_url",
        "https://hooks.example.com/T/B/XXX",
    )
    monkeypatch.setattr("app.workers.blocked_goal_alert.httpx.AsyncClient", _Client)

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            await _make_blocked_goal(db, needs_review=True)
        async with factory() as db:
            await alert_on_blocked_goals(db)
    finally:
        await engine.dispose()

    body = _json.dumps(posted["json"])
    for forbidden in ("proof_data", "dispatch_criteria", "repo_url", "github_token"):
        assert forbidden not in body, f"{forbidden} must not be posted off-box"


@pytest.mark.asyncio
async def test_an_unreachable_webhook_does_not_fail_the_run(monkeypatch, caplog):
    """A Slack outage must not turn a monitoring run into a retrying task.

    The log line is already written by then, so the alert is never *worse* than
    log-only — and a task that raised here would re-run and double-notify.
    """

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "app.workers.blocked_goal_alert.settings.blocked_goal_alert_webhook_url",
        "https://hooks.example.com/T/B/XXX",
    )
    monkeypatch.setattr("app.workers.blocked_goal_alert.httpx.AsyncClient", _Client)

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            await _make_blocked_goal(db, needs_review=True)
        with caplog.at_level(logging.INFO, logger="app.workers.blocked_goal_alert"):
            async with factory() as db:
                summary = await alert_on_blocked_goals(db)
    finally:
        await engine.dispose()

    assert summary["needs_review"] >= 1
    assert summary["webhook_delivered"] is False
    assert any(r.levelno == logging.ERROR for r in caplog.records), (
        "the log line is the record and must survive a failed delivery"
    )


@pytest.mark.asyncio
async def test_no_webhook_configured_is_log_only(monkeypatch):
    """The default. Every existing deployment keeps working with no config."""
    monkeypatch.setattr(
        "app.workers.blocked_goal_alert.settings.blocked_goal_alert_webhook_url", ""
    )

    def _explode(*a, **k):
        raise AssertionError("no HTTP call may be made when no webhook is set")

    monkeypatch.setattr("app.workers.blocked_goal_alert.httpx.AsyncClient", _explode)

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            await _make_blocked_goal(db, needs_review=True)
        async with factory() as db:
            summary = await alert_on_blocked_goals(db)
    finally:
        await engine.dispose()

    assert summary["webhook_delivered"] is False


@pytest.mark.asyncio
async def test_a_quiet_run_notifies_nobody(monkeypatch):
    """No stranded goals, no notification. An alert that fires on nothing is noise."""

    def _explode(*a, **k):
        raise AssertionError("a run with nothing to report must not notify")

    monkeypatch.setattr(
        "app.workers.blocked_goal_alert.settings.blocked_goal_alert_webhook_url",
        "https://hooks.example.com/T/B/XXX",
    )
    monkeypatch.setattr("app.workers.blocked_goal_alert.httpx.AsyncClient", _explode)

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            summary = await alert_on_blocked_goals(db)
    finally:
        await engine.dispose()

    assert summary["needs_review"] == 0


@pytest.mark.asyncio
async def test_the_alert_changes_nothing_and_cannot_charge(caplog):
    """A monitor for pledges we decided not to collect must not collect them.

    Asserted two ways: the goal and submission rows are identical after a run,
    and the module's source names no charge path — ``process_charge_for_goal`` is
    what ``app/services/verification_result.py`` calls on ``failed``, and an
    alerting sweep has no business reaching it.
    """
    import inspect

    from app.workers import blocked_goal_alert

    assert "process_charge_for_goal" not in inspect.getsource(blocked_goal_alert)

    _STATE_SQL = text(
        "SELECT g.status, s.verification_status, s.dispatch_attempts, "
        "s.verification_details FROM goals g "
        "JOIN proof_submissions s ON s.goal_id = g.id WHERE g.id = :id"
    )

    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal = await _make_blocked_goal(db, needs_review=True)

        async with factory() as db:
            before = (await db.execute(_STATE_SQL, {"id": goal.id})).one()
            await alert_on_blocked_goals(db)
            assert (await db.execute(_STATE_SQL, {"id": goal.id})).one() == before
    finally:
        await engine.dispose()
