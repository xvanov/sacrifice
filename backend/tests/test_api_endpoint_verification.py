from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.goal import Goal
from app.models.proof import ProofSubmission


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(
    client,
    email="test@example.com",
    name="Test User",
    sub="test-sub-123",
    token="valid-token",
):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


def _create_api_goal(
    title="My API Goal",
    method="GET",
    url="https://httpbin.org/get",
    expected_status=200,
    expected_body_schema=None,
    headers=None,
):
    criteria = {
        "method": method,
        "url": url,
        "expected_status": expected_status,
    }
    if expected_body_schema:
        criteria["expected_body_schema"] = expected_body_schema
    if headers:
        criteria["headers"] = headers

    return {
        "title": title,
        "description": "Get my API endpoint working",
        "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "pledge_amount": 5000,
        "goal_type": "api_endpoint",
        "criteria": criteria,
        "charity_id": "acct_charity123",
    }


async def _create_goal_and_activate(client, token, goal_data=None):
    if goal_data is None:
        goal_data = _create_api_goal()
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json=goal_data,
    )
    goal_id = resp.json()["id"]
    await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    return goal_id


def _make_mock_response(
    status_code=200,
    json_data=None,
    text=None,
    headers=None,
):
    if json_data is None:
        json_data = {"status": "ok"}
    if text is None:
        text = str(json_data)
    if headers is None:
        headers = {"content-type": "application/json"}

    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.text = text
    mock_resp.headers = headers
    return mock_resp


def _make_httpx_mock(mock_response):
    mock_client = AsyncMock()
    mock_client.request.return_value = mock_response
    mock_client_cls = MagicMock()
    mock_client_cls.return_value.__aenter__.return_value = mock_client
    return mock_client_cls, mock_client


# ─── Core verification logic (mocked HTTP, no DB) ──────────────────


@pytest.mark.asyncio
async def test_verify_api_get_request_returns_status_and_body():
    from app.workers.api_check import verify_api_endpoint

    criteria_data = {
        "method": "GET",
        "url": "https://example.com/api/health",
        "expected_status": 200,
    }
    proof_data = {
        "url": "https://example.com/api/health",
        "method": "GET",
    }

    mock_resp = _make_mock_response()
    mock_cls, _ = _make_httpx_mock(mock_resp)

    with patch("app.workers.api_check.httpx.AsyncClient", mock_cls):
        result = await verify_api_endpoint(proof_data, criteria_data)

    assert result["verification_status"] == "verified"
    assert result["verification_details"]["actual_status"] == 200
    assert result["verification_details"]["expected_status"] == 200
    assert result["verification_details"]["response_body_preview"] is not None
    assert result["verification_details"]["status_passed"] is True


@pytest.mark.asyncio
async def test_verify_api_expected_status_200_returns_verified_for_200():
    from app.workers.api_check import verify_api_endpoint

    criteria_data = {
        "method": "GET",
        "url": "https://example.com/api/health",
        "expected_status": 200,
    }
    proof_data = {
        "url": "https://example.com/api/health",
        "method": "GET",
    }

    mock_resp = _make_mock_response()
    mock_cls, _ = _make_httpx_mock(mock_resp)

    with patch("app.workers.api_check.httpx.AsyncClient", mock_cls):
        result = await verify_api_endpoint(proof_data, criteria_data)

    assert result["verification_status"] == "verified"
    assert result["verification_details"]["status_passed"] is True


@pytest.mark.asyncio
async def test_verify_api_expected_status_200_fails_for_500():
    from app.workers.api_check import verify_api_endpoint

    criteria_data = {
        "method": "GET",
        "url": "https://example.com/api/health",
        "expected_status": 200,
    }
    proof_data = {
        "url": "https://example.com/api/health",
        "method": "GET",
    }

    mock_resp = _make_mock_response(
        status_code=500, text="Internal Server Error", headers={}
    )
    mock_cls, _ = _make_httpx_mock(mock_resp)

    with patch("app.workers.api_check.httpx.AsyncClient", mock_cls):
        result = await verify_api_endpoint(proof_data, criteria_data)

    assert result["verification_status"] == "failed"
    assert result["verification_details"]["status_passed"] is False
    assert "500" in str(result["verification_details"])


@pytest.mark.asyncio
async def test_verify_api_with_json_body_schema_validates_response():
    from app.workers.api_check import verify_api_endpoint

    criteria_data = {
        "method": "POST",
        "url": "https://example.com/api/data",
        "expected_status": 200,
        "expected_body_schema": {
            "type": "object",
            "required": ["id", "name"],
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
            },
        },
    }
    proof_data = {
        "url": "https://example.com/api/data",
        "method": "POST",
    }

    mock_resp = _make_mock_response(json_data={"id": 1, "name": "Test"})
    mock_cls, _ = _make_httpx_mock(mock_resp)

    with patch("app.workers.api_check.httpx.AsyncClient", mock_cls):
        result = await verify_api_endpoint(proof_data, criteria_data)

    assert result["verification_status"] == "verified"
    assert result["verification_details"]["status_passed"] is True
    assert result["verification_details"]["schema_passed"] is True


@pytest.mark.asyncio
async def test_verify_api_with_json_body_schema_fails_on_mismatch():
    from app.workers.api_check import verify_api_endpoint

    criteria_data = {
        "method": "POST",
        "url": "https://example.com/api/data",
        "expected_status": 200,
        "expected_body_schema": {
            "type": "object",
            "required": ["id", "name"],
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
            },
        },
    }
    proof_data = {
        "url": "https://example.com/api/data",
        "method": "POST",
    }

    mock_resp = _make_mock_response(json_data={"id": "not-an-integer"})
    mock_cls, _ = _make_httpx_mock(mock_resp)

    with patch("app.workers.api_check.httpx.AsyncClient", mock_cls):
        result = await verify_api_endpoint(proof_data, criteria_data)

    assert result["verification_status"] == "failed"
    assert result["verification_details"]["schema_passed"] is False
    assert "schema" in str(result["verification_details"]).lower()


@pytest.mark.asyncio
async def test_verify_api_with_custom_headers_sends_them():
    from app.workers.api_check import verify_api_endpoint

    criteria_data = {
        "method": "GET",
        "url": "https://example.com/api/private",
        "expected_status": 200,
        "headers": {"Authorization": "Bearer test-token-123"},
    }
    proof_data = {
        "url": "https://example.com/api/private",
        "method": "GET",
    }

    mock_resp = _make_mock_response(json_data={"data": "secret"})
    mock_cls, mock_client = _make_httpx_mock(mock_resp)

    with patch("app.workers.api_check.httpx.AsyncClient", mock_cls):
        result = await verify_api_endpoint(proof_data, criteria_data)

    assert result["verification_status"] == "verified"

    call_kwargs = mock_client.request.call_args.kwargs
    sent_headers = call_kwargs.get("headers", {})
    assert sent_headers.get("Authorization") == "Bearer test-token-123"


@pytest.mark.asyncio
async def test_verify_api_timeout_returns_clear_failure():
    from app.workers.api_check import verify_api_endpoint

    criteria_data = {
        "method": "GET",
        "url": "https://example.com/api/slow",
        "expected_status": 200,
    }
    proof_data = {
        "url": "https://example.com/api/slow",
        "method": "GET",
    }

    mock_client = AsyncMock()
    mock_client.request.side_effect = TimeoutError("Request timed out after 10 seconds")
    mock_cls = MagicMock()
    mock_cls.return_value.__aenter__.return_value = mock_client

    with patch("app.workers.api_check.httpx.AsyncClient", mock_cls):
        result = await verify_api_endpoint(proof_data, criteria_data)

    assert result["verification_status"] == "failed"
    details_lower = str(result["verification_details"]).lower()
    assert "timeout" in details_lower or "timed out" in details_lower


@pytest.mark.asyncio
async def test_verify_api_unreachable_host_returns_clear_failure():
    from app.workers.api_check import verify_api_endpoint

    criteria_data = {
        "method": "GET",
        "url": "https://nonexistent.example.com/api",
        "expected_status": 200,
    }
    proof_data = {
        "url": "https://nonexistent.example.com/api",
        "method": "GET",
    }

    mock_client = AsyncMock()
    mock_client.request.side_effect = ConnectionError("Failed to resolve host")
    mock_cls = MagicMock()
    mock_cls.return_value.__aenter__.return_value = mock_client

    with patch("app.workers.api_check.httpx.AsyncClient", mock_cls):
        result = await verify_api_endpoint(proof_data, criteria_data)

    # Still the user's fault and still charges: the URL is theirs, and an address
    # that does not answer is the very thing being measured. The wording changed
    # when transport failures started being attributed explicitly ("Endpoint did
    # not respond" rather than "Host unreachable"), so assert the outcome and the
    # underlying cause rather than the old phrasing.
    assert result["verification_status"] == "failed"
    assert result.get("inconclusive_reason") is None
    details_lower = str(result["verification_details"]).lower()
    assert "did not respond" in details_lower
    assert "failed to resolve host" in details_lower


@pytest.mark.asyncio
async def test_verify_api_non_json_response_handled_gracefully():
    from app.workers.api_check import verify_api_endpoint

    criteria_data = {
        "method": "GET",
        "url": "https://example.com/api/text",
        "expected_status": 200,
    }
    proof_data = {
        "url": "https://example.com/api/text",
        "method": "GET",
    }

    mock_resp = _make_mock_response(
        headers={"content-type": "text/html"},
    )
    mock_resp.json.side_effect = ValueError("Not JSON")
    mock_resp.text = "<html>not json</html>"

    mock_cls, _ = _make_httpx_mock(mock_resp)

    with patch("app.workers.api_check.httpx.AsyncClient", mock_cls):
        result = await verify_api_endpoint(proof_data, criteria_data)

    assert result["verification_status"] == "verified"
    assert result["verification_details"]["status_passed"] is True
    assert result["verification_details"]["is_json"] is False


@pytest.mark.asyncio
async def test_verify_api_records_request_and_response_details():
    from app.workers.api_check import verify_api_endpoint

    criteria_data = {
        "method": "POST",
        "url": "https://example.com/api/data",
        "expected_status": 201,
        "headers": {"X-Custom": "value"},
    }
    proof_data = {
        "url": "https://example.com/api/data",
        "method": "POST",
        "body": {"key": "value"},
    }

    mock_resp = _make_mock_response(
        status_code=201,
        json_data={"id": 42, "status": "created"},
        headers={"content-type": "application/json", "x-request-id": "abc123"},
    )
    mock_cls, _ = _make_httpx_mock(mock_resp)

    with patch("app.workers.api_check.httpx.AsyncClient", mock_cls):
        result = await verify_api_endpoint(proof_data, criteria_data)

    details = result["verification_details"]
    assert details["request_url"] == "https://example.com/api/data"
    assert details["request_method"] == "POST"
    assert details["actual_status"] == 201
    assert details["expected_status"] == 201
    assert details["actual_headers"] is not None
    assert details["response_body_preview"] is not None
    assert details["status_passed"] is True


# ─── POST /api/goals/{id}/submit-proof for api_endpoint goals ─────


@pytest.mark.asyncio
async def test_submit_proof_api_endpoint_valid_returns_202():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch(
            "app.workers.api_check.run_api_verification_task.delay"
        ) as mock_task:
            mock_task.return_value = None
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "url": "https://example.com/api/health",
                    "method": "GET",
                },
            )

    assert response.status_code == 202
    body = response.json()
    assert "submission_id" in body
    assert body["verification_status"] == "pending"
    mock_task.assert_called_once()


@pytest.mark.asyncio
async def test_submit_proof_api_endpoint_returns_422_for_missing_url():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"method": "GET"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_proof_api_endpoint_returns_400_for_non_api_goal():
    async with make_client() as client:
        token, _ = await _auth(client)

        youtube_goal = {
            "title": "YouTube goal",
            "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "pledge_amount": 5000,
            "goal_type": "youtube_video",
            "criteria": {
                "min_duration_seconds": 120,
                "video_description": "A walkthrough",
            },
        }
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=youtube_goal,
        )
        goal_id = resp.json()["id"]
        await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "url": "https://example.com/api/health",
                "method": "GET",
            },
        )

    assert response.status_code == 400


# ─── GET /api/goals/{id}/verification-status for api_endpoint ─────


@pytest.mark.asyncio
async def test_verification_status_returns_pending_after_submission_api():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch("app.workers.api_check.run_api_verification_task.delay"):
            submit_resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "url": "https://example.com/api/health",
                    "method": "GET",
                },
            )
            submission_id = submit_resp.json()["submission_id"]

            status_resp = await client.get(
                f"/api/goals/{goal_id}/verification-status",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["submission_id"] == submission_id
    assert body["verification_status"] == "pending"


# ─── Goal status transitions (via DB) for api_endpoint ─────────────


@pytest.mark.asyncio
async def test_api_verification_goal_status_transitions_to_verified():
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from app.config import settings

    local_engine = create_async_engine(settings.database_url, echo=False)
    local_session_factory = async_sessionmaker(
        local_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch("app.workers.api_check.run_api_verification_task.delay"):
            submit_resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "url": "https://example.com/api/health",
                    "method": "GET",
                },
            )
            submission_id = submit_resp.json()["submission_id"]

        async with local_session_factory() as db:
            result = await db.execute(
                select(ProofSubmission).where(ProofSubmission.id == submission_id)
            )
            submission = result.scalar_one()

            result = await db.execute(select(Goal).where(Goal.id == goal_id))
            goal = result.scalar_one()

            criteria_data = {
                "method": "GET",
                "url": "https://example.com/api/health",
                "expected_status": 200,
            }
            proof_data = {
                "url": "https://example.com/api/health",
                "method": "GET",
            }

            mock_resp = _make_mock_response()
            mock_cls, _ = _make_httpx_mock(mock_resp)

            with patch("app.workers.api_check.httpx.AsyncClient", mock_cls):
                from app.workers.api_check import run_api_verification

                await run_api_verification(
                    goal_id=goal.id,
                    submission_id=submission.id,
                    proof_data=proof_data,
                    criteria_data=criteria_data,
                    db=db,
                )

            await db.refresh(goal)
            await db.refresh(submission)

            assert goal.status == "verified"
            assert submission.verification_status == "verified"

        status_resp = await client.get(
            f"/api/goals/{goal_id}/verification-status",
            headers={"Authorization": f"Bearer {token}"},
        )
    body = status_resp.json()
    assert body["verification_status"] == "verified"
    await local_engine.dispose()


@pytest.mark.asyncio
async def test_api_verification_goal_status_transitions_to_failed():
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from app.config import settings

    local_engine = create_async_engine(settings.database_url, echo=False)
    local_session_factory = async_sessionmaker(
        local_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch("app.workers.api_check.run_api_verification_task.delay"):
            submit_resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "url": "https://example.com/api/health",
                    "method": "GET",
                },
            )
            submission_id = submit_resp.json()["submission_id"]

        async with local_session_factory() as db:
            result = await db.execute(
                select(ProofSubmission).where(ProofSubmission.id == submission_id)
            )
            submission = result.scalar_one()

            result = await db.execute(select(Goal).where(Goal.id == goal_id))
            goal = result.scalar_one()

            criteria_data = {
                "method": "GET",
                "url": "https://example.com/api/health",
                "expected_status": 200,
            }
            proof_data = {
                "url": "https://example.com/api/health",
                "method": "GET",
            }

            mock_resp = _make_mock_response(
                status_code=500, text="Internal Error", headers={}
            )
            mock_cls, _ = _make_httpx_mock(mock_resp)

            # A failed verification dispatches the pledge charge; isolate
            # billing (as the deadline-worker tests do) and assert dispatch.
            with (
                patch("app.workers.api_check.httpx.AsyncClient", mock_cls),
                patch(
                    "app.workers.payments.process_charge_for_goal",
                    new_callable=AsyncMock,
                ) as mock_charge,
            ):
                from app.workers.api_check import run_api_verification

                await run_api_verification(
                    goal_id=goal.id,
                    submission_id=submission.id,
                    proof_data=proof_data,
                    criteria_data=criteria_data,
                    db=db,
                )
            mock_charge.assert_awaited_once_with(str(goal.id), str(goal.user_id))

            await db.refresh(goal)
            await db.refresh(submission)
            assert goal.status == "failed"
            assert submission.verification_status == "failed"

        status_resp = await client.get(
            f"/api/goals/{goal_id}/verification-status",
            headers={"Authorization": f"Bearer {token}"},
        )
    body = status_resp.json()
    assert body["verification_status"] == "failed"
    await local_engine.dispose()
