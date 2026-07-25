"""Audit event model for capturing proof validation outcomes.

Records accepted and rejected proof validation outcomes so the system
can account for every submission attempt — not just the ones that pass.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goals.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    # ``proof_dispatch_failed`` records that a validated proof could not be
    # handed to the verification queue (broker down). It is not a rejection —
    # the proof was accepted and persisted — so it needs its own value rather
    # than overloading proof_rejected. Added in migration e7a8b9c0d1e2.
    event_type: Mapped[str] = mapped_column(
        Enum(
            "proof_accepted",
            "proof_rejected",
            "proof_dispatch_failed",
            name="audit_event_type",
        ),
        nullable=False,
    )
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    goal = relationship("Goal")
    user = relationship("User")
