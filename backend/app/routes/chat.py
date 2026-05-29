"""Chat routes — goal-type generation and direction management.

D010: request-new-goal-type, generation-status, accept-generated-type,
and iterate-generated-type endpoints.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.chat_session import ChatSession
from app.models.goal import Goal
from app.models.user import User
from app.services.direction_synth import (
    read_direction_state,
    synthesize_direction,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ─── Request / response schemas ──────────────────────────────────────


class RequestNewGoalTypeBody(BaseModel):
    prompt_summary: str
    goal_payload_draft: dict


class RequestNewGoalTypeResponse(BaseModel):
    direction_id: str
    goal_id: str
    status: str


class GenerationStatusResponse(BaseModel):
    direction_id: str
    status: str
    pr_url: str | None = None
    summary: str | None = None


class AcceptGeneratedTypeResponse(BaseModel):
    goal_id: str
    status: str


class IterateGeneratedTypeBody(BaseModel):
    feedback: str


class IterateGeneratedTypeResponse(BaseModel):
    direction_id: str
    previous_direction_id: str
    status: str


# ─── Endpoints ────────────────────────────────────────────────────────


@router.post(
    "/sessions/{session_id}/request-new-goal-type",
    status_code=202,
    response_model=RequestNewGoalTypeResponse,
)
async def request_new_goal_type(
    session_id: str,
    body: RequestNewGoalTypeBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Synthesize a direction from chat context, write to factory
    directions volume, and create a goal in awaiting_goal_type status."""

    # Check for existing in-flight generation for this user
    existing = await db.execute(
        select(Goal).where(
            Goal.user_id == user.id,
            Goal.status == "awaiting_goal_type",
            Goal.awaiting_direction_id.isnot(None),
        )
    )
    existing_goal = existing.scalar_one_or_none()

    if existing_goal is not None:
        return JSONResponse(
            status_code=409,
            content={
                "message": "User already has an in-flight generation",
                "direction_id": existing_goal.awaiting_direction_id,
            },
        )

    # Synthesize the direction
    result: DirectionResult = await synthesize_direction(
        prompt_summary=body.prompt_summary,
        goal_payload_draft=body.goal_payload_draft,
    )

    # Create the goal in awaiting_goal_type status
    goal_data = body.goal_payload_draft

    deadline_str = goal_data.get("deadline", "")
    deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))

    goal = Goal(
        user_id=user.id,
        title=goal_data.get("title", "Untitled Goal"),
        description=goal_data.get("description"),
        deadline=deadline,
        pledge_amount=goal_data.get("pledge_amount", 1000),
        goal_type=goal_data.get("goal_type", "youtube_video"),
        status="awaiting_goal_type",
        awaiting_direction_id=result.direction_id,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)

    # Record chat session for tracking
    chat_session = ChatSession(
        user_id=user.id,
        direction_id=result.direction_id,
        summary=body.prompt_summary,
    )
    db.add(chat_session)
    await db.commit()

    return RequestNewGoalTypeResponse(
        direction_id=result.direction_id,
        goal_id=str(goal.id),
        status="queued",
    )


@router.get(
    "/sessions/{session_id}/generation-status",
    response_model=GenerationStatusResponse,
)
async def get_generation_status(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the direction's state.yaml and return a coarse status."""

    # Find the chat session
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    chat_session = result.scalar_one_or_none()

    if chat_session is None or chat_session.direction_id is None:
        raise HTTPException(
            status_code=404,
            detail="Session has no in-flight generation",
        )

    state = read_direction_state(chat_session.direction_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail="Direction state not found",
        )

    return GenerationStatusResponse(
        direction_id=chat_session.direction_id,
        status=state.get("status", "unknown"),
        pr_url=state.get("pr_url"),
        summary=state.get("summary"),
    )


@router.post(
    "/sessions/{session_id}/accept-generated-type",
    response_model=AcceptGeneratedTypeResponse,
)
async def accept_generated_type(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Transition the goal from awaiting_goal_type to active."""

    # Find the chat session
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    chat_session = result.scalar_one_or_none()

    if chat_session is None or chat_session.direction_id is None:
        raise HTTPException(
            status_code=404,
            detail="Session or pending goal not found",
        )

    # Check generation status
    state = read_direction_state(chat_session.direction_id)
    if state is None or state.get("status") != "pr_merged":
        raise HTTPException(
            status_code=409,
            detail="Generation not yet merged",
        )

    # Find the pending goal
    goal_result = await db.execute(
        select(Goal).where(
            Goal.user_id == user.id,
            Goal.status == "awaiting_goal_type",
            Goal.awaiting_direction_id == chat_session.direction_id,
        )
    )
    goal = goal_result.scalar_one_or_none()

    if goal is None:
        raise HTTPException(
            status_code=404,
            detail="Pending goal not found",
        )

    goal.status = "active"
    goal.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(goal)

    return AcceptGeneratedTypeResponse(
        goal_id=str(goal.id),
        status="active",
    )


@router.post(
    "/sessions/{session_id}/iterate-generated-type",
    status_code=202,
    response_model=IterateGeneratedTypeResponse,
)
async def iterate_generated_type(
    session_id: str,
    body: IterateGeneratedTypeBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """File a follow-up direction that modifies the existing module."""

    if not body.feedback or not body.feedback.strip():
        raise HTTPException(
            status_code=422,
            detail="Feedback must not be empty",
        )

    # Find the chat session
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    chat_session = result.scalar_one_or_none()

    if chat_session is None or chat_session.direction_id is None:
        raise HTTPException(
            status_code=404,
            detail="Session or pending goal not found",
        )

    previous_direction_id = chat_session.direction_id

    # Check the goal hasn't already been accepted
    goal_result = await db.execute(
        select(Goal).where(
            Goal.user_id == user.id,
            Goal.awaiting_direction_id == previous_direction_id,
        )
    )
    goal = goal_result.scalar_one_or_none()

    if goal is None:
        raise HTTPException(
            status_code=404,
            detail="Pending goal not found",
        )

    if goal.status == "active":
        raise HTTPException(
            status_code=409,
            detail="Goal already accepted — cannot iterate after acceptance",
        )

    # Synthesize a new iteration direction
    result: DirectionResult = await synthesize_direction(
        prompt_summary=f"Iteration on {previous_direction_id}: {body.feedback}",
        goal_payload_draft={
            "title": goal.title,
            "description": goal.description or "",
        },
    )

    # Update chat session to point to new direction
    chat_session.direction_id = result.direction_id
    chat_session.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return IterateGeneratedTypeResponse(
        direction_id=result.direction_id,
        previous_direction_id=previous_direction_id,
        status="queued",
    )