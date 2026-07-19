"""
Tests for D010: compose bind-mount for factory directions volume.

Covers:
- docker-compose.yml bind-mount contracts (source, target, rw)
- Config default and env-override for factory_directions_path
- Smoke: write_direction() creates real files visible at the configured path
"""

import glob
import json
import os

import yaml
from app.config import Settings
from app.services.directions import write_direction

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

HOST_DIRECTIONS_PATH = (
    "${HOST_FACTORY_DIRECTIONS_PATH:-${HOME}/software-factory/apps/sacrifice/directions}"
)
CONTAINER_DIRECTIONS_PATH = "/var/factory/directions"


def _normalise_source(source: str) -> str:
    """Resolve env-style defaults, expand ~/$HOME, collapse trailing slashes.

    Compose does NOT reliably expand a literal ``~`` in bind sources, so the
    compose file uses ``${HOST_FACTORY_DIRECTIONS_PATH:-${HOME}/...}``; this
    normaliser resolves that expression the same way Compose would with the
    variable unset.
    """
    # Resolve ${VAR:-default} by taking the default (VAR unset in tests).
    while "${" in source:
        start = source.index("${")
        depth, i = 0, start
        for i in range(start, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    break
        expr = source[start + 2 : i]
        if ":-" in expr:
            _, default = expr.split(":-", 1)
            resolved = default
        elif expr == "HOME":
            resolved = os.path.expanduser("~")
        else:
            resolved = ""
        source = source[:start] + resolved + source[i + 1 :]
    if source.startswith("~"):
        source = os.path.expanduser(source)
    return source.rstrip("/")


def _find_directions_mount(volumes):
    """Return (source, target, read_only) for a directions bind-mount or None."""
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
# docker-compose volume contract
# ---------------------------------------------------------------------------


class TestDockerComposeVolume:
    """Every docker-compose*.yml in the repo that defines a backend service
    must bind-mount the directions volume (rw) at the correct host and
    container paths."""

    @staticmethod
    def _load_compose(path):
        with open(path) as fh:
            return yaml.safe_load(fh)

    def test_all_compose_files_have_directions_bind_mount(self):
        files = _compose_files()
        assert files, "No docker-compose*.yml files found at repo root"

        for compose_path in files:
            basename = os.path.basename(compose_path)
            compose = self._load_compose(compose_path)
            services = compose.get("services", {})
            assert "backend" in services, f"{basename}: must define a 'backend' service"

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
                f"{basename}: directions bind-mount must be rw, but it is read_only={read_only}"
            )


# ---------------------------------------------------------------------------
# Config path — default and override
# ---------------------------------------------------------------------------


class TestFactoryDirectionsPathConfig:
    """backend/app/config.py must expose a configurable factory_directions_path
    defaulting to /var/factory/directions."""

    def test_factory_directions_path_default(self):
        s = Settings()
        assert s.factory_directions_path == "/var/factory/directions", (
            f"Expected default /var/factory/directions, got {s.factory_directions_path}"
        )

    def test_factory_directions_path_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("FACTORY_DIRECTIONS_PATH", "/custom/directions")
        s = Settings()
        assert s.factory_directions_path == "/custom/directions"


# ---------------------------------------------------------------------------
# Behavioural smoke tests — write through the configured path
# ---------------------------------------------------------------------------


class TestDirectionsWriteRead:
    """Smoke: write_direction() creates real files that are visible at the
    configured path, proving the host ↔ container file visibility the
    bind-mount is meant to support."""

    def test_write_direction_creates_directory_and_files(self, tmp_path):
        payload = {"goal_type": "test_type", "params": {"x": 1}}
        dir_path = write_direction("d010-smoke", payload, base_path=str(tmp_path))

        assert os.path.isdir(dir_path)
        assert dir_path == os.path.join(str(tmp_path), "d010-smoke")

        # direction.json must exist and contain the exact payload
        json_path = os.path.join(dir_path, "direction.json")
        assert os.path.isfile(json_path)
        with open(json_path) as fh:
            written = json.load(fh)
        assert written == payload

        # .manifest must exist and reference the direction name
        manifest_path = os.path.join(dir_path, ".manifest")
        assert os.path.isfile(manifest_path)
        with open(manifest_path) as fh:
            manifest = json.load(fh)
        assert manifest["direction"] == "d010-smoke"
        assert "written_at" in manifest

    def test_write_direction_uses_configured_path_by_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FACTORY_DIRECTIONS_PATH", str(tmp_path))
        s = Settings()
        payload = {"goal_type": "default_path_test"}
        dir_path = write_direction("default-path", payload, base_path=s.factory_directions_path)

        assert dir_path == os.path.join(str(tmp_path), "default-path")
        assert os.path.isfile(os.path.join(dir_path, "direction.json"))

    def test_write_direction_overwrites_existing(self, tmp_path):
        existing = os.path.join(str(tmp_path), "overwrite-test")
        os.makedirs(existing, exist_ok=True)
        with open(os.path.join(existing, "direction.json"), "w") as fh:
            json.dump({"old": True}, fh)

        payload = {"new": True}
        dir_path = write_direction("overwrite-test", payload, base_path=str(tmp_path))

        with open(os.path.join(dir_path, "direction.json")) as fh:
            written = json.load(fh)
        assert written == payload
