"""MVP acceptance criteria, one clearly named test each, plus one end-to-end walk.

Real code path: HTTP routes + app.workflow.process.run / send.run on the test database.
Only the AI service functions, app.email.send_email and the job-queue enqueues are mocked.
"""
import uuid
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock

import pytest
from arq import Retry
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import email
from app.ai import AIError
from app.ai.schemas import LeadExtraction, ReplyDraft
from app.config import settings
from app.main import app
from app.models import AuditEvent, Draft, DraftDecision, Lead, LeadAnalysis, LeadStatus, Tenant
from app.workflow import process, send
from tests.auth_helpers import token

pytestmark = pytest.mark.usefixtures("clerk")
client = TestClient(app)

PAYLOAD = {"name": "Ann", "email": "ann@example.com", "company": "Acme", "message": "Need a site"}
S = LeadStatus


def auth(tok=None):
    return {"Authorization": f"Bearer {tok or token()}"}


def other_tenant_token():
    return token(o={"id": "org_2", "rol": "admin", "slg": "b"}, sub="user_b")


@pytest.fixture
def factory(session):
    return lambda: nullcontext(session)


@pytest.fixture(autouse=True)
def email_configured(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "resend_from", "AgentOS <hi@example.com>")


@pytest.fixture
def enqueue_process(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr("app.routes.intake.enqueue_process_lead", mock)
    return mock


@pytest.fixture
def enqueue_send(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr("app.routes.leads.enqueue_send_reply", mock)
    return mock


@pytest.fixture
def mail(monkeypatch):
    m = MagicMock(return_value="msg_1")
    monkeypatch.setattr(email, "send_email", m)
    return m


def extraction(score=70):
    return LeadExtraction(
        intent="wants a site", service="Web design", budget=None, urgency="high",
        fit_score=score, reasoning="because",
    )


@pytest.fixture
def ai(monkeypatch):
    extract = MagicMock(return_value=(extraction(), "m-x", {"raw": 1}))
    draft = MagicMock(return_value=(ReplyDraft(subject="Hi", body="Body"), "m-d", {}))
    monkeypatch.setattr("app.workflow.process.extract_lead", extract)
    monkeypatch.setattr("app.workflow.process.draft_reply", draft)
    return extract, draft


def intake_key(tok=None) -> str:
    r = client.post("/v1/intake-key", headers=auth(tok))
    assert r.status_code == 200
    return r.json()["key"]


def post_lead(key, body=PAYLOAD, **headers):
    return client.post("/v1/leads", json=body, headers={"X-Intake-Key": key} | headers)


def n(session, model, lead_id=None):
    q = select(func.count()).select_from(model)
    if lead_id is not None:
        q = q.where(model.lead_id == lead_id)
    return session.scalar(q)


def status_changes(session, lead_id):
    rows = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.lead_id == lead_id, AuditEvent.action == "status_change")
        .order_by(AuditEvent.id)
    )
    return [(e.from_state, e.to_state) for e in rows]


def lead_with_draft(session, status=S.DRAFT_READY, decision=None, org="org_1", **dkw):
    client.get("/v1/me", headers=auth())  # provision org_1
    client.get("/v1/me", headers=auth(other_tenant_token()))  # provision org_2
    tid = session.scalars(select(Tenant).where(Tenant.clerk_org_id == org)).one().id
    lead = Lead(tenant_id=tid, source="api", name="Ann", email="ann@x.com", message="hi",
                idempotency_key=str(uuid.uuid4()), status=status)
    session.add(lead)
    session.flush()
    d = Draft(tenant_id=tid, lead_id=lead.id, subject="Hello", body="Body text",
              decision=decision, **dkw)
    session.add(d)
    session.flush()
    return lead, d


# --- end to end ---

def test_end_to_end_happy_path(session, factory, ai, mail, enqueue_process, enqueue_send):
    # intake
    key = intake_key()
    r = post_lead(key)
    assert r.status_code == 201 and r.json()["status"] == "NEW"
    lead = session.get(Lead, uuid.UUID(r.json()["id"]))
    enqueue_process.assert_awaited_once_with(lead.id)

    # AI step
    assert process.run(factory, str(lead.id), 1) == "draft_ready"
    session.refresh(lead)
    assert lead.status == S.DRAFT_READY
    analysis = session.scalars(select(LeadAnalysis).where(LeadAnalysis.lead_id == lead.id)).one()
    draft = session.scalars(select(Draft).where(Draft.lead_id == lead.id)).one()
    assert analysis.fit_score == 70 and (draft.subject, draft.body) == ("Hi", "Body")
    assert draft.decision is None
    mail.assert_not_called()  # nothing leaves before approval

    # human approves through the web route
    r = client.post(f"/v1/drafts/{draft.id}/approve", headers=auth())
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    enqueue_send.assert_awaited_once_with(lead.id)
    mail.assert_not_called()

    # worker sends
    assert send.run(factory, str(lead.id), 1) == "lead_sent"
    session.refresh(lead)
    session.refresh(draft)
    assert lead.status == S.SENT and draft.provider_message_id == "msg_1"
    mail.assert_called_once_with("ann@example.com", "Hi", "Body", draft.send_idempotency_key)

    # audit trail
    assert status_changes(session, lead.id) == [
        ("NEW", "PROCESSING"), ("PROCESSING", "DRAFT_READY"),
        ("DRAFT_READY", "APPROVED"), ("APPROVED", "SENT"),
    ]
    assert n(session, Lead) == 1 and n(session, Draft, lead.id) == 1


# --- AC1 ---

def test_ac1_lead_stored_exactly_once(session, enqueue_process):
    key = intake_key()
    first = post_lead(key, **{"Idempotency-Key": "same"})
    second = post_lead(key, **{"Idempotency-Key": "same"})
    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["id"] == first.json()["id"] and second.json()["duplicate"] is True
    assert n(session, Lead) == 1
    assert enqueue_process.await_count == 1


def test_ac1_same_payload_without_key_stored_once(session, enqueue_process):
    key = intake_key()
    assert post_lead(key).status_code == 201
    assert post_lead(key).json()["duplicate"] is True
    assert n(session, Lead) == 1 and enqueue_process.await_count == 1


# --- AC2 ---

def test_ac2_ai_output_validated(session, factory, ai):
    extract, _ = ai
    lead, _d = lead_with_draft(session, status=S.NEW)
    session.delete(_d)
    session.flush()

    # valid output -> saved and DRAFT_READY
    assert process.run(factory, str(lead.id), 1) == "draft_ready"
    session.refresh(lead)
    assert lead.status == S.DRAFT_READY
    assert n(session, LeadAnalysis, lead.id) == 1 and n(session, Draft, lead.id) == 1

    # schema-invalid output (what the real service raises after its own validation fails)
    bad, _ = lead_with_draft(session, status=S.NEW)
    session.delete(session.scalars(select(Draft).where(Draft.lead_id == bad.id)).one())
    session.flush()
    extract.side_effect = AIError([("m1", "invalid output")])
    with pytest.raises(Retry):  # try 1 and 2 signal a retry
        process.run(factory, str(bad.id), 1)
    session.refresh(bad)
    assert bad.status == S.PROCESSING
    assert n(session, LeadAnalysis, bad.id) == 0 and n(session, Draft, bad.id) == 0
    assert process.run(factory, str(bad.id), 3) == "lead_failed"  # last try -> FAILED
    session.refresh(bad)
    assert bad.status == S.FAILED
    assert n(session, LeadAnalysis, bad.id) == 0 and n(session, Draft, bad.id) == 0


def test_ac2_invalid_schema_is_rejected_by_pydantic():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        LeadExtraction(intent="x", service="y", budget=None, urgency="high",
                       fit_score=999, reasoning="r")
    with pytest.raises(ValidationError):
        ReplyDraft(subject="Hi")  # body missing


# --- AC3 ---

def test_ac3_only_agency_users_can_decide(session, enqueue_send, mail):
    lead, d = lead_with_draft(session)
    other = auth(other_tenant_token())
    assert client.post(f"/v1/drafts/{d.id}/approve", headers=other).status_code == 404
    assert client.post(f"/v1/drafts/{d.id}/reject", headers=other,
                       json={"reason": "other"}).status_code == 404
    assert client.post(f"/v1/drafts/{d.id}/approve").status_code == 401
    assert client.post(f"/v1/drafts/{d.id}/reject", json={"reason": "x"}).status_code == 401
    session.refresh(lead)
    session.refresh(d)
    assert lead.status == S.DRAFT_READY and d.decision is None
    enqueue_send.assert_not_called()

    member = auth(token(role="member", sub="user_m"))
    r = client.post(f"/v1/drafts/{d.id}/approve", headers=member)
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    session.refresh(d)
    assert d.decision == DraftDecision.APPROVED and d.decided_by is not None
    enqueue_send.assert_awaited_once_with(lead.id)


# --- AC4 ---

@pytest.mark.parametrize("status,decision", [
    (S.DRAFT_READY, None),
    (S.REJECTED, DraftDecision.REJECTED),
    (S.LOW_FIT, None),
])
def test_ac4_nothing_sent_before_approval(session, factory, mail, status, decision):
    lead, _ = lead_with_draft(session, status=status, decision=decision)
    assert send.run(factory, str(lead.id), 1) is None
    mail.assert_not_called()
    session.refresh(lead)
    assert lead.status == status


def test_ac4_sent_exactly_once_after_approval(session, factory, mail, enqueue_send):
    lead, d = lead_with_draft(session)
    assert send.run(factory, str(lead.id), 1) is None
    mail.assert_not_called()
    assert client.post(f"/v1/drafts/{d.id}/approve", headers=auth()).status_code == 200
    mail.assert_not_called()  # approval alone does not send; only the worker does
    assert send.run(factory, str(lead.id), 1) == "lead_sent"
    mail.assert_called_once()
    session.refresh(lead)
    assert lead.status == S.SENT


# --- AC5 ---

def test_ac5_every_state_change_audited(session, factory, ai, mail, enqueue_process,
                                        enqueue_send):
    key = intake_key()
    lead = session.get(Lead, uuid.UUID(post_lead(key).json()["id"]))
    process.run(factory, str(lead.id), 1)
    draft = session.scalars(select(Draft).where(Draft.lead_id == lead.id)).one()
    client.post(f"/v1/drafts/{draft.id}/approve", headers=auth())
    send.run(factory, str(lead.id), 1)

    expected = [("NEW", "PROCESSING"), ("PROCESSING", "DRAFT_READY"),
                ("DRAFT_READY", "APPROVED"), ("APPROVED", "SENT")]
    assert status_changes(session, lead.id) == expected
    detail = client.get(f"/v1/leads/{lead.id}", headers=auth()).json()
    assert detail["status"] == "SENT"
    changes = [e for e in detail["timeline"] if e["action"] == "status_change"]
    assert [(e["from_state"], e["to_state"]) for e in changes] == expected
    assert [e["actor"] for e in changes][:2] == ["worker", "worker"]
    assert changes[2]["actor"].startswith("user:") and changes[3]["actor"] == "worker"
    # the lead_created intake event is on the timeline too, first
    assert detail["timeline"][0]["action"] == "lead_created"


# --- AC6 ---

def test_ac6_retries_never_duplicate(session, factory, ai, mail, enqueue_process, enqueue_send):
    extract, draft_fn = ai
    key = intake_key()
    r = post_lead(key)
    assert post_lead(key).json()["duplicate"] is True  # re-delivered intake
    assert n(session, Lead) == 1 and enqueue_process.await_count == 1
    lead = session.get(Lead, uuid.UUID(r.json()["id"]))

    # process twice (e.g. requeue sweep): one analysis, one draft, no second transition
    assert process.run(factory, str(lead.id), 1) == "draft_ready"
    assert process.run(factory, str(lead.id), 2) is None
    assert n(session, Draft, lead.id) == 1 and n(session, LeadAnalysis, lead.id) == 1
    assert extract.call_count == 1 and draft_fn.call_count == 1

    draft = session.scalars(select(Draft).where(Draft.lead_id == lead.id)).one()
    client.post(f"/v1/drafts/{draft.id}/approve", headers=auth())

    # simulated crash after the provider accepted: id stored, lead still APPROVED
    draft.provider_message_id = "msg_crashed"
    session.flush()
    assert send.run(factory, str(lead.id), 2) == "lead_sent"
    mail.assert_not_called()  # resumed from provider_message_id, no second email
    session.refresh(lead)
    assert lead.status == S.SENT

    assert send.run(factory, str(lead.id), 3) is None  # already SENT
    mail.assert_not_called()


def test_ac6_send_twice_sends_one_email(session, factory, mail, enqueue_send):
    lead, d = lead_with_draft(session)
    client.post(f"/v1/drafts/{d.id}/approve", headers=auth())
    assert send.run(factory, str(lead.id), 1) == "lead_sent"
    assert send.run(factory, str(lead.id), 1) is None
    assert mail.call_count == 1
    assert n(session, Draft, lead.id) == 1
    assert status_changes(session, lead.id).count(("APPROVED", "SENT")) == 1
