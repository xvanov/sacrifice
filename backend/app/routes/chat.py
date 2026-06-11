import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.goal_types.registry import get_type, list_types
from app.models.chat_session import ChatSession
from app.models.user import User
from app.services.chat_match import match as chat_match

router = APIRouter(prefix="/api/chat", tags=["chat"])

GREETING = "Tell me what you want to do, and I'll figure out how to track it."


class ChatMessage(BaseModel):
    role: str
    content: str
    action: dict | None = None


class CreateChatSessionResponse(BaseModel):
    session_id: uuid.UUID
    messages: list[ChatMessage]
    status: str


class SendMessageBody(BaseModel):
    content: str


class SendMessageResponse(BaseModel):
    messages: list[ChatMessage]
    draft_goal: dict | None = None


class RequestNewGoalTypeBody(BaseModel):
    prompt_summary: str


async def _get_owned_session(
    session_id: str, current_user: User, db: AsyncSession
) -> ChatSession:
    """Fetch a chat session by id, verifying it exists and is owned by the user.

    Returns 404 for nonexistent sessions, 403 for sessions owned by others.
    """
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found")
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == sid)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Session not owned by user")
    return session


def _build_criteria_schema_map():
    """Return {goal_type_name: list of required criterion names} for every
    registered goal type."""
    result: dict[str, list[str]] = {}
    for name in list_types():
        try:
            gt = get_type(name)
        except KeyError:
            continue
        result[name] = gt.criteria_schema.get("required", [])
    return result


def _compute_missing_criteria(goal_type: str, draft_goal: dict | None) -> list[str]:
    """Return required criteria for *goal_type* that are not yet present in
    *draft_goal*."""
    schema_map = _build_criteria_schema_map()
    required = schema_map.get(goal_type, [])
    if not draft_goal:
        return list(required)
    criteria = draft_goal.get("criteria", {})
    return [c for c in required if c not in criteria or criteria[c] is None]


def _extract_draft_fields(user_message: str, goal_type: str) -> dict:
    """Heuristically extract draft goal fields from the user message.

    Returns a partial goal payload dict — best-effort extraction from free text.
    """
    draft: dict = {"goal_type": goal_type}

    lower = user_message.lower()

    # Try to extract dollar amount
    import re

    dollar_match = re.search(r"\$(\d[\d,]*)", user_message)
    if dollar_match:
        try:
            draft["pledge_amount"] = int(dollar_match.group(1).replace(",", ""))
        except ValueError:
            pass

    # Try to extract a title from the first sentence
    first_sentence = re.split(r"[.!?]", user_message)[0].strip()
    if first_sentence:
        draft["title"] = first_sentence

    # Try to find "by <date>" pattern for deadline
    # This is heuristic-only; real NL extraction comes later
    deadline_match = re.search(
        r"by\s+(?:(mon|tue|wed|thu|fri|sat|sun)[a-z]*day)", lower
    )
    if deadline_match:
        # Extremely rough: just note we found one
        pass

    return draft


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateChatSessionResponse,
)
async def create_chat_session(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = ChatSession(
        user_id=current_user.id,
        messages=[{"role": "assistant", "content": GREETING, "action": None}],
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


@router.post(
    "/sessions/{session_id}/messages",
    response_model=SendMessageResponse,
)
async def send_message(
    session_id: str,
    body: SendMessageBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Post a user message to a chat session, run goal-type matching, and
    return the assistant's response per `api_spec.md`."""
    # Validate non-empty content
    content = body.content
    if not content or not content.strip():
        raise HTTPException(
            status_code=422,
            detail="Content must not be empty or whitespace-only",
        )

    session = await _get_owned_session(session_id, current_user, db)

    # Build chat context from prior messages
    messages = list(session.messages) if session.messages else []

    # ── Persist user message ──
    user_msg = {"role": "user", "content": content.strip(), "action": None}
    messages.append(user_msg)
    session.messages = messages

    # ── Invoke match service ──
    try:
        match_result = await chat_match(content.strip(), chat_context=messages[:-1])
    except Exception:
        # Transient upstream LLM failure → 502
        await db.commit()  # still persist the user message
        raise HTTPException(
            status_code=502,
            detail="Upstream LLM service temporarily unavailable — retry",
        )

    match_name = match_result["match"]
    confidence = float(match_result["confidence"])
    threshold = settings.chat_match_confidence_threshold

    # ── Verify match is a known goal type ──
    known_types = set(list_types())
    if match_name not in known_types and match_name != "none":
        # Return as no-match rather than erroring
        match_name = "none"
        confidence = 0.0

    # ── Build draft_goal ──
    draft_goal: dict | None = None
    if match_name != "none" and confidence >= threshold:
        draft_goal = _extract_draft_fields(content.strip(), match_name)
        session.draft_goal = draft_goal

    # ── Build assistant response ──
    if match_name != "none" and confidence >= threshold:
        missing_criteria = _compute_missing_criteria(match_name, draft_goal)
        assistant_content = (
            f"Looks like this is a {match_name.replace('_', ' ')} goal."
        )
        if missing_criteria:
            assistant_content += (
                f" I'll need: {', '.join(missing_criteria).replace('_', ' ')}."
            )
        assistant_msg: dict = {
            "role": "assistant",
            "content": assistant_content,
            "action": {
                "type": "match_proposed",
                "goal_type": match_name,
                "confidence": confidence,
                "missing_criteria": missing_criteria,
            },
        }
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

    messages.append(assistant_msg)
    session.messages = messages
    session.updated_at = datetime.now(timezone.utc)

    await db.commit()

    return SendMessageResponse(
        messages=messages,
        draft_goal=draft_goal,
    )


@router.post("/sessions/{session_id}/request-new-goal-type")
async def request_new_goal_type(
    session_id: str,
    body: RequestNewGoalTypeBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_session(session_id, current_user, db)
    raise HTTPException(
        status_code=501,
        detail="Goal-type generation is delivered in D010",
    )
