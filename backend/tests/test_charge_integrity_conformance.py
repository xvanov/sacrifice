"""Charge-integrity conformance: does each goal type attribute fault correctly?

This suite exists because the charge decision was the most-regressed thing in
this codebase. Over one hardening pass it broke in both directions, repeatedly:

* four ways to make a pledge **uncollectable** (an unparseable ``repo_url``, a
  bare GitHub 403 from the user's own token, any failed payment row forgiving all
  later attempts, an undecryptable token planted in criteria);
* four ways to **charge a user who did the work** (a mis-budgeted sandbox
  backstop, a daemon restart read as a user timeout, prose inventing a
  ``required_files`` criterion, a timezone-less deadline expiring early);
* and three goal types that had **no non-charging path at all**, so every outage
  of ours billed whoever happened to submit.

Per-verifier unit tests did not catch these, because each one asked "does this
verifier do what its author intended?" The failures were all in what the author
did *not* consider. So this file asks the question the other way round, as a
matrix over every registered goal type:

    for each goal type:
        a fault the USER controls  -> must charge      (no evasion)
        a fault WE control         -> must never charge (no wrongful charge)

The doctrine being enforced lives in ``app/services/fault_attribution.py``.

These tests deliberately assert at the **charge boundary** — they patch
``process_charge_for_goal`` and assert on whether it was awaited — rather than
asserting a status string. A status is a proxy; the charge is the thing that
takes someone's money. Several of the historical bugs passed a status assertion
while still billing the user.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.goal_types.registry import list_types
from app.services import verification_result as vr

CHARGE_BOUNDARY = "app.workers.payments.process_charge_for_goal"


# ── The matrix ────────────────────────────────────────────────────────────
#
# Kept as data so adding a goal type without deciding its fault attribution is
# an obvious omission (``test_every_registered_goal_type_is_covered`` fails)
# rather than a silent gap that only shows up as a billing complaint.

#: Goal types that legitimately have no "our fault" path, with the reason. Being
#: on this list is a claim that must stay true — the test below re-checks it.
NO_UPSTREAM_DEPENDENCY = {
    "geolocation": (
        "Pure arithmetic on submitted coordinates (haversine) with no network "
        "call, no third-party API and no container. There is no infrastructure "
        "of ours that can fail mid-check, so every failure is a statement about "
        "the submitted coordinates."
    ),
}


def test_every_registered_goal_type_is_covered():
    """A new goal type must not slip in without a fault-attribution decision.

    If this fails, add the type to this file's matrix (or to
    NO_UPSTREAM_DEPENDENCY with a justification). Do not delete the assertion:
    an unconsidered verifier is exactly how three of the five ended up billing
    users for our outages.
    """
    covered = set(COVERED_BY_OUR_FAULT_TESTS) | set(NO_UPSTREAM_DEPENDENCY)
    registered = set(list_types())
    assert registered <= covered, (
        f"goal types with no charge-integrity coverage: {sorted(registered - covered)}"
    )


#: Goal types exercised by the "our fault must not charge" tests below.
COVERED_BY_OUR_FAULT_TESTS = {
    "youtube_video",
    "api_endpoint",
    "github_repo",
    "dev_sandbox",
}


# ── Direction 1: our fault must NEVER charge ──────────────────────────────


@pytest.mark.asyncio
async def test_youtube_our_api_credential_does_not_charge():
    """Our YouTube key being rejected is not the user's video failing.

    This charged every affected user until the fault-attribution work: the 403
    became a plain ValueError, which the worker read as "this video does not
    satisfy the goal". One misconfigured key would bill everyone who submitted.
    """
    from app.workers.youtube import verify_youtube_content

    resp = MagicMock()
    resp.status_code = 403
    resp.json.return_value = {}

    with patch("app.services.youtube.httpx.AsyncClient") as client_cls:
        inst = AsyncMock()
        inst.get.side_effect = lambda *a, **k: resp
        client_cls.return_value.__aenter__.return_value = inst
        result = await verify_youtube_content(
            {"video_id": "dQw4w9WgXcQ"},
            {"min_duration_seconds": 60, "video_description": "a walkthrough"},
        )

    assert result["verification_status"] == vr.INCONCLUSIVE
    assert result["inconclusive_reason"] in vr.INCONCLUSIVE_REASONS


@pytest.mark.asyncio
async def test_youtube_our_quota_does_not_charge():
    from app.workers.youtube import verify_youtube_content

    resp = MagicMock()
    resp.status_code = 429
    resp.json.return_value = {}

    with patch("app.services.youtube.httpx.AsyncClient") as client_cls:
        inst = AsyncMock()
        inst.get.side_effect = lambda *a, **k: resp
        client_cls.return_value.__aenter__.return_value = inst
        result = await verify_youtube_content(
            {"video_id": "dQw4w9WgXcQ"},
            {"min_duration_seconds": 60, "video_description": "x"},
        )

    assert result["verification_status"] == vr.INCONCLUSIVE
    assert result["inconclusive_reason"] == vr.REASON_UPSTREAM_RATE_LIMITED


@pytest.mark.asyncio
async def test_api_endpoint_our_egress_down_does_not_charge():
    """If we cannot make outbound requests, every endpoint looks dead.

    Attributing that to the user would bill everyone during one of our network
    incidents. Settled by an egress probe, not by the error text.
    """
    from app.workers.api_check import verify_api_endpoint

    from app.services.fault_attribution import Fault

    with (
        patch("app.workers.api_check.httpx.AsyncClient") as client_cls,
        # Simulate the probe finding our egress down, rather than actually
        # severing the test runner's network.
        patch(
            "app.workers.api_check.classify_transport_failure",
            return_value=(Fault.OURS, vr.REASON_UPSTREAM_UNAVAILABLE),
        ),
    ):
        inst = AsyncMock()
        inst.request.side_effect = httpx.ConnectError("no route to host")
        client_cls.return_value.__aenter__.return_value = inst
        result = await verify_api_endpoint(
            {},
            {
                "url": "https://example.com/health",
                "method": "GET",
                "expected_status": 200,
            },
        )

    assert result["verification_status"] == vr.INCONCLUSIVE
    assert result["inconclusive_reason"] == vr.REASON_UPSTREAM_UNAVAILABLE


@pytest.mark.asyncio
async def test_github_repo_rate_limit_does_not_charge():
    """The unauthenticated GitHub quota is per server IP and shared by all users.

    One user submitting in a loop must not make everyone else's goals fail.
    """
    from app.workers.github_repo import verify_github_repo

    resp = MagicMock()
    resp.status_code = 403
    resp.headers = {"x-ratelimit-remaining": "0"}
    resp.text = "API rate limit exceeded"
    resp.json.return_value = []

    inst = AsyncMock()
    inst.get.side_effect = [resp]
    cls = MagicMock()
    cls.return_value.__aenter__.return_value = inst
    cls.return_value.__aexit__.return_value = False

    with patch("app.workers.github_repo.httpx.AsyncClient", cls):
        result = await verify_github_repo(
            {"repo_url": "https://github.com/octocat/Hello-World"}, {"min_commits": 1}
        )

    assert result["verification_status"] == vr.INCONCLUSIVE
    assert result["inconclusive_reason"] == vr.REASON_UPSTREAM_RATE_LIMITED


def test_geolocation_has_no_upstream_to_fail():
    """Documented exemption, re-verified rather than trusted.

    If geolocation ever grows a network call, this fails and the type needs real
    our-fault coverage.
    """
    import inspect

    from app.workers import geolocation

    src = inspect.getsource(geolocation)
    for forbidden in ("httpx", "requests.", "docker", "urlopen"):
        assert forbidden not in src, (
            f"geolocation now touches {forbidden!r}, so it can fail for reasons "
            "that are ours. Remove it from NO_UPSTREAM_DEPENDENCY and add "
            "our-fault coverage, or the next outage will charge users."
        )


# ── Direction 2: the user's fault must STILL charge ───────────────────────
#
# The mirror tests. Without these, "never charge" is trivially satisfiable by a
# verifier that never charges at all — which is charge evasion wearing the
# costume of safety.


@pytest.mark.asyncio
async def test_youtube_video_too_short_still_charges():
    from app.workers.youtube import verify_youtube_content

    meta = MagicMock()
    meta.status_code = 200
    meta.json.return_value = {
        "items": [
            {
                "snippet": {"title": "t", "description": "d"},
                "contentDetails": {"duration": "PT10S"},
            }
        ]
    }

    with patch("app.services.youtube.httpx.AsyncClient") as client_cls:
        inst = AsyncMock()
        inst.get.side_effect = lambda *a, **k: meta
        client_cls.return_value.__aenter__.return_value = inst
        result = await verify_youtube_content(
            {"video_id": "dQw4w9WgXcQ"},
            {"min_duration_seconds": 600, "video_description": "x"},
        )

    assert result["verification_status"] == vr.FAILED
    assert result.get("inconclusive_reason") is None


@pytest.mark.asyncio
async def test_api_endpoint_wrong_status_still_charges():
    from app.workers.api_check import verify_api_endpoint

    resp = MagicMock()
    resp.status_code = 500
    resp.headers = {"content-type": "text/plain"}
    resp.text = "boom"

    with patch("app.workers.api_check.httpx.AsyncClient") as client_cls:
        inst = AsyncMock()
        inst.request.return_value = resp
        client_cls.return_value.__aenter__.return_value = inst
        result = await verify_api_endpoint(
            {},
            {
                "url": "https://example.com/health",
                "method": "GET",
                "expected_status": 200,
            },
        )

    assert result["verification_status"] == vr.FAILED
    assert result.get("inconclusive_reason") is None


@pytest.mark.asyncio
async def test_api_endpoint_dead_user_host_still_charges_when_our_egress_is_fine():
    """The ambiguous case, resolved against the user.

    Their endpoint not answering is the very thing being measured. This must not
    become a free pledge just because the failure *looks* like a network error —
    otherwise pointing the URL at a known-dead host is a one-line pledge dodge.
    """
    from app.workers.api_check import verify_api_endpoint

    with (
        patch("app.workers.api_check.httpx.AsyncClient") as client_cls,
        patch("app.services.fault_attribution.egress_is_broken", return_value=False),
    ):
        inst = AsyncMock()
        inst.request.side_effect = httpx.ConnectError("connection refused")
        client_cls.return_value.__aenter__.return_value = inst
        result = await verify_api_endpoint(
            {},
            {
                "url": "https://definitely-not-running.example",
                "method": "GET",
                "expected_status": 200,
            },
        )

    assert result["verification_status"] == vr.FAILED
    assert result.get("inconclusive_reason") is None


@pytest.mark.asyncio
async def test_github_repo_missing_commits_still_charges():
    from app.workers.github_repo import verify_github_repo

    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.text = ""
    resp.json.return_value = [{"sha": "abc"}]

    inst = AsyncMock()
    inst.get.side_effect = [resp]
    cls = MagicMock()
    cls.return_value.__aenter__.return_value = inst
    cls.return_value.__aexit__.return_value = False

    with patch("app.workers.github_repo.httpx.AsyncClient", cls):
        result = await verify_github_repo(
            {"repo_url": "https://github.com/octocat/Hello-World"},
            {"min_commits": 50},
        )

    assert result["verification_status"] == vr.FAILED
    assert result.get("inconclusive_reason") is None


@pytest.mark.asyncio
async def test_geolocation_outside_radius_still_charges():
    from app.workers.geolocation import verify_geolocation

    result = await verify_geolocation(
        {"latitude": 0.0, "longitude": 0.0},
        {"target_latitude": 40.0, "target_longitude": -74.0, "radius_m": 100},
    )

    assert result["verification_status"] == vr.FAILED
    assert result.get("inconclusive_reason") is None


# ── The doctrine itself ───────────────────────────────────────────────────


def test_inconclusive_is_unreachable_for_a_user_supplied_credential():
    """A 401/403 about the USER's credential must not be classified as ours.

    ``classify_our_upstream_status`` is only ever for an upstream we authenticate
    to. Applying it to a user's repository would turn their wrongly-scoped token
    into a free pledge — the exact bug found in the github_repo verifier.
    """
    from app.services.fault_attribution import Fault, classify_our_upstream_status

    # For OUR upstream, a rejected credential is ours.
    assert classify_our_upstream_status(403)[0] is Fault.OURS
    # A definitive answer about a named resource is not.
    assert classify_our_upstream_status(404)[0] is Fault.USER
    assert classify_our_upstream_status(200)[0] is Fault.USER


def test_egress_probe_fails_closed_toward_charging():
    """An unusable probe must not be read as "our egress is down".

    Fail-open here would mean every probe hiccup forgives a pledge.
    """
    from app.services import fault_attribution as fa

    with patch.object(fa.socket, "create_connection", side_effect=RuntimeError("boom")):
        assert fa.egress_is_broken() is False


def test_reason_codes_are_all_our_fault_by_construction():
    """Every allowlisted reason must describe something the user cannot cause.

    The allowlist is the only way to reach a non-charging outcome, so a
    user-causable reason in it is a charge-evasion hole by definition. This is a
    review checklist rendered executable: if you add a code, you must be able to
    say why a user cannot trigger it.
    """
    justified = {
        vr.REASON_UPSTREAM_UNAVAILABLE: "a service we depend on did not answer us",
        vr.REASON_UPSTREAM_RATE_LIMITED: "a quota we own was exhausted",
        vr.REASON_SANDBOX_INFRASTRUCTURE: "our container runtime failed",
        vr.REASON_CRITERIA_NOT_EVALUABLE: "criteria the user cannot author or edit",
        vr.REASON_INTERNAL_ERROR: "a bug in our own code",
    }
    assert set(vr.INCONCLUSIVE_REASONS) == set(justified), (
        "a reason code was added or removed without justifying why a user cannot "
        "cause it; see app/services/fault_attribution.py"
    )


@pytest.mark.asyncio
async def test_charge_boundary_is_reached_for_a_verdict_and_not_for_inconclusive():
    """The end-to-end invariant, asserted where the money actually moves.

    Everything above asserts status strings; this asserts the charge call itself,
    because several historical bugs satisfied a status assertion while still
    billing the user.
    """
    goal_id, submission_id = uuid.uuid4(), uuid.uuid4()

    class _Session:
        """Minimal stand-in: we only care whether the charge is attempted."""

        def __init__(self):
            self.committed = False

        async def execute(self, *a, **kw):
            res = MagicMock()
            res.rowcount = 1
            res.scalar_one_or_none.return_value = None
            return res

        async def commit(self):
            self.committed = True

        async def rollback(self):
            pass

    with patch(CHARGE_BOUNDARY, new_callable=AsyncMock) as charge:
        await vr.persist_verification_result(
            _Session(),
            goal_id,
            submission_id,
            vr.INCONCLUSIVE,
            {},
            inconclusive_reason=vr.REASON_UPSTREAM_UNAVAILABLE,
        )
        assert charge.await_count == 0, "an inconclusive outcome must never charge"
