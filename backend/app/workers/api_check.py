import uuid

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.net_safety import UnsafeUrlError, assert_public_url
from app.database import async_session
from app.services.fault_attribution import (
    Fault,
    classify_transport_failure,
    internal_error,
)
from app.services.verification_result import (
    FAILED,
    INCONCLUSIVE,
    VERIFIED,
    persist_verification_result,
)


logger = logging.getLogger(__name__)


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
                _validate_schema(
                    instance[key], prop_schema, f"{path}.{key}" if path else key
                )

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
                details["json_parse_error"] = (
                    "Response is not valid JSON despite JSON content-type"
                )
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

    except (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.HTTPError,
        TimeoutError,
        ConnectionError,
    ) as e:
        # No response at all. This is the one genuinely ambiguous case: the URL
        # is the user's, so "it did not answer" is usually the very thing they
        # are being measured on — but if OUR egress is down, every user's
        # endpoint looks dead and we would bill all of them.
        #
        # Settled by probing a host WE name, never by reading the error text: a
        # user could otherwise point the URL at an address they know is dead and
        # have their own failure attributed to us, dodging the pledge.
        fault, reason = classify_transport_failure(target_is_user_supplied=True)
        details["error"] = f"Endpoint did not respond: {e}"
        details["status_passed"] = False
        if fault is Fault.OURS:
            details["inconclusive_detail"] = (
                "We could not make outbound requests when this check ran, so we "
                "could not reach your endpoint. Your pledge has not been charged "
                "and we will retry."
            )
            return {
                "verification_status": INCONCLUSIVE,
                "inconclusive_reason": reason,
                "verification_details": details,
            }
        failed = True
    except Exception as e:
        # Our own bug, not a statement about the user's endpoint.
        _fault, reason = internal_error()
        details["error"] = f"Unexpected error: {e}"
        details["status_passed"] = False
        details["inconclusive_detail"] = (
            "Something went wrong on our side while checking your endpoint. Your "
            "pledge has not been charged."
        )
        logger.exception("api_endpoint verification raised unexpectedly")
        return {
            "verification_status": INCONCLUSIVE,
            "inconclusive_reason": reason,
            "verification_details": details,
        }

    status = FAILED if failed else VERIFIED
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
    inconclusive_reason: str | None = None,
):
    # The reason must travel: the contract requires one for INCONCLUSIVE and
    # rejects one for a verdict.
    await persist_verification_result(
        db,
        goal_id,
        submission_id,
        status,
        details,
        inconclusive_reason=inconclusive_reason,
    )


async def run_api_verification(
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    proof_data: dict,
    criteria_data: dict,
    db: AsyncSession | None = None,
) -> dict:
    result = await verify_api_endpoint(proof_data, criteria_data)
    # Mirrors run_youtube_verification. `_persist_result` accepts the reason and
    # the write REQUIRES one for INCONCLUSIVE, but both call sites here used to
    # pass five positional args and drop it — so every one of our own outages
    # (upstream unreachable / internal error) raised InconclusiveContractError
    # before any write. The task then retried deterministically three times,
    # the submission stayed `pending` with NULL verification_details,
    # `goal_verification_is_blocked` saw nothing to block on, and the deadline
    # sweep failed the goal and charged a real card for our outage.
    reason = result.get("inconclusive_reason")

    if db is not None:
        await _persist_result(
            db,
            goal_id,
            submission_id,
            result["verification_status"],
            result["verification_details"],
            inconclusive_reason=reason,
        )
    else:
        async with async_session() as session:
            await _persist_result(
                session,
                goal_id,
                submission_id,
                result["verification_status"],
                result["verification_details"],
                inconclusive_reason=reason,
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
