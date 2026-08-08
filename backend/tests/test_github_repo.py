"""Unit tests for the github_repo verification worker.

These tests exercise ``verify_github_repo`` directly with mocked
``httpx.AsyncClient`` instances — no real HTTP and no DB are involved.
The persistence layer (``_persist_result``) is covered through the
success-path DB test at the bottom of this file, mirroring the pattern
used by ``test_youtube_verification.py``.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.core.crypto import encrypt_token
from app.models.goal import Goal
from app.models.proof import ProofSubmission
from app.models.user import User


# ─── Mock helpers ──────────────────────────────────────────────────


def _make_response(status_code=200, json_data=None, headers=None, text=""):
    """Build a minimal mock ``httpx.Response`` object."""
    if json_data is None:
        json_data = []
    if headers is None:
        headers = {}
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.headers = headers
    mock_resp.text = text
    return mock_resp


def _make_async_client(get_side_effect):
    """Patch ``httpx.AsyncClient`` so each ``await client.get(...)`` returns
    the next value from ``get_side_effect`` (a list or callable).
    Returns ``(client_cls_mock, client_instance_mock)`` so callers can spy
    on call arguments (e.g. to assert headers contained a decrypted token).
    """
    client_instance = AsyncMock()
    if callable(get_side_effect):
        client_instance.get.side_effect = get_side_effect
    else:
        client_instance.get.side_effect = list(get_side_effect)

    client_cls = MagicMock()
    client_cls.return_value.__aenter__.return_value = client_instance
    return client_cls, client_instance


# ─── Success path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_success_when_all_conditions_met():
    from app.workers.github_repo import verify_github_repo

    proof_data = {"repo_url": "https://github.com/octocat/Hello-World"}
    criteria_data = {
        "conditions": [
            {"type": "commits", "min_count": 1},
        ],
    }

    # commits endpoint returns 1 commit
    client_cls, _ = _make_async_client(
        [_make_response(json_data=[{"sha": "abc123"}], headers={})]
    )

    with patch("app.workers.github_repo.httpx.AsyncClient", client_cls):
        result = await verify_github_repo(proof_data, criteria_data)

    assert result["verification_status"] == "verified"
    assert result["verification_details"]["owner"] == "octocat"
    assert result["verification_details"]["repo"] == "Hello-World"
    commit_result = result["verification_details"]["condition_results"][0]
    assert commit_result["passed"] is True
    assert commit_result["actual"] == 1


# ─── Commits criterion: too few ────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_fails_when_commits_below_min_count():
    from app.workers.github_repo import verify_github_repo

    proof_data = {"repo_url": "https://github.com/octocat/Hello-World"}
    criteria_data = {
        "conditions": [
            {"type": "commits", "min_count": 5},
        ],
    }

    # Only one commit returned, but criterion requires 5.
    client_cls, _ = _make_async_client(
        [_make_response(json_data=[{"sha": "abc123"}], headers={})]
    )

    with patch("app.workers.github_repo.httpx.AsyncClient", client_cls):
        result = await verify_github_repo(proof_data, criteria_data)

    assert result["verification_status"] == "failed"
    commit_result = result["verification_details"]["condition_results"][0]
    assert commit_result["passed"] is False
    assert commit_result["actual"] == 1
    assert commit_result["min_count"] == 5
    assert "failure_reason" in commit_result


# ─── lines_changed criterion: pass when actual >= required ─────────


@pytest.mark.asyncio
async def test_verify_lines_changed_passes_when_actual_meets_min():
    from app.workers.github_repo import verify_github_repo

    proof_data = {"repo_url": "https://github.com/octocat/Hello-World"}
    criteria_data = {
        "conditions": [
            {"type": "lines_changed", "min_count": 50},
        ],
    }

    # First call: list one commit on the branch. Second call: commit detail
    # with stats.additions=30, deletions=25 => total=55, which meets the 50
    # minimum. Third call: empty list to terminate the pagination loop.
    list_resp = _make_response(
        json_data=[
            {
                "sha": "abc123",
                "url": "https://api.github.com/repos/octocat/Hello-World/commits/abc123",
            }
        ]
    )
    detail_resp = _make_response(
        json_data={"stats": {"additions": 30, "deletions": 25}}
    )
    empty_page = _make_response(json_data=[])

    client_cls, _ = _make_async_client([list_resp, detail_resp, empty_page])

    with patch("app.workers.github_repo.httpx.AsyncClient", client_cls):
        result = await verify_github_repo(proof_data, criteria_data)

    assert result["verification_status"] == "verified"
    cond = result["verification_details"]["condition_results"][0]
    assert cond["passed"] is True
    assert cond["actual"] == 55
    assert cond["additions"] == 30
    assert cond["deletions"] == 25


# ─── lines_changed criterion: fail when actual < required ──────────


@pytest.mark.asyncio
async def test_verify_lines_changed_fails_when_actual_below_min():
    from app.workers.github_repo import verify_github_repo

    proof_data = {"repo_url": "https://github.com/octocat/Hello-World"}
    criteria_data = {
        "conditions": [
            {"type": "lines_changed", "min_count": 500},
        ],
    }

    list_resp = _make_response(
        json_data=[
            {
                "sha": "abc123",
                "url": "https://api.github.com/repos/octocat/Hello-World/commits/abc123",
            }
        ]
    )
    detail_resp = _make_response(json_data={"stats": {"additions": 10, "deletions": 5}})
    empty_page = _make_response(json_data=[])

    client_cls, _ = _make_async_client([list_resp, detail_resp, empty_page])

    with patch("app.workers.github_repo.httpx.AsyncClient", client_cls):
        result = await verify_github_repo(proof_data, criteria_data)

    assert result["verification_status"] == "failed"
    cond = result["verification_details"]["condition_results"][0]
    assert cond["passed"] is False
    assert cond["actual"] == 15
    assert "failure_reason" in cond


# ─── Token decryption: verify the decrypted token is sent to GitHub ─


@pytest.mark.asyncio
async def test_verify_decrypts_encrypted_token_before_calling_github():
    """An encrypted token in criteria_data must be decrypted before being
    placed in the ``Authorization`` header on the GitHub request."""
    from app.workers.github_repo import verify_github_repo

    plaintext_token = "ghp_test_secret_token_xyz"
    encrypted = encrypt_token(plaintext_token)
    # sanity: the encrypted form must not equal plaintext, otherwise the
    # test would pass trivially.
    assert encrypted != plaintext_token
    assert encrypted.startswith("fernet:")

    proof_data = {
        "repo_url": "https://github.com/octocat/Hello-World",
        "github_token": encrypted,
    }
    criteria_data = {
        "conditions": [
            {"type": "commits", "min_count": 1},
        ],
    }

    client_cls, client_instance = _make_async_client(
        [_make_response(json_data=[{"sha": "abc123"}], headers={})]
    )

    with patch("app.workers.github_repo.httpx.AsyncClient", client_cls):
        await verify_github_repo(proof_data, criteria_data)

    # Inspect what was sent. The worker calls client.get(url, params=..., headers=...)
    assert client_instance.get.await_count >= 1
    sent_headers = client_instance.get.call_args_list[0].kwargs["headers"]
    auth_header = sent_headers.get("Authorization", "")
    assert auth_header == f"Bearer {plaintext_token}"
    # And the encrypted form must not appear in the header.
    assert encrypted not in auth_header


# ─── HTTP failure: 500 from GitHub → handled cleanly ──────────────


@pytest.mark.asyncio
async def test_verify_marks_inconclusive_on_github_500():
    """A GitHub outage is not evidence that the user missed their goal.

    This test previously asserted ``verification_status == "failed"``. That was
    deliberately changed, not accidentally broken: ``failed`` is terminal and
    ``services/verification_result.py`` dispatches the pledge charge on it, so
    the old expectation meant a single 502 from GitHub billed the user's card
    with no way back. The per-check assertions below are unchanged — the check
    still does not pass, and the error still names the status code — only the
    top-level outcome moved from "charge them" to "we could not tell".
    """
    from app.workers.github_repo import verify_github_repo

    proof_data = {"repo_url": "https://github.com/octocat/Hello-World"}
    criteria_data = {
        "conditions": [
            {"type": "commits", "min_count": 1},
        ],
    }

    client_cls, _ = _make_async_client(
        [_make_response(status_code=500, text="Internal Server Error")]
    )

    with patch("app.workers.github_repo.httpx.AsyncClient", client_cls):
        result = await verify_github_repo(proof_data, criteria_data)

    assert result["verification_status"] == "inconclusive"
    cond = result["verification_details"]["condition_results"][0]
    assert cond["passed"] is False
    assert "error" in cond
    assert "500" in cond["error"]


# ─── Invalid repo URL: clean rejection ─────────────────────────────


@pytest.mark.asyncio
async def test_verify_returns_inconclusive_on_malformed_repo_url():
    """A malformed repo URL must not produce a silent pass — and must not escape
    as an exception either.

    This test previously asserted ``pytest.raises(ValueError)``, which was
    deliberately changed. ``GithubRepoProofSubmission.repo_url`` is a bare
    ``str`` with no URL validation (``app/schemas/proof.py:40``), so the bad
    value is one we accepted; letting the parser raise meant the Celery task
    retried three times and then dropped the submission, leaving it ``pending``
    forever, where the deadline sweep charges the pledge anyway. The outcome is
    now recorded as inconclusive, which charges nothing and puts the row in
    front of an operator.
    """
    from app.workers.github_repo import verify_github_repo

    proof_data = {"repo_url": "not-a-github-url"}
    criteria_data = {"conditions": [{"type": "commits", "min_count": 1}]}

    result = await verify_github_repo(proof_data, criteria_data)

    assert result["verification_status"] == "inconclusive"
    assert result["inconclusive_reason"] == "criteria_not_evaluable"
    details = result["verification_details"]
    # The contract raises if a top-level failure_reason ("here is why YOU
    # failed") accompanies an inconclusive outcome.
    assert "failure_reason" not in details
    assert "not-a-github-url" in details["parse_error"]


# ─── tickets_closed: all closed → passes; any open → fails ────────


@pytest.mark.asyncio
async def test_verify_tickets_closed_passes_when_all_closed():
    from app.workers.github_repo import verify_github_repo

    proof_data = {"repo_url": "https://github.com/octocat/Hello-World"}
    criteria_data = {
        "conditions": [
            {
                "type": "tickets_closed",
                "tickets": [
                    "https://github.com/octocat/Hello-World/issues/1",
                    "https://github.com/octocat/Hello-World/issues/2",
                ],
            },
        ],
    }

    client_cls, _ = _make_async_client(
        [
            _make_response(json_data={"state": "closed"}),
            _make_response(json_data={"state": "closed"}),
        ]
    )

    with patch("app.workers.github_repo.httpx.AsyncClient", client_cls):
        result = await verify_github_repo(proof_data, criteria_data)

    assert result["verification_status"] == "verified"
    cond = result["verification_details"]["condition_results"][0]
    assert cond["passed"] is True
    assert len(cond["closed"]) == 2


@pytest.mark.asyncio
async def test_verify_tickets_closed_fails_when_one_open():
    from app.workers.github_repo import verify_github_repo

    proof_data = {"repo_url": "https://github.com/octocat/Hello-World"}
    criteria_data = {
        "conditions": [
            {
                "type": "tickets_closed",
                "tickets": [
                    "https://github.com/octocat/Hello-World/issues/1",
                    "https://github.com/octocat/Hello-World/issues/2",
                ],
            },
        ],
    }

    client_cls, _ = _make_async_client(
        [
            _make_response(json_data={"state": "closed"}),
            _make_response(json_data={"state": "open"}),
        ]
    )

    with patch("app.workers.github_repo.httpx.AsyncClient", client_cls):
        result = await verify_github_repo(proof_data, criteria_data)

    assert result["verification_status"] == "failed"
    cond = result["verification_details"]["condition_results"][0]
    assert cond["passed"] is False
    assert len(cond["closed"]) == 1
    assert len(cond["open_or_not_found"]) == 1


# ─── DB persistence: success path writes verified to goal & submission ─


@pytest.mark.asyncio
async def test_run_verification_persists_verified_status_to_db():
    """Cover the persistence side: the worker should update both the
    ``ProofSubmission`` and ``Goal`` rows when verification passes."""
    from app.workers.github_repo import run_github_repo_verification

    local_engine = create_async_engine(settings.database_url, echo=False)
    local_session_factory = async_sessionmaker(
        local_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with local_session_factory() as db:
        user = User(
            email=f"github-test-{uuid.uuid4().hex[:8]}@example.com",
            display_name="GH Test",
            auth_provider="google",
            auth_provider_id=f"sub-{uuid.uuid4().hex[:8]}",
        )
        db.add(user)
        await db.flush()

        goal = Goal(
            user_id=user.id,
            title="Ship the PR",
            goal_type="github_repo",
            pledge_amount=5000,
            deadline=datetime.now(timezone.utc) + timedelta(days=7),
            status="active",
        )
        db.add(goal)
        await db.flush()

        submission = ProofSubmission(
            goal_id=goal.id,
            submitted_at=datetime.now(timezone.utc),
            proof_data={"repo_url": "https://github.com/octocat/Hello-World"},
            verification_status="pending",
        )
        db.add(submission)
        await db.commit()

        client_cls, _ = _make_async_client(
            [_make_response(json_data=[{"sha": "abc123"}], headers={})]
        )

        with patch("app.workers.github_repo.httpx.AsyncClient", client_cls):
            await run_github_repo_verification(
                goal_id=goal.id,
                submission_id=submission.id,
                proof_data={"repo_url": "https://github.com/octocat/Hello-World"},
                criteria_data={
                    "conditions": [{"type": "commits", "min_count": 1}],
                },
                db=db,
            )

        await db.refresh(goal)
        await db.refresh(submission)

        assert goal.status == "verified"
        assert submission.verification_status == "verified"
        assert submission.verification_details is not None

    await local_engine.dispose()


# ─── THE charge boundary, against the real persistence layer ───────────


async def _seed_goal(db):
    """A user + active goal + pending submission, ready to verify."""
    user = User(
        email=f"github-charge-{uuid.uuid4().hex[:8]}@example.com",
        display_name="GH Charge Test",
        auth_provider="google",
        auth_provider_id=f"sub-{uuid.uuid4().hex[:8]}",
    )
    db.add(user)
    await db.flush()

    goal = Goal(
        user_id=user.id,
        title="Ship the PR",
        goal_type="github_repo",
        pledge_amount=5000,
        deadline=datetime.now(timezone.utc) + timedelta(days=7),
        status="active",
    )
    db.add(goal)
    await db.flush()

    submission = ProofSubmission(
        goal_id=goal.id,
        submitted_at=datetime.now(timezone.utc),
        proof_data={"repo_url": "https://github.com/octocat/Hello-World"},
        verification_status="pending",
        dispatch_attempts=1,
    )
    db.add(submission)
    await db.commit()
    return goal, submission


@pytest.mark.asyncio
async def test_inconclusive_does_not_charge_but_a_real_failure_does():
    """The money assertion, made where the money actually moves.

    Everything else in these files stops at ``persist_verification_result``. This
    one runs the real persistence layer against a real row and watches
    ``process_charge_for_goal`` — the function that creates a Stripe
    PaymentIntent with ``confirm=True``. A GitHub 503 must never reach it. A
    repo with too few commits must — eventually: with time left on the goal's
    deadline the owner gets a chance to push more commits and resubmit (see
    verification_result.py's "A real failure before the deadline is not yet a
    verdict on the goal"), so the charge doesn't fire on this call, only once
    the deadline sweep resolves the still-active, still-failing goal.

    All three halves matter and are asserted in one test on purpose: a change
    that suppressed the charge for *everything* would satisfy the first
    assertion alone, and that is the failure mode a no-charge fix invites.
    """
    from sqlalchemy import text

    from app.workers.deadline import check_deadlines
    from app.workers.github_repo import run_github_repo_verification

    local_engine = create_async_engine(settings.database_url, echo=False)
    local_session_factory = async_sessionmaker(
        local_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with local_session_factory() as db:
        # ── Half 1: GitHub is down. Our fault; no charge. ──
        goal, submission = await _seed_goal(db)
        client_cls, _ = _make_async_client(
            [_make_response(status_code=503, text="unavailable")]
        )
        with (
            patch("app.workers.github_repo.httpx.AsyncClient", client_cls),
            patch(
                "app.workers.payments.process_charge_for_goal",
                new_callable=AsyncMock,
            ) as charge,
        ):
            result = await run_github_repo_verification(
                goal_id=goal.id,
                submission_id=submission.id,
                proof_data={"repo_url": "https://github.com/octocat/Hello-World"},
                criteria_data={"min_commits": 1},
                db=db,
            )

        assert result["verification_status"] == "inconclusive"
        charge.assert_not_awaited()

        await db.refresh(goal)
        await db.refresh(submission)
        # The submission stays claimable by the reconciler and the goal stays
        # exactly where submit-proof left it, so the owner can retry.
        assert submission.verification_status == "pending"
        assert goal.status == "active"
        assert submission.verification_details["outcome"] == "inconclusive"
        assert submission.verification_details["inconclusive_reason"] == (
            "upstream_unavailable"
        )
        assert submission.verification_details["inconclusive_retryable"] is True

        # ── Half 2: the repo really is short of commits. Their miss — but the
        # deadline hasn't passed, so no charge yet and the goal stays active. ──
        goal2, submission2 = await _seed_goal(db)
        client_cls, _ = _make_async_client([_make_response(json_data=[])])
        with (
            patch("app.workers.github_repo.httpx.AsyncClient", client_cls),
            patch(
                "app.workers.payments.process_charge_for_goal",
                new_callable=AsyncMock,
            ) as charge,
        ):
            result = await run_github_repo_verification(
                goal_id=goal2.id,
                submission_id=submission2.id,
                proof_data={"repo_url": "https://github.com/octocat/Hello-World"},
                criteria_data={"min_commits": 1},
                db=db,
            )

        assert result["verification_status"] == "failed"
        charge.assert_not_awaited()

        await db.refresh(goal2)
        await db.refresh(submission2)
        assert goal2.status == "active"
        assert submission2.verification_status == "failed"

        # ── Half 3: the deadline arrives with no further (verified) proof —
        # the sweep resolves the goal, and once its midnight buffer elapses,
        # process_deferred_charges is what actually dispatches the charge.
        await db.execute(
            text("UPDATE goals SET deadline = :d WHERE id = :g"),
            {"d": datetime.now(timezone.utc) - timedelta(minutes=1), "g": goal2.id},
        )
        await db.commit()

        with patch(
            "app.workers.payments.process_charge_for_goal",
            new_callable=AsyncMock,
        ) as sweep_charge:
            await check_deadlines()
        sweep_charge.assert_not_awaited()

        await db.refresh(goal2)
        assert goal2.status == "failed"
        assert goal2.charge_after is not None
        assert submission2.verification_status == "failed"

        await db.execute(
            text("UPDATE goals SET charge_after = :ca WHERE id = :g"),
            {"ca": datetime.now(timezone.utc) - timedelta(minutes=1), "g": goal2.id},
        )
        await db.commit()

        from app.workers.payments import process_deferred_charges

        with patch(
            "app.workers.payments.process_charge_for_goal",
            new_callable=AsyncMock,
        ) as sweep_charge:
            await process_deferred_charges()
        sweep_charge.assert_awaited_once_with(str(goal2.id), str(goal2.user_id))

    await local_engine.dispose()


@pytest.mark.asyncio
async def test_unevaluable_criteria_skip_the_retry_loop_and_reach_an_operator():
    """A permanent reason must not burn the reconciler's attempts.

    Re-running a check we have no implementation for cannot produce a different
    answer, so the contract saturates ``dispatch_attempts`` to the cap and flags
    the row for review. Pinning it here is what makes the worker's choice of
    ``CRITERIA_NOT_EVALUABLE`` over a transient code load-bearing rather than
    cosmetic.
    """
    from app.workers.github_repo import run_github_repo_verification

    local_engine = create_async_engine(settings.database_url, echo=False)
    local_session_factory = async_sessionmaker(
        local_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with local_session_factory() as db:
        goal, submission = await _seed_goal(db)
        client_cls, client_instance = _make_async_client([])
        with (
            patch("app.workers.github_repo.httpx.AsyncClient", client_cls),
            patch(
                "app.workers.payments.process_charge_for_goal",
                new_callable=AsyncMock,
            ) as charge,
        ):
            result = await run_github_repo_verification(
                goal_id=goal.id,
                submission_id=submission.id,
                proof_data={},
                criteria_data={
                    "repo_owner": "octocat",
                    "repo_name": "Hello-World",
                    "conditions": [{"type": "language_stats", "min_percent": 80}],
                },
                db=db,
            )

        assert result["verification_status"] == "inconclusive"
        charge.assert_not_awaited()
        assert client_instance.get.await_count == 0

        await db.refresh(submission)
        details = submission.verification_details
        assert details["inconclusive_reason"] == "criteria_not_evaluable"
        assert details["inconclusive_retryable"] is False
        assert details["needs_operator_review"] is True
        assert submission.dispatch_attempts == (
            settings.verification_dispatch_max_attempts
        )

    await local_engine.dispose()
