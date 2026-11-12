from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.models import AuditEvent, Lead, LeadStatus

S = LeadStatus
ALLOWED: dict[LeadStatus, frozenset[LeadStatus]] = {
    S.NEW: frozenset({S.PROCESSING}),
    S.PROCESSING: frozenset({S.DRAFT_READY, S.FAILED}),
    S.FAILED: frozenset({S.PROCESSING}),
    S.DRAFT_READY: frozenset({S.APPROVED, S.REJECTED}),
    S.APPROVED: frozenset({S.SENT}),
    S.REJECTED: frozenset(),
    S.SENT: frozenset(),
}


class InvalidTransition(Exception):
    pass


class ConcurrentTransition(Exception):
    pass


def transition(
    session: Session, lead: Lead, to_state: LeadStatus, actor: str, data: dict | None = None
) -> AuditEvent:
    """Move a lead to a new status and write the audit row. Caller commits."""
    from_state = lead.status
    if to_state not in ALLOWED[from_state]:
        raise InvalidTransition(f"{from_state} -> {to_state}")
    result = session.execute(
        update(Lead)
        .where(Lead.id == lead.id, Lead.tenant_id == lead.tenant_id, Lead.status == from_state)
        .values(status=to_state, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ConcurrentTransition(f"lead {lead.id} is no longer {from_state}")
    event = AuditEvent(
        tenant_id=lead.tenant_id,
        lead_id=lead.id,
        actor=actor,
        action="status_change",
        from_state=from_state,
        to_state=to_state,
        data=data or {},
    )
    session.add(event)
    session.flush()
    lead.status = to_state
    return event
