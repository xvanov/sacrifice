import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.core.payload_guard import (
    PayloadTooDeepError,
    PayloadTooLargeError,
    validate_json_payload,
)
from app.goal_types import registry as goal_type_registry
from app.goal_types.base import ProofTypeMismatch
from app.models.goal import Goal
from app.models.proof import ProofSubmission
from app.models.user import User
from app.schemas.goal import GoalCreate, GoalUpdate
from app.schemas.proof import ProofSubmissionCreate
from app.services.audit import create_audit_event
from app.services.goal import (
    create_goal,
    delete_goal,
    get_goal_by_id,
    get_goal_criteria,
    get_user_goals,
    update_goal,
)
from app.services.notification import create_notification


def _proof_upload_dir() -> Path:
    return Path(settings.media_dir) / "proofs"


router = APIRouter(prefix="/api/goals", tags=["goals"])

goal_types_router = APIRouter(tags=["goal_types"])

# Status transitions a USER may drive through PUT /api/goals/{id}. Everything
# else (pending_review, verified, failed, payment_failed) is system-only,
# written by the verification/deadline/payment workers. Keeping this tight is
# the accountability guarantee: once a goal is active, the owner cannot
# self-complete or self-escape it.
_USER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active", "cancelled"},
    "awaiting_goal_type": {"active", "cancelled"},
}

# Only goals in these statuses may accept a proof submission through
# POST /api/goals/{id}/submit-proof. Once a goal has moved past "active"
# (into pending_review, verified, failed, etc.) the submission window is
# closed — the goal is either already under review or already resolved.
_PROOF_ALLOWED_STATUSES: frozenset[str] = frozenset({"active"})


@goal_types_router.get("/api/goal-types")
async def list_goal_types(
    current_user: User = Depends(get_current_user),
):
    names = goal_type_registry.list_types()
    result = []
    for name in names:
        gt = goal_type_registry.get_type(name)
        result.append(
            {
                "name": gt.name,
                "description": gt.description,
                "sample_prompts": gt.sample_prompts,
                "criteria_schema": gt.criteria_schema,
            }
        )
    return {"goal_types": result}


async def _build_goal_response(db, goal):
    criteria = await get_goal_criteria(db, goal.id)
    return {
        "id": str(goal.id),
        "title": goal.title,
        "description": goal.description,
        "goal_type": goal.goal_type,
        "pledge_amount": goal.pledge_amount,
        "currency": goal.currency,
        "deadline": goal.deadline.isoformat(),
        "timezone": goal.timezone,
        "recurrence": goal.recurrence,
        "status": goal.status,
        "charity_id": goal.charity_id,
        "awaiting_direction_id": goal.awaiting_direction_id,
        "criteria": {
            "criteria_type": criteria.criteria_type,
            "criteria_data": criteria.criteria_data,
        }
        if criteria
        else None,
        "created_at": goal.created_at.isoformat(),
        "updated_at": goal.updated_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_goal_endpoint(
    body: GoalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    goal = await create_goal(db, current_user.id, body)
    await create_notification(
        db,
        user_id=current_user.id,
        notification_type="goal_created",
        title=f"Goal Created: {goal.title}",
        body=f"Your goal '{goal.title}' with a pledge of ${goal.pledge_amount / 100:.2f} has been created.",
        goal_id=goal.id,
    )
    return await _build_goal_response(db, goal)


@router.get("")
async def list_goals(
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    goals = await get_user_goals(db, current_user.id, status_filter)
    results = []
    for g in goals:
        results.append(await _build_goal_response(db, g))
    return results


@router.get("/{goal_id}")
async def get_goal(
    goal_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    goal = await get_goal_by_id(db, goal_id)
    if not goal or str(goal.user_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return await _build_goal_response(db, goal)


@router.put("/{goal_id}")
async def update_goal_endpoint(
    goal_id: str,
    body: GoalUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    goal = await get_goal_by_id(db, goal_id)
    if not goal or str(goal.user_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Accountability guard. The resolution statuses — pending_review, verified,
    # failed, payment_failed — are driven ONLY by the verification/deadline/
    # payment workers (which write goal.status directly). A user must never be
    # able to move their own goal into them via this endpoint: doing so let an
    # owner walk active→pending_review→verified with no proof, or active→
    # cancelled, and escape the pledge entirely. Users may only activate or
    # cancel a goal that has not started yet.
    if body.status is not None and body.status != goal.status:
        user_allowed = _USER_STATUS_TRANSITIONS.get(goal.status, set())
        if body.status not in user_allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Users cannot move a goal from '{goal.status}' to "
                    f"'{body.status}'. Active goals are resolved only by "
                    f"verified proof or the deadline, not by request."
                ),
            )

    if body.status is None:
        if goal.status not in {"draft", "active"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot edit goal in status '{goal.status}'",
            )
    old_status_before = goal.status
    new_status = body.status

    try:
        updated = await update_goal(db, goal, body)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if new_status and new_status != old_status_before:
        if new_status == "verified":
            await create_notification(
                db,
                user_id=current_user.id,
                notification_type="goal_completed",
                title=f"Goal Completed: {updated.title}",
                body=f"Your goal '{updated.title}' has been completed successfully!",
                goal_id=updated.id,
            )
        elif new_status == "failed":
            await create_notification(
                db,
                user_id=current_user.id,
                notification_type="goal_failed",
                title=f"Goal Failed: {updated.title}",
                body=f"Your goal '{updated.title}' has failed. Your pledge of ${updated.pledge_amount / 100:.2f} will be charged and donated to your selected charity.",
                goal_id=updated.id,
            )

    return await _build_goal_response(db, updated)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal_endpoint(
    goal_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    goal = await get_goal_by_id(db, goal_id)
    if not goal or str(goal.user_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if goal.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only draft goals can be deleted",
        )
    await delete_goal(db, goal)


async def _prepare_goal_type_submission(
    *,
    goal: Goal,
    body: ProofSubmissionCreate,
    db: AsyncSession,
    current_user: User,
):
    criteria = await get_goal_criteria(db, goal.id)
    criteria_data = criteria.criteria_data if criteria else {}

    try:
        goal_type = goal_type_registry.get_type(goal.goal_type)
    except KeyError:
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "unsupported_goal_type",
                "goal_type": goal.goal_type,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Proof submission not supported for goal type '{goal.goal_type}'",
        )

    try:
        prepared = goal_type.submit_proof({"_body": body}, criteria_data)
    except ProofTypeMismatch as e:
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "proof_type_mismatch",
                "goal_type": goal.goal_type,
                "error": str(e),
            },
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "schema_validation_failed",
                "goal_type": goal.goal_type,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        )

    return goal_type, prepared, criteria_data


async def _persist_and_dispatch_proof(
    *,
    goal: Goal,
    db: AsyncSession,
    current_user: User,
    goal_type,
    proof_data: dict,
    criteria_data: dict,
    submission_id: uuid.UUID | None = None,
    extra_audit_details: dict | None = None,
):
    """Persist a validated proof submission, audit it, and dispatch verification.

    Shared by the JSON and multipart submit-proof paths. The multipart path used
    to stop after persisting — it never dispatched, so every file-upload proof
    sat in ``verification_status="pending"`` forever. Both paths now run the
    identical tail so a file-upload proof verifies as reliably as a JSON one.

    ``verification_details`` is deliberately left NULL: it belongs to the
    verifier, and echoing the submitted proof back into it leaked encrypted
    github tokens and absolute server paths through the verification-status
    endpoint.
    """
    fields: dict = {
        "goal_id": goal.id,
        "submitted_at": datetime.now(timezone.utc),
        "proof_data": proof_data,
        "verification_status": "pending",
        # Snapshot the criteria actually handed to the verifier so a re-dispatch
        # replays the same call — see the model for why re-deriving is unsafe.
        "dispatch_criteria": criteria_data,
    }
    # The multipart path names the evidence file after the submission id, so it
    # must mint the id before persisting; the JSON path lets the model default it.
    if submission_id is not None:
        fields["id"] = submission_id

    submission = ProofSubmission(**fields)
    db.add(submission)
    await db.commit()
    await db.refresh(submission)

    # Emit audit event for accepted proof validation outcome.
    audit_details = {
        "submission_id": str(submission.id),
        "goal_type": goal.goal_type,
    }
    if extra_audit_details:
        audit_details.update(extra_audit_details)
    await create_audit_event(
        db,
        goal_id=goal.id,
        user_id=current_user.id,
        event_type="proof_accepted",
        details=audit_details,
    )

    # Async/background verification dispatch — guarded so test mocks that
    # don't implement the method don't break the synchronous flow.
    # Dispatch failures are logged but never crash the endpoint: the proof
    # submission is already persisted, and a missing broker (e.g. Redis not
    # available in CI) must not cause a 500 for the user. Failing the request
    # instead would throw away a validated submission (and, for a multipart
    # proof, an upload the user already paid the bandwidth for) because OUR
    # broker is down. What the failure must NOT do is vanish: it is recorded on
    # the submission row so the reconciler beat task
    # (app/workers/reconcile_dispatch.py) can pick the proof back up.
    dispatch = getattr(goal_type, "dispatch_verification", None)
    dispatched = False
    if callable(dispatch):
        submission.dispatch_attempts = (submission.dispatch_attempts or 0) + 1
        try:
            dispatch(
                goal_id=str(goal.id),
                submission_id=str(submission.id),
                proof_data=proof_data,
                criteria_data=criteria_data,
            )
            submission.dispatched_at = datetime.now(timezone.utc)
            dispatched = True
        except Exception as exc:
            from app.goal_types.security_logger import log_verifier_exception

            log_verifier_exception(
                goal_type=goal.goal_type,
                submission_id=str(submission.id),
                exception_type="DispatchError",
                detail="Verifier dispatch failed (broker may be unavailable); proof already persisted",
            )
            # Two durable traces, because a log line is not recoverable:
            # the row (dispatch_attempts >= 1 with dispatched_at IS NULL is
            # exactly what the reconciler selects on), and an audit event so an
            # operator can find these without reading worker logs.
            #
            # The exception TYPE only — its message can carry proof content.
            await create_audit_event(
                db,
                goal_id=goal.id,
                user_id=current_user.id,
                event_type="proof_dispatch_failed",
                details={
                    "submission_id": str(submission.id),
                    "goal_type": goal.goal_type,
                    "exception_type": type(exc).__name__,
                },
            )
        await db.commit()

    if dispatched:
        body = (
            f"Your proof submission for '{goal.title}' has been received "
            f"and is being verified."
        )
    else:
        # Don't promise verification that was never queued.
        body = (
            f"Your proof submission for '{goal.title}' has been received. "
            f"Verification is queued and will start shortly."
        )
    await create_notification(
        db,
        user_id=current_user.id,
        notification_type="proof_received",
        title=f"Proof Received: {goal.title}",
        body=body,
        goal_id=goal.id,
    )

    return {
        "submission_id": str(submission.id),
        "verification_status": "pending",
    }


async def _multipart_proof_submission(
    request: Request,
    goal: Goal,
    db: AsyncSession,
    current_user: User,
):
    """Handle multipart/form-data proof submission with schema enforcement."""
    form = await request.form()
    file = form.get("file")
    if file is None:
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "schema_validation_failed",
                "goal_type": goal.goal_type,
                "error": "No file provided in multipart proof submission",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No file provided in multipart proof submission",
        )
    if not hasattr(file, "filename") or not file.filename:
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "schema_validation_failed",
                "goal_type": goal.goal_type,
                "error": "Uploaded file has no filename",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Uploaded file has no filename",
        )

    proof_metadata_raw = form.get("proof_metadata")
    if proof_metadata_raw is None:
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "schema_validation_failed",
                "goal_type": goal.goal_type,
                "error": "proof_metadata is required and must be a JSON object",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="proof_metadata is required and must be a JSON object",
        )

    raw = (
        proof_metadata_raw
        if isinstance(proof_metadata_raw, str)
        else str(proof_metadata_raw)
    )
    try:
        proof_metadata = json.loads(raw)
    except json.JSONDecodeError:
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "schema_validation_failed",
                "goal_type": goal.goal_type,
                "error": "proof_metadata must be valid JSON",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="proof_metadata must be valid JSON",
        )

    if not isinstance(proof_metadata, dict):
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "schema_validation_failed",
                "goal_type": goal.goal_type,
                "error": "proof_metadata must be a JSON object",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="proof_metadata must be a JSON object",
        )

    # Same abuse guard the JSON path applies. Without this, a deeply nested or
    # oversized payload simply moved to the multipart field to bypass it.
    try:
        validate_json_payload(proof_metadata)
    except PayloadTooLargeError as e:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(e),
        )
    except PayloadTooDeepError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        )

    try:
        body = ProofSubmissionCreate(**proof_metadata)
    except Exception:
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "schema_validation_failed",
                "goal_type": goal.goal_type,
                "error": "proof_metadata must match ProofSubmissionCreate",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="proof_metadata must match ProofSubmissionCreate",
        )

    goal_type, prepared, base_criteria_data = await _prepare_goal_type_submission(
        goal=goal,
        body=body,
        db=db,
        current_user=current_user,
    )

    # Size cap, enforced before the spooled upload is pulled into memory when
    # the multipart parser reported a size, and again on the actual bytes.
    # POST /api/uploads/video already caps at the same setting; this path did not.
    max_bytes = settings.max_upload_size_bytes
    declared_size = getattr(file, "size", None)
    if isinstance(declared_size, int) and declared_size > max_bytes:
        await _reject_oversize_proof_file(
            db=db, goal=goal, current_user=current_user, max_bytes=max_bytes
        )

    content = await file.read()
    if len(content) > max_bytes:
        await _reject_oversize_proof_file(
            db=db, goal=goal, current_user=current_user, max_bytes=max_bytes
        )

    submission_id = uuid.uuid4()

    file_path = await _save_proof_file_bytes(
        submission_id=submission_id,
        original_filename=file.filename,
        content=content,
    )
    evidence_file = {
        "file_path": str(file_path),
        "original_filename": file.filename,
        "mime_type": file.content_type or "application/octet-stream",
        "size_bytes": len(content),
    }

    proof_data = dict(prepared.get("proof_data", {}))
    proof_data["evidence_file"] = evidence_file

    # Goal types may refine the criteria from the submitted body (api_endpoint
    # overrides url/method, github_repo adds the encrypted token). The multipart
    # path used to drop `prepared["criteria_data"]` on the floor, so those
    # refinements never reached the verifier.
    criteria_data = prepared.get("criteria_data", base_criteria_data)

    return await _persist_and_dispatch_proof(
        goal=goal,
        db=db,
        current_user=current_user,
        goal_type=goal_type,
        proof_data=proof_data,
        criteria_data=criteria_data,
        submission_id=submission_id,
        extra_audit_details={"payload_type": "multipart"},
    )


async def _reject_oversize_proof_file(
    *,
    db: AsyncSession,
    goal: Goal,
    current_user: User,
    max_bytes: int,
) -> None:
    """Audit and raise 413 for a proof file over ``max_upload_size_bytes``."""
    await create_audit_event(
        db,
        goal_id=goal.id,
        user_id=current_user.id,
        event_type="proof_rejected",
        details={
            "reason": "file_too_large",
            "goal_type": goal.goal_type,
            "max_upload_size_bytes": max_bytes,
        },
    )
    raise HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail="File exceeds configured max size",
    )


def _safe_upload_extension(original_filename: str) -> str:
    """Return a conservative on-disk extension for a client-supplied filename.

    The filename is attacker-controlled and only ever used to name the stored
    file, so anything that is not a short alphanumeric suffix becomes ``.bin``.
    """
    ext = os.path.splitext(original_filename)[1]
    if not ext or len(ext) > 12:
        return ".bin"
    if not ext.startswith(".") or not ext[1:].isalnum():
        return ".bin"
    return ext.lower()


async def _save_proof_file_bytes(
    submission_id: uuid.UUID,
    original_filename: str,
    content: bytes,
) -> Path:
    """Save uploaded proof bytes to disk and return the absolute path."""
    import asyncio

    upload_dir = _proof_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = _safe_upload_extension(original_filename)
    dest = upload_dir / f"{submission_id}{ext}"
    await asyncio.to_thread(dest.write_bytes, content)
    return dest


@router.post("/{goal_id}/submit-proof", status_code=status.HTTP_202_ACCEPTED)
async def submit_proof(
    goal_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    goal = await get_goal_by_id(db, goal_id)
    if not goal or str(goal.user_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if goal.status not in _PROOF_ALLOWED_STATUSES:
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "illegal_transition",
                "goal_status": goal.status,
                "allowed_statuses": sorted(_PROOF_ALLOWED_STATUSES),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot submit proof for goal in status '{goal.status}'. "
                f"Proof is only accepted for goals in: "
                f"{', '.join(sorted(_PROOF_ALLOWED_STATUSES))}."
            ),
        )

    # ── Multipart path: non-JSON proof (file upload) ──────────────────
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        return await _multipart_proof_submission(
            request=request,
            goal=goal,
            db=db,
            current_user=current_user,
        )

    # ── JSON path: payload guard then parse ────────────────────────
    try:
        body_data = await request.json()
    except Exception:
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "schema_validation_failed",
                "goal_type": goal.goal_type,
                "error": "Request body must be valid JSON",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Request body must be valid JSON",
        )

    # Abuse guard: check size and depth before parsing into pydantic model.
    try:
        validate_json_payload(body_data)
    except PayloadTooLargeError as e:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(e),
        )
    except PayloadTooDeepError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        )

    try:
        body = ProofSubmissionCreate(**body_data)
    except Exception:
        await create_audit_event(
            db,
            goal_id=goal.id,
            user_id=current_user.id,
            event_type="proof_rejected",
            details={
                "reason": "schema_validation_failed",
                "goal_type": goal.goal_type,
                "error": "Request body must be valid JSON matching ProofSubmissionCreate",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Request body must be valid JSON matching ProofSubmissionCreate",
        )

    goal_type, prepared, base_criteria_data = await _prepare_goal_type_submission(
        goal=goal,
        body=body,
        db=db,
        current_user=current_user,
    )

    proof_data = prepared.get("proof_data", {})
    criteria_data = prepared.get("criteria_data", base_criteria_data)

    return await _persist_and_dispatch_proof(
        goal=goal,
        db=db,
        current_user=current_user,
        goal_type=goal_type,
        proof_data=proof_data,
        criteria_data=criteria_data,
    )


@router.get("/{goal_id}/verification-status")
async def get_verification_status(
    goal_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    goal = await get_goal_by_id(db, goal_id)
    if not goal or str(goal.user_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    result = await db.execute(
        select(ProofSubmission)
        .where(ProofSubmission.goal_id == goal.id)
        .order_by(ProofSubmission.submitted_at.desc())
        .limit(1)
    )
    submission = result.scalar_one_or_none()
    if not submission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No proof submission found for this goal",
        )

    return {
        "submission_id": str(submission.id),
        "verification_status": submission.verification_status,
        "verification_details": submission.verification_details,
    }
