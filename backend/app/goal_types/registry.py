"""Goal-type registry with auto-discovery.

Discovers sub-packages under ``app.goal_types`` at import time, validates each
conforms to GoalTypeBase, and exposes ``list_types()`` and ``get_type(name)``.
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
    """
    global _discovered, _registry

    package_dir = Path(__file__).parent

    for finder_info in pkgutil.iter_modules([str(package_dir)]):
        if not finder_info.ispkg:
            continue
        name = finder_info.name
        if name.startswith("__"):
            continue

        try:
            mod = importlib.import_module(f"app.goal_types.{name}")
        except Exception:
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
    """Return sorted list of Celery worker module paths for all registered types.

    Modules follow the ``app.workers.X`` naming convention.
    """
    _ensure_discovered()
    modules = [f"app.workers.{name}" for name in sorted(_registry.keys())]
    # Always include payments and deadline which are not goal-type-specific
    for extra in ("app.workers.payments", "app.workers.deadline"):
        if extra not in modules:
            modules.append(extra)
    return sorted(modules)