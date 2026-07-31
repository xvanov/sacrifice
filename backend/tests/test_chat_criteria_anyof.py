"""The chat flow must never create a goal that cannot be verified.

``github_repo``'s verifier fails a goal whose criteria name only the repo (see
``app/workers/github_repo.py`` — "a configuration that expresses no check we
can actually run never returns verified"). Its schema records that as an
``anyOf`` contract: at least one of ``min_commits``, ``required_files``,
``require_pr`` or ``conditions``. These tests pin the chat side of that
contract — the assistant asks for a checkable criterion, understands the
plain-language answer, refuses to store a degenerate one, and the create-goal
endpoint rejects a client payload that drops it.

The ``anyOf`` handling is also pinned against a synthetic goal type so the
mechanism is proven generic rather than tied to one definition.
"""

import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routes.chat import (
    _AWAITING_INPUT_PROMPTS,
    _apply_reply_to_draft,
    _build_awaiting_input_message,
    _compute_missing_criteria,
    _extract_partial_goal_fields,
    _REPO_CRITERION_FIELDS,
)
from app.services.chat_match import MatchResult

_FUTURE_DATE = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()

# The alternatives github_repo's schema offers; the chat must collect one.
_ANY_OF_FIELDS = ("min_commits", "required_files", "require_pr", "conditions")


def _repo_draft(**criteria) -> dict:
    """A github_repo draft with every non-criteria field already collected."""
    return {
        "goal_type": "github_repo",
        "title": "Ship the parser",
        "description": "Push commits to my repo",
        "deadline": f"{_FUTURE_DATE}T23:59:59+00:00",
        "pledge_amount": 2500,
        "currency": "usd",
        "criteria": {"repo_owner": "kalin", "repo_name": "sacrifice", **criteria},
    }


@contextlib.contextmanager
def _synthetic_goal_type(name: str, criteria_schema: dict):
    """Register a throwaway goal type so schema handling is tested generically."""
    from app.goal_types import registry

    registry._ensure_discovered()

    async def _verify(proof_data, criteria_data):
        return {"verification_status": "failed"}

    registry._registry[name] = registry._DynamicGoalType(
        name=name,
        description="synthetic goal type for schema tests",
        sample_prompts=[],
        criteria_schema=criteria_schema,
        verify=_verify,
    )
    try:
        yield
    finally:
        registry._registry.pop(name, None)


# ── The anyOf contract ────────────────────────────────────────────────


def test_repo_only_criteria_report_a_checkable_criterion_as_missing():
    """Owner + name alone verifies nothing, so the chat must still ask."""
    missing = _compute_missing_criteria(_repo_draft(), goal_type_name="github_repo")

    assert missing, "repo-only criteria must not read as fully collected"
    assert set(missing) & set(_ANY_OF_FIELDS), (
        f"expected a checkable-criterion field to be asked for, got {missing}"
    )
    # And the field asked for is one the assistant has words for.
    assert missing[0] in _AWAITING_INPUT_PROMPTS


@pytest.mark.parametrize(
    "criteria",
    [
        {"min_commits": 3},
        {"required_files": ["README.md"]},
        {"require_pr": True},
        {"conditions": [{"type": "commits", "min_count": 2}]},
    ],
    ids=["min_commits", "required_files", "require_pr", "legacy_conditions"],
)
def test_a_single_alternative_satisfies_the_any_of(criteria):
    missing = _compute_missing_criteria(
        _repo_draft(**criteria), goal_type_name="github_repo"
    )

    assert missing == [], f"{criteria} is checkable; nothing more should be asked"


@pytest.mark.parametrize(
    "criteria",
    [
        {"min_commits": 0},
        {"required_files": []},
        {"require_pr": False},
        {"conditions": []},
    ],
    ids=["zero_commits", "no_files", "pr_not_required", "empty_conditions"],
)
def test_degenerate_alternatives_do_not_satisfy_the_any_of(criteria):
    """The verifier calls these "inert" — present but checking nothing. Treating
    them as collected is how an unwinnable goal gets created."""
    missing = _compute_missing_criteria(
        _repo_draft(**criteria), goal_type_name="github_repo"
    )

    assert set(missing) & set(_ANY_OF_FIELDS), (
        f"{criteria} configures no check, so the chat must keep asking; got {missing}"
    )


def test_required_fields_are_still_enforced_alongside_the_any_of():
    """Satisfying the anyOf must not excuse repo_owner/repo_name."""
    draft = _repo_draft(min_commits=3)
    draft["criteria"].pop("repo_owner")

    missing = _compute_missing_criteria(draft, goal_type_name="github_repo")

    assert "repo_owner" in missing


def test_any_of_handling_is_generic_over_the_schema():
    """Driven off a synthetic schema: no goal-type name is hardcoded."""
    schema = {
        "type": "object",
        "properties": {
            "thing": {"type": "string"},
            "alpha": {"type": "integer"},
            "beta": {"type": "boolean"},
        },
        "required": ["thing"],
        "anyOf": [{"required": ["alpha"]}, {"required": ["beta"]}],
    }
    with _synthetic_goal_type("synthetic_anyof", schema):
        base = {
            "goal_type": "synthetic_anyof",
            "title": "t",
            "description": "d",
            "deadline": f"{_FUTURE_DATE}T23:59:59+00:00",
            "pledge_amount": 100,
            "currency": "usd",
        }

        unmet = _compute_missing_criteria(
            {**base, "criteria": {"thing": "x"}}, goal_type_name="synthetic_anyof"
        )
        assert unmet == ["alpha"]

        # Either alternative satisfies it.
        for satisfied in ({"thing": "x", "alpha": 2}, {"thing": "x", "beta": True}):
            assert (
                _compute_missing_criteria(
                    {**base, "criteria": satisfied}, goal_type_name="synthetic_anyof"
                )
                == []
            )


def test_any_of_prefers_an_alternative_the_chat_can_ask_about():
    """A schema may list an alternative the chat has no prompt for (github_repo's
    legacy ``conditions``). Ask for one we can actually put a question to."""
    schema = {
        "type": "object",
        "properties": {},
        "required": [],
        "anyOf": [{"required": ["undocumented_field"]}, {"required": ["min_commits"]}],
    }
    with _synthetic_goal_type("synthetic_prompted", schema):
        missing = _compute_missing_criteria(
            {
                "goal_type": "synthetic_prompted",
                "title": "t",
                "description": "d",
                "deadline": f"{_FUTURE_DATE}T23:59:59+00:00",
                "pledge_amount": 100,
                "currency": "usd",
                "criteria": {},
            },
            goal_type_name="synthetic_prompted",
        )

    assert missing == ["min_commits"]


def test_schema_without_any_of_is_unaffected():
    draft = {
        "goal_type": "youtube_video",
        "title": "t",
        "description": "d",
        "deadline": f"{_FUTURE_DATE}T23:59:59+00:00",
        "pledge_amount": 100,
        "currency": "usd",
        "criteria": {"video_description": "demo", "min_duration_seconds": 300},
    }

    assert _compute_missing_criteria(draft, goal_type_name="youtube_video") == []


# ── Prompts ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("field", _REPO_CRITERION_FIELDS)
def test_every_repo_criterion_field_has_prompt_text(field):
    prompt = _AWAITING_INPUT_PROMPTS.get(field)

    assert prompt, f"{field} can be asked for but has no question"
    assert prompt == _build_awaiting_input_message(field)["action"]["prompt"]
    assert not prompt.startswith("What's the value for"), "generic fallback leaked"


# ── Extraction ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "asked_field,reply,expected",
    [
        ("min_commits", "at least 3 commits", {"min_commits": 3}),
        ("min_commits", "3 commits", {"min_commits": 3}),
        ("min_commits", "5", {"min_commits": 5}),
        ("min_commits", "10+ commits please", {"min_commits": 10}),
        (
            "required_files",
            "it needs a README.md and tests/",
            {"required_files": ["README.md", "tests/"]},
        ),
        (
            "required_files",
            "src/main.py, .github/workflows/ci.yml",
            {"required_files": ["src/main.py", ".github/workflows/ci.yml"]},
        ),
        ("required_files", "a Makefile", {"required_files": ["Makefile"]}),
        ("require_pr", "yes, require a PR", {"require_pr": True}),
        ("require_pr", "yes", {"require_pr": True}),
        ("require_pr", "I want a pull request open", {"require_pr": True}),
    ],
)
def test_plain_language_replies_set_typed_criteria(asked_field, reply, expected):
    updated = _apply_reply_to_draft(
        _repo_draft(), asked_field, reply, goal_type_name="github_repo"
    )

    for key, value in expected.items():
        assert updated["criteria"][key] == value
        assert type(updated["criteria"][key]) is type(value)
    assert _compute_missing_criteria(updated, goal_type_name="github_repo") == []


def test_an_answer_may_satisfy_a_different_alternative_than_the_one_asked():
    """Asked how many commits, the user names a file instead. That is a real
    answer — the same way a pasted coordinate pair fills both axes."""
    updated = _apply_reply_to_draft(
        _repo_draft(),
        "min_commits",
        "just make sure README.md exists",
        goal_type_name="github_repo",
    )

    assert updated["criteria"]["required_files"] == ["README.md"]
    assert "min_commits" not in updated["criteria"]
    assert _compute_missing_criteria(updated, goal_type_name="github_repo") == []


@pytest.mark.parametrize(
    "asked_field,reply",
    [
        ("min_commits", "zero commits"),
        ("min_commits", "0 commits"),
        ("min_commits", "0"),
        ("min_commits", "abc"),
        ("required_files", "abc"),
        ("require_pr", "no, skip the PR"),
    ],
)
def test_unusable_replies_store_nothing_and_are_re_asked(asked_field, reply):
    draft = _repo_draft()

    updated = _apply_reply_to_draft(
        draft, asked_field, reply, goal_type_name="github_repo"
    )

    # No garbage value landed in the criteria.
    for field in _ANY_OF_FIELDS:
        assert field not in updated["criteria"], (
            f"{reply!r} must not store {field}={updated['criteria'].get(field)!r}"
        )
    # And the chat asks again instead of moving on.
    missing = _compute_missing_criteria(updated, goal_type_name="github_repo")
    assert set(missing) & set(_ANY_OF_FIELDS)
    assert (
        _build_awaiting_input_message(missing[0])["action"]["type"] == "awaiting_input"
    )


def test_a_bad_reply_does_not_clobber_an_already_collected_criterion():
    draft = _repo_draft(min_commits=3)

    updated = _apply_reply_to_draft(
        draft, "min_commits", "abc", goal_type_name="github_repo"
    )

    assert updated["criteria"]["min_commits"] == 3


# ── Free-form extraction from the opening prompt ──────────────────────


def test_opening_prompt_honours_criteria_the_user_stated():
    draft = _extract_partial_goal_fields(
        "Open a PR with at least 3 commits by next Thursday",
        goal_type_name="github_repo",
    )

    assert draft["criteria"]["min_commits"] == 3
    assert draft["criteria"]["require_pr"] is True


@pytest.mark.parametrize(
    "prompt",
    [
        "Push a working implementation to my github repo by Saturday",
        # A date and a dollar amount must not read as a commit count or a path.
        f"Push commits to my repo by {_FUTURE_DATE} and pledge $25",
    ],
)
def test_opening_prompt_invents_no_criterion_when_none_was_stated(prompt):
    """A vague prompt must leave the criteria empty so the user is asked. Quietly
    defaulting to min_commits=1 would fabricate a commitment they never made."""
    draft = _extract_partial_goal_fields(prompt, goal_type_name="github_repo")

    for field in _ANY_OF_FIELDS:
        assert field not in draft["criteria"]


def test_a_pasted_repo_url_is_not_mistaken_for_a_required_file():
    draft = _extract_partial_goal_fields(
        "Push commits to https://github.com/kalin/sacrifice by Friday",
        goal_type_name="github_repo",
    )

    assert "required_files" not in draft["criteria"]


# ── End-to-end through the API ────────────────────────────────────────


def _make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _auth(client, email="anyof@example.com", sub="anyof-sub"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {
            "email": email,
            "name": "Any Of",
            "sub": sub,
            "picture": None,
        }
        resp = await client.post("/api/auth/google", json={"token": "valid-token"})
        return resp.json()["access_token"]


async def _create_session(client, token):
    resp = await client.post(
        "/api/chat/sessions", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 201
    return resp.json()["session_id"]


async def _drive_github_repo_flow(client, token, session_id):
    """Match → confirm → answer every awaiting_input. Returns (fields, body)."""
    with patch("app.routes.chat.match_message", new_callable=AsyncMock) as mock_match:
        mock_match.return_value = MatchResult(
            matched=True,
            goal_type="github_repo",
            confidence=0.9,
            rationale="repo work",
        )
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={
                "content": f"Push commits to my repo by {_FUTURE_DATE} and pledge $25"
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    resp = await client.post(
        f"/api/chat/sessions/{session_id}/messages",
        json={"content": "Use this goal type: github_repo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    answers = {
        "title": "Ship the parser",
        "repo_owner": "kalin",
        "repo_name": "sacrifice",
        "min_commits": "at least 3 commits",
    }
    asked: list[str] = []
    body = resp.json()
    for _ in range(6):
        action = body["messages"][-1].get("action")
        if not (isinstance(action, dict) and action.get("type") == "awaiting_input"):
            break
        field = action["field"]
        asked.append(field)
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={"content": answers.get(field, f"answer for {field}")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()

    return asked, body


@pytest.mark.asyncio
async def test_chat_asks_for_a_checkable_criterion_before_offering_to_create():
    async with _make_client() as client:
        token = await _auth(client)
        session_id = await _create_session(client, token)

        asked, body = await _drive_github_repo_flow(client, token, session_id)

    assert "min_commits" in asked, (
        f"the chat never asked for a checkable criterion; it asked {asked}"
    )
    action = body["messages"][-1]["action"]
    assert action["type"] == "ready_to_create"
    criteria = action["goal_payload"]["criteria"]
    assert criteria["min_commits"] == 3
    assert criteria["repo_owner"] == "kalin"
    assert criteria["repo_name"] == "sacrifice"


@pytest.mark.asyncio
async def test_create_goal_rejects_a_payload_stripped_of_every_checkable_criterion():
    """The final payload is client-submitted, so the conversational gate is not
    the last word: a payload carrying only owner/name must be refused."""
    async with _make_client() as client:
        token = await _auth(client, email="anyof2@example.com", sub="anyof-sub-2")
        session_id = await _create_session(client, token)

        _, body = await _drive_github_repo_flow(client, token, session_id)
        payload = dict(body["messages"][-1]["action"]["goal_payload"])

        stripped = dict(payload)
        stripped["criteria"] = {
            k: v for k, v in payload["criteria"].items() if k not in _ANY_OF_FIELDS
        }
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": stripped},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
        assert "checkable" in resp.json()["detail"]

        # The honest payload still creates the goal.
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/create-goal",
            json={"goal_payload": payload},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
