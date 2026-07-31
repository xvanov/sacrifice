"""Tests for the declarative ``github_repo`` criteria, the vacuous-pass fix, and
the charge boundary.

``tests/test_github_repo.py`` covers the legacy ``conditions`` shape. This file
covers what that shape never exercised:

* the criteria the chat flow actually collects (``repo_owner``/``repo_name``/
  ``min_commits``/``required_files``/``require_pr``), which previously had no
  implementation at all;
* the invariant that a criteria set with nothing checkable in it must **not**
  come back ``verified`` — and, just as importantly, must not come back
  ``failed`` either, because ``failed`` charges the user's card;
* correct commit counting (the old code asked for ``per_page=1`` and used
  ``len(data)``, so the count could never exceed 1);
* the three-way outcome: which conditions are the user's fault (``failed``,
  charges) versus ours (``inconclusive``, never charges);
* error, branch-identity and token-handling paths.

Every test here is hermetic: ``httpx.AsyncClient`` is patched, no DB is
touched. The single opt-in live-network test at the bottom is skipped unless
``SACRIFICE_GITHUB_LIVE_TEST=1``.
"""

import os
import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.crypto import encrypt_token
from app.services.verification_result import (
    INCONCLUSIVE_REASONS,
    REASON_CRITERIA_NOT_EVALUABLE,
    REASON_INTERNAL_ERROR,
    REASON_UPSTREAM_RATE_LIMITED,
    REASON_UPSTREAM_UNAVAILABLE,
)
from app.workers.github_repo import (
    FAILED,
    INCONCLUSIVE,
    NO_CRITERIA_INCONCLUSIVE_DETAIL,
    NO_REPO_INCONCLUSIVE_DETAIL,
    REPO_MISMATCH_FAILURE_REASON,
    VERIFIED,
    verify_github_repo,
)


# ─── Mock helpers ──────────────────────────────────────────────────

REPO = "https://github.com/octocat/Hello-World"


def _resp(status_code=200, json_data=None, headers=None, text=""):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = [] if json_data is None else json_data
    mock.headers = {} if headers is None else headers
    mock.text = text
    return mock


def _client(responses):
    """Patch ``httpx.AsyncClient`` so successive ``get`` calls return
    ``responses`` in order. Returns ``(class_mock, instance_mock)``."""
    instance = AsyncMock()
    if callable(responses):
        instance.get.side_effect = responses
    else:
        instance.get.side_effect = list(responses)
    cls = MagicMock()
    cls.return_value.__aenter__.return_value = instance
    cls.return_value.__aexit__.return_value = False
    return cls, instance


def _link(page: int) -> dict:
    """A GitHub ``Link`` header advertising ``page`` as the last page."""
    base = "https://api.github.com/repositories/1/commits"
    return {
        "Link": (
            f'<{base}?per_page=1&page=2>; rel="next", '
            f'<{base}?per_page=1&page={page}>; rel="last"'
        )
    }


def _pr(number, state="open", merged_at=None, head_ref="feature/x", head_label=None):
    """A pulls-API entry. ``head`` matters: the verifier matches PRs on
    ``head.ref`` rather than using the ``head=owner:branch`` query filter."""
    return {
        "number": number,
        "state": state,
        "merged_at": merged_at,
        "head": {"ref": head_ref, "label": head_label or f"someone:{head_ref}"},
    }


async def _verify(proof, criteria, responses):
    cls, instance = _client(responses)
    with patch("app.workers.github_repo.httpx.AsyncClient", cls):
        result = await verify_github_repo(proof, criteria)
    return result, instance


def _check(result, check_type):
    for entry in result["verification_details"]["condition_results"]:
        if entry["type"] == check_type:
            return entry
    raise AssertionError(
        f"no {check_type} result in {result['verification_details']['condition_results']}"
    )


def _assert_inconclusive(result, expected_reason):
    """Assert a result is inconclusive *and persistable* under the contract.

    ``persist_verification_result`` raises unless the reason is in its closed
    allowlist and no top-level ``failure_reason`` accompanies it, so checking the
    status alone would pass for a result that blows up at the write. Every
    inconclusive expectation in this file goes through here.
    """
    details = result["verification_details"]
    assert result["verification_status"] == INCONCLUSIVE, details
    reason = result["inconclusive_reason"]
    assert reason == expected_reason, details
    assert reason in INCONCLUSIVE_REASONS
    assert "failure_reason" not in details, (
        "a top-level failure_reason says the USER failed; the contract refuses "
        f"to persist it alongside an inconclusive outcome: {details}"
    )
    return details


# ─── THE critical regression: nothing checkable must not pass ──────
#
# ...and must not charge either. Every case in this block resolves to
# ``inconclusive``: the user did not author these criteria and cannot edit them,
# so billing a pledge over them would trade a vacuous pass for a false charge.


@pytest.mark.asyncio
async def test_empty_criteria_does_not_verify():
    """The headline bug: the old worker iterated ``criteria_data["conditions"]``
    only. With the key absent — the normal case for chat-created goals — the
    loop body never ran and it returned ``verified`` having checked nothing.
    """
    cls, instance = _client([])
    with patch("app.workers.github_repo.httpx.AsyncClient", cls):
        result = await verify_github_repo({"repo_url": REPO}, {})

    details = _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    assert details["inconclusive_detail"] == NO_CRITERIA_INCONCLUSIVE_DETAIL
    # And it must not have burned a GitHub request to decide that.
    assert instance.get.await_count == 0


@pytest.mark.asyncio
async def test_declarative_criteria_without_checks_does_not_verify():
    """Criteria that name a repo but express no requirement (the exact shape
    ``definition.py`` requires — only ``repo_owner``/``repo_name``) are not
    evidence of anything. Old code: ``verified``."""
    result, instance = await _verify(
        {}, {"repo_owner": "octocat", "repo_name": "Hello-World"}, []
    )

    details = _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    assert details["inconclusive_detail"] == NO_CRITERIA_INCONCLUSIVE_DETAIL
    assert instance.get.await_count == 0


@pytest.mark.asyncio
async def test_degenerate_criteria_do_not_verify():
    """``min_commits: 0`` / ``required_files: []`` / ``require_pr: False``
    cannot fail, so they cannot certify. Each is recorded as skipped so the
    stored details show what was ignored and why. Old code: ``verified``."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {"min_commits": 0, "required_files": [], "require_pr": False},
        [],
    )

    details = _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    assert details["inconclusive_detail"] == NO_CRITERIA_INCONCLUSIVE_DETAIL
    assert _check(result, "min_commits")["skipped"] is True
    assert _check(result, "required_files")["skipped"] is True
    # require_pr: False produced no record at all before, contradicting the
    # "degenerate values are recorded" contract the other two follow.
    assert _check(result, "require_pr")["skipped"] is True
    assert details["repo_url"] == REPO


@pytest.mark.asyncio
async def test_require_pr_string_false_is_not_truthy():
    """``"false"`` survives a JSON round-trip as a non-empty string, so plain
    ``bool()`` switched on a check the goal had turned off — and then failed the
    goal for having no PR."""
    result, instance = await _verify(
        {"repo_url": REPO},
        {"require_pr": "false", "min_commits": 1},
        [_resp(json_data=[{"sha": "a"}])],
    )

    assert result["verification_status"] == VERIFIED
    assert _check(result, "require_pr")["skipped"] is True
    # Only the commit check went out; no /pulls request.
    assert instance.get.await_count == 1


@pytest.mark.asyncio
async def test_empty_tickets_list_does_not_verify():
    """A ``tickets_closed`` condition with no tickets used to set
    ``all_closed = True`` and pass."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {"conditions": [{"type": "tickets_closed", "tickets": []}]},
        [],
    )

    details = _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    assert details["inconclusive_detail"] == NO_CRITERIA_INCONCLUSIVE_DETAIL


@pytest.mark.asyncio
async def test_unsupported_condition_type_does_not_verify():
    """An unknown condition type used to append ``passed: False`` without ever
    setting ``failed``, so the overall status came back ``verified``.

    It resolves to ``inconclusive``, not ``failed``: legacy goals really do
    carry types like ``language_stats`` (the old goal-type description
    advertised it), and a condition we dropped support for must never become a
    permanently unpassable goal that charges on every attempt.
    """
    result, _ = await _verify(
        {"repo_url": REPO},
        {"conditions": [{"type": "language_stats", "min_percent": 80}]},
        [],
    )

    _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    entry = _check(result, "language_stats")
    assert entry["passed"] is False
    assert entry["inconclusive"] is True
    assert entry["inconclusive_reason"] == REASON_CRITERIA_NOT_EVALUABLE
    assert "Unsupported condition type" in entry["failure_reason"]


@pytest.mark.asyncio
async def test_unsupported_condition_blocks_verified_even_when_others_pass():
    """Fail closed on the *verdict* without charging: one unverifiable promise
    stops ``verified``, but cannot manufacture a chargeable failure."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {
            "min_commits": 1,
            "conditions": [{"type": "language_stats", "min_percent": 80}],
        },
        [_resp(json_data=[{"sha": "a"}])],
    )

    _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    assert _check(result, "min_commits")["passed"] is True
    assert _check(result, "language_stats")["passed"] is False


@pytest.mark.asyncio
async def test_confirmed_failure_outranks_inconclusive():
    """Criteria are conjunctive, so a definite miss stays chargeable even when a
    sibling check was rate-limited. Otherwise a rate limit would launder every
    genuine failure into a free pass."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {"min_commits": 5, "require_pr": True},
        [
            _resp(json_data=[{"sha": "a"}]),  # 1 commit, need 5
            _resp(status_code=429, text="API rate limit exceeded"),  # inconclusive
        ],
    )

    assert result["verification_status"] == FAILED
    assert _check(result, "min_commits")["passed"] is False
    assert _check(result, "require_pr")["inconclusive"] is True
    # A failed verdict carries no reason code: the contract rejects a status
    # that is partly inconclusive, and this must charge.
    assert result["inconclusive_reason"] is None

    details = result["verification_details"]
    # The user-facing reason names only the criterion they were measured
    # against. The rate-limited sibling is recorded apart from it, so the
    # failure notification cannot claim they missed a check we never ran.
    assert "need at least 5" in details["failure_reason"]
    assert "require_pr" not in details["failure_reason"]
    assert any("require_pr" in text for text in details["unresolved_checks"])


# ─── Repo identity reconciliation ──────────────────────────────────


@pytest.mark.asyncio
async def test_criteria_owner_name_used_when_proof_has_no_url():
    """``repo_owner``/``repo_name`` alone used to hit ``_parse_repo_url("")``
    and raise a bare ValueError."""
    result, instance = await _verify(
        {},
        {"repo_owner": "octocat", "repo_name": "Hello-World", "min_commits": 1},
        [_resp(json_data=[{"sha": "a"}])],
    )

    assert result["verification_status"] == VERIFIED
    details = result["verification_details"]
    assert (details["owner"], details["repo"]) == ("octocat", "Hello-World")
    called_url = instance.get.call_args_list[0].args[0]
    assert called_url == "https://api.github.com/repos/octocat/Hello-World/commits"


@pytest.mark.asyncio
async def test_no_repo_identity_at_all_does_not_charge():
    result, instance = await _verify({}, {"min_commits": 1}, [])

    details = _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    assert details["inconclusive_detail"] == NO_REPO_INCONCLUSIVE_DETAIL
    assert instance.get.await_count == 0


@pytest.mark.asyncio
async def test_proof_repo_must_match_criteria_repo():
    """Proof pointing at a different repo than the goal named is not proof.
    Old code read only ``repo_url``, so the criteria repo was never compared.
    This one IS chargeable: the user chose where to submit from."""
    result, instance = await _verify(
        {"repo_url": "https://github.com/attacker/scratch"},
        {"repo_owner": "octocat", "repo_name": "Hello-World", "min_commits": 1},
        [],
    )

    assert result["verification_status"] == FAILED
    details = result["verification_details"]
    assert details["failure_reason"] == REPO_MISMATCH_FAILURE_REASON
    assert details["expected_repo"] == "octocat/Hello-World"
    assert details["submitted_repo"] == "attacker/scratch"
    assert instance.get.await_count == 0


@pytest.mark.asyncio
async def test_matching_proof_and_criteria_repo_is_accepted():
    """``submit_proof`` fills the proof ``repo_url`` into criteria; the
    owner/name fields must still win, and an agreeing pair must not trip the
    mismatch guard (case-insensitively).

    Asserts the resolved identity and the URL actually called — status alone
    would have passed against the old code, which returned ``verified``
    vacuously without resolving or calling anything.
    """
    result, instance = await _verify(
        {"repo_url": "https://github.com/OctoCat/hello-world"},
        {
            "repo_owner": "octocat",
            "repo_name": "Hello-World",
            "repo_url": "https://github.com/OctoCat/hello-world",
            "min_commits": 1,
        },
        [_resp(json_data=[{"sha": "a"}])],
    )

    assert result["verification_status"] == VERIFIED
    details = result["verification_details"]
    assert (details["owner"], details["repo"]) == ("octocat", "Hello-World")
    assert instance.get.await_count == 1
    assert instance.get.call_args_list[0].args[0] == (
        "https://api.github.com/repos/octocat/Hello-World/commits"
    )


# ─── Dotted repo names ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/mrdoob/three.js", ("mrdoob", "three.js")),
        ("https://github.com/octocat/dotfiles.old", ("octocat", "dotfiles.old")),
        ("https://github.com/kalin/kalin.github.io", ("kalin", "kalin.github.io")),
        ("git@github.com:mrdoob/three.js.git", ("mrdoob", "three.js")),
        ("https://github.com/octocat/Hello-World.git", ("octocat", "Hello-World")),
        (
            "https://github.com/octocat/Hello-World/tree/main",
            ("octocat", "Hello-World"),
        ),
    ],
)
def test_dotted_and_suffixed_repo_urls_parse_whole(url, expected):
    """``[^/.]+`` truncated at the first dot, so ``three.js`` became ``three``.
    Harmless until the mismatch guard compared the truncation against the real
    ``repo_name`` — then every dotted repo became a false "wrong repository"
    failure that charged the pledge."""
    from app.workers.github_repo import _parse_repo_url

    assert _parse_repo_url(url) == expected


@pytest.mark.asyncio
async def test_dotted_repo_name_does_not_trip_the_mismatch_guard():
    """Live-verified regression: ``mrdoob/three.js`` reported
    ``submitted_repo: "mrdoob/three"`` and charged with zero HTTP calls."""
    result, instance = await _verify(
        {"repo_url": "https://github.com/mrdoob/three.js"},
        {"repo_owner": "mrdoob", "repo_name": "three.js", "min_commits": 1},
        [_resp(json_data=[{"sha": "a"}])],
    )

    assert result["verification_status"] == VERIFIED
    details = result["verification_details"]
    assert (details["owner"], details["repo"]) == ("mrdoob", "three.js")
    assert instance.get.call_args_list[0].args[0] == (
        "https://api.github.com/repos/mrdoob/three.js/commits"
    )


@pytest.mark.asyncio
async def test_dotted_repo_issue_url_parses_for_tickets_closed():
    """``_parse_issue_url`` carried the same truncation, so a ticket on a dotted
    repo was reported unparseable and failed the goal."""
    ticket = "https://github.com/mrdoob/three.js/issues/42"
    result, instance = await _verify(
        {"repo_url": "https://github.com/mrdoob/three.js"},
        {"conditions": [{"type": "tickets_closed", "tickets": [ticket]}]},
        [_resp(json_data={"state": "closed"})],
    )

    assert result["verification_status"] == VERIFIED
    entry = _check(result, "tickets_closed")
    assert entry["closed"] == [ticket]
    assert "parse_errors" not in entry
    assert instance.get.call_args_list[0].args[0] == (
        "https://api.github.com/repos/mrdoob/three.js/issues/42"
    )


# ─── Branch resolution: no hardcoded "main" ────────────────────────


@pytest.mark.asyncio
async def test_no_branch_specified_omits_ref_so_github_uses_default():
    """Hardcoding ``"main"`` 404'd on a ``master``-default repo and charged the
    user for our wrong guess. Omitting ``sha`` lets GitHub resolve the repo's
    own default branch, without spending a metadata request to learn its name.
    """
    result, instance = await _verify(
        {"repo_url": REPO}, {"min_commits": 1}, [_resp(json_data=[{"sha": "a"}])]
    )

    assert result["verification_status"] == VERIFIED
    params = instance.get.call_args_list[0].kwargs["params"]
    assert "sha" not in params
    details = result["verification_details"]
    assert details["branch"] is None
    assert details["branch_resolution"] == "repository default branch (none specified)"


@pytest.mark.asyncio
async def test_proof_branch_main_is_treated_as_unspecified():
    """``GithubRepoProofSubmission.branch`` defaults to ``"main"``, so a proof
    saying ``main`` cannot be distinguished from one saying nothing. Honouring
    it would reintroduce the 404-on-master charge through the back door."""
    result, instance = await _verify(
        {"repo_url": REPO, "branch": "main"},
        {"min_commits": 1},
        [_resp(json_data=[{"sha": "a"}])],
    )

    assert result["verification_status"] == VERIFIED
    assert "sha" not in instance.get.call_args_list[0].kwargs["params"]


@pytest.mark.asyncio
async def test_required_files_omits_ref_when_no_branch_specified():
    result, instance = await _verify(
        {"repo_url": REPO},
        {"required_files": ["README.md"]},
        [_resp(json_data={"name": "README.md"})],
    )

    assert result["verification_status"] == VERIFIED
    assert instance.get.call_args_list[0].kwargs["params"] is None


@pytest.mark.asyncio
async def test_contents_404_for_a_missing_ref_is_not_reported_as_missing_files():
    """The contents API answers 404 for "no such path" *and* "no such ref".
    Reading the second as the first told users their files were missing when the
    branch was what we could not find — measured live against a ``master`` repo
    as "Missing required file(s) on branch main"."""
    result, _ = await _verify(
        {"repo_url": REPO, "branch": "nope"},
        {"required_files": ["README.md"]},
        [
            _resp(
                status_code=404,
                text='{"message": "No commit found for the ref nope"}',
            )
        ],
    )

    assert result["verification_status"] == FAILED
    entry = _check(result, "required_files")
    assert entry["ref_missing"] is True
    assert entry["missing"] == []
    assert "does not exist" in entry["failure_reason"]
    assert "nope" in entry["failure_reason"]


# ─── Branch identity: a proof must not retarget verification ───────


@pytest.mark.asyncio
async def test_criteria_branch_wins_over_proof_branch():
    """The false-pass hole: ``submit_proof`` clobbered ``criteria["branch"]``
    with the proof's, and the proof's branch field is user-editable — so a goal
    about ``feature/x`` could be passed off 40 commits of ``main``'s
    pre-existing history without ever touching ``feature/x``."""
    result, instance = await _verify(
        {"repo_url": REPO, "branch": "main"},
        {"branch": "feature/x", "min_commits": 1},
        [_resp(json_data=[{"sha": "a"}])],
    )

    assert result["verification_status"] == VERIFIED
    assert instance.get.call_args_list[0].kwargs["params"]["sha"] == "feature/x"
    details = result["verification_details"]
    assert details["branch"] == "feature/x"
    assert details["submitted_branch_ignored"] == "main"


@pytest.mark.asyncio
async def test_proof_branch_is_used_when_criteria_names_none():
    """A legacy goal that never named a branch still honours a deliberate
    non-default choice in the proof."""
    result, instance = await _verify(
        {"repo_url": REPO, "branch": "develop"},
        {"min_commits": 1},
        [_resp(json_data=[{"sha": "a"}])],
    )

    assert result["verification_status"] == VERIFIED
    assert instance.get.call_args_list[0].kwargs["params"]["sha"] == "develop"


def test_submit_proof_does_not_clobber_criteria_repo_or_branch():
    """The fix at its source: ``submit_proof`` fills only what the criteria left
    unset, so a submitted repo/branch can never retarget the goal."""
    from app.goal_types.github_repo import goal_type

    body = MagicMock()
    body.repo_url = "https://github.com/attacker/scratch"
    body.branch = "main"
    body.github_token = None

    out = goal_type.submit_proof(
        {"_body": body},
        {
            "repo_owner": "octocat",
            "repo_name": "Hello-World",
            "repo_url": REPO,
            "branch": "feature/x",
            "min_commits": 3,
        },
    )

    criteria = out["criteria_data"]
    assert criteria["repo_url"] == REPO
    assert criteria["branch"] == "feature/x"
    # The submitted values are still carried on the proof side for the record.
    assert out["proof_data"]["repo_url"] == "https://github.com/attacker/scratch"


def test_submit_proof_does_not_bake_in_the_default_branch():
    """The schema defaults ``branch`` to ``"main"``. Writing that into criteria
    would recreate the hardcoded guess that 404s on ``master`` repositories."""
    from app.goal_types.github_repo import goal_type

    body = MagicMock()
    body.repo_url = REPO
    body.branch = "main"
    body.github_token = None

    out = goal_type.submit_proof({"_body": body}, {"min_commits": 1})

    assert "branch" not in out["criteria_data"]


def test_submit_proof_fills_a_deliberate_branch_when_criteria_is_silent():
    from app.goal_types.github_repo import goal_type

    body = MagicMock()
    body.repo_url = REPO
    body.branch = "develop"
    body.github_token = None

    out = goal_type.submit_proof({"_body": body}, {"min_commits": 1})

    assert out["criteria_data"]["branch"] == "develop"


# ─── min_commits: correct counting at 0 / 1 / several / >100 ───────


@pytest.mark.asyncio
async def test_min_commits_zero_commits_fails():
    result, _ = await _verify(
        {"repo_url": REPO}, {"min_commits": 1}, [_resp(json_data=[])]
    )

    assert result["verification_status"] == FAILED
    entry = _check(result, "min_commits")
    assert entry["actual"] == 0
    assert "Found 0 commits" in entry["failure_reason"]


@pytest.mark.asyncio
async def test_min_commits_empty_repository_409_counts_as_zero():
    """GitHub answers 409 Conflict for a repo with no commits at all."""
    result, _ = await _verify(
        {"repo_url": REPO}, {"min_commits": 1}, [_resp(status_code=409)]
    )

    assert result["verification_status"] == FAILED
    assert _check(result, "min_commits")["actual"] == 0


@pytest.mark.asyncio
async def test_min_commits_single_commit():
    result, instance = await _verify(
        {"repo_url": REPO}, {"min_commits": 1}, [_resp(json_data=[{"sha": "a"}])]
    )

    assert result["verification_status"] == VERIFIED
    assert _check(result, "min_commits")["actual"] == 1
    assert instance.get.call_args_list[0].kwargs["params"]["per_page"] == 1


@pytest.mark.asyncio
async def test_min_commits_several_uses_link_last_page_for_true_count():
    """The old counter requested ``per_page=1`` and returned ``len(data)``, so
    ``actual`` was capped at 1 and ``min_commits > 1`` could never pass. Here
    the repo has 7 commits and the goal asked for 5."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {"min_commits": 5},
        [_resp(json_data=[{"sha": "a"}], headers=_link(7))],
    )

    assert result["verification_status"] == VERIFIED
    assert _check(result, "min_commits")["actual"] == 7


@pytest.mark.asyncio
async def test_min_commits_several_below_threshold_fails():
    result, _ = await _verify(
        {"repo_url": REPO},
        {"min_commits": 10},
        [_resp(json_data=[{"sha": "a"}], headers=_link(7))],
    )

    assert result["verification_status"] == FAILED
    assert _check(result, "min_commits")["actual"] == 7


@pytest.mark.asyncio
async def test_min_commits_above_one_hundred():
    """The old ``if actual_count == 100:`` estimate branch was unreachable
    (``len(data)`` could only be 0 or 1) and ended in a bare ``pass``."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {"min_commits": 150},
        [_resp(json_data=[{"sha": "a"}], headers=_link(207))],
    )

    assert result["verification_status"] == VERIFIED
    assert _check(result, "min_commits")["actual"] == 207


@pytest.mark.asyncio
async def test_min_commits_walks_pages_when_only_next_link_present():
    """Fallback path: a ``rel="next"`` without ``rel="last"`` is counted by
    walking 100-per-page until a short page ends it (100 + 100 + 30 = 230)."""
    next_only = {
        "Link": '<https://api.github.com/repositories/1/commits?page=2>; rel="next"'
    }
    responses = [
        _resp(json_data=[{"sha": "a"}], headers=next_only),
        _resp(json_data=[{"sha": f"p1-{i}"} for i in range(100)]),
        _resp(json_data=[{"sha": f"p2-{i}"} for i in range(100)]),
        _resp(json_data=[{"sha": f"p3-{i}"} for i in range(30)]),
    ]
    result, _ = await _verify({"repo_url": REPO}, {"min_commits": 200}, responses)

    assert result["verification_status"] == VERIFIED
    assert _check(result, "min_commits")["actual"] == 230


@pytest.mark.asyncio
async def test_min_commits_targets_requested_branch():
    result, instance = await _verify(
        {"repo_url": REPO, "branch": "feature/x"},
        {"min_commits": 1},
        [_resp(json_data=[{"sha": "a"}])],
    )

    assert result["verification_status"] == VERIFIED
    assert instance.get.call_args_list[0].kwargs["params"]["sha"] == "feature/x"


@pytest.mark.asyncio
async def test_non_numeric_min_commits_does_not_charge():
    """Malformed criteria are our defect, not a missed goal."""
    result, _ = await _verify({"repo_url": REPO}, {"min_commits": "lots"}, [])

    _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    assert "not a number" in _check(result, "min_commits")["failure_reason"]


@pytest.mark.asyncio
async def test_malformed_threshold_beside_a_runnable_check_does_not_charge():
    """The over-fire the no-runnable-checks path hid.

    Only the unsupported-condition branch used to set ``inconclusive``, so a
    malformed threshold sitting next to a check that *could* run folded into a
    chargeable ``failed`` — the criteria-are-unusable path that made the other
    malformed cases safe was never reached, because ``runnable`` was non-empty.
    """
    result, _ = await _verify(
        {"repo_url": REPO},
        {"min_commits": "lots", "required_files": ["README.md"]},
        [_resp(json_data={"name": "README.md"})],
    )

    _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    assert _check(result, "required_files")["passed"] is True
    assert _check(result, "min_commits")["inconclusive"] is True


@pytest.mark.asyncio
async def test_malformed_required_files_beside_a_runnable_check_does_not_charge():
    result, _ = await _verify(
        {"repo_url": REPO},
        {"required_files": "README.md", "min_commits": 1},
        [_resp(json_data=[{"sha": "a"}])],
    )

    _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    assert _check(result, "min_commits")["passed"] is True
    assert _check(result, "required_files")["inconclusive"] is True


# ─── required_files ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_required_files_all_present_passes():
    result, instance = await _verify(
        {"repo_url": REPO, "branch": "dev"},
        {"required_files": ["README.md", "src/app.py"]},
        [
            _resp(json_data={"name": "README.md"}),
            _resp(json_data={"name": "app.py"}),
        ],
    )

    assert result["verification_status"] == VERIFIED
    entry = _check(result, "required_files")
    assert entry["present"] == ["README.md", "src/app.py"]
    assert entry["missing"] == []
    urls = [call.args[0] for call in instance.get.call_args_list]
    assert urls == [
        "https://api.github.com/repos/octocat/Hello-World/contents/README.md",
        "https://api.github.com/repos/octocat/Hello-World/contents/src/app.py",
    ]
    # Files are looked up on the branch under test, not the default branch.
    assert instance.get.call_args_list[0].kwargs["params"] == {"ref": "dev"}


@pytest.mark.asyncio
async def test_required_files_missing_one_fails():
    result, _ = await _verify(
        {"repo_url": REPO, "branch": "dev"},
        {"required_files": ["README.md", "tests/test_app.py"]},
        [
            _resp(json_data={"name": "README.md"}),
            _resp(status_code=404, text='{"message": "Not Found"}'),
        ],
    )

    assert result["verification_status"] == FAILED
    entry = _check(result, "required_files")
    assert entry["present"] == ["README.md"]
    assert entry["missing"] == ["tests/test_app.py"]
    assert "tests/test_app.py" in entry["failure_reason"]
    assert "dev" in entry["failure_reason"]


@pytest.mark.asyncio
async def test_required_files_403_is_an_error_not_an_absence():
    """A rate-limited lookup must not be recorded as "file missing" — and must
    not be recorded as present either. Asserts the key is absent rather than
    empty, since the error path never reaches the point of creating it."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {"required_files": ["README.md"]},
        [_resp(status_code=403, text="rate limit exceeded")],
    )

    _assert_inconclusive(result, REASON_UPSTREAM_RATE_LIMITED)
    entry = _check(result, "required_files")
    assert "403" in entry["error"]
    assert "missing" not in entry
    assert "present" not in entry


@pytest.mark.asyncio
async def test_required_files_must_be_a_list():
    result, _ = await _verify({"repo_url": REPO}, {"required_files": "README.md"}, [])

    _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    assert "must be a list" in _check(result, "required_files")["failure_reason"]


# ─── require_pr ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_pr_no_pull_request_fails():
    result, instance = await _verify(
        {"repo_url": REPO, "branch": "feature/x"},
        {"require_pr": True},
        [_resp(json_data=[])],
    )

    assert result["verification_status"] == FAILED
    entry = _check(result, "require_pr")
    assert entry["passed"] is False
    assert "No pull request found" in entry["failure_reason"]
    call = instance.get.call_args_list[0]
    assert call.args[0] == "https://api.github.com/repos/octocat/Hello-World/pulls"
    # The ``head=owner:branch`` filter is gone: it searched for a PR *from* the
    # branch under the repo owner, which excluded fork PRs and — with the branch
    # defaulted — looked for a PR from the default branch, which never exists.
    assert "head" not in call.kwargs["params"]
    assert call.kwargs["params"]["state"] == "all"


@pytest.mark.asyncio
async def test_require_pr_open_pull_request_passes():
    result, _ = await _verify(
        {"repo_url": REPO, "branch": "feature/x"},
        {"require_pr": True},
        [_resp(json_data=[_pr(12, state="open")])],
    )

    assert result["verification_status"] == VERIFIED
    entry = _check(result, "require_pr")
    assert entry["open"] == [12]
    assert entry["merged"] == []


@pytest.mark.asyncio
async def test_require_pr_merged_pull_request_passes():
    result, _ = await _verify(
        {"repo_url": REPO, "branch": "feature/x"},
        {"require_pr": True},
        [_resp(json_data=[_pr(9, state="closed", merged_at="2026-07-01T00:00:00Z")])],
    )

    assert result["verification_status"] == VERIFIED
    assert _check(result, "require_pr")["merged"] == [9]


@pytest.mark.asyncio
async def test_require_pr_matches_a_fork_pull_request():
    """A PR opened from a fork has ``head.label`` under the contributor's
    account, so the old ``head=<repo owner>:<branch>`` filter never returned
    it — the user opened the PR the goal asked for and was failed anyway."""
    result, _ = await _verify(
        {"repo_url": REPO, "branch": "feature/x"},
        {"require_pr": True},
        [
            _resp(
                json_data=[
                    _pr(
                        4,
                        state="open",
                        head_ref="feature/x",
                        head_label="contributor:feature/x",
                    )
                ]
            )
        ],
    )

    assert result["verification_status"] == VERIFIED
    assert _check(result, "require_pr")["open"] == [4]


@pytest.mark.asyncio
async def test_require_pr_ignores_pull_requests_from_other_branches():
    result, _ = await _verify(
        {"repo_url": REPO, "branch": "feature/x"},
        {"require_pr": True},
        [_resp(json_data=[_pr(3, state="open", head_ref="unrelated")])],
    )

    assert result["verification_status"] == FAILED
    entry = _check(result, "require_pr")
    assert entry["pull_requests"] == []
    assert "No pull request found" in entry["failure_reason"]


@pytest.mark.asyncio
async def test_require_pr_without_a_branch_accepts_any_open_pr():
    result, _ = await _verify(
        {"repo_url": REPO},
        {"require_pr": True},
        [_resp(json_data=[_pr(7, state="open", head_ref="whatever")])],
    )

    assert result["verification_status"] == VERIFIED
    assert _check(result, "require_pr")["open"] == [7]


@pytest.mark.asyncio
async def test_require_pr_closed_unmerged_pull_request_fails():
    result, _ = await _verify(
        {"repo_url": REPO, "branch": "feature/x"},
        {"require_pr": True},
        [_resp(json_data=[_pr(9, state="closed", merged_at=None)])],
    )

    assert result["verification_status"] == FAILED
    entry = _check(result, "require_pr")
    assert entry["passed"] is False
    assert "closed, unmerged" in entry["failure_reason"]


# ─── Combined declarative + legacy criteria ────────────────────────


@pytest.mark.asyncio
async def test_declarative_and_legacy_conditions_both_enforced():
    """Order of GitHub calls: min_commits, required_files, require_pr, then
    the legacy conditions list. The legacy ``commits`` condition fails here,
    which must fail the whole verification even though the rest passed."""
    responses = [
        _resp(json_data=[{"sha": "a"}], headers=_link(3)),  # min_commits -> 3
        _resp(json_data={"name": "README.md"}),  # required_files
        _resp(json_data=[_pr(1, state="open")]),  # require_pr
        _resp(json_data=[{"sha": "a"}], headers=_link(3)),  # legacy commits -> 3
    ]
    result, _ = await _verify(
        {"repo_url": REPO, "branch": "feature/x"},
        {
            "min_commits": 2,
            "required_files": ["README.md"],
            "require_pr": True,
            "conditions": [{"type": "commits", "min_count": 50}],
        },
        responses,
    )

    assert result["verification_status"] == FAILED
    assert _check(result, "min_commits")["passed"] is True
    assert _check(result, "required_files")["passed"] is True
    assert _check(result, "require_pr")["passed"] is True
    legacy = _check(result, "commits")
    assert legacy["passed"] is False
    assert legacy["actual"] == 3
    assert "need at least 50" in result["verification_details"]["failure_reason"]


@pytest.mark.asyncio
async def test_legacy_commits_condition_can_now_exceed_one():
    """Same legacy shape as ``test_github_repo.py`` but with
    ``min_count > 1`` — impossible to pass before the counting fix."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {"conditions": [{"type": "commits", "min_count": 5}]},
        [_resp(json_data=[{"sha": "a"}], headers=_link(6))],
    )

    assert result["verification_status"] == VERIFIED
    assert _check(result, "commits")["actual"] == 6


@pytest.mark.asyncio
async def test_legacy_commits_since_date_is_forwarded():
    result, instance = await _verify(
        {"repo_url": REPO},
        {
            "conditions": [
                {
                    "type": "commits",
                    "min_count": 1,
                    "since_date": "2026-07-01T00:00:00Z",
                }
            ]
        },
        [_resp(json_data=[{"sha": "a"}])],
    )

    assert result["verification_status"] == VERIFIED
    params = instance.get.call_args_list[0].kwargs["params"]
    assert params["since"] == "2026-07-01T00:00:00Z"


# ─── Authentication ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_token_is_sent_on_every_added_call_and_never_leaks():
    plaintext = "ghp_live_secret_do_not_leak"
    encrypted = encrypt_token(plaintext)
    assert encrypted != plaintext

    responses = [
        _resp(json_data=[{"sha": "a"}]),  # min_commits
        _resp(json_data={"name": "README.md"}),  # required_files
        _resp(json_data=[_pr(1, state="open")]),  # require_pr
    ]
    result, instance = await _verify(
        {"repo_url": REPO, "branch": "feature/x", "github_token": encrypted},
        {"min_commits": 1, "required_files": ["README.md"], "require_pr": True},
        responses,
    )

    assert result["verification_status"] == VERIFIED
    assert instance.get.await_count == 3
    for call in instance.get.call_args_list:
        assert call.kwargs["headers"]["Authorization"] == f"Bearer {plaintext}"

    # The token — encrypted or plaintext — must never reach the stored details.
    rendered = repr(result["verification_details"])
    assert plaintext not in rendered
    assert encrypted not in rendered
    assert "Authorization" not in rendered
    assert "github_token" not in rendered


@pytest.mark.asyncio
async def test_no_authorization_header_when_no_token():
    result, instance = await _verify(
        {"repo_url": REPO, "branch": "feature/x", "github_token": None},
        {"min_commits": 1, "require_pr": True},
        [
            _resp(json_data=[{"sha": "a"}]),
            _resp(json_data=[_pr(1, state="open")]),
        ],
    )

    assert result["verification_status"] == VERIFIED
    # Pin the request count: without it the loop below is vacuously true when no
    # requests are made at all, which is exactly how the old code behaved.
    assert instance.get.await_count == 2
    for call in instance.get.call_args_list:
        assert "Authorization" not in call.kwargs["headers"]


@pytest.mark.asyncio
async def test_criteria_token_is_used_when_proof_has_none():
    plaintext = "ghp_from_criteria"
    result, instance = await _verify(
        {"repo_url": REPO},
        {"min_commits": 1, "github_token": encrypt_token(plaintext)},
        [_resp(json_data=[{"sha": "a"}])],
    )

    assert result["verification_status"] == VERIFIED
    headers = instance.get.call_args_list[0].kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {plaintext}"


@pytest.mark.asyncio
async def test_error_details_do_not_echo_the_token():
    """Error results quote a snippet of the GitHub response body; if a token
    ever appears in one it must be redacted before it is persisted."""
    plaintext = "ghp_secret_in_error_path"
    result, _ = await _verify(
        {"repo_url": REPO, "github_token": encrypt_token(plaintext)},
        {"min_commits": 1},
        [_resp(status_code=500, text=f"bad credentials for {plaintext}")],
    )

    _assert_inconclusive(result, REASON_UPSTREAM_UNAVAILABLE)
    assert plaintext not in repr(result["verification_details"])
    assert "[redacted]" in _check(result, "min_commits")["error"]


@pytest.mark.asyncio
async def test_credentials_in_the_submitted_url_are_not_persisted():
    """``details["repo_url"]`` echoed the submitted string verbatim, so a URL
    carrying basic-auth credentials was stored and served by the
    verification-status endpoint. The canonical form is rebuilt from
    owner/repo."""
    secret = "ghp_in_the_url"
    result, _ = await _verify(
        {"repo_url": f"https://octocat:{secret}@github.com/octocat/Hello-World"},
        {"min_commits": 1},
        [_resp(json_data=[{"sha": "a"}])],
    )

    assert result["verification_status"] == VERIFIED
    details = result["verification_details"]
    assert details["repo_url"] == REPO
    assert secret not in repr(details)


# ─── Error paths: transient vs definitive ──────────────────────────


@pytest.mark.asyncio
async def test_404_repository_is_a_chargeable_failure():
    """A repo that is absent or private is user-controlled — they can delete it
    or flip its visibility — so routing this to no-charge would hand over a way
    to dodge the pledge."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {"min_commits": 1},
        [_resp(status_code=404, text="Not Found")],
    )

    assert result["verification_status"] == FAILED
    entry = _check(result, "min_commits")
    assert "404" in entry["error"]
    assert entry.get("inconclusive") is not True
    assert "not found" in entry["failure_reason"].lower()


@pytest.mark.parametrize(
    "status_code, expected_reason",
    [
        (429, REASON_UPSTREAM_RATE_LIMITED),
        (500, REASON_UPSTREAM_UNAVAILABLE),
        (502, REASON_UPSTREAM_UNAVAILABLE),
        (503, REASON_UPSTREAM_UNAVAILABLE),
        (504, REASON_UPSTREAM_UNAVAILABLE),
        (408, REASON_UPSTREAM_UNAVAILABLE),
    ],
)
@pytest.mark.asyncio
async def test_transient_http_status_never_charges(status_code, expected_reason):
    """The critical charge bug: a blanket ``except`` turned every 5xx and rate
    limit into a terminal ``failed`` and billed the pledge. Unauthenticated
    GitHub allows 60 requests/hour per server IP shared across all users, so an
    exhausted budget would have mass-charged.

    The reason code is pinned per status because it decides the retry regime:
    every one of these is transient, so the reconciler re-dispatches rather than
    escalating to an operator.
    """
    result, _ = await _verify(
        {"repo_url": REPO},
        {"min_commits": 1},
        [_resp(status_code=status_code, text="transient")],
    )

    _assert_inconclusive(result, expected_reason)
    entry = _check(result, "min_commits")
    assert entry["inconclusive"] is True
    assert entry["inconclusive_reason"] == expected_reason
    assert str(status_code) in entry["error"]


@pytest.mark.asyncio
async def test_403_rate_limit_never_charges():
    result, _ = await _verify(
        {"repo_url": REPO},
        {"min_commits": 1},
        [_resp(status_code=403, text="API rate limit exceeded")],
    )

    _assert_inconclusive(result, REASON_UPSTREAM_RATE_LIMITED)
    entry = _check(result, "min_commits")
    assert "403" in entry["error"]
    assert "rate limited" in entry["failure_reason"]


@pytest.mark.asyncio
async def test_403_rate_limit_is_read_from_the_quota_header():
    """The other documented signal: an exhausted quota with a body that says
    nothing about rate limiting."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {"min_commits": 1},
        [
            _resp(
                status_code=403,
                headers={"X-RateLimit-Remaining": "0"},
                text="Forbidden",
            )
        ],
    )

    _assert_inconclusive(result, REASON_UPSTREAM_RATE_LIMITED)


@pytest.mark.asyncio
async def test_bare_403_is_a_user_fault_not_our_outage():
    """A 403 with quota remaining is the user's credential, so it must charge.

    This test previously asserted ``inconclusive`` on the reasoning that "a
    private repo reads as 404, not 403". That holds only for an unauthenticated
    read. Once the user supplies a PAT, GitHub answers 403 for a fine-grained
    token scoped to the wrong repository, for SAML-enforced orgs, and for
    blocked repositories — all of them the user's choice, none of them our
    infrastructure. Classifying those as inconclusive made the pledge
    uncollectable: transient reason -> retries exhausted -> goal flagged blocked
    -> skipped by every deadline sweep, permanently.

    An exhausted quota is still ours; that is the sibling test below.
    """
    result, _ = await _verify(
        {"repo_url": REPO},
        {"min_commits": 1},
        [
            _resp(
                status_code=403, text="Resource not accessible by personal access token"
            )
        ],
    )

    assert result["verification_status"] == "failed"
    assert result.get("inconclusive_reason") is None
    # The message has to tell the user what to do about it.
    entry = _check(result, "min_commits")
    assert "403" in entry["error"]


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectTimeout("timed out"),
    ],
)
@pytest.mark.asyncio
async def test_network_error_never_charges(exc):
    def boom(*args, **kwargs):
        raise exc

    result, _ = await _verify({"repo_url": REPO}, {"min_commits": 1}, boom)

    _assert_inconclusive(result, REASON_UPSTREAM_UNAVAILABLE)
    entry = _check(result, "min_commits")
    assert type(exc).__name__ in entry["error"]
    assert entry["inconclusive"] is True
    assert "could not be completed" in entry["failure_reason"]


@pytest.mark.asyncio
async def test_unexpected_exception_is_ours_not_a_missed_goal():
    """A response shape we did not anticipate is a bug in this module. It must
    not charge, and it must not claim an upstream fault we never observed."""

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    result, _ = await _verify({"repo_url": REPO}, {"min_commits": 1}, boom)

    _assert_inconclusive(result, REASON_INTERNAL_ERROR)
    assert _check(result, "min_commits")["inconclusive"] is True


@pytest.mark.asyncio
async def test_one_check_erroring_does_not_pass_the_goal():
    """A 500 on the PR lookup must not be shrugged off just because the commit
    check passed — but it must not charge either."""
    result, _ = await _verify(
        {"repo_url": REPO, "branch": "feature/x"},
        {"min_commits": 1, "require_pr": True},
        [
            _resp(json_data=[{"sha": "a"}]),
            _resp(status_code=500, text="boom"),
        ],
    )

    _assert_inconclusive(result, REASON_UPSTREAM_UNAVAILABLE)
    assert _check(result, "min_commits")["passed"] is True
    assert "500" in _check(result, "require_pr")["error"]


@pytest.mark.asyncio
async def test_lines_changed_unreadable_commit_detail_is_an_error():
    """A commit whose detail fetch fails used to be skipped silently, so the
    line total came back confidently wrong (0 here) instead of unresolved."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {"conditions": [{"type": "lines_changed", "min_count": 50}]},
        [
            _resp(
                json_data=[
                    {
                        "sha": "a",
                        "url": "https://api.github.com/repos/octocat/Hello-World/commits/a",
                    }
                ]
            ),
            _resp(status_code=403, text="API rate limit exceeded"),
        ],
    )

    _assert_inconclusive(result, REASON_UPSTREAM_RATE_LIMITED)
    entry = _check(result, "lines_changed")
    assert "403" in entry["error"]
    assert "actual" not in entry


@pytest.mark.asyncio
async def test_tickets_closed_records_why_a_ticket_could_not_be_read():
    """A rate-limited issue lookup must be distinguishable from a genuinely
    open ticket, and must not charge."""
    ticket = "https://github.com/octocat/Hello-World/issues/1"
    result, _ = await _verify(
        {"repo_url": REPO},
        {"conditions": [{"type": "tickets_closed", "tickets": [ticket]}]},
        [_resp(status_code=403, text="API rate limit exceeded")],
    )

    _assert_inconclusive(result, REASON_UPSTREAM_RATE_LIMITED)
    entry = _check(result, "tickets_closed")
    assert entry["open_or_not_found"] == [ticket]
    assert "403" in entry["errors"][ticket]
    assert entry["inconclusive"] is True


@pytest.mark.asyncio
async def test_open_ticket_is_a_chargeable_failure():
    """Contrast with the test above: a ticket we *could* read and that is open
    is a genuine miss."""
    ticket = "https://github.com/octocat/Hello-World/issues/1"
    result, _ = await _verify(
        {"repo_url": REPO},
        {"conditions": [{"type": "tickets_closed", "tickets": [ticket]}]},
        [_resp(json_data={"state": "open"})],
    )

    assert result["verification_status"] == FAILED
    entry = _check(result, "tickets_closed")
    assert entry.get("inconclusive") is not True


@pytest.mark.asyncio
async def test_one_open_ticket_still_charges_when_a_sibling_was_rate_limited():
    """The precedence rule *inside* a single check.

    A ticket list is itself conjunctive, so an open ticket is terminal on its
    own. Marking the whole check inconclusive because some *other* ticket
    errored would let one 403 launder a confirmed miss into a free pass — the
    same loophole ``verification_outcome`` closes across checks, one level down.
    """
    open_ticket = "https://github.com/octocat/Hello-World/issues/1"
    limited_ticket = "https://github.com/octocat/Hello-World/issues/2"
    result, _ = await _verify(
        {"repo_url": REPO},
        {
            "conditions": [
                {
                    "type": "tickets_closed",
                    "tickets": [open_ticket, limited_ticket],
                }
            ]
        },
        [
            _resp(json_data={"state": "open"}),
            _resp(status_code=403, text="API rate limit exceeded"),
        ],
    )

    assert result["verification_status"] == FAILED
    assert result["inconclusive_reason"] is None
    entry = _check(result, "tickets_closed")
    assert entry.get("inconclusive") is not True
    # The unreadable ticket is still recorded as unreadable, not as open.
    assert "403" in entry["errors"][limited_ticket]


@pytest.mark.asyncio
async def test_deleted_ticket_is_a_chargeable_failure():
    """A 404 on an issue means it was deleted or its repo was made private, both
    of which the user controls — so it charges, like a 404 on the repo itself."""
    ticket = "https://github.com/octocat/Hello-World/issues/1"
    result, _ = await _verify(
        {"repo_url": REPO},
        {"conditions": [{"type": "tickets_closed", "tickets": [ticket]}]},
        [_resp(status_code=404, text="Not Found")],
    )

    assert result["verification_status"] == FAILED
    entry = _check(result, "tickets_closed")
    assert entry.get("inconclusive") is not True
    assert "404" in entry["errors"][ticket]


@pytest.mark.asyncio
async def test_unparseable_ticket_url_does_not_charge():
    """An unparseable ticket URL is in criteria the user did not author and
    cannot edit, so it is ours. It used to count as "not closed" with no reason
    recorded, which failed the goal and charged."""
    result, _ = await _verify(
        {"repo_url": REPO},
        {"conditions": [{"type": "tickets_closed", "tickets": ["not-a-url"]}]},
        [],
    )

    _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    entry = _check(result, "tickets_closed")
    assert entry["parse_errors"] == ["not-a-url"]
    assert entry["inconclusive"] is True


# ─── The charge boundary ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_inconclusive_is_persisted_with_its_reason():
    """An inconclusive outcome must be *recorded*, not dropped.

    An earlier version of this worker returned early without writing anything,
    which kept the money safe but lost the outcome: the row stayed
    indistinguishable from "never dispatched", so nothing recorded that a check
    had been attempted and ``goal_verification_is_blocked`` could not see it —
    leaving ``workers/deadline.py`` free to charge the pledge for our own
    outage. Persistence is safe because the charge decision lives below this
    call and reads the status alone.
    """
    from app.workers.github_repo import run_github_repo_verification

    cls, _ = _client([_resp(status_code=503, text="unavailable")])
    with (
        patch("app.workers.github_repo.httpx.AsyncClient", cls),
        patch(
            "app.workers.github_repo.persist_verification_result",
            new_callable=AsyncMock,
        ) as persist,
    ):
        result = await run_github_repo_verification(
            goal_id=uuid.uuid4(),
            submission_id=uuid.uuid4(),
            proof_data={"repo_url": REPO},
            criteria_data={"min_commits": 1},
            db=MagicMock(),
        )

    _assert_inconclusive(result, REASON_UPSTREAM_UNAVAILABLE)
    persist.assert_awaited_once()
    assert persist.await_args.args[3] == INCONCLUSIVE
    # Passed as a keyword, which is what the contract requires; it raises on an
    # inconclusive status with no reason.
    assert persist.await_args.kwargs["inconclusive_reason"] == (
        REASON_UPSTREAM_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_confirmed_failure_does_reach_the_persistence_layer():
    """The other half of the boundary: a real miss must still be recorded and
    charged, or the fix above would have turned every failure into a free
    pass."""
    from app.workers.github_repo import run_github_repo_verification

    cls, _ = _client([_resp(json_data=[])])
    with (
        patch("app.workers.github_repo.httpx.AsyncClient", cls),
        patch(
            "app.workers.github_repo.persist_verification_result",
            new_callable=AsyncMock,
        ) as persist,
    ):
        result = await run_github_repo_verification(
            goal_id=uuid.uuid4(),
            submission_id=uuid.uuid4(),
            proof_data={"repo_url": REPO},
            criteria_data={"min_commits": 1},
            db=MagicMock(),
        )

    assert result["verification_status"] == FAILED
    persist.assert_awaited_once()
    assert persist.await_args.args[3] == FAILED
    assert persist.await_args.kwargs["inconclusive_reason"] is None


@pytest.mark.asyncio
async def test_no_verifiable_criteria_is_recorded_as_permanently_inconclusive():
    """The legacy/chat-created population: ``{repo_owner, repo_name}`` with
    nothing checkable, and dropped condition types like ``language_stats``.
    Before the charge boundary these became ``failed`` — a permanently
    unpassable goal that charges on every attempt.

    The reason has to be ``CRITERIA_NOT_EVALUABLE`` and not merely *some*
    inconclusive code: it is the one reason the contract treats as permanent, so
    it saturates the attempt counter and reaches an operator now instead of
    burning four re-dispatches on a question that cannot be answered.
    """
    from app.workers.github_repo import run_github_repo_verification

    cls, _ = _client([])
    with (
        patch("app.workers.github_repo.httpx.AsyncClient", cls),
        patch(
            "app.workers.github_repo.persist_verification_result",
            new_callable=AsyncMock,
        ) as persist,
    ):
        for criteria in (
            {"repo_owner": "octocat", "repo_name": "Hello-World"},
            {
                "repo_owner": "octocat",
                "repo_name": "Hello-World",
                "conditions": [{"type": "language_stats", "min_percent": 80}],
            },
        ):
            result = await run_github_repo_verification(
                goal_id=uuid.uuid4(),
                submission_id=uuid.uuid4(),
                proof_data={},
                criteria_data=criteria,
                db=MagicMock(),
            )
            _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)

    assert persist.await_count == 2
    for call in persist.await_args_list:
        assert call.args[3] == INCONCLUSIVE
        assert call.kwargs["inconclusive_reason"] == REASON_CRITERIA_NOT_EVALUABLE


@pytest.mark.asyncio
async def test_unparseable_repo_url_does_not_escape_as_an_exception():
    """``GithubRepoProofSubmission.repo_url`` is a bare ``str`` with no URL
    validation (app/schemas/proof.py:40), so an unparseable value reaches the
    worker. It used to raise out of the Celery task, which retried three times
    and dropped it — leaving the submission ``pending`` forever, where the
    deadline sweep charges the pledge anyway. We accepted the input, so it is
    ours: recording it as inconclusive is what makes
    ``goal_verification_is_blocked`` true for the goal.
    """
    from app.workers.github_repo import (
        UNPARSEABLE_REPO_INCONCLUSIVE_DETAIL,
        run_github_repo_verification,
    )

    cls, _ = _client([])
    with (
        patch("app.workers.github_repo.httpx.AsyncClient", cls),
        patch(
            "app.workers.github_repo.persist_verification_result",
            new_callable=AsyncMock,
        ) as persist,
    ):
        result = await run_github_repo_verification(
            goal_id=uuid.uuid4(),
            submission_id=uuid.uuid4(),
            proof_data={"repo_url": "my repo"},
            criteria_data={"min_commits": 1},
            db=MagicMock(),
        )

    details = _assert_inconclusive(result, REASON_CRITERIA_NOT_EVALUABLE)
    assert details["inconclusive_detail"] == UNPARSEABLE_REPO_INCONCLUSIVE_DETAIL
    persist.assert_awaited_once()
    assert persist.await_args.args[3] == INCONCLUSIVE


# ─── Opt-in live network test ──────────────────────────────────────


@pytest.mark.skipif(
    os.getenv("SACRIFICE_GITHUB_LIVE_TEST") != "1",
    reason="live GitHub API test; set SACRIFICE_GITHUB_LIVE_TEST=1 to run",
)
@pytest.mark.asyncio
async def test_live_github_commit_count_against_public_repo():
    """One unauthenticated request against a known public repo, to prove the
    real ``Link: rel="last"`` counting path works outside the mocks.

    ``octocat/Hello-World``'s default branch is ``master``, which is the point:
    no branch is specified, so this also proves the omitted-``sha`` default
    branch resolution works against the real API where hardcoding ``"main"``
    returned 404. It has had 3 commits for years; assert loosely (>= 3) so the
    test cannot rot.
    """
    result = await verify_github_repo(
        {"repo_url": "https://github.com/octocat/Hello-World"},
        {"min_commits": 3},
    )

    assert result["verification_status"] == VERIFIED, result
    entry = _check(result, "min_commits")
    assert entry["actual"] >= 3
    assert re.fullmatch(r"\d+", str(entry["actual"]))
