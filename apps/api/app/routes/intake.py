import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.auth import Current, get_current
from app.db import get_db, get_session_factory
from app.intake import LeadIn, create_lead, sha256_hex
from app.jobs.queue import enqueue_process_lead
from app.models import AuditEvent, Lead, Tenant, UserRole

router = APIRouter(prefix="/v1")


class IntakeResult(BaseModel):
    id: uuid.UUID
    status: str
    duplicate: bool


def _result(response: Response, lead: Lead, created: bool) -> IntakeResult:
    response.status_code = 201 if created else 200
    return IntakeResult(id=lead.id, status=lead.status, duplicate=not created)


@router.get("/intake-key")
def get_intake_key(current: Annotated[Current, Depends(get_current)]) -> dict:
    return {"prefix": current.tenant.intake_key_prefix}


@router.post("/intake-key")
def rotate_intake_key(
    current: Annotated[Current, Depends(get_current)],
    session: Annotated[Session, Depends(get_db)],
) -> dict:
    if current.user.role != UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Only the owner can manage the intake key")
    key = "aos_" + secrets.token_urlsafe(32)
    tenant = session.get(Tenant, current.tenant.id)
    tenant.intake_key_hash = sha256_hex(key)
    tenant.intake_key_prefix = key[:12]
    session.add(
        AuditEvent(
            tenant_id=tenant.id,
            lead_id=None,
            actor=f"user:{current.user.id}",
            action="intake_key_rotated",
            data={},
        )
    )
    return {"key": key, "prefix": tenant.intake_key_prefix}


def _create_public(
    factory, key: str, data: LeadIn, idempotency_key: str | None
) -> tuple[Lead, bool] | None:
    with factory() as session:
        tenant_id = session.scalar(
            select(Tenant.id).where(Tenant.intake_key_hash == sha256_hex(key))
        )
        if tenant_id is None:
            return None
        lead, created = create_lead(session, tenant_id, data, idempotency_key, "api")
        session.commit()  # must be committed before the job is enqueued
        return lead, created


@router.post("/leads", status_code=201)
async def intake_lead(
    data: LeadIn,
    response: Response,
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    x_intake_key: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
) -> IntakeResult:
    found = (
        await run_in_threadpool(_create_public, factory, x_intake_key, data, idempotency_key)
        if x_intake_key
        else None
    )
    if found is None:
        raise HTTPException(status_code=401, detail="Invalid or missing intake key")
    lead, created = found
    if created:
        await enqueue_process_lead(lead.id)
    return _result(response, lead, created)


def _create_test(session: Session, current: Current, data: LeadIn) -> tuple[Lead, bool]:
    lead, created = create_lead(
        session, current.tenant.id, data, None, "test_form", actor=f"user:{current.user.id}"
    )
    session.commit()  # must be committed before the job is enqueued
    return lead, created


@router.post("/leads/test", status_code=201)
async def test_lead(
    data: LeadIn,
    response: Response,
    current: Annotated[Current, Depends(get_current)],
    session: Annotated[Session, Depends(get_db)],
) -> IntakeResult:
    lead, created = await run_in_threadpool(_create_test, session, current, data)
    if created:
        await enqueue_process_lead(lead.id)
    return _result(response, lead, created)
