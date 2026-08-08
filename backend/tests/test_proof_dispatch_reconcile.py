"""Tests for verification-dispatch bookkeeping and the reconciler beat task.

A proof whose Celery task never reached the worker used to sit ``pending``
forever while ``check_deadlines`` charged the pledge anyway. The route now
records whether the enqueue succeeded, and a beat task re-dispatches stale
submissions — bounded, atomically claimed, and unable to cause a second charge.

Covers:
- The route records dispatched_at / dispatch_attempts / dispatch_criteria
- A failed dispatch leaves the row recoverable and doesn't promise verification
- The reconciler re-dispatches a stale pending submission with the SNAPSHOT
  criteria (not re-derived ones, which could turn a passing proof into a charge)
- It ignores fresh, terminal, and attempt-capped rows
- Overlapping sweeps claim each row exactly once (FOR UPDATE SKIP LOCKED)
- A duplicated verification landing on "failed" cannot double-charge
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.goal_types import registry as goal_type_registry
from app.main import app
from app.models.goal import Goal
from app.models.proof import ProofSubmission
from app.workers.reconcile_dispatch import (
    count_stale_dispatches,
    reconcile_stale_dispatches,
)

pytestmark = pytest.mark.asyncio

YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
DELAY_PATH = "app.workers.youtube.run_youtube_verification_task.delay"


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _engine_and_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    return engine, async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


async def _auth(
    client, email="test@example.com", sub="test-sub-123", token="valid-token"
):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {
            "email": email,
            "name": "Test User",
            "sub": sub,
            "picture": None,
        }
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def _create_active_goal(client, token, goal_type="youtube_video", criteria=None):
    if criteria is None:
        criteria = {"min_duration_seconds": 120, "video_description": "A walkthrough"}
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Reconcile goal",
            "description": "dispatch bookkeeping",
            "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "pledge_amount": 5000,
            "goal_type": goal_type,
            "criteria": criteria,
            "charity_id": "acct_charity123",
        },
    )
    assert resp.status_code in (200, 201), resp.text
    goal_id = resp.json()["id"]
    resp = await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    assert resp.status_code == 200, resp.text
    return goal_id


async def _submit_json_proof(client, token, goal_id, dispatch=None):
    """Submit a JSON proof. ``dispatch`` replaces the Celery enqueue."""
    target = DELAY_PATH
    if dispatch is None:
        with patch(target):
            return await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={"youtube_url": YT_URL},
            )
    with patch(target, dispatch):
        return await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": YT_URL},
        )


async def _backdate(db, submission_id, minutes):
    """Age a submission so the sweep considers it stale."""
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    sid = uuid.UUID(str(submission_id))
    await db.execute(
        text("UPDATE proof_submissions SET submitted_at = :w WHERE id = :i"),
        {"w": when, "i": sid},
    )
    # Age an existing dispatch too; a NULL one must stay NULL.
    await db.execute(
        text(
            "UPDATE proof_submissions SET dispatched_at = :w "
            "WHERE id = :i AND dispatched_at IS NOT NULL"
        ),
        {"w": when, "i": sid},
    )
    await db.commit()


def _stale_minutes():
    return settings.verification_dispatch_stale_minutes + 5


# ── route-side bookkeeping ────────────────────────────────────────────────


async def test_successful_dispatch_records_bookkeeping():
    """A dispatched proof records the timestamp, the attempt, and the criteria.

    Fails before this change: none of the three columns existed.
    """
    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id)
            assert resp.status_code == 202, resp.text

            async with factory() as db:
                sub = (
                    await db.execute(
                        select(ProofSubmission).where(
                            ProofSubmission.id
                            == uuid.UUID(resp.json()["submission_id"])
                        )
                    )
                ).scalar_one()
                assert sub.dispatched_at is not None
                assert sub.dispatch_attempts == 1
                # The snapshot is what the verifier was actually given.
                assert sub.dispatch_criteria["min_duration_seconds"] == 120
    finally:
        await engine.dispose()


async def test_failed_dispatch_leaves_row_recoverable():
    """A broker outage still returns 202, but the row records the failure.

    ``dispatch_attempts >= 1`` with ``dispatched_at IS NULL`` is the durable,
    queryable trace the reconciler selects on — previously the failure existed
    only as a log line and the proof was unrecoverable.
    """

    def _boom(**kwargs):
        raise RuntimeError("broker unavailable")

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id, dispatch=_boom)
            assert resp.status_code == 202, resp.text
            assert resp.json()["verification_status"] == "pending"

            async with factory() as db:
                sub = (
                    await db.execute(
                        select(ProofSubmission).where(
                            ProofSubmission.id
                            == uuid.UUID(resp.json()["submission_id"])
                        )
                    )
                ).scalar_one()
                assert sub.dispatched_at is None
                assert sub.dispatch_attempts == 1

            # And the user is not told verification is underway when it isn't.
            notifs = await client.get(
                "/api/notifications", headers={"Authorization": f"Bearer {token}"}
            )
            bodies = [n["body"] for n in notifs.json()]
            proof_bodies = [b for b in bodies if "proof submission" in b]
            assert proof_bodies, bodies
            assert not any("is being verified" in b for b in proof_bodies)
    finally:
        await engine.dispose()


# ── the reconciler ────────────────────────────────────────────────────────


async def test_reconciler_redispatches_stale_pending_submission():
    """The core recovery: a stale un-dispatched proof gets re-queued.

    Fails before this change: nothing re-dispatched, so the submission stayed
    pending until the deadline sweep charged the pledge.
    """

    def _boom(**kwargs):
        raise RuntimeError("broker unavailable")

    dispatched = []

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id, dispatch=_boom)
            submission_id = resp.json()["submission_id"]

            async with factory() as db:
                await _backdate(db, submission_id, _stale_minutes())
                assert await count_stale_dispatches(db) == 1

                with patch(DELAY_PATH, lambda **kw: dispatched.append(kw)):
                    summary = await reconcile_stale_dispatches(db=db)

                assert summary["claimed"] == 1
                assert summary["redispatched"] == 1
                assert len(dispatched) == 1
                assert dispatched[0]["submission_id_str"] == submission_id
                assert dispatched[0]["goal_id_str"] == goal_id
                assert dispatched[0]["proof_data"]["video_id"] == "dQw4w9WgXcQ"
                assert dispatched[0]["criteria_data"]["min_duration_seconds"] == 120

                sub = (
                    await db.execute(
                        select(ProofSubmission).where(
                            ProofSubmission.id == uuid.UUID(submission_id)
                        )
                    )
                ).scalar_one()
                await db.refresh(sub)
                assert sub.dispatch_attempts == 2
                assert sub.dispatched_at is not None
                # Already claimed — a second sweep must not pick it up again.
                assert await count_stale_dispatches(db) == 0
    finally:
        await engine.dispose()


async def test_reconciler_ignores_fresh_submission():
    """A just-submitted proof is never re-dispatched.

    This is the guard against racing the in-flight submit-proof request, which
    briefly looks un-dispatched between its two commits.
    """

    def _boom(**kwargs):
        raise RuntimeError("broker unavailable")

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            await _submit_json_proof(client, token, goal_id, dispatch=_boom)

            async with factory() as db:
                assert await count_stale_dispatches(db) == 0
                summary = await reconcile_stale_dispatches(db=db)
                assert summary["claimed"] == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize("terminal_status", ["verified", "failed"])
async def test_reconciler_ignores_terminal_submissions(terminal_status):
    """A completed verification is never re-run — the no-double-work guard.

    ``failed`` is the dangerous one: re-verifying it would re-enter
    persist_verification_result, which charges the pledge.
    """
    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id)
            submission_id = resp.json()["submission_id"]

            async with factory() as db:
                await db.execute(
                    text(
                        "UPDATE proof_submissions SET verification_status = :s, "
                        "dispatch_attempts = 1 WHERE id = :i"
                    ),
                    {"s": terminal_status, "i": uuid.UUID(submission_id)},
                )
                await db.commit()
                await _backdate(db, submission_id, _stale_minutes())

                assert await count_stale_dispatches(db) == 0
                summary = await reconcile_stale_dispatches(db=db)
                assert summary["claimed"] == 0
    finally:
        await engine.dispose()


async def test_reconciler_respects_attempt_cap():
    """A submission at the attempt cap is left alone rather than retried forever."""
    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id)
            submission_id = resp.json()["submission_id"]

            async with factory() as db:
                await db.execute(
                    text(
                        "UPDATE proof_submissions SET dispatch_attempts = :n WHERE id = :i"
                    ),
                    {
                        "n": settings.verification_dispatch_max_attempts,
                        "i": uuid.UUID(submission_id),
                    },
                )
                await db.commit()
                await _backdate(db, submission_id, _stale_minutes())

                assert await count_stale_dispatches(db) == 0
                assert (await reconcile_stale_dispatches(db=db))["claimed"] == 0

                # One below the cap is still eligible.
                await db.execute(
                    text(
                        "UPDATE proof_submissions SET dispatch_attempts = :n WHERE id = :i"
                    ),
                    {
                        "n": settings.verification_dispatch_max_attempts - 1,
                        "i": uuid.UUID(submission_id),
                    },
                )
                await db.commit()
                assert await count_stale_dispatches(db) == 1
    finally:
        await engine.dispose()


async def test_reconciler_replays_snapshot_criteria_not_rederived():
    """Re-dispatch must reuse the exact criteria the original verifier got.

    github_repo puts the ENCRYPTED PAT into criteria_data. Re-deriving criteria
    from goal_criteria would drop it, the private-repo verification would fail,
    and a failed verification charges the pledge — the reconciler would cause
    the very wrong charge it exists to prevent.
    """
    dispatched = []

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id)
            submission_id = resp.json()["submission_id"]

            async with factory() as db:
                # Stand in for a goal type that refined criteria from the body.
                await db.execute(
                    text(
                        "UPDATE proof_submissions "
                        "SET dispatch_criteria = CAST(:c AS jsonb), "
                        "dispatch_attempts = 1 WHERE id = :i"
                    ),
                    {
                        "c": '{"github_token": "gAAAAAencrypted", "branch": "main"}',
                        "i": uuid.UUID(submission_id),
                    },
                )
                await db.commit()
                await _backdate(db, submission_id, _stale_minutes())

                with patch(DELAY_PATH, lambda **kw: dispatched.append(kw)):
                    summary = await reconcile_stale_dispatches(db=db)

                assert summary["redispatched"] == 1
                assert (
                    dispatched[0]["criteria_data"]["github_token"] == "gAAAAAencrypted"
                )
                assert dispatched[0]["criteria_data"]["branch"] == "main"
    finally:
        await engine.dispose()


async def test_overlapping_sweeps_claim_each_row_once():
    """Two concurrent sweeps must not both re-dispatch the same submission.

    FOR UPDATE SKIP LOCKED plus incrementing the counter inside the claiming
    statement is what makes overlapping beat ticks safe.
    """
    import asyncio

    dispatched = []

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id)
            submission_id = resp.json()["submission_id"]

            async with factory() as setup_db:
                await _backdate(setup_db, submission_id, _stale_minutes())

            async with factory() as db_a, factory() as db_b:
                with patch(DELAY_PATH, lambda **kw: dispatched.append(kw)):
                    summaries = await asyncio.gather(
                        reconcile_stale_dispatches(db=db_a),
                        reconcile_stale_dispatches(db=db_b),
                    )

            assert sum(s["claimed"] for s in summaries) == 1, summaries
            assert len(dispatched) == 1
    finally:
        await engine.dispose()


# ── the money guard ───────────────────────────────────────────────────────


async def test_duplicate_failed_verification_charges_only_once():
    """A duplicated verification that lands on "failed" must not double-charge.

    This is the property that makes re-dispatch safe at all: the reconciler
    cannot select a terminal row, but if a duplicate verification ever did
    run, the charge that eventually follows is idempotent —
    process_charge_for_goal returns early once a payment row exists for the
    goal, and its Stripe call carries idempotency_key="goal-charge-{goal_id}".

    Moves the goal to ``pending_review`` before verifying: a `failed` verdict
    on a still-``active`` goal now defers resolution to the deadline sweep
    instead of resolving on the spot (see verification_result.py's "A real
    failure before the deadline is not yet a verdict on the goal"), which
    isn't what this test is pinning.

    Charging itself is deferred further still — persist_verification_result
    only sets ``charge_after`` now; ``process_deferred_charges``
    (app/workers/payments.py) is what actually calls Stripe once the buffer
    has passed. This test drives both duplication points: two verdicts for
    the same submission, and two sweep passes over the same due goal.
    """
    from app.services.verification_result import persist_verification_result
    from app.workers.payments import process_deferred_charges

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, user = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id)
            submission_id = resp.json()["submission_id"]

            async with factory() as db:
                await db.execute(
                    text("UPDATE users SET stripe_customer_id = :c WHERE id = :i"),
                    {"c": "cus_test123", "i": uuid.UUID(user["id"])},
                )
                await db.execute(
                    text("UPDATE goals SET status = 'pending_review' WHERE id = :g"),
                    {"g": uuid.UUID(goal_id)},
                )
                await db.commit()

            async with factory() as db:
                for _ in range(2):
                    await persist_verification_result(
                        db,
                        uuid.UUID(goal_id),
                        uuid.UUID(submission_id),
                        "failed",
                        {"reason": "duplicate-verification test"},
                    )

            async with factory() as db:
                result = await db.execute(
                    text("SELECT status, charge_after FROM goals WHERE id = :g"),
                    {"g": uuid.UUID(goal_id)},
                )
                row = result.one()
                assert row.status == "failed"
                assert row.charge_after is not None

                # Backdate the buffer so the sweep considers it due, then run
                # the sweep TWICE — simulating two overlapping ticks over the
                # same due goal, the other place a duplicate charge could
                # sneak in.
                await db.execute(
                    text("UPDATE goals SET charge_after = :ca WHERE id = :g"),
                    {"ca": datetime.now(timezone.utc) - timedelta(minutes=1), "g": uuid.UUID(goal_id)},
                )
                await db.commit()

            pi = SimpleNamespace(id="pi_test123", status="succeeded")
            transfer = SimpleNamespace(id="tr_test123")

            with (
                patch(
                    "app.workers.payments._resolve_payment_method",
                    return_value="pm_test123",
                ),
                patch(
                    "app.workers.payments.stripe.PaymentIntent.create", return_value=pi
                ) as mock_create,
                patch(
                    "app.workers.payments.stripe.PaymentIntent.retrieve",
                    return_value=pi,
                ),
                patch(
                    "app.workers.payments.stripe.Transfer.create", return_value=transfer
                ),
            ):
                await process_deferred_charges()
                # Second pass would only find this goal again if charge_after
                # were not cleared, or if the payments-row check were absent.
                await process_deferred_charges()

            # Exactly one charge and one payment row despite two failed
            # verdicts AND two sweep passes.
            assert mock_create.call_count == 1, (
                f"charged {mock_create.call_count} times — double charge"
            )
            kwargs = mock_create.call_args.kwargs
            assert kwargs["idempotency_key"] == f"goal-charge-{goal_id}"

            async with factory() as db:
                count = (
                    await db.execute(
                        text("SELECT COUNT(*) FROM payments WHERE goal_id = :g"),
                        {"g": uuid.UUID(goal_id)},
                    )
                ).scalar_one()
                assert count == 1, f"{count} payment rows for one goal"
    finally:
        await engine.dispose()


async def test_reconciler_never_writes_verification_results():
    """The sweep only re-queues; it must never resolve a goal or charge.

    A reconciler that decided outcomes itself would be a second, untested path
    to "failed" — and "failed" spends money.
    """
    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id)
            submission_id = resp.json()["submission_id"]

            async with factory() as db:
                await _backdate(db, submission_id, _stale_minutes())
                with patch(DELAY_PATH):
                    await reconcile_stale_dispatches(db=db)

                sub = (
                    await db.execute(
                        select(ProofSubmission).where(
                            ProofSubmission.id == uuid.UUID(submission_id)
                        )
                    )
                ).scalar_one()
                await db.refresh(sub)
                assert sub.verification_status == "pending"
                assert sub.verification_details is None

                goal = (
                    await db.execute(select(Goal).where(Goal.id == uuid.UUID(goal_id)))
                ).scalar_one()
                await db.refresh(goal)
                assert goal.status == "active"

                payments = (
                    await db.execute(
                        text("SELECT COUNT(*) FROM payments WHERE goal_id = :g"),
                        {"g": uuid.UUID(goal_id)},
                    )
                ).scalar_one()
                assert payments == 0
    finally:
        await engine.dispose()


# ── wiring ────────────────────────────────────────────────────────────────


async def test_reconciler_is_wired_into_celery_beat():
    """The task is auto-included by the worker and the beat entry resolves."""
    from app.core.celery_app import celery_app
    from app.goal_types.registry import get_celery_include_modules

    assert "app.workers.reconcile_dispatch" in get_celery_include_modules()

    entry = celery_app.conf.beat_schedule["reconcile-verification-dispatch"]
    assert entry["task"] == "app.workers.reconcile_dispatch.reconcile_dispatch_task"

    celery_app.loader.import_default_modules()
    assert entry["task"] in celery_app.tasks, (
        "beat would log 'received unregistered task' every tick"
    )


# ── migration ─────────────────────────────────────────────────────────────


async def test_dispatch_bookkeeping_migration_applies_and_rolls_back():
    """The revision applies to a populated DB and downgrades cleanly.

    Runs against a scratch database so it cannot disturb the test schema. Also
    pins the backfill: pre-existing pending rows must come out INELIGIBLE for
    the sweep, or first boot re-verifies historical proofs — which, with no
    dispatch_criteria snapshot to replay, can fail them and charge pledges.
    """
    import os
    import subprocess

    import asyncpg

    revision = "d5e6f7a8b9c0"
    db_name = f"sacrifice_mig_{uuid.uuid4().hex[:12]}"
    admin_dsn = settings.database_url.replace("+asyncpg", "").rsplit("/", 1)[0]

    try:
        conn = await asyncpg.connect(f"{admin_dsn}/postgres")
    except Exception as exc:  # pragma: no cover - env without a reachable server
        pytest.skip(f"cannot reach postgres to test migrations: {exc}")

    await conn.execute(f'CREATE DATABASE "{db_name}"')
    await conn.close()

    env = {
        **os.environ,
        "DATABASE_URL": f"{settings.database_url.rsplit('/', 1)[0]}/{db_name}",
    }

    def alembic(*args):
        return subprocess.run(
            ["uv", "run", "alembic", *args],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    scratch = await asyncpg.connect(f"{admin_dsn}/{db_name}")
    try:
        # Migrate to the revision BEFORE this one, then populate it. The parent
        # is read from the revision module so this test doesn't pin a second
        # copy of the chain.
        import importlib.util

        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rev_path = os.path.join(
            backend_dir,
            "alembic",
            "versions",
            f"{revision}_add_proof_dispatch_bookkeeping.py",
        )
        spec = importlib.util.spec_from_file_location("_rev_under_test", rev_path)
        rev_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rev_mod)
        parent = rev_mod.down_revision
        assert parent, "revision must have a parent"

        down = alembic("upgrade", parent)
        assert down.returncode == 0, down.stderr

        uid, gid = uuid.uuid4(), uuid.uuid4()
        await scratch.execute(
            "INSERT INTO users (id, email, display_name, auth_provider,"
            " auth_provider_id, auth_session_id, created_at, updated_at)"
            " VALUES ($1,$2,$3,$4,$5,$6,now(),now())",
            uid,
            f"{db_name}@example.com",
            "Seed",
            "google",
            "sub-1",
            str(uuid.uuid4()),
        )
        await scratch.execute(
            "INSERT INTO goals (id, user_id, title, description, goal_type,"
            " pledge_amount, currency, deadline, timezone, status, created_at,"
            " updated_at) VALUES ($1,$2,'t','d','youtube_video',5000,'usd',"
            "now(),'UTC','active',now(),now())",
            gid,
            uid,
        )
        old = datetime.now(timezone.utc) - timedelta(days=3)
        ids = {}
        for status_value in ("pending", "verified", "failed"):
            ids[status_value] = uuid.uuid4()
            await scratch.execute(
                "INSERT INTO proof_submissions (id, goal_id, submitted_at,"
                " proof_data, verification_status)"
                " VALUES ($1,$2,$3,'{}'::jsonb,$4)",
                ids[status_value],
                gid,
                old,
                status_value,
            )

        # ── upgrade ──
        up = alembic("upgrade", revision)
        assert up.returncode == 0, up.stderr

        cols = {
            r["column_name"]
            for r in await scratch.fetch(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'proof_submissions'"
                "   AND column_name LIKE 'dispatch%'"
            )
        }
        assert cols == {"dispatched_at", "dispatch_attempts", "dispatch_criteria"}

        index = await scratch.fetchval(
            "SELECT indexdef FROM pg_indexes"
            " WHERE indexname = 'ix_proof_submissions_pending_dispatch'"
        )
        assert index is not None and "verification_status = 'pending'" in index

        # Backfill: the pre-existing pending row is ineligible for the sweep.
        pending_attempts = await scratch.fetchval(
            "SELECT dispatch_attempts FROM proof_submissions WHERE id = $1",
            ids["pending"],
        )
        assert pending_attempts > settings.verification_dispatch_max_attempts
        assert (
            await scratch.fetchval(
                "SELECT dispatched_at IS NOT NULL FROM proof_submissions WHERE id = $1",
                ids["pending"],
            )
            is True
        )
        # Terminal rows are untouched defaults — never candidates anyway.
        for status_value in ("verified", "failed"):
            assert (
                await scratch.fetchval(
                    "SELECT dispatch_attempts FROM proof_submissions WHERE id = $1",
                    ids[status_value],
                )
                == 0
            )

        # ── downgrade ──
        rollback = alembic("downgrade", "-1")
        assert rollback.returncode == 0, rollback.stderr

        assert (
            await scratch.fetchval(
                "SELECT COUNT(*) FROM information_schema.columns"
                " WHERE table_name = 'proof_submissions' AND column_name LIKE 'dispatch%'"
            )
            == 0
        )
        assert (
            await scratch.fetchval(
                "SELECT COUNT(*) FROM pg_indexes"
                " WHERE indexname = 'ix_proof_submissions_pending_dispatch'"
            )
            == 0
        )
        # Rollback must not destroy data.
        assert await scratch.fetchval("SELECT COUNT(*) FROM proof_submissions") == 3
    finally:
        await scratch.close()
        conn = await asyncpg.connect(f"{admin_dsn}/postgres")
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.close()


async def test_fast_worker_result_is_not_clobbered_by_dispatch_bookkeeping():
    """A verification that completes before the route records the dispatch wins.

    The route writes dispatch_attempts/dispatched_at AFTER handing off, so a
    fast worker can resolve the submission in between. If that second commit
    reset verification_status back to "pending", a resolved proof would become
    a reconciler candidate again — a second verification, and on "failed" a
    second charge. SQLAlchemy must emit an UPDATE for only the two dirty
    columns; this pins that.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _fast_worker(**kwargs):
        """Stand in for a worker that finishes before the request continues."""
        submission_id = uuid.UUID(kwargs["submission_id_str"])

        def _work():
            async def _go():
                e = create_async_engine(settings.database_url, echo=False)
                async with e.begin() as conn:
                    await conn.execute(
                        text(
                            "UPDATE proof_submissions "
                            "SET verification_status = 'verified', "
                            "    verification_details = CAST(:d AS jsonb) "
                            "WHERE id = :i"
                        ),
                        {"d": '{"raced": true}', "i": submission_id},
                    )
                await e.dispose()

            asyncio.run(_go())

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(_work).result(timeout=30)

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(
                client, token, goal_id, dispatch=_fast_worker
            )
            assert resp.status_code == 202, resp.text
            submission_id = resp.json()["submission_id"]

            async with factory() as db:
                sub = (
                    await db.execute(
                        select(ProofSubmission).where(
                            ProofSubmission.id == uuid.UUID(submission_id)
                        )
                    )
                ).scalar_one()
                # The worker's result survived the route's bookkeeping commit.
                assert sub.verification_status == "verified"
                assert sub.verification_details == {"raced": True}
                # And the bookkeeping still landed.
                assert sub.dispatched_at is not None
                assert sub.dispatch_attempts == 1

                # Resolved rows are not sweep candidates even once stale.
                await _backdate(db, submission_id, _stale_minutes())
                assert await count_stale_dispatches(db) == 0
    finally:
        await engine.dispose()


# ── dispatch_criteria holds an encrypted token: leak + retention ───────────


async def test_snapshot_token_is_ciphertext_not_plaintext():
    """The snapshotted PAT must be the ciphertext submit_proof produced.

    Snapshotting a decrypted token would be a straight downgrade in posture, so
    this asserts the stored value is neither the plaintext nor decodable as it.
    """
    from app.core.crypto import decrypt_token

    plaintext = "ghp_supersecrettoken_do_not_store"
    goal_type = goal_type_registry.get_type("github_repo")

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(
                client,
                token,
                "github_repo",
                {
                    "repo_owner": "test",
                    "repo_name": "repo",
                    "repo_url": "https://github.com/test/repo",
                    "min_commits": 1,
                },
            )
            with patch.object(goal_type, "dispatch_verification", lambda **kw: None):
                resp = await client.post(
                    f"/api/goals/{goal_id}/submit-proof",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "repo_url": "https://github.com/test/repo",
                        "github_token": plaintext,
                    },
                )
            assert resp.status_code == 202, resp.text

            async with factory() as db:
                sub = (
                    await db.execute(
                        select(ProofSubmission).where(
                            ProofSubmission.id
                            == uuid.UUID(resp.json()["submission_id"])
                        )
                    )
                ).scalar_one()
                stored = sub.dispatch_criteria["github_token"]
                assert stored != plaintext
                assert plaintext not in str(sub.dispatch_criteria)
                # It is real ciphertext of the real token, not a mangled string.
                assert decrypt_token(stored) == plaintext
    finally:
        await engine.dispose()


async def test_dispatch_criteria_never_reaches_any_api_response():
    """No endpoint that can serialize a ProofSubmission may expose the snapshot.

    A new model column is exactly how a field silently starts appearing in
    responses, so this walks the goal/proof-facing endpoints rather than trusting
    the one hand-built dict in get_verification_status.
    """
    plaintext = "ghp_leakcanary_token"
    goal_type = goal_type_registry.get_type("github_repo")

    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_active_goal(
            client,
            token,
            "github_repo",
            {
                "repo_owner": "test",
                "repo_name": "repo",
                "repo_url": "https://github.com/test/repo",
                "min_commits": 1,
            },
        )
        with patch.object(goal_type, "dispatch_verification", lambda **kw: None):
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "repo_url": "https://github.com/test/repo",
                    "github_token": plaintext,
                },
            )
        assert resp.status_code == 202, resp.text

        auth = {"Authorization": f"Bearer {token}"}
        responses = [
            resp,
            await client.get(f"/api/goals/{goal_id}/verification-status", headers=auth),
            await client.get(f"/api/goals/{goal_id}", headers=auth),
            await client.get("/api/goals", headers=auth),
            await client.get("/api/notifications", headers=auth),
        ]
        for r in responses:
            body = r.text
            assert "dispatch_criteria" not in body, r.request.url
            assert "github_token" not in body, r.request.url
            assert plaintext not in body, r.request.url
            # The ciphertext must not appear either.
            assert "gAAAAA" not in body, r.request.url


async def test_no_model_to_response_autoserialization_exists():
    """Structural fence: nothing turns a ProofSubmission row into a response.

    If someone later adds a from_attributes/orm_mode schema over
    ProofSubmission, every column — including the token-bearing snapshot —
    starts being serialized by default. This fails loudly if that appears.
    """
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        text_content = path.read_text()
        if "from_attributes" in text_content or "orm_mode" in text_content:
            offenders.append(str(path))
    assert not offenders, (
        "ORM auto-serialization introduced; re-audit that dispatch_criteria "
        f"is excluded from responses: {offenders}"
    )


@pytest.mark.parametrize("verdict", ["verified", "failed"])
async def test_resolved_submission_criteria_snapshot_is_cleared(verdict):
    """Once a verdict lands, the token-bearing snapshot is dropped.

    Retention is bounded to one beat interval past the verdict rather than
    forever.
    """
    from app.workers.reconcile_dispatch import clear_resolved_dispatch_criteria

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id)
            submission_id = resp.json()["submission_id"]

            async with factory() as db:
                sub = (
                    await db.execute(
                        select(ProofSubmission).where(
                            ProofSubmission.id == uuid.UUID(submission_id)
                        )
                    )
                ).scalar_one()
                assert sub.dispatch_criteria is not None

                await db.execute(
                    text(
                        "UPDATE proof_submissions SET verification_status = :s "
                        "WHERE id = :i"
                    ),
                    {"s": verdict, "i": uuid.UUID(submission_id)},
                )
                await db.commit()

                cleared = await clear_resolved_dispatch_criteria(db)
                assert cleared >= 1

                await db.refresh(sub)
                assert sub.dispatch_criteria is None
                # The verdict itself is untouched.
                assert sub.verification_status == verdict
    finally:
        await engine.dispose()


async def test_pending_submission_keeps_its_criteria_snapshot():
    """Clearing must not strip a snapshot still needed for a replay.

    An INCONCLUSIVE outcome returns the row to 'pending' so it can be
    re-dispatched; dropping the snapshot there would make the replay verify
    against {} — for github_repo, no token, which fails and charges the pledge.
    """
    from app.workers.reconcile_dispatch import clear_resolved_dispatch_criteria

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id)
            submission_id = resp.json()["submission_id"]

            async with factory() as db:
                await clear_resolved_dispatch_criteria(db)

                sub = (
                    await db.execute(
                        select(ProofSubmission).where(
                            ProofSubmission.id == uuid.UUID(submission_id)
                        )
                    )
                ).scalar_one()
                assert sub.verification_status == "pending"
                assert sub.dispatch_criteria is not None
    finally:
        await engine.dispose()


async def test_sweep_reports_cleared_snapshots():
    """The beat sweep does the clearing, not just the standalone helper."""
    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id)
            submission_id = resp.json()["submission_id"]

            async with factory() as db:
                await db.execute(
                    text(
                        "UPDATE proof_submissions SET verification_status = 'verified' "
                        "WHERE id = :i"
                    ),
                    {"i": uuid.UUID(submission_id)},
                )
                await db.commit()

                summary = await reconcile_stale_dispatches(db=db)
                assert summary["criteria_cleared"] >= 1
    finally:
        await engine.dispose()


# ── Layer 1: the dispatch-failure audit event ────────────────────────────


async def test_failed_dispatch_writes_audit_event():
    """A failed enqueue is recorded in the audit log, not only in worker logs."""
    from app.models.audit_event import AuditEvent

    def _boom(**kwargs):
        raise RuntimeError("broker unavailable")

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, user = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id, dispatch=_boom)
            assert resp.status_code == 202, resp.text

            async with factory() as db:
                events = (
                    (
                        await db.execute(
                            select(AuditEvent).where(
                                AuditEvent.goal_id == uuid.UUID(goal_id),
                                AuditEvent.event_type == "proof_dispatch_failed",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(events) == 1
                event = events[0]
                assert str(event.user_id) == user["id"]
                assert event.details["submission_id"] == resp.json()["submission_id"]
                assert event.details["goal_type"] == "youtube_video"
                assert event.details["exception_type"] == "RuntimeError"
                # The exception MESSAGE can carry proof content — type only.
                assert "broker unavailable" not in str(event.details)

                # The accepted event still fires exactly once: the new value
                # must not disturb existing per-type invariants.
                accepted = (
                    (
                        await db.execute(
                            select(AuditEvent).where(
                                AuditEvent.goal_id == uuid.UUID(goal_id),
                                AuditEvent.event_type == "proof_accepted",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(accepted) == 1
    finally:
        await engine.dispose()


async def test_successful_dispatch_writes_no_failure_event():
    """The failure event must not fire when the enqueue worked."""
    from app.models.audit_event import AuditEvent

    engine, factory = _engine_and_factory()
    try:
        async with make_client() as client:
            token, _ = await _auth(client)
            goal_id = await _create_active_goal(client, token)
            resp = await _submit_json_proof(client, token, goal_id)
            assert resp.status_code == 202, resp.text

            async with factory() as db:
                events = (
                    (
                        await db.execute(
                            select(AuditEvent).where(
                                AuditEvent.goal_id == uuid.UUID(goal_id),
                                AuditEvent.event_type == "proof_dispatch_failed",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert events == []
    finally:
        await engine.dispose()


async def test_audit_enum_migration_applies_and_rolls_back():
    """The enum revision applies, and its recreate-type downgrade is clean.

    Postgres has no DROP VALUE, so the downgrade rebuilds audit_event_type. That
    must (a) restore the original two values, (b) delete only the rows carrying
    the removed value, (c) preserve every other audit row, and (d) leave no
    orphaned ..._old type behind.
    """
    import os
    import subprocess

    import asyncpg

    revision = "e7a8b9c0d1e2"
    db_name = f"sacrifice_enum_{uuid.uuid4().hex[:12]}"
    admin_dsn = settings.database_url.replace("+asyncpg", "").rsplit("/", 1)[0]

    try:
        conn = await asyncpg.connect(f"{admin_dsn}/postgres")
    except Exception as exc:  # pragma: no cover - env without a reachable server
        pytest.skip(f"cannot reach postgres to test migrations: {exc}")

    await conn.execute(f'CREATE DATABASE "{db_name}"')
    await conn.close()

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {
        **os.environ,
        "DATABASE_URL": f"{settings.database_url.rsplit('/', 1)[0]}/{db_name}",
    }

    def alembic(*args):
        return subprocess.run(
            ["uv", "run", "alembic", *args],
            cwd=backend_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    scratch = await asyncpg.connect(f"{admin_dsn}/{db_name}")
    try:
        up = alembic("upgrade", revision)
        assert up.returncode == 0, up.stderr

        async def enum_values():
            rows = await scratch.fetch(
                "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid"
                " WHERE t.typname = 'audit_event_type' ORDER BY e.enumsortorder"
            )
            return [r["enumlabel"] for r in rows]

        assert "proof_dispatch_failed" in await enum_values()

        # Rows of both kinds, so the downgrade has something to preserve.
        uid, gid = uuid.uuid4(), uuid.uuid4()
        await scratch.execute(
            "INSERT INTO users (id, email, display_name, auth_provider,"
            " auth_provider_id, auth_session_id, created_at, updated_at)"
            " VALUES ($1,$2,$3,$4,$5,$6,now(),now())",
            uid,
            f"{db_name}@example.com",
            "Seed",
            "google",
            "sub-1",
            str(uuid.uuid4()),
        )
        await scratch.execute(
            "INSERT INTO goals (id, user_id, title, description, goal_type,"
            " pledge_amount, currency, deadline, timezone, status, created_at,"
            " updated_at) VALUES ($1,$2,'t','d','youtube_video',100,'usd',"
            "now(),'UTC','active',now(),now())",
            gid,
            uid,
        )
        for event_type in ("proof_accepted", "proof_rejected", "proof_dispatch_failed"):
            await scratch.execute(
                "INSERT INTO audit_events (id, goal_id, user_id, event_type,"
                " details, created_at) VALUES ($1,$2,$3,$4,'{}'::jsonb,now())",
                uuid.uuid4(),
                gid,
                uid,
                event_type,
            )

        rollback = alembic("downgrade", "-1")
        assert rollback.returncode == 0, rollback.stderr

        assert await enum_values() == ["proof_accepted", "proof_rejected"]
        remaining = {
            r["event_type"]: r["count"]
            for r in await scratch.fetch(
                "SELECT event_type::text AS event_type, COUNT(*) AS count"
                " FROM audit_events GROUP BY 1"
            )
        }
        assert remaining == {"proof_accepted": 1, "proof_rejected": 1}
        assert (
            await scratch.fetchval(
                "SELECT COUNT(*) FROM pg_type WHERE typname = 'audit_event_type_old'"
            )
            == 0
        )
    finally:
        await scratch.close()
        conn = await asyncpg.connect(f"{admin_dsn}/postgres")
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.close()
