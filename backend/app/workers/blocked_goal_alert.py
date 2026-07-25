"""The thing that actually runs ``blocked-goals list`` on a schedule.

``app/services/blocked_goals.py`` closed the "nobody can see stranded pledges"
half of this problem: it is the reader the deadline sweep's skip list never had,
and it is reachable from ``sacrifice blocked-goals list`` and
``GET /api/operator/blocked-goals``. What it did not come with is anything that
*invokes* it. Both entry points are pull-only, so a blocked goal was still
silent — the owner is told "our team is looking into it"
(``app/services/verification_result.py``), and until somebody happened to run a
command, no team was.

That silence has a direction. An inconclusive verification never charges, by
design, and ``goal_verification_is_blocked`` makes every deadline sweep skip the
goal. So an unresolved blocked goal is not a stalled decision, it is a decision:
the pledge is quietly forgiven and the promise quietly broken. It stays that way
for as long as nobody looks, which without this module is forever.

What this covers
----------------
A Celery beat entry (``alert-blocked-goals``, ``app/core/celery_app.py``) runs
:func:`alert_on_blocked_goals` every 15 minutes in the same worker that already
runs the deadline sweep and the dispatch reconciler. It counts the goals whose
automatic retries are exhausted — the ``--needs-review-only`` set, the ones that
will never move again on their own — and writes one ``ERROR`` line per run when
there are any, naming each goal, how long it has been stuck, the pledge at stake
and the command that resolves it. A run that finds none logs one ``INFO`` line,
so "the alert is quiet" is distinguishable from "the alert is not running".

How it reaches a person
-----------------------
The log line alone does not. This repository deploys no aggregator — no Sentry,
no hosted logging, no MTA — so an ``ERROR`` in the worker log reaches whoever
happens to run ``docker compose logs worker``, which on a quiet week is nobody.

So when ``settings.blocked_goal_alert_webhook_url`` is set, each non-empty run
also POSTs a compact JSON summary to it: any incoming-webhook endpoint (Slack,
Discord, Mattermost) turns that into a notification on somebody's phone. Empty
is the default and means log-only, which is what every deployment gets until an
operator sets it — the alert is never *worse* than the log line, and a webhook it
cannot deliver is logged and swallowed rather than failing the run.

The no-egress alternative, for a deployment that will not call out: journald
already receives these lines, so

    journalctl -u sacrifice-worker -p err --since -20min | grep 'need operator review'

in a cron job with ``MAILTO`` set is a real channel too. It is not built here
because it depends on a unit name and an MTA this repo does not own.

Two gaps that remain, both deliberate:

* **Goals still inside their retry budget are not reported.** They are excluded
  by ``needs_review_only`` because they are being retried automatically and
  usually resolve themselves. They are visible to
  ``sacrifice blocked-goals list`` with no flag.
* **It does not resolve anything.** Resolution stays a human decision through
  ``sacrifice blocked-goals resolve`` — and note that neither of its outcomes
  charges (``app/services/blocked_goals.py``). This module holds no charge path
  and imports nothing that has one.
"""

import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.celery_app import celery_app
from app.services.blocked_goals import list_blocked_goals

logger = logging.getLogger(__name__)

#: How the operator clears one. Named in the alert so the line is actionable on
#: its own, without a reader having to know this subsystem exists.
RESOLVE_HINT = "sacrifice blocked-goals resolve <goal-id> --retry | --give-up"

#: Short on purpose. A monitoring run must not sit on a hung webhook host for
#: longer than the interval between runs.
_WEBHOOK_TIMEOUT = 10.0


def _get_session():
    """A dedicated engine, as ``app/workers/reconcile_dispatch.py`` does.

    The beat task owns its pool for the length of one run rather than sharing
    ``app.database``'s module-level engine, which belongs to the API process.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    return engine, async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


async def _post_webhook(blocked, pledge_total: int) -> bool:
    """Push the alert to the configured webhook. Returns whether it delivered.

    Best-effort by construction: the log line has already been written, and a
    Slack outage must not turn a monitoring run into a failed Celery task that
    retries and double-notifies. Every failure mode ends in a warning and
    ``False``.

    The payload names goals and pledges, never proof data or criteria — the
    ``BlockedGoal`` dataclass is the exposure allowlist and either of those can
    carry an encrypted GitHub PAT (see ``app/services/blocked_goals.py``).
    ``text`` is included because Slack-compatible endpoints render that key;
    the structured fields sit alongside for anything that reads JSON.
    """
    url = settings.blocked_goal_alert_webhook_url
    if not url:
        return False

    payload = {
        "text": (
            f"{len(blocked)} blocked goal(s) need operator review; "
            f"{pledge_total / 100:.2f} in pledges is uncollectable. "
            f"Resolve with: {RESOLVE_HINT}"
        ),
        "needs_review": len(blocked),
        "pledge_total_cents": pledge_total,
        "goals": [
            {
                "goal_id": str(goal.goal_id),
                "goal_type": goal.goal_type,
                "pledge_cents": goal.pledge_amount,
                "currency": goal.currency,
                "reason": goal.inconclusive_reason,
                "blocked_for_hours": round(goal.blocked_for_seconds / 3600, 1),
            }
            for goal in blocked
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "Blocked-goal alert webhook returned %s; the alert is in the log "
                "above but was not delivered.",
                resp.status_code,
            )
            return False
        return True
    except Exception:
        logger.warning(
            "Blocked-goal alert webhook could not be reached; the alert is in "
            "the log above but was not delivered.",
            exc_info=True,
        )
        return False


def _describe(goal) -> str:
    """One blocked goal, compact enough to read in a log line."""
    hours = goal.blocked_for_seconds / 3600
    return (
        f"goal={goal.goal_id} user={goal.user_email} type={goal.goal_type} "
        f"status={goal.goal_status} pledge={goal.pledge_amount / 100:.2f}"
        f"{goal.currency.upper()} reason={goal.inconclusive_reason or 'unknown'} "
        f"blocked_for={hours:.1f}h"
    )


async def alert_on_blocked_goals(db: AsyncSession | None = None) -> dict:
    """Log the goals stranded past their retry budget. Read-only.

    Returns ``{"needs_review": n, "goal_ids": [...], "pledge_total": cents,
    "webhook_delivered": bool}`` so a test can assert on the count without
    parsing log output, and so an operator can tell "nothing was stranded" from
    "something was, and the notification did not get out".

    Never writes to the database, never commits, and cannot charge:
    ``list_blocked_goals`` issues a single SELECT, and the only other things this
    function does are call ``logger`` and POST a summary to a webhook the
    operator configured.
    """
    engine = None
    if db is None:
        engine, session_factory = _get_session()
        session = session_factory()
    else:
        session = db

    try:
        blocked = await list_blocked_goals(session, needs_review_only=True)
    finally:
        if engine is not None:
            await session.close()
            await engine.dispose()

    pledge_total = sum(goal.pledge_amount for goal in blocked)
    delivered = False

    if blocked:
        # ERROR, not WARNING: every entry here is an uncollected pledge and a
        # user who was promised a human. The severity is what makes the line
        # findable in an aggregator that filters INFO/WARNING away.
        logger.error(
            "%d blocked goal(s) need operator review; %.2f in pledges is "
            "uncollectable while they sit. Resolve with: %s\n%s",
            len(blocked),
            pledge_total / 100,
            RESOLVE_HINT,
            "\n".join(f"  {_describe(goal)}" for goal in blocked),
        )
        # After the log, never instead of it: the log line is the record, the
        # webhook is the notification, and a failure of the second must not cost
        # us the first.
        delivered = await _post_webhook(blocked, pledge_total)
    else:
        # Logged on purpose: a heartbeat is what tells an operator the check is
        # running. Silence from a sweep that has crashed looks exactly like
        # silence from a sweep with nothing to report.
        logger.info("No blocked goals need operator review.")

    return {
        "needs_review": len(blocked),
        "goal_ids": [str(goal.goal_id) for goal in blocked],
        "pledge_total": pledge_total,
        "webhook_delivered": delivered,
    }


@celery_app.task(bind=True, max_retries=0)
def alert_on_blocked_goals_task(self):
    """Beat entry point.

    ``max_retries=0``: this is a read that repeats every 15 minutes anyway, so a
    failed run costs one skipped observation and a retry would only stack
    duplicate alerts on top of a database that is already unhappy.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(alert_on_blocked_goals())
    finally:
        loop.close()
