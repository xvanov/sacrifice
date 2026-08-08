import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class Goal(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "goals"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    goal_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    pledge_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="usd")
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    recurrence: Mapped[str | None] = mapped_column(
        Enum("none", "daily", "weekly", "monthly", name="recurrence"),
        default="none",
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "draft",
            "active",
            "pending_review",
            "verified",
            "failed",
            "cancelled",
            "payment_failed",
            "awaiting_goal_type",
            name="goal_status",
        ),
        default="draft",
        nullable=False,
    )
    charity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    awaiting_direction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Set only by the (updated) failure-resolution paths, to the next local
    # midnight (in `timezone`) after `deadline`. NULL means either "not failed
    # yet" or "failed before this buffer existed" — see the migration's
    # docstring for why nothing ever backfills this column.
    charge_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user = relationship("User", back_populates="goals")
    criteria = relationship("GoalCriteria", back_populates="goal", uselist=False)
    proofs = relationship("ProofSubmission", back_populates="goal")
    payments = relationship("Payment", back_populates="goal")
    uploads = relationship("MediaUpload", back_populates="goal")


class GoalCriteria(UUIDMixin, Base):
    __tablename__ = "goal_criteria"

    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id"), nullable=False, unique=True
    )
    criteria_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    criteria_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    goal = relationship("Goal", back_populates="criteria")
