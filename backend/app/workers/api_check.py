import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.net_safety import UnsafeUrlError, assert_public_url
from app.database import async_session
from app.models.goal import Goal
from app.models.proof import ProofSubmission
from app.services.notification import notify_goal_resolution


def _safe_headers(headers) -> dict:
    result = {}
    try:
        for k, v in headers.items():
            result[str(k)] = str(v)
    except Exception:
        return {}
    return result


def _validate_json_schema(instance: dict, schema: dict) -> tuple[bool, str]:
    try:
        _validate_schema(instance, schema)
        return True, ""
    except ValueError as e:
        return False, str(e)


def _validate_schema(instance, schema, path=""):
    if not isinstance(schema, dict):
        return

    schema_type = schema.get("type")

    if schema_type == "object":
        if not isinstance(instance, dict):
            raise ValueError(f"{path}: expected object, got {type(instance).__name__}")
        required = schema.get("required", [])
        for field in required:
            if field not in instance:
                raise ValueError(f"{path}: missing required field '{field}'")
        properties = schema.get("properties", {})
        for key, prop_schema in properties.items():
            if key in instance:
                _validate_schema(instance[key], prop_schema, f"{path}.{key}" if path else key)

    elif schema_type == "array":
        if not isinstance(instance, list):
            raise ValueError(f"{path}: expected array, got {type(instance).__name__}")
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(instance):
                _validate_schema(item, items_schema, f"{path}[{i}]")

    elif schema_type == "string":
        if not isinstance(instance, str):
            raise ValueError(f"{path}: expected string, got {type(instance).__name__}")

    elif schema_type == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            raise ValueError(f"{path}: expected integer, got {type(instance).__name__}")

    elif schema_type == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            raise ValueError(f"{path}: expected number, got {type(instance).__name__}")

    elif schema_type == "boolean":
        if not isinstance(instance, bool):
            raise ValueError(f"{path}: expected boolean, got {type(instance).__name__}")

    elif schema_type == "null":
        if instance is not None:
            raise ValueError(f"{path}: expected null, got {type(instance).__name__}")


async def verify_api_endpoint(
    proof_data: dict,
    criteria_data: dict,
) -> dict:
    method = criteria_data.get("method", "GET").upper()
    url = criteria_data.get("url", proof_data.get("url", ""))
    expected_status = criteria_data.get("expected_status", 200)
    expected_body_schema = criteria_data.get("expected_body_schema")
    custom_headers = criteria_data.get("headers", {})

    details = {
        "request_url": url,
        "request_method": method,
        "expected_status": expected_status,
    }

    if custom_headers:
        details["request_headers"] = dict(custom_headers)

    failed = False

    # SSRF guard: never let a user-supplied criteria URL reach an internal
    # address (cloud metadata, localhost, RFC1918). A blocked URL is a clean
    # verification failure, not a crash.
    try:
        assert_public_url(url)
    except UnsafeUrlError as e:
        details["error"] = f"URL rejected: {e}"
        details["url_rejected"] = True
        details["status_passed"] = False
        return {"verification_status": "failed", "verification_details": details}

    try:
        # follow_redirects stays False so a public URL can't 30x-redirect into
        # an internal host after the pre-flight check.
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=custom_headers or None,
            )

        actual_status = response.status_code
        details["actual_status"] = actual_status
        details["actual_headers"] = _safe_headers(response.headers)
        details["response_body_preview"] = response.text[:2000]
        details["status_passed"] = actual_status == expected_status

        if actual_status != expected_status:
            details["status_failure_reason"] = (
                f"Expected status {expected_status}, got {actual_status}"
            )
            failed = True

        is_json = False
        content_type = ""
        try:
            content_type = response.headers.get("content-type", "") or ""
        except Exception:
            pass
        if "application/json" in content_type:
            is_json = True
            try:
                body_json = response.json()
                details["response_body_json"] = body_json
                details["is_json"] = True
            except (ValueError, TypeError):
                details["is_json"] = False
                details["json_parse_error"] = "Response is not valid JSON despite JSON content-type"
        else:
            details["is_json"] = False

        if expected_body_schema and is_json and not failed:
            schema_valid, schema_error = _validate_json_schema(
                body_json, expected_body_schema
            )
            details["schema_passed"] = schema_valid
            if not schema_valid:
                details["schema_failure_reason"] = schema_error
                failed = True
        elif expected_body_schema:
            details["schema_passed"] = False

    except httpx.TimeoutException as e:
        details["error"] = f"Request timed out: {e}"
        details["status_passed"] = False
        failed = True
    except httpx.ConnectError as e:
        details["error"] = f"Host unreachable: {e}"
        details["status_passed"] = False
        failed = True
    except httpx.HTTPError as e:
        details["error"] = f"HTTP error: {e}"
        details["status_passed"] = False
        failed = True
    except TimeoutError as e:
        details["error"] = f"Request timed out: {e}"
        details["status_passed"] = False
        failed = True
    except ConnectionError as e:
        details["error"] = f"Host unreachable: {e}"
        details["status_passed"] = False
        failed = True
    except Exception as e:
        details["error"] = f"Unexpected error: {e}"
        details["status_passed"] = False
        failed = True

    status = "failed" if failed else "verified"
    return {
        "verification_status": status,
        "verification_details": details,
    }


async def _persist_result(
    db: AsyncSession,
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    status: str,
    details: dict,
):
    result = await db.execute(
        select(ProofSubmission).where(ProofSubmission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if submission:
        submission.verification_status = status
        submission.verification_details = details

    result = await db.execute(select(Goal).where(Goal.id == goal_id))
    goal = result.scalar_one_or_none()
    if goal:
        goal.status = status
        # Notify the user their goal was resolved (verified/failed).
        await notify_goal_resolution(db, goal, status)

    await db.commit()


async def run_api_verification(
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    proof_data: dict,
    criteria_data: dict,
    db: AsyncSession | None = None,
) -> dict:
    result = await verify_api_endpoint(proof_data, criteria_data)

    if db is not None:
        await _persist_result(
            db, goal_id, submission_id,
            result["verification_status"], result["verification_details"],
        )
    else:
        async with async_session() as session:
            await _persist_result(
                session, goal_id, submission_id,
                result["verification_status"], result["verification_details"],
            )

    return result


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def run_api_verification_task(
    self,
    goal_id_str: str,
    submission_id_str: str,
    proof_data: dict,
    criteria_data: dict,
):
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            run_api_verification(
                goal_id=uuid.UUID(goal_id_str),
                submission_id=uuid.UUID(submission_id_str),
                proof_data=proof_data,
                criteria_data=criteria_data,
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        loop.close()
