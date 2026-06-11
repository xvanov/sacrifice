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
    *draft_goal*.

    Derives the set of required fields from the actual ``GoalCreate`` schema
    (every field without a default that is not ``Optional``) plus goal-type-
    specific ``criteria_schema.required`` fields.  ``charity_id`` is also
    collected conversationally even though it is optional at the schema level
    (the story requires the chat to collect it).
    """
    from app.schemas.goal import GoalCreate

    # ── Determine which GoalCreate fields are truly required ──
    # A field is required if it has no default value and its type does not
    # include None.  Pydantic v2 encodes "no default" as PydanticUndefined.
    from pydantic.fields import PydanticUndefined

    TOP_LEVEL_REQUIRED: list[str] = []
    for field_name, field_info in GoalCreate.model_fields.items():
        default = field_info.default
        if default is not PydanticUndefined:
            continue  # has a default value → not required
        # Check if the annotation allows None
        annotation = field_info.annotation
        if annotation is not None:
            # If the annotation is Optional (Union[T, None]), it's not required
            import types
            origin = getattr(annotation, "__origin__", None)
            # Handle Union types: Union[X, None] → Optional
            if origin is not None:
                args = getattr(annotation, "__args__", ())
                if type(None) in args:
                    continue  # Optional → has implicit None default
        TOP_LEVEL_REQUIRED.append(field_name)

    # charity_id is optional in GoalCreate but required by the chat flow.
    if "charity_id" not in TOP_LEVEL_REQUIRED:
        TOP_LEVEL_REQUIRED.append("charity_id")

    missing: list[str] = []

    if not draft_goal:
        missing.extend(TOP_LEVEL_REQUIRED)
        schema_map = _build_criteria_schema_map()
        missing.extend(schema_map.get(goal_type, []))
        return missing

    # Top-level fields not yet extracted.
    # ``criteria`` is checked at the top level (key presence) AND at the
    # nested level (individual criterion fields from criteria_schema.required).
    for field in TOP_LEVEL_REQUIRED:
        if field == "criteria":
            # Top-level: the criteria dict must exist and be non-None / non-empty
            if "criteria" not in draft_goal or draft_goal["criteria"] is None:
                missing.append(field)
        elif field not in draft_goal or draft_goal[field] is None:
            missing.append(field)

    # Nested criteria fields from the goal type's criteria_schema.required
    schema_map = _build_criteria_schema_map()
    required_criteria = schema_map.get(goal_type, [])
    criteria = draft_goal.get("criteria", {}) or {}
    for c in required_criteria:
        if c not in criteria or criteria[c] is None:
            missing.append(c)

    return missing


def _extract_draft_fields(user_message: str, goal_type: str) -> dict:
    """Heuristically extract draft goal fields from the user message.

    Returns a partial goal payload dict — best-effort extraction from free text.
    Extracted criteria fields (where the message text suggests a value) are placed
    under ``criteria`` so that ``_compute_missing_criteria`` can exclude them.
    """
    import re

    draft: dict = {"goal_type": goal_type}

    lower = user_message.lower()

    # Try to extract dollar amount
    dollar_match = re.search(r"\$(\d[\d,]*)", user_message)
    if dollar_match:
        try:
            parsed_dollars = int(dollar_match.group(1).replace(",", ""))
            draft["pledge_amount"] = parsed_dollars * 100  # cents per api_spec.md
        except ValueError:
            pass

    # Try to extract a title from the first sentence
    first_sentence = re.split(r"[.!?]", user_message)[0].strip()
    if first_sentence:
        draft["title"] = first_sentence

    # ── Goal-type-specific criteria extraction ──
    criteria: dict = {}

    if goal_type == "youtube_video":
        # Extract video_description from descriptive phrases after "walkthrough",
        # "video", "demo", or "showcase"
        desc_match = re.search(
            r"(?:walkthrough|video|demo|showcase)\s+(?:of|about|for)\s+(.+?)(?:\s+by\s+|\s+and\s+pledge|\s+pledge|\s*$)",
            lower,
        )
        if desc_match:
            criteria["video_description"] = desc_match.group(1).strip().rstrip(".")
        else:
            # Fallback: capture the middle clause between "upload a" and "by/pledge"
            desc_match2 = re.search(
                r"upload\s+a\s+(?:youtube\s+)?(?:walkthrough|video|demo|showcase)\s+(?:of|about|for)?\s*(.+?)(?:\s+by\s+|\s+and\s+pledge|\s+pledge|\s*$)",
                lower,
            )
            if desc_match2:
                criteria["video_description"] = desc_match2.group(1).strip().rstrip(".")

    if criteria:
        draft["criteria"] = criteria

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

    # ── Capture prior context BEFORE appending the current turn ──
    prior_messages = list(session.messages) if session.messages else []

    # ── Persist user message ──
    user_msg = {"role": "user", "content": content.strip(), "action": None}
    messages = prior_messages + [user_msg]
    session.messages = messages

    # ── Invoke match service ──
    try:
        match_result = await chat_match(content.strip(), chat_context=prior_messages)
    except Exception:
        # Persist user message + assistant retry message so the conversation
        # record stays intact and the frontend retry card flow (flow.md) works
        # when the client reloads the session after a transient failure.
        retry_msg = {
            "role": "assistant",
            "content": "I'm having trouble understanding right now — try again?",
            "action": {"type": "retry"},
        }
        session.messages = messages + [retry_msg]
        session.updated_at = datetime.now(timezone.utc)
        await db.commit()
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
