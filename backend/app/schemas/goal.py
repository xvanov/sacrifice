from datetime import datetime

from pydantic import BaseModel, field_validator


class GoalCriteriaCreate(BaseModel):
    criteria_type: str
    criteria_data: dict


class GoalCreate(BaseModel):
    title: str
    description: str | None = None
    deadline: datetime
    pledge_amount: int
    goal_type: str
    criteria: dict
    charity_id: str | None = None
    timezone: str = "UTC"
    recurrence: str = "none"
    currency: str = "usd"

    @field_validator("pledge_amount")
    @classmethod
    def pledge_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("pledge_amount must be positive")
        return v

    @field_validator("goal_type")
    @classmethod
    def validate_goal_type(cls, v):
        if not v or not v.strip():
            raise ValueError("goal_type must not be empty")
        v = v.strip()
        # Reject types that aren't registered plugins. Previously any non-empty
        # string was accepted, so a goal could be created with e.g. "api"
        # (correct name: "api_endpoint") and then never be fulfilled —
        # submit-proof would 400 on the unknown type. Fail fast at creation.
        # Imported lazily so the schema module doesn't pull the registry (and
        # its plugin discovery) at import time.
        from app.goal_types import registry

        # "__generated__" is the sentinel the chat flow uses for a goal whose
        # type is still being built (chat.GENERATED_PLACEHOLDER_TYPE); it's a
        # legitimate creation value even though it's not a registered plugin.
        valid = set(registry.list_types()) | {"__generated__"}
        if v not in valid:
            raise ValueError(f"Unknown goal_type '{v}'. Valid types: {sorted(valid)}")
        return v

    @field_validator("recurrence")
    @classmethod
    def validate_recurrence(cls, v):
        allowed = {"none", "daily", "weekly", "monthly"}
        if v not in allowed:
            raise ValueError(f"recurrence must be one of {allowed}")
        return v


class GoalUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    #: Movable until the goal is within ``app/services/goal.DEADLINE_LOCK_WINDOW``
    #: of falling due, at which point it is fixed and a change is a 403. Sending the
    #: deadline unchanged is always fine — the edit form submits every field, so an
    #: echo is not treated as a move.
    deadline: datetime | None = None
    pledge_amount: int | None = None
    charity_id: str | None = None
    timezone: str | None = None
    recurrence: str | None = None
    status: str | None = None
    #: Replacement criteria for a **draft** goal, gated exactly as at creation.
    #:
    #: Added because the activation gate had no inverse: a draft whose criteria
    #: the gate refuses (a value it cannot coerce, a required field never
    #: collected) could not be repaired by any endpoint, so the only way out was
    #: to delete the goal and start over.
    #:
    #: Refused on any other status by ``app/routes/goals.py``. An active goal's
    #: criteria are the commitment its pledge is measured against, and letting
    #: the owner rewrite them mid-flight is moving the goalposts — a
    #: ``min_commits`` edited down the day before the deadline is charge evasion
    #: with extra steps.
    criteria: dict | None = None

    @field_validator("pledge_amount")
    @classmethod
    def pledge_must_be_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("pledge_amount must be positive")
        return v

    @field_validator("recurrence")
    @classmethod
    def validate_recurrence(cls, v):
        if v is not None and v not in {"none", "daily", "weekly", "monthly"}:
            raise ValueError("recurrence must be one of none, daily, weekly, monthly")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is not None:
            allowed = {
                "draft",
                "active",
                "pending_review",
                "verified",
                "failed",
                "cancelled",
                "payment_failed",
                "awaiting_goal_type",
            }
            if v not in allowed:
                raise ValueError(f"status must be one of {allowed}")
        return v


class GoalCriteriaResponse(BaseModel):
    criteria_type: str
    criteria_data: dict


class GoalResponse(BaseModel):
    id: str
    title: str
    description: str | None
    goal_type: str
    pledge_amount: int
    currency: str
    deadline: datetime
    #: True once this goal is inside ``DEADLINE_LOCK_WINDOW`` of falling due, i.e.
    #: an attempt to move ``deadline`` would now be refused with a 403.
    deadline_locked: bool = False
    #: True once this goal is inside ``DEADLINE_LOCK_WINDOW`` of falling due, i.e.
    #: an attempt to change ``pledge_amount`` or ``charity_id`` would now be
    #: refused with a 403. Same window as ``deadline_locked``.
    stake_locked: bool = False
    timezone: str
    recurrence: str | None
    status: str
    charity_id: str | None
    awaiting_direction_id: str | None = None
    criteria: GoalCriteriaResponse | None = None
    created_at: datetime
    updated_at: datetime
