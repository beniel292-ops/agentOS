import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from arq import Retry
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.ai import AIError
from app.ai.schemas import LeadExtraction, ReplyDraft
from app.jobs import worker
from app.main import app
from app.models import AuditEvent, Draft, DraftDecision, Lead, LeadAnalysis, LeadStatus, Tenant
from tests.auth_helpers import token

client = TestClient(app)
auth = {"Authorization": f"Bearer {token()}"}  # tenant org_1


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


@pytest.fixture
def factory(session, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: nullcontext(session))


@pytest.fixture
def make_lead(session):
    tenant = Tenant(clerk_org_id=f"org_{uuid.uuid4().hex}", name="Acme")
    session.add(tenant)
    session.flush()

    def _make(status=LeadStatus.NEW, tenant_id=None, **kw):
        lead = Lead(
            tenant_id=tenant_id or tenant.id, source="api", name="A", email="a@x.com",
            message="hi", idempotency_key=str(uuid.uuid4()), status=status, **kw,
        )
        session.add(lead)
        session.flush()
        return lead

    return _make


async def run(lead, job_try=1, force=False):
    await worker.process_lead({"job_try": job_try}, str(lead.id), force)


def count(session, model, lead):
    return session.scalar(select(func.count()).select_from(model).where(model.lead_id == lead.id))


def transitions(session, lead):
    rows = session.scalars(
        select(AuditEvent).where(AuditEvent.lead_id == lead.id).order_by(AuditEvent.id)
    )
    return [(e.from_state, e.to_state, e.actor) for e in rows]


# --- worker job ---

@pytest.mark.usefixtures("factory")
async def test_happy_path(session, make_lead, ai):
    lead = make_lead()
    await run(lead)
    session.refresh(lead)
    assert lead.status == LeadStatus.DRAFT_READY and lead.attempts == 1
    assert count(session, LeadAnalysis, lead) == 1 and count(session, Draft, lead) == 1
    assert transitions(session, lead) == [
        ("NEW", "PROCESSING", "worker"), ("PROCESSING", "DRAFT_READY", "worker")
    ]
    analysis = session.scalars(select(LeadAnalysis).where(LeadAnalysis.lead_id == lead.id)).one()
    assert (analysis.fit_score, analysis.model, analysis.raw_json) == (70, "m-x", {"raw": 1})
    draft = session.scalars(select(Draft).where(Draft.lead_id == lead.id)).one()
    assert (draft.subject, draft.body, draft.decision) == ("Hi", "Body", None)


@pytest.mark.usefixtures("factory")
async def test_low_fit_then_force(session, make_lead, ai):
    extract, draft = ai
    extract.return_value = (extraction(10), "m-x", {})
    lead = make_lead()
    await run(lead)
    session.refresh(lead)
    assert lead.status == LeadStatus.LOW_FIT
    assert count(session, LeadAnalysis, lead) == 1 and count(session, Draft, lead) == 0
    draft.assert_not_called()

    # "draft anyway": endpoint moves to PROCESSING, then the job runs with force=True
    from app.workflow.transitions import transition
    transition(session, lead, LeadStatus.PROCESSING, "user:x")
    await run(lead, force=True)
    session.refresh(lead)
    assert lead.status == LeadStatus.DRAFT_READY
    assert count(session, LeadAnalysis, lead) == 1 and count(session, Draft, lead) == 1


@pytest.mark.usefixtures("factory")
async def test_ai_error_retries_then_fails(session, make_lead, ai):
    extract, _ = ai
    extract.side_effect = AIError([("kr/claude-haiku-4.5", "APITimeoutError"), ("m2", "bad json")])
    lead = make_lead()
    with pytest.raises(Retry) as r:
        await run(lead, job_try=1)
    assert r.value.defer_score == 30 * 1000
    session.refresh(lead)
    assert lead.status == LeadStatus.PROCESSING
    assert lead.last_error == "AIError: kr/claude-haiku-4.5 APITimeoutError; m2 bad json"

    await run(lead, job_try=3)  # last try: no raise
    session.refresh(lead)
    assert lead.status == LeadStatus.FAILED and lead.attempts == 3
    assert lead.last_error.startswith("AIError")
    assert transitions(session, lead)[-1] == ("PROCESSING", "FAILED", "worker")


@pytest.mark.usefixtures("factory")
async def test_unexpected_error_does_not_leak_text(session, make_lead, ai):
    ai[0].side_effect = ValueError("secret lead text")
    lead = make_lead()
    with pytest.raises(Retry):
        await run(lead)
    session.refresh(lead)
    assert "secret" not in lead.last_error


@pytest.mark.usefixtures("factory")
async def test_idempotent_and_resume(session, make_lead, ai):
    extract, _ = ai
    lead = make_lead()
    await run(lead)
    await run(lead)  # DRAFT_READY: skipped
    assert extract.call_count == 1
    assert count(session, Draft, lead) == 1 and len(transitions(session, lead)) == 2

    crashed = make_lead(LeadStatus.PROCESSING)  # simulated crash after NEW -> PROCESSING
    await run(crashed, job_try=2)
    session.refresh(crashed)
    assert crashed.status == LeadStatus.DRAFT_READY and crashed.attempts == 2


@pytest.mark.usefixtures("factory")
async def test_missing_lead_is_ignored(ai):
    await worker.process_lead({"job_try": 1}, str(uuid.uuid4()))
    ai[0].assert_not_called()


@pytest.mark.usefixtures("factory")
async def test_decided_draft_is_never_overwritten(session, make_lead, ai):
    lead = make_lead(LeadStatus.PROCESSING)
    session.add(Draft(
        tenant_id=lead.tenant_id, lead_id=lead.id, subject="Old", body="Old body",
        decision=DraftDecision.APPROVED,
    ))
    session.flush()
    await run(lead)
    draft = session.scalars(select(Draft).where(Draft.lead_id == lead.id)).one()
    assert (draft.subject, draft.body) == ("Old", "Old body")


@pytest.mark.usefixtures("factory")
async def test_undecided_draft_is_replaced(session, make_lead, ai):
    lead = make_lead(LeadStatus.PROCESSING)
    session.add(Draft(tenant_id=lead.tenant_id, lead_id=lead.id, subject="Old", body="Old body"))
    session.flush()
    await run(lead)
    draft = session.scalars(select(Draft).where(Draft.lead_id == lead.id)).one()
    assert (draft.subject, draft.body) == ("Hi", "Body")


# --- sweep ---

async def test_sweep_new_and_crashed_processing(session, make_lead, monkeypatch):
    now = datetime.now(UTC)
    old_new = make_lead(created_at=now - timedelta(minutes=5))
    make_lead(created_at=now - timedelta(minutes=1))
    stale = make_lead(LeadStatus.PROCESSING)
    fresh = make_lead(LeadStatus.PROCESSING)
    session.execute(
        text("UPDATE leads SET updated_at = now() - interval '20 minutes' WHERE id = :i"),
        {"i": stale.id},
    )
    session.execute(
        text("UPDATE leads SET updated_at = now() - interval '5 minutes' WHERE id = :i"),
        {"i": fresh.id},
    )
    monkeypatch.setattr(worker, "SessionLocal", lambda: nullcontext(session))
    redis = AsyncMock()
    await worker.requeue_stuck_leads({"redis": redis})
    queued = {c.args[1] for c in redis.enqueue_job.await_args_list}
    assert queued == {str(old_new.id), str(stale.id)}


def test_worker_settings():
    assert worker.WorkerSettings.max_tries == 3 and worker.WorkerSettings.job_timeout == 600


# --- endpoints ---

@pytest.fixture
def enqueue(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr("app.routes.leads.enqueue_process_lead", mock)
    return mock


@pytest.fixture
def mine(session, clerk):
    client.get("/v1/me", headers=auth)
    return session.scalars(select(Tenant).where(Tenant.clerk_org_id == "org_1")).one()


@pytest.mark.parametrize(
    ("path", "state", "force"),
    [("retry", LeadStatus.FAILED, False), ("draft-anyway", LeadStatus.LOW_FIT, True)],
)
def test_restart_endpoints(session, mine, make_lead, enqueue, path, state, force):
    lead = make_lead(state, tenant_id=mine.id)
    r = client.post(f"/v1/leads/{lead.id}/{path}", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "PROCESSING"
    session.refresh(lead)
    assert lead.status == LeadStatus.PROCESSING
    ev = transitions(session, lead)
    assert ev[0][:2] == (state, "PROCESSING") and ev[0][2].startswith("user:")
    if force:
        enqueue.assert_awaited_once_with(lead.id, force=True)
    else:
        enqueue.assert_awaited_once_with(lead.id)

    again = client.post(f"/v1/leads/{lead.id}/{path}", headers=auth)  # now PROCESSING
    assert again.status_code == 409 and enqueue.await_count == 1


@pytest.mark.parametrize("path", ["retry", "draft-anyway"])
def test_restart_wrong_state_and_other_tenant(session, mine, make_lead, enqueue, path):
    wrong = make_lead(LeadStatus.DRAFT_READY, tenant_id=mine.id)
    assert client.post(f"/v1/leads/{wrong.id}/{path}", headers=auth).status_code == 409
    other = make_lead(LeadStatus.FAILED if path == "retry" else LeadStatus.LOW_FIT)
    assert client.post(f"/v1/leads/{other.id}/{path}", headers=auth).status_code == 404
    assert client.post(f"/v1/leads/{uuid.uuid4()}/{path}", headers=auth).status_code == 404
    assert client.post(f"/v1/leads/{wrong.id}/{path}").status_code == 401
    enqueue.assert_not_called()
    session.refresh(other)
    assert transitions(session, other) == []


def test_lead_out_has_fit_score_and_last_error(session, mine, make_lead):
    scored = make_lead(LeadStatus.LOW_FIT, tenant_id=mine.id, last_error="boom")
    session.add(LeadAnalysis(
        tenant_id=mine.id, lead_id=scored.id, intent="i", urgency="low", fit_score=12,
        model="m", raw_json={},
    ))
    plain = make_lead(tenant_id=mine.id)
    session.flush()
    rows = {x["id"]: x for x in client.get("/v1/leads", headers=auth).json()}
    assert rows[str(scored.id)]["fit_score"] == 12 and rows[str(scored.id)]["last_error"] == "boom"
    assert rows[str(plain.id)]["fit_score"] is None
    one = client.get(f"/v1/leads/{scored.id}", headers=auth).json()
    assert one["fit_score"] == 12
