"""Private-repo credentials for dev_sandbox, and the sandbox's network blast radius.

Two independent hazards are pinned here.

**The token must not leak.** A PAT that reaches ``verification_details`` is
persisted to the database *and* handed straight back to the client by
``GET /api/goals/{id}/verification-status``. The failure path is where this
surfaces, because that is the only path that copies git's diagnostics into the
record — so most of these tests drive a *failed* clone on purpose.

**A failure must not be billed to the wrong party.** ``failed`` charges a real
card, so each credential failure mode is asserted against the charge boundary
itself rather than against a proxy like the goal's status.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from subprocess import CompletedProcess
from unittest.mock import AsyncMock, MagicMock, patch

import docker
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.crypto import encrypt_token
from app.models.goal import Goal, GoalCriteria
from app.models.proof import ProofSubmission
from app.models.user import User
from app.workers import dev_sandbox as ds

CHARGE_BOUNDARY = "app.workers.payments.process_charge_for_goal"

TOKEN = "ghp_liveTokenMustNeverEscape0123456789"
PRIVATE_REPO = "https://github.com/acme/private-thing.git"

# What git prints when a PAT is rejected. Carries the secret twice over — once
# bare, once inside the remote's userinfo — because both are real leak shapes.
AUTH_FAILED_STDERR = (
    "remote: Invalid username or password.\n"
    f"remote: token {TOKEN} is not authorized\n"
    "fatal: Authentication failed for "
    f"'https://x-access-token:{TOKEN}@github.com/acme/private-thing.git'\n"
)


# ─── helpers ───────────────────────────────────────────────────────────────


def _fake_git(returncode: int = 0, stderr: str = "", stdout: str = ""):
    """Stand in for the git subprocess, recording exactly how it was invoked.

    Returns ``(patcher, calls)``; each call is the ``(argv, env)`` pair, which is
    what the argv-vs-environment assertions below are about.
    """
    calls: list[tuple[list[str], dict]] = []

    def _run(argv, **kwargs):
        calls.append((argv, kwargs.get("env") or {}))
        return CompletedProcess(argv, returncode, stdout, stderr)

    return patch.object(ds.subprocess, "run", _run), calls


def _session_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    return engine, async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


async def _make_goal(
    db: AsyncSession, proof_data: dict
) -> tuple[Goal, ProofSubmission]:
    """A user + active dev_sandbox goal + one pending submission, via the ORM."""
    user = User(
        email=f"sandbox-creds-{uuid.uuid4()}@example.com",
        display_name="Sandbox Creds",
        auth_provider="google",
        auth_provider_id=str(uuid.uuid4()),
        stripe_customer_id="cus_test_dummy",
    )
    db.add(user)
    await db.flush()

    goal = Goal(
        user_id=user.id,
        title="Ship the private thing",
        description="A goal under verification",
        goal_type="dev_sandbox",
        pledge_amount=5000,
        currency="usd",
        deadline=datetime.now(timezone.utc) + timedelta(days=3),
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
            criteria_type="dev_sandbox",
            criteria_data={"repo_url": PRIVATE_REPO, "test_command": "pytest"},
        )
    )

    submission = ProofSubmission(
        goal_id=goal.id,
        submitted_at=datetime.now(timezone.utc),
        proof_data=proof_data,
        verification_status="pending",
        dispatched_at=datetime.now(timezone.utc),
        dispatch_attempts=1,
    )
    db.add(submission)
    await db.commit()
    await db.refresh(goal)
    await db.refresh(submission)
    return goal, submission


def _mock_db() -> AsyncMock:
    scoped = MagicMock()
    scoped.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=scoped)
    db.commit = AsyncMock()
    return db


# ─── the token never rides in argv ─────────────────────────────────────────


class TestTokenTransport:
    def test_token_travels_in_the_environment_not_the_command_line(self, tmp_path):
        """``/proc/<pid>/cmdline`` is world-readable; ``environ`` is owner-only.

        This is the whole reason the credential helper exists instead of the far
        shorter ``https://<token>@github.com/...``: any local user could read the
        latter out of /proc for as long as git ran.
        """
        patcher, calls = _fake_git()
        with patcher:
            ds.clone_repo(PRIVATE_REPO, "main", str(tmp_path), github_token=TOKEN)

        argv, env = calls[0]
        assert TOKEN not in " ".join(argv), (
            "the PAT reached git's argv, which is world-readable via /proc"
        )
        assert env[ds._GIT_TOKEN_ENV] == TOKEN
        # The helper is configured, and names the env var rather than the value.
        assert f"credential.helper={ds._CREDENTIAL_HELPER}" in argv
        assert ds._GIT_TOKEN_ENV in ds._CREDENTIAL_HELPER

    def test_inherited_credential_helpers_are_cleared_first(self, tmp_path):
        """A host credential store must not answer ahead of ours, or persist ours."""
        patcher, calls = _fake_git()
        with patcher:
            ds.clone_repo(PRIVATE_REPO, "main", str(tmp_path), github_token=TOKEN)

        argv, _ = calls[0]
        assert argv.index("credential.helper=") < argv.index(
            f"credential.helper={ds._CREDENTIAL_HELPER}"
        )

    def test_interactive_prompts_are_disabled(self, tmp_path):
        """Otherwise a private repo hangs to the 120s timeout instead of failing."""
        patcher, calls = _fake_git()
        with patcher:
            ds.clone_repo(PRIVATE_REPO, "main", str(tmp_path), github_token=TOKEN)

        assert calls[0][1]["GIT_TERMINAL_PROMPT"] == "0"

    def test_public_clone_passes_no_credential_material_at_all(self, tmp_path):
        """The common case must be untouched: no helper, no token in the env."""
        patcher, calls = _fake_git()
        with patcher:
            ds.clone_repo(
                "https://github.com/octocat/Hello-World.git", "master", str(tmp_path)
            )

        argv, env = calls[0]
        assert not any("credential.helper" in a for a in argv)
        assert ds._GIT_TOKEN_ENV not in env
        assert argv[:4] == ["git", "clone", "--depth=1", "--branch"]

    def test_credential_pasted_into_the_url_is_moved_out_of_argv(self, tmp_path):
        """Users do paste ``https://<pat>@github.com/...``. Honour it, but relocate it.

        Left alone that token would sit in argv, in the clone's .git/config, and
        in ``verification_details`` (repo_url is copied there verbatim).
        """
        patcher, calls = _fake_git()
        with patcher:
            ds.clone_repo(
                f"https://{TOKEN}@github.com/acme/private-thing.git",
                "main",
                str(tmp_path),
            )

        argv, env = calls[0]
        assert TOKEN not in " ".join(argv)
        assert env[ds._GIT_TOKEN_ENV] == TOKEN
        assert "https://github.com/acme/private-thing.git" in argv

    def test_ssh_login_name_is_not_mistaken_for_a_secret(self, tmp_path):
        """``ssh://git@host/...``: the userinfo is a username. Stripping it breaks
        the clone, so credential-splitting is http(s)-only."""
        patcher, calls = _fake_git()
        with patcher:
            ds.clone_repo("ssh://git@github.com/acme/thing.git", "main", str(tmp_path))

        argv, env = calls[0]
        assert "ssh://git@github.com/acme/thing.git" in argv
        assert ds._GIT_TOKEN_ENV not in env


# ─── the token never lands in a persisted or returned field ────────────────


class TestTokenDoesNotLeakOnFailure:
    async def test_token_absent_from_stored_details_and_proof_data(self):
        """The requirement, end to end and against the real database.

        A clone fails with the PAT all over git's stderr; afterwards neither the
        submission's ``verification_details`` (which the verification-status
        endpoint returns to the client) nor its ``proof_data`` may contain the
        plaintext. ``proof_data`` still holds the *ciphertext* — that is the point
        of encrypting it, and it is what lets the reconciler retry.
        """
        engine, factory = _session_factory()
        try:
            async with factory() as db:
                ciphertext = encrypt_token(TOKEN)
                goal, submission = await _make_goal(
                    db,
                    {
                        "repo_url": PRIVATE_REPO,
                        "test_command": "pytest",
                        "github_token": ciphertext,
                    },
                )

                patcher, _ = _fake_git(returncode=1, stderr=AUTH_FAILED_STDERR)
                with patcher, patch(CHARGE_BOUNDARY, new_callable=AsyncMock):
                    result = await ds.run_dev_sandbox_verification(
                        goal_id=goal.id,
                        submission_id=submission.id,
                        proof_data=dict(submission.proof_data),
                        criteria_data={"repo_url": PRIVATE_REPO},
                        db=db,
                    )

                await db.refresh(submission)
                stored = json.dumps(submission.verification_details)
                assert TOKEN not in stored, "PAT persisted into verification_details"
                assert ciphertext not in stored
                assert TOKEN not in json.dumps(result["verification_details"])
                # ...and not in what the client is handed back.
                assert TOKEN not in json.dumps(submission.proof_data)
                assert submission.proof_data["github_token"] == ciphertext
                # The stage still has to be reported, or the panel is blank.
                assert result["verification_details"]["stage"] == "clone"
        finally:
            await engine.dispose()

    async def test_url_embedded_token_is_scrubbed_from_the_persisted_repo_url(self):
        """``repo_url`` is echoed back verbatim, so it is a leak channel of its own."""
        patcher, _ = _fake_git(returncode=1, stderr="fatal: repository not found\n")
        with patcher, patch(CHARGE_BOUNDARY, new_callable=AsyncMock):
            result = await ds.run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": f"https://{TOKEN}@github.com/acme/private-thing.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=_mock_db(),
            )

        details = result["verification_details"]
        assert TOKEN not in json.dumps(details)
        assert details["repo_url"] == "https://github.com/acme/private-thing.git"

    async def test_token_absent_from_details_when_our_own_egress_is_down(self):
        """The inconclusive branch copies a different string; scrub that one too."""
        patcher, _ = _fake_git(returncode=1, stderr=f"fatal: boom {TOKEN}\n")
        with (
            patcher,
            patch.object(ds, "_worker_egress_is_broken", return_value=True),
            patch(CHARGE_BOUNDARY, new_callable=AsyncMock),
        ):
            result = await ds.run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": PRIVATE_REPO,
                    "test_command": "pytest",
                    "github_token": encrypt_token(TOKEN),
                },
                criteria_data={},
                db=_mock_db(),
            )

        assert TOKEN not in json.dumps(result["verification_details"])


# ─── whose fault was it, and does it charge ────────────────────────────────


class TestCredentialFaultAttribution:
    @pytest.mark.parametrize(
        "stderr",
        [
            "remote: Invalid username or password.\nfatal: Authentication failed\n",
            "fatal: could not read Username for 'https://github.com': "
            "terminal prompts disabled\n",
            "remote: Repository not found.\nfatal: repository not found\n",
            "remote: HTTP Basic: Access denied\n",
        ],
    )
    async def test_a_rejected_or_missing_credential_is_the_users_fault(self, stderr):
        """They named the repo and chose whether to authenticate it.

        Both "the token is wrong" and "there is no token and the repo is private"
        are statements about the submitter's input, so this stays a charging
        verdict — with a message that says which of the two it was.
        """
        patcher, _ = _fake_git(returncode=1, stderr=stderr)
        with patcher, patch(CHARGE_BOUNDARY, new_callable=AsyncMock):
            result = await ds.run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={"repo_url": PRIVATE_REPO, "test_command": "pytest"},
                criteria_data={},
                db=_mock_db(),
            )

        assert result["verification_status"] == "failed"
        assert result["verification_details"]["stage"] == "clone"
        assert "no access token was provided" in result["verification_details"]["error"]

    async def test_a_rejected_token_says_the_token_was_the_problem(self):
        """Actionability: "not public" would be wrong advice when they *did* pay
        attention and supply one."""
        patcher, _ = _fake_git(returncode=1, stderr=AUTH_FAILED_STDERR)
        with patcher, patch(CHARGE_BOUNDARY, new_callable=AsyncMock):
            result = await ds.run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": PRIVATE_REPO,
                    "test_command": "pytest",
                    "github_token": encrypt_token(TOKEN),
                },
                criteria_data={},
                db=_mock_db(),
            )

        error = result["verification_details"]["error"]
        assert "token was rejected or lacks access" in error

    async def test_our_broken_egress_is_inconclusive_and_never_charges(self):
        """A clone that never reached the remote is no evidence about the repo."""
        patcher, _ = _fake_git(returncode=1, stderr="fatal: unable to access\n")
        with (
            patcher,
            patch.object(ds, "_worker_egress_is_broken", return_value=True),
            patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge,
            # Wrapped, not replaced: the real contract still runs (and would raise
            # on an inadmissible reason code), but the code it was handed is
            # observable. It is annotated onto the row rather than the returned
            # dict, so a mock session cannot show it.
            patch.object(
                ds,
                "persist_verification_result",
                wraps=ds.persist_verification_result,
            ) as persist,
        ):
            result = await ds.run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={"repo_url": PRIVATE_REPO, "test_command": "pytest"},
                criteria_data={},
                db=_mock_db(),
            )

        charge.assert_not_awaited()
        assert result["verification_status"] == "inconclusive"
        assert result["verification_details"]["stage"] == "upstream"
        assert persist.await_args.kwargs["inconclusive_reason"] == (
            ds.REASON_UPSTREAM_UNAVAILABLE
        )

    async def test_an_auth_failure_is_not_laundered_by_a_broken_egress_probe(self):
        """Ordering guard, and the loophole this whole split could open.

        If the egress probe were consulted first, anyone could dodge a pledge
        whenever our network was merely flaky: an unambiguous *rejection* from the
        remote proves we reached it, so it must win.
        """
        patcher, _ = _fake_git(returncode=1, stderr=AUTH_FAILED_STDERR)
        with (
            patcher,
            patch.object(ds, "_worker_egress_is_broken", return_value=True),
            patch(CHARGE_BOUNDARY, new_callable=AsyncMock),
        ):
            result = await ds.run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": PRIVATE_REPO,
                    "test_command": "pytest",
                    "github_token": encrypt_token(TOKEN),
                },
                criteria_data={},
                db=_mock_db(),
            )

        assert result["verification_status"] == "failed"

    async def test_a_corrupt_stored_token_is_our_fault_not_a_failed_pledge(self):
        """Our key, our storage. A rotated ``TOKEN_ENCRYPTION_KEY`` must not bill
        every in-flight goal."""
        with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
            result = await ds.run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": PRIVATE_REPO,
                    "test_command": "pytest",
                    "github_token": "fernet:not-a-real-ciphertext",
                },
                criteria_data={},
                db=_mock_db(),
            )

        charge.assert_not_awaited()
        assert result["verification_status"] == "inconclusive"
        assert result["verification_details"]["stage"] == "internal"


# ─── submit_proof encrypts before anything is stored ───────────────────────


class TestSubmitProofEncryption:
    def test_token_is_encrypted_before_it_reaches_storage_or_the_broker(self):
        """proof_data is persisted AND serialised into the Celery broker, so the
        plaintext must not survive this call."""
        from app.core.crypto import decrypt_token
        from app.goal_types.dev_sandbox import goal_type

        body = MagicMock()
        body.repo_url = PRIVATE_REPO
        body.branch = "main"
        body.test_command = "pytest"
        body.language = None
        body.env_vars = None
        body.github_token = TOKEN

        out = goal_type.submit_proof({"_body": body}, {})

        stored = out["proof_data"]["github_token"]
        assert stored.startswith("fernet:")
        assert TOKEN not in json.dumps(out)
        assert decrypt_token(stored) == TOKEN
        assert decrypt_token(out["criteria_data"]["github_token"]) == TOKEN

    def test_omitting_the_token_keeps_the_public_path_credential_free(self):
        from app.goal_types.dev_sandbox import goal_type

        body = MagicMock()
        body.repo_url = "https://github.com/octocat/Hello-World.git"
        body.branch = "master"
        body.test_command = "pytest"
        body.language = None
        body.env_vars = None
        body.github_token = None

        out = goal_type.submit_proof({"_body": body}, {})

        assert out["proof_data"]["github_token"] is None
        assert "github_token" not in out["criteria_data"]

    def test_a_blank_submission_does_not_wipe_a_working_credential(self):
        """FILL-only, like every other key this method writes."""
        from app.goal_types.dev_sandbox import goal_type

        body = MagicMock()
        body.repo_url = PRIVATE_REPO
        body.branch = "main"
        body.test_command = "pytest"
        body.language = None
        body.env_vars = None
        body.github_token = None

        existing = encrypt_token(TOKEN)
        out = goal_type.submit_proof({"_body": body}, {"github_token": existing})

        assert out["criteria_data"]["github_token"] == existing


# ─── the sandbox is off the shared bridge ──────────────────────────────────


def _network_client(mock_client):
    """Wire the docker mock so the sandbox network already exists."""
    network = MagicMock()
    network.name = ds.SANDBOX_NETWORK_NAME
    mock_client.networks.get.return_value = network
    container = MagicMock()
    container.attrs = {"NetworkSettings": {"Networks": {ds.SANDBOX_NETWORK_NAME: {}}}}
    container.put_archive.return_value = True
    mock_client.containers.create.return_value = container
    return network, container


@pytest.fixture
def mock_client():
    with patch("app.workers.dev_sandbox.docker.from_env") as from_env:
        client = MagicMock()
        from_env.return_value = client
        yield client


class TestSandboxNetworkIsolation:
    def test_workspace_container_is_not_placed_on_the_default_bridge(
        self, mock_client, tmp_path
    ):
        """The exposure this closes: with no ``network=`` the sandbox joins
        Docker's default bridge, which on a real deployment put it one hop from
        the app's own Postgres — and the install step runs repo-authored code."""
        _network_client(mock_client)
        (tmp_path / "a.py").write_text("x = 1\n")

        ds.DockerSandbox().prepare_workspace(str(tmp_path))

        kwargs = mock_client.containers.create.call_args.kwargs
        assert kwargs["network"] == ds.SANDBOX_NETWORK_NAME

    def test_an_existing_network_is_reused_rather_than_recreated(
        self, mock_client, tmp_path
    ):
        """One named network, not one per verification — nothing to leak or reap."""
        _network_client(mock_client)
        (tmp_path / "a.py").write_text("x = 1\n")

        ds.DockerSandbox().prepare_workspace(str(tmp_path))
        ds.DockerSandbox().prepare_workspace(str(tmp_path))

        mock_client.networks.create.assert_not_called()

    def test_a_concurrent_creator_is_not_an_error(self, mock_client, tmp_path):
        """Two verifications starting together race to create it; the loser re-gets."""
        _, container = _network_client(mock_client)
        network = MagicMock()
        network.name = ds.SANDBOX_NETWORK_NAME
        mock_client.networks.get.side_effect = [
            docker.errors.NotFound("absent"),
            network,
        ]
        mock_client.networks.create.side_effect = docker.errors.APIError(
            "already exists"
        )
        (tmp_path / "a.py").write_text("x = 1\n")

        ds.DockerSandbox().prepare_workspace(str(tmp_path))

        assert (
            mock_client.containers.create.call_args.kwargs["network"]
            == ds.SANDBOX_NETWORK_NAME
        )

    def test_an_unresolvable_network_fails_closed(self, mock_client, tmp_path):
        """Never silently fall back to the default bridge: that *is* the bug."""
        mock_client.networks.get.side_effect = docker.errors.NotFound("absent")
        mock_client.networks.create.side_effect = docker.errors.APIError("denied")
        (tmp_path / "a.py").write_text("x = 1\n")

        with pytest.raises(ds.SandboxSetupError, match="sandbox network"):
            ds.DockerSandbox().prepare_workspace(str(tmp_path))

        mock_client.containers.create.assert_not_called()

    async def test_network_failure_is_inconclusive_and_never_charges(self):
        """It is our infrastructure, so it must not reach the card."""
        patcher, _ = _fake_git()
        with (
            patcher,
            patch.object(
                ds.DockerSandbox,
                "prepare_workspace",
                side_effect=ds.SandboxSetupError("Could not create sandbox network"),
            ),
            patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge,
        ):
            result = await ds.run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://github.com/octocat/Hello-World.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=_mock_db(),
            )

        charge.assert_not_awaited()
        assert result["verification_status"] == "inconclusive"
        assert result["verification_details"]["stage"] == "sandbox"
