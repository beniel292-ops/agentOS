import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.auth import Current, get_current
from app.db import get_db, get_session_factory
from app.jobs.queue import enqueue_process_lead, enqueue_send_reply
from app.models import AuditEvent, Draft, DraftDecision, Lead, LeadAnalysis, LeadStatus
from app.workflow.decisions import decide
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
    status: LeadStatus | None = None,
) -> list[LeadOut]:
    q = (
        select(Lead, LeadAnalysis.fit_score)
        .outerjoin(LeadAnalysis, LeadAnalysis.lead_id == Lead.id)
        .where(Lead.tenant_id == current.tenant.id)
    )
    if status is not None:
        q = q.where(Lead.status == status)
    rows = session.execute(
        q
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


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    intent: str
    service: str | None
    budget: str | None
    urgency: str | None
    fit_score: int
    reasoning: str | None


class DraftOut(BaseModel):
    id: uuid.UUID
    subject: str
    body: str
    edited_body: str | None
    decision: DraftDecision | None
    decided_at: datetime | None
    reject_reason: str | None
    sent: bool


class TimelineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    actor: str
    action: str
    from_state: str | None
    to_state: str | None


class LeadDetail(LeadOut):
    analysis: AnalysisOut | None
    draft: DraftOut | None
    timeline: list[TimelineOut]


def _detail(session: Session, lead: Lead, fit_score: int | None) -> LeadDetail:
    tenant_id = lead.tenant_id
    analysis = session.scalars(
        select(LeadAnalysis).where(LeadAnalysis.lead_id == lead.id,
                                   LeadAnalysis.tenant_id == tenant_id)
    ).first()
    draft = session.scalars(
        select(Draft).where(Draft.lead_id == lead.id, Draft.tenant_id == tenant_id)
    ).first()
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id, AuditEvent.lead_id == lead.id)
        .order_by(AuditEvent.at, AuditEvent.id)
    )
    return LeadDetail(
        **_out(lead, fit_score).model_dump(),
        analysis=AnalysisOut.model_validate(analysis) if analysis else None,
        draft=DraftOut(
            id=draft.id, subject=draft.subject, body=draft.body, edited_body=draft.edited_body,
            decision=draft.decision, decided_at=draft.decided_at,
            reject_reason=draft.reject_reason, sent=draft.provider_message_id is not None,
        ) if draft else None,
        timeline=[TimelineOut.model_validate(e) for e in events],
    )


@router.get("/leads/{lead_id}")
def get_lead(
    lead_id: uuid.UUID,
    current: Annotated[Current, Depends(get_current)],
    session: Annotated[Session, Depends(get_db)],
) -> LeadDetail:
    return _detail(session, *_get_lead_row(session, current.tenant.id, lead_id))


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


Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ApproveIn(BaseModel):
    edited_body: Body | None = None


class RejectIn(BaseModel):
    reason: Reason


def _decide(
    factory: sessionmaker[Session], current: Current, draft_id: uuid.UUID, approve: bool,
    reason: str | None = None, edited_body: str | None = None,
) -> tuple[LeadDetail, uuid.UUID]:
    with factory() as session:
        outcome, lead = decide(
            session, current.tenant.id, draft_id=draft_id, approve=approve,
            actor=f"user:{current.user.id}", decided_by=current.user.id, reason=reason,
            edited_body=edited_body, data={},
        )
        if outcome == "missing":
            raise HTTPException(status_code=404, detail="Draft not found")
        if outcome == "handled":
            raise HTTPException(status_code=409, detail="Already decided or lead changed")
        session.commit()  # must be committed before the send job is enqueued
        _, fit_score = _get_lead_row(session, current.tenant.id, lead.id)
        return _detail(session, lead, fit_score), lead.id


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(
    draft_id: uuid.UUID,
    current: Annotated[Current, Depends(get_current)],
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    body: ApproveIn | None = None,
) -> LeadDetail:
    edited = body.edited_body if body else None
    out, lead_id = await run_in_threadpool(_decide, factory, current, draft_id, True, None, edited)
    await enqueue_send_reply(lead_id)  # the worker's per-minute sweep is the fallback
    return out


@router.post("/drafts/{draft_id}/reject")
async def reject_draft(
    draft_id: uuid.UUID,
    body: RejectIn,
    current: Annotated[Current, Depends(get_current)],
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> LeadDetail:
    out, _ = await run_in_threadpool(_decide, factory, current, draft_id, False, body.reason)
    return out
