import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import tarfile
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from urllib.parse import urlsplit, urlunsplit

import docker
import requests
import urllib3
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.crypto import resolve_submitted_token
from app.database import async_session
from app.services.llm import judge_code_authenticity
from app.services.verification_result import (
    FAILED,
    INCONCLUSIVE,
    REASON_CRITERIA_NOT_EVALUABLE,
    REASON_INTERNAL_ERROR,
    REASON_SANDBOX_INFRASTRUCTURE,
    REASON_UPSTREAM_UNAVAILABLE,
    persist_verification_result,
)

# Cap on the repo bytes we will package for the sandbox. The tar is streamed to
# disk, so this guards the daemon upload and the container, not worker RAM.
MAX_WORKSPACE_BYTES = 1024 * 1024 * 1024  # 1 GiB

# The workspace container's main process must outlive EVERY sequential exec, or
# Docker SIGKILLs an in-flight command when it exits (exit 137, no output, which
# used to read as a failed test run and charge the user). A per-step budget
# cannot express that, so idle effectively forever and rely on close() in the
# caller's finally. A literal count rather than `sleep infinity` because the
# image is configurable and busybox sleep does not portably accept "infinity".
IDLE_COMMAND = ["sleep", "2147483647"]

# Capabilities are dropped entirely: pip/npm/go/cargo need none of them, and the
# container runs as uid 0, where the default capability set is real privilege.
CAP_DROP = ["ALL"]
PIDS_LIMIT = 512

# Dedicated bridge network for sandbox containers. Passing no ``network=`` to
# ``containers.create`` attaches to Docker's DEFAULT bridge, which is a shared
# segment: on a normal deployment that put the sandbox one hop from the app's own
# Postgres (``sacrifice-db``, 172.17.0.2:5432) — reachable container-to-container,
# bypassing the host entirely and regardless of port publishing. Since the
# install step runs repo-authored code (``pip install -e .`` executes setup.py
# and build hooks), that was untrusted code with a route to the goals/pledges
# database. A single named network is reused rather than one per verification, so
# concurrent runs share it and nothing has to be torn down on a hot path.
SANDBOX_NETWORK_NAME = "sacrifice-sandbox"

# Stages that describe OUR failure rather than the submitter's. These are routed
# to the `inconclusive` outcome, which cannot reach the charge; a `failed` status
# carrying one of them is a routing bug (see _persist_result).
NON_CHARGING_STAGES = frozenset({"sandbox", "criteria", "internal", "upstream"})

# ── private-repo credentials ───────────────────────────────────────────────
#
# The PAT is handed to git through a credential helper that reads it from the
# environment, never from the command line. Leak vectors this closes, in order of
# severity:
#
# 1. **argv** — ``/proc/<pid>/cmdline`` is world-readable, so any local user
#    could read a token embedded in the remote URL (``https://tok@github.com/…``)
#    or passed via ``-c http.extraHeader=Authorization:…`` for as long as git
#    ran. The environment block (``/proc/<pid>/environ``) is 0400 owner-only, so
#    moving the secret there removes the world-readable exposure. Note the
#    ``-c credential.helper=<helper>`` argument itself is in argv, but it carries
#    only the *name* of the env var, not its value.
# 2. **.git/config** — a credentialled remote URL is written verbatim into the
#    clone's config, so the token would persist on disk and (absent the tar
#    exclusion) inside the container. A helper is never written to the config.
# 3. **stderr → verification_details → API response** — git can echo the remote
#    it failed on. Everything raised or persisted goes through ``_scrub_secrets``.
#
# ``git`` invokes helpers as ``sh -c '<helper> get'`` when the string starts with
# ``!``, so the shell function below is expanded by git, not by us.
_GIT_TOKEN_ENV = "SACRIFICE_GIT_TOKEN"
_CREDENTIAL_HELPER = (
    '!f() { printf "username=x-access-token\\npassword=%s\\n" '
    f'"${_GIT_TOKEN_ENV}"; }}; f'
)

# Matches the userinfo of an http(s) URL anywhere in a string, for scrubbing text
# (not for parsing a URL — see ``_split_repo_credentials``).
_URL_USERINFO_RE = re.compile(r"(?P<scheme>https?://)[^/\s@]*@")

# Unambiguous "your credential was rejected" signals from git. These describe the
# submitter's input — the repo they named, and the token they did or did not
# supply — so they stay a charging `failed`, with a message that says what to fix.
_AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "could not read username",
    "could not read password",
    "terminal prompts disabled",
    "invalid username or password",
    "repository not found",
    "permission denied",
    "access denied",
    "http basic: access denied",
)

# Reachability canary for attributing a clone failure. Deliberately a host WE
# name, for the same reason as ``EGRESS_PROBE_HOST``: see _worker_egress_is_broken.
CLONE_EGRESS_PROBE_HOST = "github.com"
CLONE_EGRESS_PROBE_PORT = 443

# Host used to tell "our egress is broken" apart from "their requirements are
# broken" when an install fails. Deliberately OUR index rather than anything
# named in the submitter's requirements: a user could otherwise point a
# dependency at a dead host and have the failure blamed on us, dodging the
# pledge. See the note in _install_outcome.
EGRESS_PROBE_HOST = "pypi.org"
EGRESS_PROBE_PORT = 443

logger = logging.getLogger(__name__)


class SandboxSetupError(Exception):
    """Raised when the sandbox itself cannot be brought up or kept running.

    Maps to ``inconclusive`` / ``REASON_SANDBOX_INFRASTRUCTURE``: our fault, so
    it must never reach the pledge charge.

    Deliberately not a ``CloneError``: a sandbox fault must never be reported
    to the user as their repo failing to clone.
    """


class CloneError(RuntimeError):
    """The repo/branch the user named could not be cloned.

    Their input, so it maps to a `failed` verdict and charges. A RuntimeError
    subclass because that is this module's long-standing clone contract, but a
    *distinct* type: catching bare RuntimeError blamed the user's repo for any
    RuntimeError raised anywhere in the flow, including our own bugs.
    """


class CloneUnavailableError(Exception):
    """The clone could not be *attempted* because our own egress is down.

    Distinct from ``CloneError``, which means git reached the remote and the
    remote said no. This one means we never got a usable answer, so there is no
    evidence about the user's repo at all. Maps to ``inconclusive`` /
    ``REASON_UPSTREAM_UNAVAILABLE`` — transient, retried by the reconciler, and
    it can never reach the charge.
    """


class CriteriaNotEvaluableError(Exception):
    """Raised when we accepted a goal this sandbox cannot check at all.

    Maps to ``inconclusive`` / ``REASON_CRITERIA_NOT_EVALUABLE`` — permanent, so
    W6's contract escalates it to operator review immediately instead of
    retrying. Ours because we accepted the goal: e.g. a language whose toolchain
    is absent from the sandbox image, or a test command that WE supplied (via
    criteria) and cannot parse.
    """


class SandboxResult:
    def __init__(
        self, exit_code: int, stdout: str, stderr: str, timed_out: bool = False
    ):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def detect_language(repo_path: str) -> str:
    files = set()
    for root, _dirs, filenames in os.walk(repo_path):
        for f in filenames:
            files.add(f.lower())

    if any(
        f in files
        for f in ("requirements.txt", "setup.py", "setup.cfg", "pyproject.toml")
    ):
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
    """Normalise a repo URL to its ``.git`` form.

    NOTE: not used by the clone path — ``clone_repo`` hands the submitted URL to
    git verbatim, which is correct (git accepts both forms, and appending
    ``.git`` would break local/``file://`` sources). Kept because it is part of
    the module's tested surface.
    """
    if url.endswith(".git"):
        return url, branch
    return f"{url}.git", branch


def _scrub_secrets(text: str, token: str | None = None) -> str:
    """Strip credential material from anything we log, raise or persist.

    Belt-and-braces on top of keeping the token out of argv and out of the remote
    URL: git's own diagnostics are not a stable interface, so a token must be
    unable to survive this function even if a future git version echoes one.
    """
    out = text or ""
    if token:
        out = out.replace(token, "[redacted]")
    return _URL_USERINFO_RE.sub(r"\g<scheme>", out)


def _split_repo_credentials(url: str) -> tuple[str, str | None]:
    """Separate an http(s) repo URL from any credential in its userinfo.

    Users do paste ``https://<pat>@github.com/owner/repo`` into the repo field.
    Left in place that PAT would ride in git's argv, be written into the clone's
    ``.git/config``, and — because ``repo_url`` is copied into
    ``verification_details`` — be persisted to the database and echoed back by
    the verification-status endpoint. So the secret is lifted out here, re-supplied
    through the credential helper, and only the scrubbed URL is ever cloned,
    logged or stored.

    Restricted to http/https on purpose: in ``ssh://git@host/…`` the userinfo is
    the login name, not a secret, and stripping it would break the clone.
    """
    raw = url or ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw, None
    if parts.scheme not in ("http", "https") or "@" not in parts.netloc:
        return raw, None

    userinfo, _, hostport = parts.netloc.rpartition("@")
    user, sep, password = userinfo.partition(":")
    # A PAT is accepted by GitHub as either half, but when both are present the
    # password is the secret and the username is a placeholder.
    secret = password if sep else user
    clean = urlunsplit(
        (parts.scheme, hostport, parts.path, parts.query, parts.fragment)
    )
    return clean, (secret or None)


def _worker_egress_is_broken() -> bool:
    """Can the WORKER still reach a host we choose? Only asked after a clone failed.

    Same reasoning as ``_egress_is_broken`` one level down: attributing a clone
    failure by grepping git's stderr for network-shaped words would let a
    submitter point ``repo_url`` at a host they know is dead and have their own
    input charged to us. The probe target is ours, so it is not user-steerable —
    it answers "is our egress up", not "was their host up".
    """
    try:
        socket.create_connection(
            (CLONE_EGRESS_PROBE_HOST, CLONE_EGRESS_PROBE_PORT), timeout=10
        ).close()
        return False
    except OSError:
        return True


def _is_auth_failure(stderr: str) -> bool:
    """Did the remote reject the credential (or its absence)?"""
    lowered = (stderr or "").lower()
    return any(marker in lowered for marker in _AUTH_FAILURE_MARKERS)


def clone_repo(
    url: str,
    branch: str,
    target_dir: str,
    github_token: str | None = None,
) -> None:
    """Shallow-clone ``url`` into ``target_dir``, optionally authenticated.

    ``github_token`` is a *plaintext* PAT (callers decrypt first). It reaches git
    through a credential helper that reads it from the environment — see the
    ``_CREDENTIAL_HELPER`` block for the leak vectors that closes. A token is
    optional and public repos clone exactly as before.

    Raises ``CloneError`` when the remote answered and refused (the user's input,
    so it charges) and ``CloneUnavailableError`` when our own egress is down (our
    fault, so it must not).
    """
    url, url_token = _split_repo_credentials(url)
    token = github_token or url_token

    argv = ["git"]
    env = dict(os.environ)
    # Without this git blocks on an interactive username prompt for a private
    # repo and only fails at the 120s timeout, which reads as "their repo is
    # huge" rather than "they gave us no usable credential".
    env["GIT_TERMINAL_PROMPT"] = "0"

    if token:
        argv += [
            # Clear inherited helpers first: a host-level credential store could
            # otherwise answer ahead of ours, or *persist* the submitter's token
            # into the worker's own credential store.
            "-c",
            "credential.helper=",
            "-c",
            f"credential.helper={_CREDENTIAL_HELPER}",
        ]
        env[_GIT_TOKEN_ENV] = token

    argv += ["clone", "--depth=1", "--branch", branch, url, target_dir]

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        # Would otherwise escape as a non-RuntimeError and be mislabelled
        # `stage: "unknown"` with an opaque message.
        raise CloneError(
            f"Failed to clone repo {url} (branch: {branch}): timed out after 120s"
        ) from exc

    if result.returncode != 0:
        stderr = _scrub_secrets(result.stderr, token)
        if _is_auth_failure(stderr):
            # Their credential, their repo. Charging is correct, but the message
            # has to be actionable or the user cannot tell what to fix.
            hint = (
                "the token was rejected or lacks access to this repository"
                if token
                else "this repository is not public and no access token was provided"
            )
            raise CloneError(
                f"Failed to clone repo {url} (branch: {branch}): {hint}. {stderr[:500]}"
            )
        if _worker_egress_is_broken():
            raise CloneUnavailableError(
                f"clone of {url} failed and {CLONE_EGRESS_PROBE_HOST} is "
                "unreachable from the worker — our egress, not the submitted repo"
            )
        raise CloneError(
            f"Failed to clone repo {url} (branch: {branch}): {stderr[:500]}"
        )


def _read_timeout_in_chain(exc: BaseException) -> bool:
    """Does this exception chain bottom out in a urllib3 read timeout?

    ``container.wait(timeout=N)`` does NOT raise ``docker.errors.APIError`` —
    the deadline trips in the HTTP layer, so requests raises
    ``ConnectionError`` wrapping urllib3's ``ReadTimeoutError`` (verified
    against docker-py 7.1.0 / Docker 29.x). We match on that wrapped cause
    rather than on the exception class, because plain ``ConnectionError`` is
    also what a *dead daemon* looks like — and those two must not share a
    verdict (see ``_is_transport_fault``).
    """
    seen: list[int] = []
    current: BaseException | None = exc
    while current is not None and len(seen) < 8 and id(current) not in seen:
        seen.append(id(current))
        if isinstance(current, urllib3.exceptions.ReadTimeoutError):
            return True
        nxt = current.__cause__ or current.__context__
        if nxt is None and current.args and isinstance(current.args[0], BaseException):
            nxt = current.args[0]
        current = nxt
    return False


def _is_deadline_timeout(exc: BaseException) -> bool:
    """True only for "the command outlived its deadline", never for infra faults."""
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return _read_timeout_in_chain(exc)
    if isinstance(exc, docker.errors.APIError):
        # The daemon itself reporting the wait deadline it was given.
        return "timed out" in str(exc).lower() or "timeout" in str(exc).lower()
    return False


def _is_transport_fault(exc: BaseException) -> bool:
    """A broken daemon/socket — our infrastructure failing, not the user's code.

    This distinction is a billing control: a ``failed`` verdict charges the
    user's card, so a daemon restart mid-exec must surface as
    ``SandboxSetupError`` (stage ``sandbox``, no charge) and never as a user
    timeout or a failed test run.
    """
    return isinstance(
        exc, (requests.exceptions.RequestException, docker.errors.DockerException)
    )


def _tar_directory(path: str, max_bytes: int | None = None):
    """Tar a directory's contents into a temp file for ``put_archive``.

    Streamed to disk rather than ``io.BytesIO``: the Celery worker also runs
    beat, so holding a multi-GB tar in RAM could OOM-kill the deadline sweep
    and every concurrent verification alongside this one.

    ``.git`` is excluded — nothing in the container reads it (no git command
    runs there, and ``detect_language``/``_generate_code_summary`` read the host
    clone), and a ``--depth=1`` clone's history roughly doubles the payload.

    Returns an open file object positioned at 0; the caller must close it.
    """
    # Read the module constant at call time so deployments (and tests) can move
    # the cap without the default being frozen at import.
    max_bytes = MAX_WORKSPACE_BYTES if max_bytes is None else max_bytes
    total = 0
    for root, dirs, filenames in os.walk(path):
        if ".git" in dirs:
            dirs.remove(".git")
        for fname in filenames:
            fpath = os.path.join(root, fname)
            if os.path.islink(fpath):
                continue
            try:
                total += os.path.getsize(fpath)
            except OSError:
                continue
            if total > max_bytes:
                raise SandboxSetupError(
                    f"Repo exceeds the {max_bytes // (1024 * 1024)} MiB sandbox limit"
                )

    handle = tempfile.TemporaryFile()
    try:
        with tarfile.open(fileobj=handle, mode="w") as tf:
            tf.add(path, arcname=".", filter=_exclude_git)
        handle.seek(0)
    except Exception:
        handle.close()
        raise
    return handle


def _exclude_git(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if info.name == "./.git" or info.name.startswith("./.git/"):
        return None
    return info


class DockerSandbox:
    """Runs commands inside a locked-down container.

    Two modes:

    * **ephemeral** (default) — every ``run_command`` starts a throwaway
      container with ``network_disabled=True`` and no workspace. Suitable for
      one-off commands that need nothing from the repo.
    * **workspace** — ``prepare_workspace(repo_path)`` starts ONE long-lived
      container and copies the repo into ``/workspace`` with ``put_archive``,
      after which each ``run_command`` is an ``exec_run`` inside that same
      container. This is what verification uses, for two reasons: the repo has
      to actually be visible to the commands, and dependencies installed by the
      install step must still be there when the test step runs.

    Why copy instead of bind-mount: the Celery worker may itself be a container
    talking to the host daemon over /var/run/docker.sock, in which case its
    tmpdir path does not exist on the daemon's host and a ``volumes=`` mount
    would silently mount an empty dir.
    """

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
        self.workspace_container = None
        self._client = docker.from_env()

    # ── workspace mode ────────────────────────────────────────────────

    def _ensure_sandbox_network(self):
        """Return the dedicated sandbox network, creating it if absent.

        Isolation posture (see ``SANDBOX_NETWORK_NAME``): a user-defined bridge
        is what buys the separation. Docker's ``DOCKER-ISOLATION-STAGE-*``
        chains drop traffic between distinct bridge networks, while each
        user-defined bridge still gets its own MASQUERADE rule — so the sandbox
        loses its route to every compose project's containers (and to the
        default bridge) but keeps the internet egress the dependency install
        needs. Verified empirically on Docker 29.4.3: from a container on this
        network, ``pypi.org:443`` connects and ``172.17.0.2:5432`` times out.

        Deliberately NOT ``internal=True``: that also severs the package index,
        which would turn every install into a sandbox fault.

        Known residual, out of this function's reach: cross-network isolation
        does not close ports the host publishes on *all* interfaces, which a
        container can still reach via this network's gateway. That is closed
        where the port is published — ``docker-compose.yml`` binds Postgres to
        ``127.0.0.1:5433`` — not here. Both halves are needed; neither alone is
        sufficient.

        Fails closed: if the network cannot be resolved we raise rather than let
        ``containers.create`` fall back to the default bridge, because the
        silent fallback is exactly the exposure this method exists to remove.
        """
        try:
            return self._client.networks.get(SANDBOX_NETWORK_NAME)
        except docker.errors.NotFound:
            pass
        except Exception as exc:
            raise SandboxSetupError(
                f"Could not look up sandbox network {SANDBOX_NETWORK_NAME!r}: {exc}"
            ) from exc

        try:
            return self._client.networks.create(SANDBOX_NETWORK_NAME, driver="bridge")
        except Exception as exc:
            # Two verifications racing to create the same network is the normal
            # case, not an error: the loser gets 409/"already exists", so re-get
            # before giving up.
            try:
                return self._client.networks.get(SANDBOX_NETWORK_NAME)
            except Exception:
                raise SandboxSetupError(
                    f"Could not create sandbox network {SANDBOX_NETWORK_NAME!r}: {exc}"
                ) from exc

    def prepare_workspace(self, repo_path: str, workdir: str = "/workspace") -> None:
        """Start the long-lived container and copy the repo into ``workdir``.

        The container is started WITH network access so the dependency-install
        step can reach the package index; ``isolate_network()`` must be called
        before any user-authored code is run. See the note on that method.

        That egress is scoped to a dedicated network rather than the default
        bridge — see ``_ensure_sandbox_network``.
        """
        # Resolved before the tar so a network fault cannot leak the temp file.
        network = self._ensure_sandbox_network()

        try:
            payload = _tar_directory(repo_path)
        except SandboxSetupError:
            raise
        except OSError as exc:
            raise SandboxSetupError(
                f"Could not package repo for sandbox: {exc}"
            ) from exc

        try:
            container = self._client.containers.create(
                image=self.image,
                command=IDLE_COMMAND,
                working_dir=workdir,
                mem_limit=self.memory_limit,
                nano_cpus=int(self.cpu_limit * 1e9),
                pids_limit=PIDS_LIMIT,
                cap_drop=CAP_DROP,
                detach=True,
                privileged=False,
                security_opt=["no-new-privileges:true"],
                # Keeps the container off the default bridge. Set at create time
                # (not connect-after-start) so there is no window in which the
                # container is attached to the shared segment.
                network=getattr(network, "name", SANDBOX_NETWORK_NAME),
            )
        except Exception as exc:
            payload.close()
            raise SandboxSetupError(
                f"Could not create sandbox container: {exc}"
            ) from exc

        self.workspace_container = container

        try:
            container.start()
            # working_dir is created by the daemon, so put_archive lands cleanly.
            if not container.put_archive(workdir, payload):
                raise SandboxSetupError(f"Docker refused the repo upload to {workdir}")
        except SandboxSetupError:
            self._cleanup_workspace_container()
            raise
        except Exception as exc:
            self._cleanup_workspace_container()
            raise SandboxSetupError(
                f"Could not upload repo into sandbox: {exc}"
            ) from exc
        finally:
            payload.close()

    def isolate_network(self) -> None:
        """Detach the workspace container from every network. Fail closed.

        Posture: dependency installation genuinely needs egress, but running
        the submitter's own test code with egress is a security regression (it
        could exfiltrate anything the worker can reach, or phone home to fake a
        passing run). The docker SDK has no per-exec network toggle, so we
        install with the network attached and then disconnect the container
        before executing the user's test command — verified to yield
        ``Network is unreachable`` for subsequent execs.

        If a disconnect fails we raise rather than continue, because the
        alternative is silently running untrusted tests with live egress.
        """
        container = self.workspace_container
        if container is None:
            raise SandboxSetupError(
                "isolate_network() called before prepare_workspace()"
            )

        try:
            container.reload()
            networks = list(container.attrs["NetworkSettings"]["Networks"])
        except Exception as exc:
            raise SandboxSetupError(
                f"Could not inspect sandbox networks: {exc}"
            ) from exc

        for name in networks:
            try:
                self._client.networks.get(name).disconnect(container)
            except Exception as exc:
                raise SandboxSetupError(
                    f"Could not detach sandbox from network {name!r}: {exc}"
                ) from exc

    def _exec_in_workspace(
        self,
        command: list[str],
        workdir: str,
        env: dict | None,
    ) -> SandboxResult:
        container = self.workspace_container

        # Host-side timeout: exec_run has no timeout of its own, and enforcing
        # it in-container (coreutils `timeout`) would depend on the image and be
        # in reach of the code we are containing. Killing the container from
        # here cannot be evaded.
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(
                container.exec_run,
                command,
                workdir=workdir,
                environment=env,
                demux=True,
            )
            try:
                exec_result = future.result(timeout=self.timeout)
            except FutureTimeoutError:
                try:
                    container.kill()
                except Exception:
                    pass
                return SandboxResult(exit_code=-1, stdout="", stderr="", timed_out=True)
        except Exception as exc:
            # FutureTimeoutError above is the authoritative deadline here, so
            # anything else from the transport is our infrastructure breaking,
            # not the submitter's code — and must not reach a charging verdict.
            if _is_transport_fault(exc):
                raise SandboxSetupError(
                    f"Docker transport failed mid-exec: {exc}"
                ) from exc
            raise
        finally:
            # Never join: on timeout the worker thread is blocked on a socket
            # that only unblocks once the container dies.
            pool.shutdown(wait=False)

        raw_out, raw_err = exec_result.output if exec_result.output else (None, None)
        return SandboxResult(
            exit_code=exec_result.exit_code
            if exec_result.exit_code is not None
            else -1,
            stdout=raw_out.decode("utf-8", errors="replace") if raw_out else "",
            stderr=raw_err.decode("utf-8", errors="replace") if raw_err else "",
        )

    # ── ephemeral mode ────────────────────────────────────────────────

    def run_command(
        self,
        command: list[str],
        workdir: str = "/workspace",
        env: dict | None = None,
    ) -> SandboxResult:
        if self.workspace_container is not None:
            return self._exec_in_workspace(command, workdir, env)

        try:
            container = self._client.containers.run(
                image=self.image,
                command=command,
                working_dir=workdir,
                environment=env,
                mem_limit=self.memory_limit,
                nano_cpus=int(self.cpu_limit * 1e9),
                pids_limit=PIDS_LIMIT,
                cap_drop=CAP_DROP,
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

        except Exception as exc:
            if _is_deadline_timeout(exc):
                try:
                    container.kill()
                except Exception:
                    pass
                return SandboxResult(exit_code=-1, stdout="", stderr="", timed_out=True)
            if _is_transport_fault(exc):
                raise SandboxSetupError(
                    f"Docker transport failed mid-run: {exc}"
                ) from exc
            raise

        finally:
            self._cleanup_container()

    # ── teardown ──────────────────────────────────────────────────────

    def _cleanup_container(self):
        if self.container is not None:
            try:
                self.container.remove(force=True)
            except Exception:
                pass
            self.container = None

    def _cleanup_workspace_container(self):
        if self.workspace_container is not None:
            try:
                self.workspace_container.remove(force=True)
            except Exception:
                pass
            self.workspace_container = None

    def close(self) -> None:
        """Remove every container this sandbox created. Safe to call twice."""
        self._cleanup_container()
        self._cleanup_workspace_container()

    def __enter__(self) -> "DockerSandbox":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _extract_function_signatures(filepath: str) -> list[str]:
    signatures = []
    try:
        with open(filepath, "r", errors="replace") as f:
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


def parse_test_command(test_command: str) -> list[str]:
    """Split the submitted test command into argv, rejecting unusable values.

    Raised as ValueError so the API can answer 400 at submission time instead of
    the worker burning a verification (and a charge) on a typo.
    """
    try:
        argv = shlex.split(test_command or "")
    except ValueError as exc:
        raise ValueError(f"test_command could not be parsed ({exc})") from exc
    if not argv:
        raise ValueError("test_command must not be empty")
    return argv


def _is_infrastructure_kill(result: SandboxResult) -> bool:
    """SIGKILL (128+9) with no output at all — the container died under us.

    A submitter's own suite can be OOM-killed too, but it produces output
    first; a *silent* 137 means the container went away mid-exec, which is what
    the old per-step backstop `sleep` did. The ambiguity is resolved in the
    user's favour on purpose: the alternative charges their card for our fault.
    """
    return (
        result.exit_code == 137
        and not (result.stdout or "").strip()
        and not (result.stderr or "").strip()
    )


def _resolve_test_command(proof_data: dict, criteria_data: dict) -> tuple[str, str]:
    """Return the test command and WHO supplied it.

    The source decides who pays when it cannot be parsed: a command the
    submitter typed is their problem (``failed``), one that came from the stored
    criteria — which our own goal-creation flow writes — is ours
    (``inconclusive`` / criteria-not-evaluable). Same string, different fault.
    """
    if proof_data.get("test_command") is not None:
        return proof_data["test_command"], "proof"
    if criteria_data.get("test_command") is not None:
        return criteria_data["test_command"], "criteria"
    return "python -m pytest -v", "default"


def _egress_is_broken(sandbox: "DockerSandbox") -> bool:
    """Can the sandbox still reach OUR package index?

    Used only after an install has already failed, to attribute the failure.
    Probing a host WE choose is the whole point: parsing pip's stderr for
    network-looking words would let a submitter aim a dependency at a dead host
    and have their broken requirements charged to us.
    """
    probe = sandbox.run_command(
        [
            "python",
            "-c",
            "import socket; socket.create_connection("
            f"({EGRESS_PROBE_HOST!r}, {EGRESS_PROBE_PORT}), timeout=10).close()",
        ]
    )
    return not probe.success


def _toolchain_is_missing(sandbox: "DockerSandbox", install_cmd: list[str]) -> bool:
    """Is the installer this repo needs absent from the sandbox image?

    `get_install_command` happily returns npm/go/cargo, but the sandbox image is
    a Python one, so those submissions can only ever fail at install. That is a
    goal we accepted and cannot evaluate — never the submitter's fault.
    """
    probe = sandbox.run_command(
        ["sh", "-c", f"command -v {shlex.quote(install_cmd[0])}"]
    )
    return not probe.success


async def run_dev_sandbox_verification(
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    proof_data: dict,
    criteria_data: dict,
    db: AsyncSession | None = None,
) -> dict:
    raw_repo_url = proof_data.get("repo_url", criteria_data.get("repo_url", ""))
    # Two variables on purpose: the clone gets what the user actually submitted,
    # but everything persisted or echoed back uses the scrubbed form, so a PAT
    # pasted into the URL field never reaches the database or the API response.
    repo_url, _ = _split_repo_credentials(raw_repo_url)
    branch = proof_data.get("branch", criteria_data.get("branch", "main"))
    test_command, command_source = _resolve_test_command(proof_data, criteria_data)
    goal_description = criteria_data.get("goal_description", "")

    async def _finish(status, details, inconclusive_reason=None):
        if db is not None:
            await _persist_result(
                db,
                goal_id,
                submission_id,
                status,
                details,
                inconclusive_reason=inconclusive_reason,
            )
        else:
            async with async_session() as session:
                await _persist_result(
                    session,
                    goal_id,
                    submission_id,
                    status,
                    details,
                    inconclusive_reason=inconclusive_reason,
                )
        return {"verification_status": status, "verification_details": details}

    tmpdir = tempfile.mkdtemp(prefix="sacrifice_sandbox_")
    try:
        # An unusable command is a validation failure, and who pays depends on
        # who wrote it (see _resolve_test_command). submit_proof rejects the
        # submitter's version with a 400, so this is the defensive half.
        try:
            test_argv = parse_test_command(test_command)
        except ValueError as exc:
            if command_source == "proof":
                return await _finish(
                    FAILED,
                    {
                        "repo_url": repo_url,
                        "branch": branch,
                        "test_command": test_command,
                        "stage": "validation",
                        "error": str(exc),
                    },
                )
            raise CriteriaNotEvaluableError(
                f"stored test_command is unusable ({exc})"
            ) from exc

        # Decrypted inside the try so a corrupt ciphertext or a rotated key on a
        # token WE stored lands on the generic handler below (inconclusive /
        # internal_error) — our storage, our key, never billed as a failed
        # pledge. A token planted in criteria_data is the user's and is ignored
        # rather than excused; see resolve_submitted_token.
        github_token = resolve_submitted_token(proof_data, criteria_data)

        clone_repo(raw_repo_url, branch, tmpdir, github_token=github_token)

        language = detect_language(tmpdir)
        install_cmd = get_install_command(language, tmpdir)

        sandbox = DockerSandbox(timeout=600)
        try:
            # Copy the clone into the container: without this /workspace is
            # empty and neither the install nor the test command can ever see
            # the repo, so every submission failed regardless of its code.
            sandbox.prepare_workspace(tmpdir)

            if install_cmd:
                if _toolchain_is_missing(sandbox, install_cmd):
                    raise CriteriaNotEvaluableError(
                        f"the sandbox image has no {install_cmd[0]!r} toolchain, so a "
                        f"{language} repo cannot be verified here"
                    )

                install_result = sandbox.run_command(install_cmd, workdir="/workspace")
                if _is_infrastructure_kill(install_result):
                    raise SandboxSetupError(
                        "Sandbox container died during dependency install "
                        "(exit 137, no output)"
                    )
                if not install_result.success:
                    # Attribute the failure before billing anyone for it: if the
                    # sandbox cannot reach our index, the install never had a
                    # chance and the fault is ours.
                    if _egress_is_broken(sandbox):
                        raise SandboxSetupError(
                            f"dependency install failed and {EGRESS_PROBE_HOST} is "
                            "unreachable from the sandbox — our egress, not the "
                            "submitted requirements"
                        )
                    details = _build_verification_details(
                        install_result, repo_url, branch, test_command, language
                    )
                    details["stage"] = "install"
                    details["error"] = (
                        "Dependency installation timed out"
                        if install_result.timed_out
                        else f"Dependency installation failed: {install_result.stderr[:500]}"
                    )
                    return await _finish(FAILED, details)

            # Cut egress before running submitter-authored code.
            sandbox.isolate_network()

            test_result = sandbox.run_command(test_argv, workdir="/workspace")
            if _is_infrastructure_kill(test_result):
                raise SandboxSetupError(
                    "Sandbox container died during the test command "
                    "(exit 137, no output)"
                )
        finally:
            sandbox.close()

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
            combined_verdict = test_result.success and llm_result.get(
                "authentic", False
            )
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

        return await _finish(status, details)

    except SandboxSetupError as e:
        # Our infrastructure broke. `inconclusive` never reaches the charge, and
        # W6's contract re-dispatches it until the attempt cap, then escalates.
        # Explicitly not `stage: clone`, which would blame the user's repo.
        return await _finish(
            INCONCLUSIVE,
            {
                "repo_url": repo_url,
                "branch": branch,
                "stage": "sandbox",
                "inconclusive_detail": str(e),
            },
            inconclusive_reason=REASON_SANDBOX_INFRASTRUCTURE,
        )

    except CriteriaNotEvaluableError as e:
        # We accepted a goal this sandbox cannot check. Permanent, so the
        # contract skips the retry loop and escalates immediately.
        return await _finish(
            INCONCLUSIVE,
            {
                "repo_url": repo_url,
                "branch": branch,
                "test_command": test_command,
                "stage": "criteria",
                "inconclusive_detail": str(e),
            },
            inconclusive_reason=REASON_CRITERIA_NOT_EVALUABLE,
        )

    except CloneUnavailableError as e:
        # We never reached the remote, so we have no evidence about the user's
        # repo. Our egress, so it must not charge.
        return await _finish(
            INCONCLUSIVE,
            {
                "repo_url": repo_url,
                "branch": branch,
                "stage": "upstream",
                "inconclusive_detail": _scrub_secrets(str(e)),
            },
            inconclusive_reason=REASON_UPSTREAM_UNAVAILABLE,
        )

    except CloneError as e:
        # The repo or branch the user named does not exist, is not public, or
        # rejected the token they supplied. Their input, so it is a real `failed`
        # verdict and it charges. Scrubbed again here because this string is
        # persisted and echoed back by the verification-status endpoint.
        return await _finish(
            FAILED,
            {
                "repo_url": repo_url,
                "branch": branch,
                "error": _scrub_secrets(str(e)),
                "stage": "clone",
            },
        )

    except Exception as e:
        # An unexpected exception in OUR orchestration. This used to be a
        # charging `stage: unknown` catch-all; a bug in this worker is not a
        # failed pledge.
        logger.exception("dev_sandbox verification crashed for goal %s", goal_id)
        return await _finish(
            INCONCLUSIVE,
            {
                "repo_url": repo_url,
                "branch": branch,
                "stage": "internal",
                # Scrubbed: an unexpected exception can be raised from anywhere,
                # including code holding the decrypted token.
                "inconclusive_detail": _scrub_secrets(f"Unexpected error: {e}"),
            },
            inconclusive_reason=REASON_INTERNAL_ERROR,
        )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _persist_result(
    db: AsyncSession,
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    status: str,
    details: dict,
    *,
    inconclusive_reason: str | None = None,
):
    stage = details.get("stage")
    if status == FAILED and stage in NON_CHARGING_STAGES:
        # Defense in depth: a `failed` status here charges a real card, and these
        # stages describe OUR failure. Rather than bill for a routing slip, route
        # it the way the caller should have — the outcome still lands, still gets
        # retried, and still shows up for operators.
        logger.error(
            "Routing bug: stage %r must not be reported as %s (goal %s); "
            "recording as inconclusive instead",
            stage,
            FAILED,
            goal_id,
        )
        status = INCONCLUSIVE
        inconclusive_reason = inconclusive_reason or REASON_INTERNAL_ERROR
        details = {k: v for k, v in details.items() if k != "failure_reason"}

    await persist_verification_result(
        db,
        goal_id,
        submission_id,
        status,
        details,
        inconclusive_reason=inconclusive_reason,
    )


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
        raise self.retry(exc=exc)
    finally:
        loop.close()
