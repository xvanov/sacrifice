"""
Smoke test for goal-type registry auto-discovery.

Creates a temporary ``_smoke/`` sub-package inside ``goal_types/``, verifies
that the registry picks it up without any other file changes, then removes
the package and verifies it is no longer listed.

NOTE: This test modifies the filesystem under ``app/goal_types/`` and must
clean up after itself even on failure.
"""

import importlib
import os
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

# The goal_types package directory
GOAL_TYPES_DIR = Path(__file__).resolve().parent.parent / "app" / "goal_types"
SMOKE_DIR = GOAL_TYPES_DIR / "_smoke"


@pytest.fixture(autouse=True)
def _cleanup_smoke_dir():
    """Ensure the _smoke directory is removed before and after each test."""
    if SMOKE_DIR.exists():
        _remove_smoke_package()
    yield
    if SMOKE_DIR.exists():
        _remove_smoke_package()


def _create_smoke_package():
    """Create a minimal _smoke sub-package with definition, verifier, and __init__."""
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)

    # __init__.py
    (SMOKE_DIR / "__init__.py").write_text(textwrap.dedent("""\
        from .definition import definition
        from .verifier import verify

        # Expose a module-level goal_type instance for registry auto-discovery
        from app.goal_types.registry import _DynamicGoalType
        goal_type = _DynamicGoalType(
            name=definition["name"],
            description=definition["description"],
            sample_prompts=definition["sample_prompts"],
            criteria_schema=definition["criteria_schema"],
            verify=verify,
        )
    """))

    # definition.py
    (SMOKE_DIR / "definition.py").write_text(textwrap.dedent("""\
        definition = {
            "name": "_smoke",
            "description": "A smoke-test goal type used to verify registry auto-discovery.",
            "sample_prompts": ["Run the smoke test"],
            "criteria_schema": {
                "type": "object",
                "properties": {"smoke_param": {"type": "string"}},
            },
        }
    """))

    # verifier.py
    (SMOKE_DIR / "verifier.py").write_text(textwrap.dedent("""\
        async def verify(proof_data: dict, criteria_data: dict) -> dict:
            return {
                "verification_status": "verified",
                "verification_details": {"smoke": True},
            }
    """))


def _remove_smoke_package():
    """Remove the _smoke directory tree and clean up cached modules."""
    # Remove any imported modules from sys.modules
    to_remove = [k for k in sys.modules if "goal_types._smoke" in k]
    for k in to_remove:
        del sys.modules[k]

    if SMOKE_DIR.exists():
        shutil.rmtree(SMOKE_DIR, ignore_errors=True)

    # Also remove __pycache__ under _smoke if it exists
    pycache = SMOKE_DIR / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache, ignore_errors=True)


@pytest.mark.order("first")
class TestRegistrySmokeDiscovery:
    """Smoke tests that the registry discovers newly added sub-packages."""

    def test_smoke_package_not_registered_initially(self):
        """Before creation, _smoke should not be in the registry.

        NOTE: This will fail if _smoke already exists or if the registry
        does not support re-discovery after initial import.
        """
        from app.goal_types import registry
        # Re-discover to get current state
        types = registry.list_types()
        assert "_smoke" not in types

    def test_smoke_package_discovered_after_creation(self):
        """After creating _smoke/ and re-importing registry, it should appear."""
        _create_smoke_package()

        # Force re-import of the registry module to trigger re-discovery
        import app.goal_types.registry
        importlib.reload(app.goal_types.registry)

        types = app.goal_types.registry.list_types()
        assert "_smoke" in types, (
            f"Registry should have discovered _smoke; got: {types}"
        )

    def test_smoke_package_has_correct_metadata(self):
        """The discovered _smoke type should expose correct metadata."""
        # The autouse cleanup fixture removes _smoke between tests, so this
        # test must create the package itself rather than relying on a prior
        # test's leftover state (which is why it used to KeyError).
        _create_smoke_package()

        import app.goal_types.registry
        importlib.reload(app.goal_types.registry)

        gt = app.goal_types.registry.get_type("_smoke")
        assert gt.name == "_smoke"
        assert "smoke" in gt.description.lower()
        assert isinstance(gt.sample_prompts, list)
        assert "smoke_param" in gt.criteria_schema.get("properties", {})

    def test_smoke_package_removed_after_cleanup(self):
        """After removing _smoke/ and re-importing registry, it should disappear."""
        _remove_smoke_package()

        import app.goal_types.registry
        importlib.reload(app.goal_types.registry)

        types = app.goal_types.registry.list_types()
        assert "_smoke" not in types, (
            f"Registry should no longer list _smoke after removal; got: {types}"
        )