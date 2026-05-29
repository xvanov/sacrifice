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
        allowed = {"youtube_video", "api_endpoint", "dev_sandbox", "github_repo"}
        if v not in allowed:
            raise ValueError(f"goal_type must be one of {allowed}")
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
    deadline: datetime | None = None
    pledge_amount: int | None = None
    charity_id: str | None = None
    timezone: str | None = None
    status: str | None = None

    @field_validator("pledge_amount")
    @classmethod
    def pledge_must_be_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("pledge_amount must be positive")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is not None:
            allowed = {"draft", "active", "pending_review", "verified", "failed", "cancelled", "awaiting_goal_type"}
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
    timezone: str
    recurrence: str | None
    status: str
    charity_id: str | None
    awaiting_direction_id: str | None = None
    criteria: GoalCriteriaResponse | None = None
    created_at: datetime
    updated_at: datetime
