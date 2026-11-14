import enum
import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _enum(cls: type[enum.StrEnum], name: str, length: int = 20) -> Enum:
    return Enum(
        cls, name=name, native_enum=False, create_constraint=True, validate_strings=True,
        length=length, values_callable=lambda e: [m.value for m in e],
    )


class TaskStatus(enum.StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DROPPED = "dropped"


class TaskSource(enum.StrEnum):
    VOICE = "voice"
    TELEGRAM = "telegram"
    DASHBOARD = "dashboard"
    AGENT = "agent"


class CallKind(enum.StrEnum):
    MORNING = "morning"
    EVENING = "evening"
    CALLBACK = "callback"


class CallStatus(enum.StrEnum):
    PENDING = "pending"
    PLACED = "placed"
    COMPLETED = "completed"
    MISSED = "missed"
    CANCELLED = "cancelled"


class Verification(enum.StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


class OppStatus(enum.StrEnum):
    NEW = "new"
    SHOWN = "shown"
    SAVED = "saved"
    DISMISSED = "dismissed"


class MemoryKind(enum.StrEnum):
    PREFERENCE = "preference"
    PROJECT = "project"
    DECISION = "decision"
    DEADLINE = "deadline"
    SUMMARY = "summary"


def _tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentKey(Base):
    __tablename__ = "agent_keys"
    __table_args__ = (
        Index(
            "uq_agent_keys_tenant_id_name_active", "tenant_id", "name", unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    # sha256 of the key (plaintext is never stored) + display prefix
    key_hash: Mapped[str] = mapped_column(Text, unique=True)
    key_prefix: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CosSettings(Base):
    __tablename__ = "cos_settings"
    __table_args__ = (
        CheckConstraint("max_calls_per_day BETWEEN 1 AND 20", name="ck_cos_settings_max_calls"),
        CheckConstraint("min_gap_minutes BETWEEN 5 AND 240", name="ck_cos_settings_min_gap"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    stopped: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_by: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, server_default=text("'Asia/Kolkata'"))
    morning_time: Mapped[time] = mapped_column(Time, server_default=text("'08:30'"))
    evening_time: Mapped[time] = mapped_column(Time, server_default=text("'21:00'"))
    quiet_start: Mapped[time] = mapped_column(Time, server_default=text("'22:30'"))
    quiet_end: Mapped[time] = mapped_column(Time, server_default=text("'07:30'"))
    max_calls_per_day: Mapped[int] = mapped_column(Integer, server_default=text("6"))
    min_gap_minutes: Mapped[int] = mapped_column(Integer, server_default=text("10"))


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("char_length(title) BETWEEN 1 AND 300", name="ck_tasks_title_len"),
        CheckConstraint("priority BETWEEN 1 AND 3", name="ck_tasks_priority"),
        Index("ix_tasks_tenant_id_status_due_date", "tenant_id", "status", "due_date"),
        Index(
            "uq_tasks_tenant_id_title_norm_open", "tenant_id", "title_norm", unique=True,
            postgresql_where=text("status IN ('pending','in_progress')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    title: Mapped[str] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus, "task_status"), default=TaskStatus.PENDING
    )
    priority: Mapped[int] = mapped_column(Integer, default=2, server_default=text("2"))
    due_date: Mapped[date | None] = mapped_column(Date)
    project: Mapped[str | None] = mapped_column(Text)
    source: Mapped[TaskSource] = mapped_column(_enum(TaskSource, "task_source"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScheduledCall(Base):
    __tablename__ = "scheduled_calls"
    __table_args__ = (
        Index(
            "uq_scheduled_calls_pending_callback", "tenant_id", unique=True,
            postgresql_where=text("kind = 'callback' AND status = 'pending'"),
        ),
        Index(
            "uq_scheduled_calls_daily_slot", "tenant_id", "kind",
            text("((due_at AT TIME ZONE 'UTC')::date)"), unique=True,
            postgresql_where=text("kind IN ('morning','evening') AND status <> 'cancelled'"),
        ),
        Index("ix_scheduled_calls_tenant_id_due_at", "tenant_id", "due_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    kind: Mapped[CallKind] = mapped_column(_enum(CallKind, "call_kind"))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[CallStatus] = mapped_column(
        _enum(CallStatus, "call_status"), default=CallStatus.PENDING
    )
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Checkin(Base):
    __tablename__ = "checkins"
    __table_args__ = (Index("ix_checkins_tenant_id_started_at", "tenant_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    kind: Mapped[CallKind] = mapped_column(_enum(CallKind, "checkin_kind"))
    call_id: Mapped[str | None] = mapped_column(Text)  # agentring call id
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcript: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        CheckConstraint("char_length(title) BETWEEN 1 AND 300", name="ck_opportunities_title_len"),
        CheckConstraint("relevance BETWEEN 0 AND 100", name="ck_opportunities_relevance"),
        Index(
            "uq_opportunities_tenant_id_url", "tenant_id", "url", unique=True,
            postgresql_where=text("url IS NOT NULL"),
        ),
        Index("ix_opportunities_tenant_id_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    title: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    relevance: Mapped[int] = mapped_column(Integer)
    estimated_value: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[date | None] = mapped_column(Date)
    next_action: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[Verification] = mapped_column(
        _enum(Verification, "opportunity_verification"), default=Verification.UNVERIFIED
    )
    status: Mapped[OppStatus] = mapped_column(
        _enum(OppStatus, "opportunity_status"), default=OppStatus.NEW
    )
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class MemoryNote(Base):
    __tablename__ = "memory_notes"
    __table_args__ = (
        CheckConstraint("char_length(text) BETWEEN 1 AND 2000", name="ck_memory_notes_text_len"),
        UniqueConstraint(
            "tenant_id", "content_hash", name="uq_memory_notes_tenant_id_content_hash"
        ),
        Index("ix_memory_notes_tenant_id_kind", "tenant_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    kind: Mapped[MemoryKind] = mapped_column(_enum(MemoryKind, "memory_kind"))
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
