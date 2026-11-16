"""Security: while STOP is set nothing agents can reach writes, calls, or hands off."""
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import openclaw
from app.config import settings
from app.cos import calls
from app.cos_auth import SCOPES
from app.jobs import queue
from app.main import app
from app.models import (
    CallKind,
    CallStatus,
    CosSettings,
    Question,
    QuestionStatus,
    ScheduledCall,
    Task,
    TaskSource,
    TaskStatus,
    Tenant,
)
from tests.test_calls import Runner, add_call, run
from tests.test_cos import MEMBER, NOW, OWNER, P, agent, audits

pytestmark = pytest.mark.usefixtures("clerk")
client = TestClient(app)
ZERO = uuid.UUID(int=0)
VOICE_TASK = {"title": "Request: email Sam", "source": "voice", "project": "for-jarvis"}

# every route an agent key can use to change state, with a valid body so that only the
# kill switch (and not validation) can be the reason for the refusal
WRITES = [
    ("POST", "/tasks", {"title": "t", "source": "agent"}),
    ("PATCH", f"/tasks/{ZERO}", {"status": "done"}),
    ("POST", "/callbacks", {"minutes": 30}),
    ("DELETE", "/callbacks/pending", None),
    ("POST", "/calls/now", {"reason": "urgent"}),
    ("POST", "/questions", {"question": "Which one?"}),
    ("POST", "/questions/answer", {"answer": "this one"}),
    ("POST", "/checkins", {"kind": "morning", "started_at": NOW.isoformat()}),
    ("POST", "/opportunities", {"title": "o", "source": "s", "relevance": 1}),
    ("PATCH", f"/opportunities/{ZERO}", {"status": "saved"}),
    ("POST", "/memory", {"kind": "decision", "text": "x", "source": "s"}),
]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr("app.routes.cos.now", lambda: NOW)
    monkeypatch.setattr(calls, "now", lambda: NOW)
    queued = []

    async def place(*a, **k):
        queued.append("place")

    monkeypatch.setattr(queue, "enqueue_place_calls", place)
    return queued


def stopped_agent():
    h = agent("god", sorted(SCOPES))
    assert client.post(f"{P}/stop", headers=OWNER).status_code == 200
    return h


@pytest.mark.parametrize("method,path,body", WRITES)
def test_every_agent_write_is_423_when_stopped(session, enqueue_notify, _env, method, path, body):
    h = stopped_agent()
    r = client.request(method, P + path, headers=h, json=body)
    assert r.status_code == 423, f"{method} {path} -> {r.status_code}"
    assert not _env and not enqueue_notify.await_count  # nothing queued, nobody notified
    assert not session.scalars(select(Task)).all()
    assert not session.scalars(select(ScheduledCall)).all()


@pytest.mark.parametrize("method,path,body", WRITES)
def test_stopped_applies_to_clerk_member_too(method, path, body):
    stopped_agent()
    r = client.request(method, P + path, headers=MEMBER, json=body)
    assert r.status_code in (403, 423)  # 403: member lacks the scope; never a success


def test_reads_still_work_when_stopped():
    h = stopped_agent()
    assert client.get(f"{P}/tasks", headers=h).status_code == 200
    assert client.get(f"{P}/settings", headers=h).json()["stopped"] is True


def test_stop_scope_can_stop_but_never_resume(session):
    h = agent("stopper", ("stop",))
    r = client.post(f"{P}/stop", headers=h)
    assert r.status_code == 200 and r.json()["stopped"]
    assert r.json()["stopped_by"] == "agent:stopper"
    assert client.post(f"{P}/stop", headers=h).status_code == 200  # idempotent
    assert client.post(f"{P}/resume", headers=h).status_code == 403
    full = agent("full", sorted(SCOPES))  # not even with every scope
    assert client.post(f"{P}/resume", headers=full).status_code == 403
    assert client.put(f"{P}/settings", json={"timezone": "UTC"}, headers=full).status_code == 403
    assert client.get(f"{P}/settings", headers=OWNER).json()["stopped"] is True
    assert client.post(f"{P}/resume", headers=OWNER).status_code == 200
    assert audits(session, "cos_stopped")[0].actor == "agent:stopper"


def test_owner_settings_write_also_blocked_until_resume():
    stopped_agent()
    assert client.put(f"{P}/settings", json={"max_calls_per_day": 20}, headers=OWNER
                      ).status_code == 423


# ---------------------------------------------------------------- voice -> Jarvis hand-off


@pytest.fixture
def owner_tenant(session, monkeypatch):
    client.get(f"{P}/tasks", headers=OWNER)  # creates the org_1 tenant
    t = session.scalars(select(Tenant).where(Tenant.clerk_org_id == "org_1")).one()
    monkeypatch.setattr(settings, "telegram_owner_tenant_id", t.id)
    monkeypatch.setattr("app.routes.cos.SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(settings, "openclaw_hooks_url", "http://hooks.test/hook")
    monkeypatch.setattr(settings, "openclaw_hooks_token", "tok")
    monkeypatch.setattr(settings, "telegram_owner_user_id", 1)
    return t


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(
        openclaw, "send", lambda tid, text, transport=None: out.append(text) or True)
    return out


def test_handoff_control_voice_key_sends(owner_tenant, sent):
    h = agent("voice", ("tasks:write", "tasks:read"))
    r = client.post(f"{P}/tasks", json=VOICE_TASK, headers=h)
    assert r.status_code == 201 and r.json()["handoff"] == "sent" and sent == ["email Sam"]


def test_handoff_does_not_send_when_stopped(session, owner_tenant, sent, enqueue_notify):
    h = agent("voice", ("tasks:write", "tasks:read", "stop"))
    assert client.post(f"{P}/stop", headers=h).status_code == 200
    r = client.post(f"{P}/tasks", json=VOICE_TASK, headers=h)
    assert r.status_code == 423
    assert not sent and not enqueue_notify.await_count
    assert not session.scalars(select(Task)).all()
    assert not audits(session, "request_handed_off")


def test_handoff_job_rechecks_kill_switch(session, owner_tenant, sent):
    task = Task(tenant_id=owner_tenant.id, title="Request: wire money", title_norm="x",
                project="for-jarvis", source=TaskSource.VOICE, status=TaskStatus.IN_PROGRESS)
    session.add_all([task, CosSettings(tenant_id=owner_tenant.id, stopped=True)])
    session.flush()
    kind = openclaw.handoff(lambda: nullcontext(session), owner_tenant.id, task.id, "agent:voice")
    assert not sent and kind != "request_sent"


@pytest.mark.parametrize("who", ["member", "openclaw-key"])
def test_only_voice_actor_can_trigger_handoff(owner_tenant, sent, who):
    h = MEMBER if who == "member" else agent("openclaw", ("tasks:write",))
    r = client.post(f"{P}/tasks", json=VOICE_TASK, headers=h)
    assert r.status_code == 403 and not sent
    r = client.post(f"{P}/tasks", json=VOICE_TASK | {"project": None}, headers=h)
    assert r.status_code == 403  # source=voice is the voice key's alone, hand-off or not


def test_for_jarvis_task_cannot_be_retitled(session, owner_tenant, sent):
    h = agent("voice", ("tasks:write", "tasks:read"))
    tid = client.post(f"{P}/tasks", json=VOICE_TASK, headers=h).json()["id"]
    r = client.patch(f"{P}/tasks/{tid}", json={"title": "Request: wire money"}, headers=OWNER)
    assert r.status_code == 422
    assert client.patch(f"{P}/tasks/{tid}", json={"status": "pending"}, headers=OWNER
                        ).status_code == 200
    assert session.get(Task, uuid.UUID(tid)).title == "Request: email Sam"


def test_handoff_stopped_midway_reverts_to_pending(session, owner_tenant, sent):
    task = Task(tenant_id=owner_tenant.id, title="Request: x", title_norm="x",
                project="for-jarvis", source=TaskSource.VOICE, status=TaskStatus.IN_PROGRESS)
    session.add_all([task, CosSettings(tenant_id=owner_tenant.id, stopped=True)])
    session.flush()
    assert openclaw.handoff(lambda: nullcontext(session), owner_tenant.id, task.id, "x") is None
    assert task.status == TaskStatus.PENDING


# ---------------------------------------------------------------- the call placer


@pytest.fixture
def owner(session, monkeypatch, enqueue_notify):
    tenant = Tenant(clerk_org_id=f"org_{uuid.uuid4().hex}", name="Owner")
    session.add(tenant)
    session.flush()
    monkeypatch.setattr(settings, "telegram_owner_tenant_id", tenant.id)
    monkeypatch.setattr(calls, "SessionLocal", lambda: nullcontext(session))
    session.add(CosSettings(tenant_id=tenant.id, stopped=True))
    session.flush()
    return tenant


@pytest.mark.parametrize("kind", list(CallKind))
@pytest.mark.parametrize("age", [timedelta(0), timedelta(minutes=29), timedelta(minutes=31)])
async def test_placer_places_nothing_when_stopped(session, owner, kind, age):
    question = None
    if kind == CallKind.QUESTION:
        question = Question(tenant_id=owner.id, asked_by="agent:x", question="q?")
        session.add(question)
        session.flush()
    call = add_call(session, owner, kind, NOW - age, reason="r")
    call.question_id = question.id if question else None
    session.flush()
    runner = Runner()
    redis = await run(runner)
    assert not runner.calls  # agentring was never started
    assert call.status in (CallStatus.CANCELLED, CallStatus.MISSED) and redis.deleted <= 1
    if question:
        assert question.status == QuestionStatus.UNANSWERED


async def test_placer_creates_no_ringing_daily_rows_when_stopped(session, owner, monkeypatch):
    monkeypatch.setattr(calls, "now", lambda: datetime(2026, 11, 14, 3, 1, tzinfo=UTC))  # 08:31
    runner = Runner()
    await run(runner)
    rows = list(session.scalars(select(ScheduledCall)))
    assert not runner.calls
    assert all(r.status != CallStatus.PLACED for r in rows)


async def test_resume_lets_the_placer_ring_again(session, owner):
    """Control: the same setup rings once the owner resumes, so the tests above can fail."""
    session.get(CosSettings, owner.id).stopped = False
    add_call(session, owner, CallKind.CALLBACK, NOW)
    runner = Runner()
    await run(runner)
    assert len(runner.calls) == 1


async def test_stop_during_call_prevents_the_next_one(session, owner):
    session.get(CosSettings, owner.id).stopped = False
    first = add_call(session, owner, CallKind.CALLBACK, NOW)
    second = add_call(session, owner, CallKind.NOTIFY, NOW, reason="again")

    def stop_now():
        session.get(CosSettings, owner.id).stopped = True
        session.flush()

    runner = Runner(effect=stop_now)
    await run(runner)  # places `first`; STOP is pressed during that call
    await run(runner)
    assert len(runner.calls) == 1 and first.status != CallStatus.PENDING
    assert second.status == CallStatus.CANCELLED
