import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDMixin


class ProofSubmission(UUIDMixin, Base):
    __tablename__ = "proof_submissions"

    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    proof_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    verification_status: Mapped[str] = mapped_column(
        Enum("pending", "verified", "failed", name="verification_status"),
        default="pending",
        nullable=False,
    )
    verification_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    goal = relationship("Goal", back_populates="proofs")
