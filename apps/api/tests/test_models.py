import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import AuditEvent, Draft, Lead, LeadAnalysis, Tenant


def tenant(session):
    t = Tenant(name="T")
    session.add(t)
    session.flush()
    return t


def lead(session, t, key="k"):
    obj = Lead(
        tenant_id=t.id, source="api", name="A", email="a@x.com", message="m", idempotency_key=key
    )
    session.add(obj)
    session.flush()
    return obj


def test_idempotency_unique_per_tenant(session):
    t1, t2 = tenant(session), tenant(session)
    lead(session, t1)
    lead(session, t2)  # same key, other tenant: fine
    with pytest.raises(IntegrityError), session.begin_nested():
        lead(session, t1)


def test_fit_score_check(session):
    t = tenant(session)
    l1 = lead(session, t)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(LeadAnalysis(
            tenant_id=t.id, lead_id=l1.id, intent="x", fit_score=101, model="m", raw_json={}
        ))
        session.flush()


def test_audit_events_append_only(session):
    t = tenant(session)
    ev = AuditEvent(tenant_id=t.id, actor="system", action="x")
    session.add(ev)
    session.flush()
    for sql in ("UPDATE audit_events SET actor = 'y'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
            session.execute(text(sql))


def test_one_draft_per_lead(session):
    t = tenant(session)
    l1 = lead(session, t)
    session.add(Draft(tenant_id=t.id, lead_id=l1.id, body="a"))
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Draft(tenant_id=t.id, lead_id=l1.id, body="b"))
        session.flush()


def test_defaults(session):
    t = tenant(session)
    l1 = lead(session, t)
    session.refresh(t)
    assert t.settings == {}
    assert l1.status == "NEW" and l1.attempts == 0
    assert uuid.UUID(str(t.id))
