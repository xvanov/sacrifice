"""``accept-generated-type`` is an activation, so it gates criteria like one.

``app/routes/chat.py`` set ``goal.status = "active"`` directly and skipped the
criteria gate that ``app/services/goal.create_goal`` and ``PUT /api/goals/{id}``
both apply (``app/services/criteria_gate.py``, ``_gate_criteria_for_activation``
in ``app/routes/goals.py``). The goal it activates was created as the
``__generated__`` placeholder, whose criteria are ``{generated, direction_id,
module_name}`` and nothing else — so a factory-built goal type declaring required
or ``anyOf`` criteria became active, and chargeable, with none of them collected.

That is a money bug, the same one the gate was written for. Its verifier can only
ever refuse to certify such a goal, and a goal nobody can win is a goal that gets
charged: an unverifiable outcome leaves the goal ``active`` past its deadline and
``app/workers/deadline.py`` charges the pledge. So the refusal is a 422 naming the
field, not a warning.

The gate is applied against the *new* module name, not ``goal.goal_type``:
``__generated__`` has no registered ``criteria_schema``, and the gate
deliberately has no opinion on an unregistered type, so gating against it would
check nothing at all.

These tests live in their own module rather than in
``tests/test_goal_generation_lifecycle.py``, which owns the rest of this
endpoint's coverage: that file carries pre-existing ruff findings and is
unformatted, and the required ``lint`` check runs on changed files — so appending
here would have turned it red or forced an unrelated reformat of 900 lines.
"""

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings

from . import utils_goal_generation as gen
from .utils_goal_generation import (
    GENERATION_REQUEST_BODY,
    _auth,
    _ensure_session,
    _write_state_yaml,
    make_client,
)

# Bound by assignment, not imported by name: pytest only applies a fixture that
# is visible in the test module's namespace, and importing these two would both
# read as unused to ruff and collide with the parameter of every test that
# requests them (F401 + F811, which is exactly the debt the module docstring
# explains this file exists to avoid).
temp_directions_path = gen.temp_directions_path
mock_synthesize_direction = gen.mock_synthesize_direction

MODULE_NAME = "pushup_counter"

#: A schema whose one criterion is mandatory — the shape a generated goal type
#: has whenever its verifier needs a threshold to compare against.
REQUIRES_A_COUNT = {
    "type": "object",
    "properties": {"target_count": {"type": "integer"}},
    "required": ["target_count"],
}

#: The "at least one checkable requirement" contract, declared the way
#: ``github_repo`` declares it, since plain ``required`` cannot express a choice.
REQUIRES_ONE_OF = {
    "type": "object",
    "properties": {
        "min_reps": {"type": "integer"},
        "required_angles": {"type": "array", "items": {"type": "string"}},
    },
    "anyOf": [{"required": ["min_reps"]}, {"required": ["required_angles"]}],
}


async def _force_criteria(goal_id: str, criteria: dict) -> None:
    """Replace a goal's stored criteria_data.

    Straight SQL because the request route writes only the placeholder: this is
    how a generated goal that *does* carry its module's criteria is simulated,
    and how the pre-gate rows that exist in production today are reproduced.
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


async def _request_accept(
    client,
    token,
    session_id,
    directions_root,
    *,
    criteria_schema,
    criteria_override=None,
    supplied_criteria=None,
):
    """Request a generated type, mark it merged, register it, and accept it.

    Returns ``(goal_id, accept_response)``. The registry insertion mirrors
    ``tests/test_goal_generation_lifecycle.py``: the factory chain's merge
    migration is what installs a module for real, and the endpoint refuses to
    activate a type it cannot resolve.
    """
    resp = await client.post(
        f"/api/chat/sessions/{session_id}/request-new-goal-type",
        headers={"Authorization": f"Bearer {token}"},
        json=GENERATION_REQUEST_BODY,
    )
    assert resp.status_code == 202, resp.text
    goal_id = resp.json()["goal_id"]
    direction_id = resp.json()["direction_id"]

    _write_state_yaml(
        directions_root,
        direction_id,
        "pr_merged",
        pr_url="https://github.com/xvanov/sacrifice/pull/47",
    )

    if criteria_override is not None:
        await _force_criteria(goal_id, criteria_override)

    import app.goal_types.registry as registry
    from app.goal_types.registry import _DynamicGoalType

    async def _fake_verify(proof_data, criteria_data):
        return {"status": "verified"}

    registry._registry[MODULE_NAME] = _DynamicGoalType(
        name=MODULE_NAME,
        description="Count pushups from video",
        sample_prompts=["Do 20 pushups"],
        criteria_schema=criteria_schema,
        verify=_fake_verify,
    )
    try:
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json=(
                None if supplied_criteria is None else {"criteria": supplied_criteria}
            ),
        )
    finally:
        registry._registry.pop(MODULE_NAME, None)
    return goal_id, resp


async def _goal(client, token, goal_id) -> dict:
    resp = await client.get(
        f"/api/goals/{goal_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_accept_refuses_criteria_the_module_cannot_verify(temp_directions_path):
    """A required criterion the goal does not carry is a 422, naming the field.

    Defaulting it instead would invent a commitment the owner never made and then
    charge them for missing it, which is the rule ``criteria_gate`` exists to
    hold: no invented defaults.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-gate-required")

        goal_id, resp = await _request_accept(
            client,
            token,
            "sess-gate-required",
            temp_directions_path,
            criteria_schema=REQUIRES_A_COUNT,
        )

        assert resp.status_code == 422, resp.text
        assert "target_count" in resp.json()["detail"], (
            "the refusal has to name the missing criterion; 'no' alone leaves the "
            "owner nothing to act on"
        )


async def test_a_refused_accept_leaves_the_goal_exactly_as_it_was(
    temp_directions_path,
):
    """No half-switched goal, and nothing chargeable.

    The gate runs before the status and goal_type assignments, and before the
    acceptance notification, so a refusal is fully recoverable: the goal is still
    ``awaiting_goal_type``, still the placeholder type, and still acceptable once
    its criteria exist. ``awaiting_goal_type`` is also skipped by the deadline
    sweep, so a refused accept cannot be charged while it waits.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-gate-intact")

        goal_id, resp = await _request_accept(
            client,
            token,
            "sess-gate-intact",
            temp_directions_path,
            criteria_schema=REQUIRES_A_COUNT,
        )
        assert resp.status_code == 422, resp.text

        body = await _goal(client, token, goal_id)
        assert body["status"] == "awaiting_goal_type"
        assert body["goal_type"] == "__generated__"
        assert body["awaiting_direction_id"] is not None, (
            "the direction linkage must survive so the goal can be accepted later"
        )


async def test_accept_refuses_when_no_criterion_is_checkable(temp_directions_path):
    """The ``anyOf`` half of the contract.

    Criteria satisfying no alternative name nothing to check, so the verifier can
    only ever answer "we checked nothing" — which is not evidence the goal was
    met, and must not be activated as though it were.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-gate-anyof")

        _, resp = await _request_accept(
            client,
            token,
            "sess-gate-anyof",
            temp_directions_path,
            criteria_schema=REQUIRES_ONE_OF,
        )

        assert resp.status_code == 422, resp.text
        assert "checkable" in resp.json()["detail"].lower()


async def test_accept_activates_when_the_criteria_are_there(temp_directions_path):
    """The gate must not become a wall in front of every generated goal.

    Same schema as the refusal above with the criterion present, and supplied as
    the string an LLM-collected value arrives as — so this also pins that accept
    *coerces* (``"20"`` -> ``20``) rather than rejecting a value whose intent is
    unambiguous. Without this test, a gate that refused unconditionally would
    satisfy every other assertion in this file.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-gate-ok")

        goal_id, resp = await _request_accept(
            client,
            token,
            "sess-gate-ok",
            temp_directions_path,
            criteria_schema=REQUIRES_A_COUNT,
            criteria_override={
                "generated": True,
                "direction_id": "042-pushup-counter",
                "module_name": MODULE_NAME,
                "target_count": "20",
            },
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"

        body = await _goal(client, token, goal_id)
        assert body["status"] == "active"
        assert body["goal_type"] == MODULE_NAME

        criteria_data = body["criteria"]["criteria_data"]
        assert criteria_data["target_count"] == 20, (
            "the gate coerces on this path too; storing the string leaves a "
            "criterion the verifier compares against an int and can never match"
        )
        # The placeholder migration still happens after the gate.
        assert criteria_data.get("generated") is None
        assert criteria_data.get("direction_id") is None
        assert criteria_data["module_name"] == MODULE_NAME


async def test_criteria_supplied_at_acceptance_satisfy_the_gate(temp_directions_path):
    """The gate needs an input path, or it is a wall.

    Acceptance is the first moment the new module's ``criteria_schema`` is
    knowable — the goal was created before the type existed — so it is the only
    place its criteria can be collected. Without this, a generated type that
    declares required criteria could never be accepted at all, and the custom goal
    the user waited for would be a dead end.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-gate-body")

        goal_id, resp = await _request_accept(
            client,
            token,
            "sess-gate-body",
            temp_directions_path,
            criteria_schema=REQUIRES_A_COUNT,
            supplied_criteria={"target_count": 20},
        )

        assert resp.status_code == 200, resp.text
        body = await _goal(client, token, goal_id)
        assert body["status"] == "active"
        assert body["criteria"]["criteria_data"]["target_count"] == 20


async def test_supplied_criteria_cannot_drop_the_module_name(temp_directions_path):
    """``module_name`` is load-bearing and is merged over, not replaced.

    Every later dispatch resolves the verifier through it, and the accept route
    reads it to decide which type the goal becomes. A client sending only its own
    criteria must not be able to erase it.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-gate-keepname")

        goal_id, resp = await _request_accept(
            client,
            token,
            "sess-gate-keepname",
            temp_directions_path,
            criteria_schema=REQUIRES_A_COUNT,
            supplied_criteria={"target_count": 5, "module_name": "something_else"},
        )

        assert resp.status_code == 200, resp.text
        body = await _goal(client, token, goal_id)
        assert body["goal_type"] == MODULE_NAME
        assert body["criteria"]["criteria_data"]["module_name"] == MODULE_NAME
        assert body["criteria"]["criteria_type"] == MODULE_NAME


async def test_supplied_criteria_are_gated_not_trusted(temp_directions_path):
    """Arriving in the request body does not exempt a value from the gate.

    ``"about twenty"`` is not a number and cannot be coerced into one without
    guessing; storing it would leave a criterion the verifier can never match,
    which is the charge this gate exists to prevent.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-gate-badbody")

        _, resp = await _request_accept(
            client,
            token,
            "sess-gate-badbody",
            temp_directions_path,
            criteria_schema=REQUIRES_A_COUNT,
            supplied_criteria={"target_count": "about twenty"},
        )

        assert resp.status_code == 422, resp.text
        assert "target_count" in resp.json()["detail"]


async def _generation_status(client, token, session_id) -> dict:
    resp = await client.get(
        f"/api/chat/sessions/{session_id}/generation-status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _request_and_merge(client, token, session_id, directions_root):
    """Get a goal to ``pr_merged`` without accepting it. Returns the goal id."""
    resp = await client.post(
        f"/api/chat/sessions/{session_id}/request-new-goal-type",
        headers={"Authorization": f"Bearer {token}"},
        json=GENERATION_REQUEST_BODY,
    )
    assert resp.status_code == 202, resp.text
    _write_state_yaml(
        directions_root,
        resp.json()["direction_id"],
        "pr_merged",
        pr_url="https://github.com/xvanov/sacrifice/pull/47",
    )
    return resp.json()["goal_id"]


def _register(criteria_schema):
    """Register the merged module, as the factory chain's migration would."""
    import app.goal_types.registry as registry
    from app.goal_types.registry import _DynamicGoalType

    async def _fake_verify(proof_data, criteria_data):
        return {"status": "verified"}

    registry._registry[MODULE_NAME] = _DynamicGoalType(
        name=MODULE_NAME,
        description="Count pushups from video",
        sample_prompts=["Do 20 pushups"],
        criteria_schema=criteria_schema,
        verify=_fake_verify,
    )
    return registry


async def test_generation_status_names_the_criteria_still_needed(temp_directions_path):
    """The 422 should be rare, which means the client has to be told in advance.

    ``pr_merged`` is the first moment the new module's schema exists to read, so it
    is the first moment anyone can know what to collect. Without this the client's
    only way to discover a required criterion is to call accept and be refused.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-status-gap")
        await _request_and_merge(client, token, "sess-status-gap", temp_directions_path)

        registry = _register(REQUIRES_A_COUNT)
        try:
            body = await _generation_status(client, token, "sess-status-gap")
        finally:
            registry._registry.pop(MODULE_NAME, None)

    assert body["status"] == "pr_merged"
    assert body["missing_criteria"] == ["target_count"]
    assert body["criteria_schema"]["properties"]["target_count"]["type"] == "integer", (
        "the schema travels with the names so a client can render the right input "
        "instead of guessing from the field name"
    )


async def test_generation_status_names_an_any_of_alternative(temp_directions_path):
    """It asks the same two questions the gate will ask, including ``anyOf``.

    Answering a different question here produces a client that collects the wrong
    things and still gets refused.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-status-anyof")
        await _request_and_merge(
            client, token, "sess-status-anyof", temp_directions_path
        )

        registry = _register(REQUIRES_ONE_OF)
        try:
            body = await _generation_status(client, token, "sess-status-anyof")
        finally:
            registry._registry.pop(MODULE_NAME, None)

    assert body["missing_criteria"], "an unsatisfied anyOf contract must be reported"
    assert body["missing_criteria"][0] in {"min_reps", "required_angles"}


async def test_generation_status_reports_no_gap_when_there_is_none(
    temp_directions_path,
):
    """A module that declares no required criteria asks for nothing.

    Otherwise the chat would interrogate its user about a type that needs no
    answers, which is worse than the 422 this feature exists to avoid.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-status-nogap")
        await _request_and_merge(
            client, token, "sess-status-nogap", temp_directions_path
        )

        registry = _register(
            {"type": "object", "properties": {"count": {"type": "integer"}}}
        )
        try:
            body = await _generation_status(client, token, "sess-status-nogap")
        finally:
            registry._registry.pop(MODULE_NAME, None)

    assert body["missing_criteria"] == []


async def test_generation_status_asks_for_nothing_before_the_merge(
    temp_directions_path,
):
    """Nothing to ask about yet: the module — and its schema — do not exist."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-status-early")

        resp = await client.post(
            "/api/chat/sessions/sess-status-early/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202, resp.text
        _write_state_yaml(
            temp_directions_path, resp.json()["direction_id"], "in_progress"
        )

        body = await _generation_status(client, token, "sess-status-early")

    assert body["status"] == "in_progress"
    assert body["missing_criteria"] == []
    assert body["criteria_schema"] is None


async def test_what_status_reports_is_exactly_what_accept_requires(
    temp_directions_path,
):
    """The two must not drift.

    A gap list that does not match what the gate enforces is worse than no gap
    list: the client collects, submits, and is refused anyway. So collect precisely
    what ``generation-status`` named, and assert accept is then satisfied.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-status-contract")
        await _request_and_merge(
            client, token, "sess-status-contract", temp_directions_path
        )

        registry = _register(REQUIRES_A_COUNT)
        try:
            body = await _generation_status(client, token, "sess-status-contract")
            supplied = {field: 7 for field in body["missing_criteria"]}
            resp = await client.post(
                "/api/chat/sessions/sess-status-contract/accept-generated-type",
                headers={"Authorization": f"Bearer {token}"},
                json={"criteria": supplied},
            )
        finally:
            registry._registry.pop(MODULE_NAME, None)

        assert resp.status_code == 200, (
            f"collecting exactly what generation-status asked for must satisfy "
            f"the accept gate, got {resp.status_code}: {resp.text}"
        )


async def test_accept_still_works_for_a_module_that_declares_no_criteria(
    temp_directions_path,
):
    """An unconstrained schema is not a criteria failure.

    A generated module may legitimately declare no required criteria — its
    verifier reads the proof alone. The gate has no opinion there, and must not
    invent one, or every early generated goal type becomes unacceptable.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-gate-open")

        _, resp = await _request_accept(
            client,
            token,
            "sess-gate-open",
            temp_directions_path,
            criteria_schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
            },
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"
