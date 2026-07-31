"""Who pays when verification cannot answer the question.

Every verification ends in one of three outcomes, and the choice between two of
them is a money decision: ``failed`` charges the user's pledge via a real Stripe
PaymentIntent (``services/verification_result.py``), ``inconclusive`` never
charges. So "whose fault was it?" is not a diagnostic nicety — it decides
whether someone is billed.

Why this module exists
----------------------
That decision used to be made ad hoc, at every ``raise`` in every verifier. The
result was a predictable pair of failure modes, and this repository has shipped
both:

* Read "our fault" too generously and you create **charge evasion** — a user
  makes verification error out on purpose and the pledge becomes uncollectable.
  A blocked goal is skipped by every deadline sweep, so an unresolved
  ``inconclusive`` is indistinguishable from forgiving the debt. Real examples
  fixed here: an unparseable ``repo_url`` (one request, pledge gone), a bare
  GitHub 403 from the user's own wrongly-scoped token, an undecryptable token
  planted in criteria.
* Read it too narrowly and you **charge people for your own outages**. Real
  examples: a mis-budgeted sandbox timeout that SIGKILLed passing test suites,
  a Docker daemon restart reported as a user timeout, and — until this module —
  an invalid YouTube API key billing every affected user.

Neither is a bug you can fix once. They recur because the question is asked in
many places and answered from local context ("what was I debugging when I found
this?") rather than from a rule.

The rule
--------
**Attribute by who controls the input, not by what the error looks like.**

If the user chose it, supplied it, or can change it, a negative answer about it
is ``failed`` and charges: a repo they deleted, a branch they never created, a
token they scoped wrongly, an endpoint of theirs that is down, a test suite of
theirs that fails, a video too short. They can fix these, and letting them dodge
a pledge by breaking their own input on purpose defeats the product.

If we chose it, run it, or pay for it, it is ``inconclusive`` and never charges:
our API credentials, our request quota, our network egress, our container
daemon, our database, our own bugs, and criteria the user never authored and
cannot edit.

Corollaries that are easy to get wrong
--------------------------------------
1. **Never attribute by grepping an error message for network-shaped words.** A
   user can point an input at a host they know is dead and have the failure read
   as ours. When a failure is genuinely ambiguous — "the address did not
   respond" — settle it by probing a host *we* name (``egress_is_broken``). If
   our egress is fine, the dead address was theirs.
2. **Check an unambiguous rejection before probing.** Otherwise a flaky network
   launders a real authentication failure into a free pledge.
3. **A confirmed user failure outranks any inconclusive.** Criteria are
   conjunctive, so "2 of the 5 commits" is terminal on its own even if a sibling
   check was rate-limited. Only a clean absence of confirmed failure suppresses
   the charge.
4. **Ambiguity is not a licence to be generous.** "The host you named is slow"
   must not become a free pledge; a user-supplied timeout stays ``failed``.
5. **Absence of an inconclusive path is not safety.** A verifier that can only
   answer verified/failed bills users for our outages. That was true of three of
   the five goal types until this module.

The conformance suite in ``tests/test_charge_integrity_conformance.py`` asserts
this rule per goal type, in both directions, so a new verifier cannot quietly
pick its own doctrine.
"""

from __future__ import annotations

import logging
import socket
from enum import Enum

from app.services.verification_result import (
    REASON_INTERNAL_ERROR,
    REASON_UPSTREAM_RATE_LIMITED,
    REASON_UPSTREAM_UNAVAILABLE,
)

logger = logging.getLogger(__name__)


class Fault(Enum):
    """Who owns a failure, and therefore whether it charges."""

    #: The user chose or controls the input. Charges the pledge.
    USER = "user"
    #: We chose, run, or pay for the thing that broke. Never charges.
    OURS = "ours"


#: Probe target for "is our egress working?". Deliberately a host WE name — see
#: corollary 1. Never probe a host taken from user input.
EGRESS_PROBE_HOST = "one.one.one.one"
EGRESS_PROBE_PORT = 443
EGRESS_PROBE_TIMEOUT = 3.0


def egress_is_broken(
    host: str = EGRESS_PROBE_HOST,
    port: int = EGRESS_PROBE_PORT,
    timeout: float = EGRESS_PROBE_TIMEOUT,
) -> bool:
    """Is outbound network from this worker broken right now?

    Used to settle the one genuinely ambiguous case: a connection to a
    user-supplied address failed, and we need to know whether the network or the
    address was at fault. A failure here means ours; success means theirs.

    Fails **closed toward charging**: if the probe itself cannot run we return
    False (i.e. "our egress is fine"), because the alternative — treating an
    unknown state as our fault — hands out free pledges on every hiccup.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return False
    except OSError:
        logger.warning(
            "Egress probe to %s:%s failed; treating this worker as offline.",
            host,
            port,
        )
        return True
    except Exception:  # noqa: BLE001 - a probe must never decide by crashing
        logger.exception("Egress probe raised unexpectedly; assuming egress is fine.")
        return False


#: HTTP statuses from a service WE depend on that mean "ask again later".
#: Not for a status returned by a user-supplied endpoint — there, the status IS
#: the answer the user is being measured against.
_OUR_UPSTREAM_TRANSIENT = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Statuses that mean OUR credential for a third-party API is bad. Only ever
#: applied to an upstream we authenticate to (the YouTube Data API, say) — never
#: to the user's own repository or endpoint, where a 401/403 is about the
#: credential or permissions THEY chose.
_OUR_CREDENTIAL_REJECTED = frozenset({401, 403})


def classify_our_upstream_status(status: int) -> tuple[Fault, str | None]:
    """Attribute an HTTP status from a third-party API **we** call and pay for.

    ``(Fault.OURS, reason)`` for a quota, an outage or a rejected credential of
    ours; ``(Fault.USER, None)`` otherwise, because a 404 from such an API is a
    genuine statement about the resource the user named.

    Do not use this for a user-supplied endpoint. There, the response status is
    the measurement, not an infrastructure signal.
    """
    if status == 429:
        return Fault.OURS, REASON_UPSTREAM_RATE_LIMITED
    if status in _OUR_CREDENTIAL_REJECTED:
        # Our API key is missing, revoked, or over its quota. The user cannot
        # fix this and must not pay for it.
        return Fault.OURS, REASON_UPSTREAM_UNAVAILABLE
    if status in _OUR_UPSTREAM_TRANSIENT:
        return Fault.OURS, REASON_UPSTREAM_UNAVAILABLE
    return Fault.USER, None


def classify_transport_failure(
    *, target_is_user_supplied: bool
) -> tuple[Fault, str | None]:
    """Attribute a connection/timeout failure that produced no HTTP response.

    For an upstream we chose, a transport failure is ours outright.

    For a user-supplied address it is ambiguous, so it is settled by probing a
    host we name rather than by inspecting the error text (corollary 1). If our
    egress is up, the address the user gave us is simply not answering — which is
    the thing they were being measured on.
    """
    if not target_is_user_supplied:
        return Fault.OURS, REASON_UPSTREAM_UNAVAILABLE
    if egress_is_broken():
        return Fault.OURS, REASON_UPSTREAM_UNAVAILABLE
    return Fault.USER, None


def internal_error() -> tuple[Fault, str]:
    """Our own bug or corrupted state. Always ours, never charges."""
    return Fault.OURS, REASON_INTERNAL_ERROR
