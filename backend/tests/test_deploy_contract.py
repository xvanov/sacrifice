"""Tests that verify the production deploy contract defined in
apps/sacrifice/config.yaml is consistent with the codebase.

These tests are red-first: they validate that the config contract
matches the actual deploy artifacts (Dockerfile, compose file, health
endpoint) present in the repo.
"""

import os
import re
import subprocess
import yaml

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


# ─── Path helpers ───

def _repo_root():
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )


def _config_path():
    # The canonical deploy config lives in the factory's apps/sacrifice tree.
    # In a factory worktree this resolves relative to the factory root.
    # Standard factory layout: ~/software-factory/apps/sacrifice/config.yaml
    # Worktrees live under ~/software-factory/state/worktrees/<name>/
    candidates = [
        os.path.join(_repo_root(), "..", "..", "..", "apps", "sacrifice", "config.yaml"),
        os.path.join(_repo_root(), "..", "..", "apps", "sacrifice", "config.yaml"),
        os.path.join(_repo_root(), "..", "apps", "sacrifice", "config.yaml"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return os.path.abspath(p)
    return os.path.abspath(candidates[0])


def _load_config():
    path = _config_path()
    if not os.path.isfile(path):
        pytest.skip(f"config.yaml not found at {path}")
    with open(path) as fh:
        return yaml.safe_load(fh)


# ─── Health endpoint ───


async def test_healthz_endpoint_serves_200_unauthenticated():
    """The /healthz route must return 200 without auth — it is the deploy
    health-check endpoint used by the factory's deploy machinery."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ─── Dockerfile exists and is valid ───


def test_backend_dockerfile_exists():
    dockerfile = os.path.join(_repo_root(), "backend", "Dockerfile")
    assert os.path.isfile(dockerfile), (
        f"backend/Dockerfile must exist at {dockerfile}"
    )


def test_dockerfile_exposes_port_8000():
    dockerfile = os.path.join(_repo_root(), "backend", "Dockerfile")
    with open(dockerfile) as fh:
        content = fh.read()
    assert "EXPOSE 8000" in content, "Dockerfile must EXPOSE 8000"


def test_dockerfile_has_healthcheck():
    dockerfile = os.path.join(_repo_root(), "backend", "Dockerfile")
    with open(dockerfile) as fh:
        content = fh.read()
    assert "HEALTHCHECK" in content, "Dockerfile must have a HEALTHCHECK"


# ─── docker-compose.prod.yml exists ───


def test_docker_compose_prod_exists():
    compose_file = os.path.join(_repo_root(), "docker-compose.prod.yml")
    assert os.path.isfile(compose_file), (
        f"docker-compose.prod.yml must exist at {compose_file}"
    )


def test_docker_compose_prod_is_valid_yaml():
    compose_file = os.path.join(_repo_root(), "docker-compose.prod.yml")
    with open(compose_file) as fh:
        data = yaml.safe_load(fh)
    assert "services" in data
    assert "backend" in data["services"], (
        "docker-compose.prod.yml must define a 'backend' service"
    )


def test_prod_compose_backend_maps_port_8000():
    compose_file = os.path.join(_repo_root(), "docker-compose.prod.yml")
    with open(compose_file) as fh:
        data = yaml.safe_load(fh)
    backend = data["services"]["backend"]
    ports = backend.get("ports", [])
    assert any("8000" in str(p) for p in ports), (
        "backend service must map port 8000"
    )


# ─── Config contract ───


def test_config_deploy_section_present():
    cfg = _load_config()
    assert "deploy" in cfg, "config.yaml must have a 'deploy' section"


def test_config_health_check_matches_healthz():
    """The deploy health_check_command must use /healthz, which is the
    unauthenticated endpoint that exists in the app."""
    cfg = _load_config()
    hc = cfg["deploy"].get("health_check_command", "")
    assert "/healthz" in hc, (
        f"health_check_command must target /healthz, got: {hc}"
    )


def test_config_deploy_command_is_docker_compose_up():
    cfg = _load_config()
    cmd = cfg["deploy"].get("deploy_command", "")
    assert "docker compose -f docker-compose.prod.yml up -d" in cmd, (
        f"deploy_command must use docker-compose.prod.yml, got: {cmd}"
    )


def test_config_pre_deploy_builds_prod_compose():
    cfg = _load_config()
    pre = cfg["deploy"].get("pre_deploy_commands", [])
    assert any("docker-compose.prod.yml build" in c for c in pre), (
        f"pre_deploy_commands must build docker-compose.prod.yml, got: {pre}"
    )


def test_config_rollback_targets_previous_compose():
    cfg = _load_config()
    rollback = cfg["deploy"].get("rollback_command", "")
    assert "docker-compose.prod.yml.previous" in rollback, (
        f"rollback_command must reference docker-compose.prod.yml.previous, got: {rollback}"
    )


def test_config_deploy_enabled_is_true():
    """The deploy block must be enabled so factory auto-deploy triggers on
    merge to main."""
    cfg = _load_config()
    assert cfg["deploy"].get("enabled") is True, (
        "deploy.enabled must be true for the factory's auto-deploy machinery"
    )


def test_config_smoke_test_command_is_make_smoke():
    """AC5.3: the smoke gate (make smoke) gates the deploy. The deploy
    config's smoke_test_command must be 'make smoke'."""
    cfg = _load_config()
    smoke = cfg["deploy"].get("smoke_test_command", "")
    assert smoke == "make smoke", (
        f"smoke_test_command must be 'make smoke' to match AC5.3, got: {smoke}"
    )


# ─── Mobile auth contract (AC4) — verify routes are public ───


async def test_email_login_route_accepts_post():
    """AC4.1/AC4.3: POST /api/auth/email/login must be reachable without
    auth (the credentials are in the body, not a header)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Empty body triggers validation error, not auth error
        resp = await client.post("/api/auth/email/login", json={})
    # 422 = validation error (missing fields), not 401/403 (auth required)
    assert resp.status_code == 422


async def test_email_register_route_accepts_post():
    """AC4.2/AC4.4: POST /api/auth/email/register must be reachable without
    auth."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/auth/email/register", json={})
    # 422 = validation error (missing fields), not 401/403
    assert resp.status_code == 422


# ─── Rollback artifact (AC2.2, AC2.3) ───


def test_docker_compose_prod_previous_exists():
    """AC2.2: the rollback artifact must exist for the factory's
    rollback_command to function."""
    previous_file = os.path.join(_repo_root(), "docker-compose.prod.yml.previous")
    assert os.path.isfile(previous_file), (
        f"Rollback artifact missing: {previous_file}. "
        "The factory's rollback_command references this file."
    )


def test_docker_compose_prod_previous_matches_service_topology():
    """The .previous file must have the same service topology as current.
    Different topology could make rollback ineffective."""
    current_path = os.path.join(_repo_root(), "docker-compose.prod.yml")
    previous_path = os.path.join(_repo_root(), "docker-compose.prod.yml.previous")

    with open(current_path) as fh:
        current = yaml.safe_load(fh)
    with open(previous_path) as fh:
        previous = yaml.safe_load(fh)

    current_services = set(current.get("services", {}).keys())
    previous_services = set(previous.get("services", {}).keys())
    assert current_services == previous_services, (
        f"Service mismatch between current ({current_services}) "
        f"and .previous ({previous_services})"
    )


# ─── CSRF does NOT block email auth (AC4.3, AC4.4) ───


async def test_email_login_does_not_require_csrf_header():
    """Mobile clients (Expo Go) cannot set X-CSRF-Token reliably.
    Email login must succeed without it."""
    transport = ASGITransport(app=app)
    email = f"csrf-test-login-{os.urandom(4).hex()}@test.com"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register first so we have a valid user
        reg_resp = await client.post(
            "/api/auth/email/register",
            json={
                "email": email,
                "password": "longenoughpw",
            },
        )
        assert reg_resp.status_code == 200
        # Login WITHOUT X-CSRF-Token header
        resp = await client.post(
            "/api/auth/email/login",
            json={"email": email, "password": "longenoughpw"},
        )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_email_register_does_not_require_csrf_header():
    """Mobile clients (Expo Go) must be able to register without CSRF header."""
    transport = ASGITransport(app=app)
    email = f"csrf-test-reg-{os.urandom(4).hex()}@test.com"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/email/register",
            json={
                "email": email,
                "password": "longenoughpw",
            },
        )
    assert resp.status_code == 200
    assert "access_token" in resp.json()