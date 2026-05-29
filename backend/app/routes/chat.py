"""Chat routes for goal creation through natural-language conversation.

Exposes endpoints defined in api_spec.md:
- POST /api/chat/sessions
- POST /api/chat/sessions/{session_id}/messages
- POST /api/chat/sessions/{session_id}/create-goal
- POST /api/chat/sessions/{session_id}/request-new-goal-type
- GET /api/chat/sessions/{session_id}
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
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
from app.services.goal import create_goal

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

    # Delegate validation and creation to the canonical goal contract
    # (same GoalCreate schema + create_goal service as POST /api/goals).
    try:
        goal_create = GoalCreate(**body.goal_payload)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    goal = await create_goal(db, current_user.id, goal_create)

    # Mirror the notification side-effect of POST /api/goals.
    from app.services.notification import create_notification
    await create_notification(
        db,
        user_id=current_user.id,
        notification_type="goal_created",
        title=f"Goal Created: {goal.title}",
        body=(
            f"Your goal '{goal.title}' with a pledge of "
            f"${goal.pledge_amount / 100:.2f} has been created."
        ),
        goal_id=goal.id,
    )

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
    # Ownership and existence validated by _get_session.
    session = await _get_session(db, session_id, current_user)

    # Store the prompt summary so D010 can pick it up.
    session.draft_goal = {"prompt_summary": body.prompt_summary}
    session.status = "awaiting_goal_type"
    await db.commit()
    await db.refresh(session)

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
            # Build initial draft from the user message
            draft = {
                "title": extract_title(user_content),
                "goal_type": match_name,
            }
            pledge = extract_pledge_amount(user_content)
            if pledge:
                draft["pledge_amount"] = pledge
            else:
                draft["pledge_amount"] = 0

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
        # Distinguish confirmation from field input.
        # If the user sends an affirmative confirmation, enter criteria
        # collection.  Otherwise treat the message as a value for the
        # first missing criterion so the user's input is never discarded.
        is_confirmation = _is_confirmation(user_content)
        missing = get_missing_criteria(matched_type, draft)

        if not is_confirmation and missing:
            # User jumped straight to providing a value — apply it to the
            # first missing criterion and continue.
            field = missing[0]
            _apply_criterion_value(draft, field, user_content.strip())

            # If pledge_amount not set yet, try to parse it
            if draft.get("pledge_amount", 0) == 0:
                pledge = extract_pledge_amount(user_content)
                if pledge:
                    draft["pledge_amount"] = pledge

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

        # User confirmed — start collecting criteria
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
            # No criteria needed — ready to create
            return _ready_to_create(draft)

    if last_assistant_action and last_assistant_action["type"] == "awaiting_input":
        # User just replied with a criterion value
        field = last_assistant_action["field"]
        value = user_content.strip()

        # Update draft with the new value
        if field in ("deadline", "charity_id"):
            draft[field] = value
        else:
            # Goal-type specific criteria field
            criteria = dict(draft.get("criteria", {}) or {})
            criteria[field] = value
            draft["criteria"] = criteria

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
    affirmatives = {
        "yes", "yep", "yeah", "y", "ok", "okay", "sure", "use this",
        "use that", "use it", "yes use that", "yes use that goal type",
        "that works", "looks good", "good", "confirm", "confirmed",
        "let's go", "proceed", "go ahead", "go for it",
    }
    return content.strip().lower() in affirmatives


def _apply_criterion_value(draft: dict, field: str, value: str) -> None:
    """Apply a user-supplied value to the draft for the given criterion field."""
    if field in ("deadline", "charity_id"):
        draft[field] = value
    else:
        criteria = dict(draft.get("criteria", {}) or {})
        criteria[field] = value
        draft["criteria"] = criteria


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