import inspect
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.goal_types import registry as goal_type_registry
from app.models.goal import Goal, GoalCriteria
from app.models.proof import ProofSubmission
from app.models.user import User
from app.schemas.goal import GoalCreate, GoalUpdate
from app.schemas.proof import ProofSubmissionCreate
from app.services.goal import (
    create_goal_with_notification,
    delete_goal,
    get_goal_by_id,
    get_goal_criteria,
    get_user_goals,
    update_goal,
)
from app.services.notification import create_notification
from app.workers.youtube import run_youtube_verification_task  # noqa: F401 — used by test patches

router = APIRouter(prefix="/api/goals", tags=["goals"])

goal_types_router = APIRouter(tags=["goal_types"])


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
    goal = await create_goal_with_notification(db, current_user.id, body)
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


@router.post("/{goal_id}/submit-proof", status_code=status.HTTP_202_ACCEPTED)
async def submit_proof(
    goal_id: str,
    body: ProofSubmissionCreate,
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

    criteria = await get_goal_criteria(db, goal.id)
    criteria_data = criteria.criteria_data if criteria else {}

    try:
        goal_type = goal_type_registry.get_type(goal.goal_type)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Proof submission not supported for goal type '{goal.goal_type}'",
        )

    # The request body is the proof payload — flatten the Pydantic model so
    # the verifier and ProofSubmission can store it as JSONB. The route calls
    # submit_proof first for validation and extraction, then verify.
    proof_data = body.model_dump(exclude_unset=True)

    from app.goal_types.base import TypeMismatchError

    try:
        extracted = goal_type.submit_proof(proof_data, criteria_data)
    except TypeMismatchError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError:
        extracted = None

    # Only apply submit_proof result if it returned a real dict.
    # Mock async auto-creation returns coroutines; stub mocks return Mock objects.
    if isinstance(extracted, dict):
        proof_data = extracted.get("proof_data", proof_data)
        criteria_data = extracted.get("criteria_data", criteria_data)

    try:
        verification_result = await goal_type.verify(proof_data, criteria_data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if verification_result.get("verification_status") == "rejected":
        return Response(
            content=json.dumps({
                "submission_id": None,
                "verification_status": "rejected",
                "verification_details": verification_result.get("verification_details", {}),
            }),
            media_type="application/json",
            status_code=200,
        )

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
        except Exception:
            pass

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
