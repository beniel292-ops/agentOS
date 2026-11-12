import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DraftDecision(enum.StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), unique=True
    )
    subject: Mapped[str] = mapped_column(Text, server_default=text("''"))
    body: Mapped[str] = mapped_column(Text)
    edited_body: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[DraftDecision | None] = mapped_column(
        Enum(DraftDecision, native_enum=False, create_constraint=True, validate_strings=True,
             length=20, values_callable=lambda e: [m.value for m in e])
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    send_idempotency_key: Mapped[str] = mapped_column(
        Text, unique=True, default=lambda: str(uuid.uuid4())
    )
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
