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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.core.crypto import encrypt_token
from app.models.goal import Goal, GoalCriteria
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
    detail_resp = _make_response(
        json_data={"stats": {"additions": 10, "deletions": 5}}
    )
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
async def test_verify_marks_failed_on_github_500():
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

    assert result["verification_status"] == "failed"
    cond = result["verification_details"]["condition_results"][0]
    assert cond["passed"] is False
    assert "error" in cond
    assert "500" in cond["error"]


# ─── Invalid repo URL: clean rejection ─────────────────────────────


@pytest.mark.asyncio
async def test_verify_raises_value_error_on_malformed_repo_url():
    """A malformed repo URL should not produce a silent pass; the parser
    raises before any HTTP call happens."""
    from app.workers.github_repo import verify_github_repo

    proof_data = {"repo_url": "not-a-github-url"}
    criteria_data = {"conditions": [{"type": "commits", "min_count": 1}]}

    with pytest.raises(ValueError, match="Could not parse owner/repo"):
        await verify_github_repo(proof_data, criteria_data)


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
