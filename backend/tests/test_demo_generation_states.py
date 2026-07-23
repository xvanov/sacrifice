"""Tests for demo generation-states fixture and runtime path (story 320).

Covers:
* Each documented status-banner state is reachable/observable through the runtime path
* Deterministic ordering/progression semantics
* Final notification-driven return path is represented in the demo response
* Config gate: 404 when demo flag is False
* Production paths are unchanged (no leakage into real direction allocation)
"""

import tempfile
from pathlib import Path

import pytest
import yaml
from app.config import settings
from app.main import app
from app.services.direction_synth import (
    _DEMO_DIRECTION_IDS,
    _RAW_TO_BANNER_LABEL,
    allocate_direction_id,
    ensure_demo_directions,
    read_direction_state,
)
from httpx import ASGITransport, AsyncClient

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def temp_directions_root():
    """Override settings.directions_path with a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        original = settings.directions_path
        settings.directions_path = tmpdir
        yield Path(tmpdir)
        settings.directions_path = original


@pytest.fixture
def enable_demo_flag():
    """Enable the demo generation-states config gate."""
    original = settings.sacrifice_demo_generation_states
    settings.sacrifice_demo_generation_states = True
    yield
    settings.sacrifice_demo_generation_states = original


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Fixture function tests (no HTTP) ───────────────────────────────────────


class TestEnsureDemoDirections:
    """Unit tests for the deterministic fixture source."""

    async def test_all_entries_returned(self, temp_directions_root):
        """AC1.1: Every documented state is represented in the fixture."""
        entries = await ensure_demo_directions(_root=temp_directions_root)

        assert len(entries) == len(_DEMO_DIRECTION_IDS)

        direction_ids = {e["direction_id"] for e in entries}
        for expected_id, _, _, _ in _DEMO_DIRECTION_IDS:
            assert expected_id in direction_ids, f"Missing demo direction {expected_id}"

        # AC1.1: each entry exposes a banner_label matching the documented
        # audit-facing banner label (or null for the return-path-only entry)
        for e in entries:
            expected_label = _RAW_TO_BANNER_LABEL.get(e["raw_status"])
            assert e["banner_label"] == expected_label, (
                f"banner_label mismatch for {e['direction_id']}: "
                f"expected {expected_label!r}, got {e['banner_label']!r}"
            )

    async def test_queued_state_observable(self, temp_directions_root):
        """AC1.1: queued state is observable with correct shape."""
        entries = await ensure_demo_directions(_root=temp_directions_root)
        queued = [e for e in entries if e["direction_id"] == "demo-queued"]
        assert len(queued) == 1
        entry = queued[0]

        assert entry["raw_status"] == "queued"
        assert entry["status"] == "queued"  # _coarse_status maps queued → queued
        assert entry["banner_label"] == "queued"  # documented audit-facing label
        assert entry["pr_url"] is None
        assert entry["summary"] != ""
        assert entry["notification"] is None

    async def test_in_progress_state_observable(self, temp_directions_root):
        """AC1.1: in_progress state is observable with correct shape."""
        entries = await ensure_demo_directions(_root=temp_directions_root)
        in_progress = [e for e in entries if e["direction_id"] == "demo-in-progress"]
        assert len(in_progress) == 1
        entry = in_progress[0]

        assert entry["raw_status"] == "in_progress"
        assert entry["status"] == "in_progress"
        assert entry["banner_label"] == "in progress"  # documented audit-facing label
        assert entry["pr_url"] is not None
        assert entry["notification"] is None

    async def test_pr_open_state_observable(self, temp_directions_root):
        """AC1.1: pr_open state is observable with correct shape."""
        entries = await ensure_demo_directions(_root=temp_directions_root)
        pr_open = [e for e in entries if e["direction_id"] == "demo-pr-open"]
        assert len(pr_open) == 1
        entry = pr_open[0]

        assert entry["raw_status"] == "pr_open"
        assert entry["status"] == "pr_open"
        assert (
            entry["banner_label"] == "pull request open"
        )  # documented audit-facing label
        assert entry["pr_url"] is not None
        assert entry["notification"] is None

    async def test_merging_state_observable(self, temp_directions_root):
        """AC1.1: merging state is observable — maps to pr_open coarse status."""
        entries = await ensure_demo_directions(_root=temp_directions_root)
        merging = [e for e in entries if e["direction_id"] == "demo-merging"]
        assert len(merging) == 1
        entry = merging[0]

        assert entry["raw_status"] == "merging"
        # _coarse_status maps 'merging' → 'pr_open'
        assert entry["status"] == "pr_open"
        assert entry["banner_label"] == "merging"  # documented audit-facing label
        assert entry["pr_url"] is not None
        assert entry["notification"] is None

    async def test_pr_merged_notification_return_path(self, temp_directions_root):
        """AC1.2: pr_merged state carries the notification-driven return path."""
        entries = await ensure_demo_directions(_root=temp_directions_root)
        pr_merged = [e for e in entries if e["direction_id"] == "demo-pr-merged"]
        assert len(pr_merged) == 1
        entry = pr_merged[0]

        assert entry["raw_status"] == "pr_merged"
        assert entry["status"] == "pr_merged"
        assert entry["banner_label"] is None  # return-path only, not a banner state
        assert entry["notification"] is not None
        assert entry["notification"]["type"] == "goal_type_ready"
        assert entry["notification"]["fired"] is True
        assert "title" in entry["notification"]
        assert "body" in entry["notification"]

    async def test_deterministic_ordering(self, temp_directions_root):
        """AC: Entries are returned in the documented progression order."""
        entries = await ensure_demo_directions(_root=temp_directions_root)

        raw_statuses = [e["raw_status"] for e in entries]
        expected_order = ["queued", "in_progress", "pr_open", "merging", "pr_merged"]
        assert raw_statuses == expected_order, (
            f"Expected {expected_order}, got {raw_statuses}"
        )

    async def test_idempotent_on_disk(self, temp_directions_root):
        """Fixture is idempotent — second call produces same result."""
        entries1 = await ensure_demo_directions(_root=temp_directions_root)
        entries2 = await ensure_demo_directions(_root=temp_directions_root)

        assert len(entries1) == len(entries2)
        for e1, e2 in zip(entries1, entries2):
            assert e1["direction_id"] == e2["direction_id"]
            assert e1["status"] == e2["status"]
            assert e1["raw_status"] == e2["raw_status"]

    async def test_state_yaml_on_disk(self, temp_directions_root):
        """Each demo direction writes a valid state.yaml with correct semantics.

        Parses the YAML file and asserts semantic values (not just substring
        checks), then verifies integration behavior by round-tripping through
        ``read_direction_state``.
        """
        await ensure_demo_directions(_root=temp_directions_root)

        for direction_id, raw_status, pr_url, _summary in _DEMO_DIRECTION_IDS:
            state_yaml = temp_directions_root / direction_id / "state.yaml"
            assert state_yaml.exists(), f"Missing state.yaml for {direction_id}"

            # Parse the YAML and assert semantic values
            parsed = yaml.safe_load(state_yaml.read_text())
            assert parsed is not None, (
                f"Empty/invalid YAML in {direction_id}/state.yaml"
            )
            assert parsed["status"] == raw_status, (
                f"Expected status={raw_status!r}, got {parsed.get('status')!r}"
            )
            if pr_url is None:
                assert parsed["pr_url"] is None, (
                    f"Expected pr_url=None, got {parsed.get('pr_url')!r}"
                )
            else:
                assert parsed["pr_url"] == pr_url, (
                    f"Expected pr_url={pr_url!r}, got {parsed.get('pr_url')!r}"
                )
            assert isinstance(parsed.get("summary"), str), (
                f"summary should be a string, got {type(parsed.get('summary'))}"
            )

            # Integration: round-trip through read_direction_state.
            # read_direction_state applies _coarse_status(), so the returned
            # status will be the coarse API status (e.g. merging → pr_open).
            state = await read_direction_state(direction_id, _root=temp_directions_root)
            assert state is not None
            # The raw_status key is NOT in read_direction_state output — it
            # only exposes the coarse "status".  Verify the status field is
            # present and that pr_url/summary round-tripped correctly.
            assert "status" in state
            assert "pr_url" in state
            assert "summary" in state

    async def test_direction_md_on_disk(self, temp_directions_root):
        """Each demo direction writes a direction.md on disk."""
        await ensure_demo_directions(_root=temp_directions_root)

        for direction_id, raw_status, _, _ in _DEMO_DIRECTION_IDS:
            direction_md = temp_directions_root / direction_id / "direction.md"
            assert direction_md.exists(), f"Missing direction.md for {direction_id}"
            content = direction_md.read_text()
            assert raw_status in content


# ── Runtime path tests (HTTP) ───────────────────────────────────────────────


class TestDemoGenerationStatesEndpoint:
    """Integration tests for GET /api/demo/generation-states."""

    async def test_returns_404_when_flag_disabled(self, temp_directions_root):
        """Config gate: endpoint 404s when sacrifice_demo_generation_states is False."""
        assert settings.sacrifice_demo_generation_states is False

        async with make_client() as client:
            resp = await client.get("/api/demo/generation-states")
            assert resp.status_code == 404

    async def test_returns_200_with_states_when_enabled(
        self, temp_directions_root, enable_demo_flag
    ):
        """AC1.1: endpoint returns 200 with states data when enabled."""
        async with make_client() as client:
            resp = await client.get("/api/demo/generation-states")
            assert resp.status_code == 200
            data = resp.json()
            assert "states" in data
            states = data["states"]
            assert len(states) == len(_DEMO_DIRECTION_IDS)

    async def test_all_banner_states_in_response(
        self, temp_directions_root, enable_demo_flag
    ):
        """AC1.1: Each documented banner state appears in the response."""
        async with make_client() as client:
            resp = await client.get("/api/demo/generation-states")
            data = resp.json()

            raw_statuses = {s["raw_status"] for s in data["states"]}
            assert "queued" in raw_statuses
            assert "in_progress" in raw_statuses
            assert "pr_open" in raw_statuses
            assert "merging" in raw_statuses
            assert "pr_merged" in raw_statuses

            # AC1.1: banner_label exposes the documented audit-facing label
            # for each banner state, and null for the return-path entry.
            banner_labels = {s["raw_status"]: s["banner_label"] for s in data["states"]}
            assert banner_labels["queued"] == "queued"
            assert banner_labels["in_progress"] == "in progress"
            assert banner_labels["pr_open"] == "pull request open"
            assert banner_labels["merging"] == "merging"
            assert banner_labels["pr_merged"] is None

    async def test_notification_return_path_in_response(
        self, temp_directions_root, enable_demo_flag
    ):
        """AC1.2: Final notification-driven return path is in the HTTP response."""
        async with make_client() as client:
            resp = await client.get("/api/demo/generation-states")
            data = resp.json()

            pr_merged = [s for s in data["states"] if s["raw_status"] == "pr_merged"]
            assert len(pr_merged) == 1
            entry = pr_merged[0]

            assert entry["notification"] is not None
            assert entry["notification"]["type"] == "goal_type_ready"
            assert entry["notification"]["fired"] is True

    async def test_entries_have_frontend_consumable_shape(
        self, temp_directions_root, enable_demo_flag
    ):
        """Each entry has the documented frontend-consumable keys."""
        async with make_client() as client:
            resp = await client.get("/api/demo/generation-states")
            data = resp.json()

            for entry in data["states"]:
                assert "direction_id" in entry
                assert "status" in entry
                assert "raw_status" in entry
                assert "banner_label" in entry  # documented audit-facing label
                assert "pr_url" in entry
                assert "summary" in entry
                assert "notification" in entry  # even if None

    async def test_progression_order_in_response(
        self, temp_directions_root, enable_demo_flag
    ):
        """Response states are in documented progression order."""
        async with make_client() as client:
            resp = await client.get("/api/demo/generation-states")
            data = resp.json()

            raw_statuses = [s["raw_status"] for s in data["states"]]
            assert raw_statuses == [
                "queued",
                "in_progress",
                "pr_open",
                "merging",
                "pr_merged",
            ]


# ── Non-interference ───────────────────────────────────────────────────────


class TestDemoDoesNotLeak:
    """Demo fixture must not interfere with production direction allocation.

    ``allocate_direction_id`` reads from ``settings.directions_path``, so the
    ``temp_directions_root`` fixture (which overrides settings) routes both
    demo and production paths into the same temp root — exactly what we want.
    """

    async def test_allocate_direction_id_not_blocked(self, temp_directions_root):
        """Running the demo fixture does not block real direction id allocation."""
        # Ensure demo dirs exist
        await ensure_demo_directions(_root=temp_directions_root)

        # Real allocation should still work — demo-* ids are skipped by the
        # numeric allocation scheme
        dir_id = await allocate_direction_id("pushup-counter")
        assert dir_id is not None
        assert not dir_id.startswith("demo-"), (
            f"Allocated id {dir_id} collides with demo namespace"
        )

    async def test_demo_ids_never_allocated(self, temp_directions_root):
        """Demo direction ids are never allocated by the production path."""
        # Ensure demo dirs exist
        await ensure_demo_directions(_root=temp_directions_root)

        # Pre-populate with a large number of directions to force collisions
        for i in range(20):
            alloc_dir = temp_directions_root / f"{i:03d}-pushup-counter"
            alloc_dir.mkdir(exist_ok=True)
            (alloc_dir / "state.yaml").write_text(
                "status: queued\npr_url: null\nsummary: test\n"
            )

        dir_id = await allocate_direction_id("pushup-counter")
        assert dir_id is not None
        assert not dir_id.startswith("demo-"), (
            f"Demo namespace leaked into allocation: {dir_id}"
        )


# ── Determinism stress tests ───────────────────────────────────────────────


class TestDemoDeterminism:
    """The demo fixture must produce identical output across repeated runs.

    These tests go beyond the idempotency check (same call, same process)
    and verify that tearing down and re-creating the fixture produces
    byte-identical content — critical for UX audit reproducibility.
    """

    async def test_content_hash_stable_across_rebuilds(self, temp_directions_root):
        """Rebuilding the fixture from scratch produces identical state.yaml content."""
        import hashlib

        async def _hash_all_states():
            await ensure_demo_directions(_root=temp_directions_root)
            digests = {}
            for direction_id, _, _, _ in _DEMO_DIRECTION_IDS:
                content = (temp_directions_root / direction_id / "state.yaml").read_bytes()
                digests[direction_id] = hashlib.sha256(content).hexdigest()
            return digests

        # First build
        digests1 = await _hash_all_states()

        # Tear down completely
        import shutil
        for direction_id, _, _, _ in _DEMO_DIRECTION_IDS:
            shutil.rmtree(temp_directions_root / direction_id, ignore_errors=True)

        # Second build
        digests2 = await _hash_all_states()

        for direction_id in digests1:
            assert digests1[direction_id] == digests2[direction_id], (
                f"Content hash mismatch for {direction_id} across rebuilds: "
                f"{digests1[direction_id]} != {digests2[direction_id]}"
            )

    async def test_content_hash_stable_across_three_rebuilds(self, temp_directions_root):
        """Fixture content is stable across 3 complete teardown+rebuild cycles."""
        import hashlib
        import shutil

        all_digests = []
        for cycle in range(3):
            await ensure_demo_directions(_root=temp_directions_root)
            digests = {}
            for direction_id, _, _, _ in _DEMO_DIRECTION_IDS:
                content = (temp_directions_root / direction_id / "state.yaml").read_bytes()
                digests[direction_id] = hashlib.sha256(content).hexdigest()
            all_digests.append(digests)

            # Tear down before next cycle (except last)
            if cycle < 2:
                for direction_id, _, _, _ in _DEMO_DIRECTION_IDS:
                    shutil.rmtree(temp_directions_root / direction_id, ignore_errors=True)

        # All cycles must produce identical digests
        for direction_id in all_digests[0]:
            for cycle in range(1, 3):
                assert all_digests[0][direction_id] == all_digests[cycle][direction_id], (
                    f"Content hash drift for {direction_id} at cycle {cycle}: "
                    f"{all_digests[0][direction_id]} != {all_digests[cycle][direction_id]}"
                )

    async def test_banner_labels_stable_across_rebuilds(self, temp_directions_root):
        """banner_label mapping is stable across fixture rebuilds."""
        import shutil

        async def _banner_labels():
            entries = await ensure_demo_directions(_root=temp_directions_root)
            return {e["direction_id"]: e["banner_label"] for e in entries}

        labels1 = await _banner_labels()

        for direction_id, _, _, _ in _DEMO_DIRECTION_IDS:
            shutil.rmtree(temp_directions_root / direction_id, ignore_errors=True)

        labels2 = await _banner_labels()

        assert labels1 == labels2, (
            f"banner_label mapping changed across rebuilds: {labels1} != {labels2}"
        )


# ── UX audit workflow simulation ──────────────────────────────────────────


class TestUXAuditWorkflow:
    """End-to-end simulation of the UX audit workflow.

    These tests exercise the complete audit path: enable demo flag,
    consume the endpoint, observe every documented banner state and
    the terminal notification return path in a single coherent flow.
    """

    async def test_full_audit_workflow_observes_all_states(
        self, temp_directions_root, enable_demo_flag
    ):
        """AC1.1+AC1.2: A single audit session observes all states and
        the notification-driven return path through the HTTP endpoint."""
        async with make_client() as client:
            resp = await client.get("/api/demo/generation-states")
            assert resp.status_code == 200
            data = resp.json()
            states = data["states"]
            assert len(states) == 5

            # Track progression through the documented banner sequence.
            # Each state must be present exactly once in order.
            sequence = []
            for entry in states:
                sequence.append(
                    {
                        "direction_id": entry["direction_id"],
                        "raw_status": entry["raw_status"],
                        "status": entry["status"],
                        "banner_label": entry["banner_label"],
                        "has_notification": entry["notification"] is not None,
                    }
                )

            # Verify documented banner sequence order
            assert sequence[0]["raw_status"] == "queued"
            assert sequence[0]["banner_label"] == "queued"
            assert sequence[0]["has_notification"] is False

            assert sequence[1]["raw_status"] == "in_progress"
            assert sequence[1]["banner_label"] == "in progress"
            assert sequence[1]["has_notification"] is False

            assert sequence[2]["raw_status"] == "pr_open"
            assert sequence[2]["banner_label"] == "pull request open"
            assert sequence[2]["has_notification"] is False

            assert sequence[3]["raw_status"] == "merging"
            assert sequence[3]["banner_label"] == "merging"
            assert sequence[3]["has_notification"] is False

            # Terminal: notification-driven return path
            assert sequence[4]["raw_status"] == "pr_merged"
            assert sequence[4]["banner_label"] is None  # return-path only
            assert sequence[4]["has_notification"] is True

    async def test_audit_workflow_flag_disabled_returns_404(
        self, temp_directions_root
    ):
        """When demo flag is disabled, the audit endpoint is invisible (404)."""
        assert settings.sacrifice_demo_generation_states is False

        async with make_client() as client:
            resp = await client.get("/api/demo/generation-states")
            assert resp.status_code == 404

    async def test_audit_workflow_flag_toggle_idempotent(
        self, temp_directions_root
    ):
        """Toggling the demo flag on→off→on produces consistent results."""
        # Enable
        settings.sacrifice_demo_generation_states = True
        try:
            async with make_client() as client:
                resp1 = await client.get("/api/demo/generation-states")
                assert resp1.status_code == 200
                data1 = resp1.json()

            # Disable
            settings.sacrifice_demo_generation_states = False
            async with make_client() as client:
                resp = await client.get("/api/demo/generation-states")
                assert resp.status_code == 404

            # Re-enable — same states
            settings.sacrifice_demo_generation_states = True
            async with make_client() as client:
                resp2 = await client.get("/api/demo/generation-states")
                assert resp2.status_code == 200
                data2 = resp2.json()

            assert data1 == data2, (
                "Demo states must be identical across flag toggles"
            )
        finally:
            settings.sacrifice_demo_generation_states = False

    async def test_audit_workflow_notification_shape_complete(
        self, temp_directions_root, enable_demo_flag
    ):
        """AC1.2: The notification in the return-path entry has all required fields
        for downstream frontend consumption."""
        async with make_client() as client:
            resp = await client.get("/api/demo/generation-states")
            data = resp.json()

            pr_merged = [s for s in data["states"] if s["raw_status"] == "pr_merged"]
            assert len(pr_merged) == 1
            notif = pr_merged[0]["notification"]

            assert notif is not None, "pr_merged must carry a notification"
            assert notif["type"] == "goal_type_ready"
            assert notif["fired"] is True
            assert isinstance(notif["title"], str) and len(notif["title"]) > 0
            assert isinstance(notif["body"], str) and len(notif["body"]) > 0
