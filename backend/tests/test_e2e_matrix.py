"""End-to-end regression matrix: every goal type × verified / failed.

Each cell drives the REAL product pipeline through the API — dummy account
registration, goal creation, activation, proof submission — then runs the
type's REAL verification worker with only the external edges mocked
(HTTP/YouTube/GitHub/Docker/LLM/Stripe), and asserts:

- verified path: goal → verified, and NO money moves;
- failed path:   goal stays active and uncharged immediately (the owner still
  has until the deadline to submit again), then — once the deadline sweep
  runs — goal → failed but still uncharged (the pledge waits for local
  midnight, see app/services/charge_scheduling.py), and only once that buffer
  elapses and process_deferred_charges runs does the charge fire and a
  succeeded payment row land (Stripe mocked — dummy transactions, no real
  money).

Born from a live incident (2026-07-17) where a failed goal never left
"active": no single test drove creation → verification → charge end-to-end.
"""

import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app

pytestmark = pytest.mark.asyncio


def make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _register_dummy(client, tag: str):
    email = f"e2e-{tag}-{uuid_mod.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/auth/email/register",
        json={"email": email, "password": "E2e-matrix-pass1", "display_name": f"E2E {tag}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["access_token"], body["user"]


# ── external-edge mocks ────────────────────────────────────────────────────


def _httpx_client_mock(status_code: int, json_body=None, text_body: str = ""):
    """An AsyncClient class mock whose request/get return a canned response."""
    response = MagicMock()
    response.status_code = status_code
    response.text = text_body or (str(json_body) if json_body is not None else "")
    response.json.return_value = json_body if json_body is not None else {}
    response.headers = {"content-type": "application/json"}

    client = MagicMock()
    client.request = AsyncMock(return_value=response)
    client.get = AsyncMock(return_value=response)
    cls = MagicMock()
    cls.return_value.__aenter__ = AsyncMock(return_value=client)
    cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return cls


class _FakeSandboxResult(SimpleNamespace):
    @property
    def success(self):
        return self.exit_code == 0


def _fake_docker_sandbox(exit_code: int):
    result = _FakeSandboxResult(exit_code=exit_code, stdout="test run", stderr="", timed_out=False)
    sandbox = MagicMock()
    sandbox.run_command.return_value = result
    return MagicMock(return_value=sandbox)


# ── the matrix ─────────────────────────────────────────────────────────────
#
# Each scenario: goal criteria, proof body, the real worker entrypoint, the
# celery dispatch to intercept at submit time, and the external patches that
# force a pass vs a fail.

def _scenarios():
    return [
        {
            "goal_type": "geolocation",
            "criteria": {"target_latitude": 35.8982, "target_longitude": -78.9408, "radius_m": 100},
            "proof": {"latitude": 35.8983, "longitude": -78.9409, "accuracy_m": 10},
            "fail_proof": {"latitude": 36.5, "longitude": -78.9409, "accuracy_m": 10},
            "dispatch": "app.workers.geolocation.run_geolocation_verification_task.delay",
            "runner": "app.workers.geolocation.run_geolocation_verification",
            "pass_patches": lambda: [],
            "fail_patches": lambda: [],
        },
        {
            "goal_type": "api_endpoint",
            "criteria": {"url": "https://example.com/health", "method": "GET", "expected_status": 200},
            "proof": {"url": "https://example.com/health", "method": "GET"},
            "dispatch": "app.workers.api_check.run_api_verification_task.delay",
            "runner": "app.workers.api_check.run_api_verification",
            "pass_patches": lambda: [
                patch("app.workers.api_check.httpx.AsyncClient", _httpx_client_mock(200, {"ok": True})),
            ],
            "fail_patches": lambda: [
                patch("app.workers.api_check.httpx.AsyncClient", _httpx_client_mock(500, {"error": "boom"})),
            ],
        },
        {
            "goal_type": "youtube_video",
            "criteria": {"min_duration_seconds": 60, "video_description": "A walkthrough of my project"},
            "proof": {"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            "dispatch": "app.workers.youtube.run_youtube_verification_task.delay",
            "runner": "app.workers.youtube.run_youtube_verification",
            "pass_patches": lambda: [
                patch("app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock,
                      return_value={"video_id": "dQw4w9WgXcQ", "title": "Walkthrough", "duration_seconds": 300}),
                patch("app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock,
                      return_value="a real walkthrough of the project"),
                patch("app.workers.youtube.judge_transcript_content", new_callable=AsyncMock,
                      return_value={"authentic": True, "reasoning": "matches"}),
            ],
            "fail_patches": lambda: [
                patch("app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock,
                      return_value={"video_id": "dQw4w9WgXcQ", "title": "Too short", "duration_seconds": 5}),
                patch("app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock,
                      return_value="unrelated"),
                patch("app.workers.youtube.judge_transcript_content", new_callable=AsyncMock,
                      return_value={"authentic": False, "reasoning": "does not match"}),
            ],
        },
        {
            "goal_type": "github_repo",
            "criteria": {
                "repo_owner": "octocat", "repo_name": "hello",
                "conditions": [{"type": "commits", "min_count": 1}],
            },
            "proof": {"repo_url": "https://github.com/octocat/hello"},
            "dispatch": "app.workers.github_repo.run_github_repo_verification_task.delay",
            "runner": "app.workers.github_repo.run_github_repo_verification",
            "pass_patches": lambda: [
                patch(
                    "app.workers.github_repo.httpx.AsyncClient",
                    _httpx_client_mock(200, [{"sha": "abc123"}]),
                ),
            ],
            "fail_patches": lambda: [
                patch(
                    "app.workers.github_repo.httpx.AsyncClient",
                    _httpx_client_mock(200, []),
                ),
            ],
        },
        {
            "goal_type": "dev_sandbox",
            "criteria": {"repo_url": "https://github.com/octocat/hello", "test_command": "pytest -q",
                         "goal_description": "make the tests pass"},
            "proof": {"repo_url": "https://github.com/octocat/hello", "test_command": "pytest -q"},
            "dispatch": "app.workers.dev_sandbox.run_dev_sandbox_verification_task.delay",
            "runner": "app.workers.dev_sandbox.run_dev_sandbox_verification",
            "pass_patches": lambda: [
                patch("app.workers.dev_sandbox.clone_repo", return_value=None),
                patch("app.workers.dev_sandbox.detect_language", return_value="python"),
                patch("app.workers.dev_sandbox.get_install_command", return_value=None),
                patch("app.workers.dev_sandbox.DockerSandbox", _fake_docker_sandbox(exit_code=0)),
                patch("app.workers.dev_sandbox._generate_code_summary", return_value="summary"),
                patch("app.workers.dev_sandbox.judge_code_authenticity", new_callable=AsyncMock,
                      return_value={"authentic": True, "reasoning": "looks real"}),
            ],
            "fail_patches": lambda: [
                patch("app.workers.dev_sandbox.clone_repo", return_value=None),
                patch("app.workers.dev_sandbox.detect_language", return_value="python"),
                patch("app.workers.dev_sandbox.get_install_command", return_value=None),
                patch("app.workers.dev_sandbox.DockerSandbox", _fake_docker_sandbox(exit_code=1)),
                patch("app.workers.dev_sandbox._generate_code_summary", return_value="summary"),
            ],
        },
    ]


async def _drive(scenario: dict, outcome: str):
    """Run one matrix cell end-to-end; returns (goal_id, payments, notifications)."""
    import importlib

    async with make_client() as client:
        token, user = await _register_dummy(client, f"{scenario['goal_type']}-{outcome}")
        auth_hdr = {"Authorization": f"Bearer {token}"}

        deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        resp = await client.post(
            "/api/goals", headers=auth_hdr,
            json={
                "title": f"E2E {scenario['goal_type']} {outcome}",
                "deadline": deadline,
                "pledge_amount": 500,
                "goal_type": scenario["goal_type"],
                "criteria": scenario["criteria"],
            },
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

        resp = await client.put(
            f"/api/goals/{goal_id}", headers=auth_hdr, json={"status": "active"}
        )
        assert resp.status_code == 200, resp.text

        proof = scenario.get("fail_proof") if (outcome == "failed" and scenario.get("fail_proof")) else scenario["proof"]
        with patch(scenario["dispatch"]) as mock_delay:
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof", headers=auth_hdr, json=proof
            )
        assert resp.status_code == 202, resp.text
        submission_id = resp.json()["submission_id"]
        mock_delay.assert_called_once()
        dispatch_kwargs = mock_delay.call_args.kwargs

        # Give the dummy user a (fake) saved card so a failed goal charges.
        engine = create_async_engine(settings.database_url, echo=False)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as db:
            await db.execute(
                text("UPDATE users SET stripe_customer_id = 'cus_e2e' WHERE id = :uid"),
                {"uid": user["id"]},
            )
            await db.commit()

        # Run the REAL verification worker (fresh session path) with external
        # edges + Stripe mocked. This is exactly what celery would execute.
        module_path, func_name = scenario["runner"].rsplit(".", 1)
        runner = getattr(importlib.import_module(module_path), func_name)

        pi = MagicMock()
        pi.id = f"pi_e2e_{scenario['goal_type']}_{outcome}"
        pi.status = "succeeded"
        patches = (scenario["pass_patches"] if outcome == "verified" else scenario["fail_patches"])()
        patches += [
            patch("app.workers.payments._resolve_payment_method", return_value="pm_e2e"),
            patch("app.workers.payments.stripe.PaymentIntent.create", return_value=pi),
            patch("app.workers.payments.stripe.PaymentIntent.retrieve", return_value=pi),
            patch("app.workers.payments.stripe.Transfer.create"),
        ]
        from contextlib import ExitStack

        # Pass an explicit per-test session: the workers' fallback
        # `async_session()` is a module-global engine bound to the FIRST
        # test's event loop, and pytest-asyncio gives every test a fresh
        # loop ("Event loop is closed" otherwise).
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            async with session_factory() as worker_db:
                result = await runner(
                    goal_id=uuid_mod.UUID(goal_id),
                    submission_id=uuid_mod.UUID(submission_id),
                    proof_data=dispatch_kwargs["proof_data"],
                    criteria_data=dispatch_kwargs["criteria_data"],
                    db=worker_db,
                )

            assert result["verification_status"] == outcome, result

            if outcome == "failed":
                # A failed verdict on a still-active, not-yet-due goal no
                # longer resolves or charges on the spot — the owner has a
                # window to submit again before the deadline (see
                # verification_result.py's "A real failure before the
                # deadline is not yet a verdict on the goal"). Confirm that
                # deferral, then advance to the deadline sweep — the same
                # component that resolves a goal with no submission at all —
                # to drive the rest of the pipeline this test exists to cover.
                resp = await client.get(f"/api/goals/{goal_id}", headers=auth_hdr)
                assert resp.json()["status"] == "active"
                resp = await client.get("/api/payments", headers=auth_hdr)
                assert resp.json() == [], "must not charge before the deadline"

                async with session_factory() as db:
                    await db.execute(
                        text("UPDATE goals SET deadline = :d WHERE id = :g"),
                        {
                            "d": datetime.now(timezone.utc) - timedelta(minutes=1),
                            "g": goal_id,
                        },
                    )
                    await db.commit()

                from app.workers.deadline import check_deadlines

                await check_deadlines()

                # The goal is failed now, but the charge itself waits for
                # local midnight (app/services/charge_scheduling.py) — back-
                # date the buffer and run the sweep that actually collects it.
                resp = await client.get(f"/api/goals/{goal_id}", headers=auth_hdr)
                assert resp.json()["status"] == "failed"
                resp = await client.get("/api/payments", headers=auth_hdr)
                assert resp.json() == [], "must not charge before the midnight buffer"

                async with session_factory() as db:
                    await db.execute(
                        text("UPDATE goals SET charge_after = :ca WHERE id = :g"),
                        {
                            "ca": datetime.now(timezone.utc) - timedelta(minutes=1),
                            "g": goal_id,
                        },
                    )
                    await db.commit()

                from app.workers.payments import process_deferred_charges

                await process_deferred_charges()

        # Final state through the API, like a user would see it.
        resp = await client.get(f"/api/goals/{goal_id}", headers=auth_hdr)
        goal_status = resp.json()["status"]
        resp = await client.get("/api/payments", headers=auth_hdr)
        payments = resp.json()

        await engine.dispose()
        return goal_status, payments


@pytest.mark.parametrize("scenario", _scenarios(), ids=lambda s: s["goal_type"])
async def test_goal_type_verifies_end_to_end(scenario):
    goal_status, payments = await _drive(scenario, "verified")
    assert goal_status == "verified"
    assert payments == [], "a verified goal must never be charged"


@pytest.mark.parametrize("scenario", _scenarios(), ids=lambda s: s["goal_type"])
async def test_goal_type_fails_and_charges_end_to_end(scenario):
    goal_status, payments = await _drive(scenario, "failed")
    assert goal_status == "failed"
    assert len(payments) == 1, "a failed goal must be charged exactly once"
    assert payments[0]["status"] == "succeeded"
    assert payments[0]["amount"] == 500
