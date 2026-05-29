"""Chat routes for goal creation through natural-language conversation.

Exposes endpoints defined in api_spec.md:
- POST /api/chat/sessions
- POST /api/chat/sessions/{session_id}/messages
- POST /api/chat/sessions/{session_id}/create-goal
- POST /api/chat/sessions/{session_id}/request-new-goal-type
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.chat import ChatSession
from app.models.user import User
from app.schemas.chat import (
    CreateGoalRequest,
    CreateGoalResponse,
    MessagesResponse,
    PostMessageRequest,
    RequestNewGoalTypeRequest,
    SessionResponse,
)
from app.schemas.goal import GoalCreate
from app.services.chat_match import (
    build_goal_payload,
    extract_pledge_amount,
    extract_title,
    get_criterion_prompt,
    get_missing_criteria,
    match_goal_type,
)
from app.services.goal import create_goal_with_notification

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _session_to_response(session: ChatSession) -> dict:
    return {
        "session_id": str(session.id),
        "messages": session.messages,
        "draft_goal": session.draft_goal,
        "status": session.status,
    }


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    greeting = {
        "role": "assistant",
        "content": "Tell me what you want to do, and I'll figure out how to track it.",
        "action": None,
    }
    session = ChatSession(
        user_id=current_user.id,
        messages=[greeting],
        status="active",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _session_to_response(session)


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session(db, session_id, current_user)
    return _session_to_response(session)


@router.post("/sessions/{session_id}/messages", response_model=MessagesResponse)
async def post_message(
    session_id: str,
    body: PostMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session(db, session_id, current_user)

    # Append user message
    user_msg = {"role": "user", "content": body.content, "action": None}
    messages = list(session.messages) if session.messages else []
    messages.append(user_msg)

    # Determine the next assistant response
    assistant_msg, updated_draft = await _process_turn(
        db, session, messages, body.content
    )

    messages.append(assistant_msg)
    session.messages = messages
    session.draft_goal = updated_draft
    await db.commit()
    await db.refresh(session)

    return MessagesResponse(
        messages=[_clean_action(m) for m in messages],
        draft_goal=session.draft_goal,
    )


@router.post("/sessions/{session_id}/create-goal", status_code=status.HTTP_201_CREATED)
async def create_goal_from_chat(
    session_id: str,
    body: CreateGoalRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session(db, session_id, current_user)

    # When the session has a conversational draft, verify the submitted
    # payload is consistent with it so chat state is not bypassed.
    if session.draft_goal:
        draft = session.draft_goal
        submitted = body.goal_payload
        for key in ("goal_type", "title", "pledge_amount", "currency"):
            if key in draft and key in submitted and submitted[key] != draft[key]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Submitted {key} does not match conversational draft",
                )

    # Delegate validation to the existing POST /api/goals contract by
    # instantiating GoalCreate with the user-supplied payload.  On failure
    # we raise RequestValidationError so FastAPI returns the standard 422
    # format, matching the canonical goal creation endpoint.
    try:
        goal_create = GoalCreate(**body.goal_payload)
    except ValidationError as e:
        raise RequestValidationError(e.errors())

    # Create goal + notification through the same shared path used by
    # POST /api/goals, then activate via the canonical state machine
    # (chat-confirmed goals skip the draft review step).
    goal = await create_goal_with_notification(db, current_user.id, goal_create)

    from app.schemas.goal import GoalUpdate
    from app.services.goal import update_goal
    goal = await update_goal(db, goal, GoalUpdate(status="active"))

    session.status = "goal_created"
    await db.commit()
    await db.refresh(session)

    return CreateGoalResponse(goal_id=str(goal.id), status=goal.status)


@router.post("/sessions/{session_id}/request-new-goal-type")
async def request_new_goal_type(
    session_id: str,
    body: RequestNewGoalTypeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Enforce ownership so an authenticated user cannot mutate another
    # user's session state.  The spec does not list 403, but leaking
    # session existence is the lesser concern vs cross-user mutation.
    await _get_session(db, session_id, current_user)

    # Stub: return 501 without persisting any state.  D010 will replace
    # this with real goal-type generation that writes draft_goal + status.
    raise HTTPException(
        status_code=501,
        detail="Goal-type generation is delivered in D010",
    )


# ── helpers ──────────────────────────────────────────────────────────


async def _get_session(
    db: AsyncSession, session_id: str, current_user: User
) -> ChatSession:
    """Fetch a chat session by id, verifying ownership."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    result = await db.execute(
        select(ChatSession).where(ChatSession.id == sid)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )
    if str(session.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session not owned by user",
        )
    return session


def _clean_action(msg: dict) -> dict:
    """Ensure action is JSON-serializable (convert None to null)."""
    return {
        "role": msg["role"],
        "content": msg["content"],
        "action": msg.get("action"),
    }


async def _process_turn(
    db: AsyncSession,
    session: ChatSession,
    messages: list[dict],
    user_content: str,
) -> tuple[dict, dict | None]:
    """Process a user turn and return (assistant_msg, updated_draft).

    Implements the state machine:
    1. If no match yet → run matching, return match_proposed or no_match
    2. If match accepted (match_proposed with user confirmation) →
       check missing criteria, return awaiting_input or ready_to_create
    3. If awaiting_input → update draft with user's reply,
       check remaining missing criteria
    """
    draft = dict(session.draft_goal) if session.draft_goal else {}

    # Determine if we're in a matched flow
    matched_type = draft.get("goal_type")

    # Check last assistant action to understand state
    last_assistant_action = None
    for m in reversed(messages[:-1]):  # exclude the just-appended user message
        if m["role"] == "assistant" and m.get("action"):
            last_assistant_action = m["action"]
            break

    if matched_type is None:
        # State 1: No match yet — run matching
        match_result = await match_goal_type(user_content)
        match_name = match_result["match"]
        confidence = match_result["confidence"]

        if match_name != "none" and confidence >= settings.chat_match_confidence_threshold:
            # Build initial draft from the user message with canonical
            # defaults for fields the chat won't prompt for (currency).
            draft = {
                "title": extract_title(user_content),
                "goal_type": match_name,
                "currency": "usd",
                "pledge_amount": extract_pledge_amount(user_content) or 0,
            }

            missing = get_missing_criteria(match_name, draft)

            return (
                {
                    "role": "assistant",
                    "content": f"Looks like this is a {match_name.replace('_', ' ').title()} goal. "
                    f"I'll need a few more details to set it up.",
                    "action": {
                        "type": "match_proposed",
                        "goal_type": match_name,
                        "confidence": confidence,
                        "missing_criteria": missing,
                    },
                },
                draft,
            )
        else:
            return (
                {
                    "role": "assistant",
                    "content": "I don't have a built-in way to verify that yet. "
                    "Want me to build a new goal type for it?",
                    "action": {
                        "type": "no_match",
                        "suggested_action": "generate_new_goal_type",
                    },
                },
                draft,
            )

    # State 2/3: We have a matched type
    if last_assistant_action and last_assistant_action["type"] == "match_proposed":
        # Require explicit confirmation before entering criteria
        # collection.  Non-confirmation messages re-trigger matching so
        # the user can rephrase or try a different description.
        if _is_confirmation(user_content):
            missing = get_missing_criteria(matched_type, draft)
            if missing:
                field = missing[0]
                return (
                    {
                        "role": "assistant",
                        "content": get_criterion_prompt(field),
                        "action": {
                            "type": "awaiting_input",
                            "field": field,
                            "prompt": get_criterion_prompt(field),
                        },
                    },
                    draft,
                )
            else:
                return _ready_to_create(draft)
        else:
            # User didn't confirm — re-run matching with the new input.
            match_result = await match_goal_type(user_content)
            match_name = match_result["match"]
            confidence = match_result["confidence"]

            if match_name != "none" and confidence >= settings.chat_match_confidence_threshold:
                draft = {
                    "title": extract_title(user_content),
                    "goal_type": match_name,
                    "currency": "usd",
                    "pledge_amount": extract_pledge_amount(user_content) or 0,
                }

                missing = get_missing_criteria(match_name, draft)
                return (
                    {
                        "role": "assistant",
                        "content": f"Looks like this is a {match_name.replace('_', ' ').title()} goal. "
                        f"I'll need a few more details to set it up.",
                        "action": {
                            "type": "match_proposed",
                            "goal_type": match_name,
                            "confidence": confidence,
                            "missing_criteria": missing,
                        },
                    },
                    draft,
                )
            else:
                return (
                    {
                        "role": "assistant",
                        "content": "I don't have a built-in way to verify that yet. "
                        "Want me to build a new goal type for it?",
                        "action": {
                            "type": "no_match",
                            "suggested_action": "generate_new_goal_type",
                        },
                    },
                    draft,
                )

    if last_assistant_action and last_assistant_action["type"] == "awaiting_input":
        # User just replied with a criterion value
        field = last_assistant_action["field"]
        value = user_content.strip()

        _apply_criterion_value(draft, field, value)

        # If pledge_amount not set yet, try to parse it
        if draft.get("pledge_amount", 0) == 0:
            pledge = extract_pledge_amount(user_content)
            if pledge:
                draft["pledge_amount"] = pledge

        # Check remaining missing criteria
        missing = get_missing_criteria(matched_type, draft)
        if missing:
            next_field = missing[0]
            return (
                {
                    "role": "assistant",
                    "content": get_criterion_prompt(next_field),
                    "action": {
                        "type": "awaiting_input",
                        "field": next_field,
                        "prompt": get_criterion_prompt(next_field),
                    },
                },
                draft,
            )
        else:
            return _ready_to_create(draft)

    # Fallback: if we have a match but can't determine state, check missing
    missing = get_missing_criteria(matched_type, draft)
    if missing:
        field = missing[0]
        return (
            {
                "role": "assistant",
                "content": get_criterion_prompt(field),
                "action": {
                    "type": "awaiting_input",
                    "field": field,
                    "prompt": get_criterion_prompt(field),
                },
            },
            draft,
        )
    else:
        return _ready_to_create(draft)


def _is_confirmation(content: str) -> bool:
    """Return True if *content* is an affirmative confirmation of a match."""
    import re as _re
    cleaned = _re.sub(r"[^\w\s]", "", content.strip().lower())
    affirmatives = {
        "yes", "yep", "yeah", "y", "ok", "okay", "sure", "use this",
        "use that", "use it", "yes use that", "yes use that goal type",
        "that works", "looks good", "good", "confirm", "confirmed",
        "lets go", "proceed", "go ahead", "go for it",
    }
    return cleaned in affirmatives


def _apply_criterion_value(draft: dict, field: str, value: str) -> None:
    """Apply a user-supplied value to the draft for the given criterion field.

    Parses the value according to the registry schema type so downstream
    GoalCreate validation receives correct types (int, bool, dict, etc.).
    """
    if field in ("deadline", "charity_id"):
        draft[field] = value
        return

    goal_type_name = draft.get("goal_type")
    criteria = dict(draft.get("criteria", {}) or {})
    criteria[field] = _parse_criterion_value(goal_type_name, field, value)
    draft["criteria"] = criteria


def _parse_criterion_value(
    goal_type_name: str | None, field: str, value: str
):
    """Parse a string criterion value according to the registry schema type."""
    if not goal_type_name:
        return value

    try:
        from app.goal_types import registry as goal_type_registry
        gt = goal_type_registry.get_type(goal_type_name)
        prop = gt.criteria_schema.get("properties", {}).get(field, {})
        field_type = prop.get("type", "string")
    except (KeyError, AttributeError):
        return value

    if field_type == "integer" or field_type == "number":
        try:
            return int(value)
        except ValueError:
            return value
    elif field_type == "boolean":
        cleaned = value.strip().lower()
        if cleaned in ("yes", "true", "1", "y"):
            return True
        elif cleaned in ("no", "false", "0", "n"):
            return False
        return value
    elif field_type == "object" or field_type == "array":
        import json as _json
        try:
            return _json.loads(value)
        except (_json.JSONDecodeError, TypeError):
            return value

    return value


def _ready_to_create(draft: dict) -> tuple[dict, dict]:
    """Build the ready_to_create assistant action."""
    payload = build_goal_payload(draft)
    return (
        {
            "role": "assistant",
            "content": "Here's a summary of your goal. Ready to create it?",
            "action": {
                "type": "ready_to_create",
                "goal_payload": payload,
            },
        },
        draft,
    )