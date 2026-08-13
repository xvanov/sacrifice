"""Shared test helpers for D010 goal-generation focused suites.

These utilities are imported by the request, lifecycle, and persistence
test modules so that each suite stays focused on its slice of acceptance
behavior.
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models.chat_session import ChatSession
from app.models.user import User


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(
    client,
    email="test@example.com",
    name="Test User",
    sub="test-sub-123",
    token="valid-token",
):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def _ensure_session(client, session_id: str) -> str:
    """Create a ChatSession row for the given session_id, scoped to the
    authenticated user. Returns the user_id as a string.

    Call AFTER _auth() so the user exists and the token is valid.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as db_session:
        result = await db_session.execute(
            select(User).where(User.email == "test@example.com")
        )
        user = result.scalar_one()
        cs = ChatSession(session_id=session_id, user_id=user.id)
        db_session.add(cs)
        await db_session.commit()
    await engine.dispose()
    return str(user.id)


# Future deadlines: activating (and accepting a generated) goal requires a
# deadline beyond the minimum lead. Computed at import so these fixtures never rot
# as the wall clock advances past a hard-coded date.
_FUTURE_DEADLINE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

VALID_GOAL = {
    "title": "Ship the MVP",
    "description": "Launch the sacrifice app",
    "deadline": _FUTURE_DEADLINE,
    "pledge_amount": 5000,
    "goal_type": "youtube_video",
    "criteria": {
        "min_duration_seconds": 300,
        "video_description": "A walkthrough demo",
    },
    "charity_id": "acct_charity123",
}

GENERATION_REQUEST_BODY = {
    "prompt_summary": "Do 20 pushups every morning at 7am verified with my phone camera",
    "goal_payload_draft": {
        "title": "20 morning pushups",
        "description": "Do 20 pushups every morning at 7am, verified with my phone camera.",
        "pledge_amount": 1000,
        "currency": "usd",
        "deadline": _FUTURE_DEADLINE,
        "timezone": "America/New_York",
        "charity_id": None,
        "recurrence": "daily",
    },
}


def _derive_fake_slug(prompt_summary: str) -> str:
    """Derive a deterministic fake slug from the prompt content.

    The real ``synthesize_direction`` asks an LLM for a slug; in tests we
    inspect keyword patterns instead.
    """
    prompt_lower = prompt_summary.lower()
    # YouTube-related prompts get youtube-video-v2 so the E2E test can assert
    # the v2 module co-exists with and matches the existing youtube_video.
    if any(
        kw in prompt_lower
        for kw in ("youtube", "video", "link as proof", "building a feature")
    ):
        return "youtube-video-v2"
    # Pushup-related prompts get pushup-counter.
    if any(kw in prompt_lower for kw in ("pushup", "pushups", "phone camera")):
        return "pushup-counter"
    # Fallback for generic / vague prompts.
    return "pushup-counter"


def _fake_synthesis(prompt_summary="", chat_history=None):
    """Deterministic fake synthesis for tests — never calls an external LLM."""
    # Vague / underspecified prompts should be rejected (422) when no
    # force-generate bypass is in play, mirroring the LLM refusal path.
    prompt_lower = prompt_summary.lower().strip()
    vague_markers = (
        len(prompt_lower.split()) < 6,
        "when i'm done" in prompt_lower,
        "i will submit" in prompt_lower
        and "link" in prompt_lower
        and "video" not in prompt_lower
        and "youtube" not in prompt_lower,
        prompt_lower in ("", "help", "test", "asdf"),
    )
    if any(vague_markers):
        from app.services.direction_synth import DirectionSynthesisError

        raise DirectionSynthesisError("Prompt too vague to synthesize")

    slug = _derive_fake_slug(prompt_summary)
    title = " ".join(w.capitalize() for w in slug.split("-"))
    direction_md = f"""---
title: "{title}"
type: feature
why: "User requested verification for: {prompt_summary}"
acceptance:
  - "Create backend/app/goal_types/{slug}/ module conforming to the goal-type plugin base"
  - "Verifier accepts proof uploads and criteria_data payload"
  - "All fixture-based assertions pass"
---

# {title}

## Why
User needs a custom goal type for: {prompt_summary}

## Acceptance Criteria
1. Module created at `backend/app/goal_types/{slug}/`
2. Verifier correctly evaluates proof submissions
3. Tests pass with provided fixtures
"""
    return {
        "title": title,
        "slug": slug,
        "direction_md": direction_md,
        "flow_md": "# User flow\n\n1. Create goal\n2. Submit proof\n3. Verifier runs\n",
        "api_spec_md": "# API spec\n\nExisting endpoints apply.\n",
    }


@pytest.fixture
def temp_directions_path():
    """Override settings.directions_path with a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        original = settings.directions_path
        settings.directions_path = tmpdir
        yield Path(tmpdir)
        settings.directions_path = original


def _write_state_yaml(
    directions_root: Path,
    direction_id: str,
    status: str,
    pr_url: str | None = None,
    summary: str | None = None,
):
    """Write a state.yaml for a direction, creating the directory if needed."""
    direction_dir = directions_root / direction_id
    direction_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"status: {status}"]
    if pr_url:
        lines.append(f"pr_url: {pr_url}")
    else:
        lines.append("pr_url: null")
    if summary:
        lines.append(f"summary: {summary}")
    else:
        lines.append(f"summary: Direction is {status}.")
    (direction_dir / "state.yaml").write_text("\n".join(lines) + "\n")


# Async wrapper so the mock can be used with AsyncMock if needed.
# For now patch the module-level symbol so all callers get the fake.
@pytest.fixture(autouse=True)
def mock_synthesize_direction():
    """Globally mock synthesize_direction so no test hits a real LLM."""
    with patch("app.routes.chat.synthesize_direction", side_effect=_fake_synthesis):
        yield
