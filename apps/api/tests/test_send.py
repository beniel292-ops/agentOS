import uuid
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from arq import Retry
from sqlalchemy import select

from app import email
from app.config import settings
from app.jobs import worker
from app.models import AuditEvent, Draft, DraftDecision, Lead, LeadStatus, Tenant
from app.telegram import notify
from app.workflow import send

KEY = "re_SECRET_KEY"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", KEY)
    monkeypatch.setattr(settings, "resend_from", "AgentOS <hi@example.com>")


@pytest.fixture
def factory(session):
    return lambda: nullcontext(session)


@pytest.fixture
def mail(monkeypatch):
    m = MagicMock(return_value="msg_1")
    monkeypatch.setattr(email, "send_email", m)
    return m


@pytest.fixture
def make(session):
    tenant = Tenant(clerk_org_id=f"org_{uuid.uuid4().hex}", name="Acme")
    session.add(tenant)
    session.flush()

    def _make(status=LeadStatus.APPROVED, decision=DraftDecision.APPROVED, draft=True,
              tenant_id=None, draft_tenant_id=None, **dkw):
        lead = Lead(
            tenant_id=tenant_id or tenant.id, source="api", name="Ann", email="ann@x.com",
            message="hi", idempotency_key=str(uuid.uuid4()), status=status,
        )
        session.add(lead)
        session.flush()
        d = None
        if draft:
            d = Draft(tenant_id=draft_tenant_id or lead.tenant_id, lead_id=lead.id,
                      subject="Hello", body="Body text", decision=decision, **dkw)
            session.add(d)
            session.flush()
        return lead, d

    return _make


def audits(session, lead):
    return list(session.scalars(select(AuditEvent).where(
        AuditEvent.lead_id == lead.id, AuditEvent.action == "status_change")))


# --- the send guard ---

@pytest.mark.parametrize("status,decision", [
    (LeadStatus.DRAFT_READY, None),
    (LeadStatus.REJECTED, DraftDecision.REJECTED),
    (LeadStatus.SENT, DraftDecision.APPROVED),
    (LeadStatus.LOW_FIT, None),
    (LeadStatus.APPROVED, None),
    (LeadStatus.APPROVED, DraftDecision.REJECTED),
])
def test_never_sends_unless_approved(session, factory, mail, make, status, decision):
    lead, _ = make(status=status, decision=decision)
    assert send.run(factory, str(lead.id), 1) is None
    mail.assert_not_called()
    session.refresh(lead)
    assert lead.status == status


def test_no_draft_sends_nothing(session, factory, mail, make):
    lead, _ = make(draft=False)
    assert send.run(factory, str(lead.id), 1) is None
    mail.assert_not_called()


def test_not_configured_sends_nothing(session, factory, mail, make, monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "")
    lead, _ = make()
    assert send.run(factory, str(lead.id), 1) is None
    mail.assert_not_called()
    session.refresh(lead)
    assert lead.status == LeadStatus.APPROVED and lead.last_error is None


def test_mismatched_tenant_draft_is_not_sent(session, factory, mail, make):
    other = Tenant(clerk_org_id=f"org_{uuid.uuid4().hex}", name="Other")
    session.add(other)
    session.flush()
    lead, _ = make(draft_tenant_id=other.id)  # draft belongs to another tenant than the lead
    assert send.run(factory, str(lead.id), 1) is None
    mail.assert_not_called()
    session.refresh(lead)
    assert lead.status == LeadStatus.APPROVED


# --- happy path ---

def test_happy_path(session, factory, mail, make):
    lead, d = make()
    assert send.run(factory, str(lead.id), 1) == "lead_sent"
    mail.assert_called_once_with("ann@x.com", "Hello", "Body text", d.send_idempotency_key)
    session.refresh(lead)
    session.refresh(d)
    assert lead.status == LeadStatus.SENT and d.provider_message_id == "msg_1"
    ev = audits(session, lead)[-1]
    assert (ev.from_state, ev.to_state, ev.actor) == (
        LeadStatus.APPROVED, LeadStatus.SENT, "worker")
    assert ev.data == {"provider_message_id": "msg_1"}


def test_edited_body_and_default_subject(session, factory, mail, make):
    lead, d = make(edited_body="Edited")
    d.subject = ""
    session.flush()
    send.run(factory, str(lead.id), 1)
    assert mail.call_args.args[1:3] == ("Re: your enquiry", "Edited")


def test_crash_after_send_skips_http(session, factory, mail, make):
    lead, _ = make(provider_message_id="msg_old")
    assert send.run(factory, str(lead.id), 1) == "lead_sent"
    mail.assert_not_called()
    session.refresh(lead)
    assert lead.status == LeadStatus.SENT


def test_running_twice_sends_once(session, factory, mail, make):
    lead, _ = make()
    assert send.run(factory, str(lead.id), 1) == "lead_sent"
    assert send.run(factory, str(lead.id), 2) is None
    assert mail.call_count == 1
    assert len(audits(session, lead)) == 1


# --- errors ---

def test_temporary_error_retries_then_fails(session, factory, mail, make):
    mail.side_effect = email.SendError("HTTP 503", permanent=False)
    lead, _ = make()
    with pytest.raises(Retry) as r:
        send.run(factory, str(lead.id), 1)
    assert r.value.defer_score == 30_000
    session.refresh(lead)
    assert lead.last_error is None
    assert send.run(factory, str(lead.id), send.MAX_TRIES) == "send_failed"
    session.refresh(lead)
    assert lead.status == LeadStatus.APPROVED and lead.last_error == "send: HTTP 503"


def test_permanent_error_no_retry(session, factory, mail, make):
    mail.side_effect = email.SendError("HTTP 422 validation_error", permanent=True)
    lead, _ = make()
    assert send.run(factory, str(lead.id), 1) == "send_failed"
    session.refresh(lead)
    assert lead.status == LeadStatus.APPROVED
    assert lead.last_error == "send: HTTP 422 validation_error"


# --- email.send_email ---

def fake_post(monkeypatch, response=None, exc=None):
    seen = {}

    def post(url, **kw):
        seen.update(url=url, **kw)
        if exc:
            raise exc
        return response

    monkeypatch.setattr(httpx, "post", post)
    return seen


def test_send_email_request(monkeypatch):
    seen = fake_post(monkeypatch, httpx.Response(200, json={"id": "abc"}))
    assert email.send_email("a@x.com", "S", "T", "idem-1") == "abc"
    assert seen["url"] == "https://api.resend.com/emails"
    assert seen["headers"] == {"Authorization": f"Bearer {KEY}", "Idempotency-Key": "idem-1"}
    assert seen["json"] == {
        "from": "AgentOS <hi@example.com>", "to": ["a@x.com"], "subject": "S", "text": "T"}
    assert seen["timeout"]


@pytest.mark.parametrize("code,permanent", [
    (429, False), (500, False), (503, False), (400, True), (422, True), (401, True)])
def test_send_email_status_classes(monkeypatch, code, permanent):
    body = {"name": "validation_error", "message": f"bad {KEY} secret body"}
    fake_post(monkeypatch, httpx.Response(code, json=body))
    with pytest.raises(email.SendError) as e:
        email.send_email("a@x.com", "S", "SECRET BODY", "k")
    assert e.value.permanent is permanent
    assert str(code) in str(e.value)


def test_send_email_error_has_no_body(monkeypatch):
    fake_post(monkeypatch, httpx.Response(400, json={"name": "validation_error"}))
    with pytest.raises(email.SendError) as e:
        email.send_email("a@x.com", "S", "SECRET BODY", "k")
    assert KEY not in str(e.value) and "SECRET BODY" not in str(e.value)


def test_send_email_network_error_is_temporary(monkeypatch):
    fake_post(monkeypatch, exc=httpx.ConnectTimeout(f"timeout {KEY}"))
    with pytest.raises(email.SendError) as e:
        email.send_email("a@x.com", "S", "T", "k")
    assert e.value.permanent is False and KEY not in str(e.value)


# --- worker job, sweep, notifications ---

async def test_send_reply_job_notifies(session, mail, make, monkeypatch, enqueue_notify):
    monkeypatch.setattr(worker, "SessionLocal", lambda: nullcontext(session))
    lead, _ = make()
    await worker.send_reply({"job_try": 1}, str(lead.id))
    enqueue_notify.assert_awaited_once_with("lead_sent", str(lead.id))


async def test_sweep_enqueues_approved_only(session, make, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: nullcontext(session))
    ok, _ = make()
    retry, _ = make()
    retry.last_error = "something else"
    failed, _ = make()
    failed.last_error = "send: HTTP 422"
    make(status=LeadStatus.DRAFT_READY, decision=None)
    make(status=LeadStatus.SENT)
    session.flush()
    redis = MagicMock(enqueue_job=AsyncMock())
    await worker.requeue_approved_leads({"redis": redis})
    ids = {c.args[1] for c in redis.enqueue_job.await_args_list}
    assert ids == {str(ok.id), str(retry.id)}
    assert all(c.args[0] == "send_reply" for c in redis.enqueue_job.await_args_list)


async def test_sweep_off_when_not_configured(session, make, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(settings, "resend_from", "")
    make()
    redis = MagicMock(enqueue_job=AsyncMock())
    await worker.requeue_approved_leads({"redis": redis})
    redis.enqueue_job.assert_not_awaited()


def test_worker_registers_send_reply():
    assert worker.send_reply in worker.WorkerSettings.functions
    assert any(c.coroutine is worker.requeue_approved_leads
               for c in worker.WorkerSettings.cron_jobs)


def test_notify_kinds(session, make, monkeypatch):
    tenant_id = uuid.uuid4()
    sent, _ = make(status=LeadStatus.SENT)
    failed, _ = make()
    failed.last_error = "send: HTTP 422 validation_error"
    session.flush()
    a = notify.build_lead_sent(session, sent.tenant_id, str(sent.id))
    b = notify.build_send_failed(session, failed.tenant_id, str(failed.id))
    assert a == ("✉️ Sent to Ann <ann@x.com>", None)
    assert b[0] == (
        "⚠️ Couldn't send to Ann <ann@x.com>: send: HTTP 422 validation_error\n"
        "The lead stays approved.")
    # wrong status or wrong tenant renders nothing
    assert notify.build_lead_sent(session, sent.tenant_id, str(failed.id)) is None
    assert notify.build_send_failed(session, tenant_id, str(failed.id)) is None
    assert {"lead_sent", "send_failed"} <= set(notify.BUILDERS)
