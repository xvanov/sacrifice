import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── judge_code_authenticity ───────────────────────────────────────


class TestJudgeCodeAuthenticity:
    @pytest.mark.asyncio
    async def test_llm_receives_goal_description_code_summary_and_test_results(self):
        from app.services.llm import judge_code_authenticity

        with patch(
            "app.services.llm._call_azure_foundry_for_code", new_callable=AsyncMock
        ) as mock_azure:
            mock_azure.return_value = {"authentic": True, "reasoning": "Looks good"}
            result = await judge_code_authenticity(
                goal_description="Build a FastAPI CRUD API",
                code_summary="src/main.py: FastAPI app with routes\nsrc/models.py: SQLAlchemy models",
                test_results="exit_code=0, tests passed: 5/5",
            )

            mock_azure.assert_awaited_once()
            call_args = mock_azure.await_args
            assert call_args is not None
            goal_desc = (
                call_args[0]
                if len(call_args.args) > 0
                else call_args.kwargs.get("goal_description", "")
            )
            code_summary = (
                call_args[0]
                if len(call_args.args) > 0
                else call_args.kwargs.get("code_summary", "")
            )
            assert "Build a FastAPI CRUD API" in str(goal_desc) or any(
                "Build a FastAPI CRUD API" in str(a) for a in call_args.args
            )
            assert "src/main.py" in str(code_summary) or any(
                "src/main.py" in str(a) for a in call_args.args
            )
            assert result == {"authentic": True, "reasoning": "Looks good"}

    @pytest.mark.asyncio
    async def test_returns_structured_verdict_with_authentic_and_reasoning(self):
        from app.services.llm import judge_code_authenticity

        with patch(
            "app.services.llm._call_azure_foundry_for_code", new_callable=AsyncMock
        ) as mock_azure:
            mock_azure.return_value = {
                "authentic": True,
                "reasoning": "Code implements the described functionality",
            }
            result = await judge_code_authenticity(
                goal_description="Build a FastAPI CRUD API",
                code_summary="src/main.py: FastAPI routes",
                test_results="exit_code=0",
            )

        assert isinstance(result, dict)
        assert "authentic" in result
        assert isinstance(result["authentic"], bool)
        assert "reasoning" in result
        assert isinstance(result["reasoning"], str)

    @pytest.mark.asyncio
    async def test_local_fallback_returns_correct_format(self):
        from app.services.llm import _local_code_fallback_judgment

        result = _local_code_fallback_judgment(
            goal_description="Build a FastAPI CRUD API",
            code_summary="src/main.py: FastAPI routes",
            test_results="exit_code=0, all tests passed",
        )

        assert isinstance(result, dict)
        assert "authentic" in result
        assert isinstance(result["authentic"], bool)
        assert "reasoning" in result
        assert isinstance(result["reasoning"], str)

    @pytest.mark.asyncio
    async def test_hardcoded_code_returns_authentic_false(self):
        from app.services.llm import _local_code_fallback_judgment

        code_summary = (
            "src/tests/test_main.py:\n"
            "  def test_add_returns_correct_sum() -> hardcoded to always pass with 42\n"
            "src/main.py:\n"
            "  def add(a, b) -> always returns 42 regardless of input\n"
        )

        result = _local_code_fallback_judgment(
            goal_description="Build a function that adds two numbers",
            code_summary=code_summary,
            test_results="exit_code=0, tests passed: 1/1",
        )

        assert result["authentic"] is False
        assert "hardcoded" in result["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_local_fallback_marks_code_with_no_function_signatures_as_unknown(self):
        from app.services.llm import _local_code_fallback_judgment

        result = _local_code_fallback_judgment(
            goal_description="Build a FastAPI CRUD API",
            code_summary="No source files found",
            test_results="exit_code=0, tests passed: 5/5",
        )

        assert result["authentic"] is False
        assert (
            "no source" in result["reasoning"].lower()
            or "not authentic" in result["reasoning"].lower()
        )

    @pytest.mark.asyncio
    async def test_legitimate_code_returns_authentic_true(self):
        from app.services.llm import _local_code_fallback_judgment

        code_summary = (
            "src/models.py:\n"
            "  class User(Base): __tablename__='users', id=Column(Integer), name=Column(String)\n"
            "  class Goal(Base): __tablename__='goals', id=Column(Integer), user_id=Column(ForeignKey)\n"
            "src/routes.py:\n"
            "  def create_user(): parses request body, creates User, returns 201\n"
            "  def list_goals(): queries DB, returns list of goals\n"
            "  def update_goal(): validates input, updates record, returns updated goal\n"
            "src/services.py:\n"
            "  def calculate_pledge(): computes pledge amount based on goal criteria\n"
        )

        result = _local_code_fallback_judgment(
            goal_description="Build a FastAPI CRUD API with user and goal management",
            code_summary=code_summary,
            test_results="exit_code=0, tests passed: 12/12, coverage: 85%",
        )

        assert result["authentic"] is True
        assert "implement" in result["reasoning"].lower() or "covers" in result["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_azure_error_returns_authentic_false_with_reasoning(self):
        from app.services.llm import _call_azure_foundry_for_code

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await _call_azure_foundry_for_code(
                goal_description="Build API",
                code_summary="src/main.py",
                test_results="exit_code=0",
            )

        assert result["authentic"] is False
        assert "LLM API error" in result["reasoning"]


# ─── _generate_code_summary ─────────────────────────────────────────


class TestGenerateCodeSummary:
    def test_generates_file_tree_and_function_signatures(self):
        import tempfile
        from pathlib import Path

        from app.workers.dev_sandbox import _generate_code_summary

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "src").mkdir()
            Path(tmpdir, "src", "main.py").write_text(
                "def hello():\n    pass\n\nclass MyClass:\n    def method(self):\n        pass\n"
            )
            Path(tmpdir, "src", "utils.py").write_text("def util_func(a, b):\n    return a + b\n")
            Path(tmpdir, "tests").mkdir()
            Path(tmpdir, "tests", "test_main.py").write_text("def test_hello():\n    assert True\n")
            Path(tmpdir, "README.md").write_text("# Project\n")

            result = _generate_code_summary(tmpdir)

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "tests/test_main.py" in result
        assert "def hello()" in result
        assert "def method(self)" in result
        assert "def util_func(a, b)" in result
        assert "def test_hello()" in result
        assert "README.md" not in result

    def test_returns_message_when_no_source_files_found(self):
        import tempfile
        from pathlib import Path

        from app.workers.dev_sandbox import _generate_code_summary

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "README.md").write_text("# Empty project\n")
            result = _generate_code_summary(tmpdir)

        assert "No source files found" in result

    def test_skips_binary_and_large_files(self):
        import tempfile
        from pathlib import Path

        from app.workers.dev_sandbox import _generate_code_summary

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "main.py").write_text("def foo():\n    pass\n")
            Path(tmpdir, "data.bin").write_bytes(b"\x00\x01\x02\x03")
            large = "x" * 20000
            Path(tmpdir, "large.py").write_text(large)

            result = _generate_code_summary(tmpdir)

        assert "main.py" in result
        assert "def foo()" in result
        assert "data.bin" not in result
        assert "large.py" in result


# ─── run_dev_sandbox_verification with LLM review ───────────────────


class TestDevSandboxWithLLMReview:
    @patch("app.workers.dev_sandbox.shutil.rmtree")
    @patch("app.workers.dev_sandbox.tempfile.mkdtemp")
    @patch("app.workers.dev_sandbox.DockerSandbox")
    @patch("app.workers.dev_sandbox.subprocess.run")
    async def test_tests_pass_and_llm_authentic_returns_verified(
        self, mock_subprocess, mock_sandbox_cls, mock_mkdtemp, mock_rmtree
    ):
        from app.workers.dev_sandbox import SandboxResult, run_dev_sandbox_verification

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
                "reasoning": "Code legitimately implements the goal",
            }

            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://github.com/user/repo.git",
                    "branch": "main",
                    "test_command": "pytest",
                },
                criteria_data={"goal_description": "Build a FastAPI CRUD API"},
                db=mock_db,
            )

        assert result["verification_status"] == "verified"
        assert result["verification_details"]["tests_passed"] is True
        assert result["verification_details"]["authentic"] is True
        assert (
            result["verification_details"]["llm_reasoning"]
            == "Code legitimately implements the goal"
        )

    @patch("app.workers.dev_sandbox.shutil.rmtree")
    @patch("app.workers.dev_sandbox.tempfile.mkdtemp")
    @patch("app.workers.dev_sandbox.DockerSandbox")
    @patch("app.workers.dev_sandbox.subprocess.run")
    async def test_tests_pass_but_llm_not_authentic_returns_failed(
        self, mock_subprocess, mock_sandbox_cls, mock_mkdtemp, mock_rmtree
    ):
        from app.workers.dev_sandbox import SandboxResult, run_dev_sandbox_verification

        mock_mkdtemp.return_value = "/tmp/test-sandbox"
        mock_subprocess.return_value = MagicMock(returncode=0)

        mock_sandbox_instance = MagicMock()
        mock_sandbox_instance.run_command.return_value = SandboxResult(
            exit_code=0, stdout="passing tests", stderr=""
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
                "authentic": False,
                "reasoning": "Code appears to hardcode test answers",
            }

            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://github.com/user/repo.git",
                    "branch": "main",
                    "test_command": "pytest",
                },
                criteria_data={"goal_description": "Build a FastAPI CRUD API"},
                db=mock_db,
            )

        assert result["verification_status"] == "failed"
        assert result["verification_details"]["tests_passed"] is True
        assert result["verification_details"]["authentic"] is False
        assert "hardcode" in result["verification_details"]["llm_reasoning"].lower()

    @patch("app.workers.dev_sandbox.shutil.rmtree")
    @patch("app.workers.dev_sandbox.tempfile.mkdtemp")
    @patch("app.workers.dev_sandbox.DockerSandbox")
    @patch("app.workers.dev_sandbox.subprocess.run")
    async def test_tests_fail_and_llm_authentic_still_returns_failed(
        self, mock_subprocess, mock_sandbox_cls, mock_mkdtemp, mock_rmtree
    ):
        from app.workers.dev_sandbox import SandboxResult, run_dev_sandbox_verification

        mock_mkdtemp.return_value = "/tmp/test-sandbox"
        mock_subprocess.return_value = MagicMock(returncode=0)

        mock_sandbox_instance = MagicMock()
        mock_sandbox_instance.run_command.return_value = SandboxResult(
            exit_code=1, stdout="", stderr="FAILED tests"
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
            criteria_data={"goal_description": "Build a FastAPI CRUD API"},
            db=mock_db,
        )

        assert result["verification_status"] == "failed"
        assert result["verification_details"]["tests_passed"] is False

    @patch("app.workers.dev_sandbox.shutil.rmtree")
    @patch("app.workers.dev_sandbox.tempfile.mkdtemp")
    @patch("app.workers.dev_sandbox.DockerSandbox")
    @patch("app.workers.dev_sandbox.subprocess.run")
    async def test_verdict_reasoning_stored_in_details(
        self, mock_subprocess, mock_sandbox_cls, mock_mkdtemp, mock_rmtree
    ):
        from app.workers.dev_sandbox import SandboxResult, run_dev_sandbox_verification

        mock_mkdtemp.return_value = "/tmp/test-sandbox"
        mock_subprocess.return_value = MagicMock(returncode=0)

        mock_sandbox_instance = MagicMock()
        mock_sandbox_instance.run_command.return_value = SandboxResult(
            exit_code=0, stdout="passing tests", stderr=""
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
                "reasoning": "The code implements proper CRUD operations with database models, validation, and error handling.",
            }

            result = await run_dev_sandbox_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={
                    "repo_url": "https://github.com/user/repo.git",
                    "branch": "main",
                    "test_command": "pytest",
                },
                criteria_data={"goal_description": "Build a FastAPI CRUD API"},
                db=mock_db,
            )

        details = result["verification_details"]
        assert "llm_reasoning" in details
        assert "authentic" in details
        assert "code_summary" in details
        assert details["llm_reasoning"] == (
            "The code implements proper CRUD operations with database models, validation, and error handling."
        )
        assert details["authentic"] is True
