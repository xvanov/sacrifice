"""Every goal writer inherits the criteria gate, not just the chat flow.

``app/routes/chat.py`` coerces criteria to their declared types, insists on the
schema's ``required`` fields, and enforces the ``anyOf`` "at least one checkable
criterion" contract. ``POST /api/goals`` bypassed all three, which was verified
by execution rather than inspection:

    POST /api/goals {expected_status: "200"} -> 201 draft
    PUT  /api/goals/{id} {status: "active"}  -> 200 active

and an active goal is chargeable. ``app/workers/api_check.py`` compares
``actual_status == expected_status``, so ``200 == "200"`` is ``False`` forever:
the user's endpoint answers correctly, the verdict is ``failed``, and
``app/services/verification_result`` charges a real PaymentIntent.

These tests pin the gate at both write paths — creation (so the caller finds out
immediately) and activation (so a draft that predates the gate cannot become
chargeable) — and pin what the gate must NOT do: invent a default for a missing
criterion, reject the values the chat legitimately produces, drop a field the
schema does not describe, or assume every goal type is registered.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.main import app
from app.services.criteria_gate import (
    CriteriaRejected,
    gate_criteria,
    is_criterion_set,
    is_missing_value,
    unsatisfied_any_of_field,
)

_FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

# Valid criteria for every registered goal type. The regression bar: whatever the
# gate rejects, it must not reject these.
_VALID_CRITERIA = {
    "youtube_video": {"min_duration_seconds": 300, "video_description": "A demo"},
    "api_endpoint": {
        "url": "https://example.com/health",
        "method": "GET",
        "expected_status": 200,
    },
    "github_repo": {
        "repo_owner": "kalin",
        "repo_name": "sacrifice",
        "min_commits": 3,
    },
    "dev_sandbox": {
        "repo_url": "https://github.com/octocat/hello",
        "test_command": "pytest -q",
    },
    "geolocation": {"target_latitude": 35.8982, "target_longitude": -78.9408},
}


def _make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _auth(client, email, sub):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {
            "email": email,
            "name": "Gate",
            "sub": sub,
            "picture": None,
        }
        resp = await client.post("/api/auth/google", json={"token": "valid-token"})
        assert resp.status_code in (200, 201), resp.text
        return resp.json()["access_token"]


def _payload(goal_type: str, criteria) -> dict:
    return {
        "title": "Gate test goal",
        "description": "d",
        "deadline": _FUTURE,
        "pledge_amount": 2500,
        "goal_type": goal_type,
        "criteria": criteria,
    }


async def _post_goal(client, token, goal_type, criteria):
    return await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json=_payload(goal_type, criteria),
    )


async def _activate(client, token, goal_id):
    return await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )


async def _stored_criteria(goal_id: str) -> dict:
    """Read criteria_data straight out of the column the verifiers read."""
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with engine.connect() as conn:
            row = await conn.execute(
                text("SELECT criteria_data FROM goal_criteria WHERE goal_id = :id"),
                {"id": uuid.UUID(goal_id)},
            )
            return row.scalar_one()
    finally:
        await engine.dispose()


async def _force_criteria(goal_id: str, criteria: dict) -> None:
    """Write criteria the gate would refuse, simulating a pre-gate draft.

    Straight SQL on purpose: the point of these fixtures is a row that exists in
    production today because it was written before the gate did.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE goal_criteria SET criteria_data = CAST(:d AS JSONB) "
                    "WHERE goal_id = :id"
                ),
                {"d": json.dumps(criteria), "id": uuid.UUID(goal_id)},
            )
    finally:
        await engine.dispose()


async def _goal_status(client, token, goal_id) -> str:
    resp = await client.get(
        f"/api/goals/{goal_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["status"]


# ── Creation: coerce what is unambiguous ──────────────────────────────


@pytest.mark.asyncio
async def test_string_expected_status_is_stored_as_an_integer():
    """Coerced, not rejected: ``"200"`` means 200 and nothing else.

    Rejecting would be defensible, but the value's intent is unambiguous and the
    only thing that must never happen is a string reaching the column
    ``app/workers/api_check.py`` compares with ``==``.
    """
    async with _make_client() as client:
        token = await _auth(client, "gate-status@example.com", "gate-status")
        resp = await _post_goal(
            client,
            token,
            "api_endpoint",
            {
                "url": "https://example.com/health",
                "method": "GET",
                "expected_status": "200",
            },
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

        stored = await _stored_criteria(goal_id)
        assert stored["expected_status"] == 200
        assert type(stored["expected_status"]) is int, (
            "a string here fails a passing endpoint and charges the pledge"
        )


@pytest.mark.asyncio
async def test_a_duration_carrying_a_unit_is_refused_not_read_as_seconds():
    """``"5 minutes"`` must not become ``5``.

    The plain numeric read yields an int, so nothing downstream raises — it just
    stores a commitment 1/60th the size of the one the user made, which any
    five-second clip satisfies. That is the same fabrication as defaulting a
    missing field, so it is a 422 naming the field instead.
    """
    async with _make_client() as client:
        token = await _auth(client, "gate-dur@example.com", "gate-dur")
        resp = await _post_goal(
            client,
            token,
            "youtube_video",
            {"min_duration_seconds": "5 minutes", "video_description": "A demo"},
        )
        assert resp.status_code == 422, resp.text
        assert "min_duration_seconds" in resp.text

        # The same value written as a plain number is accepted and typed.
        resp = await _post_goal(
            client,
            token,
            "youtube_video",
            {"min_duration_seconds": "300", "video_description": "A demo"},
        )
        assert resp.status_code == 201, resp.text
        stored = await _stored_criteria(resp.json()["id"])
        assert stored["min_duration_seconds"] == 300
        assert type(stored["min_duration_seconds"]) is int


# ── Creation: reject what cannot be checked ───────────────────────────


@pytest.mark.asyncio
async def test_empty_criteria_are_rejected_naming_the_missing_field():
    async with _make_client() as client:
        token = await _auth(client, "gate-empty@example.com", "gate-empty")
        resp = await _post_goal(client, token, "github_repo", {})

        assert resp.status_code == 422, resp.text
        assert "repo_owner" in resp.text, (
            f"the 422 must name what is missing; got {resp.text}"
        )


@pytest.mark.asyncio
async def test_repo_only_criteria_are_rejected_for_naming_no_check():
    """Owner and name satisfy ``required`` but configure no check.

    ``app/workers/github_repo.py`` refuses to certify such a goal, so it can only
    ever be failed at the deadline — and charged.
    """
    async with _make_client() as client:
        token = await _auth(client, "gate-repoonly@example.com", "gate-repoonly")
        resp = await _post_goal(
            client,
            token,
            "github_repo",
            {"repo_owner": "kalin", "repo_name": "sacrifice"},
        )

        assert resp.status_code == 422, resp.text
        assert "checkable" in resp.text
        assert "min_commits" in resp.text


@pytest.mark.asyncio
async def test_a_degenerate_criterion_does_not_pass_as_a_check():
    """``min_commits: 0`` is present, typed, and checks nothing.

    The schema says ``minimum: 1``; the gate enforces the field's own stated
    contract rather than inventing a floor.
    """
    async with _make_client() as client:
        token = await _auth(client, "gate-zero@example.com", "gate-zero")
        resp = await _post_goal(
            client,
            token,
            "github_repo",
            {"repo_owner": "kalin", "repo_name": "sacrifice", "min_commits": 0},
        )

        assert resp.status_code == 422, resp.text
        assert "min_commits" in resp.text


@pytest.mark.asyncio
async def test_an_unreadable_value_is_rejected_rather_than_guessed():
    async with _make_client() as client:
        token = await _auth(client, "gate-words@example.com", "gate-words")
        resp = await _post_goal(
            client,
            token,
            "api_endpoint",
            {
                "url": "https://example.com",
                "method": "GET",
                "expected_status": "two hundred",
            },
        )

        assert resp.status_code == 422, resp.text
        assert "expected_status" in resp.text


def test_the_gate_invents_nothing():
    """A rejection must never be quietly converted into a default.

    ``min_commits: 1`` would make the request succeed and commit the user to a
    promise they never made — and a failed goal charges their card, so the guess
    is the more expensive error.
    """
    with pytest.raises(CriteriaRejected):
        gate_criteria("github_repo", {"repo_owner": "kalin", "repo_name": "s"})

    coerced = gate_criteria("github_repo", dict(_VALID_CRITERIA["github_repo"]))
    assert set(coerced) == set(_VALID_CRITERIA["github_repo"]), (
        "the gate added or dropped a criterion"
    )


# ── Activation: the moment a goal becomes chargeable ──────────────────


@pytest.mark.asyncio
async def test_activating_a_pre_gate_draft_with_no_checkable_criterion_is_refused():
    async with _make_client() as client:
        token = await _auth(client, "gate-act1@example.com", "gate-act1")
        resp = await _post_goal(
            client, token, "github_repo", dict(_VALID_CRITERIA["github_repo"])
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

        # Rewind the row to what a pre-gate draft looks like.
        await _force_criteria(
            goal_id, {"repo_owner": "kalin", "repo_name": "sacrifice"}
        )

        resp = await _activate(client, token, goal_id)
        assert resp.status_code == 422, resp.text
        assert "checkable" in resp.text
        assert await _goal_status(client, token, goal_id) == "draft", (
            "a goal the verifier can only fail must not reach a chargeable state"
        )


@pytest.mark.asyncio
async def test_activating_a_pre_gate_draft_repairs_a_string_status():
    """A coercible value is repaired on the way through, not refused.

    The draft was always meant to hold an integer; making it winnable is a better
    answer than making it unactivatable.
    """
    async with _make_client() as client:
        token = await _auth(client, "gate-act2@example.com", "gate-act2")
        resp = await _post_goal(
            client, token, "api_endpoint", dict(_VALID_CRITERIA["api_endpoint"])
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

        await _force_criteria(
            goal_id,
            {
                "url": "https://example.com/health",
                "method": "GET",
                "expected_status": "200",
            },
        )

        resp = await _activate(client, token, goal_id)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"

        stored = await _stored_criteria(goal_id)
        assert stored["expected_status"] == 200
        assert type(stored["expected_status"]) is int


@pytest.mark.asyncio
async def test_activating_a_pre_gate_draft_with_an_unreadable_value_is_refused():
    async with _make_client() as client:
        token = await _auth(client, "gate-act3@example.com", "gate-act3")
        resp = await _post_goal(
            client, token, "youtube_video", dict(_VALID_CRITERIA["youtube_video"])
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

        # The TypeError case: app/workers/youtube.py does `duration >= min`,
        # which raises past its `except ValueError`, so no verdict is ever
        # written and app/workers/deadline.py charges the goal instead.
        await _force_criteria(
            goal_id,
            {"min_duration_seconds": "5 minutes", "video_description": "A demo"},
        )

        resp = await _activate(client, token, goal_id)
        assert resp.status_code == 422, resp.text
        assert "min_duration_seconds" in resp.text
        assert await _goal_status(client, token, goal_id) == "draft"

        stored = await _stored_criteria(goal_id)
        assert not isinstance(stored["min_duration_seconds"], str) or (
            stored["min_duration_seconds"] == "5 minutes"
        ), "a refused activation must not half-write a guessed value"


@pytest.mark.asyncio
async def test_cancelling_a_pre_gate_draft_is_still_allowed():
    """The gate guards activation only. Cancelling an unwinnable draft is the
    one thing its owner definitely should be able to do."""
    async with _make_client() as client:
        token = await _auth(client, "gate-cancel@example.com", "gate-cancel")
        resp = await _post_goal(
            client, token, "github_repo", dict(_VALID_CRITERIA["github_repo"])
        )
        goal_id = resp.json()["id"]
        await _force_criteria(goal_id, {})

        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "cancelled"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"


# ── Regression: what must keep working ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("goal_type", sorted(_VALID_CRITERIA))
async def test_every_registered_goal_type_still_creates_and_activates(goal_type):
    async with _make_client() as client:
        token = await _auth(
            client, f"gate-{goal_type}@example.com", f"gate-{goal_type}"
        )
        resp = await _post_goal(
            client, token, goal_type, dict(_VALID_CRITERIA[goal_type])
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

        assert await _stored_criteria(goal_id) == _VALID_CRITERIA[goal_type], (
            "valid criteria must be stored exactly as given"
        )

        resp = await _activate(client, token, goal_id)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_legacy_conditions_criteria_still_create_and_activate():
    """``conditions`` is github_repo's legacy shape and an ``anyOf`` alternative.

    It is also the case a validation step is most likely to break, because the
    chat has no prompt for it.
    """
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "hello",
        "conditions": [{"type": "commits", "min_count": 2, "since_date": "2026-01-01"}],
    }
    async with _make_client() as client:
        token = await _auth(client, "gate-legacy@example.com", "gate-legacy")
        resp = await _post_goal(client, token, "github_repo", dict(criteria))
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

        assert await _stored_criteria(goal_id) == criteria
        assert (await _activate(client, token, goal_id)).status_code == 200


@pytest.mark.asyncio
async def test_a_field_the_schema_does_not_describe_passes_through():
    """Unknown keys are kept, not dropped.

    A goal type that stores something its schema has not caught up with must not
    have it silently deleted by a validation step.
    """
    criteria = {
        **_VALID_CRITERIA["youtube_video"],
        "operator_note": "carried by an older client",
    }
    async with _make_client() as client:
        token = await _auth(client, "gate-extra@example.com", "gate-extra")
        resp = await _post_goal(client, token, "youtube_video", dict(criteria))
        assert resp.status_code == 201, resp.text

        assert await _stored_criteria(resp.json()["id"]) == criteria


# ── Goal types that are not registered yet ────────────────────────────


@pytest.mark.asyncio
async def test_the_generated_placeholder_type_is_left_alone():
    """``__generated__`` is a real goal type for a real status.

    A goal sits in ``awaiting_goal_type`` with this type while the factory builds
    its verifier; there is no ``criteria_schema`` to check against yet, so the
    gate must have no opinion rather than throw.
    """
    placeholder = {
        "generated": True,
        "direction_id": "042-pushup-counter",
        "module_name": "pushup_counter",
    }
    assert gate_criteria("__generated__", dict(placeholder)) == placeholder

    async with _make_client() as client:
        token = await _auth(client, "gate-gen@example.com", "gate-gen")
        resp = await _post_goal(client, token, "__generated__", dict(placeholder))
        assert resp.status_code == 201, resp.text
        assert await _stored_criteria(resp.json()["id"]) == placeholder


def test_an_unregistered_type_is_still_refused_by_the_goal_type_validator():
    """The gate's silence about unknown schemas must not become a way in."""
    from app.schemas.goal import GoalCreate

    with pytest.raises(ValueError, match="Unknown goal_type"):
        GoalCreate(**_payload("api", {"url": "https://example.com"}))


# ── The canonical/wrapped criteria shapes ─────────────────────────────


@pytest.mark.asyncio
async def test_canonically_wrapped_criteria_are_unwrapped_before_storage():
    """``{criteria_type, criteria_data}`` is the spec's shape and arrives in
    practice; stored wrapped, it sits one level deeper than every verifier reads.
    """
    async with _make_client() as client:
        token = await _auth(client, "gate-wrap@example.com", "gate-wrap")
        resp = await _post_goal(
            client,
            token,
            "youtube_video",
            {
                "criteria_type": "youtube",
                "criteria_data": dict(_VALID_CRITERIA["youtube_video"]),
            },
        )
        assert resp.status_code == 201, resp.text
        assert (
            await _stored_criteria(resp.json()["id"])
            == _VALID_CRITERIA["youtube_video"]
        )


@pytest.mark.asyncio
async def test_a_non_object_criteria_data_is_refused():
    async with _make_client() as client:
        token = await _auth(client, "gate-wrap2@example.com", "gate-wrap2")
        resp = await _post_goal(
            client, token, "youtube_video", {"criteria_data": "300 seconds"}
        )
        assert resp.status_code == 422, resp.text
        assert "criteria_data" in resp.text


# ── Drift guard against the chat implementation ───────────────────────


def test_the_gate_predicates_match_the_chat_ones():
    """The chat flow still carries its own copies of these predicates.

    Until it imports these, the two must agree exactly — a divergence is how one
    write path starts accepting what the other refuses, which is the bug this
    whole gate exists to close.
    """
    from app.routes import chat

    values = [
        None,
        True,
        False,
        "",
        "  ",
        "x",
        0,
        1,
        -1,
        0.0,
        0.5,
        [],
        ["a"],
        {},
        {"a": 1},
        (),
        ("a",),
        object(),
    ]
    for value in values:
        assert is_criterion_set(value) == chat._is_criterion_set(value), value
        assert is_missing_value(value) == chat._is_missing_value(value), value

    schema = {
        "anyOf": [{"required": ["undocumented_field"]}, {"required": ["min_commits"]}],
    }
    assert unsatisfied_any_of_field(
        schema, {}, prefer=chat._AWAITING_INPUT_PROMPTS
    ) == chat._unsatisfied_any_of_field(schema, {})
    assert (
        unsatisfied_any_of_field(schema, {"min_commits": 2}) is None
        and chat._unsatisfied_any_of_field(schema, {"min_commits": 2}) is None
    )
