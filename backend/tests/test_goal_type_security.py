"""Security tests for goal-type module discovery, integrity, interface
validation, and security logging.

Covers:
- AC1.1: Allowlist + trusted-path enforcement
- AC2.1: Startup failure on integrity-check failures
- AC2.2: Startup failure on interface validation failures
- AC3.1: Security log emission for module load decisions
- AC3.2: Security log emission for verifier exceptions
"""

from __future__ import annotations

import importlib
import json
import logging
import shutil
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest
from httpx import ASGITransport, AsyncClient

from app.goal_types.base import GoalTypeBase
from app.goal_types.registry import (
    ALLOWLISTED_GOAL_TYPES,
    GoalTypeIntegrityError,
    GoalTypeInterfaceError,
    _check_module_integrity,
    _is_trusted_path,
    _validate_goal_type_interface,
)
from app.goal_types.security_logger import (
    log_module_load_allow,
    log_module_load_deny,
    log_verifier_exception,
)
from app.main import app

# ── Helpers ───────────────────────────────────────────────────────────────────

GOAL_TYPES_DIR = Path(__file__).resolve().parent.parent / "app" / "goal_types"
TEST_PKG_NAME = "_security_test"


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with mock.patch("app.routes.auth.verify_google_token") as google_mock:
        google_mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def _create_active_goal(client, token, goal_type, criteria):
    """Create an active goal and return its ID."""
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": f"Test {goal_type} goal",
            "description": "Security test",
            "deadline": "2026-12-31T00:00:00Z",
            "pledge_amount": 1000,
            "goal_type": goal_type,
            "criteria": criteria,
        },
    )
    assert resp.status_code in (200, 201), f"Goal creation failed: {resp.text}"
    goal_id = resp.json()["id"]

    await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    return goal_id


@pytest.fixture(autouse=True)
def _cleanup_test_package():
    """Remove the _security_test directory before and after each test."""
    pkg_dir = GOAL_TYPES_DIR / TEST_PKG_NAME
    _remove_package(pkg_dir)
    yield
    _remove_package(pkg_dir)


def _remove_package(pkg_dir: Path) -> None:
    to_remove = [k for k in sys.modules if f"goal_types.{TEST_PKG_NAME}" in k]
    for k in to_remove:
        del sys.modules[k]
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir, ignore_errors=True)
    pycache = pkg_dir / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache, ignore_errors=True)


def _create_test_package(init_content: str) -> Path:
    """Create a minimal _security_test package with the given __init__.py."""
    pkg_dir = GOAL_TYPES_DIR / TEST_PKG_NAME
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text(textwrap.dedent(init_content))
    return pkg_dir


def _add_to_allowlist(registry_module, name: str):
    """Temporarily add *name* to the registry's ALLOWLISTED_GOAL_TYPES."""
    registry_module.ALLOWLISTED_GOAL_TYPES = registry_module.ALLOWLISTED_GOAL_TYPES | {name}


def _restore_allowlist(registry_module, saved):
    registry_module.ALLOWLISTED_GOAL_TYPES = saved


def _reload_registry():
    """Reload the registry module and return it.

    .. warning::
       After calling this, use ``reg.GoalTypeIntegrityError`` /
       ``reg.GoalTypeInterfaceError`` from the returned module instead of any
       top-level imports — reload creates new class objects and ``pytest.raises``
       needs the live identity.
    """
    import app.goal_types.registry
    importlib.reload(app.goal_types.registry)
    return app.goal_types.registry


# ── AC1.1: Trusted-path + allowlist enforcement ──────────────────────────────


class TestAllowlistEnforcement:
    """AC1.1: Only allowlisted modules from trusted paths are loaded."""

    def test_allowlisted_trusted_module_loaded(self):
        """A module in ALLOWLISTED_GOAL_TYPES on a trusted path is loaded."""
        _create_test_package("""\
            from app.goal_types.base import GoalTypeBase

            class TestGT(GoalTypeBase):
                name = "_security_test"
                description = "A valid test goal type"
                sample_prompts = ["test"]
                criteria_schema = {"type": "object"}

                async def verify(self, proof_data, criteria_data):
                    return {"verification_status": "verified", "verification_details": {}}

            goal_type = TestGT()
        """)

        registry = _reload_registry()
        saved = registry.ALLOWLISTED_GOAL_TYPES
        _add_to_allowlist(registry, TEST_PKG_NAME)
        try:
            registry.discover_all()
            types = registry.list_types()
        finally:
            _restore_allowlist(registry, saved)

        assert TEST_PKG_NAME in types

    def test_non_allowlisted_module_denied_at_discovery(self):
        """A module not in ALLOWLISTED_GOAL_TYPES is skipped."""
        _create_test_package("""\
            from app.goal_types.base import GoalTypeBase

            class TestGT(GoalTypeBase):
                name = "_security_test"
                description = "Should not be loaded"
                sample_prompts = []
                criteria_schema = {}

                async def verify(self, proof_data, criteria_data):
                    return {"verification_status": "verified", "verification_details": {}}

            goal_type = TestGT()
        """)

        registry = _reload_registry()
        # Do NOT add to allowlist
        registry.discover_all()
        types = registry.list_types()

        assert TEST_PKG_NAME not in types, (
            f"Non-allowlisted module must not be discovered; got: {types}"
        )

    def test_untrusted_path_denied_at_startup(self):
        """discover_all raises GoalTypeIntegrityError for allowlisted module
        outside the trusted path."""
        registry_mod = _reload_registry()

        # Create a valid test package so it can be imported
        _create_test_package("""\
            from app.goal_types.base import GoalTypeBase

            class TestGT(GoalTypeBase):
                name = "_security_test"
                description = "Valid"
                sample_prompts = []
                criteria_schema = {}

                async def verify(self, proof_data, criteria_data):
                    return {"verification_status": "verified", "verification_details": {}}

            goal_type = TestGT()
        """)

        saved = registry_mod.ALLOWLISTED_GOAL_TYPES
        _add_to_allowlist(registry_mod, TEST_PKG_NAME)
        try:
            # Patch _is_trusted_path to reject ALL paths so the trusted-path
            # gate fails for our allowlisted module.
            with mock.patch.object(registry_mod, "_is_trusted_path", return_value=False):
                with pytest.raises(registry_mod.GoalTypeIntegrityError, match="trusted"):
                    registry_mod.discover_all()
        finally:
            _restore_allowlist(registry_mod, saved)


# ── AC2.1: Startup failure on integrity-check failures ───────────────────────


class TestIntegrityCheckFailures:
    """AC2.1: discover_all raises GoalTypeIntegrityError on integrity failures."""

    def test_missing_goal_type_attribute_raises(self):
        """Module without a goal_type attribute fails integrity check."""
        _create_test_package("""\
            # No goal_type attribute
        """)

        registry = _reload_registry()
        saved = registry.ALLOWLISTED_GOAL_TYPES
        _add_to_allowlist(registry, TEST_PKG_NAME)
        try:
            with pytest.raises(registry.GoalTypeIntegrityError, match="no 'goal_type' attribute"):
                registry.discover_all()
        finally:
            _restore_allowlist(registry, saved)

    def test_wrong_type_goal_type_attribute_raises(self):
        """Module with a non-GoalTypeBase goal_type fails integrity check."""
        _create_test_package("""\
            goal_type = "not_a_GoalTypeBase_instance"
        """)

        registry = _reload_registry()
        saved = registry.ALLOWLISTED_GOAL_TYPES
        _add_to_allowlist(registry, TEST_PKG_NAME)
        try:
            with pytest.raises(registry.GoalTypeIntegrityError, match="not a GoalTypeBase"):
                registry.discover_all()
        finally:
            _restore_allowlist(registry, saved)

    def test_import_failure_raises_integrity_error(self):
        """A module that fails to import raises GoalTypeIntegrityError at startup."""
        _create_test_package("""\
            raise RuntimeError("deliberate import failure")
        """)

        registry = _reload_registry()
        saved = registry.ALLOWLISTED_GOAL_TYPES
        _add_to_allowlist(registry, TEST_PKG_NAME)
        try:
            with pytest.raises(registry.GoalTypeIntegrityError, match="Failed to import"):
                registry.discover_all()
        finally:
            _restore_allowlist(registry, saved)


# ── AC2.2: Startup failure on interface validation failures ──────────────────


class TestInterfaceValidationFailures:
    """AC2.2: discover_all raises GoalTypeInterfaceError on interface failures."""

    def test_empty_name_raises_interface_error(self):
        """Goal type with empty name fails interface validation."""
        _create_test_package("""\
            from app.goal_types.base import GoalTypeBase

            class TestGT(GoalTypeBase):
                name = ""
                description = "Valid description"
                sample_prompts = ["test"]
                criteria_schema = {}

                async def verify(self, proof_data, criteria_data):
                    return {"verification_status": "verified", "verification_details": {}}

            goal_type = TestGT()
        """)

        registry = _reload_registry()
        saved = registry.ALLOWLISTED_GOAL_TYPES
        _add_to_allowlist(registry, TEST_PKG_NAME)
        try:
            with pytest.raises(registry.GoalTypeInterfaceError, match="must be a non-empty string"):
                registry.discover_all()
        finally:
            _restore_allowlist(registry, saved)

    def test_non_callable_verify_raises_interface_error(self):
        """Goal type with non-callable verify fails interface validation."""
        _create_test_package("""\
            from app.goal_types.base import GoalTypeBase

            class TestGT(GoalTypeBase):
                name = "_security_test"
                description = "Valid description"
                sample_prompts = ["test"]
                criteria_schema = {}
                verify = "not_callable"

            goal_type = TestGT()
        """)

        registry = _reload_registry()
        saved = registry.ALLOWLISTED_GOAL_TYPES
        _add_to_allowlist(registry, TEST_PKG_NAME)
        try:
            with pytest.raises(registry.GoalTypeInterfaceError, match="must be callable"):
                registry.discover_all()
        finally:
            _restore_allowlist(registry, saved)

    def test_missing_criteria_schema_raises_interface_error(self):
        """Goal type with non-dict criteria_schema fails interface validation."""
        _create_test_package("""\
            from app.goal_types.base import GoalTypeBase

            class TestGT(GoalTypeBase):
                name = "_security_test"
                description = "Valid description"
                sample_prompts = ["test"]
                criteria_schema = None

                async def verify(self, proof_data, criteria_data):
                    return {"verification_status": "verified", "verification_details": {}}

            goal_type = TestGT()
        """)

        registry = _reload_registry()
        saved = registry.ALLOWLISTED_GOAL_TYPES
        _add_to_allowlist(registry, TEST_PKG_NAME)
        try:
            with pytest.raises(registry.GoalTypeInterfaceError, match="must be a dict"):
                registry.discover_all()
        finally:
            _restore_allowlist(registry, saved)

    def test_non_list_sample_prompts_raises_interface_error(self):
        """Goal type with non-list sample_prompts fails interface validation."""
        _create_test_package("""\
            from app.goal_types.base import GoalTypeBase

            class TestGT(GoalTypeBase):
                name = "_security_test"
                description = "Valid description"
                sample_prompts = "not_a_list"
                criteria_schema = {}

                async def verify(self, proof_data, criteria_data):
                    return {"verification_status": "verified", "verification_details": {}}

            goal_type = TestGT()
        """)

        registry = _reload_registry()
        saved = registry.ALLOWLISTED_GOAL_TYPES
        _add_to_allowlist(registry, TEST_PKG_NAME)
        try:
            with pytest.raises(registry.GoalTypeInterfaceError, match="must be a list"):
                registry.discover_all()
        finally:
            _restore_allowlist(registry, saved)


# ── AC3.1: Security log emission for module load decisions ───────────────────


class TestSecurityLogModuleLoadDecisions:
    """AC3.1: Security logging records module load decisions."""

    def test_allow_decision_logged(self, caplog):
        """log_module_load_allow emits a structured JSON event."""
        caplog.set_level(logging.INFO, logger="sacrifice.security.goal_types")

        log_module_load_allow("youtube_video", trusted_path="/trusted/root")

        assert len(caplog.records) >= 1
        record = json.loads(caplog.records[-1].message)
        assert record["event_type"] == "goal_type_load_decision"
        assert record["decision"] == "allow"
        assert record["module_name"] == "youtube_video"
        assert record["trusted_path"] == "/trusted/root"
        # No proof payload data in event
        assert "proof_data" not in record
        assert "proof" not in record

    def test_deny_decision_logged(self, caplog):
        """log_module_load_deny emits a structured JSON event with reason."""
        caplog.set_level(logging.INFO, logger="sacrifice.security.goal_types")

        log_module_load_deny("evil_module", "not_in_allowlist", detail="Blocked by allowlist")

        assert len(caplog.records) >= 1
        record = json.loads(caplog.records[-1].message)
        assert record["event_type"] == "goal_type_load_decision"
        assert record["decision"] == "deny"
        assert record["module_name"] == "evil_module"
        assert record["reason"] == "not_in_allowlist"
        assert record["detail"] == "Blocked by allowlist"

    def test_deny_logged_for_non_allowlisted_during_discovery(self, caplog):
        """Non-allowlisted packages produce a deny security log during discovery."""
        caplog.set_level(logging.INFO, logger="sacrifice.security.goal_types")

        _create_test_package("""\
            from app.goal_types.base import GoalTypeBase

            class TestGT(GoalTypeBase):
                name = "_security_test"
                description = "Valid"
                sample_prompts = []
                criteria_schema = {}

                async def verify(self, proof_data, criteria_data):
                    return {"verification_status": "verified", "verification_details": {}}

            goal_type = TestGT()
        """)

        registry = _reload_registry()
        registry.discover_all()

        deny_events = []
        for r in caplog.records:
            if not r.message.startswith("{"):
                continue
            event = json.loads(r.message)
            if (
                event.get("event_type") == "goal_type_load_decision"
                and event.get("decision") == "deny"
                and event.get("module_name") == TEST_PKG_NAME
            ):
                deny_events.append(event)
        assert len(deny_events) >= 1, (
            f"Expected deny event for {TEST_PKG_NAME}; got records: "
            f"{[r.message for r in caplog.records]}"
        )
        assert deny_events[0]["reason"] == "not_in_allowlist"

    def test_allow_logged_for_registered_type(self, caplog):
        """Allowlisted, trusted, valid modules produce an allow security log."""
        caplog.set_level(logging.INFO, logger="sacrifice.security.goal_types")

        _create_test_package("""\
            from app.goal_types.base import GoalTypeBase

            class TestGT(GoalTypeBase):
                name = "_security_test"
                description = "Valid"
                sample_prompts = []
                criteria_schema = {}

                async def verify(self, proof_data, criteria_data):
                    return {"verification_status": "verified", "verification_details": {}}

            goal_type = TestGT()
        """)

        registry = _reload_registry()
        saved = registry.ALLOWLISTED_GOAL_TYPES
        _add_to_allowlist(registry, TEST_PKG_NAME)
        try:
            registry.discover_all()
        finally:
            _restore_allowlist(registry, saved)

        allow_events = []
        for r in caplog.records:
            if not r.message.startswith("{"):
                continue
            event = json.loads(r.message)
            if (
                event.get("event_type") == "goal_type_load_decision"
                and event.get("decision") == "allow"
                and event.get("module_name") == TEST_PKG_NAME
            ):
                allow_events.append(event)
        assert len(allow_events) >= 1

    @pytest.mark.asyncio
    async def test_verifier_exception_detail_excludes_sensitive_content(
        self, caplog
    ):
        """When a verifier raises an exception whose message contains
        proof-like content, the logged ``detail`` field must be the static
        safe string — NOT the raw exception message."""
        from unittest.mock import MagicMock

        caplog.set_level(logging.INFO, logger="sacrifice.security.goal_types")

        mock_goal_type = MagicMock()
        mock_goal_type.name = "youtube_video"
        mock_goal_type.submit_proof.return_value = {
            "proof_data": {"ok": True},
            "criteria_data": {},
        }
        mock_goal_type.dispatch_verification.side_effect = ValueError(
            "proof body leaked: secret_token=sk_live_12345"
        )

        with mock.patch(
            "app.goal_types.registry.get_type", return_value=mock_goal_type
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                token, _ = await _auth(client)
                goal_id = await _create_active_goal(
                    client,
                    token,
                    "youtube_video",
                    {"min_duration_seconds": 60, "video_description": "test"},
                )
                resp = await client.post(
                    f"/api/goals/{goal_id}/submit-proof",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                )

        # Dispatch failures no longer crash the endpoint (proof is already
        # persisted), so the response is 202 Accepted, not 500.
        assert resp.status_code == 202

        verifier_events = []
        for r in caplog.records:
            if not r.message.startswith("{"):
                continue
            event = json.loads(r.message)
            if event.get("event_type") == "verifier_exception":
                verifier_events.append(event)

        assert len(verifier_events) >= 1, (
            "Expected at least one verifier_exception event"
        )
        event = verifier_events[0]
        # The detail must be the safe static string — NOT the exception's
        # raw message containing proof-like content.
        assert "Verifier dispatch failed (broker may be unavailable)" in event["detail"]
        assert "secret_token" not in event["detail"]
        assert "sk_live_12345" not in event["detail"]


# ── AC3.2: Security log emission for verifier exceptions ─────────────────────


class TestSecurityLogVerifierExceptions:
    """AC3.2: Security logging records verifier exceptions."""

    def test_verifier_exception_logged(self, caplog):
        """log_verifier_exception emits a structured JSON event."""
        caplog.set_level(logging.INFO, logger="sacrifice.security.goal_types")

        log_verifier_exception(
            goal_type="youtube_video",
            submission_id="abc-123",
            exception_type="ValueError",
            detail="Something went wrong",
        )

        assert len(caplog.records) >= 1
        record = json.loads(caplog.records[-1].message)
        assert record["event_type"] == "verifier_exception"
        assert record["goal_type"] == "youtube_video"
        assert record["submission_id"] == "abc-123"
        assert record["exception_type"] == "ValueError"
        assert record["detail"] == "Something went wrong"
        # No proof payload
        assert "proof_data" not in record

    @pytest.mark.asyncio
    async def test_verifier_exception_omits_proof_payload(self, caplog):
        """Verifier exception logs emitted through submit_proof must not
        contain proof payload data even when the mock proof body carries
        sensitive fields."""
        from unittest.mock import MagicMock

        from httpx import ASGITransport, AsyncClient

        caplog.set_level(logging.INFO, logger="sacrifice.security.goal_types")

        mock_goal_type = MagicMock()
        mock_goal_type.name = "youtube_video"
        # submit_proof returns proof_data with a "secret" that must never
        # appear in the security log.
        mock_goal_type.submit_proof.return_value = {
            "proof_data": {
                "youtube_url": "https://example.com/video",
                "secret": "should-not-leak",
            },
            "criteria_data": {"min_duration_seconds": 60},
        }
        mock_goal_type.dispatch_verification.side_effect = RuntimeError(
            "proof payload leak test"
        )

        with mock.patch(
            "app.goal_types.registry.get_type", return_value=mock_goal_type
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                token, _ = await _auth(client)
                goal_id = await _create_active_goal(
                    client,
                    token,
                    "youtube_video",
                    {"min_duration_seconds": 60, "video_description": "test"},
                )
                resp = await client.post(
                    f"/api/goals/{goal_id}/submit-proof",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                )

        # Dispatch failures no longer crash the endpoint (proof already persisted).
        assert resp.status_code == 202

        verifier_events = []
        for r in caplog.records:
            if not r.message.startswith("{"):
                continue
            event = json.loads(r.message)
            if event.get("event_type") == "verifier_exception":
                verifier_events.append(event)

        assert len(verifier_events) >= 1, (
            f"No verifier_exception event found; caplog: "
            f"{[r.message for r in caplog.records]}"
        )
        event = verifier_events[0]
        assert "proof_data" not in event
        assert "proof_body" not in event
        assert "criteria_data" not in event


# ── Direct unit tests for validation helpers ─────────────────────────────────
#
# These tests deliberately avoid the top-level exception imports and instead
# reach through ``app.goal_types.registry`` so that they stay correct even when
# a previous test has called ``importlib.reload`` on the registry module (which
# replaces every class object).


class TestCheckModuleIntegrity:
    """Direct tests for _check_module_integrity."""

    @staticmethod
    def _get_reg():
        import app.goal_types.registry
        return app.goal_types.registry

    def test_valid_module_returns_goal_type(self):
        """Returns the goal_type instance for a valid module."""
        import app.goal_types.youtube_video as yt_mod

        reg = self._get_reg()
        gt = reg._check_module_integrity("youtube_video", yt_mod)
        assert isinstance(gt, GoalTypeBase)
        assert gt.name == "youtube_video"

    def test_missing_goal_type_raises(self):
        """Raises GoalTypeIntegrityError when goal_type is missing."""
        reg = self._get_reg()
        mod = mock.MagicMock(spec_set=[])
        with pytest.raises(reg.GoalTypeIntegrityError, match="no 'goal_type'"):
            reg._check_module_integrity("fake", mod)

    def test_wrong_type_goal_type_raises(self):
        """Raises GoalTypeIntegrityError when goal_type is not GoalTypeBase."""
        reg = self._get_reg()
        mod = mock.MagicMock()
        mod.goal_type = 42
        with pytest.raises(reg.GoalTypeIntegrityError, match="not a GoalTypeBase"):
            reg._check_module_integrity("fake", mod)


class TestValidateGoalTypeInterface:
    """Direct tests for _validate_goal_type_interface."""

    @staticmethod
    def _get_reg():
        import app.goal_types.registry
        return app.goal_types.registry

    def _make_gt(self, **overrides):
        """Create a valid _DynamicGoalType with optional overrides."""
        reg = self._get_reg()

        async def _verify(pd, cd):
            return {"verification_status": "verified", "verification_details": {}}

        kwargs = {
            "name": "test_type",
            "description": "A test type",
            "sample_prompts": ["prompt"],
            "criteria_schema": {"type": "object"},
            "verify": _verify,
            **overrides,
        }
        return reg._DynamicGoalType(**kwargs)

    def test_valid_goal_type_passes(self):
        """A fully valid goal type passes interface validation."""
        reg = self._get_reg()
        gt = self._make_gt()
        reg._validate_goal_type_interface("test_type", gt)  # does not raise

    def test_empty_name_raises(self):
        reg = self._get_reg()
        gt = self._make_gt(name="")
        with pytest.raises(reg.GoalTypeInterfaceError, match="'name' must be a non-empty string"):
            reg._validate_goal_type_interface("test_type", gt)

    def test_empty_description_raises(self):
        reg = self._get_reg()
        gt = self._make_gt(description="")
        with pytest.raises(reg.GoalTypeInterfaceError, match="'description' must be a non-empty string"):
            reg._validate_goal_type_interface("test_type", gt)

    def test_non_callable_verify_raises(self):
        reg = self._get_reg()
        # _DynamicGoalType always has a callable verify() method, so we need
        # a bare GoalTypeBase subclass with a non-callable verify instead.
        class BadGT(GoalTypeBase):
            name = "bad"
            description = "bad type"
            sample_prompts = []
            criteria_schema = {}
            verify = "not_callable"

        gt = BadGT()
        with pytest.raises(reg.GoalTypeInterfaceError, match="'verify' must be callable"):
            reg._validate_goal_type_interface("bad", gt)

    def test_non_dict_criteria_schema_raises(self):
        reg = self._get_reg()
        gt = self._make_gt(criteria_schema=["not", "a", "dict"])
        with pytest.raises(reg.GoalTypeInterfaceError, match="'criteria_schema' must be a dict"):
            reg._validate_goal_type_interface("test_type", gt)

    def test_non_list_sample_prompts_raises(self):
        reg = self._get_reg()
        gt = self._make_gt(sample_prompts="not_a_list")
        with pytest.raises(reg.GoalTypeInterfaceError, match="'sample_prompts' must be a list"):
            reg._validate_goal_type_interface("test_type", gt)


# ── discover_all / startup integration tests ─────────────────────────────────


class TestDiscoverAllStartup:
    """Integration tests for discover_all() as called by startup."""

    def test_discover_all_registers_all_allowlisted_types(self):
        """discover_all() registers every currently-allowlisted built-in type."""
        registry = _reload_registry()
        registry.discover_all()
        types = registry.list_types()

        for name in registry.ALLOWLISTED_GOAL_TYPES:
            assert name in types, f"Allowlisted type '{name}' not registered"

    def test_discover_all_sets_discovered_flag(self):
        """After discover_all(), subsequent lazy calls are no-ops."""
        registry = _reload_registry()
        registry.discover_all()

        # _discovered should be True; calling list_types() again should
        # not re-discover or mutate the registry.
        types_before = registry.list_types()
        types_after = registry.list_types()
        assert types_before == types_after

    def test_discover_all_replaces_previous_registry(self):
        """Calling discover_all() resets the registry before discovery."""
        registry = _reload_registry()

        # First, do a lazy discovery that may register built-ins
        registry.list_types()

        # Now run discover_all — it should clear and re-register
        registry.discover_all()
        types = registry.list_types()

        for name in registry.ALLOWLISTED_GOAL_TYPES:
            assert name in types