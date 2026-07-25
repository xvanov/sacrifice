"""The reader for goals stranded on an inconclusive verification.

Why this exists
---------------
``app/services/verification_result.py`` records ``inconclusive`` when the fault
is ours (a GitHub outage, an exhausted rate-limit quota, a sandbox
infrastructure fault, criteria we accepted but cannot evaluate). That outcome
deliberately never charges, and once the retry budget is spent it sets
``details["needs_operator_review"]``. ``check_deadlines`` then consults
``goal_verification_is_blocked`` and skips the goal — on every sweep, forever.

Until this module, nothing read that marker: no endpoint, no query, no metric,
no alert. So the promise in the user-facing notification ("our team is looking
into it") had nobody behind it, and a blocked goal was in practice silent,
permanent forgiveness of the pledge — uncollected money and an unkept promise
at the same time. This module is the missing reader, plus the two resolutions an
operator can apply.

What "blocked" means here
-------------------------
Exactly what makes the deadline sweep skip the goal, and nothing else:

* the goal is in a status the sweep enforces (``active``/``pending_review``);
* its most recent submission has no verdict (``verification_status='pending'``);
* that submission's details carry ``outcome='inconclusive'``.

Goals whose retry budget is still unspent are included on purpose — they are
being retried automatically and usually need no action, but they are also
already invisible to the sweep, so an operator should be able to see them
before they age into review. ``needs_operator_review`` distinguishes the two.

Resolutions, and the one that is deliberately absent
----------------------------------------------------
``retry`` hands the row back to ``reconcile_stale_dispatches`` by resetting the
attempt counter. ``give_up`` closes the goal as ``cancelled`` — terminal, and
the only existing ``goal_status`` value that neither lies about the user's
outcome (``failed`` does, and charges) nor leaves the goal enforceable.

There is no resolution that charges. If we could not adjudicate the proof, the
pledge is not ours to collect, and an operator staring at a list of stuck
pledges is exactly the wrong person to be offered a "charge anyway" button.
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.notification import create_notification

# These name a JSON contract that ``verification_result`` writes and this module
# reads. Imported rather than re-spelled as literals so the two readings cannot
# drift apart silently: if that module renames a key, this import fails loudly
# at startup instead of this module quietly reporting zero blocked goals.
from app.services.verification_result import (
    INCONCLUSIVE,
    _AT_KEY,
    _OUTCOME_KEY,
    _REASON_KEY,
    _REVIEW_KEY,
)

logger = logging.getLogger(__name__)

# Mirrors ``ENFORCEABLE_STATUSES`` in ``app/workers/deadline.py``. Duplicated
# rather than imported because that module pulls in Celery and the payments
# worker, and this one is imported by an API route.
_SWEEP_ENFORCED_STATUSES = ("active", "pending_review")

# Operator bookkeeping written into ``verification_details``. Namespaced so it
# cannot collide with the verification contract's own keys.
_ACTION_KEY = "operator_action"
_ACTION_AT_KEY = "operator_action_at"

ACTION_RETRY = "retry"
ACTION_GIVE_UP = "give_up"
ACTIONS = frozenset({ACTION_RETRY, ACTION_GIVE_UP})

# Status a given-up goal lands in. Terminal, not enforced by the deadline sweep,
# and not a claim that the user failed.
GIVE_UP_STATUS = "cancelled"


class BlockedGoalError(Exception):
    """Base for operator-resolution refusals."""


class GoalNotFound(BlockedGoalError):
    """No goal with that id exists."""


class GoalNotBlocked(BlockedGoalError):
    """The goal exists but is not blocked on an inconclusive verification.

    Raised rather than silently no-oping: an operator resolving the wrong id
    must not be told it worked, and a goal that resolved itself between the
    ``list`` and the ``resolve`` must not be touched.
    """


@dataclass(frozen=True)
class BlockedGoal:
    """One stranded goal, as an operator needs to see it.

    This type *is* the exposure allowlist. Two fields on the underlying rows can
    carry an encrypted GitHub PAT (``proof_submissions.dispatch_criteria``,
    ``proof_data``) and one (``inconclusive_detail``) echoes user-supplied text;
    none of them are selected by the query below, so no caller can leak them by
    forgetting to filter. ``user_email`` is the one piece of user identity
    included, because triage means being able to contact the person waiting.
    """

    goal_id: uuid.UUID
    submission_id: uuid.UUID
    user_email: str
    goal_type: str
    goal_status: str
    pledge_amount: int
    currency: str
    deadline: datetime
    blocked_since: datetime
    blocked_for_seconds: int
    inconclusive_reason: str | None
    dispatch_attempts: int
    max_attempts: int
    needs_operator_review: bool

    def to_public_dict(self) -> dict:
        """JSON-ready form: uuids and datetimes as strings."""
        out = asdict(self)
        out["goal_id"] = str(self.goal_id)
        out["submission_id"] = str(self.submission_id)
        out["deadline"] = self.deadline.isoformat()
        out["blocked_since"] = self.blocked_since.isoformat()
        return out


@dataclass(frozen=True)
class ResolveResult:
    """What ``resolve_blocked_goal`` actually changed, for the operator to read.

    ``reclaimable_by_reconciler`` means the submission is once again eligible for
    ``reconcile_stale_dispatches`` — it is claimed on the first sweep that finds
    the row older than ``verification_dispatch_stale_minutes``, which for a goal
    that has been blocked long enough to reach an operator is the next one.
    """

    goal_id: uuid.UUID
    submission_id: uuid.UUID
    action: str
    previous_goal_status: str
    new_goal_status: str
    previous_dispatch_attempts: int
    new_dispatch_attempts: int
    inconclusive_reason: str | None
    reclaimable_by_reconciler: bool


# LATERAL, not a window function or a correlated subquery, so "most recent
# submission" is evaluated once per goal and the pending/inconclusive predicates
# apply to *that* row only. Ordering matches ``goal_verification_is_blocked``
# exactly (``submitted_at DESC LIMIT 1``, no tiebreaker) — a divergence here
# would let this list disagree with the predicate the deadline sweep uses, which
# is the one thing this reader must never do.
_BLOCKED_SQL = """
    SELECT g.id            AS goal_id,
           s.id            AS submission_id,
           u.email         AS user_email,
           g.goal_type     AS goal_type,
           g.status        AS goal_status,
           g.pledge_amount AS pledge_amount,
           g.currency      AS currency,
           g.deadline      AS deadline,
           s.submitted_at  AS submitted_at,
           s.dispatch_attempts AS dispatch_attempts,
           s.verification_details ->> :at_key     AS inconclusive_at,
           s.verification_details ->> :reason_key AS inconclusive_reason,
           COALESCE(
               s.verification_details ->> :review_key = 'true', FALSE
           ) AS needs_operator_review
    FROM goals AS g
    JOIN users AS u ON u.id = g.user_id
    JOIN LATERAL (
        SELECT ps.id, ps.submitted_at, ps.dispatch_attempts,
               ps.verification_status, ps.verification_details
        FROM proof_submissions AS ps
        WHERE ps.goal_id = g.id
        ORDER BY ps.submitted_at DESC
        LIMIT 1
    ) AS s ON TRUE
    WHERE g.status::text IN (__ENFORCED__)
      AND s.verification_status = 'pending'
      AND s.verification_details ->> :outcome_key = :inconclusive
""".replace("__ENFORCED__", ", ".join(f"'{s}'" for s in _SWEEP_ENFORCED_STATUSES))

_BY_GOAL_SQL = _BLOCKED_SQL + " AND g.id = :goal_id"


def _params(**extra) -> dict:
    return {
        "at_key": _AT_KEY,
        "reason_key": _REASON_KEY,
        "review_key": _REVIEW_KEY,
        "outcome_key": _OUTCOME_KEY,
        "inconclusive": INCONCLUSIVE,
        **extra,
    }


def _action_patch(action: str, at: datetime) -> str:
    """The details patch recording an operator resolution, as a JSON string.

    Merged with ``||`` rather than rewriting the blob, so the verification
    contract's own keys survive. Passed as one ``jsonb`` parameter because
    asyncpg cannot infer the type of a bare text key in ``jsonb_build_object``.

    ``needs_operator_review`` goes false either way: the operator has now looked,
    which is the whole meaning of that flag.
    """
    return json.dumps(
        {
            _REVIEW_KEY: False,
            _ACTION_KEY: action,
            _ACTION_AT_KEY: at.isoformat(),
        }
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _blocked_since(row, now: datetime) -> datetime:
    """When this goal stopped being adjudicable.

    ``inconclusive_at`` is written by ``verification_result`` on every
    inconclusive outcome, so it is the accurate answer; ``submitted_at`` is the
    fallback for a row written before that key existed (or by a future caller
    that forgets it). Never returns a future timestamp — a clock-skewed value
    would otherwise sort as "blocked for negative time" and hide at the bottom
    of the list.
    """
    raw = row.inconclusive_at
    parsed = None
    if raw:
        try:
            parsed = _as_utc(datetime.fromisoformat(raw))
        except ValueError:
            logger.warning(
                "Unparseable %s=%r on submission %s; falling back to submitted_at",
                _AT_KEY,
                raw,
                row.submission_id,
            )
    since = parsed or _as_utc(row.submitted_at)
    return min(since, now)


def _to_blocked_goal(row, now: datetime) -> BlockedGoal:
    since = _blocked_since(row, now)
    return BlockedGoal(
        goal_id=row.goal_id,
        submission_id=row.submission_id,
        user_email=row.user_email,
        goal_type=row.goal_type,
        goal_status=row.goal_status,
        pledge_amount=row.pledge_amount,
        currency=row.currency,
        deadline=_as_utc(row.deadline),
        blocked_since=since,
        blocked_for_seconds=int((now - since).total_seconds()),
        inconclusive_reason=row.inconclusive_reason,
        dispatch_attempts=row.dispatch_attempts or 0,
        max_attempts=settings.verification_dispatch_max_attempts,
        needs_operator_review=bool(row.needs_operator_review),
    )


async def list_blocked_goals(
    db: AsyncSession, *, needs_review_only: bool = False
) -> list[BlockedGoal]:
    """Every goal the deadline sweep is skipping, longest-blocked first.

    Read-only: no UPDATE, no commit, safe against a production database.
    """
    result = await db.execute(text(_BLOCKED_SQL), _params())
    now = datetime.now(timezone.utc)
    blocked = [_to_blocked_goal(row, now) for row in result]
    if needs_review_only:
        blocked = [b for b in blocked if b.needs_operator_review]
    # Longest-blocked first: that is the goal whose owner has been waiting most
    # and the pledge that has been uncollectable longest.
    blocked.sort(key=lambda b: b.blocked_since)
    return blocked


async def get_blocked_goal(db: AsyncSession, goal_id: uuid.UUID) -> BlockedGoal:
    """The blocked view of one goal, or a refusal explaining why it is not blocked."""
    result = await db.execute(text(_BY_GOAL_SQL), _params(goal_id=goal_id))
    row = result.one_or_none()
    if row is not None:
        return _to_blocked_goal(row, datetime.now(timezone.utc))

    # Distinguish "no such goal" from "not blocked", because they call for
    # different operator reactions (wrong id vs. already resolved).
    existing = await db.execute(
        text("SELECT status FROM goals WHERE id = :goal_id"), {"goal_id": goal_id}
    )
    status = existing.scalar_one_or_none()
    if status is None:
        raise GoalNotFound(f"No goal {goal_id}")
    raise GoalNotBlocked(
        f"Goal {goal_id} (status={status!r}) is not blocked on an inconclusive "
        "verification. Nothing was changed."
    )


async def resolve_blocked_goal(
    db: AsyncSession, goal_id: uuid.UUID, action: str
) -> ResolveResult:
    """Clear one blocked goal, the way the operator chose.

    ``ACTION_RETRY`` makes the submission claimable by
    ``reconcile_stale_dispatches`` again. ``ACTION_GIVE_UP`` closes the goal
    without charging.

    Operates on exactly one goal, and refuses (``GoalNotBlocked`` /
    ``GoalNotFound``) rather than guessing. Neither branch can reach
    ``process_charge_for_goal``: this module does not import it.
    """
    if action not in ACTIONS:
        raise ValueError(
            f"Unknown action {action!r}; expected one of {sorted(ACTIONS)}"
        )

    blocked = await get_blocked_goal(db, goal_id)
    now = datetime.now(timezone.utc)
    cap = settings.verification_dispatch_max_attempts

    if action == ACTION_RETRY:
        # Reset the counter the reconciler bounds itself by, and NULL
        # ``dispatched_at`` so the row is eligible on the very next sweep rather
        # than one staleness window later.
        #
        # Deliberately keeping ``outcome='inconclusive'`` in the details: that key
        # is what ``goal_verification_is_blocked`` reads, and these goals are past
        # their deadline. Clearing it would un-skip the goal in the deadline sweep,
        # which runs every 60s and would fail-and-charge the pledge before the
        # re-verification we just asked for ever ran — billing the user for our
        # outage at the exact moment an operator tried to be fair. The marker is
        # cleared by the retry itself: a verdict overwrites the details, and
        # another inconclusive result re-spends the budget and comes back here.
        new_attempts = 0
        await db.execute(
            text(
                """
                UPDATE proof_submissions
                SET dispatch_attempts = 0,
                    dispatched_at = NULL,
                    verification_details =
                        COALESCE(verification_details, '{}'::jsonb)
                        || CAST(:patch AS jsonb)
                WHERE id = :submission_id
                """
            ),
            {
                "patch": _action_patch(ACTION_RETRY, now),
                "submission_id": blocked.submission_id,
            },
        )
        new_status = blocked.goal_status
        reclaimable = True
    else:
        # Saturate the counter instead of leaving it where it was: a
        # transient-reason row can be blocked with budget to spare, and the
        # reconciler claims on ``verification_status='pending'`` alone — it never
        # looks at the goal's status, so an unsaturated row would be re-verified
        # after we closed the goal.
        new_attempts = cap
        await db.execute(
            text(
                """
                UPDATE proof_submissions
                SET dispatch_attempts = :cap,
                    verification_details =
                        COALESCE(verification_details, '{}'::jsonb)
                        || CAST(:patch AS jsonb)
                WHERE id = :submission_id
                """
            ),
            {
                "cap": cap,
                "patch": _action_patch(ACTION_GIVE_UP, now),
                "submission_id": blocked.submission_id,
            },
        )
        # Set directly, as the workers do. ``ALLOWED_TRANSITIONS`` in
        # ``app/services/goal.py`` governs the user-facing PATCH path and has no
        # pending_review -> cancelled edge; this is not that path, and closing a
        # goal we could not check is not a transition a user is requesting.
        await db.execute(
            text("UPDATE goals SET status = :status WHERE id = :goal_id"),
            {"status": GIVE_UP_STATUS, "goal_id": goal_id},
        )
        new_status = GIVE_UP_STATUS
        reclaimable = False

    await db.commit()

    if action == ACTION_GIVE_UP:
        await _notify_given_up(db, goal_id)

    logger.warning(
        "Operator resolved blocked goal %s: action=%s reason=%s "
        "goal_status %s -> %s attempts %s -> %s",
        goal_id,
        action,
        blocked.inconclusive_reason,
        blocked.goal_status,
        new_status,
        blocked.dispatch_attempts,
        new_attempts,
    )

    return ResolveResult(
        goal_id=goal_id,
        submission_id=blocked.submission_id,
        action=action,
        previous_goal_status=blocked.goal_status,
        new_goal_status=new_status,
        previous_dispatch_attempts=blocked.dispatch_attempts,
        new_dispatch_attempts=new_attempts,
        inconclusive_reason=blocked.inconclusive_reason,
        reclaimable_by_reconciler=reclaimable,
    )


async def _notify_given_up(db: AsyncSession, goal_id: uuid.UUID) -> None:
    """Close the loop with the owner, who was told a human would look.

    Best-effort: the resolution is already committed, and a notification failure
    must not make an operator think the goal is still stuck.
    """
    result = await db.execute(
        text("SELECT user_id, title FROM goals WHERE id = :goal_id"),
        {"goal_id": goal_id},
    )
    row = result.one_or_none()
    if row is None:
        return
    try:
        await create_notification(
            db,
            user_id=row.user_id,
            # ``notification_type`` has no value for "closed, nobody's fault"
            # (app/models/notification.py). ``goal_failed`` is the closest true
            # statement about the outcome — the goal is closed unachieved — and
            # the body carries the part that matters: no charge.
            notification_type="goal_failed",
            title=f"Closed without a verdict: {row.title}",
            body=(
                "We could not complete the check for this goal, and the problem "
                "was on our side. You have not been charged, and the goal has "
                "been closed. If you want another attempt, create the goal again."
            ),
            goal_id=goal_id,
        )
    except Exception:
        logger.exception(
            "Could not notify the owner that goal %s was closed without a verdict",
            goal_id,
        )
