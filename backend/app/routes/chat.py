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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.chat_session import ChatSession
from app.models.chat_spend import ChatSpendLedger
from app.models.goal import Goal
from app.models.user import User
from app.schemas.goal import GoalCreate
from app.services.direction_synth import (
    DirectionSynthesisError,
    allocate_direction_id,
    fire_notification_on_merge,
    read_direction_metadata,
    read_direction_state,
    synthesize_direction,
    write_direction,
)
from app.services.goal import create_goal

router = APIRouter(prefix="/api/chat", tags=["chat"])

# ── Placeholder values for generated goals ───────────────────────────

GENERATED_PLACEHOLDER_TYPE = "__generated__"
GENERATED_PLACEHOLDER_CRITERIA_TYPE = "generated"
GENERATED_PLACEHOLDER_CRITERIA = {"generated": True, "direction_id": None}


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
    chat_history: list[dict] | None = None


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
    """Add a spend ledger entry WITHOUT committing — caller manages the transaction."""
    entry = ChatSpendLedger(
        user_id=user_id,
        cost_millicents=cost_millicents,
        model=model,
        call_description=description,
    )
    db.add(entry)


# ── Session helpers ───────────────────────────────────────────────────


async def _get_session_or_404(
    db: AsyncSession,
    session_id: str,
    user_id: uuid.UUID,
) -> ChatSession:
    """Load a chat session by id; 404 if missing or not owned by user."""
    result = await db.execute(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


async def _get_linked_goal(
    db: AsyncSession,
    session: ChatSession,
    *,
    require_awaiting: bool = False,
) -> Goal | None:
    """Return the goal linked to this session, if any.

    When require_awaiting is True, only return a goal that is still in
    awaiting_goal_type status.
    """
    if not session.goal_id:
        return None
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Goal).options(selectinload(Goal.criteria)).where(Goal.id == session.goal_id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        return None
    if require_awaiting and goal.status != "awaiting_goal_type":
        return None
    return goal


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/request-new-goal-type", status_code=202)
async def request_new_goal_type(
    session_id: str,
    body: RequestNewGoalTypeBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Synthesize a direction, write it to disk, and create goal in awaiting_goal_type."""
    # 1. Session must already exist — 404 if not found (per API spec).
    session = await _get_session_or_404(db, session_id, current_user.id)

    # 2. If session already has an in-flight awaiting goal, 409
    existing_goal = await _get_linked_goal(db, session, require_awaiting=True)
    if existing_goal and existing_goal.awaiting_direction_id:
        return JSONResponse(
            status_code=409,
            content={
                "detail": f"You're already building '{existing_goal.awaiting_direction_id}'. Want to add to that one instead?",
                "direction_id": existing_goal.awaiting_direction_id,
            },
        )

    # 3. Check spend cap
    if not await _check_spend_cap(db, current_user.id):
        raise HTTPException(
            status_code=429,
            detail="You've hit today's AI budget. Try again tomorrow, or reach out if this is wrong.",
        )

    # 4. Synthesize direction (no disk write yet — CR7: write after DB success)
    model = settings.direction_synth_model or settings.azure_foundry_deployment
    try:
        synthesis = await synthesize_direction(
            body.prompt_summary,
            chat_history=body.chat_history,
        )
    except DirectionSynthesisError as e:
        # Record the failed LLM call spend and commit it atomically.
        # This is the only pending DB change on this path (no goal was
        # created yet), so the explicit commit is correct — the spend
        # happened and must be recorded before the 422 rolls back the
        # surrounding transaction.
        await _record_spend(db, current_user.id, 0, model, f"synthesis_failed: {body.prompt_summary[:100]}")
        await db.commit()
        raise HTTPException(
            status_code=422,
            detail=f"I couldn't pin down what you want — try rephrasing with more concrete success criteria. ({e})",
        )

    slug = synthesis["slug"]
    direction_id = await allocate_direction_id(slug)

    # Derive canonical module_name from slug (hyphens → underscores).
    # This is persisted in criteria_data so accept-generated-type reads
    # the canonical name rather than deriving it from direction_id (CR2).
    module_name = slug.replace("-", "_")

    # 5. Create goal with neutral placeholder (CR2: no youtube_video hard-code)
    criteria_placeholder = {
        **GENERATED_PLACEHOLDER_CRITERIA,
        "direction_id": direction_id,
        "module_name": module_name,
    }
    goal_data = GoalCreate(
        title=body.goal_payload_draft.title,
        description=body.goal_payload_draft.description,
        deadline=body.goal_payload_draft.deadline,
        pledge_amount=body.goal_payload_draft.pledge_amount,
        goal_type=GENERATED_PLACEHOLDER_TYPE,
        criteria=criteria_placeholder,
        charity_id=body.goal_payload_draft.charity_id,
        timezone=body.goal_payload_draft.timezone,
        recurrence=body.goal_payload_draft.recurrence,
        currency=body.goal_payload_draft.currency,
    )

    try:
        goal = await create_goal(
            db=db,
            user_id=current_user.id,
            data=goal_data,
            status="awaiting_goal_type",
            awaiting_direction_id=direction_id,
            commit=False,
        )
    except Exception:
        # DB failure — don't leave an orphaned reserved directory (CR2)
        import shutil
        from pathlib import Path as _Path
        direction_dir = _Path(settings.directions_path) / direction_id
        if direction_dir.exists():
            shutil.rmtree(direction_dir, ignore_errors=True)
        raise

    # 6. Write the direction to disk. If this fails, delete the created goal
    #    and session linkage to avoid an orphaned awaiting_goal_type goal
    #    referencing a non-existent direction (CR2).
    try:
        await write_direction(synthesis, direction_id)
    except Exception:
        import shutil
        from pathlib import Path as _Path
        # Rollback all pending DB changes (goal + criteria) since the
        # greenlet is gone by the time this handler runs.
        await db.rollback()
        # Remove the reserved directory
        direction_dir = _Path(settings.directions_path) / direction_id
        if direction_dir.exists():
            shutil.rmtree(direction_dir, ignore_errors=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to write direction to disk. Goal creation was rolled back.",
        )

    # 7. Link session to goal, record spend, and commit atomically. If the
    #    COMMIT itself fails, the direction directory written in step 6 must
    #    not be left orphaned on disk (compensating cleanup, mirrors step 6's
    #    handling of the inverse failure order).
    session.goal_id = goal.id
    session.awaiting_direction_id = direction_id
    session.last_activity_at = datetime.now(timezone.utc)
    await _record_spend(db, current_user.id, 10, model, f"direction_synthesis: {direction_id}")
    try:
        await db.commit()
    except Exception:
        import shutil
        from pathlib import Path as _Path

        await db.rollback()
        direction_dir = _Path(settings.directions_path) / direction_id
        if direction_dir.exists():
            shutil.rmtree(direction_dir, ignore_errors=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to persist goal/session linkage. Direction was rolled back.",
        )

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
    # Session-scoped lookup (CR1)
    session = await _get_session_or_404(db, session_id, current_user.id)

    goal = await _get_linked_goal(db, session, require_awaiting=True)
    if not goal or not goal.awaiting_direction_id:
        raise HTTPException(
            status_code=404,
            detail="No in-flight generation found for this session.",
        )

    direction_id = goal.awaiting_direction_id

    state = await read_direction_state(direction_id)
    if not state:
        raise HTTPException(
            status_code=404,
            detail="Direction state not found.",
        )

    # On pr_merged, fire notification idempotently (poll-based path).
    # Also fired in accept-generated-type as the action-based path (CR6).
    if state.get("status") == "pr_merged":
        await fire_notification_on_merge(
            direction_id=direction_id,
            goal_id=str(goal.id),
            user_id=str(current_user.id),
            db_session=db,
        )

    return GenerationStatusResponse(
        direction_id=direction_id,
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
    # Session-scoped lookup (CR1)
    session = await _get_session_or_404(db, session_id, current_user.id)

    goal = await _get_linked_goal(db, session, require_awaiting=True)
    if not goal:
        raise HTTPException(status_code=404, detail="No pending goal found for this session.")

    direction_id = goal.awaiting_direction_id
    if not direction_id:
        raise HTTPException(status_code=404, detail="No direction linked to pending goal.")

    # Verify generation is merged
    state = await read_direction_state(direction_id)
    if not state or state.get("status") != "pr_merged":
        raise HTTPException(
            status_code=409,
            detail="Generation is not yet merged. Wait for the PR to merge before accepting.",
        )

    # Read canonical module_name from criteria_data and verify the registry
    # has it. The factory chain's merge migration adds the type to the PG
    # goal_type enum, so setting goal.goal_type directly is safe (the DB
    # accepts the new value). We no longer keep the '__generated__'
    # placeholder — the accepted goal is fully dispatchable without any
    # fallback path.
    criteria_data = (goal.criteria.criteria_data if goal.criteria else {}) or {}
    module_name = criteria_data.get("module_name")
    if not module_name:
        raise HTTPException(
            status_code=409,
            detail="Goal criteria is missing the canonical module_name. The goal may not have been created via the generation flow.",
        )

    # Verify the module is registered in the in-memory registry. The
    # factory chain's merge migration installs the module and adds its name
    # to the PG goal_type enum; if it's missing the merge hasn't completed
    # — return 409 rather than activating a non-dispatchable goal.
    try:
        from app.goal_types.registry import get_type as _get_registered_type
        _get_registered_type(module_name)
    except (KeyError, ImportError):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Goal type '{module_name}' is not yet registered. "
                "The factory chain merge migration may not have completed. "
                "Wait for the PR to fully merge before accepting."
            ),
        )

    # Fire notification on acceptance. Idempotent — won't duplicate if
    # already fired.
    await fire_notification_on_merge(
        direction_id=direction_id,
        goal_id=str(goal.id),
        user_id=str(current_user.id),
        db_session=db,
    )

    goal.status = "active"
    goal.goal_type = module_name
    # Clear pending-generation linkage so future chat actions only inspect
    # in-flight generation state, not an already-accepted goal.
    goal.awaiting_direction_id = None
    session.awaiting_direction_id = None
    session.goal_id = None

    # Migrate criteria from generated placeholder to concrete verifier contract.
    if goal.criteria:
        goal.criteria.criteria_type = module_name
        # Remove generated-placeholder flags; keep only module metadata.
        cleaned_criteria = {
            k: v for k, v in (goal.criteria.criteria_data or {}).items()
            if k not in ("generated", "direction_id")
        }
        goal.criteria.criteria_data = cleaned_criteria

    await db.commit()

    return AcceptGeneratedTypeResponse(goal_id=str(goal.id), status=goal.status)


@router.post("/sessions/{session_id}/iterate-generated-type", status_code=202)
async def iterate_generated_type(
    session_id: str,
    body: IterateGeneratedTypeBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """File a follow-up direction that modifies the existing module."""
    # Session-scoped lookup (CR1)
    session = await _get_session_or_404(db, session_id, current_user.id)

    goal = await _get_linked_goal(db, session, require_awaiting=True)
    if not goal:
        # Check if there was a goal that's been accepted
        accepted_goal = await _get_linked_goal(db, session, require_awaiting=False)
        if accepted_goal and accepted_goal.status != "awaiting_goal_type":
            raise HTTPException(
                status_code=409,
                detail="Goal has already been accepted. Cannot iterate after acceptance.",
            )
        raise HTTPException(status_code=404, detail="No pending goal found for this session.")

    previous_direction_id = goal.awaiting_direction_id
    if not previous_direction_id:
        raise HTTPException(status_code=404, detail="No direction linked to pending goal.")

    # Check spend cap
    if not await _check_spend_cap(db, current_user.id):
        raise HTTPException(
            status_code=429,
            detail="You've hit today's AI budget. Try again tomorrow, or reach out if this is wrong.",
        )

    feedback = body.feedback

    # Read the canonical module_name from criteria_data (persisted at
    # synthesis time). This is the underscore-form directory name (e.g.
    # "pushup_counter"), NOT the hyphenated slug. CR4: use this in
    # direction content paths so the Dev persona writes to the right dir.
    criteria_data = (goal.criteria.criteria_data if goal.criteria else {}) or {}
    canonical_module_name = criteria_data.get("module_name", "")
    if not canonical_module_name:
        # Fallback: derive from slug (hyphens → underscores)
        slug_parts = previous_direction_id.split("-", 1)
        fallback_slug = slug_parts[1] if len(slug_parts) > 1 else slug_parts[0]
        canonical_module_name = fallback_slug.replace("-", "_")

    # Derive a feedback-based slug (not iterate-N — concurrent-safe, CR5).
    # Strip chain-position tokens and standalone numbers so user feedback like
    # "iterate 2 with side angle" cannot produce an "iterate-N" style slug
    # (explicitly forbidden by the story).
    slug_parts = previous_direction_id.split("-", 1)
    base_slug = slug_parts[1] if len(slug_parts) > 1 else slug_parts[0]
    _FORBIDDEN_SLUG_TOKENS = {
        "iterate", "iteration", "iter", "v2", "v3", "v4", "v5",
    }
    feedback_words = feedback.lower().split()[:6]
    cleaned_words = []
    for w in feedback_words:
        token = w.strip(",.!?()[]{}\"'")
        if len(token) <= 2:
            continue
        if token in _FORBIDDEN_SLUG_TOKENS:
            continue
        if token.isdigit():
            continue
        cleaned_words.append(token)
    feedback_slug = "-".join(cleaned_words) if cleaned_words else "refinement"
    iterate_slug = f"{base_slug}-{feedback_slug}"

    model = settings.direction_synth_model or settings.azure_foundry_deployment

    try:
        synthesis = await synthesize_direction(
            f"Iteration on {previous_direction_id}: {feedback}",
        )
    except DirectionSynthesisError as e:
        # Record the failed LLM call spend and commit it atomically.
        # This is the only pending DB change on this path (no goal or
        # direction write occurred yet), so the explicit commit is
        # correct — the spend happened and must be recorded before the
        # 422 rolls back the surrounding transaction.
        await _record_spend(db, current_user.id, 0, model, f"iterate_synthesis_failed: {previous_direction_id}")
        await db.commit()
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
  - "modify the existing backend/app/goal_types/{canonical_module_name}/ module to address the following feedback: {feedback}"
---

# {synthesis['title']}

## Why
This iterates on {previous_direction_id} to address user feedback: {feedback}

## Acceptance Criteria
1. Modify the existing `backend/app/goal_types/{canonical_module_name}/` module to address the following feedback: {feedback}
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

    # ══ Stage DB changes BEFORE writing direction to disk (CR3).
    # This way the DB can always be rolled back; no disk artifact is
    # visible externally unless the DB commit succeeds.
    goal.awaiting_direction_id = new_direction_id
    if goal.criteria:
        criteria_data = dict(goal.criteria.criteria_data) if goal.criteria.criteria_data else {}
        criteria_data["direction_id"] = new_direction_id
        goal.criteria.criteria_data = criteria_data
    session.awaiting_direction_id = new_direction_id
    session.last_activity_at = datetime.now(timezone.utc)
    await _record_spend(db, current_user.id, 10, model, f"iterate_synthesis: {new_direction_id}")

    try:
        await write_direction(new_synthesis, new_direction_id)
    except Exception:
        # Disk write failed — rollback DB changes to avoid leaking a
        # direction-id reservation into the session/goal.
        # Also remove the reserved directory so future retries don't collide.
        await db.rollback()
        from pathlib import Path as _Path
        _direction_dir = _Path(settings.directions_path) / new_direction_id
        if _direction_dir.exists():
            import shutil as _shutil
            _shutil.rmtree(_direction_dir, ignore_errors=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to write iteration direction to disk.",
        )

    # Disk write succeeded — commit the DB changes atomically.
    try:
        await db.commit()
    except Exception:
        # DB commit failed — clean up the written direction dir (CR3)
        from pathlib import Path as _Path
        _direction_dir = _Path(settings.directions_path) / new_direction_id
        if _direction_dir.exists():
            import shutil as _shutil
            _shutil.rmtree(_direction_dir, ignore_errors=True)
        raise

    return {
        "direction_id": new_direction_id,
        "previous_direction_id": previous_direction_id,
        "status": "queued",
    }