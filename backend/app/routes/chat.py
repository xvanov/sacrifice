"""Chat routes for goal-type generation flow.

Endpoints:
- POST /api/chat/sessions/{session_id}/request-new-goal-type
- GET /api/chat/sessions/{session_id}/generation-status
- POST /api/chat/sessions/{session_id}/accept-generated-type
- POST /api/chat/sessions/{session_id}/iterate-generated-type
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.chat_spend import ChatSpendLedger
from app.models.goal import Goal
from app.models.notification import Notification as NotificationModel
from app.models.user import User
from app.schemas.goal import GoalCreate
from app.services.direction_synth import (
    DirectionSynthesisError,
    allocate_direction_id,
    read_direction_state,
    synthesize_direction,
    write_direction,
)
from app.services.goal import create_goal
from app.services.notification import create_notification

router = APIRouter(prefix="/api/chat", tags=["chat"])


class GoalPayloadDraft(BaseModel):
    """Goal fields from chat client — no goal_type or criteria since those
    are determined by the synthesis process."""
    title: str
    description: str | None = None
    deadline: datetime
    pledge_amount: int
    currency: str = "usd"
    timezone: str = "UTC"
    recurrence: str = "none"
    charity_id: str | None = None

    @field_validator("pledge_amount")
    @classmethod
    def pledge_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("pledge_amount must be positive")
        return v

    @field_validator("recurrence")
    @classmethod
    def validate_recurrence(cls, v):
        allowed = {"none", "daily", "weekly", "monthly"}
        if v not in allowed:
            raise ValueError(f"recurrence must be one of {allowed}")
        return v


class RequestNewGoalTypeBody(BaseModel):
    prompt_summary: str
    goal_payload_draft: GoalPayloadDraft


class GenerationStatusResponse(BaseModel):
    direction_id: str
    status: str  # queued | in_progress | pr_open | pr_merged | rejected
    pr_url: str | None = None
    summary: str | None = None


class AcceptGeneratedTypeResponse(BaseModel):
    goal_id: str
    status: str


class IterateGeneratedTypeBody(BaseModel):
    feedback: str

    @field_validator("feedback")
    @classmethod
    def feedback_must_not_be_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("feedback must not be empty or whitespace")
        return v.strip()


# ── Spend tracking helpers ────────────────────────────────────────────


async def _check_spend_cap(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Check if user has exceeded daily spend cap. Returns True if OK."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.sum(ChatSpendLedger.cost_millicents)).where(
            ChatSpendLedger.user_id == user_id,
            ChatSpendLedger.call_timestamp >= today_start,
        )
    )
    total = result.scalar() or 0
    return total < settings.chat_spend_cap_millicents


async def _record_spend(
    db: AsyncSession,
    user_id: uuid.UUID,
    cost_millicents: int,
    model: str,
    description: str,
) -> None:
    entry = ChatSpendLedger(
        user_id=user_id,
        cost_millicents=cost_millicents,
        model=model,
        call_description=description,
    )
    db.add(entry)
    await db.commit()


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/request-new-goal-type", status_code=202)
async def request_new_goal_type(
    session_id: str,
    body: RequestNewGoalTypeBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Synthesize a direction, write it to disk, and create goal in awaiting_goal_type."""
    # Check spend cap
    if not await _check_spend_cap(db, current_user.id):
        raise HTTPException(
            status_code=429,
            detail="You've hit today's AI budget. Try again tomorrow, or reach out if this is wrong.",
        )

    # Check for in-flight generation
    result = await db.execute(
        select(Goal).where(
            Goal.user_id == current_user.id,
            Goal.status == "awaiting_goal_type",
        )
    )
    existing = result.scalars().all()
    if existing:
        direction_id = existing[0].awaiting_direction_id
        raise HTTPException(
            status_code=409,
            detail=f"You're already building '{direction_id}'. Want to add to that one instead?",
        )

    # Synthesize direction
    model = settings.direction_synth_model or settings.azure_foundry_deployment
    try:
        synthesis = await synthesize_direction(body.prompt_summary)
    except DirectionSynthesisError as e:
        # Record spend even on failure (the LLM call was made)
        await _record_spend(db, current_user.id, 0, model, f"synthesis_failed: {body.prompt_summary[:100]}")
        raise HTTPException(
            status_code=422,
            detail=f"I couldn't pin down what you want — try rephrasing with more concrete success criteria. ({e})",
        )

    slug = synthesis["slug"]
    direction_id = await allocate_direction_id(slug)

    # Write direction to disk
    await write_direction(synthesis, direction_id)

    # Create goal in awaiting_goal_type — use a placeholder goal_type/criteria
    # since the real module doesn't exist yet. The goal_type will be updated
    # when the user accepts the generated type.
    goal_data = GoalCreate(
        title=body.goal_payload_draft.title,
        description=body.goal_payload_draft.description,
        deadline=body.goal_payload_draft.deadline,
        pledge_amount=body.goal_payload_draft.pledge_amount,
        goal_type="youtube_video",  # placeholder, replaced on accept
        criteria={"placeholder": True},
        charity_id=body.goal_payload_draft.charity_id,
        timezone=body.goal_payload_draft.timezone,
        recurrence=body.goal_payload_draft.recurrence,
        currency=body.goal_payload_draft.currency,
    )
    goal = await create_goal(
        db=db,
        user_id=current_user.id,
        data=goal_data,
        status="awaiting_goal_type",
        awaiting_direction_id=direction_id,
    )

    # Record spend (estimated at 10 millicents per synthesis call)
    await _record_spend(db, current_user.id, 10, model, f"direction_synthesis: {direction_id}")

    return {
        "direction_id": direction_id,
        "goal_id": str(goal.id),
        "status": "queued",
    }


@router.get("/sessions/{session_id}/generation-status")
async def generation_status(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read direction state.yaml and return coarse status."""
    # Find the user's awaiting goal
    result = await db.execute(
        select(Goal).where(
            Goal.user_id == current_user.id,
            Goal.status == "awaiting_goal_type",
        )
    )
    goal = result.scalar_one_or_none()
    if not goal or not goal.awaiting_direction_id:
        raise HTTPException(
            status_code=404,
            detail="No in-flight generation found for this session.",
        )

    state = await read_direction_state(goal.awaiting_direction_id)
    if not state:
        raise HTTPException(
            status_code=404,
            detail="Direction state not found.",
        )

    # If pr_merged, fire notification
    if state.get("status") == "pr_merged":
        notif_check = await db.execute(
            select(NotificationModel).where(
                NotificationModel.goal_id == goal.id,
                NotificationModel.type == "goal_type_ready",
            )
        )
        if not notif_check.scalar_one_or_none():
            await create_notification(
                db=db,
                user_id=current_user.id,
                notification_type="goal_type_ready",
                title="Goal Type Ready",
                body=f"Your {goal.awaiting_direction_id} goal type is ready. Accept and activate your goal?",
                goal_id=goal.id,
            )

    return GenerationStatusResponse(
        direction_id=goal.awaiting_direction_id,
        status=state.get("status", "queued"),
        pr_url=state.get("pr_url"),
        summary=state.get("summary"),
    )


@router.post("/sessions/{session_id}/accept-generated-type")
async def accept_generated_type(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Transition the pending goal from awaiting_goal_type to active."""
    result = await db.execute(
        select(Goal).where(
            Goal.user_id == current_user.id,
            Goal.status == "awaiting_goal_type",
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="No pending goal found.")

    # Verify generation is merged
    if goal.awaiting_direction_id:
        state = await read_direction_state(goal.awaiting_direction_id)
        if not state or state.get("status") != "pr_merged":
            raise HTTPException(
                status_code=409,
                detail="Generation is not yet merged. Wait for the PR to merge before accepting.",
            )

    goal.status = "active"
    # Update goal_type from the direction slug (module name)
    if goal.awaiting_direction_id:
        parts = goal.awaiting_direction_id.split("-", 1)
        if len(parts) > 1:
            goal.goal_type = parts[1]
    await db.commit()
    await db.refresh(goal)

    return AcceptGeneratedTypeResponse(goal_id=str(goal.id), status=goal.status)


@router.post("/sessions/{session_id}/iterate-generated-type", status_code=202)
async def iterate_generated_type(
    session_id: str,
    body: IterateGeneratedTypeBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """File a follow-up direction that modifies the existing module."""
    # Check spend cap
    if not await _check_spend_cap(db, current_user.id):
        raise HTTPException(
            status_code=429,
            detail="You've hit today's AI budget. Try again tomorrow, or reach out if this is wrong.",
        )

    # Find the pending goal
    result = await db.execute(
        select(Goal).where(
            Goal.user_id == current_user.id,
            Goal.status == "awaiting_goal_type",
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="No pending goal found.")

    # Check not already accepted
    if goal.status != "awaiting_goal_type":
        raise HTTPException(
            status_code=409,
            detail="Goal has already been accepted. Cannot iterate after acceptance.",
        )

    previous_direction_id = goal.awaiting_direction_id
    if not previous_direction_id:
        raise HTTPException(status_code=404, detail="No direction linked to pending goal.")

    # Synthesize iteration direction
    feedback = body.feedback
    slug_parts = previous_direction_id.split("-", 1)
    base_slug = slug_parts[1] if len(slug_parts) > 1 else slug_parts[0]
    # Create a feedback-derived slug
    feedback_words = feedback.lower().split()[:4]
    feedback_slug = "-".join(w.strip(",.!?()[]{}\"'") for w in feedback_words if len(w.strip(",.!?()[]{}\"'")) > 2)
    iterate_slug = f"{base_slug}-{feedback_slug}" if feedback_slug else f"{base_slug}-iteration"

    model = settings.direction_synth_model or settings.azure_foundry_deployment

    try:
        synthesis = await synthesize_direction(
            f"Iteration on {previous_direction_id}: {feedback}",
        )
    except DirectionSynthesisError as e:
        await _record_spend(db, current_user.id, 0, model, f"iterate_synthesis_failed: {previous_direction_id}")
        raise HTTPException(
            status_code=422,
            detail=f"I couldn't pin down what you want — try rephrasing with more concrete success criteria. ({e})",
        )

    # Build direction.md with parent_direction frontmatter
    direction_md = f"""---
title: "{synthesis['title']}"
type: feature
parent_direction: {previous_direction_id}
why: "This iterates on {previous_direction_id} to address: {feedback}"
acceptance:
  - "modify the existing backend/app/goal_types/{base_slug}/ module to address the following feedback: {feedback}"
---

# {synthesis['title']}

## Why
This iterates on {previous_direction_id} to address user feedback: {feedback}

## Acceptance Criteria
1. Modify the existing `backend/app/goal_types/{base_slug}/` module to address the following feedback: {feedback}
2. All existing tests continue to pass
3. New verifier behavior matches updated acceptance criteria
"""

    new_direction_id = await allocate_direction_id(iterate_slug)
    new_synthesis = {
        "title": synthesis["title"],
        "slug": iterate_slug,
        "direction_md": direction_md,
        "flow_md": synthesis.get("flow_md", ""),
        "api_spec_md": synthesis.get("api_spec_md", ""),
    }
    await write_direction(new_synthesis, new_direction_id)

    # Update goal's direction_id to the new iteration
    goal.awaiting_direction_id = new_direction_id
    await db.commit()

    # Record spend
    await _record_spend(db, current_user.id, 10, model, f"iterate_synthesis: {new_direction_id}")

    return {
        "direction_id": new_direction_id,
        "previous_direction_id": previous_direction_id,
        "status": "queued",
    }