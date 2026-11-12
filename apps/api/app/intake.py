import hashlib
import uuid

from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import AuditEvent, Lead, LeadStatus


class LeadIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    company: str | None = Field(default=None, max_length=200)
    message: str = Field(min_length=1, max_length=5000)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_lead(
    session: Session,
    tenant_id: uuid.UUID,
    data: LeadIn,
    idempotency_key: str | None,
    source: str,
    actor: str = "intake:api",
) -> tuple[Lead, bool]:
    """Insert a NEW lead once per (tenant, idempotency key). Caller commits and enqueues."""
    key = idempotency_key or sha256_hex(f"{data.email.lower()}|{data.message.strip()}")
    lead_id = session.scalar(
        insert(Lead)
        .values(
            tenant_id=tenant_id,
            source=source,
            name=data.name,
            email=data.email,
            company=data.company,
            message=data.message,
            status=LeadStatus.NEW,
            idempotency_key=key,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
        .returning(Lead.id)
    )
    created = lead_id is not None
    lead = session.scalars(
        select(Lead).where(Lead.tenant_id == tenant_id, Lead.idempotency_key == key)
    ).one()
    if created:
        session.add(
            AuditEvent(
                tenant_id=tenant_id,
                lead_id=lead.id,
                actor=actor,
                action="lead_created",
                from_state=None,
                to_state=LeadStatus.NEW.value,
                data={"source": source},
            )
        )
        session.flush()
    return lead, created
