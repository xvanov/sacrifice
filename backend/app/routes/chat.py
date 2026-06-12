"""Chat goal-type generation routes.

POST /api/chat/sessions/{session_id}/request-new-goal-type
GET  /api/chat/sessions/{session_id}/generation-status
POST /api/chat/sessions/{session_id}/accept-generated-type
POST /api/chat/sessions/{session_id}/iterate-generated-type

The actual business logic lives in module-level functions that can be mocked
independently by tests (e.g. synthesize_and_create_goal).
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.goal import Goal, GoalCriteria
from app.models.user import User
from app.services.chat_spend import (
    check_daily_spend_cap,
    has_in_flight_generation,
    record_spend,
)
from app.services.direction_synth import (
    synthesize_iteration_direction as _synthesize_iteration_direction,
)
from app.services.goal import get_goal_by_id

router = APIRouter(prefix="/api/chat/sessions", tags=["chat"])

# Estimated LLM cost per synthesis call in millicents (~$0.002)
_SYNTHESIS_COST_MILLICENTS = 200


def _read_direction_state(direction_id: str) -> dict | None:
    """Read the state.yaml for a direction and return a dict."""
    state_path = (
        Path(settings.directions_output_path) / direction_id / "state.yaml"
    )
    if not state_path.exists():
        return None
    state = {}
    for line in state_path.read_text().splitlines():
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip("'\"")
            state[key] = val
    return state


# ── Module-level functions (mockable by tests) ───────────────────────


async def synthesize_and_create_goal(
    prompt_summary: str,
    goal_payload_draft: dict,
    db: AsyncSession,
    user_id: uuid.UUID,
    session_id: str,
) -> dict:
    """Synthesize direction, write to disk, create goal in awaiting_goal_type.

    Returns: {"direction_id": str, "goal_id": str, "status": str}
    Raises ValueError on business-rule violations (spend_cap, vague prompt,
        in-flight conflict).
    """
    from app.services.direction_synth import synthesize_direction

    if not prompt_summary or not prompt_summary.strip():
        raise ValueError("prompt:too_vague: prompt_summary is required")

    # Spend cap check
    if not await check_daily_spend_cap(db, user_id):
        raise ValueError("spend_cap:exceeded")

    # In-flight check — scoped to this session
    existing = await has_in_flight_generation(db, user_id, session_id=session_id)
    if existing:
        raise ValueError(f"conflict:generation_in_flight:{existing}")

    # Synthesize
    from pathlib import Path as _Path
    import httpx

    class _LiveLLM:
        async def chat(self, system_prompt, user_prompt):
            headers = {
                "Authorization": f"Bearer {settings.azure_foundry_api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 4000,
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    settings.azure_foundry_endpoint,
                    headers=headers,
                    json=payload,
                    timeout=120,
                )
            if resp.status_code != 200:
                raise RuntimeError(f"LLM API error: {resp.status_code}")
            result = resp.json()
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")

    llm = _LiveLLM()
    direction_id = await synthesize_direction(
        llm_client=llm,
        prompt_summary=prompt_summary.strip(),
        output_base=_Path(settings.directions_output_path),
    )

    # Record spend
    await record_spend(
        db,
        user_id,
        call_type="direction_synthesis",
        model=settings.direction_synth_model,
        millicents=_SYNTHESIS_COST_MILLICENTS,
        direction_id=direction_id,
    )

    # Create goal
    from datetime import datetime as _dt

    goal_title = goal_payload_draft.get("title", prompt_summary.strip()[:255])
    goal_description = goal_payload_draft.get("description", prompt_summary.strip())
    pledge_amount = goal_payload_draft.get("pledge_amount", 0)
    currency = goal_payload_draft.get("currency", "usd")
    tz = goal_payload_draft.get("timezone", "UTC")
    recurrence = goal_payload_draft.get("recurrence", "none")
    charity_id = goal_payload_draft.get("charity_id")
    deadline_raw = goal_payload_draft.get("deadline")
    deadline = _dt.fromisoformat(deadline_raw) if isinstance(deadline_raw, str) else deadline_raw

    goal = Goal(
        user_id=user_id,
        title=goal_title,
        description=goal_description,
        goal_type="youtube_video",  # placeholder
        pledge_amount=pledge_amount,
        currency=currency,
        deadline=deadline,
        timezone=tz,
        recurrence=recurrence,
        status="awaiting_goal_type",
        awaiting_direction_id=direction_id,
        session_id=session_id,
        charity_id=charity_id,
    )
    db.add(goal)
    await db.flush()

    criteria = GoalCriteria(
        goal_id=goal.id,
        criteria_type="_generated",
        criteria_data={"direction_id": direction_id},
    )
    db.add(criteria)
    await db.commit()

    return {
        "direction_id": direction_id,
        "goal_id": str(goal.id),
        "status": "queued",
    }


async def get_generation_status_for_session(
    session_id: str,
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict:
    """Return generation status for the generation linked to the given session.

    Returns: {"direction_id": str, "status": str, "pr_url": str|None, "summary": str}
    Raises ValueError("generation:not_found") if no in-flight generation for this session.
    """
    import sqlalchemy as _sa

    result = await db.execute(
        _sa.select(Goal)
        .where(
            Goal.user_id == user_id,
            Goal.status == "awaiting_goal_type",
            Goal.awaiting_direction_id.isnot(None),
            Goal.session_id == session_id,
        )
        .order_by(Goal.created_at.desc())
        .limit(1),
    )
    goal = result.scalar_one_or_none()
    if not goal or not goal.awaiting_direction_id:
        raise ValueError("generation:not_found")

    direction_id = goal.awaiting_direction_id
    state = _read_direction_state(direction_id)
    if state is None:
        return {
            "direction_id": direction_id,
            "status": "queued",
            "pr_url": None,
            "summary": "",
        }

    current_status = state.get("status", "queued")
    if current_status == "pr_merged":
        await _ensure_goal_type_ready_notification(db, goal, current_status)

    return {
        "direction_id": direction_id,
        "status": current_status,
        "pr_url": state.get("pr_url") or None,
        "summary": state.get("summary", ""),
    }


async def _ensure_goal_type_ready_notification(
    db: AsyncSession,
    goal: Goal,
    direction_status: str,
) -> None:
    """Fire a goal_type_ready notification if one hasn't been fired yet."""
    import sqlalchemy as _sa

    from app.models.notification import Notification as NotifModel

    result = await db.execute(
        _sa.select(NotifModel)
        .where(
            NotifModel.goal_id == goal.id,
            NotifModel.type == "goal_type_ready",
        )
        .limit(1),
    )
    if result.scalar_one_or_none() is not None:
        return  # Already notified for this goal

    from app.services.notification import create_notification

    direction_id = goal.awaiting_direction_id or "unknown"
    await create_notification(
        db,
        user_id=goal.user_id,
        notification_type="goal_type_ready",
        title="Your goal type is ready",
        body=f"Goal type for '{goal.title}' (direction {direction_id} has been merged. "
        "Accept to activate your goal.",
        goal_id=goal.id,
    )


async def accept_generated_type_for_session(
    session_id: str,
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict:
    """Accept the generated goal type — transition goal to active.

    Returns: {"goal_id": str, "status": str}
    Raises ValueError("generation:not_merged") if not yet merged.
    Raises ValueError("generation:not_found") if no pending goal for this session.
    """
    import sqlalchemy as _sa

    result = await db.execute(
        _sa.select(Goal)
        .where(
            Goal.user_id == user_id,
            Goal.status == "awaiting_goal_type",
            Goal.session_id == session_id,
        )
        .order_by(Goal.created_at.desc())
        .limit(1)
    )
    goal = result.scalar_one_or_none()
    if not goal or not goal.awaiting_direction_id:
        raise ValueError("generation:not_found")

    state = _read_direction_state(goal.awaiting_direction_id)
    if state is None or state.get("status") != "pr_merged":
        raise ValueError("generation:not_merged")

    goal.status = "active"
    await db.commit()

    return {"goal_id": str(goal.id), "status": "active"}


async def iterate_generated_type_for_session(
    session_id: str,
    feedback: str,
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict:
    """File a new iteration direction, re-link the pending goal for this session.

    Returns: {"direction_id": str, "previous_direction_id": str, "status": str}
    Raises ValueError("goal:already_accepted") if goal was already accepted.
    Raises ValueError("generation:not_found") if no pending goal for this session.
    """
    import sqlalchemy as _sa

    if not feedback or not feedback.strip():
        raise ValueError("feedback:empty")

    # Spend cap
    if not await check_daily_spend_cap(db, user_id):
        raise ValueError("spend_cap:exceeded")

    result = await db.execute(
        _sa.select(Goal)
        .where(
            Goal.user_id == user_id,
            Goal.status == "awaiting_goal_type",
            Goal.session_id == session_id,
        )
        .order_by(Goal.created_at.desc())
        .limit(1)
    )
    goal = result.scalar_one_or_none()

    if not goal or not goal.awaiting_direction_id:
        # Check if there's an already-accepted goal (active status) with
        # an awaiting_direction_id in this session — the user already accepted it.
        accepted_result = await db.execute(
            _sa.select(Goal)
            .where(
                Goal.user_id == user_id,
                Goal.status == "active",
                Goal.awaiting_direction_id.isnot(None),
                Goal.session_id == session_id,
            )
            .order_by(Goal.created_at.desc())
            .limit(1)
        )
        if accepted_result.scalar_one_or_none():
            raise ValueError("goal:already_accepted")
        raise ValueError("generation:not_found")

    previous_direction_id = goal.awaiting_direction_id

    import httpx

    class _LiveLLM:
        async def chat(self, system_prompt, user_prompt):
            headers = {
                "Authorization": f"Bearer {settings.azure_foundry_api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 4000,
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    settings.azure_foundry_endpoint,
                    headers=headers,
                    json=payload,
                    timeout=120,
                )
            if resp.status_code != 200:
                raise RuntimeError(f"LLM API error: {resp.status_code}")
            result = resp.json()
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")

    new_direction_id = await _synthesize_iteration_direction(
        llm_client=_LiveLLM(),
        previous_direction_id=previous_direction_id,
        feedback=feedback.strip(),
        output_base=Path(settings.directions_output_path),
    )

    await record_spend(
        db,
        user_id,
        call_type="direction_iteration",
        model=settings.direction_synth_model,
        millicents=_SYNTHESIS_COST_MILLICENTS,
        direction_id=new_direction_id,
    )

    goal.awaiting_direction_id = new_direction_id
    await db.commit()

    return {
        "direction_id": new_direction_id,
        "previous_direction_id": previous_direction_id,
        "status": "queued",
    }


# ── Route handlers ────────────────────────────────────────────────────


@router.post("/{session_id}/request-new-goal-type")
async def request_new_goal_type(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    body = await request.json()
    prompt_summary = body.get("prompt_summary", "")
    goal_payload_draft = body.get("goal_payload_draft", {})

    try:
        result = await synthesize_and_create_goal(
            prompt_summary=prompt_summary,
            goal_payload_draft=goal_payload_draft,
            db=db,
            user_id=current_user.id,
            session_id=session_id,
        )
    except ValueError as e:
        msg = str(e)
        if "spend_cap" in msg:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "You've hit today's AI budget ($"
                    f"{settings.chat_daily_spend_cap_millicents / 100_000:.2f}). "
                    "Try again tomorrow."
                ),
            )
        if "generation_in_flight" in msg:
            parts = msg.split(":")
            direction_id = parts[-1] if len(parts) >= 3 else "unknown"
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "detail": msg,
                    "direction_id": direction_id,
                },
            )
        if "too_vague" in msg:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Prompt is too vague. Please be more specific about what you want to verify.",
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"LLM synthesis failed: {e}",
        )

    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=result)


@router.get("/{session_id}/generation-status")
async def generation_status(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await get_generation_status_for_session(
            session_id=session_id,
            db=db,
            user_id=current_user.id,
        )
    except ValueError as e:
        msg = str(e)
        if "not_found" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No in-flight generation found for this session",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg
        )


@router.post("/{session_id}/accept-generated-type")
async def accept_generated_type(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await accept_generated_type_for_session(
            session_id=session_id,
            db=db,
            user_id=current_user.id,
        )
    except ValueError as e:
        msg = str(e)
        if "not_merged" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Generation is not yet merged. Wait for PR merge before accepting.",
            )
        if "not_found" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No pending goal found for this session",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg
        )


@router.post("/{session_id}/iterate-generated-type")
async def iterate_generated_type(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    body = await request.json()
    feedback = body.get("feedback", "")

    try:
        result = await iterate_generated_type_for_session(
            session_id=session_id,
            feedback=feedback,
            db=db,
            user_id=current_user.id,
        )
    except ValueError as e:
        msg = str(e)
        if "already_accepted" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Pending goal has already been accepted — can't iterate after acceptance.",
            )
        if "spend_cap" in msg:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "You've hit today's AI budget ($"
                    f"{settings.chat_daily_spend_cap_millicents / 100_000:.2f}). "
                    "Try again tomorrow."
                ),
            )
        if "empty" in msg:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Feedback must not be empty or whitespace-only.",
            )
        if "not_found" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No pending goal found for this session",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"LLM synthesis failed: {e}",
        )

    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=result)