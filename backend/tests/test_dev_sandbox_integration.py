"""End-to-end dev_sandbox verification against a real Docker daemon.

Skipped when no usable daemon/image is available so a CI box without Docker
stays green. The LLM authenticity judge is monkeypatched (it would otherwise
call Azure), but the production combined verdict — tests_passed AND authentic —
is left untouched.
"""

import socket
import subprocess
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SANDBOX_IMAGE = "python:3.11-slim"


def _docker_reason() -> str | None:
    try:
        import docker

        client = docker.from_env()
        client.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        return f"docker daemon unavailable: {exc}"
    try:
        client.images.get(SANDBOX_IMAGE)
    except Exception:  # pragma: no cover - environment dependent
        return f"{SANDBOX_IMAGE} not pulled locally"
    return None


_DOCKER_REASON = _docker_reason()
requires_docker = pytest.mark.skipif(
    _DOCKER_REASON is not None, reason=str(_DOCKER_REASON)
)


def _internet() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=5).close()
        return True
    except OSError:  # pragma: no cover - environment dependent
        return False


requires_internet = pytest.mark.skipif(
    not _internet(), reason="no outbound network for dependency install"
)


def _git_repo(path: Path, files: dict[str, str]) -> str:
    """Create a committed git repo on `main` and return a clonable URL."""
    path.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (path / name).write_text(body)
    env = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": "/usr/bin:/bin",
    }
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=path, check=True, capture_output=True, env=env
    )
    run("init", "-b", "main")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    run("add", "-A")
    run("commit", "-m", "initial")
    return f"file://{path}"


def _mock_db() -> AsyncMock:
    scoped = MagicMock()
    scoped.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=scoped)
    db.commit = AsyncMock()
    return db


@pytest.fixture
def no_leaked_containers():
    """Fail if verification leaves any container behind."""
    import docker

    client = docker.from_env()
    before = {c.id for c in client.containers.list(all=True)}
    yield
    after = {c.id for c in client.containers.list(all=True)}
    assert not (after - before), f"leaked containers: {after - before}"


async def _verify(repo_url: str, test_command: str, authentic: bool = True) -> dict:
    from app.workers.dev_sandbox import run_dev_sandbox_verification

    with patch(
        "app.workers.dev_sandbox.judge_code_authenticity",
        new_callable=AsyncMock,
        return_value={"authentic": authentic, "reasoning": "stubbed judge"},
    ):
        return await run_dev_sandbox_verification(
            goal_id=uuid.uuid4(),
            submission_id=uuid.uuid4(),
            proof_data={
                "repo_url": repo_url,
                "branch": "main",
                "test_command": test_command,
            },
            criteria_data={"goal_description": "Ship a repo whose tests pass"},
            db=_mock_db(),
        )


@requires_docker
class TestRealDockerVerification:
    @requires_internet
    async def test_passing_suite_with_installed_dependency_verifies(
        self, tmp_path, no_leaked_containers
    ):
        """The whole point: clone -> deps installed -> user's pytest suite passes.

        Fails on the old code twice over — /workspace was empty, and the install
        step could not reach PyPI with network_disabled=True.
        """
        repo = _git_repo(
            tmp_path / "repo",
            {
                "requirements.txt": "pytest==8.3.2\n",
                "test_x.py": "def test_ok():\n    assert 1 + 1 == 2\n",
            },
        )

        result = await _verify(repo, "python -m pytest -v")

        details = result["verification_details"]
        assert result["verification_status"] == "verified", details
        assert details["tests_passed"] is True
        assert details["exit_code"] == 0
        assert details["stage"] == "test"
        assert details["timed_out"] is False
        assert details["language"] == "python"
        assert "test_ok" in details["stdout"]
        assert details["authentic"] is True

    async def test_failing_suite_returns_failed(self, tmp_path, no_leaked_containers):
        """A red suite must not be verified — and must still see the repo
        (on the old code this failed for the wrong reason: no such file)."""
        repo = _git_repo(
            tmp_path / "repo",
            {"run_tests.py": "assert 1 == 2, 'deliberate failure'\n"},
        )

        result = await _verify(repo, "python run_tests.py")

        details = result["verification_details"]
        assert result["verification_status"] == "failed"
        assert details["tests_passed"] is False
        assert details["exit_code"] != 0
        assert details["stage"] == "test"
        assert "deliberate failure" in details["stderr"]

    async def test_repo_files_are_visible_to_the_test_command(
        self, tmp_path, no_leaked_containers
    ):
        """Directly asserts the cloned tree is inside the container."""
        repo = _git_repo(
            tmp_path / "repo",
            {
                "marker.txt": "sacrifice-marker\n",
                "run_tests.py": (
                    "import pathlib\n"
                    "assert pathlib.Path('marker.txt').read_text().strip() == 'sacrifice-marker'\n"
                    "print('MARKER_FOUND')\n"
                ),
            },
        )

        result = await _verify(repo, "python run_tests.py")

        details = result["verification_details"]
        assert result["verification_status"] == "verified", details
        assert "MARKER_FOUND" in details["stdout"]

    async def test_user_test_code_has_no_network_egress(
        self, tmp_path, no_leaked_containers
    ):
        """The security posture: submitted test code runs with the network cut.

        The suite passes only if outbound TCP fails inside the container.
        """
        repo = _git_repo(
            tmp_path / "repo",
            {
                "run_tests.py": (
                    "import socket\n"
                    "try:\n"
                    "    socket.create_connection(('1.1.1.1', 443), timeout=5).close()\n"
                    "except OSError as exc:\n"
                    "    print('EGRESS_BLOCKED', exc)\n"
                    "else:\n"
                    "    raise SystemExit('EGRESS_REACHABLE')\n"
                ),
            },
        )

        result = await _verify(repo, "python run_tests.py")

        details = result["verification_details"]
        assert result["verification_status"] == "verified", details
        assert "EGRESS_BLOCKED" in details["stdout"]
        assert "EGRESS_REACHABLE" not in details["stdout"]

    async def test_inauthentic_code_fails_even_with_a_green_suite(
        self, tmp_path, no_leaked_containers
    ):
        """Combined verdict is tests_passed AND authentic."""
        repo = _git_repo(
            tmp_path / "repo",
            {"run_tests.py": "print('trivially green')\n"},
        )

        result = await _verify(repo, "python run_tests.py", authentic=False)

        details = result["verification_details"]
        assert details["tests_passed"] is True
        assert details["authentic"] is False
        assert result["verification_status"] == "failed"

    async def test_hanging_test_command_times_out(self, tmp_path, no_leaked_containers):
        """The timeout must be enforced host-side and reported as timed_out."""
        from app.workers.dev_sandbox import DockerSandbox

        repo = _git_repo(
            tmp_path / "repo", {"run_tests.py": "import time; time.sleep(600)\n"}
        )

        original_init = DockerSandbox.__init__

        def short_timeout_init(self, *args, **kwargs):
            kwargs["timeout"] = 5
            original_init(self, *args, **kwargs)

        with patch.object(DockerSandbox, "__init__", short_timeout_init):
            result = await _verify(repo, "python run_tests.py")

        details = result["verification_details"]
        assert result["verification_status"] == "failed"
        assert details["timed_out"] is True
        assert details["tests_passed"] is False

    @requires_internet
    async def test_install_step_has_real_network_reachability(
        self, tmp_path, no_leaked_containers
    ):
        """Positive half of the posture, asserted by reachability rather than by a
        `network_disabled is not True` double negative — which would also pass if
        a future change attached an `internal: true` network that breaks installs."""
        from app.workers.dev_sandbox import DockerSandbox

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("x = 1\n")

        probe = [
            "python",
            "-c",
            "import socket; socket.create_connection(('1.1.1.1', 443), timeout=8).close();"
            " print('NET_OK')",
        ]
        with DockerSandbox(timeout=60) as sandbox:
            sandbox.prepare_workspace(str(repo))
            before = sandbox.run_command(probe)
            sandbox.isolate_network()
            after = sandbox.run_command(probe)

        assert before.exit_code == 0, (
            f"install step cannot reach the network: {before.stderr}"
        )
        assert "NET_OK" in before.stdout
        assert after.exit_code != 0, "test step still had egress"
        assert (
            "unreachable" in after.stderr.lower() or "resolve" in after.stderr.lower()
        )

    async def test_sequential_steps_exceeding_one_step_budget_both_succeed(
        self, tmp_path, no_leaked_containers
    ):
        """Pins the backstop arithmetic against a real daemon: two steps, each
        inside the per-step deadline, whose SUM exceeds it. A backstop derived
        from one step's timeout SIGKILLs the second (exit 137, no output)."""
        from app.workers.dev_sandbox import DockerSandbox

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("x = 1\n")

        with DockerSandbox(timeout=4) as sandbox:
            sandbox.prepare_workspace(str(repo))
            first = sandbox.run_command(["sleep", "3"])
            second = sandbox.run_command(["sh", "-c", "sleep 3; echo STILL_ALIVE"])
            sandbox.workspace_container.reload()
            state = sandbox.workspace_container.status

        assert first.exit_code == 0, f"first step died: {first.exit_code}"
        assert second.exit_code == 0, f"second step was killed: exit {second.exit_code}"
        assert second.exit_code != 137
        assert "STILL_ALIVE" in second.stdout
        assert state == "running"

    async def test_backstop_expiry_midexec_is_classified_as_infrastructure(
        self, tmp_path, no_leaked_containers
    ):
        """The reviewer's repro at 6s scale, against a real daemon: when the
        container's main process exits under a running exec, Docker SIGKILLs it
        and the result is a silent 137 — which must classify as infrastructure,
        never as the user's suite failing."""
        from app.workers.dev_sandbox import DockerSandbox, _is_infrastructure_kill

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("x = 1\n")

        # Simulate the old per-step backstop by making the idle command expire
        # mid-exec; IDLE_COMMAND is what production now uses instead.
        with patch("app.workers.dev_sandbox.IDLE_COMMAND", ["sleep", "3"]):
            with DockerSandbox(timeout=60) as sandbox:
                sandbox.prepare_workspace(str(repo))
                result = sandbox.run_command(["sleep", "8"])

        assert result.exit_code == 137, f"expected SIGKILL, got {result.exit_code}"
        assert not result.stdout.strip() and not result.stderr.strip()
        assert result.timed_out is False, "not a deadline — the container died"
        assert _is_infrastructure_kill(result) is True

    async def test_git_directory_is_not_uploaded_to_the_container(
        self, tmp_path, no_leaked_containers
    ):
        repo = _git_repo(
            tmp_path / "repo",
            {
                "run_tests.py": (
                    "import os\n"
                    "assert os.path.exists('run_tests.py')\n"
                    "assert not os.path.exists('.git'), 'git history was uploaded'\n"
                    "print('NO_GIT_DIR')\n"
                )
            },
        )

        result = await _verify(repo, "python run_tests.py")

        assert result["verification_status"] == "verified", result[
            "verification_details"
        ]
        assert "NO_GIT_DIR" in result["verification_details"]["stdout"]

    async def test_capabilities_are_dropped_in_the_container(
        self, tmp_path, no_leaked_containers
    ):
        """uid 0 with the default capability set is real privilege; cap_drop=ALL
        is what actually removes it (no-new-privileges does not)."""
        from app.workers.dev_sandbox import DockerSandbox

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("x = 1\n")

        with DockerSandbox(timeout=60) as sandbox:
            sandbox.prepare_workspace(str(repo))
            whoami = sandbox.run_command(["id", "-u"])
            # CAP_CHOWN is required even for root; without cap_drop this succeeds.
            chown = sandbox.run_command(
                [
                    "python",
                    "-c",
                    "open('/tmp/f','w').close(); import os; os.chown('/tmp/f', 1, 1)",
                ]
            )

        assert whoami.stdout.strip() == "0", "test assumes the container runs as root"
        assert chown.exit_code != 0, (
            "root still holds CAP_CHOWN — capabilities not dropped"
        )
        assert (
            "PermissionError" in chown.stderr or "not permitted" in chown.stderr.lower()
        )

    async def test_clone_failure_reports_clone_stage(
        self, tmp_path, no_leaked_containers
    ):
        """A bad URL is a legible clone-stage failure, not a crash."""
        result = await _verify(
            f"file://{tmp_path}/does-not-exist", "python run_tests.py"
        )

        details = result["verification_details"]
        assert result["verification_status"] == "failed"
        assert details["stage"] == "clone"
        assert "Failed to clone repo" in details["error"]


# ─── network blast radius, against a real daemon ───────────────────────────


VICTIM_NETWORK = "sacrifice-w7-isolation-probe"
VICTIM_NAME = "sacrifice-w7-isolation-victim"
VICTIM_PORT = 9999

# Accepts one connection at a time and answers with a marker, so "reachable"
# means a real TCP session was established rather than merely a resolved route.
_VICTIM_CMD = [
    "python",
    "-c",
    "import socket\n"
    "s=socket.socket()\n"
    "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
    f"s.bind(('0.0.0.0',{VICTIM_PORT}))\n"
    "s.listen(16)\n"
    "while True:\n"
    "    c,_=s.accept(); c.sendall(b'PWNED'); c.close()\n",
]

_PROBE_SRC = (
    "import socket,sys\n"
    "try:\n"
    "    c=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=6)\n"
    "    c.settimeout(4); print('REACHED:'+repr(c.recv(16))); c.close()\n"
    "except Exception as e:\n"
    "    print('BLOCKED:'+type(e).__name__)\n"
)


@pytest.fixture
def victim_on_another_network():
    """A live TCP service on its own user-defined network, plus proof it is live.

    The control matters more than the probe here: without it, a "blocked" result
    is equally consistent with a target that was never listening, which is
    exactly how a network-isolation test quietly stops testing anything.
    """
    import docker

    client = docker.from_env()
    network = client.networks.create(VICTIM_NETWORK, driver="bridge")
    victim = None
    try:
        victim = client.containers.run(
            SANDBOX_IMAGE,
            command=_VICTIM_CMD,
            name=VICTIM_NAME,
            network=VICTIM_NETWORK,
            detach=True,
        )
        for _ in range(30):
            victim.reload()
            ip = victim.attrs["NetworkSettings"]["Networks"][VICTIM_NETWORK][
                "IPAddress"
            ]
            if victim.status == "running" and ip:
                break
            time.sleep(0.2)

        control = client.containers.run(
            SANDBOX_IMAGE,
            command=["python", "-c", _PROBE_SRC, ip, str(VICTIM_PORT)],
            network=VICTIM_NETWORK,
            remove=False,
            detach=True,
        )
        control.wait(timeout=30)
        control_out = control.logs().decode()
        control.remove(force=True)
        assert "REACHED" in control_out, (
            f"control failed — the victim was not listening, so this test could "
            f"not detect a missing isolation boundary: {control_out!r}"
        )
        yield ip
    finally:
        if victim is not None:
            try:
                victim.remove(force=True)
            except Exception:
                pass
        try:
            network.remove()
        except Exception:
            pass


@requires_docker
class TestSandboxNetworkBlastRadius:
    def test_sandbox_cannot_reach_a_container_on_another_network(
        self, tmp_path, victim_on_another_network
    ):
        """The security property, not just the ``network=`` kwarg.

        Asserting the create call names our network cannot notice a regression
        that keeps the kwarg and attaches a second network as well, or one where
        Docker's inter-bridge isolation stops applying. This drives a real
        container on the real daemon against a real listener.
        """
        from app.workers.dev_sandbox import DockerSandbox

        (tmp_path / "a.py").write_text("x = 1\n")
        sandbox = DockerSandbox(timeout=120)
        try:
            sandbox.prepare_workspace(str(tmp_path))
            result = sandbox.run_command(
                [
                    "python",
                    "-c",
                    _PROBE_SRC,
                    victim_on_another_network,
                    str(VICTIM_PORT),
                ]
            )
        finally:
            sandbox.close()

        assert "BLOCKED" in result.stdout, (
            "install-time code reached a container on another Docker network: "
            f"{result.stdout!r}"
        )

    def test_sandbox_cannot_resolve_compose_service_names(self, tmp_path):
        """Docker's embedded DNS must not hand the sandbox the app's own services.

        Reachability is moot if the sandbox cannot name the target, and a
        service-name lookup is how repo-authored code would find ``db``/``redis``
        without knowing any address.
        """
        from app.workers.dev_sandbox import DockerSandbox

        (tmp_path / "a.py").write_text("x = 1\n")
        src = (
            "import socket\n"
            "for n in ('db','redis','sacrifice-db','sacrifice-redis'):\n"
            "    try: print(n+'='+socket.gethostbyname(n))\n"
            "    except Exception: print(n+'=NXDOMAIN')\n"
        )
        sandbox = DockerSandbox(timeout=120)
        try:
            sandbox.prepare_workspace(str(tmp_path))
            result = sandbox.run_command(["python", "-c", src])
        finally:
            sandbox.close()

        assert result.stdout.count("NXDOMAIN") == 4, result.stdout
