import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.auth import Current, get_current
from app.db import get_db, get_session_factory
from app.jobs.queue import enqueue_process_lead
from app.models import Lead, LeadAnalysis, LeadStatus
from app.workflow.transitions import ConcurrentTransition, transition

router = APIRouter(prefix="/v1")


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    company: str | None
    message: str
    source: str
    status: LeadStatus
    fit_score: int | None = None
    last_error: str | None = None
    created_at: datetime


def _out(lead: Lead, fit_score: int | None) -> LeadOut:
    out = LeadOut.model_validate(lead)
    out.fit_score = fit_score
    return out


@router.get("/leads")
def list_leads(
    current: Annotated[Current, Depends(get_current)],
    session: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[LeadOut]:
    rows = session.execute(
        select(Lead, LeadAnalysis.fit_score)
        .outerjoin(LeadAnalysis, LeadAnalysis.lead_id == Lead.id)
        .where(Lead.tenant_id == current.tenant.id)
        .order_by(Lead.created_at.desc(), Lead.id.desc())
        .limit(limit)
    )
    return [_out(lead, score) for lead, score in rows]


def _get_lead_row(session: Session, tenant_id: uuid.UUID, lead_id: uuid.UUID):
    row = session.execute(
        select(Lead, LeadAnalysis.fit_score)
        .outerjoin(LeadAnalysis, LeadAnalysis.lead_id == Lead.id)
        .where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return row


@router.get("/leads/{lead_id}")
def get_lead(
    lead_id: uuid.UUID,
    current: Annotated[Current, Depends(get_current)],
    session: Annotated[Session, Depends(get_db)],
) -> LeadOut:
    return _out(*_get_lead_row(session, current.tenant.id, lead_id))


def _restart(
    factory: sessionmaker[Session], current: Current, lead_id: uuid.UUID, from_state: LeadStatus
) -> LeadOut:
    with factory() as session:
        lead, fit_score = _get_lead_row(session, current.tenant.id, lead_id)
        if lead.status != from_state:
            raise HTTPException(status_code=409, detail=f"Lead is {lead.status}, not {from_state}")
        try:
            transition(
                session, lead, LeadStatus.PROCESSING, f"user:{current.user.id}",
                {"from": from_state},
            )
        except ConcurrentTransition:
            raise HTTPException(status_code=409, detail="Lead changed; refresh and retry") from None
        session.commit()  # must be committed before the job is enqueued
        return _out(lead, fit_score)


@router.post("/leads/{lead_id}/retry")
async def retry_lead(
    lead_id: uuid.UUID,
    current: Annotated[Current, Depends(get_current)],
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> LeadOut:
    out = await run_in_threadpool(_restart, factory, current, lead_id, LeadStatus.FAILED)
    await enqueue_process_lead(lead_id)
    return out


@router.post("/leads/{lead_id}/draft-anyway")
async def draft_anyway(
    lead_id: uuid.UUID,
    current: Annotated[Current, Depends(get_current)],
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> LeadOut:
    out = await run_in_threadpool(_restart, factory, current, lead_id, LeadStatus.LOW_FIT)
    await enqueue_process_lead(lead_id, force=True)
    return out
