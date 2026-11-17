import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Draft, DraftDecision, Lead, LeadStatus
from app.workflow.transitions import ConcurrentTransition, InvalidTransition, transition


def decide(
    session: Session, tenant_id: uuid.UUID, *, lead_id: uuid.UUID | None = None,
    draft_id: uuid.UUID | None = None, approve: bool, actor: str, decided_by: uuid.UUID | None,
    reason: str | None = None, edited_body: str | None = None, data: dict | None = None,
) -> tuple[str, Lead | None]:
    """Approve or reject a DRAFT_READY lead's draft. Caller commits on "done".

    Returns (outcome, lead): missing | handled | done. Locks lead and draft (tenant-filtered).
    """
    if lead_id is None:
        lead_id = session.scalar(
            select(Draft.lead_id).where(Draft.id == draft_id, Draft.tenant_id == tenant_id)
        )
        if lead_id is None:
            return "missing", None
    lead = session.scalars(
        select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id).with_for_update()
    ).first()
    if lead is None:
        return "missing", None
    draft = session.scalars(
        select(Draft).where(Draft.lead_id == lead.id, Draft.tenant_id == tenant_id)
        .with_for_update()
    ).first()
    if draft is None or (draft_id is not None and draft.id != draft_id):
        return "missing", None
    if lead.status != LeadStatus.DRAFT_READY or draft.decision is not None:
        return "handled", lead
    if approve and edited_body is not None and edited_body != draft.body:
        draft.edited_body = edited_body
    draft.decision = DraftDecision.APPROVED if approve else DraftDecision.REJECTED
    draft.decided_at, draft.decided_by = datetime.now(UTC), decided_by
    if not approve:
        draft.reject_reason = reason
    event_data = None
    if data is not None:
        event_data = {"draft_id": str(draft.id)} | data
        if approve:
            event_data["edited"] = draft.edited_body is not None
    try:
        transition(
            session, lead, LeadStatus.APPROVED if approve else LeadStatus.REJECTED, actor,
            event_data,
        )
    except (InvalidTransition, ConcurrentTransition):
        session.rollback()
        return "handled", None
    return "done", lead
