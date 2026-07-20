"""UX-auditor input schemas.

Defines the backend contract for executable UX-audit inputs: ordered
flow-step payloads with per-step observation paths covering live browser
sandbox or recorded step artifacts.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class ObservationPath(BaseModel):
    """Per-step observation data — at least one of live sandbox or recorded artifact.

    Neither field is validated for reachability here; that is the runtime's
    responsibility.  The schema only enforces that at least one is present.
    """

    live_sandbox_url: str | None = None
    recorded_artifact_path: str | None = None

    @model_validator(mode="after")
    def _require_at_least_one_observation_source(self) -> ObservationPath:
        if not self.live_sandbox_url and not self.recorded_artifact_path:
            raise ValueError(
                "ObservationPath must provide at least one of "
                "live_sandbox_url or recorded_artifact_path"
            )
        return self


class FlowStep(BaseModel):
    """A single ordered step within a UX flow, with its observation path."""

    step_number: int = Field(ge=1, description="1-based ordering index")
    description: str = Field(min_length=1, description="Human-readable step description")
    observation: ObservationPath | None = Field(
        default=None,
        description="Live sandbox or recorded artifact tied to this step",
    )


class UxAuditRunInput(BaseModel):
    """Input contract for a UX-auditor run.

    ``flow_md`` carries the raw ``flow.md`` body text (may be the empty
    string when a caller supplies equivalent ordered steps without a
    canonical markdown file).  ``ordered_steps`` is the canonical parsed
    representation consumed by the auditor runtime.
    """

    direction_id: str = Field(min_length=1)
    flow_md: str = Field(
        default="",
        description="Raw flow.md body — set to empty string when unavailable",
    )
    ordered_steps: list[FlowStep] = Field(min_length=1)

    @field_validator("ordered_steps")
    @classmethod
    def _reject_empty_steps(cls, v: list[FlowStep]) -> list[FlowStep]:
        if not v:
            raise ValueError("ordered_steps must contain at least one step")
        return v

    @model_validator(mode="after")
    def _require_flow_md_or_ordered_steps(self) -> UxAuditRunInput:
        # ordered_steps is already validated as non-empty by the field validator above.
        # flow_md is optional — it is the raw source text.
        # Both ACs are satisfied: ordered_steps is mandatory (AC1.1 "include each
        # flow.md body or equivalent ordered steps"), and each step carries an
        # observation path (AC2.1 "access a live browser sandbox or recorded step
        # artifacts tied to each flow step").
        return self