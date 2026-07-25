"""The pre-anchor population: which goals get an anchor, and which must not.

``min_commits`` is now counted from a goal's ``commits_since`` anchor. The goals
written before that field existed had to be decided one way or the other, and
migration ``b8e4c1a70d92`` is that decision:

* **``draft`` / ``awaiting_goal_type`` are anchored** to their own ``created_at``.
  Nothing is at stake — the deadline sweep does not enforce these statuses — and a
  draft can sit for months before being activated, so leaving it unanchored keeps
  the vacuous pass alive indefinitely rather than for a bounded window.
* **``active`` / ``pending_review`` are left alone.** They are live commitments
  made under whole-history counting. Narrowing what counts turns a passing goal
  into ``failed``, and ``failed`` charges a real card — doing that to somebody
  mid-goal is the wrongful charge that the whole anchor design was built to avoid.
  Their exposure ends at their own deadline.

This is the most reversible-by-accident part of the change: someone widening the
status list "for consistency" would be silently re-judging live pledges. So the
split is asserted here against the migration's own SQL rather than trusted to the
docstring.

The statements are imported from the version file by path, because that file is
the authority — a test that re-spelled the SQL would pass while the migration
did something else.
"""

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.goal import Goal, GoalCriteria
from app.models.user import User

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "b8e4c1a70d92_anchor_commit_counts_on_unstarted_github_goals.py"
)


def _load_migration():
    """Import the version file directly; alembic's directory is not a package."""
    spec = importlib.util.spec_from_file_location("_anchor_backfill", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _session_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    return engine, async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


async def _goal(
    db: AsyncSession, *, status: str, criteria: dict, created_days_ago: int
):
    user = User(
        email=f"backfill-{uuid.uuid4()}@example.com",
        display_name="Backfill",
        auth_provider="google",
        auth_provider_id=str(uuid.uuid4()),
    )
    db.add(user)
    await db.flush()

    created = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
    goal = Goal(
        user_id=user.id,
        title="Push three commits",
        goal_type="github_repo",
        pledge_amount=2500,
        currency="usd",
        deadline=datetime.now(timezone.utc) + timedelta(days=10),
        timezone="UTC",
        recurrence="none",
        status=status,
        created_at=created,
    )
    db.add(goal)
    await db.flush()
    db.add(
        GoalCriteria(
            goal_id=goal.id, criteria_type="github_repo", criteria_data=criteria
        )
    )
    await db.commit()
    await db.refresh(goal)
    return goal


async def _stored(db: AsyncSession, goal_id) -> dict:
    row = await db.execute(
        text("SELECT criteria_data FROM goal_criteria WHERE goal_id = :id"),
        {"id": goal_id},
    )
    return row.scalar_one()


async def _run_backfill(db: AsyncSession) -> int:
    migration = _load_migration()
    result = await db.execute(
        migration._BACKFILL,
        {
            "field": migration._CRITERIA_FIELD,
            "statuses": list(migration._NOT_YET_ENFORCEABLE),
        },
    )
    await db.commit()
    return result.rowcount


_UNANCHORED = {"repo_owner": "octocat", "repo_name": "hello", "min_commits": 3}


@pytest.mark.asyncio
async def test_an_unstarted_goal_is_anchored_to_its_own_creation_time():
    """The anchor a draft gets is the one ``create_goal`` would have stamped.

    Not "now": a draft created three weeks ago whose owner has been committing
    since would lose all of that work, and losing work is what produces a charge.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal = await _goal(
                db, status="draft", criteria=_UNANCHORED, created_days_ago=21
            )
            await _run_backfill(db)
            stored = await _stored(db, goal.id)
    finally:
        await engine.dispose()

    anchor = datetime.fromisoformat(stored["commits_since"].replace("Z", "+00:00"))
    assert (
        abs((anchor - goal.created_at.replace(tzinfo=timezone.utc)).total_seconds())
        <= 1
    )
    assert stored["min_commits"] == 3, "the backfill merges, it does not replace"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "pending_review"])
async def test_a_live_goal_is_never_retroactively_anchored(status):
    """The charge-safety line, and the assertion most worth keeping.

    An enforceable goal's owner committed under whole-history counting. Anchoring
    it now can only reduce the count, and a reduced count is a ``failed``
    verification, which is a real Stripe charge against somebody who was never
    told the rule changed.
    """
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal = await _goal(
                db, status=status, criteria=_UNANCHORED, created_days_ago=5
            )
            await _run_backfill(db)
            stored = await _stored(db, goal.id)
    finally:
        await engine.dispose()

    assert "commits_since" not in stored


@pytest.mark.asyncio
async def test_an_existing_anchor_is_left_exactly_as_it_was():
    """Idempotent, so a re-run (or a re-stamped deploy) cannot move a window."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            goal = await _goal(
                db,
                status="draft",
                criteria={**_UNANCHORED, "commits_since": "2026-01-01T00:00:00Z"},
                created_days_ago=2,
            )
            await _run_backfill(db)
            stored = await _stored(db, goal.id)
    finally:
        await engine.dispose()

    assert stored["commits_since"] == "2026-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_other_goal_types_are_untouched():
    """Scoped to github_repo: no other type has a commit count to anchor."""
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            user = User(
                email=f"backfill-{uuid.uuid4()}@example.com",
                display_name="Backfill",
                auth_provider="google",
                auth_provider_id=str(uuid.uuid4()),
            )
            db.add(user)
            await db.flush()
            goal = Goal(
                user_id=user.id,
                title="Post a video",
                goal_type="youtube_video",
                pledge_amount=2500,
                currency="usd",
                deadline=datetime.now(timezone.utc) + timedelta(days=10),
                timezone="UTC",
                recurrence="none",
                status="draft",
            )
            db.add(goal)
            await db.flush()
            db.add(
                GoalCriteria(
                    goal_id=goal.id,
                    criteria_type="youtube",
                    criteria_data={"min_duration_seconds": 300},
                )
            )
            await db.commit()

            await _run_backfill(db)
            stored = await _stored(db, goal.id)
    finally:
        await engine.dispose()

    assert stored == {"min_duration_seconds": 300}


def test_the_backfill_is_scoped_to_statuses_that_cannot_be_charged():
    """Guard the list itself, not just its effect.

    ``draft`` and ``awaiting_goal_type`` are exactly the statuses
    ``app/workers/deadline.py`` does not enforce. Adding an enforceable one here
    would re-judge live pledges, which no migration should ever do quietly.
    """
    from app.workers.deadline import ENFORCEABLE_STATUSES

    migration = _load_migration()

    assert set(migration._NOT_YET_ENFORCEABLE).isdisjoint(set(ENFORCEABLE_STATUSES)), (
        "the backfill must never touch a status the deadline sweep can charge"
    )
