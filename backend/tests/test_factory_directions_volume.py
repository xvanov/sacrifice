"""
Tests for D010: compose bind-mount for factory directions volume.

These tests assert infrastructure configuration:
- docker-compose.yml exists with a bind-mount for the directions volume
- backend config exposes a configurable factory_directions_path
- any prod compose variant also carries the bind mount
"""

import os
import re

import yaml

from app.config import Settings

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

HOST_DIRECTIONS_PATH = "~/software-factory/apps/sacrifice/directions"
CONTAINER_DIRECTIONS_PATH = "/var/factory/directions"


# ---------------------------------------------------------------------------
# docker-compose.yml existence and volume tests
# ---------------------------------------------------------------------------

class TestDockerComposeExists:
    """docker-compose.yml must exist at the repo root."""

    def test_docker_compose_yml_exists(self):
        compose_path = os.path.join(REPO_ROOT, "docker-compose.yml")
        assert os.path.isfile(compose_path), (
            f"docker-compose.yml not found at {compose_path}"
        )


class TestDockerComposeBackendVolume:
    """The backend service in docker-compose.yml must declare the directions
    bind-mount volume (rw)."""

    @staticmethod
    def _load_compose():
        compose_path = os.path.join(REPO_ROOT, "docker-compose.yml")
        with open(compose_path, "r") as fh:
            return yaml.safe_load(fh)

    def test_backend_service_exists(self):
        compose = self._load_compose()
        services = compose.get("services", {})
        assert "backend" in services, (
            "docker-compose.yml must define a 'backend' service"
        )

    def test_backend_has_volumes(self):
        compose = self._load_compose()
        backend = compose["services"]["backend"]
        volumes = backend.get("volumes", [])
        assert volumes, "backend service must declare at least one volume"

    def test_directions_bind_mount_present(self):
        compose = self._load_compose()
        backend = compose["services"]["backend"]
        volumes = backend.get("volumes", [])

        found = False
        for vol in volumes:
            if isinstance(vol, str):
                parts = vol.split(":")
                if len(parts) >= 2:
                    host = parts[0]
                    container = parts[1]
                    if (HOST_DIRECTIONS_PATH in host
                            and container == CONTAINER_DIRECTIONS_PATH):
                        found = True
                        break
            elif isinstance(vol, dict):
                # long-syntax volumes
                if vol.get("target") == CONTAINER_DIRECTIONS_PATH:
                    found = True
                    break

        assert found, (
            f"No bind-mount mapping {HOST_DIRECTIONS_PATH} "
            f"-> {CONTAINER_DIRECTIONS_PATH} found in backend volumes"
        )

    def test_directions_bind_mount_is_read_write(self):
        compose = self._load_compose()
        backend = compose["services"]["backend"]
        volumes = backend.get("volumes", [])

        for vol in volumes:
            if isinstance(vol, str):
                parts = vol.split(":")
                if (HOST_DIRECTIONS_PATH in parts[0]
                        and len(parts) >= 3
                        and parts[2] == "ro"):
                    raise AssertionError(
                        "directions bind-mount must be rw, got 'ro'"
                    )
            elif isinstance(vol, dict):
                if vol.get("target") == CONTAINER_DIRECTIONS_PATH:
                    if vol.get("read_only") is True:
                        raise AssertionError(
                            "directions bind-mount must be rw, got read_only: true"
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


# ---------------------------------------------------------------------------
# Prod compose variant test (conditional)
# ---------------------------------------------------------------------------

class TestProdComposeVariant:
    """If a production compose variant exists, it must also bind-mount the
    directions volume."""

    PROD_CANDIDATES = (
        "docker-compose.prod.yml",
        "docker-compose.production.yml",
    )

    @staticmethod
    def _prod_compose_path():
        for name in TestProdComposeVariant.PROD_CANDIDATES:
            path = os.path.join(REPO_ROOT, name)
            if os.path.isfile(path):
                return path, name
        return None, None

    def test_prod_compose_has_directions_volume_if_present(self):
        path, name = self._prod_compose_path()
        if path is None:
            return  # no prod variant — nothing to assert

        with open(path, "r") as fh:
            compose = yaml.safe_load(fh)

        backend = compose.get("services", {}).get("backend", {})
        volumes = backend.get("volumes", [])

        found = False
        for vol in volumes:
            if isinstance(vol, str):
                parts = vol.split(":")
                if len(parts) >= 2:
                    if (HOST_DIRECTIONS_PATH in parts[0]
                            and parts[1] == CONTAINER_DIRECTIONS_PATH):
                        found = True
                        break
            elif isinstance(vol, dict):
                if vol.get("target") == CONTAINER_DIRECTIONS_PATH:
                    found = True
                    break

        assert found, (
            f"{name} exists but is missing the directions bind-mount "
            f"({HOST_DIRECTIONS_PATH} -> {CONTAINER_DIRECTIONS_PATH})"
        )