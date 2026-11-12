import uuid

import pytest
from sqlalchemy import func, select, text

from app.models import AuditEvent, Lead, LeadStatus, Tenant
from app.workflow.transitions import (
    ALLOWED,
    ConcurrentTransition,
    InvalidTransition,
    transition,
)


@pytest.fixture
def make_lead(session):
    def _make(status=LeadStatus.NEW):
        tenant = Tenant(clerk_org_id=f"org_{uuid.uuid4().hex}", name="Acme")
        session.add(tenant)
        session.flush()
        lead = Lead(
            tenant_id=tenant.id, source="api", name="A", email="a@x.com", message="hi",
            idempotency_key=str(uuid.uuid4()), status=status,
        )
        session.add(lead)
        session.flush()
        return lead

    return _make


def audit_count(session, lead):
    return session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.lead_id == lead.id)
    )


CASES = [(a, b) for a, tos in ALLOWED.items() for b in tos]


@pytest.mark.parametrize(("src", "dst"), CASES)
def test_allowed_transition(session, make_lead, src, dst):
    lead = make_lead(src)
    event = transition(session, lead, dst, "system", {"k": "v"})
    assert lead.status == dst
    assert (event.from_state, event.to_state, event.action) == (src, dst, "status_change")
    assert event.data == {"k": "v"}
    assert audit_count(session, lead) == 1
    session.refresh(lead)
    assert lead.status == dst


@pytest.mark.parametrize(
    ("src", "dst"),
    [(LeadStatus.NEW, LeadStatus.SENT)] + [(LeadStatus.SENT, s) for s in LeadStatus]
    + [(LeadStatus.REJECTED, LeadStatus.NEW)],
)
def test_invalid_transition(session, make_lead, src, dst):
    lead = make_lead(src)
    with pytest.raises(InvalidTransition):
        transition(session, lead, dst, "system")
    assert lead.status == src
    assert audit_count(session, lead) == 0


def test_concurrent_transition(session, make_lead):
    lead = make_lead(LeadStatus.NEW)
    session.execute(
        text("UPDATE leads SET status = 'PROCESSING' WHERE id = :id"), {"id": lead.id}
    )
    with pytest.raises(ConcurrentTransition):
        transition(session, lead, LeadStatus.PROCESSING, "worker")
    assert audit_count(session, lead) == 0


def test_rollback_removes_status_and_audit(engine):
    from sqlalchemy.orm import Session

    with Session(engine) as s:
        tenant = Tenant(clerk_org_id=f"org_{uuid.uuid4().hex}", name="Acme")
        s.add(tenant)
        s.flush()
        lead = Lead(
            tenant_id=tenant.id, source="api", name="A", email="a@x.com", message="hi",
            idempotency_key="k",
        )
        s.add(lead)
        s.commit()
        try:
            transition(s, lead, LeadStatus.PROCESSING, "worker")
            s.rollback()
            s.refresh(lead)
            assert lead.status == LeadStatus.NEW
            assert audit_count(s, lead) == 0
        finally:
            s.rollback()
            # audit rows are immutable; deleting the tenant cascades to the lead only
            s.delete(tenant)
            s.commit()
