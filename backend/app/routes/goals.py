import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_token
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.goal import Goal, GoalCriteria
from app.models.proof import ProofSubmission
from app.models.user import User
from app.schemas.goal import GoalCreate, GoalUpdate
from app.schemas.proof import (
    ApiEndpointProofSubmission,
    ProofSubmissionCreate,
    VerificationStatusResponse,
    YouTubeProofSubmission,
)
from app.services.goal import (
    create_goal,
    delete_goal,
    get_goal_by_id,
    get_goal_criteria,
    get_user_goals,
    update_goal,
)
from app.services.notification import create_notification
from app.services.youtube import extract_video_id
from app.workers.api_check import run_api_verification_task
from app.workers.dev_sandbox import run_dev_sandbox_verification_task
from app.workers.github_repo import run_github_repo_verification_task
from app.workers.youtube import run_youtube_verification_task

router = APIRouter(prefix="/api/goals", tags=["goals"])


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

    if goal.goal_type == "youtube_video":
        if body.url or body.method:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Proof submission type mismatch: goal is 'youtube_video', not 'api_endpoint'",
            )
        if not body.youtube_url:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="youtube_url is required for youtube_video proof submission",
            )
        try:
            YouTubeProofSubmission(youtube_url=body.youtube_url)
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(e.errors()[0]["msg"]) if e.errors() else "Invalid YouTube URL",
            )
        video_id = extract_video_id(body.youtube_url)
        if not video_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Could not extract video ID from URL",
            )

        submission = ProofSubmission(
            goal_id=goal.id,
            submitted_at=datetime.now(timezone.utc),
            proof_data={"video_id": video_id, "url": body.youtube_url},
            verification_status="pending",
        )
        db.add(submission)
        await db.commit()
        await db.refresh(submission)

        run_youtube_verification_task.delay(
            goal_id_str=str(goal.id),
            submission_id_str=str(submission.id),
            proof_data=submission.proof_data,
            criteria_data=criteria_data,
        )

    elif goal.goal_type == "api_endpoint":
        if body.youtube_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Proof submission type mismatch: goal is 'api_endpoint', not 'youtube_video'",
            )
        if not body.url:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="url is required for api_endpoint proof submission",
            )
        try:
            ApiEndpointProofSubmission(
                url=body.url,
                method=body.method or "GET",
                headers=body.headers,
                expected_status=body.expected_status,
                expected_body_schema=body.expected_body_schema,
            )
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(e.errors()[0]["msg"]) if e.errors() else "Invalid API endpoint proof data",
            )

        overridden_criteria = dict(criteria_data)
        overridden_criteria["url"] = body.url
        overridden_criteria["method"] = body.method or "GET"
        if body.headers is not None:
            overridden_criteria["headers"] = body.headers
        if body.expected_status is not None:
            overridden_criteria["expected_status"] = body.expected_status
        if body.expected_body_schema is not None:
            overridden_criteria["expected_body_schema"] = body.expected_body_schema

        proof_data = {
            "url": body.url,
            "method": body.method or "GET",
            "headers": body.headers,
            "expected_status": body.expected_status,
            "expected_body_schema": body.expected_body_schema,
        }

        submission = ProofSubmission(
            goal_id=goal.id,
            submitted_at=datetime.now(timezone.utc),
            proof_data=proof_data,
            verification_status="pending",
        )
        db.add(submission)
        await db.commit()
        await db.refresh(submission)

        run_api_verification_task.delay(
            goal_id_str=str(goal.id),
            submission_id_str=str(submission.id),
            proof_data=submission.proof_data,
            criteria_data=overridden_criteria,
        )

    elif goal.goal_type == "dev_sandbox":
        if not body.repo_url:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="repo_url is required for dev_sandbox proof submission",
            )

        overridden_criteria = dict(criteria_data)
        overridden_criteria["repo_url"] = body.repo_url
        overridden_criteria["branch"] = body.branch or criteria_data.get("branch", "main")
        overridden_criteria["test_command"] = body.test_command or criteria_data.get("test_command", "python -m pytest -v")
        if body.language:
            overridden_criteria["language"] = body.language
        if body.env_vars is not None:
            overridden_criteria["env_vars"] = body.env_vars

        proof_data = {
            "repo_url": body.repo_url,
            "branch": body.branch or "main",
            "test_command": body.test_command or "python -m pytest -v",
            "language": body.language,
            "env_vars": body.env_vars,
        }

        submission = ProofSubmission(
            goal_id=goal.id,
            submitted_at=datetime.now(timezone.utc),
            proof_data=proof_data,
            verification_status="pending",
        )
        db.add(submission)
        await db.commit()
        await db.refresh(submission)

        run_dev_sandbox_verification_task.delay(
            goal_id_str=str(goal.id),
            submission_id_str=str(submission.id),
            proof_data=submission.proof_data,
            criteria_data=overridden_criteria,
        )

    elif goal.goal_type == "github_repo":
        if not body.repo_url:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="repo_url is required for github_repo proof submission",
            )

        encrypted_token = encrypt_token(body.github_token) if body.github_token else None
        proof_data = {
            "repo_url": body.repo_url,
            "branch": body.branch or "main",
            "github_token": encrypted_token,
        }

        overridden_criteria = dict(criteria_data)
        overridden_criteria["repo_url"] = body.repo_url
        overridden_criteria["branch"] = body.branch or criteria_data.get("branch", "main")
        if body.github_token:
            overridden_criteria["github_token"] = encrypted_token

        submission = ProofSubmission(
            goal_id=goal.id,
            submitted_at=datetime.now(timezone.utc),
            proof_data=proof_data,
            verification_status="pending",
        )
        db.add(submission)
        await db.commit()
        await db.refresh(submission)

        run_github_repo_verification_task.delay(
            goal_id_str=str(goal.id),
            submission_id_str=str(submission.id),
            proof_data=submission.proof_data,
            criteria_data=overridden_criteria,
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Proof submission not supported for goal type '{goal.goal_type}'",
        )

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
