import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditEvent(Base):
    # No FKs on purpose: an FK action (SET NULL/CASCADE) would UPDATE/DELETE audit rows,
    # which the DB append-only trigger blocks.
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_tenant_id_lead_id_at", "tenant_id", "lead_id", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    from_state: Mapped[str | None] = mapped_column(Text)
    to_state: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
