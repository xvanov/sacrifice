"""The chat flow must never store a criteria value that makes verification impossible.

Each bug pinned here ends in the same place: `verification_result.py` fires a real
Stripe PaymentIntent the moment a verdict is `failed`, and the deadline sweep
(`app/workers/deadline.py`) charges a goal that never got a verdict at all. So a
criteria value the verifier cannot compare, or a deadline that lands earlier than
the user's own wall clock, is a charge for work they actually did.

Covers:
- `expected_status` stored as a string ("200" != 200 forever, silently)
- `min_duration_seconds` stored as a string ("5 minutes" never parsed; int >= str raises)
- coordinates stored as unparsed prose
- deadlines pinned to UTC instead of the user's timezone
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.main import app
from app.routes.chat import (
    _apply_reply_to_draft,
    _compute_missing_criteria,
    _extract_deadline,
    _extract_partial_goal_fields,
    _parse_duration_seconds,
)
from app.services.chat_match import MatchResult
from app.services.input_parsing import parse_deadline

_FUTURE_DATE = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()

LA = "America/Los_Angeles"
AUCKLAND = "Pacific/Auckland"
# A fixed date keeps the timezone assertions exact; the expected offsets are
# derived from ZoneInfo rather than hardcoded, so DST changes cannot rot them.
NAMED_DAY = "2026-08-01"


def _draft(goal_type: str, **criteria) -> dict:
    return {
        "goal_type": goal_type,
        "title": "A goal",
        "description": "d",
        "deadline": f"{_FUTURE_DATE}T23:59:59+00:00",
        "pledge_amount": 3000,
        "currency": "usd",
        "criteria": dict(criteria),
    }


# ── #1 api_endpoint.expected_status ───────────────────────────────────


@pytest.mark.parametrize(
    "reply,expected", [("200", 200), ("  200 ", 200), ("200 OK", 200), ("204", 204)]
)
def test_expected_status_reply_is_stored_as_an_integer(reply, expected):
    """`api_check.py` compares `response.status_code == expected_status`. Stored
    as a string it never matches, the verdict is `failed`, and the user is
    charged for an endpoint that did exactly what they promised."""
    updated = _apply_reply_to_draft(
        _draft("api_endpoint", url="https://x/health", method="GET"),
        "expected_status",
        reply,
        goal_type_name="api_endpoint",
    )

    result = updated["criteria"]["expected_status"]
    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize("reply", ["two hundred", "whatever", "abc"])
def test_unreadable_expected_status_is_not_stored_and_is_re_asked(reply):
    draft = _draft("api_endpoint", url="https://x/health", method="GET")

    updated = _apply_reply_to_draft(
        draft, "expected_status", reply, goal_type_name="api_endpoint"
    )

    assert "expected_status" not in updated["criteria"]
    assert "expected_status" in _compute_missing_criteria(
        updated, goal_type_name="api_endpoint"
    )


def test_string_criteria_fields_are_still_stored_as_typed_strings():
    """The coercion must not disturb the fields that were always fine."""
    updated = _apply_reply_to_draft(
        _draft("api_endpoint"),
        "url",
        "  https://x/health  ",
        goal_type_name="api_endpoint",
    )
    updated = _apply_reply_to_draft(
        updated, "method", "GET", goal_type_name="api_endpoint"
    )

    assert updated["criteria"]["url"] == "https://x/health"
    assert updated["criteria"]["method"] == "GET"


def test_unknown_goal_type_still_stores_the_reply():
    """A goal type not in the registry has no schema; the reply must survive
    rather than being dropped as uncoercible."""
    updated = _apply_reply_to_draft(
        _draft("__generated__"),
        "whatever",
        " some value ",
        goal_type_name="__generated__",
    )

    assert updated["criteria"]["whatever"] == "some value"


# ── #2 youtube_video.min_duration_seconds ─────────────────────────────


@pytest.mark.parametrize(
    "text_in,expected",
    [
        ("5 minutes", 300),
        ("5 minute", 300),
        ("5 mins", 300),
        ("5 min", 300),
        ("5-minute", 300),
        ("at least 10 minutes", 600),
        ("90 seconds", 90),
        ("90 secs", 90),
        ("2.5 minutes", 150),
        ("300", 300),
    ],
)
def test_duration_parsing_understands_plurals_and_units(text_in, expected):
    """The plural was the whole bug: the old pattern ended in `(minute|min)\\b`,
    so "5 minutes" fell through to storing the raw string."""
    assert _parse_duration_seconds(text_in) == expected


@pytest.mark.parametrize(
    "text_in", ["as long as it takes", "abc", "0", "0 minutes", ""]
)
def test_unreadable_duration_returns_none(text_in):
    assert _parse_duration_seconds(text_in) is None


def test_duration_reply_is_stored_as_seconds_not_prose():
    updated = _apply_reply_to_draft(
        _draft("youtube_video", video_description="a demo"),
        "min_duration_seconds",
        "5 minutes",
        goal_type_name="youtube_video",
    )

    result = updated["criteria"]["min_duration_seconds"]
    assert result == 300
    assert type(result) is int
    assert _compute_missing_criteria(updated, goal_type_name="youtube_video") == []


def test_unreadable_duration_is_not_stored_and_is_re_asked():
    draft = _draft("youtube_video", video_description="a demo")

    updated = _apply_reply_to_draft(
        draft,
        "min_duration_seconds",
        "as long as it takes",
        goal_type_name="youtube_video",
    )

    assert "min_duration_seconds" not in updated["criteria"]
    assert "min_duration_seconds" in _compute_missing_criteria(
        updated, goal_type_name="youtube_video"
    )


def test_opening_prompt_extracts_a_plural_duration():
    """The shipped sample prompt for this goal type. It extracted nothing."""
    draft = _extract_partial_goal_fields(
        "Record a 5-minute video explaining my refactor",
        goal_type_name="youtube_video",
    )

    assert draft["criteria"]["min_duration_seconds"] == 300


def test_opening_prompt_without_a_duration_extracts_none():
    draft = _extract_partial_goal_fields(
        f"I want to upload a YouTube walkthrough by {_FUTURE_DATE} and pledge $20",
        goal_type_name="youtube_video",
    )

    assert "min_duration_seconds" not in draft["criteria"]


# ── #3 geolocation coordinates ────────────────────────────────────────


@pytest.mark.parametrize("reply", ["somewhere downtown", "the gym", "abc"])
def test_unparseable_coordinates_are_not_stored_and_are_re_asked(reply):
    draft = _draft("geolocation")

    updated = _apply_reply_to_draft(
        draft, "target_latitude", reply, goal_type_name="geolocation"
    )

    assert "target_latitude" not in updated["criteria"]
    assert "target_latitude" in _compute_missing_criteria(
        updated, goal_type_name="geolocation"
    )


def test_a_pasted_dms_pair_still_fills_both_axes():
    """Regression guard: the useful behaviour must survive the fix."""
    updated = _apply_reply_to_draft(
        _draft("geolocation"),
        "target_latitude",
        "35°53'53.4\"N 78°56'27.9\"W",
        goal_type_name="geolocation",
    )

    assert updated["criteria"]["target_latitude"] == pytest.approx(35.898167, abs=1e-5)
    assert updated["criteria"]["target_longitude"] == pytest.approx(-78.940_8, abs=1e-3)
    assert _compute_missing_criteria(updated, goal_type_name="geolocation") == []


def test_a_decimal_pair_and_radius_still_parse():
    updated = _apply_reply_to_draft(
        _draft("geolocation"),
        "target_latitude",
        "35.898, -78.941 (radius 200m)",
        goal_type_name="geolocation",
    )

    assert updated["criteria"]["target_latitude"] == 35.898
    assert updated["criteria"]["target_longitude"] == -78.941
    assert updated["criteria"]["radius_m"] == 200


def test_unreadable_radius_is_not_stored():
    updated = _apply_reply_to_draft(
        _draft("geolocation", target_latitude=35.898, target_longitude=-78.941),
        "radius_m",
        "pretty close",
        goal_type_name="geolocation",
    )

    assert "radius_m" not in updated["criteria"]


# ── #5 deadlines in the user's timezone ───────────────────────────────


def _local(iso: str, tz_name: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz_name))


@pytest.mark.parametrize("tz_name", [LA, AUCKLAND, "UTC"])
def test_a_named_date_means_the_end_of_that_day_where_the_user_is(tz_name):
    """Pinned to UTC, "by 2026-08-01" landed at 16:59 local for a US-Pacific
    user — seven hours of the day they named, gone, with a real charge at the
    end of it — and gave an Auckland user twelve extra hours."""
    result = _extract_deadline(f"ship it by {NAMED_DAY}", tz_name)

    assert result is not None
    assert datetime.fromisoformat(result) == datetime(
        2026, 8, 1, 23, 59, 59, tzinfo=ZoneInfo(tz_name)
    )
    local = _local(result, tz_name)
    assert (local.hour, local.minute, local.second) == (23, 59, 59)
    assert local.date().isoformat() == NAMED_DAY


def test_pacific_user_keeps_their_whole_named_day():
    """The charge-side assertion: at 17:00 on the named day, Pacific time, the
    deadline must still be in the future."""
    result = _extract_deadline(f"by {NAMED_DAY}", LA)
    deadline = datetime.fromisoformat(result)

    late_afternoon = datetime(2026, 8, 1, 17, 0, 0, tzinfo=ZoneInfo(LA))
    assert deadline > late_afternoon


def test_auckland_user_gets_no_free_extra_hours():
    """The other direction: their deadline must not spill past the named day."""
    result = _extract_deadline(f"by {NAMED_DAY}", AUCKLAND)
    deadline = datetime.fromisoformat(result)

    end_of_named_day_local = datetime(2026, 8, 1, 23, 59, 59, tzinfo=ZoneInfo(AUCKLAND))
    assert deadline <= end_of_named_day_local
    # And strictly earlier in absolute time than the old UTC-pinned behaviour.
    assert deadline < datetime(2026, 8, 1, 23, 59, 59, tzinfo=timezone.utc)


@pytest.mark.parametrize("tz_name", [LA, AUCKLAND])
def test_a_weekday_deadline_is_end_of_day_on_the_users_calendar(tz_name):
    result = _extract_deadline("finish it by friday", tz_name)

    local = _local(result, tz_name)
    assert local.weekday() == 4
    assert (local.hour, local.minute, local.second) == (23, 59, 59)


def test_no_timezone_still_means_utc():
    """Unchanged default for clients that send none."""
    result = _extract_deadline(f"by {NAMED_DAY}")

    assert datetime.fromisoformat(result) == datetime(
        2026, 8, 1, 23, 59, 59, tzinfo=timezone.utc
    )


def test_opening_prompt_records_the_client_timezone_on_the_draft():
    draft = _extract_partial_goal_fields(
        f"Push commits to my repo by {NAMED_DAY} and pledge $25",
        goal_type_name="github_repo",
        tz_name=LA,
    )

    assert draft["timezone"] == LA
    assert datetime.fromisoformat(draft["deadline"]).utcoffset() == ZoneInfo(
        LA
    ).utcoffset(datetime(2026, 8, 1))


def test_a_bogus_client_timezone_falls_back_to_utc_instead_of_raising():
    draft = _extract_partial_goal_fields(
        f"by {NAMED_DAY}", goal_type_name="github_repo", tz_name="Mars/Olympus_Mons"
    )

    assert datetime.fromisoformat(draft["deadline"]).utcoffset() == timedelta(0)


def test_prose_deadlines_must_not_go_through_parse_deadline():
    """Pins the design decision behind `_extract_deadline`: `parse_deadline` is
    built for a reply that is only a deadline, and on prose it reads the pledge
    amount as a time. If someone "simplifies" this by delegating, the deadline
    silently moves to 20:00 — earlier than the user asked, with a charge waiting."""
    prose = f"upload a walkthrough by {NAMED_DAY} and pledge $20"

    misread = parse_deadline(prose, LA)
    assert misread is not None
    assert datetime.fromisoformat(misread).hour == 20  # from "$20", not a time

    extracted = _extract_deadline(prose, LA)
    assert datetime.fromisoformat(extracted).hour == 23


# ── end to end: what actually lands in the database ───────────────────


def _make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _auth(client, email, sub):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": "T", "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": "valid-token"})
        return resp.json()["access_token"]


async def _create_session(client, token):
    resp = await client.post(
        "/api/chat/sessions", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 201
    return resp.json()["session_id"]


async def _load_criteria_data(goal_id: str) -> dict:
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with engine.connect() as conn:
            row = await conn.execute(
                text("SELECT criteria_data FROM goal_criteria WHERE goal_id = :id"),
                {"id": uuid.UUID(goal_id)},
            )
            return row.fetchone()[0]
    finally:
        await engine.dispose()


async def _drive(client, token, session_id, prompt, goal_type, answers, tz=None):
    body = {"content": prompt}
    if tz:
        body["timezone"] = tz
    with patch("app.routes.chat.match_message", new_callable=AsyncMock) as mock_match:
        mock_match.return_value = MatchResult(
            matched=True, goal_type=goal_type, confidence=0.9, rationale="r"
        )
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    resp = await client.post(
        f"/api/chat/sessions/{session_id}/messages",
        json={"content": f"Use this goal type: {goal_type}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    asked: list[str] = []
    payload = resp.json()
    for _ in range(8):
        action = payload["messages"][-1].get("action")
        if not (isinstance(action, dict) and action.get("type") == "awaiting_input"):
            break
        field = action["field"]
        asked.append(field)
        reply = answers.get(field, f"answer for {field}")
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={"content": reply},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        payload = resp.json()
    return asked, payload


@pytest.mark.asyncio
async def test_api_endpoint_goal_stores_an_integer_status_in_the_database():
    """End to end, all the way to the column the verifier reads."""
    async with _make_client() as client:
        token = await _auth(client, "coerce1@example.com", "coerce-1")
        session_id = await _create_session(client, token)

        _, payload = await _drive(
            client,
            token,
            session_id,
            f"My API endpoint returns 200 by {_FUTURE_DATE}, pledge $30",
            "api_endpoint",
            {
                "title": "Health endpoint up",
                "url": "https://example.com/health",
                "method": "GET",
                "expected_status": "200",
            },
        )
        action = payload["messages"][-1]["action"]
        assert action["type"] == "ready_to_create"

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": action["goal_payload"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        criteria_data = await _load_criteria_data(resp.json()["goal_id"])

    assert criteria_data["expected_status"] == 200
    assert isinstance(criteria_data["expected_status"], int)


@pytest.mark.asyncio
async def test_create_goal_rejects_a_criteria_value_of_the_wrong_type():
    """A crafted client payload cannot smuggle a value the verifier can never
    match — that would be a guaranteed charge for a goal the user met."""
    async with _make_client() as client:
        token = await _auth(client, "coerce2@example.com", "coerce-2")
        session_id = await _create_session(client, token)

        _, payload = await _drive(
            client,
            token,
            session_id,
            f"My API endpoint returns 200 by {_FUTURE_DATE}, pledge $30",
            "api_endpoint",
            {
                "title": "Health endpoint up",
                "url": "https://example.com/health",
                "method": "GET",
                "expected_status": "200",
            },
        )
        good_payload = payload["messages"][-1]["action"]["goal_payload"]

        bad_payload = dict(good_payload)
        bad_payload["criteria"] = {
            **good_payload["criteria"],
            "expected_status": "two hundred",
        }
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": bad_payload},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "expected_status" in detail and "integer" in detail


@pytest.mark.asyncio
async def test_send_message_honours_the_client_timezone_end_to_end():
    async with _make_client() as client:
        token = await _auth(client, "coerce3@example.com", "coerce-3")
        session_id = await _create_session(client, token)

        with patch(
            "app.routes.chat.match_message", new_callable=AsyncMock
        ) as mock_match:
            mock_match.return_value = MatchResult(
                matched=True, goal_type="github_repo", confidence=0.9, rationale="r"
            )
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={
                    "content": f"Push commits to my repo by {_FUTURE_DATE}, pledge $25",
                    "timezone": LA,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        draft = resp.json()["draft_goal"]

    assert draft["timezone"] == LA
    deadline = datetime.fromisoformat(draft["deadline"])
    assert deadline.utcoffset() == ZoneInfo(LA).utcoffset(deadline.replace(tzinfo=None))
    assert (deadline.hour, deadline.minute) == (23, 59)
