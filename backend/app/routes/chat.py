"""Chat routes for goal-creation flow.

Endpoints:
- POST /api/chat/sessions
- POST /api/chat/sessions/{session_id}/messages
- POST /api/chat/sessions/{session_id}/request-new-goal-type
- GET /api/chat/sessions/{session_id}/generation-status
- POST /api/chat/sessions/{session_id}/accept-generated-type
- POST /api/chat/sessions/{session_id}/iterate-generated-type
"""

import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.goal_types.registry import (
    get_type as get_registry_type,
    list_types as list_registry_types,
)
from app.models.chat_session import ChatSession
from app.models.chat_spend import ChatSpendLedger
from app.models.goal import Goal
from app.models.user import User
from app.schemas.chat import (
    CreateGoalRequest,
    CreateGoalResponse,
    CreateSessionResponse,
)
from app.schemas.goal import GoalCreate
from app.services.chat_match import CatalogEntry, match_message, ChatMatchError
from app.services.direction_synth import (
    DirectionSynthesisError,
    _derive_slug,
    allocate_direction_id,
    fire_notification_on_merge,
    read_direction_state,
    synthesize_direction,
    write_direction,
)
from app.services.criteria_coercion import (
    UNUSABLE,
    coerce_criteria,
    coerce_criteria_value,
    describe_expected_type,
)
from app.services.goal import create_goal
from app.services.input_parsing import (
    DEADLINE_MIN_LEAD,
    coerce_number,
    parse_coordinates,
    parse_deadline,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# ── Greeting constant ─────────────────────────────────────────────────

GREETING_MESSAGE_DICT = {
    "role": "assistant",
    "content": "Tell me what you want to do, and I'll figure out how to track it.",
    "action": None,
}

# ── Placeholder values for generated goals ───────────────────────────

GENERATED_PLACEHOLDER_TYPE = "__generated__"
GENERATED_PLACEHOLDER_CRITERIA_TYPE = "generated"
GENERATED_PLACEHOLDER_CRITERIA = {"generated": True, "direction_id": None}


def _force_generate(request: Request) -> bool:
    """Test-only bypass: env flag or X-Sacrifice-Force-Generate header.

    When True, the chat matcher is skipped and the vague-prompt guard is
    bypassed, forcing every prompt into the generation path.

    The header bypass is gated behind ``sacrifice_force_generate`` — it is
    unavailable in normal production runtime. When the setting is active,
    the header is also checked so tests can toggle per-request behaviour;
    when the setting is inactive (production), the header is ignored.
    """
    if not settings.sacrifice_force_generate:
        return False
    # When the test-only setting is active, either the global flag or the
    # per-request header activates the bypass.
    if request.headers.get("X-Sacrifice-Force-Generate", "").strip() == "1":
        return True
    return True


class SendMessageBody(BaseModel):
    """Request body for POST /api/chat/sessions/{session_id}/messages."""

    content: str
    # IANA timezone of the user's device (e.g. "America/New_York"). Deadlines
    # they type ("6am tomorrow") are interpreted in THEIR timezone, and the
    # created goal records it — defaulting to UTC silently shifted deadlines.
    timezone: str | None = None

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("content must not be empty or whitespace")
        return stripped


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
    # Lenient: in the no-match → "build a new goal type" flow the client has
    # not run slot-filling yet, so the draft is empty/partial. We recover what
    # we can from the prompt and fall back to placeholders (see endpoint), so a
    # missing/empty draft must NOT 422. A fully-specified draft is still used.
    goal_payload_draft: dict | None = None
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
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
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
    """Load a chat session by UUID primary key or legacy external session_id.

    D009 session creation returns the UUID primary key in API paths, while
    older generation-flow tests still seed and address sessions by the
    external ``session_id`` string column. Ownership violations are 403;
    missing sessions are 404.
    """
    session: ChatSession | None = None

    try:
        pk = uuid.UUID(session_id)
    except ValueError:
        pk = None

    if pk is not None:
        result = await db.execute(select(ChatSession).where(ChatSession.id == pk))
        session = result.scalar_one_or_none()

    if session is None:
        result = await db.execute(
            select(ChatSession).where(ChatSession.session_id == session_id)
        )
        session = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Session not owned by user.")
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
        select(Goal)
        .options(selectinload(Goal.criteria))
        .where(Goal.id == session.goal_id)
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
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Synthesize a direction, write it to disk, and create goal in awaiting_goal_type."""
    # 1. Session must already exist — 404 if not found (per API spec).
    session = await _get_session_or_404(db, session_id, current_user.id)

    # 2. If user already has an in-flight generation (any session), 409
    existing_goal = await _get_linked_goal(db, session, require_awaiting=True)
    if not existing_goal:
        # Also scan other sessions owned by this user for any in-flight
        # generation per the API-spec cross-session 409 rule.
        user_inflight = await db.execute(
            select(Goal).where(
                Goal.user_id == current_user.id,
                Goal.status == "awaiting_goal_type",
                Goal.awaiting_direction_id.isnot(None),
            )
        )
        existing_goal = user_inflight.scalars().first()

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
    # D010: force-generate flag/header bypasses the chat matcher and
    # the vague-prompt guard, forcing every prompt into the generation path.
    # Branch BEFORE synthesize_direction so force_generate never hits the
    # real matcher/LLM (reviewer CR2 fix).
    force_generate = _force_generate(request)
    model = settings.direction_synth_model or settings.azure_foundry_deployment

    if force_generate:
        slug = _derive_slug(body.prompt_summary, force_generate=True)
        synthesis = {
            "title": slug.replace("-", " ").title(),
            "slug": slug,
            "direction_md": f"# {slug}\n\nForce-generated from: {body.prompt_summary}\n",
            "flow_md": "# Flow\n\nForce-generated.\n",
            "api_spec_md": "# API\n\nForce-generated.\n",
        }
    else:
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
            await _record_spend(
                db,
                current_user.id,
                0,
                model,
                f"synthesis_failed: {body.prompt_summary[:100]}",
            )
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
    # Resolve the goal fields: prefer an explicit client draft, then anything
    # we can parse from the user's prompt (e.g. "$10" → 1000), then safe
    # placeholders. The goal is created in awaiting_goal_type and is not
    # finalized/charged until the user accepts the generated type, so
    # placeholder values here are intentional, not silent data loss.
    draft = body.goal_payload_draft or {}
    extracted = _extract_partial_goal_fields(
        body.prompt_summary or "", goal_type_name=GENERATED_PLACEHOLDER_TYPE
    )

    def _draft_field(name, fallback):
        for source in (draft, extracted):
            value = source.get(name)
            if value not in (None, ""):
                return value
        return fallback

    placeholder_title = (body.prompt_summary or "New goal").strip()[:200] or "New goal"
    placeholder_deadline = datetime.now(timezone.utc) + timedelta(days=7)
    goal_data = GoalCreate(
        title=_draft_field("title", placeholder_title),
        description=_draft_field("description", body.prompt_summary),
        deadline=_draft_field("deadline", placeholder_deadline),
        pledge_amount=int(_draft_field("pledge_amount", 500)),
        goal_type=GENERATED_PLACEHOLDER_TYPE,
        criteria=criteria_placeholder,
        charity_id=_draft_field("charity_id", None),
        timezone=_draft_field("timezone", "UTC"),
        recurrence=_draft_field("recurrence", "none"),
        currency=_draft_field("currency", "usd"),
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
    await _record_spend(
        db, current_user.id, 10, model, f"direction_synthesis: {direction_id}"
    )
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
        raise HTTPException(
            status_code=404, detail="No pending goal found for this session."
        )

    direction_id = goal.awaiting_direction_id
    if not direction_id:
        raise HTTPException(
            status_code=404, detail="No direction linked to pending goal."
        )

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

    # Don't activate a goal whose deadline has already passed or is within the
    # next hour (e.g. a draft left overnight): it would be failed on the next
    # deadline sweep before the owner could submit anything. Mirror the
    # future-deadline guard in the create/update services.
    if goal.deadline is not None:
        goal_deadline = (
            goal.deadline.replace(tzinfo=timezone.utc)
            if goal.deadline.tzinfo is None
            else goal.deadline
        )
        if goal_deadline <= datetime.now(timezone.utc) + DEADLINE_MIN_LEAD:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This goal's deadline is in the past or within the next "
                    "hour. Update the deadline before activating it."
                ),
            )

    goal.status = "active"
    goal.goal_type = module_name
    # Clear pending-generation linkage so future chat actions only inspect
    # in-flight generation state, not an already-accepted goal.
    goal.awaiting_direction_id = None
    session.awaiting_direction_id = None
    # session.goal_id is KEPT: the session stays associated with the now-active
    # goal, and iterate-after-accept depends on it to return the spec'd 409.

    # Migrate criteria from generated placeholder to concrete verifier contract.
    if goal.criteria:
        goal.criteria.criteria_type = module_name
        # Remove generated-placeholder flags; keep only module metadata.
        cleaned_criteria = {
            k: v
            for k, v in (goal.criteria.criteria_data or {}).items()
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
        raise HTTPException(
            status_code=404, detail="No pending goal found for this session."
        )

    previous_direction_id = goal.awaiting_direction_id
    if not previous_direction_id:
        raise HTTPException(
            status_code=404, detail="No direction linked to pending goal."
        )

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
        "iterate",
        "iteration",
        "iter",
        "v2",
        "v3",
        "v4",
        "v5",
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
        await _record_spend(
            db,
            current_user.id,
            0,
            model,
            f"iterate_synthesis_failed: {previous_direction_id}",
        )
        await db.commit()
        raise HTTPException(
            status_code=422,
            detail=f"I couldn't pin down what you want — try rephrasing with more concrete success criteria. ({e})",
        )

    # Build direction.md with parent_direction frontmatter
    direction_md = f"""---
title: "{synthesis["title"]}"
type: feature
parent_direction: {previous_direction_id}
why: "This iterates on {previous_direction_id} to address: {feedback}"
acceptance:
  - "modify the existing backend/app/goal_types/{canonical_module_name}/ module to address the following feedback: {feedback}"
---

# {synthesis["title"]}

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
        criteria_data = (
            dict(goal.criteria.criteria_data) if goal.criteria.criteria_data else {}
        )
        criteria_data["direction_id"] = new_direction_id
        goal.criteria.criteria_data = criteria_data
    session.awaiting_direction_id = new_direction_id
    session.last_activity_at = datetime.now(timezone.utc)
    await _record_spend(
        db, current_user.id, 10, model, f"iterate_synthesis: {new_direction_id}"
    )

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


# ── D009: session creation (merged from main) ────────────────────────────────


@router.post(
    "/sessions",
    status_code=201,
    response_model=CreateSessionResponse,
)
async def create_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new chat session with an initial assistant greeting."""
    session = ChatSession(
        user_id=current_user.id,
        messages=[GREETING_MESSAGE_DICT],
        draft_goal=None,
        status="active",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return {
        "session_id": session.id,
        "messages": session.messages,
        "status": session.status,
    }


# ── D009: message endpoint ─────────────────────────────────────────────


_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _is_missing_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return False
    return False


def _client_tz(tz_name: str | None):
    """Resolve the client's IANA timezone, falling back to UTC.

    A bad or missing timezone must never raise: the deadline still has to be
    interpretable, it just loses the local-wall-clock nuance.
    """
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001 — a bogus client tz falls back to UTC
            pass
    return timezone.utc


def _extract_deadline(content: str, tz_name: str | None = None) -> str | None:
    """Pull a deadline out of free text, interpreted in the USER'S timezone.

    "by 2026-08-01" means the end of August 1st where the user is standing. It
    used to be pinned to UTC, which quietly moved the deadline for everyone not
    on UTC — a US-Pacific user was chargeable from 17:00 local on July 31st,
    seven hours before the deadline they typed, and the deadline sweep
    (``app/workers/deadline.py``) charges the pledge for real.

    Note this stays a regex extractor rather than delegating to
    ``parse_deadline``: that parser is built for a reply that is *only* a
    deadline, and on prose it reads "pledge $20" as 20:00. See
    ``tests/test_chat_criteria_coercion.py``.
    """
    tz = _client_tz(tz_name)

    absolute_match = re.search(
        r"\b(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2})(?::(\d{2}))?)?\b",
        content,
    )
    if absolute_match:
        date_part, time_part, seconds_part = absolute_match.groups()
        if time_part:
            seconds = seconds_part or "00"
            deadline = datetime.fromisoformat(f"{date_part}T{time_part}:{seconds}")
        else:
            deadline = datetime.fromisoformat(f"{date_part}T23:59:59")
        return deadline.replace(tzinfo=tz).isoformat()

    weekday_match = re.search(
        r"\b(?:by|before|on)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        content,
        re.IGNORECASE,
    )
    if not weekday_match:
        return None

    weekday = _WEEKDAY_INDEX[weekday_match.group(1).lower()]
    # "next Friday" is the next Friday on the user's calendar, not UTC's — near
    # midnight the two disagree about what day it currently is.
    now = datetime.now(tz)
    days_ahead = (weekday - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    deadline = (now + timedelta(days=days_ahead)).replace(
        hour=23,
        minute=59,
        second=59,
        microsecond=0,
    )
    return deadline.isoformat()


def _extract_title(content: str, goal_type_name: str) -> str | None:
    patterns = [
        r"(?:upload|post|record|share|publish)\s+(?:a\s+|an\s+)?(.+?)(?:\s+by\b|\s+before\b|\s+on\b|\s+and\s+pledge\b|\s+pledge\b|$)",
        r"(?:build|create|ship|finish|complete)\s+(?:a\s+|an\s+)?(.+?)(?:\s+by\b|\s+before\b|\s+on\b|\s+and\s+pledge\b|\s+pledge\b|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if not match:
            continue
        title = re.sub(r"\s+", " ", match.group(1)).strip(" .")
        title = re.sub(r"^(?:a|an)\s+", "", title, flags=re.IGNORECASE)
        if title:
            return title[0].upper() + title[1:]

    if goal_type_name == "youtube_video" and "youtube" in content.lower():
        return "YouTube video"
    return None


# A duration with its unit. The plural is the whole point: the previous
# pattern ended in ``(minute|min|second|sec)\b``, so "5 minutes" — the single
# most natural answer — failed to match and fell through to storing the raw
# string. ``min_duration_seconds`` then reached ``app/workers/youtube.py``,
# where ``int >= str`` raises, no verdict is ever written, and the deadline
# sweep charges the pledge instead. Longest alternative first so "minutes"
# isn't half-matched by "min".
_DURATION_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*[-\s]?\s*(minutes?|mins?|seconds?|secs?)\b",
    re.IGNORECASE,
)


def _parse_duration_seconds(content: str) -> int | None:
    """Read a duration in seconds from free text, or ``None`` if unreadable.

    Understands "5 minutes", "5 minute", "5 mins", "5-minute", "90 seconds",
    "2.5 minutes", and a bare number (which the prompt frames as seconds).
    """
    match = _DURATION_RE.search(content)
    if match:
        amount = float(match.group(1))
        if match.group(2).lower().startswith(("minute", "min")):
            amount *= 60
        seconds = int(round(amount))
        return seconds if seconds > 0 else None

    stripped = content.strip()
    if re.fullmatch(r"\d+", stripped):
        seconds = int(stripped)
        return seconds if seconds > 0 else None
    return None


# ── Repo criteria parsing ─────────────────────────────────────────────
# The three github_repo criteria its verifier can actually run. They are
# collected as a group: the schema's ``anyOf`` only demands one of them, and
# users answer with whichever they care about ("at least 3 commits", "a
# README.md and tests/", "yes, require a PR") regardless of which one we asked
# about — the same reason a pasted coordinate pair fills both axes below.
_REPO_CRITERION_FIELDS = ("min_commits", "required_files", "require_pr")

_COMMIT_COUNT_PATTERNS = (
    re.compile(r"(\d+)\s*\+?\s*(?:or\s+more\s+)?commits?\b", re.IGNORECASE),
    re.compile(r"\bcommits?\b\s*(?:[:=]|>=|at\s+least)?\s*(\d+)", re.IGNORECASE),
)
_BARE_COUNT_RE = re.compile(r"^\s*(\d+)\s*$")
_PR_MENTION_RE = re.compile(r"\b(?:pr|prs|pull\s+requests?)\b", re.IGNORECASE)
# A bare mention of "PR" is not a commitment to open one. Requiring a verb keeps
# the goal type's own sample prompt working ("Open a PR with at least 3 commits")
# while refusing to read intent into "Push my PR-review fixes by Friday" — which
# used to set require_pr and then charge the user when no PR existed.
_PR_INTENT_RE = re.compile(
    r"\b(?:open|opens|opened|opening|create|creates|created|creating|raise|raises"
    r"|raised|raising|submit|submits|submitted|submitting|merge|merges|merged"
    r"|merging|land|lands|landed|landing|require|requires|required|with)\b"
    r"[^.!?]{0,40}?\b(?:pr|prs|pull\s+requests?)\b"
    r"|\b(?:pr|prs|pull\s+requests?)\b[^.!?]{0,40}?"
    r"\b(?:open|opened|created|merged|raised|submitted|required)\b",
    re.IGNORECASE,
)
_PR_NEGATION_RE = re.compile(
    r"\b(?:no|not|don'?t|doesn'?t|without|skip|never)\b", re.IGNORECASE
)
_AFFIRMATIVE_RE = re.compile(
    r"^\s*(?:yes|yep|yeah|yup|sure|ok|okay|true|please|do\s+it|require\s+(?:it|one))\b",
    re.IGNORECASE,
)
# Repo files the ecosystem conventionally writes without an extension, so a
# bare "add a Makefile" still reads as a path.
_EXTENSIONLESS_REPO_FILES = frozenset(
    {
        "makefile",
        "dockerfile",
        "license",
        "licence",
        "readme",
        "changelog",
        "codeowners",
        "procfile",
        "gemfile",
        "rakefile",
    }
)
_PATH_TOKEN_RE = re.compile(r"^[\w.\-/]+$")
_PATH_EXTENSION_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,9}$")


def _looks_like_repo_path(token: str) -> bool:
    """True when *token* reads as a repo-relative file or directory path."""
    if not token or "://" in token or "github.com" in token.lower():
        # A pasted repo URL is not a required file.
        return False
    if not _PATH_TOKEN_RE.match(token):
        return False
    if token.lower() in _EXTENSIONLESS_REPO_FILES:
        return True
    if token.endswith("/") or "/" in token:
        return True
    extension = _PATH_EXTENSION_RE.search(token)
    # Require a real stem so prose abbreviations ("e.g.", "i.e.") are not
    # mistaken for filenames.
    return bool(extension) and len(token[: extension.start()]) >= 2


def _parse_repo_criteria(content: str, *, asked_field: str | None = None) -> dict:
    """Read whatever runnable github_repo criteria a plain-language reply states.

    Returns only the fields it could read with confidence, and never invents a
    criterion the user did not ask for. Degenerate answers ("zero commits",
    "0 commits", "abc") yield nothing, so the caller stores nothing and the
    missing-criteria computation asks again — storing them would persist a
    criterion the verifier classifies as inert
    (``app/workers/github_repo.py`` ``_plan_criteria``), i.e. an unwinnable goal.

    *asked_field* is the criterion the assistant just asked about, which lets a
    bare answer ("3", "yes") be read against that question.
    """
    parsed: dict = {}

    for pattern in _COMMIT_COUNT_PATTERNS:
        match = pattern.search(content)
        if match:
            count = int(match.group(1))
            if count > 0:
                parsed["min_commits"] = count
            break
    if asked_field == "min_commits" and "min_commits" not in parsed:
        bare = _BARE_COUNT_RE.match(content)
        if bare and int(bare.group(1)) > 0:
            parsed["min_commits"] = int(bare.group(1))

    paths: list[str] = []
    for raw_token in re.split(r"[\s,;]+", content):
        token = raw_token.strip("\"'`()[]<>").rstrip(".,:;!?")
        if token and _looks_like_repo_path(token) and token not in paths:
            paths.append(token)
    if paths:
        parsed["required_files"] = paths

    if _PR_INTENT_RE.search(content):
        # "at least 3 commits, no PR needed" must not turn into require_pr.
        if not _PR_NEGATION_RE.search(content):
            parsed["require_pr"] = True
    elif asked_field == "require_pr" and _AFFIRMATIVE_RE.match(content):
        parsed["require_pr"] = True

    return parsed


def _extract_partial_goal_fields(
    content: str,
    *,
    goal_type_name: str,
    existing_draft: dict | None = None,
    tz_name: str | None = None,
) -> dict:
    draft_goal = dict(existing_draft or {})
    draft_goal["goal_type"] = goal_type_name
    draft_goal.setdefault("description", content)
    draft_goal.setdefault("currency", "usd")
    # Record the device timezone on the draft, exactly as the awaiting-reply
    # path does. Without this an opening prompt that specifies everything
    # created the goal with timezone "UTC" even though the client told us
    # otherwise, so every later deadline comparison used the wrong clock.
    if tz_name and _is_missing_value(draft_goal.get("timezone")):
        draft_goal["timezone"] = tz_name

    title = _extract_title(content, goal_type_name)
    if title and _is_missing_value(draft_goal.get("title")):
        draft_goal["title"] = title

    pledge_match = re.search(
        r"(?:pledge\s+)?\$(\d+(?:\.\d{1,2})?)\b", content, re.IGNORECASE
    )
    if pledge_match and _is_missing_value(draft_goal.get("pledge_amount")):
        draft_goal["pledge_amount"] = int(round(float(pledge_match.group(1)) * 100))

    deadline = _extract_deadline(content, tz_name)
    if deadline and _is_missing_value(draft_goal.get("deadline")):
        draft_goal["deadline"] = deadline

    criteria = dict(draft_goal.get("criteria") or {})
    if goal_type_name == "youtube_video":
        if title and _is_missing_value(criteria.get("video_description")):
            criteria["video_description"] = title
        duration = _parse_duration_seconds(content)
        if duration is not None and _is_missing_value(
            criteria.get("min_duration_seconds")
        ):
            criteria["min_duration_seconds"] = duration
    elif goal_type_name == "github_repo":
        # Honour a criterion the user explicitly *quantified* ("open a PR with at
        # least 3 commits") so we only ask when the prompt named nothing
        # checkable.
        #
        # Deliberately narrowed to ``min_commits``. Inferring ``required_files``
        # or ``require_pr`` from unsolicited prose reads intent that isn't there,
        # and because a missing required file is a chargeable FAILURE, a wrong
        # guess bills someone who did the work:
        #
        #   "Update README by Friday"          -> required_files: ["README"]
        #        the repo file is README.md, so verification 404s -> charged
        #   "Migrate away from requirements.txt to pyproject.toml"
        #                                      -> required_files: [both]
        #        the goal is to DELETE that file; unsatisfiable by construction
        #   "Push my PR-review fixes by Friday" -> require_pr: True
        #        no PR is opened, so the check fails -> charged
        #
        # Worse, ``_compute_missing_criteria`` then reports nothing missing, so
        # the assistant says "you're ready to create" without ever naming the
        # commitment it just invented. A quantity next to the word "commits" is a
        # statement; a filename mentioned in passing is not. When the prompt does
        # not quantify anything we ask, and the user's answer to that question is
        # honoured in full by ``_apply_reply_to_draft`` — which is the right place
        # to read a filename, because there we asked for one.
        # ``required_files`` is excluded from prose entirely: a filename in a
        # sentence carries no reliable signal about whether it must EXIST. The
        # other two are gated on explicit intent — a quantity beside "commits",
        # and a verb beside "PR" (``_PR_INTENT_RE``) — so the goal type's own
        # sample prompt still works without inventing commitments.
        stated = _parse_repo_criteria(content)
        for key in ("min_commits", "require_pr"):
            if key in stated and not _is_criterion_set(criteria.get(key)):
                criteria[key] = stated[key]

    if criteria:
        draft_goal["criteria"] = criteria
    elif "criteria" not in draft_goal:
        draft_goal["criteria"] = {}

    return draft_goal


# Fields the chat must collect before a goal can be created. charity_id is
# deliberately NOT here: a recipient is optional — without one the failed
# pledge is still charged and simply held by the platform, so the chat must
# not block goal creation on picking a charity. (It can still be set when
# the user volunteers one; extraction keeps understanding the field.)
_CONVERSATIONAL_GOAL_FIELDS = (
    "title",
    "description",
    "deadline",
    "pledge_amount",
    "currency",
)


_GOAL_TYPE_CONFIRMATION_RE = re.compile(
    r"^use this goal type(?:\s*:\s*(?P<goal_type>[a-z0-9_]+))?$",
    re.IGNORECASE,
)


_AWAITING_INPUT_PROMPTS = {
    "title": "What should I call this goal?",
    "description": "How should I describe this goal?",
    "deadline": "What's your deadline?",
    "pledge_amount": "How much do you want to pledge?",
    "currency": "Which currency should I use for the pledge?",
    "charity_id": "Which charity should receive the pledge if you miss it?",
    "video_description": "What should the video cover?",
    # Name a unit: a bare number is read as seconds, so "5" would set a
    # five-second minimum when the user meant five minutes.
    "min_duration_seconds": "How long should the video be at minimum? (e.g. 5 minutes)",
    "url": "What's the API endpoint URL?",
    "method": "Which HTTP method should I check?",
    "expected_status": "Which HTTP status should count as success?",
    "repo_url": "What's the repository URL?",
    "test_command": "What test command should I run?",
    "repo_owner": "What's the GitHub repo owner?",
    "repo_name": "What's the GitHub repo name?",
    "min_commits": (
        "How many commits should I check for? (Or name a file that has to "
        "exist, or say a PR is required — I need at least one of those to "
        "check anything.)"
    ),
    "required_files": "Which files have to exist in the repo? (e.g. README.md, src/main.py)",
    "require_pr": "Should I require an open or merged pull request? Say yes and I'll check for one.",
    "target_latitude": "What's the latitude of the place you need to be? (You can copy coordinates from Google Maps.)",
    "target_longitude": "And the longitude?",
    "radius_m": "How close do you need to be, in meters? (I'll use 150m if you skip this.)",
}


def _criteria_schema_for(goal_type_name: str) -> dict:
    """Return a goal type's criteria schema, or ``{}`` for an unknown type."""
    try:
        return get_registry_type(goal_type_name).criteria_schema
    except KeyError:
        return {}


def _is_criterion_set(value: object) -> bool:
    """True when *value* configures something a verifier can actually check.

    Deliberately stricter than :func:`_is_missing_value`: an "at least one of
    these" criteria contract is only satisfied by a criterion that expresses a
    real requirement, and the verifiers treat ``0``, ``[]`` and ``False`` as
    configuring nothing — ``app/workers/github_repo.py`` calls exactly those
    "inert" and fails the goal when nothing runnable is left. Counting them as
    collected would let the chat create a goal that can never pass.
    """
    if value is None:
        return False
    # bool before int — bool is a subclass of int, and require_pr=False asks
    # for no check at all.
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    if isinstance(value, (int, float)):
        return value > 0
    return True


def _unsatisfied_any_of_field(criteria_schema: dict, criteria_data: dict) -> str | None:
    """Return one field to collect when a schema's ``anyOf`` contract is unmet.

    A goal type declares "at least one of these criteria is required" as
    ``anyOf: [{"required": ["a"]}, {"required": ["b"]}]``. ``github_repo`` does,
    because its verifier refuses to certify a goal whose criteria name only the
    repo — plain ``required`` cannot express a choice, so without this the chat
    would happily create a goal that is impossible to ever satisfy.

    Read off the schema rather than per goal type: any type that adopts the
    pattern is enforced automatically, and no goal-type name is hardcoded here
    to rot.

    Returns ``None`` when the schema declares no such contract or one
    alternative is already satisfied. Otherwise returns the field the assistant
    should ask for, preferring one we have prompt text for so we never ask
    about an alternative the chat cannot collect (e.g. the legacy
    ``conditions`` list).
    """
    branches = criteria_schema.get("anyOf")
    if not isinstance(branches, list):
        return None

    alternatives: list[list[str]] = []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        fields = [f for f in branch.get("required", []) if isinstance(f, str)]
        if fields:
            alternatives.append(fields)
    if not alternatives:
        return None

    for fields in alternatives:
        if all(_is_criterion_set(criteria_data.get(field)) for field in fields):
            return None

    for fields in alternatives:
        for field in fields:
            if field in _AWAITING_INPUT_PROMPTS:
                return field
    return alternatives[0][0]


def _compute_missing_criteria(draft_goal: dict, *, goal_type_name: str) -> list[str]:
    """Compute conversationally meaningful missing fields for a matched goal.

    The API returns field names the assistant can ask about next, not the
    wrapper ``criteria`` object itself. We therefore cover the top-level goal
    payload the chat is responsible for collecting, then expand the required
    criteria object into its goal-type-specific fields, then honour any
    ``anyOf`` "at least one of these" contract the schema declares.
    """
    missing: list[str] = []

    for field in _CONVERSATIONAL_GOAL_FIELDS:
        if _is_missing_value(draft_goal.get(field)):
            missing.append(field)

    try:
        goal_type = get_registry_type(goal_type_name)
    except KeyError:
        return missing

    criteria_data = draft_goal.get("criteria")
    if not isinstance(criteria_data, dict):
        criteria_data = {}

    for field in goal_type.criteria_schema.get("required", []):
        if _is_missing_value(criteria_data.get(field)):
            missing.append(field)

    any_of_field = _unsatisfied_any_of_field(goal_type.criteria_schema, criteria_data)
    if any_of_field is not None and any_of_field not in missing:
        missing.append(any_of_field)

    return missing


def _resolve_confirmation_goal_type(
    messages: list[dict], content: str, draft_goal: dict | None
) -> str | None:
    match = _GOAL_TYPE_CONFIRMATION_RE.fullmatch(content.strip())
    if not match:
        return None

    previous_assistant = next(
        (
            message
            for message in reversed(messages)
            if message.get("role") == "assistant"
        ),
        None,
    )
    action = previous_assistant.get("action") if previous_assistant else None
    if not isinstance(action, dict) or action.get("type") != "match_proposed":
        return None

    proposed_goal_type = action.get("goal_type")
    selected_goal_type = match.group("goal_type") or (
        draft_goal.get("goal_type") if isinstance(draft_goal, dict) else None
    )
    if (
        proposed_goal_type
        and selected_goal_type
        and selected_goal_type != proposed_goal_type
    ):
        return None
    return selected_goal_type or proposed_goal_type


def _build_awaiting_input_message(field: str) -> dict:
    prompt = _AWAITING_INPUT_PROMPTS.get(field, f"What's the value for {field}?")
    return {
        "role": "assistant",
        "content": prompt,
        "action": {
            "type": "awaiting_input",
            "field": field,
            "prompt": prompt,
        },
    }


def _build_match_catalog() -> list[CatalogEntry]:
    """Build a registry-backed catalog for the per-turn chat matcher."""
    return [
        CatalogEntry(
            name=goal_type.name,
            description=goal_type.description,
            sample_prompts=list(goal_type.sample_prompts),
        )
        for goal_type in (get_registry_type(name) for name in list_registry_types())
    ]


# ── Draft-filling helpers ─────────────────────────────────────────────


def _apply_reply_to_draft(
    draft_goal: dict,
    field: str,
    user_content: str,
    *,
    goal_type_name: str,
    tz_name: str | None = None,
) -> dict:
    """Apply a user's conversational reply to the appropriate draft field.

    Top-level goal fields (title, description, deadline, pledge_amount,
    currency, charity_id) are set directly. Goal-type-specific criteria
    fields are set inside ``draft_goal.criteria``.
    """
    updated = dict(draft_goal)

    if field in _CONVERSATIONAL_GOAL_FIELDS:
        if field == "pledge_amount":
            pledge_match = re.search(
                r"\$(\d+(?:\.\d{1,2})?)\b", user_content, re.IGNORECASE
            )
            if pledge_match:
                updated[field] = int(round(float(pledge_match.group(1)) * 100))
            else:
                try:
                    updated[field] = int(float(user_content.strip()) * 100)
                except (ValueError, TypeError):
                    updated[field] = 0
        elif field == "deadline":
            extracted = parse_deadline(user_content, tz_name) or _extract_deadline(
                user_content, tz_name
            )
            if extracted:
                updated[field] = extracted
            else:
                updated[field] = user_content.strip()
        else:
            updated[field] = user_content.strip()
    else:
        # Goal-type-specific criteria field
        criteria = dict(updated.get("criteria") or {})
        if field == "min_duration_seconds":
            seconds = _parse_duration_seconds(user_content)
            if seconds is not None:
                criteria[field] = seconds
            # Unreadable ("as long as it takes"): store nothing so the chat
            # re-asks. Storing the raw string used to reach the verifier as
            # ``int >= str`` — see _parse_duration_seconds.
        elif field in ("target_latitude", "target_longitude"):
            # Users paste whole coordinate pairs from Google Maps (decimal or
            # 35°53'53.4"N 78°56'27.9"W) in answer to either question — fill
            # both axes when a pair is present so we don't ask again. The map
            # picker also appends "(radius NNm)"; honor it when present.
            coords = parse_coordinates(user_content)
            lat, lng = coords["latitude"], coords["longitude"]
            if lat is not None and lng is not None and lat != lng:
                criteria["target_latitude"] = lat
                criteria["target_longitude"] = lng
            else:
                axis = "latitude" if field == "target_latitude" else "longitude"
                value = coords[axis]
                if value is not None:
                    criteria[field] = value
                # Unparseable ("somewhere downtown"): store nothing so the chat
                # re-asks. The stored string used to survive to the verifier,
                # which fails the goal — and charges — for a place we never
                # understood.
            radius_match = re.search(
                r"radius\s*[:=]?\s*(\d+(?:\.\d+)?)\s*m\b", user_content, re.IGNORECASE
            )
            if radius_match:
                criteria["radius_m"] = int(float(radius_match.group(1)))
        elif field == "radius_m":
            value = coerce_number(user_content)
            if value is not None:
                criteria[field] = int(value)
        elif field in _REPO_CRITERION_FIELDS:
            # Answers cross fields — "a README.md" is a valid reply to the
            # commits question — so store whatever the reply actually states.
            parsed = _parse_repo_criteria(user_content, asked_field=field)
            if parsed:
                criteria.update(parsed)
            # Nothing usable: store NOTHING rather than a value the verifier
            # would treat as inert. The field stays missing, so the next turn
            # re-asks instead of creating an unwinnable goal.
        else:
            # Every other criteria field is typed by its goal-type schema.
            # Coerce to that declared type — "200" for an integer field must
            # become 200, because the verifier compares with ``==`` and a
            # string never matches. An answer we cannot coerce without
            # guessing stores nothing, so the chat asks again.
            coerced = coerce_criteria_value(
                field,
                user_content,
                criteria_schema=_criteria_schema_for(goal_type_name),
            )
            if coerced is not UNUSABLE:
                criteria[field] = coerced
        updated["criteria"] = criteria

    return updated


def _apply_edit_from_message(
    draft_goal: dict,
    user_content: str,
    *,
    goal_type_name: str,
    tz_name: str | None = None,
) -> dict:
    """Apply an edit request from the user to the draft goal.

    Parses natural-language edit instructions like
    "change video_description to X" or "set deadline to Y".
    Falls back to re-extracting fields from the message content if no
    explicit edit pattern is matched.
    """
    updated = dict(draft_goal)

    change_match = re.search(
        r"(?:change|set|update)\s+(?:the\s+)?(\w+(?:_\w+)*)\s+(?:to|as)\s+(.+)",
        user_content,
        re.IGNORECASE,
    )
    if change_match:
        field_name = change_match.group(1).lower()
        new_value = change_match.group(2).strip(" .\"'")

        known_fields = set(_CONVERSATIONAL_GOAL_FIELDS) | set(
            _AWAITING_INPUT_PROMPTS.keys()
        )
        if field_name in known_fields:
            updated = _apply_reply_to_draft(
                updated,
                field_name,
                new_value,
                goal_type_name=goal_type_name,
                tz_name=tz_name,
            )
            return updated

    # Fallback: re-extract from the message content.
    #
    # tz_name is threaded through both branches above: the final-review screen is
    # the most likely place a user adjusts a deadline, and dropping the client
    # timezone here re-opened the bug the timezone work exists to close — a
    # US-Pacific user editing "deadline to 2026-09-03" got 23:59:59 UTC, i.e.
    # 16:59 local, and became chargeable ~7 hours before the day they named.
    return _extract_partial_goal_fields(
        user_content,
        goal_type_name=goal_type_name,
        existing_draft=updated,
        tz_name=tz_name,
    )


_REPHRASE_PATTERNS = re.compile(
    r"^(?:try\s+(?:another\s+approach|something\s+else)|let\s+me\s+rephrase)$",
    re.IGNORECASE,
)

_CREATE_CONFIRMATION_RE = re.compile(
    r"^(yes|yep|yeah|ok|okay|confirm|confirmed|create|create it|create goal|"
    r"create the goal|looks good|lgtm|go ahead|do it|sounds good)[.! ]*$",
    re.IGNORECASE,
)

_EDIT_PATTERNS = re.compile(
    r"^(?:edit|change|modify|update)\b",
    re.IGNORECASE,
)


def _classify_turn(
    messages: list[dict],
    content: str,
    draft_goal: dict | None,
) -> str:
    """Classify the current user turn based on prior assistant action.

    Returns one of:
    - "new_match" — no prior structured state; run the matcher
    - "confirm_match" — user is confirming a match_proposed
    - "rephrase" — user wants to abandon match and rephrase
    - "awaiting_reply" — user is replying to an awaiting_input prompt
    - "edit" — user wants to edit from ready_to_create
    - "confirm_create" — user wants to create the goal from ready_to_create
    """
    content_lower = content.strip().lower()

    # Find the last assistant action
    last_assistant_action = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            action = msg.get("action")
            if isinstance(action, dict) and action.get("type") is not None:
                last_assistant_action = action
            break

    if last_assistant_action is None:
        return "new_match"

    action_type = last_assistant_action.get("type")

    if action_type == "match_proposed":
        if _GOAL_TYPE_CONFIRMATION_RE.fullmatch(content.strip()):
            return "confirm_match"
        if _REPHRASE_PATTERNS.match(content_lower):
            return "rephrase"
        # Freeform after match_proposed — try matching again
        return "new_match"

    if action_type == "awaiting_input":
        return "awaiting_reply"

    if action_type == "ready_to_create":
        if _EDIT_PATTERNS.match(content_lower):
            return "edit"
        if _CREATE_CONFIRMATION_RE.fullmatch(content_lower):
            return "confirm_create"
        # Freeform after ready_to_create is a change request, not consent —
        # only an explicit confirmation creates the goal, so messages like
        # "actually make the deadline Friday" continue the edit flow.
        return "edit"

    return "new_match"


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: SendMessageBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Post a user message and receive an assistant response with optional structured action.

    Performs one LLM match call against the goal-type catalog and returns:
    - match_proposed action when confidence >= threshold
    - no_match action when below threshold or 'none'
    - a plain assistant retry message (with 502 status) on upstream failure

    After a match is confirmed, the endpoint drives a conversational
    draft-filling state machine: awaiting_input → criterion filling →
    ready_to_create → user confirmation.
    """
    session = await _get_session_or_404(db, session_id, current_user.id)

    user_msg = {"role": "user", "content": body.content, "action": None}

    # Persist user message
    messages = list(session.messages) + [user_msg]
    session.messages = messages
    session.last_activity_at = datetime.now(timezone.utc)

    # ── Edit follow-up: draft has _editing flag set ─────────────────
    # Check this BEFORE _classify_turn so the edit follow-up turn is
    # routed directly to _apply_edit_from_message instead of falling
    # through to the new-match path.
    if isinstance(session.draft_goal, dict) and session.draft_goal.get("_editing"):
        draft_goal = dict(session.draft_goal)
        draft_goal.pop("_editing", None)
        goal_type_name = draft_goal.get("goal_type", "")
        draft_goal = _apply_edit_from_message(
            draft_goal,
            body.content,
            goal_type_name=goal_type_name,
            tz_name=body.timezone,
        )
        # Recompute missing criteria after edit — the edit may have
        # cleared a required field, so we must not emit ready_to_create
        # until all criteria are satisfied again.
        missing = _compute_missing_criteria(draft_goal, goal_type_name=goal_type_name)
        next_field = missing[0] if missing else None
        assistant_msg = (
            _build_awaiting_input_message(next_field)
            if next_field
            else {
                "role": "assistant",
                "content": "Everything looks good — you're ready to create this goal.",
                "action": {
                    "type": "ready_to_create",
                    "goal_payload": draft_goal,
                },
            }
        )
        session.messages = list(session.messages) + [assistant_msg]
        session.draft_goal = draft_goal
        await db.commit()
        return {
            "messages": session.messages,
            "draft_goal": session.draft_goal,
        }

    turn = _classify_turn(
        session.messages[:-1],  # messages before the current user message
        body.content,
        session.draft_goal,
    )

    # ── Rephrase path ──────────────────────────────────────────────
    if turn == "rephrase":
        session.draft_goal = None
        assistant_msg = {
            "role": "assistant",
            "content": "Okay, tell me what you'd like to do instead.",
            "action": None,
        }
        session.messages = list(session.messages) + [assistant_msg]
        await db.commit()
        return {
            "messages": session.messages,
            "draft_goal": session.draft_goal,
        }

    # ── Awaiting-input reply path ──────────────────────────────────
    if turn == "awaiting_reply":
        # Determine the field being asked for
        last_action = None
        for msg in reversed(session.messages[:-1]):
            if msg.get("role") == "assistant":
                action = msg.get("action")
                if isinstance(action, dict) and action.get("type") == "awaiting_input":
                    last_action = action
                    break

        if last_action and isinstance(session.draft_goal, dict):
            field = last_action["field"]
            goal_type_name = session.draft_goal.get("goal_type", "")
            draft_goal = _apply_reply_to_draft(
                dict(session.draft_goal),
                field,
                body.content,
                goal_type_name=goal_type_name,
                tz_name=body.timezone,
            )
            if body.timezone and not draft_goal.get("timezone"):
                # Record the device timezone on the goal itself.
                draft_goal["timezone"] = body.timezone
            missing = _compute_missing_criteria(
                draft_goal, goal_type_name=goal_type_name
            )
            next_field = missing[0] if missing else None
            assistant_msg = (
                _build_awaiting_input_message(next_field)
                if next_field
                else {
                    "role": "assistant",
                    "content": "Everything looks good — you're ready to create this goal.",
                    "action": {
                        "type": "ready_to_create",
                        "goal_payload": draft_goal,
                    },
                }
            )
            session.messages = list(session.messages) + [assistant_msg]
            session.draft_goal = draft_goal
            await db.commit()
            return {
                "messages": session.messages,
                "draft_goal": session.draft_goal,
            }

        # Fallthrough — if no awaiting_input context found, run the matcher
        turn = "new_match"

    # ── Edit path (from ready_to_create) ───────────────────────────
    if turn == "edit":
        if isinstance(session.draft_goal, dict):
            draft_goal = dict(session.draft_goal)
            # Set _editing flag so the next non-edit turn knows we were editing
            draft_goal["_editing"] = True
            assistant_msg = {
                "role": "assistant",
                "content": "What would you like to change?",
                "action": None,
            }
            session.messages = list(session.messages) + [assistant_msg]
            session.draft_goal = draft_goal
            await db.commit()
            return {
                "messages": session.messages,
                "draft_goal": session.draft_goal,
            }

    # ── Confirm-create path ────────────────────────────────────────
    if turn == "confirm_create":
        # The create-goal endpoint handles actual creation; here we just
        # acknowledge and remind the user to use the create button.
        # The frontend is expected to call POST /create-goal on "Create goal".
        assistant_msg = {
            "role": "assistant",
            "content": (
                "Ready to create! Tap 'Create goal' to finalize, "
                "or 'Edit' to make changes."
            ),
            "action": session.messages[-2]["action"]
            if len(session.messages) >= 2
            and session.messages[-2].get("role") == "assistant"
            and isinstance(session.messages[-2].get("action"), dict)
            and session.messages[-2]["action"].get("type") == "ready_to_create"
            else {
                "type": "ready_to_create",
                "goal_payload": session.draft_goal,
            },
        }
        session.messages = list(session.messages) + [assistant_msg]
        await db.commit()
        return {
            "messages": session.messages,
            "draft_goal": session.draft_goal,
        }

    # ── Confirm-match path ─────────────────────────────────────────
    if turn == "confirm_match":
        confirmation_goal_type = _resolve_confirmation_goal_type(
            session.messages[:-1],
            body.content,
            session.draft_goal,
        )
        if confirmation_goal_type and isinstance(session.draft_goal, dict):
            draft_goal = dict(session.draft_goal)
            draft_goal.setdefault("goal_type", confirmation_goal_type)
            missing = _compute_missing_criteria(
                draft_goal, goal_type_name=confirmation_goal_type
            )
            next_field = missing[0] if missing else None
            assistant_msg = (
                _build_awaiting_input_message(next_field)
                if next_field
                else {
                    "role": "assistant",
                    "content": "Everything looks good — you're ready to create this goal.",
                    "action": {
                        "type": "ready_to_create",
                        "goal_payload": draft_goal,
                    },
                }
            )
            session.messages = list(session.messages) + [assistant_msg]
            session.draft_goal = draft_goal
            await db.commit()
            return {
                "messages": session.messages,
                "draft_goal": session.draft_goal,
            }

    # ── New-match path (default) ───────────────────────────────────
    # The _editing flag is handled earlier (before _classify_turn), so
    # we only reach here for genuine new-match / freeform turns.

    # Build prior chat context (exclude the current message)
    prior_context: list[dict[str, str]] = [
        {"role": m["role"], "content": m["content"]}
        for m in session.messages[:-1]
        if m.get("role") in ("user", "assistant")
    ]

    # Run the match
    catalog = _build_match_catalog()
    try:
        result = await match_message(
            body.content,
            chat_context=prior_context,
            threshold=settings.chat_match_confidence_threshold,
            catalog=catalog,
        )
    except ChatMatchError:
        # Persist a plain assistant retry message and commit before returning 502.
        retry_msg = {
            "role": "assistant",
            "content": "I'm having trouble understanding right now — try again?",
            "action": None,
        }
        session.messages = list(session.messages) + [retry_msg]
        await db.commit()
        return JSONResponse(
            status_code=502,
            content={
                "messages": session.messages,
                "draft_goal": session.draft_goal,
            },
        )

    if result.matched:
        goal_type_name = result.goal_type
        draft_goal = _extract_partial_goal_fields(
            body.content,
            goal_type_name=goal_type_name,
            existing_draft=session.draft_goal,
            tz_name=body.timezone,
        )
        missing = _compute_missing_criteria(draft_goal, goal_type_name=goal_type_name)

        assistant_msg = {
            "role": "assistant",
            "content": (
                f"Looks like this is a {goal_type_name} goal. "
                f"I'll need: {', '.join(missing) if missing else 'nothing else — you are ready to create!'}"
            ),
            "action": {
                "type": "match_proposed",
                "goal_type": goal_type_name,
                "confidence": result.confidence,
                "missing_criteria": missing,
            },
        }

        session.messages = list(session.messages) + [assistant_msg]
        session.draft_goal = draft_goal
    else:
        assistant_msg = {
            "role": "assistant",
            "content": (
                "I don't have a built-in way to verify that yet. "
                "Want me to build a new goal type for it?"
            ),
            "action": {
                "type": "no_match",
                "suggested_action": "generate_new_goal_type",
            },
        }
        session.messages = list(session.messages) + [assistant_msg]

    await db.commit()

    return {
        "messages": session.messages,
        "draft_goal": session.draft_goal,
    }


# ── Create-goal endpoint ─────────────────────────────────────────────


@router.post(
    "/sessions/{session_id}/create-goal",
    status_code=201,
    response_model=CreateGoalResponse,
)
async def create_goal_from_session(
    session_id: str,
    body: CreateGoalRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a goal from a chat session's draft payload.

    Delegates validation to the existing ``POST /api/goals`` contract
    (``GoalCreate``) and creates the goal through the shared
    ``create_goal`` service with a notification.

    Returns 404 when the session is not found or not owned by the user
    (no existence leak). Returns 422 when the session has not reached
    the confirmed ``ready_to_create`` state.
    """
    # Load session with unified 404 for nonexistent AND not-owned (no existence leak).
    try:
        session = await _get_session_or_404(db, session_id, current_user.id)
    except HTTPException as e:
        if e.status_code == 403:
            raise HTTPException(status_code=404, detail="Session not found.")
        raise

    # Verify the session is CURRENTLY in ready_to_create state: the
    # LATEST assistant action must be ready_to_create.  Any other
    # action (awaiting_input, match_proposed, no_match, or null) means
    # the session is not ready.  This prevents creating from a stale
    # pre-edit reviewed payload after the user chose Edit and the edit
    # follow-up produced another awaiting_input or ready_to_create.
    # Verify the LATEST assistant action is ready_to_create.
    latest_assistant_action_type = None
    for msg in reversed(session.messages):
        if msg.get("role") == "assistant":
            action = msg.get("action")
            if isinstance(action, dict):
                latest_assistant_action_type = action.get("type")
            break

    if latest_assistant_action_type != "ready_to_create":
        raise HTTPException(
            status_code=422,
            detail="Session has not reached ready_to_create state.",
        )

    # Use the CLIENT-SUBMITTED payload — the final-review screen may
    # carry user edits to presentation fields (title, description,
    # deadline, pledge_amount).  Validate it through GoalCreate.
    submitted_payload = dict(body.goal_payload)

    # Normalize criteria: the API spec defines the canonical shape as
    # {criteria_type, criteria_data}, but the GoalCreate schema accepts a
    # flat dict.  Accept both forms by unwrapping the canonical wrapper
    # when present, so downstream validation and persistence work against
    # the inner criteria_data dict.
    # Unwrap on ``criteria_data`` ALONE, not on both keys being present.
    # Requiring ``criteria_type`` too left a bypass of everything downstream: a
    # payload carrying only ``{"criteria_data": {...}}`` stayed wrapped, so the
    # coercion below saw ``criteria_data`` as a key the schema doesn't describe
    # and passed it through untouched, and the required-field check merged the
    # wrapped view so it passed as well. Net effect — the exact values this gate
    # exists to normalise (a string ``expected_status``) reached the database
    # verbatim, and the verifier then read an absent ``url``, failed, and charged
    # the user. Anything nested under ``criteria_data`` is criteria.
    if isinstance(submitted_payload.get("criteria"), dict):
        raw_criteria = submitted_payload["criteria"]
        if isinstance(raw_criteria.get("criteria_data"), dict):
            submitted_payload["criteria"] = raw_criteria["criteria_data"]
        elif "criteria_data" in raw_criteria:
            # Present but not a dict: refuse rather than guess. Passing it on
            # would put an uncoerced, unvalidated value into stored criteria.
            raise HTTPException(
                status_code=422,
                detail="criteria.criteria_data must be an object when supplied.",
            )

    # Forgiving normalization BEFORE schema validation: honest human input
    # ("7/18/2026 6am", pasted DMS coordinates) must become valid payload
    # values, not a pydantic 422 at the very last step of the chat flow.
    deadline_raw = submitted_payload.get("deadline")
    if isinstance(deadline_raw, str):
        try:
            datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
        except ValueError:
            parsed_deadline = parse_deadline(
                deadline_raw, submitted_payload.get("timezone")
            )
            if parsed_deadline:
                submitted_payload["deadline"] = parsed_deadline
    if isinstance(submitted_payload.get("criteria"), dict):
        criteria = dict(submitted_payload["criteria"])
        for axis_field, axis in (
            ("target_latitude", "latitude"),
            ("target_longitude", "longitude"),
        ):
            if isinstance(criteria.get(axis_field), str):
                coords = parse_coordinates(criteria[axis_field])
                lat, lng = coords["latitude"], coords["longitude"]
                if lat is not None and lng is not None and lat != lng:
                    criteria["target_latitude"] = lat
                    criteria["target_longitude"] = lng
                elif coords[axis] is not None:
                    criteria[axis_field] = coords[axis]
        if isinstance(criteria.get("radius_m"), str):
            radius = coerce_number(criteria["radius_m"])
            if radius is not None:
                criteria["radius_m"] = int(radius)

        # Then coerce every remaining value to the type its goal-type schema
        # declares. This is what stops a criteria value that can never match
        # from being persisted: the criteria dict here is stored verbatim as
        # ``goal_criteria.criteria_data``, so ``expected_status: "200"`` would
        # be compared against an int status forever and charge the user for a
        # goal they met. Refuse the request instead of guessing — the
        # conversation would never have produced these values.
        criteria_schema = _criteria_schema_for(submitted_payload.get("goal_type") or "")
        criteria, unusable_fields = coerce_criteria(
            criteria, criteria_schema=criteria_schema
        )
        if unusable_fields:
            details = ", ".join(
                f"{field} expects {describe_expected_type(field, criteria_schema)}"
                for field in unusable_fields
            )
            raise HTTPException(
                status_code=422,
                detail=f"Invalid criteria value: {details}.",
            )
        submitted_payload["criteria"] = criteria

    # Validate through the canonical GoalCreate schema (422 on failure).
    try:
        goal_data = GoalCreate(**submitted_payload)
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid goal payload: {e}",
        )

    # Consistency check against the confirmed server-side draft: the
    # goal_type must match the draft's matched type, and all type-required
    # criteria fields must be present.  Presentation fields MAY differ.
    draft_goal_type = (
        (session.draft_goal or {}).get("goal_type")
        if isinstance(session.draft_goal, dict)
        else None
    )
    if draft_goal_type and goal_data.goal_type != draft_goal_type:
        raise HTTPException(
            status_code=422,
            detail=(
                f"goal_type '{goal_data.goal_type}' does not match the "
                f"confirmed session goal type '{draft_goal_type}'."
            ),
        )

    # Verify all type-required criteria fields are present in the
    # (now-normalized) submitted payload.
    try:
        goal_type = get_registry_type(goal_data.goal_type)
    except KeyError:
        pass  # If the type isn't in the registry, skip criteria checks
    else:
        required_criteria_fields = goal_type.criteria_schema.get("required", [])
        # Canonical GoalCreate payloads wrap plugin fields under
        # criteria["criteria_data"]; older flat payloads carry them directly
        # on criteria. Accept both by merging the wrapped view over the flat
        # view before checking required fields.
        raw_criteria = (
            goal_data.criteria if isinstance(goal_data.criteria, dict) else {}
        )
        wrapped = (
            raw_criteria.get("criteria_data")
            if isinstance(raw_criteria.get("criteria_data"), dict)
            else {}
        )
        submitted_criteria = {**raw_criteria, **wrapped}
        for field in required_criteria_fields:
            if _is_missing_value(submitted_criteria.get(field)):
                raise HTTPException(
                    status_code=422,
                    detail=f"Missing required criteria field: {field}",
                )

        # The payload is client-submitted, so the conversational anyOf gate is
        # not enough on its own: refuse a payload that names no checkable
        # criterion, which would create a goal the verifier can only ever fail.
        unsatisfied = _unsatisfied_any_of_field(
            goal_type.criteria_schema, submitted_criteria
        )
        if unsatisfied is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Criteria must set at least one checkable requirement "
                    f"(e.g. {unsatisfied}); without one the goal can never be "
                    "verified."
                ),
            )

    # Create goal through the shared service using the CLIENT payload. The
    # service rejects an active goal whose deadline is already in the past
    # (which would otherwise fail on the very next deadline sweep) — surface
    # that as a 422 rather than a 500.
    try:
        goal = await create_goal(db, current_user.id, goal_data, status="active")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await _create_goal_notification(db, current_user.id, goal)

    # Link session to goal and mark goal_created
    session.goal_id = goal.id
    session.status = "goal_created"
    session.last_activity_at = datetime.now(timezone.utc)
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return CreateGoalResponse(
        goal_id=str(goal.id),
        status=goal.status,
    )


async def _create_goal_notification(
    db: AsyncSession,
    user_id: uuid.UUID,
    goal: Goal,
) -> None:
    """Create a goal_created notification for the chat-created goal."""
    from app.services.notification import create_notification

    await create_notification(
        db,
        user_id=user_id,
        notification_type="goal_created",
        title=f"Goal Created: {goal.title}",
        body=(
            f"Your goal '{goal.title}' with a pledge of "
            f"${goal.pledge_amount / 100:.2f} has been created."
        ),
        goal_id=goal.id,
    )
