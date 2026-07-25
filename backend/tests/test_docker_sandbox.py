import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── Language detection tests (pure functions, no Docker) ─────────────


def test_detect_language_python_requirements_txt():
    from app.workers.dev_sandbox import detect_language

    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "requirements.txt").write_text("pytest\nhttpx\n")
        result = detect_language(tmpdir)
    assert result == "python"


def test_detect_language_python_setup_py():
    from app.workers.dev_sandbox import detect_language

    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "setup.py").write_text("from setuptools import setup\n")
        result = detect_language(tmpdir)
    assert result == "python"


def test_detect_language_python_pyproject_toml():
    from app.workers.dev_sandbox import detect_language

    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "pyproject.toml").write_text("[project]\n")
        result = detect_language(tmpdir)
    assert result == "python"


def test_detect_language_node():
    from app.workers.dev_sandbox import detect_language

    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "package.json").write_text('{"name": "test"}\n')
        result = detect_language(tmpdir)
    assert result == "node"


def test_detect_language_go():
    from app.workers.dev_sandbox import detect_language

    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "go.mod").write_text("module example.com/test\n")
        result = detect_language(tmpdir)
    assert result == "go"


def test_detect_language_rust():
    from app.workers.dev_sandbox import detect_language

    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "Cargo.toml").write_text("[package]\n")
        result = detect_language(tmpdir)
    assert result == "rust"


def test_detect_language_unknown():
    from app.workers.dev_sandbox import detect_language

    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "README.md").write_text("# Hello\n")
        result = detect_language(tmpdir)
    assert result == "unknown"


# ─── Install command tests (no Docker) ────────────────────────────────


def test_get_install_command_python_with_requirements():
    from app.workers.dev_sandbox import get_install_command

    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "requirements.txt").write_text("pytest\n")
        cmd = get_install_command("python", tmpdir)
    assert cmd == ["pip", "install", "-r", "requirements.txt"]


def test_get_install_command_python_no_requirements():
    from app.workers.dev_sandbox import get_install_command

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = get_install_command("python", tmpdir)
    assert cmd == ["pip", "install", "-e", "."]


def test_get_install_command_node():
    from app.workers.dev_sandbox import get_install_command

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = get_install_command("node", tmpdir)
    assert cmd == ["npm", "install"]


def test_get_install_command_go():
    from app.workers.dev_sandbox import get_install_command

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = get_install_command("go", tmpdir)
    assert cmd == ["go", "mod", "download"]


def test_get_install_command_rust():
    from app.workers.dev_sandbox import get_install_command

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = get_install_command("rust", tmpdir)
    assert cmd == ["cargo", "build"]


def test_get_install_command_unknown():
    from app.workers.dev_sandbox import get_install_command

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = get_install_command("unknown", tmpdir)
    assert cmd is None


# ─── DockerSandbox class tests (mocked Docker) ────────────────────────


@pytest.fixture
def mock_docker_client():
    with patch("app.workers.dev_sandbox.docker.from_env") as mock_from_env:
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_container():
    container = MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    container.logs.side_effect = [
        b"Test stdout output",
        b"",
    ]
    return container


class TestDockerSandbox:
    def test_create_sandbox_with_defaults(self, mock_docker_client):
        from app.workers.dev_sandbox import DockerSandbox

        sandbox = DockerSandbox()
        assert sandbox.image == "python:3.11-slim"
        assert sandbox.memory_limit == "1g"
        assert sandbox.cpu_limit == 1.0
        assert sandbox.timeout == 300

    def test_create_sandbox_with_custom_values(self, mock_docker_client):
        from app.workers.dev_sandbox import DockerSandbox

        sandbox = DockerSandbox(
            image="node:20-slim", memory_limit="512m", cpu_limit=0.5, timeout=60
        )
        assert sandbox.image == "node:20-slim"
        assert sandbox.memory_limit == "512m"
        assert sandbox.cpu_limit == 0.5
        assert sandbox.timeout == 60

    def test_run_command_returns_stdout_and_exit_code(
        self, mock_docker_client, mock_container
    ):
        from app.workers.dev_sandbox import DockerSandbox, SandboxResult

        mock_docker_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        result = sandbox.run_command(["echo", "hello"])

        assert isinstance(result, SandboxResult)
        assert result.exit_code == 0
        assert result.stdout == "Test stdout output"
        assert result.timed_out is False
        assert result.success is True

        mock_docker_client.containers.run.assert_called_once_with(
            image="python:3.11-slim",
            command=["echo", "hello"],
            working_dir="/workspace",
            environment=None,
            mem_limit="1g",
            nano_cpus=int(1.0 * 1e9),
            pids_limit=512,
            cap_drop=["ALL"],
            network_disabled=True,
            detach=True,
            remove=False,
            security_opt=["no-new-privileges:true"],
            privileged=False,
        )

    def test_run_nonzero_exit_code_returns_failure(self, mock_docker_client):
        from app.workers.dev_sandbox import DockerSandbox

        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 1}
        mock_container.logs.side_effect = [b"stderr output", b"stderr output"]
        mock_docker_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        result = sandbox.run_command(["false"])

        assert result.exit_code == 1
        assert result.success is False
        assert result.timed_out is False

    def test_container_is_cleaned_up_after_run(
        self, mock_docker_client, mock_container
    ):
        from app.workers.dev_sandbox import DockerSandbox

        mock_docker_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        sandbox.run_command(["echo", "done"])

        mock_container.remove.assert_called_once_with(force=True)

    def test_cleanup_removes_container(self, mock_docker_client, mock_container):
        from app.workers.dev_sandbox import DockerSandbox

        mock_docker_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        sandbox.run_command(["echo", "hello"])

        assert sandbox.container is None

    def test_cleanup_on_run_error(self, mock_docker_client):
        from app.workers.dev_sandbox import DockerSandbox

        mock_docker_client.containers.run.side_effect = Exception(
            "Failed to create container"
        )

        sandbox = DockerSandbox()
        with pytest.raises(Exception, match="Failed to create container"):
            sandbox.run_command(["echo", "fail"])

        assert sandbox.container is None

    def test_timeout_kills_container_and_returns_timeout_result(
        self, mock_docker_client
    ):
        from app.workers.dev_sandbox import DockerSandbox
        import docker

        mock_container = MagicMock()
        mock_container.wait.side_effect = docker.errors.APIError(
            "Timeout: 300 seconds exceeded"
        )
        mock_docker_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox(timeout=5)
        result = sandbox.run_command(["sleep", "60"])

        assert result.timed_out is True
        assert result.success is False
        assert result.exit_code == -1
        mock_container.kill.assert_called_once()
        mock_container.remove.assert_called_once_with(force=True)

    def test_run_with_environment_variables(self, mock_docker_client, mock_container):
        from app.workers.dev_sandbox import DockerSandbox

        mock_docker_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        sandbox.run_command(["env"], env={"MY_VAR": "my_value", "SECRET": "shh"})

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert call_kwargs["environment"] == {"MY_VAR": "my_value", "SECRET": "shh"}

    def test_sandbox_uses_no_privileged_mode(self, mock_docker_client, mock_container):
        from app.workers.dev_sandbox import DockerSandbox

        mock_docker_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        sandbox.run_command(["whoami"])

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert call_kwargs.get("privileged") is False

    def test_sandbox_uses_no_new_privileges(self, mock_docker_client, mock_container):
        from app.workers.dev_sandbox import DockerSandbox

        mock_docker_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        sandbox.run_command(["whoami"])

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert "no-new-privileges:true" in call_kwargs.get("security_opt", [])

    def test_sandbox_disables_network(self, mock_docker_client, mock_container):
        from app.workers.dev_sandbox import DockerSandbox

        mock_docker_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        sandbox.run_command(["whoami"])

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert call_kwargs.get("network_disabled") is True

    def test_run_command_with_custom_workdir(self, mock_docker_client, mock_container):
        from app.workers.dev_sandbox import DockerSandbox

        mock_docker_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        sandbox.run_command(["pwd"], workdir="/app")

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert call_kwargs["working_dir"] == "/app"


# ─── Git clone helper tests (pure function, no Docker) ───────────────


class TestGitCloneHelpers:
    def test_parse_github_url(self):
        from app.workers.dev_sandbox import parse_repo_url

        result = parse_repo_url("https://github.com/user/repo.git")
        assert result == ("https://github.com/user/repo.git", "main")

    def test_parse_github_url_with_branch(self):
        from app.workers.dev_sandbox import parse_repo_url

        result = parse_repo_url("https://github.com/user/repo.git", branch="develop")
        assert result == ("https://github.com/user/repo.git", "develop")

    def test_parse_github_url_without_dot_git(self):
        from app.workers.dev_sandbox import parse_repo_url

        result = parse_repo_url("https://github.com/user/repo")
        assert result == ("https://github.com/user/repo.git", "main")


# ─── Clone repo tests (mocked subprocess) ────────────────────────────


class TestCloneRepo:
    @patch("app.workers.dev_sandbox.subprocess.run")
    def test_clone_repo_calls_git_with_correct_args(self, mock_run):
        from app.workers.dev_sandbox import clone_repo

        mock_run.return_value = MagicMock(returncode=0)

        clone_repo("https://github.com/user/repo.git", "main", "/tmp/target")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[:2] == ["git", "clone"]
        assert "--depth=1" in args
        assert "--branch" in args
        assert "main" in args
        assert "https://github.com/user/repo.git" in args
        assert "/tmp/target" in args

    @patch("app.workers.dev_sandbox.subprocess.run")
    def test_clone_repo_raises_on_failure(self, mock_run):
        from app.workers.dev_sandbox import clone_repo

        mock_fail = MagicMock()
        mock_fail.returncode = 128
        mock_fail.stderr = "fatal: repository not found"
        mock_run.return_value = mock_fail

        with pytest.raises(RuntimeError, match="Failed to clone repo"):
            clone_repo("https://github.com/user/bad-repo.git", "main", "/tmp/target")


# ─── run_dev_sandbox_verification orchestration tests (mocked) ───────


class TestRunDevSandboxVerification:
    @patch("app.workers.dev_sandbox.shutil.rmtree")
    @patch("app.workers.dev_sandbox.tempfile.mkdtemp")
    @patch("app.workers.dev_sandbox.docker.from_env")
    @patch("app.workers.dev_sandbox.subprocess.run")
    async def test_verification_repo_clone_failure_returns_failed(
        self, mock_subprocess, mock_docker_from_env, mock_mkdtemp, mock_rmtree
    ):
        from app.workers.dev_sandbox import run_dev_sandbox_verification
        import uuid

        mock_mkdtemp.return_value = "/tmp/test-sandbox"
        mock_fail = MagicMock()
        mock_fail.returncode = 128
        mock_fail.stderr = "fatal: repository not found"
        mock_subprocess.return_value = mock_fail

        scoped_mock_result = MagicMock()
        scoped_mock_result.scalar_one_or_none.return_value = None
        scoped_execute = AsyncMock(return_value=scoped_mock_result)
        mock_db = AsyncMock()
        mock_db.execute = scoped_execute
        mock_db.commit = AsyncMock()

        result = await run_dev_sandbox_verification(
            goal_id=uuid.uuid4(),
            submission_id=uuid.uuid4(),
            proof_data={
                "repo_url": "https://github.com/user/bad-repo.git",
                "branch": "main",
                "test_command": "pytest",
            },
            criteria_data={"goal_description": "Build a FastAPI endpoint"},
            db=mock_db,
        )

        assert result["verification_status"] == "failed"
        assert "repository not found" in str(result["verification_details"])

    @patch("app.workers.dev_sandbox.shutil.rmtree")
    @patch("app.workers.dev_sandbox.tempfile.mkdtemp")
    @patch("app.workers.dev_sandbox.DockerSandbox")
    @patch("app.workers.dev_sandbox.subprocess.run")
    async def test_verification_tests_pass_returns_verified(
        self, mock_subprocess, mock_sandbox_cls, mock_mkdtemp, mock_rmtree
    ):
        from app.workers.dev_sandbox import run_dev_sandbox_verification, SandboxResult
        import uuid

        mock_mkdtemp.return_value = "/tmp/test-sandbox"
        mock_subprocess.return_value = MagicMock(returncode=0)

        mock_sandbox_instance = MagicMock()
        mock_sandbox_instance.run_command.return_value = SandboxResult(
            exit_code=0, stdout="passing tests output", stderr=""
        )
        mock_sandbox_cls.return_value = mock_sandbox_instance

        scoped_mock_result = MagicMock()
        scoped_mock_result.scalar_one_or_none.return_value = None
        scoped_execute = AsyncMock(return_value=scoped_mock_result)
        mock_db = AsyncMock()
        mock_db.execute = scoped_execute
        mock_db.commit = AsyncMock()

        with patch(
            "app.workers.dev_sandbox.judge_code_authenticity", new_callable=AsyncMock
        ) as mock_judge:
            mock_judge.return_value = {
                "authentic": True,
                "reasoning": "Code implements the goal.",
            }

            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://github.com/user/repo.git",
                    "branch": "main",
                    "test_command": "pytest",
                },
                criteria_data={"goal_description": "Build a FastAPI endpoint"},
                db=mock_db,
            )

        assert result["verification_status"] == "verified"
        assert result["verification_details"]["tests_passed"] is True
        assert result["verification_details"]["exit_code"] == 0

    @patch("app.workers.dev_sandbox.shutil.rmtree")
    @patch("app.workers.dev_sandbox.tempfile.mkdtemp")
    @patch("app.workers.dev_sandbox.DockerSandbox")
    @patch("app.workers.dev_sandbox.subprocess.run")
    async def test_verification_tests_fail_returns_failed(
        self, mock_subprocess, mock_sandbox_cls, mock_mkdtemp, mock_rmtree
    ):
        from app.workers.dev_sandbox import run_dev_sandbox_verification, SandboxResult
        import uuid

        mock_mkdtemp.return_value = "/tmp/test-sandbox"
        mock_subprocess.return_value = MagicMock(returncode=0)

        mock_sandbox_instance = MagicMock()
        mock_sandbox_instance.run_command.return_value = SandboxResult(
            exit_code=1, stdout="", stderr="FAILED test_example"
        )
        mock_sandbox_cls.return_value = mock_sandbox_instance

        scoped_mock_result = MagicMock()
        scoped_mock_result.scalar_one_or_none.return_value = None
        scoped_execute = AsyncMock(return_value=scoped_mock_result)
        mock_db = AsyncMock()
        mock_db.execute = scoped_execute
        mock_db.commit = AsyncMock()

        result = await run_dev_sandbox_verification(
            goal_id=uuid.uuid4(),
            submission_id=uuid.uuid4(),
            proof_data={
                "repo_url": "https://github.com/user/repo.git",
                "branch": "main",
                "test_command": "pytest",
            },
            criteria_data={"goal_description": "Build a FastAPI endpoint"},
            db=mock_db,
        )

        assert result["verification_status"] == "failed"
        assert result["verification_details"]["tests_passed"] is False
        assert result["verification_details"]["exit_code"] == 1

    @patch("app.workers.dev_sandbox.shutil.rmtree")
    @patch("app.workers.dev_sandbox.tempfile.mkdtemp")
    @patch("app.workers.dev_sandbox.DockerSandbox")
    @patch("app.workers.dev_sandbox.subprocess.run")
    async def test_verification_detects_language(
        self, mock_subprocess, mock_sandbox_cls, mock_mkdtemp, mock_rmtree
    ):
        from app.workers.dev_sandbox import run_dev_sandbox_verification, SandboxResult
        import uuid

        mock_mkdtemp.return_value = "/tmp/test-sandbox"
        mock_subprocess.return_value = MagicMock(returncode=0)

        mock_sandbox_instance = MagicMock()
        mock_sandbox_instance.run_command.return_value = SandboxResult(
            exit_code=0, stdout="test output", stderr=""
        )
        mock_sandbox_cls.return_value = mock_sandbox_instance

        scoped_mock_result = MagicMock()
        scoped_mock_result.scalar_one_or_none.return_value = None
        scoped_execute = AsyncMock(return_value=scoped_mock_result)
        mock_db = AsyncMock()
        mock_db.execute = scoped_execute
        mock_db.commit = AsyncMock()

        with patch(
            "app.workers.dev_sandbox.judge_code_authenticity", new_callable=AsyncMock
        ) as mock_judge:
            mock_judge.return_value = {
                "authentic": True,
                "reasoning": "Code implements the goal.",
            }

            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://github.com/user/repo.git",
                    "branch": "main",
                    "test_command": "pytest",
                },
                criteria_data={"goal_description": "Build a FastAPI endpoint"},
                db=mock_db,
            )

        assert result["verification_status"] == "verified"
        assert "language" in result["verification_details"]

    @patch("app.workers.dev_sandbox.shutil.rmtree")
    @patch("app.workers.dev_sandbox.tempfile.mkdtemp")
    @patch("app.workers.dev_sandbox.DockerSandbox")
    @patch("app.workers.dev_sandbox.subprocess.run")
    async def test_verification_test_command_respects_shell_quoting(
        self, mock_subprocess, mock_sandbox_cls, mock_mkdtemp, mock_rmtree
    ):
        """A test_command with quoted paths should be parsed as proper argv
        tokens (no stray quote chars) via shlex.split."""
        from app.workers.dev_sandbox import run_dev_sandbox_verification, SandboxResult
        import uuid

        mock_mkdtemp.return_value = "/tmp/test-sandbox"
        mock_subprocess.return_value = MagicMock(returncode=0)

        mock_sandbox_instance = MagicMock()
        mock_sandbox_instance.run_command.return_value = SandboxResult(
            exit_code=0, stdout="ok", stderr=""
        )
        mock_sandbox_cls.return_value = mock_sandbox_instance

        scoped_mock_result = MagicMock()
        scoped_mock_result.scalar_one_or_none.return_value = None
        scoped_execute = AsyncMock(return_value=scoped_mock_result)
        mock_db = AsyncMock()
        mock_db.execute = scoped_execute
        mock_db.commit = AsyncMock()

        with patch(
            "app.workers.dev_sandbox.judge_code_authenticity", new_callable=AsyncMock
        ) as mock_judge:
            mock_judge.return_value = {"authentic": True, "reasoning": "ok"}

            await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://github.com/user/repo.git",
                    "branch": "main",
                    "test_command": 'pytest "tests/test foo.py" -k "my test"',
                },
                criteria_data={"goal_description": "Build something"},
                db=mock_db,
            )

        # Find the run_command call that invoked the user-supplied test_command
        # (the first call is the install step with a list argv we don't care about here).
        test_call = None
        for call in mock_sandbox_instance.run_command.call_args_list:
            argv = call.args[0] if call.args else call.kwargs.get("command")
            if argv and argv[0] == "pytest":
                test_call = argv
                break

        assert test_call is not None, "expected a pytest invocation"
        # shlex.split should strip quotes and preserve spaces inside quoted args.
        assert test_call == ["pytest", "tests/test foo.py", "-k", "my test"]
        # Sanity: str.split would have produced these broken tokens.
        assert '"tests/test' not in test_call
        assert 'foo.py"' not in test_call
