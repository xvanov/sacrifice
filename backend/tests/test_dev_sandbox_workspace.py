"""Unit tests for the dev_sandbox workspace plumbing (mocked Docker).

These cover the class of bug where verification could never pass: the repo was
cloned to a host tmpdir but never made visible inside the container, so both the
install and the test command ran against an empty /workspace.
"""

import io
import tarfile
import threading
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
import urllib3
from docker.models.containers import ExecResult

from app.workers.dev_sandbox import SANDBOX_NETWORK_NAME


@pytest.fixture
def mock_client():
    with patch("app.workers.dev_sandbox.docker.from_env") as from_env:
        client = MagicMock()
        from_env.return_value = client
        yield client


def _make_container(
    exit_code: int = 0, stdout: bytes = b"out", stderr: bytes = b""
) -> MagicMock:
    container = MagicMock()
    container.attrs = {"NetworkSettings": {"Networks": {"bridge": {}}}}
    container.put_archive.return_value = True
    container.exec_run.return_value = ExecResult(
        exit_code=exit_code, output=(stdout, stderr)
    )
    return container


def _repo(tmp_path: Path, files: dict[str, str]) -> str:
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return str(tmp_path)


def _capture_put_archive(container: MagicMock) -> dict:
    """Record the upload as it happens — prepare_workspace closes the temp file
    in its finally, so the payload cannot be inspected after the call."""
    captured: dict = {}

    def _capture(target, data):
        captured["target"] = target
        captured["is_stream"] = hasattr(data, "read")
        captured["type"] = type(data).__name__
        data.seek(0)
        captured["bytes"] = data.read()
        return True

    container.put_archive.side_effect = _capture
    return captured


def _mock_db() -> AsyncMock:
    scoped = MagicMock()
    scoped.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=scoped)
    db.commit = AsyncMock()
    return db


# ─── repo actually gets into the container ────────────────────────────


class TestWorkspaceUpload:
    def test_prepare_workspace_puts_repo_contents_into_container(
        self, mock_client, tmp_path
    ):
        """Would have caught the core bug: nothing was ever copied in."""
        from app.workers.dev_sandbox import DockerSandbox

        repo = _repo(tmp_path, {"test_x.py": "def test_ok():\n    assert True\n"})
        container = _make_container()
        captured = _capture_put_archive(container)
        mock_client.containers.create.return_value = container

        sandbox = DockerSandbox()
        sandbox.prepare_workspace(repo)

        container.start.assert_called_once()
        container.put_archive.assert_called_once()
        assert captured["target"] == "/workspace"

        with tarfile.open(fileobj=io.BytesIO(captured["bytes"])) as tf:
            names = tf.getnames()
        assert "./test_x.py" in names, f"repo file missing from upload: {names}"

    def test_upload_is_streamed_from_disk_not_held_in_ram(self, mock_client, tmp_path):
        """A multi-GB tar in io.BytesIO could OOM-kill the worker, which also runs
        beat — taking the deadline sweep and every concurrent verification with it."""
        from app.workers.dev_sandbox import DockerSandbox

        container = _make_container()
        captured = _capture_put_archive(container)
        mock_client.containers.create.return_value = container

        DockerSandbox().prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))

        assert captured["is_stream"] is True, "payload must be a file object, not bytes"
        assert captured["type"] != "bytes"

    def test_git_directory_is_excluded_from_the_upload(self, mock_client, tmp_path):
        """`.git` was ~94% of the entries on a trivial repo and nothing in the
        container reads it, so a depth-1 clone doubled the payload for nothing."""
        from app.workers.dev_sandbox import DockerSandbox

        repo = _repo(
            tmp_path,
            {
                "test_x.py": "def test_ok():\n    assert True\n",
                ".git/HEAD": "ref: refs/heads/main\n",
                ".git/objects/ab/cdef": "binary-ish\n",
            },
        )
        container = _make_container()
        captured = _capture_put_archive(container)
        mock_client.containers.create.return_value = container

        DockerSandbox().prepare_workspace(repo)

        with tarfile.open(fileobj=io.BytesIO(captured["bytes"])) as tf:
            names = tf.getnames()
        assert "./test_x.py" in names
        assert not [n for n in names if ".git" in n], f"git history uploaded: {names}"

    def test_oversized_repo_is_a_sandbox_fault_not_a_test_failure(
        self, mock_client, tmp_path
    ):
        """The cap must fail as infrastructure — a user-facing test failure charges."""
        from app.workers.dev_sandbox import DockerSandbox, SandboxSetupError

        mock_client.containers.create.return_value = _make_container()
        repo = _repo(tmp_path, {"big.py": "x" * 4096})

        with patch("app.workers.dev_sandbox.MAX_WORKSPACE_BYTES", 1024):
            with pytest.raises(SandboxSetupError, match="exceeds the .* sandbox limit"):
                DockerSandbox().prepare_workspace(repo)

        mock_client.containers.create.assert_not_called()

    def test_prepare_workspace_keeps_container_security_options(
        self, mock_client, tmp_path
    ):
        from app.workers.dev_sandbox import DockerSandbox

        mock_client.containers.create.return_value = _make_container()

        sandbox = DockerSandbox(memory_limit="512m", cpu_limit=0.5, timeout=42)
        sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))

        kwargs = mock_client.containers.create.call_args.kwargs
        assert kwargs["privileged"] is False
        assert "no-new-privileges:true" in kwargs["security_opt"]
        assert kwargs["mem_limit"] == "512m"
        assert kwargs["nano_cpus"] == int(0.5 * 1e9)
        assert kwargs["pids_limit"] == 512
        assert "volumes" not in kwargs, (
            "must copy the repo in, not bind-mount a host path"
        )

    def test_workspace_container_drops_all_capabilities(self, mock_client, tmp_path):
        """The container runs as uid 0, so the default capability set is real
        privilege; no-new-privileges does not touch it and pip needs none of it."""
        from app.workers.dev_sandbox import DockerSandbox

        mock_client.containers.create.return_value = _make_container()

        DockerSandbox().prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))

        assert mock_client.containers.create.call_args.kwargs["cap_drop"] == ["ALL"]

    def test_ephemeral_container_drops_capabilities_and_caps_pids(self, mock_client):
        """The same hardening on the other container path."""
        from app.workers.dev_sandbox import DockerSandbox

        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.side_effect = [b"", b""]
        mock_client.containers.run.return_value = container

        DockerSandbox().run_command(["true"])

        kwargs = mock_client.containers.run.call_args.kwargs
        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["pids_limit"] == 512

    def test_prepare_workspace_upload_failure_removes_container(
        self, mock_client, tmp_path
    ):
        """No leaked container when put_archive fails."""
        from app.workers.dev_sandbox import DockerSandbox, SandboxSetupError

        container = _make_container()
        container.put_archive.side_effect = OSError("boom")
        mock_client.containers.create.return_value = container

        sandbox = DockerSandbox()
        with pytest.raises(SandboxSetupError):
            sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))

        container.remove.assert_called_once_with(force=True)
        assert sandbox.workspace_container is None


# ─── commands run inside the prepared workspace ───────────────────────


class TestWorkspaceExec:
    def test_run_command_execs_in_prepared_container(self, mock_client, tmp_path):
        """Would have caught run_command always starting a fresh, empty container."""
        from app.workers.dev_sandbox import DockerSandbox

        container = _make_container(exit_code=0, stdout=b"1 passed", stderr=b"warn")
        mock_client.containers.create.return_value = container

        sandbox = DockerSandbox()
        sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))
        result = sandbox.run_command(
            ["pytest", "-v"], workdir="/workspace", env={"CI": "1"}
        )

        mock_client.containers.run.assert_not_called()
        container.exec_run.assert_called_once()
        args, kwargs = container.exec_run.call_args
        assert args[0] == ["pytest", "-v"]
        assert kwargs["workdir"] == "/workspace"
        assert kwargs["environment"] == {"CI": "1"}
        assert kwargs["demux"] is True

        assert result.exit_code == 0
        assert result.stdout == "1 passed"
        assert result.stderr == "warn"
        assert result.success is True

    def test_install_and_test_share_one_container(self, mock_client, tmp_path):
        """Installed dependencies must survive into the test step."""
        from app.workers.dev_sandbox import DockerSandbox

        container = _make_container()
        mock_client.containers.create.return_value = container

        sandbox = DockerSandbox()
        sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))
        sandbox.run_command(["pip", "install", "-r", "requirements.txt"])
        sandbox.run_command(["pytest"])

        assert mock_client.containers.create.call_count == 1
        assert container.exec_run.call_count == 2

    def test_nonzero_exec_exit_code_is_a_failure(self, mock_client, tmp_path):
        from app.workers.dev_sandbox import DockerSandbox

        container = _make_container(exit_code=1, stdout=b"", stderr=b"FAILED")
        mock_client.containers.create.return_value = container

        sandbox = DockerSandbox()
        sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))
        result = sandbox.run_command(["pytest"])

        assert result.exit_code == 1
        assert result.success is False
        assert result.timed_out is False


# ─── network posture ──────────────────────────────────────────────────


class TestNetworkPosture:
    def test_isolate_network_disconnects_every_attached_network(
        self, mock_client, tmp_path
    ):
        from app.workers.dev_sandbox import DockerSandbox

        container = _make_container()
        container.attrs = {"NetworkSettings": {"Networks": {"bridge": {}, "extra": {}}}}
        mock_client.containers.create.return_value = container
        bridge, extra = MagicMock(), MagicMock()
        # prepare_workspace now resolves the dedicated sandbox network before it
        # creates the container, so that lookup has to succeed here too.
        mock_client.networks.get.side_effect = lambda name: {
            "bridge": bridge,
            "extra": extra,
            SANDBOX_NETWORK_NAME: MagicMock(name=SANDBOX_NETWORK_NAME),
        }[name]

        sandbox = DockerSandbox()
        sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))
        sandbox.isolate_network()

        bridge.disconnect.assert_called_once_with(container)
        extra.disconnect.assert_called_once_with(container)

    def test_isolate_network_failure_is_fail_closed(self, mock_client, tmp_path):
        """A failed disconnect must raise, never run user tests with live egress."""
        from app.workers.dev_sandbox import DockerSandbox, SandboxSetupError

        container = _make_container()
        mock_client.containers.create.return_value = container
        mock_client.networks.get.return_value.disconnect.side_effect = Exception("nope")

        sandbox = DockerSandbox()
        sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))
        with pytest.raises(SandboxSetupError, match="Could not detach"):
            sandbox.isolate_network()

    async def test_tests_run_only_after_network_is_cut(self, mock_client, tmp_path):
        """Ordering contract: install (networked) -> disconnect -> user tests."""
        from app.workers.dev_sandbox import run_dev_sandbox_verification

        repo = _repo(
            tmp_path,
            {
                "requirements.txt": "pytest\n",
                "test_x.py": "def test_ok():\n    assert True\n",
            },
        )
        container = _make_container()
        mock_client.containers.create.return_value = container

        events: list[str] = []
        container.exec_run.side_effect = lambda cmd, **kw: (
            events.append(f"exec:{cmd[0]}"),
            ExecResult(exit_code=0, output=(b"ok", b"")),
        )[1]
        mock_client.networks.get.return_value.disconnect.side_effect = lambda c: (
            events.append("disconnect")
        )

        with (
            patch("app.workers.dev_sandbox.tempfile.mkdtemp", return_value=repo),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.clone_repo", return_value=None),
            patch(
                "app.workers.dev_sandbox.judge_code_authenticity",
                new_callable=AsyncMock,
                return_value={"authentic": True, "reasoning": "ok"},
            ),
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "pytest",
                },
                criteria_data={"goal_description": "ship it"},
                db=_mock_db(),
            )

        # `exec:sh` is the toolchain probe (`command -v pip`) that runs first.
        assert events == ["exec:sh", "exec:pip", "disconnect", "exec:pytest"], events
        assert result["verification_status"] == "verified"
        assert result["verification_details"]["tests_passed"] is True


# ─── timeouts ─────────────────────────────────────────────────────────


class TestTimeouts:
    def test_ephemeral_read_timeout_is_reported_as_timed_out(self, mock_client):
        """docker-py raises requests.ConnectionError wrapping urllib3's
        ReadTimeoutError, not APIError, so the original
        `except docker.errors.APIError` made timed_out unreachable. The wrapped
        cause — not the outer class — is what identifies a real deadline
        (verified against docker-py 7.1.0 / Docker 29.4.3)."""
        from app.workers.dev_sandbox import DockerSandbox

        container = MagicMock()
        container.wait.side_effect = requests.exceptions.ConnectionError(
            urllib3.exceptions.ReadTimeoutError(None, "/", "Read timed out.")
        )
        mock_client.containers.run.return_value = container

        sandbox = DockerSandbox(timeout=1)
        result = sandbox.run_command(["sleep", "60"])

        assert result.timed_out is True
        assert result.success is False
        assert result.exit_code == -1
        container.kill.assert_called_once()
        container.remove.assert_called_once_with(force=True)

    def test_ephemeral_daemon_fault_is_infrastructure_not_a_timeout(self, mock_client):
        """A dead socket is also a bare ConnectionError. Calling it `timed_out`
        would produce a `failed` verdict — and charge the card — for our outage."""
        from app.workers.dev_sandbox import DockerSandbox, SandboxSetupError

        container = MagicMock()
        container.wait.side_effect = requests.exceptions.ConnectionError(
            "('Connection aborted.', FileNotFoundError(2, 'No such file or directory'))"
        )
        mock_client.containers.run.return_value = container

        sandbox = DockerSandbox()
        with pytest.raises(SandboxSetupError, match="transport failed"):
            sandbox.run_command(["pytest"])
        container.remove.assert_called_once_with(force=True)

    def test_workspace_daemon_fault_is_infrastructure_not_a_timeout(
        self, mock_client, tmp_path
    ):
        from app.workers.dev_sandbox import DockerSandbox, SandboxSetupError

        container = _make_container()
        container.exec_run.side_effect = requests.exceptions.ConnectionError(
            "('Connection aborted.', RemoteDisconnected('Remote end closed connection'))"
        )
        mock_client.containers.create.return_value = container

        sandbox = DockerSandbox()
        sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))
        with pytest.raises(SandboxSetupError, match="transport failed"):
            sandbox.run_command(["pytest"])

    def test_ephemeral_non_transport_error_still_raises(self, mock_client):
        """The transport carve-out must not swallow ordinary bugs."""
        from app.workers.dev_sandbox import DockerSandbox

        container = MagicMock()
        container.wait.side_effect = ValueError("something else broke")
        mock_client.containers.run.return_value = container

        sandbox = DockerSandbox()
        with pytest.raises(ValueError, match="something else broke"):
            sandbox.run_command(["true"])
        container.remove.assert_called_once_with(force=True)

    def test_idle_backstop_outlives_every_sequential_step(self, mock_client, tmp_path):
        """Pins the budget arithmetic. `sleep(timeout + 60)` was a PER-STEP budget
        while install and test each get `timeout`, so two in-budget steps could
        outlive the container: Docker then SIGKILLed the running exec (137, no
        output), which read as a failed test run and charged the user."""
        from app.workers.dev_sandbox import IDLE_COMMAND, DockerSandbox

        mock_client.containers.create.return_value = _make_container()

        sandbox = DockerSandbox(timeout=600)
        sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))

        command = mock_client.containers.create.call_args.kwargs["command"]
        assert command == IDLE_COMMAND
        # The backstop must not be derived from a single step's deadline.
        assert str(sandbox.timeout) not in command[-1]
        assert str(sandbox.timeout + 60) not in command[-1]
        assert int(command[-1]) > 10 * sandbox.timeout

    def test_workspace_exec_timeout_kills_container(self, mock_client, tmp_path):
        """exec_run has no timeout of its own — the host-side kill is the enforcement."""
        from app.workers.dev_sandbox import DockerSandbox

        release = threading.Event()
        container = _make_container()
        container.exec_run.side_effect = lambda *a, **kw: (
            release.wait(30),
            ExecResult(exit_code=0, output=(b"", b"")),
        )[1]
        mock_client.containers.create.return_value = container

        sandbox = DockerSandbox(timeout=1)
        try:
            sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))
            result = sandbox.run_command(["sleep", "600"])
        finally:
            release.set()

        assert result.timed_out is True
        assert result.success is False
        container.kill.assert_called_once()


# ─── cleanup / no leaked containers ───────────────────────────────────


class TestCleanup:
    def test_close_removes_workspace_container(self, mock_client, tmp_path):
        from app.workers.dev_sandbox import DockerSandbox

        container = _make_container()
        mock_client.containers.create.return_value = container

        sandbox = DockerSandbox()
        sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))
        sandbox.run_command(["pytest"])
        sandbox.close()

        container.remove.assert_called_once_with(force=True)
        assert sandbox.workspace_container is None

    def test_context_manager_removes_container_on_exception(
        self, mock_client, tmp_path
    ):
        from app.workers.dev_sandbox import DockerSandbox

        container = _make_container()
        container.exec_run.side_effect = RuntimeError("exec exploded")
        mock_client.containers.create.return_value = container

        with pytest.raises(RuntimeError, match="exec exploded"):
            with DockerSandbox() as sandbox:
                sandbox.prepare_workspace(_repo(tmp_path, {"a.py": "x = 1\n"}))
                sandbox.run_command(["pytest"])

        container.remove.assert_called_once_with(force=True)

    async def test_verification_removes_container_when_exec_raises(
        self, mock_client, tmp_path
    ):
        from app.workers.dev_sandbox import run_dev_sandbox_verification

        container = _make_container()
        container.exec_run.side_effect = RuntimeError("exec exploded")
        mock_client.containers.create.return_value = container

        with (
            patch(
                "app.workers.dev_sandbox.tempfile.mkdtemp", return_value=str(tmp_path)
            ),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.clone_repo", return_value=None),
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=_mock_db(),
            )

        assert result["verification_status"] == "inconclusive"
        container.remove.assert_called_once_with(force=True)


# ─── stage reporting ──────────────────────────────────────────────────


def _install_flow(tmp_path, results_by_exe: dict):
    """Drive the install path with a scripted result per executable."""
    from app.workers.dev_sandbox import DockerSandbox, SandboxResult

    repo = _repo(
        tmp_path,
        {
            "requirements.txt": "pytest\n",
            "test_x.py": "def test_ok():\n    assert True\n",
        },
    )
    calls: list[list[str]] = []

    def fake_run_command(self, command, workdir="/workspace", env=None):
        calls.append(command)
        scripted = results_by_exe.get(command[0], SandboxResult(0, "", ""))
        return scripted

    return (
        repo,
        calls,
        [
            patch("app.workers.dev_sandbox.tempfile.mkdtemp", return_value=repo),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.clone_repo", return_value=None),
            patch.object(DockerSandbox, "prepare_workspace", return_value=None),
            patch.object(DockerSandbox, "isolate_network", return_value=None),
            patch.object(DockerSandbox, "close", return_value=None),
            patch.object(DockerSandbox, "run_command", fake_run_command),
        ],
    )


class TestStageReporting:
    async def test_install_timeout_with_working_egress_charges_at_install_stage(
        self, mock_client, tmp_path
    ):
        """Previously an install timeout fell through and ran the test command
        anyway. With egress proven healthy, a slow install is the repo's problem."""
        from app.workers.dev_sandbox import SandboxResult, run_dev_sandbox_verification

        repo, calls, patches = _install_flow(
            tmp_path,
            {
                "pip": SandboxResult(-1, "", "", timed_out=True),
                "python": SandboxResult(0, "", ""),  # egress probe: healthy
            },
        )
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=_mock_db(),
            )

        assert result["verification_status"] == "failed"
        assert result["verification_details"]["stage"] == "install"
        assert result["verification_details"]["timed_out"] is True
        assert ["pytest"] not in calls, (
            f"test command must not run after an install timeout: {calls}"
        )

    async def test_install_failure_with_broken_egress_is_inconclusive(
        self, mock_client, tmp_path
    ):
        """PyPI unreachable from the sandbox: the install never had a chance, so
        this is our egress rather than broken requirements — and must not charge."""
        from app.workers.dev_sandbox import SandboxResult, run_dev_sandbox_verification

        repo, calls, patches = _install_flow(
            tmp_path,
            {
                "pip": SandboxResult(1, "", "Could not find a version"),
                "python": SandboxResult(1, "", "Network is unreachable"),  # probe fails
            },
        )
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=_mock_db(),
            )

        assert result["verification_status"] == "inconclusive"
        assert result["verification_details"]["stage"] == "sandbox"
        assert "unreachable" in result["verification_details"]["inconclusive_detail"]

    async def test_install_failure_with_healthy_egress_is_the_users_failure(
        self, mock_client, tmp_path
    ):
        """A version conflict with egress proven healthy is theirs: it charges."""
        from app.workers.dev_sandbox import SandboxResult, run_dev_sandbox_verification

        repo, calls, patches = _install_flow(
            tmp_path,
            {
                "pip": SandboxResult(1, "", "ERROR: ResolutionImpossible"),
                "python": SandboxResult(0, "", ""),  # egress healthy
            },
        )
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=_mock_db(),
            )

        assert result["verification_status"] == "failed"
        assert result["verification_details"]["stage"] == "install"
        assert "ResolutionImpossible" in result["verification_details"]["error"]

    async def test_missing_toolchain_is_inconclusive_not_a_charge(
        self, mock_client, tmp_path
    ):
        """We accept node/go/rust goals but the sandbox image is Python-only, so
        `npm install` can only ever fail. Billing for that is billing for our gap."""
        from app.workers.dev_sandbox import SandboxResult, run_dev_sandbox_verification

        repo = _repo(tmp_path, {"package.json": '{"name":"x"}\n'})
        calls: list[list[str]] = []

        def fake_run_command(self, command, workdir="/workspace", env=None):
            calls.append(command)
            if command[0] == "sh":  # `command -v npm` -> not found
                return SandboxResult(1, "", "npm: not found")
            return SandboxResult(0, "", "")

        from app.workers.dev_sandbox import DockerSandbox

        with (
            patch("app.workers.dev_sandbox.tempfile.mkdtemp", return_value=repo),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.clone_repo", return_value=None),
            patch.object(DockerSandbox, "prepare_workspace", return_value=None),
            patch.object(DockerSandbox, "isolate_network", return_value=None),
            patch.object(DockerSandbox, "close", return_value=None),
            patch.object(DockerSandbox, "run_command", fake_run_command),
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "npm test",
                },
                criteria_data={},
                db=_mock_db(),
            )

        assert result["verification_status"] == "inconclusive"
        assert result["verification_details"]["stage"] == "criteria"
        assert "npm" in result["verification_details"]["inconclusive_detail"]
        assert ["npm", "install"] not in calls

    async def test_sandbox_setup_failure_reports_sandbox_stage_not_clone(
        self, mock_client, tmp_path
    ):
        from app.workers.dev_sandbox import (
            DockerSandbox,
            SandboxSetupError,
            run_dev_sandbox_verification,
        )

        with (
            patch(
                "app.workers.dev_sandbox.tempfile.mkdtemp", return_value=str(tmp_path)
            ),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.clone_repo", return_value=None),
            patch.object(
                DockerSandbox,
                "prepare_workspace",
                side_effect=SandboxSetupError("daemon unreachable"),
            ),
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=_mock_db(),
            )

        assert result["verification_status"] == "inconclusive"
        assert result["verification_details"]["stage"] == "sandbox"
        assert (
            "daemon unreachable"
            in result["verification_details"]["inconclusive_detail"]
        )

    async def test_clone_timeout_reports_clone_stage(self, tmp_path):
        """A clone that hangs used to escape as `stage: unknown` with an opaque message."""
        import subprocess

        from app.workers.dev_sandbox import run_dev_sandbox_verification

        with (
            patch(
                "app.workers.dev_sandbox.tempfile.mkdtemp", return_value=str(tmp_path)
            ),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch(
                "app.workers.dev_sandbox.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="git clone", timeout=120),
            ),
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/slow.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=_mock_db(),
            )

        assert result["verification_status"] == "failed"
        assert result["verification_details"]["stage"] == "clone"
        assert "timed out" in result["verification_details"]["error"]

    async def test_silent_sigkill_is_a_sandbox_fault_and_does_not_charge(
        self, mock_client, tmp_path
    ):
        """The exact production shape of the backstop bug: exit 137 with empty
        output. Reported as `stage: test` it charged the user for passing code."""
        from app.workers.dev_sandbox import run_dev_sandbox_verification

        container = _make_container(exit_code=137, stdout=b"", stderr=b"")
        mock_client.containers.create.return_value = container
        db = _mock_db()

        with (
            patch(
                "app.workers.dev_sandbox.tempfile.mkdtemp", return_value=str(tmp_path)
            ),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.clone_repo", return_value=None),
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=db,
            )

        details = result["verification_details"]
        assert result["verification_status"] == "inconclusive"
        assert details["stage"] == "sandbox", (
            "a silent SIGKILL is our fault, not a test failure"
        )
        assert "137" in details["inconclusive_detail"]
        container.remove.assert_called_once_with(force=True)

    async def test_sigkill_with_output_is_still_a_test_failure(
        self, mock_client, tmp_path
    ):
        """Guard the other direction: a suite the user OOM-killed produces output
        first, and must keep its `stage: test` failure verdict."""
        from app.workers.dev_sandbox import run_dev_sandbox_verification

        container = _make_container(
            exit_code=137, stdout=b"collected 3 items", stderr=b"Killed"
        )
        mock_client.containers.create.return_value = container
        db = _mock_db()

        with (
            patch(
                "app.workers.dev_sandbox.tempfile.mkdtemp", return_value=str(tmp_path)
            ),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.clone_repo", return_value=None),
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=db,
            )

        details = result["verification_details"]
        assert details["stage"] == "test"
        assert details["tests_passed"] is False
        db.commit.assert_awaited()

    @pytest.mark.parametrize(
        "bad_command, reason",
        [
            ("", "empty"),
            ("   ", "whitespace only"),
            ('pytest "unbalanced', "unbalanced quote"),
        ],
    )
    async def test_unusable_test_command_is_validation_and_does_not_charge(
        self, mock_client, tmp_path, bad_command, reason
    ):
        """Previously landed in the generic handler as `stage: unknown` + charge."""
        from app.workers.dev_sandbox import run_dev_sandbox_verification

        db = _mock_db()
        with patch("app.workers.dev_sandbox.clone_repo") as clone:
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": bad_command,
                },
                criteria_data={},
                db=db,
            )

        details = result["verification_details"]
        assert result["verification_status"] == "failed", (
            "a command the submitter typed is their failure and should charge"
        )
        assert details["stage"] == "validation", reason
        assert details["error"]
        clone.assert_not_called()  # rejected before any work
        mock_client.containers.create.assert_not_called()

    async def test_unusable_stored_test_command_is_ours_not_the_users(
        self, mock_client, tmp_path
    ):
        """Same broken string, different author: a test_command that came from the
        stored criteria was written by our own goal-creation flow, so charging the
        user for it bills them for our bug."""
        from app.workers.dev_sandbox import run_dev_sandbox_verification

        with patch("app.workers.dev_sandbox.clone_repo") as clone:
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={"repo_url": "https://example.com/r.git"},
                criteria_data={"test_command": 'pytest "unbalanced'},
                db=_mock_db(),
            )

        details = result["verification_details"]
        assert result["verification_status"] == "inconclusive"
        assert details["stage"] == "criteria"
        assert "unusable" in details["inconclusive_detail"]
        clone.assert_not_called()

    async def test_failed_status_on_an_our_fault_stage_is_converted_not_charged(
        self, mock_client, tmp_path
    ):
        """Defense in depth in _persist_result: even a routing slip cannot bill."""
        from app.workers.dev_sandbox import _persist_result

        db = _mock_db()
        with patch(
            "app.workers.dev_sandbox.persist_verification_result",
            new_callable=AsyncMock,
        ) as persist:
            await _persist_result(
                db, uuid.uuid4(), uuid.uuid4(), "failed", {"stage": "sandbox"}
            )
            assert persist.await_args.args[3] == "inconclusive"
            assert persist.await_args.kwargs["inconclusive_reason"] == "internal_error"

            await _persist_result(
                db, uuid.uuid4(), uuid.uuid4(), "failed", {"stage": "test"}
            )
            assert persist.await_args.args[3] == "failed"
            assert persist.await_args.kwargs["inconclusive_reason"] is None

    async def test_unexpected_crash_is_inconclusive_not_a_charge(
        self, mock_client, tmp_path
    ):
        """The old `stage: unknown` catch-all charged the user for our exceptions."""
        from app.workers.dev_sandbox import run_dev_sandbox_verification

        with (
            patch(
                "app.workers.dev_sandbox.tempfile.mkdtemp", return_value=str(tmp_path)
            ),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.clone_repo", return_value=None),
            patch(
                "app.workers.dev_sandbox.detect_language",
                side_effect=TypeError("bug in our own code"),
            ),
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=_mock_db(),
            )

        assert result["verification_status"] == "inconclusive"
        assert result["verification_details"]["stage"] == "internal"

    def test_submit_proof_rejects_an_unusable_test_command(self):
        """Rejected at submission time (ValueError -> HTTP 400) so the worker
        never runs a verification that could only end in a charge."""
        from types import SimpleNamespace

        from app.goal_types.dev_sandbox import goal_type

        body = SimpleNamespace(
            repo_url="https://example.com/r.git",
            branch="main",
            test_command='pytest "unbalanced',
            language=None,
            env_vars=None,
        )
        with pytest.raises(ValueError, match="test_command could not be parsed"):
            goal_type.submit_proof({"_body": body}, {})

    def test_criteria_schema_requires_a_non_empty_test_command(self):
        from app.goal_types.dev_sandbox import goal_type

        props = goal_type.criteria_schema["properties"]
        assert props["test_command"]["minLength"] == 1
        assert props["repo_url"]["minLength"] == 1

    def test_submit_proof_accepts_a_valid_test_command(self):
        from types import SimpleNamespace

        from app.goal_types.dev_sandbox import goal_type

        body = SimpleNamespace(
            repo_url="https://example.com/r.git",
            branch="main",
            test_command='pytest -k "my test"',
            language=None,
            env_vars=None,
        )
        prepared = goal_type.submit_proof({"_body": body}, {})
        assert prepared["proof_data"]["test_command"] == 'pytest -k "my test"'

    async def test_clone_failure_reports_clone_stage(self, tmp_path):
        from app.workers.dev_sandbox import run_dev_sandbox_verification

        failed = MagicMock(returncode=128, stderr="fatal: repository not found")
        with (
            patch(
                "app.workers.dev_sandbox.tempfile.mkdtemp", return_value=str(tmp_path)
            ),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.subprocess.run", return_value=failed),
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/private.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=_mock_db(),
            )

        assert result["verification_details"]["stage"] == "clone"
        assert "repository not found" in result["verification_details"]["error"]


# ─── the charge boundary ──────────────────────────────────────────────
#
# `persist_verification_result` bills a real card on a `failed` status
# (PaymentIntent with confirm=True), so these tests run the REAL persistence
# layer against a mocked session and assert on `process_charge_for_goal`
# itself — the only assertion that proves nobody's card was touched. Both
# directions, because a charge that never fires is also a broken product.


def _db_with_rows():
    """A session whose selects return a submission then a goal, so the real
    persistence layer reaches its charge decision."""
    submission = MagicMock()
    submission.verification_status = "pending"
    submission.verification_details = None
    submission.dispatch_attempts = 0

    goal = MagicMock()
    goal.id = uuid.uuid4()
    goal.user_id = uuid.uuid4()
    goal.status = "active"
    goal.title = "Ship the repo"

    rows = [submission, goal, goal, goal]

    def execute(*_args, **_kwargs):
        result = MagicMock()
        result.scalar_one_or_none.return_value = rows.pop(0) if rows else goal
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db, goal


class TestChargeBoundary:
    async def test_infrastructure_fault_does_not_reach_the_charge(
        self, mock_client, tmp_path
    ):
        """A silent SIGKILL from our own backstop must not bill anyone."""
        from app.workers.dev_sandbox import run_dev_sandbox_verification

        container = _make_container(exit_code=137, stdout=b"", stderr=b"")
        mock_client.containers.create.return_value = container
        db, _goal = _db_with_rows()

        with (
            patch(
                "app.workers.dev_sandbox.tempfile.mkdtemp", return_value=str(tmp_path)
            ),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.clone_repo", return_value=None),
            patch(
                "app.services.notification.create_notification", new_callable=AsyncMock
            ),
            patch(
                "app.workers.payments.process_charge_for_goal", new_callable=AsyncMock
            ) as charge,
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=db,
            )

        assert result["verification_status"] == "inconclusive"
        charge.assert_not_called()

    async def test_a_red_test_suite_does_not_charge_while_goal_is_still_active(
        self, mock_client, tmp_path
    ):
        """The other direction: a real failure is still billed — eventually.

        With the goal still ``active`` (time left on the deadline), the charge
        is deferred to the deadline sweep rather than dispatched on this call —
        see verification_result.py's "A real failure before the deadline is
        not yet a verdict on the goal". ``test_charge_on_failure.py`` and
        ``test_deadline_worker.py`` cover the sweep actually collecting it.
        """
        from app.workers.dev_sandbox import run_dev_sandbox_verification

        container = _make_container(
            exit_code=1, stdout=b"1 failed", stderr=b"AssertionError"
        )
        mock_client.containers.create.return_value = container
        db, goal = _db_with_rows()

        with (
            patch(
                "app.workers.dev_sandbox.tempfile.mkdtemp", return_value=str(tmp_path)
            ),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.clone_repo", return_value=None),
            patch(
                "app.services.verification_result.notify_goal_resolution",
                new_callable=AsyncMock,
            ),
            patch(
                "app.workers.payments.process_charge_for_goal", new_callable=AsyncMock
            ) as charge,
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/r.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=db,
            )

        assert result["verification_status"] == "failed"
        charge.assert_not_awaited()

    async def test_clone_failure_does_not_charge_while_goal_is_still_active(
        self, mock_client, tmp_path
    ):
        """A repo or branch that does not exist is the user's input, so it
        bills eventually — but not while the goal is still active and the
        owner could still fix the URL and resubmit."""
        from app.workers.dev_sandbox import run_dev_sandbox_verification

        db, goal = _db_with_rows()
        failed = MagicMock(returncode=128, stderr="fatal: repository not found")

        with (
            patch(
                "app.workers.dev_sandbox.tempfile.mkdtemp", return_value=str(tmp_path)
            ),
            patch("app.workers.dev_sandbox.shutil.rmtree"),
            patch("app.workers.dev_sandbox.subprocess.run", return_value=failed),
            patch(
                "app.services.verification_result.notify_goal_resolution",
                new_callable=AsyncMock,
            ),
            patch(
                "app.workers.payments.process_charge_for_goal", new_callable=AsyncMock
            ) as charge,
        ):
            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://example.com/nope.git",
                    "test_command": "pytest",
                },
                criteria_data={},
                db=db,
            )

        assert result["verification_status"] == "failed"
        charge.assert_not_awaited()
