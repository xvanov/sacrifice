import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.goal_types import registry as goal_type_registry
from app.goal_types.base import ProofTypeMismatch
from app.models.goal import Goal, GoalCriteria
from app.models.proof import ProofSubmission
from app.models.user import User
from app.schemas.goal import GoalCreate, GoalUpdate
from app.schemas.proof import ProofSubmissionCreate
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


@goal_types_router.get("/api/goal-types")
async def list_goal_types(
    current_user: User = Depends(get_current_user),
):
    names = goal_type_registry.list_types()
    result = []
    for name in names:
        gt = goal_type_registry.get_type(name)
        result.append({
            "name": gt.name,
            "description": gt.description,
            "sample_prompts": gt.sample_prompts,
            "criteria_schema": gt.criteria_schema,
        })
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
        } if criteria else None,
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


async def _multipart_proof_submission(
    request: Request,
    goal_id: str,
    goal,
    db: AsyncSession,
    current_user: User,
):
    """Handle multipart/form-data proof submission.

    Reads the form, validates the file is present, saves it to disk, and
    persists a ProofSubmission row with file metadata stored in proof_data.
    """
    form = await request.form()
    file = form.get("file")
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No file provided in multipart proof submission",
        )
    if not hasattr(file, "filename") or not file.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file has no filename",
        )

    proof_metadata_raw = form.get("proof_metadata")
    proof_metadata = None
    if proof_metadata_raw is not None:
        raw = proof_metadata_raw if isinstance(proof_metadata_raw, str) else str(proof_metadata_raw)
        try:
            proof_metadata = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="proof_metadata must be valid JSON",
            )

    content = await file.read()
    submission_id = uuid.uuid4()

    file_path = await _save_proof_file_bytes(
        submission_id=submission_id,
        original_filename=file.filename,
        content=content,
    )

    proof_data = {
        "type": "file_upload",
        "file_path": str(file_path),
        "original_filename": file.filename,
        "mime_type": file.content_type or "application/octet-stream",
        "size_bytes": len(content),
        "metadata": proof_metadata,
    }

    submission = ProofSubmission(
        id=submission_id,
        goal_id=goal.id,
        submitted_at=datetime.now(timezone.utc),
        proof_data=proof_data,
        verification_status="pending",
        # Store a copy of file metadata in verification_details so it's
        # visible via GET /verification-status without duplicating columns.
        verification_details=proof_data,
    )
    db.add(submission)
    await db.commit()
    await db.refresh(submission)

    await create_notification(
        db,
        user_id=current_user.id,
        notification_type="proof_received",
        title=f"Proof Received: {goal.title}",
        body=f"Your proof submission for '{goal.title}' has been received and is being verified.",
        goal_id=goal.id,
    )

    return {
        "submission_id": str(submission.id),
        "verification_status": "pending",
    }


async def _save_proof_file_bytes(
    submission_id: uuid.UUID,
    original_filename: str,
    content: bytes,
) -> Path:
    """Save uploaded proof bytes to disk and return the absolute path."""
    import asyncio

    upload_dir = _proof_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(original_filename)[1] or ".bin"
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

    if goal.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot submit proof for goal in status '{goal.status}'",
        )

    # ── Multipart path: non-JSON proof (file upload) ──────────────────
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        return await _multipart_proof_submission(
            request=request,
            goal_id=goal_id,
            goal=goal,
            db=db,
            current_user=current_user,
        )

    # ── JSON path: existing behavior ──────────────────────────────────
    try:
        body_data = await request.json()
        body = ProofSubmissionCreate(**body_data)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Request body must be valid JSON matching ProofSubmissionCreate",
        )

    criteria = await get_goal_criteria(db, goal.id)
    criteria_data = criteria.criteria_data if criteria else {}

    try:
        goal_type = goal_type_registry.get_type(goal.goal_type)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Proof submission not supported for goal type '{goal.goal_type}'",
        )

    # Validate + extract the proof via the goal type's own submit_proof()
    # contract. This is where per-type rules live: reject proof shaped for a
    # different goal type (400), reject a malformed/missing field for THIS
    # type (422), and transform the body into the canonical proof_data the
    # verifier expects (e.g. youtube_url → video_id). Skipping this — calling
    # verify() on the raw body — was the bug that let mismatched/malformed
    # proofs through as 202 and left YouTube's video_id extraction dead code.
    try:
        prepared = goal_type.submit_proof({"_body": body}, criteria_data)
    except ProofTypeMismatch as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )

    proof_data = prepared.get("proof_data", {})
    criteria_data = prepared.get("criteria_data", criteria_data)

    submission = ProofSubmission(
        goal_id=goal.id,
        submitted_at=datetime.now(timezone.utc),
        proof_data=proof_data,
        verification_status="pending",
    )
    db.add(submission)
    await db.commit()
    await db.refresh(submission)

    # Async/background verification dispatch — guarded so test mocks that
    # don't implement the method don't break the synchronous flow.
    dispatch = getattr(goal_type, "dispatch_verification", None)
    if callable(dispatch):
        try:
            dispatch(
                goal_id=str(goal.id),
                submission_id=str(submission.id),
                proof_data=proof_data,
                criteria_data=criteria_data,
            )
        except Exception as exc:
            from app.goal_types.security_logger import log_verifier_exception

            log_verifier_exception(
                goal_type=goal.goal_type,
                submission_id=str(submission.id),
                exception_type=type(exc).__name__,
                detail="Verifier dispatch raised an exception",
            )
            raise

    await create_notification(
        db,
        user_id=current_user.id,
        notification_type="proof_received",
        title=f"Proof Received: {goal.title}",
        body=f"Your proof submission for '{goal.title}' has been received and is being verified.",
        goal_id=goal.id,
    )

    return {
        "submission_id": str(submission.id),
        "verification_status": "pending",
    }


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
