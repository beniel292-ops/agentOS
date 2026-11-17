import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.jobs import queue
from app.main import app
from app.models import (
    AuditEvent,
    Draft,
    DraftDecision,
    Lead,
    LeadAnalysis,
    LeadStatus,
    Tenant,
)
from tests.auth_helpers import token

pytestmark = pytest.mark.usefixtures("clerk")
client = TestClient(app)


def call(method, path, tok=None, **kw):
    h = {"Authorization": f"Bearer {tok}"} if tok else {}
    return client.request(method, path, headers=h, **kw)


@pytest.fixture
def send(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(queue, "enqueue_send_reply", mock)
    from app.routes import leads
    monkeypatch.setattr(leads, "enqueue_send_reply", mock)
    return mock


@pytest.fixture
def tid(session):
    call("GET", "/v1/me", token())  # provisions org_1
    return session.scalars(select(Tenant).where(Tenant.clerk_org_id == "org_1")).one().id


@pytest.fixture
def make(session, tid):
    def _make(status=LeadStatus.DRAFT_READY, tenant_id=None, decision=None, analysis=False):
        lead = Lead(
            tenant_id=tenant_id or tid, source="api", name="Ann", email="ann@x.com",
            message="hi", idempotency_key=str(uuid.uuid4()), status=status,
        )
        session.add(lead)
        session.flush()
        d = Draft(tenant_id=lead.tenant_id, lead_id=lead.id, subject="Hello",
                  body="Body text", decision=decision)
        session.add(d)
        if analysis:
            session.add(LeadAnalysis(
                tenant_id=lead.tenant_id, lead_id=lead.id, intent="buy", service="seo",
                budget="5k", urgency="high", fit_score=80, reasoning="good", model="m",
                raw_json={},
            ))
        session.flush()
        return lead, d

    return _make


def audits(session, lead):
    return session.scalars(
        select(AuditEvent).where(AuditEvent.lead_id == lead.id).order_by(AuditEvent.id)
    ).all()


def test_status_filter(make):
    a, _ = make(LeadStatus.DRAFT_READY)
    make(LeadStatus.NEW)
    r = call("GET", "/v1/leads?status=DRAFT_READY", token())
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [str(a.id)]
    assert call("GET", "/v1/leads?status=bogus", token()).status_code == 422


def test_detail(session, make):
    lead, d = make(analysis=True)
    session.add(AuditEvent(tenant_id=lead.tenant_id, lead_id=lead.id, actor="worker",
                           action="status_change", from_state="NEW", to_state="PROCESSING"))
    session.add(AuditEvent(tenant_id=lead.tenant_id, lead_id=lead.id, actor="worker",
                           action="status_change", from_state="PROCESSING",
                           to_state="DRAFT_READY"))
    session.flush()
    j = call("GET", f"/v1/leads/{lead.id}", token()).json()
    assert j["status"] == "DRAFT_READY" and j["fit_score"] == 80
    assert j["analysis"] == {"intent": "buy", "service": "seo", "budget": "5k",
                             "urgency": "high", "fit_score": 80, "reasoning": "good"}
    assert j["draft"]["id"] == str(d.id) and j["draft"]["decision"] is None
    assert j["draft"]["sent"] is False and j["draft"]["body"] == "Body text"
    assert [(e["from_state"], e["to_state"]) for e in j["timeline"]] == [
        ("NEW", "PROCESSING"), ("PROCESSING", "DRAFT_READY")]
    assert set(j["timeline"][0]) == {"id", "at", "actor", "action", "from_state", "to_state"}


def test_detail_without_analysis_or_draft(session, tid):
    lead = Lead(tenant_id=tid, source="api", name="A", email="a@x.com", message="m",
                idempotency_key="k1")
    session.add(lead)
    session.flush()
    j = call("GET", f"/v1/leads/{lead.id}", token()).json()
    assert j["analysis"] is None and j["draft"] is None and j["timeline"] == []


def test_approve_plain(session, make, send):
    lead, d = make()
    r = call("POST", f"/v1/drafts/{d.id}/approve", token())
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "APPROVED" and j["draft"]["decision"] == "approved"
    assert j["draft"]["edited_body"] is None and j["draft"]["decided_at"]
    ev = audits(session, lead)[-1]
    assert ev.to_state == "APPROVED" and ev.actor.startswith("user:")
    assert ev.data == {"draft_id": str(d.id), "edited": False}
    assert j["timeline"][-1]["actor"] == ev.actor
    send.assert_awaited_once_with(lead.id)


def test_approve_with_edit(session, make, send):
    lead, d = make()
    r = call("POST", f"/v1/drafts/{d.id}/approve", token(), json={"edited_body": "  New text "})
    assert r.status_code == 200
    assert r.json()["draft"]["edited_body"] == "New text"
    session.refresh(d)
    assert d.edited_body == "New text" and d.decided_by is not None
    ev = audits(session, lead)[-1]
    assert ev.data == {"draft_id": str(d.id), "edited": True}
    assert "New text" not in str(ev.data)
    send.assert_awaited_once()


def test_approve_edit_same_as_body_not_stored(session, make, send):
    _, d = make()
    call("POST", f"/v1/drafts/{d.id}/approve", token(), json={"edited_body": "Body text"})
    session.refresh(d)
    assert d.edited_body is None


@pytest.mark.parametrize("body", ["", "   ", "x" * 10001])
def test_approve_bad_edit_422(make, send, body):
    _, d = make()
    assert call("POST", f"/v1/drafts/{d.id}/approve", token(),
                json={"edited_body": body}).status_code == 422
    send.assert_not_awaited()


def test_approve_never_sends_itself(session, make, send, monkeypatch):
    from app import email
    sent = []
    monkeypatch.setattr(email, "send_email", lambda *a, **k: sent.append(1), raising=False)
    lead, d = make()
    call("POST", f"/v1/drafts/{d.id}/approve", token())
    assert sent == []
    session.refresh(d)
    assert d.provider_message_id is None
    send.assert_awaited_once()  # only the enqueue; the worker sends


def test_reject(session, make, send):
    lead, d = make()
    r = call("POST", f"/v1/drafts/{d.id}/reject", token(), json={"reason": " Not a fit "})
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "REJECTED" and j["draft"]["decision"] == "rejected"
    assert j["draft"]["reject_reason"] == "Not a fit"
    ev = audits(session, lead)[-1]
    assert ev.to_state == "REJECTED" and ev.data == {"draft_id": str(d.id)}
    assert "Not a fit" not in str(ev.data)
    send.assert_not_awaited()


@pytest.mark.parametrize("payload", [{"reason": ""}, {"reason": "  "}, {"reason": "x" * 501}, {}])
def test_reject_needs_reason(make, payload):
    _, d = make()
    assert call("POST", f"/v1/drafts/{d.id}/reject", token(), json=payload).status_code == 422


def test_second_approve_409(make, send):
    _, d = make()
    assert call("POST", f"/v1/drafts/{d.id}/approve", token()).status_code == 200
    assert call("POST", f"/v1/drafts/{d.id}/approve", token()).status_code == 409
    send.assert_awaited_once()


def test_approve_after_reject_409(make, send):
    _, d = make()
    r = call("POST", f"/v1/drafts/{d.id}/reject", token(), json={"reason": "no"})
    assert r.status_code == 200
    assert call("POST", f"/v1/drafts/{d.id}/approve", token()).status_code == 409
    send.assert_not_awaited()


def test_reject_after_approve_409(make, send):
    _, d = make()
    call("POST", f"/v1/drafts/{d.id}/approve", token())
    r = call("POST", f"/v1/drafts/{d.id}/reject", token(), json={"reason": "no"})
    assert r.status_code == 409


def test_low_fit_409(make, send):
    _, d = make(LeadStatus.LOW_FIT)
    assert call("POST", f"/v1/drafts/{d.id}/approve", token()).status_code == 409
    send.assert_not_awaited()


def test_other_tenant_404(session, make, send):
    other = Tenant(clerk_org_id="org_other", name="Other")
    session.add(other)
    session.flush()
    lead, d = make(tenant_id=other.id)
    assert call("POST", f"/v1/drafts/{d.id}/approve", token()).status_code == 404
    r = call("POST", f"/v1/drafts/{d.id}/reject", token(), json={"reason": "x"})
    assert r.status_code == 404
    assert call("GET", f"/v1/leads/{lead.id}", token()).status_code == 404
    assert call("GET", "/v1/leads?status=DRAFT_READY", token()).json() == []
    session.refresh(lead)
    assert lead.status == LeadStatus.DRAFT_READY and d.decision is None
    send.assert_not_awaited()


def test_unknown_draft_404(make, send):
    assert call("POST", f"/v1/drafts/{uuid.uuid4()}/approve", token()).status_code == 404


def test_requires_auth(make, send):
    _, d = make()
    assert call("POST", f"/v1/drafts/{d.id}/approve").status_code == 401
    assert call("POST", f"/v1/drafts/{d.id}/reject", json={"reason": "x"}).status_code == 401
    assert call("GET", "/v1/leads?status=DRAFT_READY").status_code == 401


def test_member_can_approve(session, make, send):
    lead, d = make()
    r = call("POST", f"/v1/drafts/{d.id}/approve", token(role="member"))
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    assert d.decision == DraftDecision.APPROVED
