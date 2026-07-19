"""
Smoke / trust-policy tests for goal-type registry discovery.

Creates a temporary ``_smoke/`` sub-package inside ``goal_types/``, then
exercises the allowlist and trusted-path gates to prove that discovery only
loads modules that satisfy both checks.

NOTE: These tests modify the filesystem under ``app/goal_types/`` and must
clean up after themselves even on failure.
"""

import importlib
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest import mock

import pytest
from app.goal_types.registry import (
    _is_trusted_path,
)

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
    (SMOKE_DIR / "__init__.py").write_text(
        textwrap.dedent("""\
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
    """)
    )

    # definition.py
    (SMOKE_DIR / "definition.py").write_text(
        textwrap.dedent("""\
        definition = {
            "name": "_smoke",
            "description": "A smoke-test goal type used to verify registry auto-discovery.",
            "sample_prompts": ["Run the smoke test"],
            "criteria_schema": {
                "type": "object",
                "properties": {"smoke_param": {"type": "string"}},
            },
        }
    """)
    )

    # verifier.py
    (SMOKE_DIR / "verifier.py").write_text(
        textwrap.dedent("""\
        async def verify(proof_data: dict, criteria_data: dict) -> dict:
            return {
                "verification_status": "verified",
                "verification_details": {"smoke": True},
            }
    """)
    )


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


def _reload_registry():
    """Force re-import of the registry module to trigger re-discovery."""
    import app.goal_types.registry

    importlib.reload(app.goal_types.registry)
    return app.goal_types.registry


# ── Allowlist-gate tests ──────────────────────────────────────────────────────


class TestAllowlistGate:
    """Prove the allowlist check is enforced at discovery time."""

    def test_non_allowlisted_package_not_registered(self):
        """A package on a trusted path but NOT in the allowlist is skipped."""
        _create_smoke_package()
        registry = _reload_registry()

        types = registry.list_types()
        assert "_smoke" not in types, (
            f"_smoke is not allowlisted and must not be discovered; got: {types}"
        )

    def test_allowlisted_package_discovered(self):
        """A package on a trusted path AND in the allowlist IS loaded."""
        _create_smoke_package()

        registry = _reload_registry()
        # Patch allowlist AFTER reload (so reload doesn't overwrite the
        # patch), BEFORE discovery runs in list_types().
        saved = registry.ALLOWLISTED_GOAL_TYPES
        registry.ALLOWLISTED_GOAL_TYPES = saved | {"_smoke"}
        try:
            types = registry.list_types()
        finally:
            registry.ALLOWLISTED_GOAL_TYPES = saved

        assert "_smoke" in types, (
            f"_smoke was allowlisted; registry should have discovered it; got: {types}"
        )

    def test_allowlisted_package_has_correct_metadata(self):
        """Discovered allowlisted type exposes correct metadata."""
        _create_smoke_package()

        registry = _reload_registry()
        saved = registry.ALLOWLISTED_GOAL_TYPES
        registry.ALLOWLISTED_GOAL_TYPES = saved | {"_smoke"}
        try:
            gt = registry.get_type("_smoke")
        finally:
            registry.ALLOWLISTED_GOAL_TYPES = saved

        assert gt.name == "_smoke"
        assert "smoke" in gt.description.lower()
        assert isinstance(gt.sample_prompts, list)
        assert "smoke_param" in gt.criteria_schema.get("properties", {})

    def test_package_removed_after_cleanup(self):
        """After removing the package, it disappears from the registry."""
        _create_smoke_package()

        registry = _reload_registry()
        saved = registry.ALLOWLISTED_GOAL_TYPES
        registry.ALLOWLISTED_GOAL_TYPES = saved | {"_smoke"}
        try:
            # Trigger discovery while allowlisted
            registry.list_types()
        finally:
            registry.ALLOWLISTED_GOAL_TYPES = saved

        _remove_smoke_package()
        registry = _reload_registry()

        types = registry.list_types()
        assert "_smoke" not in types, (
            f"Registry should no longer list _smoke after removal; got: {types}"
        )


# ── Trusted-path-gate tests ───────────────────────────────────────────────────


class TestTrustedPathGate:
    """Prove the trusted-path check rejects modules outside the goal_types tree."""

    def test_module_outside_trusted_root_rejected(self):
        """_is_trusted_path returns False for a module outside goal_types/."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            # Create a minimal package outside the trusted root
            pkg_dir = tmpdir_path / "evil_pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.py").write_text("goal_type = None")

            # Add tmpdir to sys.path so the package can be imported
            sys.path.insert(0, tmpdir)
            try:
                mod = importlib.import_module("evil_pkg")
            finally:
                sys.path.remove(tmpdir)

            assert not _is_trusted_path(mod), "Module outside app/goal_types/ must not be trusted"

            # Clean up sys.modules
            del sys.modules["evil_pkg"]

    def test_module_inside_trusted_root_accepted(self):
        """_is_trusted_path returns True for a built-in goal-type module."""
        import app.goal_types.youtube_video as yt_mod

        assert _is_trusted_path(yt_mod), "Built-in goal type inside app/goal_types/ must be trusted"


# ── Policy-applied-at-discovery tests ─────────────────────────────────────────


class TestPolicyAppliedAtDiscovery:
    """Prove both checks run during filesystem discovery, not only at dispatch."""

    def test_allowlist_checked_during_discovery_not_dispatch(self):
        """A non-allowlisted module never reaches get_type — it is filtered
        at discovery time, so list_types() never includes it."""
        _create_smoke_package()
        registry = _reload_registry()

        # _smoke is not allowlisted → must not appear in list_types()
        types = registry.list_types()
        assert "_smoke" not in types

        # get_type must also reject it (dispatch-time guard is still there)
        with pytest.raises(KeyError):
            registry.get_type("_smoke")

    def test_trusted_path_checked_during_discovery(self):
        """A module outside the trusted path is excluded by _discover itself.

        We prove this by patching pkgutil.iter_modules to yield a fake
        module name whose import resolves to a path outside the trusted root,
        then confirming the registry never loads it.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            pkg_dir = tmpdir_path / "outside_pkg"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.py").write_text(
                textwrap.dedent("""\
                from app.goal_types.registry import _DynamicGoalType

                async def _verify(proof_data, criteria_data):
                    return {"verification_status": "verified",
                            "verification_details": {}}

                goal_type = _DynamicGoalType(
                    name="outside_pkg",
                    description="Outside trusted root",
                    sample_prompts=[],
                    criteria_schema={},
                    verify=_verify,
                )
            """)
            )

            # Make the outside package importable
            sys.path.insert(0, tmpdir)
            try:
                # Patch iter_modules to also yield our outside package
                original_iter = __import__("pkgutil").iter_modules

                def _patched_iter_modules(paths):
                    yield from original_iter(paths)
                    for info in original_iter([str(tmpdir_path)]):
                        if info.name == "outside_pkg" and info.ispkg:
                            yield info

                with mock.patch(
                    "app.goal_types.registry.pkgutil.iter_modules",
                    side_effect=_patched_iter_modules,
                ):
                    registry = _reload_registry()
                    # Patch allowlist AFTER reload, before discovery
                    saved = registry.ALLOWLISTED_GOAL_TYPES
                    registry.ALLOWLISTED_GOAL_TYPES = saved | {"outside_pkg"}
                    try:
                        types = registry.list_types()
                    finally:
                        registry.ALLOWLISTED_GOAL_TYPES = saved
            finally:
                sys.path.remove(tmpdir)
                sys.modules.pop("outside_pkg", None)

            assert "outside_pkg" not in types, (
                "Module outside trusted path must not be registered during discovery"
            )
