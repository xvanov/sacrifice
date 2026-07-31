import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDMixin


class Payment(UUIDMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        # Database-level backstop against collecting the same pledge twice.
        #
        # ``process_charge_for_goal`` guards with a read-then-write (SELECT ...
        # WHERE goal_id, skip if a row exists), which two concurrent charge
        # attempts can both pass before either commits. Stripe's
        # idempotency_key collapses the *money* into one PaymentIntent, but
        # those keys expire after 24h — two attempts more than a day apart are
        # not deduped by Stripe, and only a committed payments row protects the
        # charge. This index makes the ledger the authority.
        #
        # Deliberately PARTIAL (``WHERE status = 'succeeded'``) rather than a
        # plain UNIQUE (goal_id): non-succeeded rows legitimately exist for a
        # goal still chargeable in principle — the no-payment-method path and
        # the retry-exhausted path both insert status='failed' — and the Stripe
        # webhook reconciler promotes a row to 'succeeded' by UPDATE. A plain
        # unique constraint would cement "one charge attempt ever" into the
        # schema. Keep this name/predicate in sync with the Alembic revision.
        Index(
            "uq_payments_goal_id_succeeded",
            "goal_id",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
    )

    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="usd")
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    stripe_transfer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("pending", "succeeded", "failed", "refunded", name="payment_status"),
        default="pending",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    goal = relationship("Goal", back_populates="payments")
    user = relationship("User", back_populates="payments")
