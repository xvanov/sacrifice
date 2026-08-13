import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.goal import Goal, GoalCriteria
from app.schemas.goal import GoalCreate, GoalUpdate
from app.services.criteria_gate import gate_criteria, stamp_goal_created_at
from app.services.input_parsing import DEADLINE_MIN_LEAD, describe_window

# Statuses in which the deadline sweep can fail-and-charge a goal. A goal must
# never enter one of these with a deadline already in the past, or it is failed
# the instant the next sweep runs — before the owner ever had a chance to act.
_ENFORCEABLE_STATUSES = frozenset({"active", "pending_review"})

_DEADLINE_TOO_SOON_MESSAGE = (
    "deadline must be at least {lead} in the future; it cannot be in the "
    "past or within the next {lead}"
).format(lead=describe_window(DEADLINE_MIN_LEAD))

#: How long before a live goal falls due its deadline stops being editable.
#:
#: The deadline is the whole commitment: it is the one thing that decides whether
#: the pledge is charged. An owner who could still move it in the final stretch had
#: an escape hatch that needed no proof and broke no rule — "push it a week" at
#: minute 29 is not a rescheduled goal, it is an un-failed one. Inside this window
#: the deadline is fixed, in both directions (see ``_deadline_locked``).
#:
#: HARDENED — see context/accountability-invariants.md Invariant 2.
#: Do NOT reduce this value. Do NOT add an override flag or bypass parameter.
#: Do NOT make this conditional on goal type, user role, or plan tier.
#:
#: Narrowed three hours -> one on 2026-08-12, one hour -> 30 minutes on
#: 2026-08-13, each with explicit owner sign-off.
#:
#: Must stay EQUAL to ``DEADLINE_MIN_LEAD``. The two guards run in sequence: this
#: one refuses an edit inside the window, then the too-soon guard refuses any new
#: deadline under the lead. If the lead were longer, the gap between them would be
#: a band where the deadline is editable but every replacement is too soon —
#: pushing it out would be the only move the API accepts, which is the evasion
#: this window exists to prevent. Retune them together or not at all.
DEADLINE_LOCK_WINDOW = timedelta(minutes=30)

#: Tolerance for "the client sent back the deadline it was served".
#:
#: A stored deadline carries Postgres microseconds; a client that round-trips it
#: through JSON (JavaScript ``Date`` keeps milliseconds) returns a value a few
#: hundred microseconds off. That is an echo, not an edit, and it must not trip the
#: lock — otherwise editing a *description* an hour before the deadline would be
#: refused, because the edit form submits every field it holds.
#:
#: HARDENED — keep at ≤1 second. A wider value turns the "echo" window into a
#: real edit window that sidesteps the deadline lock. Do not expand this to minutes.
_DEADLINE_ECHO_TOLERANCE = timedelta(seconds=1)

_DEADLINE_LOCKED_MESSAGE = (
    "the deadline is fixed within {window} of falling due and can no longer "
    "be changed; a goal this close to its deadline is met by proof, not by moving "
    "the date"
).format(window=describe_window(DEADLINE_LOCK_WINDOW))


class DeadlineLocked(ValueError):
    """Raised when a deadline inside ``DEADLINE_LOCK_WINDOW`` is edited.

    A ``ValueError`` so existing callers that only catch that still refuse the
    write, and a distinct type so ``app/routes/goals.py`` can answer 403 (the rule
    forbids this, no request will satisfy it) rather than 400.
    """


def _as_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as UTC; leave aware datetimes untouched."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _deadline_too_soon(deadline: datetime) -> bool:
    """True if the deadline is in the past or inside the minimum-lead window."""
    return _as_utc(deadline) <= datetime.now(timezone.utc) + DEADLINE_MIN_LEAD


def _deadline_locked(current_deadline: datetime) -> bool:
    """True if the goal's *stored* deadline is inside the lock window (or past).

    Measured against the deadline the goal already holds, never the requested one:
    what freezes the date is the goal being in its final stretch, so pulling a
    far-off deadline in to forty minutes from now stays legal (the owner is making
    it harder on themselves), while touching a deadline that is already ten
    minutes away — in either direction — does not.
    """
    return (
        _as_utc(current_deadline) - datetime.now(timezone.utc) <= DEADLINE_LOCK_WINDOW
    )


def _deadline_changed(current: datetime, requested: datetime) -> bool:
    """True if ``requested`` is a real move rather than a round-tripped echo."""
    return abs(_as_utc(requested) - _as_utc(current)) > _DEADLINE_ECHO_TOLERANCE


def deadline_is_locked(goal: Goal) -> bool:
    """True if ``PUT`` would refuse to move this goal's deadline right now.

    Served on the goal payload so a client can grey the field out with the note
    instead of re-deriving the window (and drifting from it). The server remains
    the enforcement point — this is the same predicate ``update_goal`` applies.
    """
    return (
        goal.status in _ENFORCEABLE_STATUSES
        and goal.deadline is not None
        and _deadline_locked(goal.deadline)
    )


TYPE_TO_CRITERIA_TYPE = {
    "youtube_video": "youtube",
    "api_endpoint": "api_endpoint",
    "dev_sandbox": "dev_sandbox",
    "github_repo": "github_repo",
    "__generated__": "generated",
}

ALLOWED_TRANSITIONS = {
    None: {"draft"},
    "draft": {"active", "cancelled", "awaiting_goal_type"},
    "active": {"pending_review", "cancelled", "failed"},
    "pending_review": {"verified", "failed"},
    "awaiting_goal_type": {"active", "cancelled"},
}


async def create_goal(
    db: AsyncSession,
    user_id: uuid.UUID,
    data: GoalCreate,
    status: str = "draft",
    awaiting_direction_id: str | None = None,
    *,
    commit: bool = True,
) -> Goal:
    if status in _ENFORCEABLE_STATUSES and _deadline_too_soon(data.deadline):
        raise ValueError(_DEADLINE_TOO_SOON_MESSAGE)

    # Criteria gate, here rather than on ``GoalCreate``, because this is the one
    # function that actually writes a goal_criteria row: ``POST /api/goals`` and
    # the chat's create-from-session both end up here, so neither can skip it,
    # and a future writer inherits it by construction. On the schema it would fire
    # before the chat's own consistency checks and replace their (more pointed)
    # diagnosis with a criteria complaint — the ordering the ``ValueError`` for a
    # too-soon deadline above already establishes.
    #
    # ``criteria`` was a bare ``dict`` with no validation anywhere in the stack,
    # so everything the chat flow enforced was reachable-around by not using the
    # chat: a string ``expected_status`` that ``app/workers/api_check.py`` can
    # never match, a ``min_duration_seconds`` that makes
    # ``app/workers/youtube.py`` raise past its own handler, criteria that name no
    # check at all. Each of those ends in a real charge for a goal its owner could
    # not win. See ``app/services/criteria_gate``.
    criteria_data = gate_criteria(data.goal_type, data.criteria)

    # Stamp the criteria a goal type declares as server-recorded, after the gate
    # so a supplied value cannot satisfy a contract it is not allowed to state.
    # Today that is ``github_repo.commits_since``, the instant its commit counts
    # are measured from: without it "push 3 commits by Saturday" was met by three
    # commits from last year. The app clock is used rather than ``goal.created_at``
    # (a DB-side default) so the anchor and the verifier's "is this anchor in the
    # future?" check are read from the same clock.
    criteria_data = stamp_goal_created_at(
        data.goal_type, criteria_data, created_at=datetime.now(timezone.utc)
    )

    goal = Goal(
        user_id=user_id,
        title=data.title,
        description=data.description,
        goal_type=data.goal_type,
        pledge_amount=data.pledge_amount,
        currency=data.currency,
        deadline=data.deadline,
        timezone=data.timezone,
        recurrence=data.recurrence,
        status=status,
        charity_id=data.charity_id,
        awaiting_direction_id=awaiting_direction_id,
    )
    db.add(goal)
    await db.flush()

    criteria_type = TYPE_TO_CRITERIA_TYPE.get(data.goal_type, data.goal_type)
    criteria = GoalCriteria(
        goal_id=goal.id,
        criteria_type=criteria_type,
        criteria_data=criteria_data,
    )
    db.add(criteria)
    if commit:
        await db.commit()
    return goal


async def get_goal_criteria(
    db: AsyncSession, goal_id: uuid.UUID
) -> GoalCriteria | None:
    result = await db.execute(
        select(GoalCriteria).where(GoalCriteria.goal_id == goal_id)
    )
    return result.scalar_one_or_none()


async def get_user_goals(
    db: AsyncSession, user_id: uuid.UUID, status: str | None = None
) -> list[Goal]:
    stmt = select(Goal).where(Goal.user_id == user_id)
    if status:
        stmt = stmt.where(Goal.status == status)
    stmt = stmt.order_by(Goal.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_goal_by_id(db: AsyncSession, goal_id: uuid.UUID) -> Goal | None:
    result = await db.execute(select(Goal).where(Goal.id == goal_id))
    return result.scalar_one_or_none()


async def update_goal(db: AsyncSession, goal: Goal, data: GoalUpdate) -> Goal:
    if data.status is not None and data.status != goal.status:
        allowed = ALLOWED_TRANSITIONS.get(goal.status, set())
        if data.status not in allowed:
            raise ValueError(
                f"Cannot transition from '{goal.status}' to '{data.status}'"
            )

    # An edit form submits every field it holds, so a request carrying the deadline
    # it was just served is not asking to move it. Both deadline guards below key
    # off a real move rather than mere presence.
    moving_deadline = (
        data.deadline is not None
        and goal.deadline is not None
        and _deadline_changed(goal.deadline, data.deadline)
    )

    # The deadline of a live goal is immovable in its final hours. Checked before
    # the too-soon guard below so an owner pulling the date *closer* in the last
    # stretch is told the deadline is locked rather than that their new date needs
    # more runway — the second is true but answers the wrong question.
    #
    # Only enforceable statuses: a draft's deadline is not a commitment yet (it is
    # chargeable by nobody until it is activated, and the activation guard already
    # refuses a stale one), so freezing draft edits would strand a draft whose
    # deadline has quietly gone by with no way to repair it.
    if (
        moving_deadline
        and goal.status in _ENFORCEABLE_STATUSES
        and _deadline_locked(goal.deadline)
    ):
        raise DeadlineLocked(_DEADLINE_LOCKED_MESSAGE)

    # Same future-deadline guard as create_goal, on the two paths that can put a
    # goal into an enforceable state with a stale deadline: activating a draft,
    # or editing the deadline of a goal that is (or is becoming) enforceable.
    activating = data.status in _ENFORCEABLE_STATUSES and data.status != goal.status
    if activating or (moving_deadline and goal.status in _ENFORCEABLE_STATUSES):
        effective_deadline = (
            data.deadline if data.deadline is not None else goal.deadline
        )
        if effective_deadline is not None and _deadline_too_soon(effective_deadline):
            raise ValueError(_DEADLINE_TOO_SOON_MESSAGE)

    set_clauses = []
    params = {}

    for field in (
        "title",
        "description",
        "deadline",
        "pledge_amount",
        "charity_id",
        "timezone",
        "recurrence",
    ):
        value = getattr(data, field, None)
        # None means "not provided" for most fields, but charity_id and
        # description are nullable by design: an explicit null in the request
        # (model_fields_set) clears them — that's how a recipient is removed.
        explicit_null = (
            field in ("charity_id", "description") and field in data.model_fields_set
        )
        if value is not None or explicit_null:
            set_clauses.append(f"{field} = :{field}")
            params[field] = value

    if data.status is not None and data.status != goal.status:
        set_clauses.append("status = :new_status")
        params["new_status"] = data.status

    if set_clauses:
        params["goal_id"] = goal.id
        await db.execute(
            text(f"UPDATE goals SET {', '.join(set_clauses)} WHERE id = :goal_id"),
            params,
        )
        await db.commit()

    result = await db.execute(
        select(Goal).where(Goal.id == goal.id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def delete_goal(db: AsyncSession, goal: Goal) -> None:
    criteria = await get_goal_criteria(db, goal.id)
    if criteria:
        await db.execute(
            text("DELETE FROM goal_criteria WHERE id = :id"),
            {"id": criteria.id},
        )
    await db.execute(
        text("DELETE FROM notifications WHERE goal_id = :goal_id"),
        {"goal_id": goal.id},
    )
    await db.execute(
        text("DELETE FROM proof_submissions WHERE goal_id = :goal_id"),
        {"goal_id": goal.id},
    )
    await db.execute(
        text("DELETE FROM payments WHERE goal_id = :goal_id"),
        {"goal_id": goal.id},
    )
    await db.execute(
        text("DELETE FROM goals WHERE id = :id"),
        {"id": goal.id},
    )
    await db.commit()
