import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDMixin


class ProofSubmission(UUIDMixin, Base):
    __tablename__ = "proof_submissions"

    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    proof_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    verification_status: Mapped[str] = mapped_column(
        Enum("pending", "verified", "failed", name="verification_status"),
        default="pending",
        nullable=False,
    )
    verification_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Dispatch bookkeeping (verification reconciliation) ────────────────
    # "pending" alone cannot distinguish "queued and being verified" from
    # "never reached the worker", and the deadline sweep charges the pledge
    # either way. These three columns make an un-verified proof recoverable.
    #
    # NULL means no successful enqueue yet: either the request's dispatch call
    # raised (broker down), or the row is mid-request. Set to now() on every
    # successful enqueue, including re-dispatch.
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Enqueue attempts made, successful or not, including the one from the
    # submit-proof request. Bounds reconciler retries.
    dispatch_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # The effective criteria handed to dispatch_verification. Goal types refine
    # criteria from the submitted body (api_endpoint overrides url/method,
    # github_repo injects the encrypted PAT) and the body is not retained, so
    # without this snapshot a re-dispatch would verify against DIFFERENT input
    # than the original — e.g. a private repo with no token, which fails
    # verification and charges the pledge. Never exposed by any endpoint.
    dispatch_criteria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    goal = relationship("Goal", back_populates="proofs")

    __table_args__ = (
        # Mirrors the partial index created in migration d5e6f7a8b9c0 so a
        # create_all-built schema (tests) matches a migrated one. The
        # reconciler sweep only ever scans pending rows.
        Index(
            "ix_proof_submissions_pending_dispatch",
            "submitted_at",
            "dispatch_attempts",
            postgresql_where=text("verification_status = 'pending'"),
        ),
    )
