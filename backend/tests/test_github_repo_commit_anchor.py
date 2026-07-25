"""``min_commits`` counts from the goal, not from the beginning of the repo.

The bug these pin: ``app/workers/github_repo.py`` counted a branch's entire
history, so "push 3 commits by Saturday" was satisfied by three commits pushed
last year. Every real repository already has commits in it, so the criterion the
chat collects most often was very close to unconditionally true.

The fix is one server-assigned criterion, ``commits_since``, stamped with the
goal's creation time by ``app/services/criteria_gate.stamp_goal_created_at`` and
used as GitHub's ``since`` parameter. Which makes this a money change in the
dangerous direction: narrowing what counts turns a ``verified`` into a
``failed``, and ``failed`` charges a real card
(``app/services/verification_result.py``). So the tests below are as much about
what the anchor must *not* do:

* a goal with no anchor — every goal that existed before this — is counted
  exactly as it was, whole history, so nobody is re-judged under a rule that did
  not exist when they committed;
* an anchor we cannot use (unreadable, future-dated) is *ours* under
  ``app/services/fault_attribution`` and comes back ``inconclusive``, never a
  charge, because "0 commits since <garbage>" is precisely how a wrong anchor
  bills someone who did the work;
* the anchor is the goal's *creation* time, the earliest of the candidate
  anchors, so work done on a draft still counts;
* a user cannot supply their own anchor, or they would point it before the
  history they already have and the free pass is back.

Hermetic: ``httpx.AsyncClient`` is patched for the verifier tests. The two
end-to-end tests hit the API and the database, which is where the stamping
actually has to happen.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.main import app
from app.services.criteria_gate import stamp_goal_created_at
from app.services.verification_result import (
    REASON_CRITERIA_NOT_EVALUABLE,
    REASON_INTERNAL_ERROR,
)
from app.workers.github_repo import (
    COMMITS_SINCE_FIELD,
    FAILED,
    INCONCLUSIVE,
    VERIFIED,
    _resolve_commit_anchor,
    verify_github_repo,
)

REPO = "https://github.com/octocat/Hello-World"
ANCHOR = "2026-07-01T12:00:00Z"


# ─── Mock helpers (mirroring tests/test_github_repo_criteria.py) ────


def _resp(status_code=200, json_data=None, headers=None, text_body=""):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = [] if json_data is None else json_data
    mock.headers = {} if headers is None else headers
    mock.text = text_body
    return mock


def _commits_page(count: int):
    """A ``per_page=1`` commits response advertising ``count`` total commits.

    That is how ``_count_commits`` reads a total: the ``rel="last"`` page number
    with one item per page *is* the count. ``count == 0`` is the empty response,
    which has no Link header at all.
    """
    if count == 0:
        return _resp(200, [])
    if count == 1:
        return _resp(200, [{"sha": "a"}])
    base = "https://api.github.com/repositories/1/commits"
    return _resp(
        200,
        [{"sha": "a"}],
        headers={
            "Link": (
                f'<{base}?per_page=1&page=2>; rel="next", '
                f'<{base}?per_page=1&page={count}>; rel="last"'
            )
        },
    )


async def _verify(criteria, responses, proof=None):
    instance = AsyncMock()
    instance.get.side_effect = list(responses)
    cls = MagicMock()
    cls.return_value.__aenter__.return_value = instance
    cls.return_value.__aexit__.return_value = False
    with patch("app.workers.github_repo.httpx.AsyncClient", cls):
        result = await verify_github_repo(proof or {"repo_url": REPO}, criteria)
    return result, instance


def _sent_params(instance) -> dict:
    """The query params of the first GitHub call."""
    _, kwargs = instance.get.call_args_list[0]
    return kwargs.get("params") or {}


def _check(result, check_type):
    for entry in result["verification_details"]["condition_results"]:
        if entry["type"] == check_type:
            return entry
    raise AssertionError(
        f"no {check_type} result in "
        f"{result['verification_details']['condition_results']}"
    )


# ─── The anchor reaches GitHub ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_anchor_is_sent_to_github_as_since():
    """Without ``since`` on the request, the count is the whole history.

    This is the actual fix: the parameter has to be on the wire. Asserting the
    outcome alone would pass against a build that anchored nothing, because a repo
    with enough total commits verifies either way.
    """
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "min_commits": 3,
        COMMITS_SINCE_FIELD: ANCHOR,
    }
    result, instance = await _verify(criteria, [_commits_page(3)])

    assert _sent_params(instance)["since"] == ANCHOR
    assert result["verification_status"] == VERIFIED


@pytest.mark.asyncio
async def test_a_goal_without_an_anchor_counts_the_whole_history():
    """The no-charge guarantee for every goal that already exists.

    Goals created before ``commits_since`` have no anchor, and their owners
    committed under whole-history counting. Sending a ``since`` we invented — or
    defaulting one to "now" — would fail goals that were passing yesterday and
    charge for it.
    """
    criteria = {"repo_owner": "octocat", "repo_name": "Hello-World", "min_commits": 3}
    result, instance = await _verify(criteria, [_commits_page(3)])

    assert "since" not in _sent_params(instance)
    assert result["verification_status"] == VERIFIED
    assert _check(result, "min_commits")["since_date"] is None


# ─── Boundaries ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_qualifying_commits_fails_and_says_which_window():
    """A repo full of old commits and none since the goal is a real miss.

    It is also the case most likely to read as a bug to the person charged, so the
    verdict has to name the window. "Found 0 commits, need at least 3" to someone
    whose repo shows hundreds is indistinguishable from us being broken.
    """
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "min_commits": 3,
        COMMITS_SINCE_FIELD: ANCHOR,
    }
    result, _ = await _verify(criteria, [_commits_page(0)])

    assert result["verification_status"] == FAILED
    check = _check(result, "min_commits")
    assert check["actual"] == 0
    assert check["since_date"] == ANCHOR
    assert ANCHOR in check["failure_reason"]
    assert ANCHOR in result["verification_details"]["failure_reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actual,expected",
    [
        (2, FAILED),  # one short
        (3, VERIFIED),  # exactly the threshold
        (4, VERIFIED),  # one over
    ],
)
async def test_the_threshold_boundary_is_at_least_not_more_than(actual, expected):
    """``min_commits`` is a minimum: equal passes.

    Pinned because an off-by-one here is a charge for someone who did exactly
    what they promised.
    """
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "min_commits": 3,
        COMMITS_SINCE_FIELD: ANCHOR,
    }
    result, _ = await _verify(criteria, [_commits_page(actual)])

    assert result["verification_status"] == expected


@pytest.mark.asyncio
async def test_commits_before_the_anchor_do_not_count_toward_the_goal():
    """The explicit decision for a goal created after commits were pushed.

    GitHub applies ``since`` server-side, so the count this worker receives is
    already the post-anchor one — which is what the two responses here model: the
    same repository answers 12 without ``since`` and 1 with it. The goal asked for
    3 commits and one was made after the commitment, so it fails.

    The alternative reading — credit work done before the goal existed — makes the
    product meaningless: any goal on any established repo is met at creation.
    """
    unanchored = {"repo_owner": "octocat", "repo_name": "Hello-World", "min_commits": 3}
    anchored = {**unanchored, COMMITS_SINCE_FIELD: ANCHOR}

    before, _ = await _verify(unanchored, [_commits_page(12)])
    after, instance = await _verify(anchored, [_commits_page(1)])

    assert before["verification_status"] == VERIFIED, (
        "whole-history counting is what the old behaviour was, and it is what a "
        "goal with no anchor must still get"
    )
    assert after["verification_status"] == FAILED
    assert _sent_params(instance)["since"] == ANCHOR
    assert _check(after, "min_commits")["actual"] == 1


@pytest.mark.asyncio
async def test_commits_after_the_anchor_count():
    """The mirror case, and the one that must not regress into a charge."""
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "min_commits": 3,
        COMMITS_SINCE_FIELD: ANCHOR,
    }
    result, _ = await _verify(criteria, [_commits_page(5)])

    assert result["verification_status"] == VERIFIED
    assert "failure_reason" not in result["verification_details"]


# ─── An anchor we cannot use is ours, and never charges ────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_anchor",
    [
        "not a timestamp",
        "2026-13-45T99:00:00Z",
        12345,
        ["2026-07-01T00:00:00Z"],
    ],
)
async def test_an_unreadable_anchor_is_inconclusive_not_a_failure(bad_anchor):
    """We wrote this field; the user cannot edit it.

    So under the rule in ``app/services/fault_attribution`` it is ours, and the
    only safe reading is "we do not know which commits count". Treating it as
    ``since=<garbage>`` — or silently dropping it — would either bill a user over
    our own bad value or hand back the free pass.
    """
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "min_commits": 3,
        COMMITS_SINCE_FIELD: bad_anchor,
    }
    result, instance = await _verify(criteria, [])

    assert result["verification_status"] == INCONCLUSIVE
    assert result["inconclusive_reason"] == REASON_CRITERIA_NOT_EVALUABLE
    assert instance.get.await_count == 0, (
        "an anchor we cannot read must not be sent to GitHub as a window"
    )
    assert COMMITS_SINCE_FIELD in _check(result, "min_commits")["failure_reason"]


@pytest.mark.asyncio
async def test_a_future_anchor_is_inconclusive_rather_than_zero_commits():
    """The wrong-anchor charge, refused by name.

    A ``since`` in the future matches no commit that can exist, so honouring one
    reports zero for a repository the user may have filled and charges them for
    our clock. Inconclusive keeps the money safe and puts the row in front of an
    operator (``app/services/blocked_goals.py``).
    """
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "min_commits": 3,
        COMMITS_SINCE_FIELD: (
            datetime.now(timezone.utc) + timedelta(days=2)
        ).isoformat(),
    }
    result, instance = await _verify(criteria, [])

    assert result["verification_status"] == INCONCLUSIVE
    assert result["inconclusive_reason"] == REASON_CRITERIA_NOT_EVALUABLE
    assert instance.get.await_count == 0
    assert "future" in _check(result, "min_commits")["failure_reason"]


def test_a_marginally_future_anchor_is_treated_as_now():
    """Clock skew between the stamping instance and the verifying one.

    Seconds of skew must not make a goal inconclusive: using an anchor a moment
    ahead only excludes a window that closed before the goal existed, so it is
    harmless, while an operator alert per skewed goal is not.
    """
    soon = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    since, problem = _resolve_commit_anchor({COMMITS_SINCE_FIELD: soon})

    assert problem is None
    assert since is not None


def test_a_missing_or_blank_anchor_is_not_a_problem():
    """Absent is the legacy shape, and it is legitimate — not an error."""
    for value in ({}, {COMMITS_SINCE_FIELD: None}, {COMMITS_SINCE_FIELD: "   "}):
        assert _resolve_commit_anchor(value) == (None, None)


def test_sub_second_precision_is_dropped_downward():
    """Rounding direction is a charge decision.

    Truncating toward the past can only widen the window by a fraction of a
    second, so the boundary commit is included rather than lost. Rounding up
    could drop the very commit a user pushed at creation time.
    """
    since, problem = _resolve_commit_anchor(
        {COMMITS_SINCE_FIELD: "2026-07-01T12:00:00.987654+00:00"}
    )

    assert problem is None
    assert since == "2026-07-01T12:00:00Z"


def test_a_naive_anchor_is_read_as_utc():
    """Matches ``_as_utc`` everywhere else in this codebase.

    A stored value without an offset is one we wrote before, or outside, the
    stamping path; reading it as local time would shift the window by hours in
    an unpredictable direction.
    """
    since, problem = _resolve_commit_anchor(
        {COMMITS_SINCE_FIELD: "2026-07-01T12:00:00"}
    )

    assert problem is None
    assert since == "2026-07-01T12:00:00Z"


# ─── The legacy ``conditions`` shape gets the same anchor ──────────


@pytest.mark.asyncio
async def test_a_legacy_commits_condition_without_its_own_window_uses_the_anchor():
    """``conditions`` is still a documented, creatable shape.

    It had the identical unanchored-history hole, so a fix that only covered
    ``min_commits`` would leave the free pass reachable by writing the criteria
    the other way.
    """
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "conditions": [{"type": "commits", "min_count": 3}],
        COMMITS_SINCE_FIELD: ANCHOR,
    }
    result, instance = await _verify(criteria, [_commits_page(0)])

    assert _sent_params(instance)["since"] == ANCHOR
    assert result["verification_status"] == FAILED


@pytest.mark.asyncio
async def test_a_legacy_condition_keeps_its_own_since_date():
    """An explicit window is the more specific statement, and it wins.

    These goals were written against a stated date; overriding it with the goal's
    creation time would silently change what an existing goal measures.
    """
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "conditions": [
            {"type": "commits", "min_count": 1, "since_date": "2020-01-01T00:00:00Z"}
        ],
        COMMITS_SINCE_FIELD: ANCHOR,
    }
    result, instance = await _verify(criteria, [_commits_page(1)])

    assert _sent_params(instance)["since"] == "2020-01-01T00:00:00Z"
    assert result["verification_status"] == VERIFIED


@pytest.mark.asyncio
async def test_a_broken_anchor_does_not_stall_a_check_that_never_needed_it():
    """Blast radius again, from the other side.

    ``min_commits: 0`` runs no count, so the window it would have been counted
    over is irrelevant — and a ``required_files`` check sitting next to it is
    perfectly runnable. Testing the anchor before the degenerate threshold would
    make the whole goal inconclusive on a field it does not use.
    """
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "min_commits": 0,
        "required_files": ["README.md"],
        COMMITS_SINCE_FIELD: "not a timestamp",
    }
    result, _ = await _verify(criteria, [_resp(200, {"path": "README.md"})])

    assert result["verification_status"] == VERIFIED


@pytest.mark.asyncio
async def test_a_broken_anchor_does_not_taint_a_condition_with_its_own_window():
    """Scoped blast radius: only checks that would have *used* the anchor stall."""
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "conditions": [
            {"type": "commits", "min_count": 1, "since_date": "2020-01-01T00:00:00Z"}
        ],
        COMMITS_SINCE_FIELD: "not a timestamp",
    }
    result, _ = await _verify(criteria, [_commits_page(1)])

    assert result["verification_status"] == VERIFIED


# ─── A count we cannot bound is not a count ────────────────────────


@pytest.mark.asyncio
async def test_exhausting_the_page_cap_is_inconclusive_not_a_low_count():
    """A floor reported as a total is a wrongful charge waiting to happen.

    ``_walk_commit_count`` walks at most ``_MAX_PAGES`` pages so one enormous
    repository cannot pin the worker. That cap is a real constraint; the bug was
    returning the partial sum as though it were the answer. A partial sum is only
    ever *lower* than the truth, so for any threshold above the cap it fails a
    user who did far more than they promised.
    """
    from app.workers.github_repo import _MAX_PAGES, _PAGE_SIZE

    full_page = _resp(200, [{"sha": f"c{i}"} for i in range(_PAGE_SIZE)])
    # First response drives ``_count_commits`` into the walk: a next link with no
    # last link is the only shape that gets there.
    first = _resp(
        200,
        [{"sha": "a"}],
        headers={
            "Link": (
                "<https://api.github.com/repositories/1/commits?per_page=1&page=2>; "
                'rel="next"'
            )
        },
    )
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "min_commits": 3,
        COMMITS_SINCE_FIELD: ANCHOR,
    }
    result, _ = await _verify(criteria, [first] + [full_page] * (_MAX_PAGES + 1))

    assert result["verification_status"] == INCONCLUSIVE, (
        "a count we know is short must never be reported as the total"
    )
    assert result["inconclusive_reason"] == REASON_INTERNAL_ERROR, (
        "ours, and permanent: the next walk stops at the same cap, so the row "
        "belongs with an operator rather than in the retry queue"
    )
    assert "could not be established" in _check(result, "min_commits")["error"]


@pytest.mark.asyncio
async def test_a_walk_that_ends_inside_the_cap_still_counts_normally():
    """The cap must only fire when it is actually reached.

    Without this, the guard above could be satisfied by making every walked count
    inconclusive — which would turn a working check into a permanent operator
    ticket for every repo that needs more than one page.
    """
    from app.workers.github_repo import _PAGE_SIZE

    first = _resp(
        200,
        [{"sha": "a"}],
        headers={
            "Link": (
                "<https://api.github.com/repositories/1/commits?per_page=1&page=2>; "
                'rel="next"'
            )
        },
    )
    full_page = _resp(200, [{"sha": f"c{i}"} for i in range(_PAGE_SIZE)])
    short_page = _resp(200, [{"sha": "tail"}])
    criteria = {
        "repo_owner": "octocat",
        "repo_name": "Hello-World",
        "min_commits": 3,
        COMMITS_SINCE_FIELD: ANCHOR,
    }
    result, _ = await _verify(criteria, [first, full_page, short_page])

    assert result["verification_status"] == VERIFIED
    assert _check(result, "min_commits")["actual"] == _PAGE_SIZE + 1


# ─── Stamping: who decides the anchor ──────────────────────────────


def test_the_stamp_overwrites_a_supplied_anchor():
    """A caller-chosen anchor is the free pass by another route.

    Point ``commits_since`` at 2020 and the repo's existing history counts again.
    The value is server-recorded, so the request's is discarded rather than
    merged.
    """
    created = datetime(2026, 7, 20, 9, 30, tzinfo=timezone.utc)
    stamped = stamp_goal_created_at(
        "github_repo",
        {
            "repo_owner": "o",
            "repo_name": "r",
            "min_commits": 3,
            COMMITS_SINCE_FIELD: "2020-01-01T00:00:00Z",
        },
        created_at=created,
    )

    assert stamped[COMMITS_SINCE_FIELD] == created.isoformat()


def test_the_stamp_leaves_a_goal_type_that_declares_no_anchor_alone():
    """Opt-in by schema. A type with no ``x-server-assigned`` field is untouched."""
    criteria = {"min_duration_seconds": 300, "video_description": "d"}

    assert (
        stamp_goal_created_at(
            "youtube_video", criteria, created_at=datetime.now(timezone.utc)
        )
        == criteria
    )


def test_the_stamp_does_not_mutate_its_input():
    """The caller's dict is request data; ``create_goal`` also reads it."""
    original = {"repo_owner": "o", "repo_name": "r", "min_commits": 1}
    stamp_goal_created_at(
        "github_repo", original, created_at=datetime.now(timezone.utc)
    )

    assert COMMITS_SINCE_FIELD not in original


def test_the_anchor_cannot_satisfy_the_at_least_one_check_contract():
    """It is not a check, and must not be mistaken for one.

    ``github_repo``'s ``anyOf`` exists so criteria naming only the repo are
    refused; if the stamped field counted as a checkable requirement, the gate
    would start accepting exactly the criteria it was written to reject.
    """
    from app.services.criteria_gate import CriteriaRejected, gate_criteria

    with pytest.raises(CriteriaRejected):
        gate_criteria(
            "github_repo",
            {
                "repo_owner": "o",
                "repo_name": "r",
                COMMITS_SINCE_FIELD: "2026-07-01T00:00:00Z",
            },
        )


# ─── End to end: the anchor is what creation actually stores ───────


def _make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _auth(client, email, sub):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {
            "email": email,
            "name": "Anchor",
            "sub": sub,
            "picture": None,
        }
        resp = await client.post("/api/auth/google", json={"token": "valid-token"})
        assert resp.status_code in (200, 201), resp.text
        return resp.json()["access_token"]


async def _stored_criteria(goal_id: str) -> dict:
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


async def _create(client, token, criteria):
    return await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Push three commits",
            "description": "d",
            "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "pledge_amount": 2500,
            "goal_type": "github_repo",
            "criteria": criteria,
        },
    )


@pytest.mark.asyncio
async def test_creating_a_github_goal_stores_an_anchor_at_creation_time():
    """The stamp has to happen on the write path, not only in a unit test.

    ``create_goal`` is the one function that writes a ``goal_criteria`` row, so
    every writer inherits the anchor there — including the chat's
    create-from-session path, which does not go through ``POST /api/goals``.
    """
    before = datetime.now(timezone.utc)
    async with _make_client() as client:
        token = await _auth(client, "anchor-create@example.com", "anchor-create")
        resp = await _create(
            client,
            token,
            {"repo_owner": "octocat", "repo_name": "Hello-World", "min_commits": 3},
        )
        assert resp.status_code == 201, resp.text

        stored = await _stored_criteria(resp.json()["id"])

    anchor = datetime.fromisoformat(stored[COMMITS_SINCE_FIELD])
    assert before <= anchor <= datetime.now(timezone.utc), (
        "the anchor must be the goal's creation instant: earlier would credit "
        "work done before the commitment, later would discard work already done"
    )


@pytest.mark.asyncio
async def test_a_client_supplied_anchor_is_replaced_on_creation():
    """End-to-end version of the overwrite: the API must not honour it either."""
    async with _make_client() as client:
        token = await _auth(client, "anchor-supplied@example.com", "anchor-supplied")
        resp = await _create(
            client,
            token,
            {
                "repo_owner": "octocat",
                "repo_name": "Hello-World",
                "min_commits": 3,
                COMMITS_SINCE_FIELD: "2010-01-01T00:00:00Z",
            },
        )
        assert resp.status_code == 201, resp.text

        stored = await _stored_criteria(resp.json()["id"])

    assert stored[COMMITS_SINCE_FIELD] != "2010-01-01T00:00:00Z"
    assert datetime.fromisoformat(stored[COMMITS_SINCE_FIELD]).year >= 2026


@pytest.mark.asyncio
async def test_activating_a_pre_anchor_draft_does_not_acquire_an_anchor():
    """Activation must not stamp: it would move the window forward.

    A draft created on Monday and activated on Wednesday would get a Wednesday
    anchor, discarding commits its owner really made on Tuesday and charging them
    for work they did. Creation is the only anchor that cannot do that, so the
    activation gate deliberately does not stamp — and a legacy draft that has no
    anchor stays unanchored.
    """
    import json as _json

    async with _make_client() as client:
        token = await _auth(client, "anchor-activate@example.com", "anchor-activate")
        resp = await _create(
            client,
            token,
            {"repo_owner": "octocat", "repo_name": "Hello-World", "min_commits": 3},
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

        # Strip the anchor to make this the pre-anchor row that exists in
        # production today, then activate through the gate.
        engine = create_async_engine(settings.database_url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE goal_criteria SET criteria_data = CAST(:d AS JSONB) "
                        "WHERE goal_id = :id"
                    ),
                    {
                        "d": _json.dumps(
                            {
                                "repo_owner": "octocat",
                                "repo_name": "Hello-World",
                                "min_commits": 3,
                            }
                        ),
                        "id": uuid.UUID(goal_id),
                    },
                )
        finally:
            await engine.dispose()

        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        assert resp.status_code == 200, resp.text

        stored = await _stored_criteria(goal_id)

    assert COMMITS_SINCE_FIELD not in stored
