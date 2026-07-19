import os
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid

import docker
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.database import async_session
from app.services.llm import judge_code_authenticity
from app.services.verification_result import persist_verification_result


class SandboxResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str, timed_out: bool = False):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def detect_language(repo_path: str) -> str:
    files = set()
    for _root, _dirs, filenames in os.walk(repo_path):
        for f in filenames:
            files.add(f.lower())

    if any(f in files for f in ("requirements.txt", "setup.py", "setup.cfg", "pyproject.toml")):
        return "python"
    if "package.json" in files:
        return "node"
    if "go.mod" in files:
        return "go"
    if "cargo.toml" in files:
        return "rust"
    return "unknown"


def get_install_command(language: str, repo_path: str) -> list[str] | None:
    if language == "python":
        if os.path.exists(os.path.join(repo_path, "requirements.txt")):
            return ["pip", "install", "-r", "requirements.txt"]
        return ["pip", "install", "-e", "."]
    if language == "node":
        return ["npm", "install"]
    if language == "go":
        return ["go", "mod", "download"]
    if language == "rust":
        return ["cargo", "build"]
    return None


def parse_repo_url(url: str, branch: str = "main") -> tuple[str, str]:
    if url.endswith(".git"):
        return url, branch
    return f"{url}.git", branch


def clone_repo(url: str, branch: str, target_dir: str) -> None:
    result = subprocess.run(
        ["git", "clone", "--depth=1", "--branch", branch, url, target_dir],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to clone repo {url} (branch: {branch}): {result.stderr[:500]}")


class DockerSandbox:
    def __init__(
        self,
        image: str = "python:3.11-slim",
        memory_limit: str = "1g",
        cpu_limit: float = 1.0,
        timeout: int = 300,
    ):
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.timeout = timeout
        self.container = None
        self._client = docker.from_env()

    def run_command(
        self,
        command: list[str],
        workdir: str = "/workspace",
        env: dict | None = None,
    ) -> SandboxResult:
        try:
            container = self._client.containers.run(
                image=self.image,
                command=command,
                working_dir=workdir,
                environment=env,
                mem_limit=self.memory_limit,
                nano_cpus=int(self.cpu_limit * 1e9),
                network_disabled=True,
                detach=True,
                remove=False,
                privileged=False,
                security_opt=["no-new-privileges:true"],
            )
        except Exception:
            self.container = None
            raise

        self.container = container

        try:
            wait_result = container.wait(timeout=self.timeout)
            exit_code = wait_result.get("StatusCode", -1)

            raw_logs = container.logs(stdout=True, stderr=False)
            stdout = raw_logs.decode("utf-8", errors="replace") if raw_logs else ""
            raw_err = container.logs(stdout=False, stderr=True)
            stderr = raw_err.decode("utf-8", errors="replace") if raw_err else ""

            return SandboxResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

        except docker.errors.APIError as e:
            error_str = str(e).lower()
            if "timeout" in error_str:
                try:
                    container.kill()
                except Exception:
                    pass
                return SandboxResult(exit_code=-1, stdout="", stderr="", timed_out=True)
            raise

        finally:
            self._cleanup_container()

    def _cleanup_container(self):
        if self.container is not None:
            try:
                self.container.remove(force=True)
            except Exception:
                pass
            self.container = None


def _extract_function_signatures(filepath: str) -> list[str]:
    signatures = []
    try:
        with open(filepath, errors="replace") as f:
            content = f.read()
        for match in re.finditer(
            r"^\s*(async\s+)?def\s+(\w+)\s*\([^)]*\)\s*(->\s*\w+)?\s*:|^\s*class\s+(\w+)\s*[\(:]",
            content,
            re.MULTILINE,
        ):
            sig = match.group(0).strip()
            if len(sig) > 200:
                sig = sig[:200] + "..."
            signatures.append(sig)
    except Exception:
        pass
    return signatures


def _generate_code_summary(repo_path: str) -> str:
    lines = []
    for root, _dirs, filenames in os.walk(repo_path):
        for fname in sorted(filenames):
            if not fname.endswith(
                (
                    ".py",
                    ".js",
                    ".ts",
                    ".tsx",
                    ".jsx",
                    ".go",
                    ".rs",
                    ".java",
                    ".rb",
                    ".c",
                    ".cpp",
                    ".h",
                    ".hpp",
                    ".swift",
                )
            ):
                continue

            fpath = os.path.join(root, fname)

            try:
                size = os.path.getsize(fpath)
                if size > 10000:
                    rel = os.path.relpath(fpath, repo_path)
                    lines.append(f"{rel} ({size} bytes, truncated)")
                    continue
                if size == 0:
                    continue
            except OSError:
                continue

            sigs = _extract_function_signatures(fpath)
            if sigs:
                rel = os.path.relpath(fpath, repo_path)
                lines.append(f"{rel}:")
                for s in sigs:
                    lines.append(f"  {s}")

    if not lines:
        return "No source files found."

    return "\n".join(lines[:200])


def _build_verification_details(
    result: SandboxResult,
    repo_url: str,
    branch: str,
    test_command: str,
    language: str,
    code_summary: str | None = None,
    llm_result: dict | None = None,
) -> dict:
    details = {
        "repo_url": repo_url,
        "branch": branch,
        "language": language,
        "test_command": test_command,
        "exit_code": result.exit_code,
        "stdout": result.stdout[:5000] if result.stdout else "",
        "stderr": result.stderr[:5000] if result.stderr else "",
        "timed_out": result.timed_out,
        "tests_passed": result.success,
    }
    if code_summary is not None:
        details["code_summary"] = code_summary[:2000]
    if llm_result is not None:
        details["authentic"] = llm_result.get("authentic", False)
        details["llm_reasoning"] = llm_result.get("reasoning", "")
    return details


async def run_dev_sandbox_verification(
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    proof_data: dict,
    criteria_data: dict,
    db: AsyncSession | None = None,
) -> dict:
    repo_url = proof_data.get("repo_url", criteria_data.get("repo_url", ""))
    branch = proof_data.get("branch", criteria_data.get("branch", "main"))
    test_command = proof_data.get(
        "test_command", criteria_data.get("test_command", "python -m pytest -v")
    )
    goal_description = criteria_data.get("goal_description", "")

    tmpdir = tempfile.mkdtemp(prefix="sacrifice_sandbox_")
    try:
        clone_repo(repo_url, branch, tmpdir)

        language = detect_language(tmpdir)
        install_cmd = get_install_command(language, tmpdir)

        sandbox = DockerSandbox(timeout=600)

        if install_cmd:
            install_result = sandbox.run_command(install_cmd, workdir="/workspace")
            if not install_result.success and not install_result.timed_out:
                details = _build_verification_details(
                    install_result, repo_url, branch, test_command, language
                )
                details["stage"] = "install"
                details["error"] = f"Dependency installation failed: {install_result.stderr[:500]}"
                status = "failed"
                if db is not None:
                    await _persist_result(db, goal_id, submission_id, status, details)
                else:
                    async with async_session() as session:
                        await _persist_result(session, goal_id, submission_id, status, details)
                return {"verification_status": status, "verification_details": details}

        test_result = sandbox.run_command(shlex.split(test_command), workdir="/workspace")

        code_summary = _generate_code_summary(tmpdir)

        test_output = (
            f"exit_code={test_result.exit_code}, "
            f"stdout={test_result.stdout[:1000]}, "
            f"stderr={test_result.stderr[:1000]}"
        )

        llm_result = None
        if test_result.success:
            llm_result = await judge_code_authenticity(
                goal_description=goal_description,
                code_summary=code_summary,
                test_results=test_output,
            )
            combined_verdict = test_result.success and llm_result.get("authentic", False)
        else:
            combined_verdict = False

        status = "verified" if combined_verdict else "failed"
        details = _build_verification_details(
            test_result,
            repo_url,
            branch,
            test_command,
            language,
            code_summary=code_summary,
            llm_result=llm_result,
        )
        details["stage"] = "test"

        if db is not None:
            await _persist_result(db, goal_id, submission_id, status, details)
        else:
            async with async_session() as session:
                await _persist_result(session, goal_id, submission_id, status, details)

        return {"verification_status": status, "verification_details": details}

    except RuntimeError as e:
        status = "failed"
        details = {
            "repo_url": repo_url,
            "branch": branch,
            "error": str(e),
            "stage": "clone",
        }
        if db is not None:
            await _persist_result(db, goal_id, submission_id, status, details)
        else:
            async with async_session() as session:
                await _persist_result(session, goal_id, submission_id, status, details)
        return {"verification_status": status, "verification_details": details}

    except Exception as e:
        status = "failed"
        details = {
            "repo_url": repo_url,
            "branch": branch,
            "error": f"Unexpected error: {e}",
            "stage": "unknown",
        }
        if db is not None:
            await _persist_result(db, goal_id, submission_id, status, details)
        else:
            async with async_session() as session:
                await _persist_result(session, goal_id, submission_id, status, details)
        return {"verification_status": status, "verification_details": details}

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _persist_result(
    db: AsyncSession,
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    status: str,
    details: dict,
):
    await persist_verification_result(db, goal_id, submission_id, status, details)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def run_dev_sandbox_verification_task(
    self,
    goal_id_str: str,
    submission_id_str: str,
    proof_data: dict,
    criteria_data: dict,
):
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            run_dev_sandbox_verification(
                goal_id=uuid.UUID(goal_id_str),
                submission_id=uuid.UUID(submission_id_str),
                proof_data=proof_data,
                criteria_data=criteria_data,
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    finally:
        loop.close()
