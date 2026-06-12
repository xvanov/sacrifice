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
from app.services.chat_match import ChatMatchError, match as chat_match

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


async def _get_session_for_stub(
    session_id: str, current_user: User, db: AsyncSession
) -> ChatSession:
    """Fetch a chat session for a stub endpoint that only exposes 401/404/501.

    Per ``api_spec.md`` the ``request-new-goal-type`` endpoint must NOT return
    403 — non-owned sessions are indistinguishable from nonexistent ones (404).
    """
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found")
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == sid)
    )
    session = result.scalar_one_or_none()
    if session is None or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")
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

    Uses an explicit list of chat-required base fields derived from the
    ``GoalCreate`` contract plus the goal-type-specific ``criteria_schema.
    required`` fields.  ``charity_id`` is included even though it is optional
    at the schema level because the chat flow requires it conversationally
    per the story.
    """

    # ── Chat-required base fields (explicit per story / GoalCreate contract) ──
    # These are the top-level fields the chat must collect before creating a
    # goal.  ``goal_type`` is excluded because the match determines it.  The
    # ``criteria`` container is excluded because individual criterion fields
    # come from the goal type's criteria_schema.required.
    #
    # Fields drawn from GoalCreate required + charity_id (chat-required):
    CHAT_REQUIRED_BASE = {
        "title",
        "description",
        "deadline",
        "pledge_amount",
        "currency",
        "charity_id",
    }

    missing: list[str] = []

    if not draft_goal:
        missing.extend(CHAT_REQUIRED_BASE)
        schema_map = _build_criteria_schema_map()
        missing.extend(schema_map.get(goal_type, []))
        return missing

    # Top-level fields not yet extracted.
    for field in CHAT_REQUIRED_BASE:
        if field not in draft_goal or draft_goal[field] is None:
            missing.append(field)

    # Nested criteria fields from the goal type's criteria_schema.required
    schema_map = _build_criteria_schema_map()
    required_criteria = schema_map.get(goal_type, [])
    criteria = draft_goal.get("criteria", {}) or {}
    for c in required_criteria:
        if c not in criteria or criteria[c] is None:
            missing.append(c)

    return missing


def _extract_draft_fields(
    user_message: str,
    goal_type: str,
    chat_context: list[dict] | None = None,
    existing_draft: dict | None = None,
) -> dict:
    """Heuristically extract draft goal fields from the user message.

    Returns a partial goal payload dict — best-effort extraction from free text.
    Extracted criteria fields (where the message text suggests a value) are placed
    under ``criteria`` so that ``_compute_missing_criteria`` can exclude them.

    When *chat_context* is provided (prior assistant/user turns), those messages
    are also scanned for field values that may not appear in the current message.
    When *existing_draft* is provided, it is used as the starting point so new
    values merge on top rather than replacing previously extracted fields.
    """
    import re

    # ── Start from existing draft if provided ──
    draft: dict
    if existing_draft:
        draft = dict(existing_draft)  # shallow copy
        draft.setdefault("goal_type", goal_type)
    else:
        draft = {"goal_type": goal_type}
    criteria: dict = dict(draft.get("criteria", {})) if draft.get("criteria") else {}

    # ── Build a combined text corpus: chat_context + current message ──
    corpus = ""
    if chat_context:
        for m in chat_context:
            if isinstance(m, dict) and m.get("content"):
                corpus += m["content"] + "\n"
    corpus += user_message
    lower = corpus.lower()

    # ── Dollar / pledge amount ──
    if "pledge_amount" not in draft:
        dollar_match = re.search(r"\$(\d[\d,]*)", corpus)
        if dollar_match:
            try:
                parsed_dollars = int(dollar_match.group(1).replace(",", ""))
                draft["pledge_amount"] = parsed_dollars * 100  # cents
                draft["currency"] = "usd"
            except ValueError:
                pass

    # ── Title from first sentence of the LAST user message ──
    # (The current message is the most recent description of what the user wants.)
    first_sentence = re.split(r"[.!?]", user_message)[0].strip()
    if first_sentence:
        draft["title"] = first_sentence

    # ── Deadline extraction ──
    if "deadline" not in draft:
        deadline = _extract_deadline(corpus)
        if deadline:
            draft["deadline"] = deadline
            draft["timezone"] = "America/New_York"  # safe default

    # ── Charity ──
    # Look for "to <name>" after "pledge" or "donate", or "charity <name>"
    if "charity_id" not in draft:
        charity_match = re.search(
            r"(?:pledge|donate|donation)\s+(?:\$\d+\s+)?(?:to|for)\s+([A-Z][A-Za-z0-9 ]{2,30}?)(?:\s*(?:by\b|$|\.|,|\band\b\s*pledge|if\b|when\b))",
            corpus,
        )
        if not charity_match:
            # Simpler pattern: "to <charity-name>"
            charity_match = re.search(
                r"\bto\s+([A-Z][A-Za-z0-9'& ]{2,30}?)(?:\s*(?:by\b|$|\.|,|if\b))",
                corpus,
            )
        if charity_match:
            draft["charity_id"] = charity_match.group(1).strip()

    # ── Goal-type-specific criteria extraction ──
    if goal_type == "youtube_video":
        if "video_description" not in criteria:
            desc_match = re.search(
                r"(?:walkthrough|video|demo|showcase)\s+(?:of|about|for)\s+(.+?)(?:\s+by\s+|\s+and\s+pledge|\s+pledge|\s*$)",
                lower,
            )
            if desc_match:
                criteria["video_description"] = desc_match.group(1).strip().rstrip(".")
            else:
                desc_match2 = re.search(
                    r"upload\s+a\s+(?:youtube\s+)?(?:walkthrough|video|demo|showcase)\s+(?:of|about|for)?\s*(.+?)(?:\s+by\s+|\s+and\s+pledge|\s+pledge|\s*$)",
                    lower,
                )
                if desc_match2:
                    criteria["video_description"] = desc_match2.group(1).strip().rstrip(".")

    if criteria:
        draft["criteria"] = criteria

    return draft


def _extract_deadline(text: str) -> str | None:
    """Return an ISO-8601 datetime string if a deadline can be parsed from
    *text*, otherwise ``None``."""
    import re
    from datetime import date, datetime, timedelta

    today = date.today()

    # ── "by Friday", "by next Monday", "by tomorrow" ──
    day_names = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }

    # "by <day>"
    m = re.search(
        r"\b(?:by|due|deadline\s*(?:is)?)\s+(next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow)",
        text.lower(),
    )
    if m:
        has_next = bool(m.group(1))
        name = m.group(2)
        if name == "tomorrow":
            target = today + timedelta(days=1)
        else:
            wd = day_names[name]
            days_ahead = (wd - today.weekday() + 6) % 7 + 1  # next occurrence
            if has_next:
                days_ahead += 7
            target = today + timedelta(days=days_ahead)
        dt = datetime(target.year, target.month, target.day, 17, 0, 0)
        return dt.isoformat()

    # ── "by <month> <day>" e.g. "by March 15", "by Dec 5" ──
    m = re.search(
        r"\b(?:by|due|deadline\s*(?:is)?)\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})",
        text.lower(),
    )
    if m:
        month_map = {
            "jan": 1, "january": 1, "feb": 2, "february": 2,
            "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
            "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8,
            "august": 8, "sep": 9, "september": 9, "oct": 10,
            "october": 10, "nov": 11, "november": 11, "dec": 12,
            "december": 12,
        }
        month = month_map[m.group(1)]
        day = int(m.group(2))
        year = today.year
        # If the month has already passed, assume next year
        target_date = date(year, month, day)
        if target_date < today:
            target_date = date(year + 1, month, day)
        dt = datetime(target_date.year, target_date.month, target_date.day, 17, 0, 0)
        return dt.isoformat()

    # ── ISO date: "2026-03-15" ──
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 17, 0, 0)
        return dt.isoformat()

    return None


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
    except ChatMatchError:
        # Persist user message + assistant retry message so the conversation
        # record stays intact and the frontend can surface a retry affordance
        # when the client reloads the session after a transient failure.
        # action stays None: api_spec.md's action enum is CLOSED
        # (match_proposed/no_match/awaiting_input/ready_to_create/null) — the
        # frontend renders the retry card off the 502 status, per flow.md.
        from fastapi.responses import JSONResponse

        retry_msg = {
            "role": "assistant",
            "content": "I'm having trouble understanding right now — try again?",
            "action": None,
        }
        session.messages = messages + [retry_msg]
        session.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return JSONResponse(
            status_code=502,
            content={
                "messages": session.messages,
                "draft_goal": session.draft_goal,
                "detail": "Upstream LLM service temporarily unavailable — retry",
            },
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
        draft_goal = _extract_draft_fields(
            content.strip(),
            match_name,
            chat_context=prior_messages,
            existing_draft=session.draft_goal,
        )
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
    # Per api_spec.md this endpoint only exposes 401, 404, 501 — no 403.
    # Cross-user access is indistinguishable from nonexistent (404).
    await _get_session_for_stub(session_id, current_user, db)
    # Validate prompt_summary — the spec says this endpoint takes a meaningful
    # request body, so reject empty/whitespace input even as a stub.
    if not body.prompt_summary or not body.prompt_summary.strip():
        raise HTTPException(
            status_code=422,
            detail="prompt_summary must not be empty or whitespace-only",
        )
    raise HTTPException(
        status_code=501,
        detail="Goal-type generation is delivered in D010",
    )
