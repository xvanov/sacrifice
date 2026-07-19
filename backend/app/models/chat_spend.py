import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class ChatSpendLedger(UUIDMixin, Base):
    __tablename__ = "chat_spend_ledger"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    call_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    cost_millicents: Mapped[int] = mapped_column(Integer, nullable=False)
    call_description: Mapped[str] = mapped_column(String(255), nullable=True)
