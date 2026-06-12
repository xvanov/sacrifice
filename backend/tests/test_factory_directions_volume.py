"""
Tests for D010: compose bind-mount for factory directions volume.

These tests assert infrastructure configuration:
- Every docker-compose*.yml at the repo root with a backend service
  declares the directions bind-mount (rw) at the correct paths.
- backend config exposes a configurable factory_directions_path
  defaulting to /var/factory/directions.
"""

import glob
import os

import yaml

from app.config import Settings

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

HOST_DIRECTIONS_PATH = "~/software-factory/apps/sacrifice/directions"
CONTAINER_DIRECTIONS_PATH = "/var/factory/directions"


def _normalise_source(source: str) -> str:
    """Expand ~ and collapse trailing slashes so we can assert exact
    path equality regardless of compose quoting or trailing-slash style."""
    if source.startswith("~"):
        source = os.path.expanduser(source)
    return source.rstrip("/")


def _find_directions_mount(volumes):
    """Return the (source, target, read_only) tuple for the directions
    bind-mount if present in *volumes*, or None."""
    for vol in volumes:
        if isinstance(vol, str):
            parts = vol.split(":")
            if len(parts) >= 2 and parts[1] == CONTAINER_DIRECTIONS_PATH:
                source = parts[0]
                mode = parts[2] if len(parts) >= 3 else "rw"
                return (source, parts[1], mode == "ro")
        elif isinstance(vol, dict):
            if vol.get("target") == CONTAINER_DIRECTIONS_PATH:
                read_only = vol.get("read_only", False)
                source = vol.get("source", "")
                return (source, vol["target"], read_only)
    return None


def _compose_files():
    """Return every docker-compose*.yml file at the repo root."""
    return sorted(glob.glob(os.path.join(REPO_ROOT, "docker-compose*.yml")))


# ---------------------------------------------------------------------------
# docker-compose files — existence and volume tests
# ---------------------------------------------------------------------------

class TestDockerComposeVolume:
    """Every docker-compose*.yml file in the repo that defines a backend
    service must bind-mount the directions volume (rw) at the correct
    host and container paths."""

    @staticmethod
    def _load_compose(path):
        with open(path, "r") as fh:
            return yaml.safe_load(fh)

    def test_all_compose_files_have_directions_bind_mount(self):
        """For each compose file in the repo, if it has a backend service
        then it must declare the directions bind-mount at the expected
        host path, container path, and rw mode.

        This single test replaces the old trivia tests that separately
        asserted backend-service existence and volume-list non-emptiness:
        this test cannot pass vacuously — it must find the mount with
        exact source, target, and rw mode."""
        files = _compose_files()
        assert files, (
            "No docker-compose*.yml files found at repo root"
        )

        for compose_path in files:
            basename = os.path.basename(compose_path)
            compose = self._load_compose(compose_path)
            services = compose.get("services", {})
            assert "backend" in services, (
                f"{basename}: must define a 'backend' service"
            )

            backend = services["backend"]
            volumes = backend.get("volumes", [])
            mount = _find_directions_mount(volumes)
            assert mount is not None, (
                f"{basename}: no bind-mount targeting "
                f"{CONTAINER_DIRECTIONS_PATH} found in backend volumes"
            )

            actual_source = _normalise_source(mount[0])
            expected_source = _normalise_source(HOST_DIRECTIONS_PATH)
            assert actual_source == expected_source, (
                f"{basename}: directions bind-mount source mismatch: "
                f"expected {expected_source!r}, got {actual_source!r}"
            )

            _, _, read_only = mount
            assert not read_only, (
                f"{basename}: directions bind-mount must be rw, "
                f"but it is read_only={read_only}"
            )


# ---------------------------------------------------------------------------
# Config path tests
# ---------------------------------------------------------------------------

class TestFactoryDirectionsPathConfig:
    """backend/app/config.py must expose a configurable directory path for
    factory directions, defaulting to /var/factory/directions/."""

    def test_settings_has_factory_directions_path(self):
        s = Settings()
        assert hasattr(s, "factory_directions_path"), (
            "Settings must declare factory_directions_path"
        )

    def test_factory_directions_path_default(self):
        s = Settings()
        assert s.factory_directions_path == "/var/factory/directions", (
            f"Expected default /var/factory/directions, "
            f"got {s.factory_directions_path}"
        )

    def test_factory_directions_path_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("FACTORY_DIRECTIONS_PATH", "/custom/directions")
        s = Settings()
        assert s.factory_directions_path == "/custom/directions"