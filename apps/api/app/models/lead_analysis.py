import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LeadAnalysis(Base):
    __tablename__ = "lead_analysis"
    __table_args__ = (CheckConstraint("fit_score BETWEEN 0 AND 100", name="fit_score_range"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), unique=True
    )
    intent: Mapped[str] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(Text)
    budget: Mapped[str | None] = mapped_column(Text)
    urgency: Mapped[str | None] = mapped_column(Text)
    fit_score: Mapped[int] = mapped_column(Integer)
    reasoning: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    raw_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
