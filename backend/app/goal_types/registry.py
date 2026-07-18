"""Goal-type registry with auto-discovery.

Discovers sub-packages under ``app.goal_types`` at import time, validates each
conforms to GoalTypeBase, and exposes ``list_types()`` and ``get_type(name)``.

Discovery is gated by a repo-local allowlist and a trusted-path check: only
modules whose names appear in ``ALLOWLISTED_GOAL_TYPES`` AND whose resolved
file location falls under the ``app/goal_types`` package directory are loaded.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from app.goal_types.base import GoalTypeBase

if TYPE_CHECKING:
    pass

# ── Trust policy ─────────────────────────────────────────────────────────────
# Only modules named here are eligible for discovery.  Adding a new built-in
# goal type requires listing it below so the registry will load it.

ALLOWLISTED_GOAL_TYPES: frozenset[str] = frozenset(
    {
        "api_endpoint",
        "dev_sandbox",
        "geolocation",
        "github_repo",
        "youtube_video",
    }
)


def _trusted_goal_types_root() -> Path:
    """Return the resolved absolute path of the goal_types package directory."""
    return Path(__file__).parent.resolve()


def _is_trusted_path(module: ModuleType) -> bool:
    """Check that *module*'s ``__file__`` resolves inside the trusted root."""
    mod_file = getattr(module, "__file__", None)
    if mod_file is None:
        return False
    resolved = Path(mod_file).resolve()
    try:
        resolved.relative_to(_trusted_goal_types_root())
    except ValueError:
        return False
    return True


# ── Registry state ───────────────────────────────────────────────────────────

_registry: dict[str, GoalTypeBase] = {}
_discovered: bool = False


class _DynamicGoalType(GoalTypeBase):
    """A concrete GoalTypeBase used for dynamic registration (e.g. smoke tests).

    Instances are constructed with name, description, sample_prompts,
    criteria_schema, a callable verify, and optional submit_proof and
    dispatch_verification.
    """

    def __init__(
        self,
        name: str,
        description: str,
        sample_prompts: list[str],
        criteria_schema: dict,
        verify,
        submit_proof=None,
        dispatch_verification=None,
    ):
        self.name = name
        self.description = description
        self.sample_prompts = sample_prompts
        self.criteria_schema = criteria_schema
        self._verify = verify
        self._submit_proof = submit_proof
        self._dispatch_verification = dispatch_verification or (lambda *a, **kw: None)

    async def verify(self, proof_data: dict, criteria_data: dict) -> dict:
        return await self._verify(proof_data, criteria_data)

    def submit_proof(self, proof_data: dict, criteria_data: dict) -> dict:
        if self._submit_proof is not None:
            return self._submit_proof(proof_data, criteria_data)
        return proof_data

    def dispatch_verification(
        self,
        goal_id: str,
        submission_id: str,
        proof_data: dict,
        criteria_data: dict,
    ) -> None:
        self._dispatch_verification(
            goal_id_str=goal_id,
            submission_id_str=submission_id,
            proof_data=proof_data,
            criteria_data=criteria_data,
        )


def _ensure_discovered() -> None:
    global _discovered, _registry
    if _discovered:
        return
    _discover()


def _discover() -> None:
    """Walk goal_types sub-packages and register goal-type instances.

    Each sub-package must expose a module-level ``goal_type`` attribute that is
    an instance of GoalTypeBase.

    Discovery enforces two trust checks before import:

    1. The package name must appear in ``ALLOWLISTED_GOAL_TYPES``.
    2. The imported module's ``__file__`` must resolve inside the
       ``app/goal_types`` directory tree.
    """
    global _discovered, _registry

    package_dir = Path(__file__).parent

    for finder_info in pkgutil.iter_modules([str(package_dir)]):
        if not finder_info.ispkg:
            continue
        name = finder_info.name
        if name.startswith("__"):
            continue

        # ── Trust policy: allowlist gate ──────────────────────────────────
        if name not in ALLOWLISTED_GOAL_TYPES:
            continue

        try:
            mod = importlib.import_module(f"app.goal_types.{name}")
        except Exception:
            continue

        # ── Trust policy: trusted-path gate ───────────────────────────────
        if not _is_trusted_path(mod):
            continue

        gt = getattr(mod, "goal_type", None)
        if gt is None or not isinstance(gt, GoalTypeBase):
            continue

        _registry[name] = gt

    _discovered = True


def list_types() -> list[str]:
    """Return sorted list of registered goal-type names."""
    _ensure_discovered()
    return sorted(_registry.keys())


def get_type(name: str) -> GoalTypeBase:
    """Return the GoalTypeBase instance for a registered goal type.

    Raises KeyError if the name is not registered.
    """
    _ensure_discovered()
    if name not in _registry:
        raise KeyError(f"Unknown goal type: {name}")
    return _registry[name]


def get_celery_include_modules() -> list[str]:
    """Return sorted list of Celery worker module paths.

    Enumerates the modules that actually exist under ``app.workers`` instead of
    deriving names from registered goal types — worker filenames don't always
    match goal-type names (e.g. the ``api_endpoint`` goal type's tasks live in
    ``app.workers.api_check``), and a derived name with no module behind it
    makes the Celery worker crash on boot.
    """
    import app.workers as workers_pkg

    package_dir = Path(workers_pkg.__file__).parent
    return sorted(
        f"app.workers.{info.name}"
        for info in pkgutil.iter_modules([str(package_dir)])
        if not info.name.startswith("__")
    )