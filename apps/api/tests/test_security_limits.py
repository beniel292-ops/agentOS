"""Security: call and hand-off guardrails, tested on both sides of every boundary."""
import json
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import openclaw
from app.config import settings
from app.cos import calls
from app.jobs import queue
from app.main import app
from app.models import (
    AuditEvent,
    CallKind,
    CallStatus,
    CosSettings,
    Question,
    QuestionStatus,
    ScheduledCall,
    Task,
    TaskStatus,
    Tenant,
)
from tests.test_calls import Runner, add_call, run
from tests.test_cos import NOW, OTHER, OWNER, P, agent, audits, mkkey

pytestmark = pytest.mark.usefixtures("clerk")
client = TestClient(app)

# endpoint name -> (request function, minutes added to "now" for the call's due time)
ENDPOINTS = {
    "callback": (lambda h: client.post(f"{P}/callbacks", json={"minutes": 10}, headers=h), 10),
    "calls_now": (lambda h: client.post(f"{P}/calls/now", json={"reason": "x"}, headers=h), 0),
    "question": (lambda h: client.post(f"{P}/questions", json={"question": "q?"}, headers=h), 0),
}
ALL = ("callbacks:write", "calls:request", "tasks:read", "tasks:write")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr("app.routes.cos.now", lambda: state["now"])
    monkeypatch.setattr(calls, "now", lambda: state["now"])

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(queue, "enqueue_place_calls", noop)
    return state


@pytest.fixture
def tenant_id():
    return client.get("/v1/me", headers=OWNER).json()["tenant"]["id"]


@pytest.fixture
def h():
    return agent("caller", ALL)


def put(**fields):
    assert client.put(f"{P}/settings", json=fields, headers=OWNER).status_code == 200


def seed(session, tenant_id, due, status=CallStatus.COMPLETED, kind=CallKind.CALLBACK):
    session.add(ScheduledCall(tenant_id=tenant_id, kind=kind, due_at=due, status=status,
                              created_by="t"))
    session.flush()


# ---------------------------------------------------------------- max calls per day


def utc(*a):
    return datetime(*a, tzinfo=UTC)


# tenant day (Asia/Kolkata) = 2026-11-13 18:30Z .. 2026-11-14 18:30Z; limit is 2
DAY_CASES = [
    ("yesterday_local_not_counted",
     [(utc(2026, 11, 13, 18, 29), "completed", "callback")] * 2, 201),
    ("day_start_counted",
     [(utc(2026, 11, 13, 18, 30), "completed", "callback"),
      (utc(2026, 11, 14, 3, 0), "placed", "callback")], 429),
    ("later_today_pending_counted",
     [(utc(2026, 11, 13, 18, 30), "completed", "callback"),
      (utc(2026, 11, 14, 18, 29), "pending", "evening")], 429),
    ("next_local_day_not_counted",
     [(utc(2026, 11, 13, 18, 30), "completed", "callback"),
      (utc(2026, 11, 14, 18, 30), "pending", "evening")], 201),
    ("cancelled_free", [
        (utc(2026, 11, 14, 1, 0), "cancelled", "callback"),
        (utc(2026, 11, 14, 2, 0), "cancelled", "callback"),
        (utc(2026, 11, 14, 3, 0), "cancelled", "callback")], 201),
    ("missed_rings_count", [
        (utc(2026, 11, 14, 1, 0), "missed", "callback"),
        (utc(2026, 11, 14, 2, 0), "missed", "callback")], 429),
    ("one_below_limit", [(utc(2026, 11, 14, 3, 0), "completed", "callback")], 201),
]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("name,seeds,expected", DAY_CASES, ids=[c[0] for c in DAY_CASES])
def test_daily_limit_boundary(session, tenant_id, h, endpoint, name, seeds, expected):
    put(max_calls_per_day=2)
    for due, status, kind in seeds:
        seed(session, tenant_id, due, CallStatus(status), CallKind(kind))
    assert ENDPOINTS[endpoint][0](h).status_code == expected


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_daily_limit_cannot_be_raised_by_agents(h, endpoint):
    assert client.put(f"{P}/settings", json={"max_calls_per_day": 20}, headers=h).status_code == 403
    for bad in (0, 21, -1):
        r = client.put(f"{P}/settings", json={"max_calls_per_day": bad}, headers=OWNER)
        assert r.status_code == 422


# ---------------------------------------------------------------- minimum gap between calls


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("status", [CallStatus.PLACED, CallStatus.COMPLETED, CallStatus.MISSED])
@pytest.mark.parametrize("delta,expected", [(-1, 409), (0, 201), (1, 201)])
def test_min_gap_boundary(session, tenant_id, h, endpoint, status, delta, expected):
    put(min_gap_minutes=30)
    due = NOW + timedelta(minutes=ENDPOINTS[endpoint][1])
    # the last call sits exactly `gap` minutes before the new one, +/- one minute
    seed(session, tenant_id, due - timedelta(minutes=30) + timedelta(minutes=-delta), status)
    assert ENDPOINTS[endpoint][0](h).status_code == expected


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_gap_ignores_calls_that_never_rang(session, tenant_id, h, endpoint):
    put(min_gap_minutes=60)
    seed(session, tenant_id, NOW - timedelta(minutes=1), CallStatus.CANCELLED)
    assert ENDPOINTS[endpoint][0](h).status_code == 201


# ---------------------------------------------------------------- quiet hours (tenant timezone)

TZS = ["Asia/Kolkata", "America/New_York", "Pacific/Auckland"]
# (quiet_start, quiet_end, local "HH:MM", quiet?) start inclusive, end exclusive
QUIET_CASES = [
    ("22:00", "07:00", "21:59", False), ("22:00", "07:00", "22:00", True),
    ("22:00", "07:00", "23:59", True), ("22:00", "07:00", "00:00", True),
    ("22:00", "07:00", "06:59", True), ("22:00", "07:00", "07:00", False),
    ("13:00", "14:00", "12:59", False), ("13:00", "14:00", "13:00", True),
    ("13:00", "14:00", "13:59", True), ("13:00", "14:00", "14:00", False),
    ("00:00", "06:00", "23:59", False), ("00:00", "06:00", "00:00", True),
    ("00:00", "06:00", "05:59", True), ("00:00", "06:00", "06:00", False),
]


def local_to_utc(tz: str, hhmm: str) -> datetime:
    h_, m_ = map(int, hhmm.split(":"))
    return datetime(2026, 11, 14, h_, m_, tzinfo=ZoneInfo(tz)).astimezone(UTC)


@pytest.mark.parametrize("endpoint", ["calls_now", "question"])
@pytest.mark.parametrize("tz", TZS)
@pytest.mark.parametrize("start,end,at,quiet", QUIET_CASES)
def test_quiet_hours_boundaries(_env, h, endpoint, tz, start, end, at, quiet):
    put(timezone=tz, quiet_start=start, quiet_end=end)
    _env["now"] = local_to_utc(tz, at)
    r = ENDPOINTS[endpoint][0](h)
    assert r.status_code == (409 if quiet else 201), (tz, start, end, at, r.text)
    if quiet:
        assert "uiet" in r.json()["detail"]


def test_callback_quiet_hours_use_the_due_time(_env, h):
    put(quiet_start="22:00", quiet_end="07:00")  # Asia/Kolkata
    _env["now"] = local_to_utc("Asia/Kolkata", "21:49")  # +10 min = 21:59: allowed
    assert ENDPOINTS["callback"][0](h).status_code == 201
    client.delete(f"{P}/callbacks/pending", headers=h)
    _env["now"] = local_to_utc("Asia/Kolkata", "21:50")  # +10 min = 22:00: quiet
    assert ENDPOINTS["callback"][0](h).status_code == 409
    _env["now"] = local_to_utc("Asia/Kolkata", "06:50")  # +10 min = 07:00: allowed
    assert ENDPOINTS["callback"][0](h).status_code == 201


def test_quiet_window_must_not_be_empty():
    r = client.put(f"{P}/settings", json={"quiet_start": "08:00", "quiet_end": "08:00"},
                   headers=OWNER)
    assert r.status_code == 422  # otherwise "start == end" could silently mean no quiet hours
    assert client.put(f"{P}/settings", json={"timezone": "Mars/Base"}, headers=OWNER
                      ).status_code == 422


@pytest.fixture
def owner(session, monkeypatch, enqueue_notify):
    t = Tenant(clerk_org_id=f"org_{uuid.uuid4().hex}", name="Owner")
    session.add(t)
    session.flush()
    monkeypatch.setattr(settings, "telegram_owner_tenant_id", t.id)
    monkeypatch.setattr(calls, "SessionLocal", lambda: nullcontext(session))
    return t


@pytest.mark.parametrize("at,quiet", [("21:59", False), ("22:00", True), ("06:59", True),
                                      ("07:00", False)])
async def test_placer_quiet_hours_in_tenant_timezone(session, owner, _env, at, quiet):
    tz = "America/New_York"
    session.add(CosSettings(tenant_id=owner.id, timezone=tz, quiet_start="22:00",
                            quiet_end="07:00"))
    _env["now"] = local_to_utc(tz, at)
    call = add_call(session, owner, CallKind.NOTIFY, _env["now"], reason="r")
    runner = Runner()
    await run(runner)
    assert bool(runner.calls) is not quiet
    assert call.status == (CallStatus.CANCELLED if quiet else CallStatus.COMPLETED)


@pytest.mark.parametrize("late,expected_rings", [(1, False), (0, True), (-1, True)])
async def test_placer_gap_boundary(session, owner, late, expected_rings):
    # the last call was exactly `gap` minutes ago, give or take one minute
    session.add(CosSettings(tenant_id=owner.id, min_gap_minutes=30))
    add_call(session, owner, CallKind.CALLBACK, NOW - timedelta(minutes=30 - late),
             CallStatus.COMPLETED)
    call = add_call(session, owner, CallKind.NOTIFY, NOW, reason="r")
    runner = Runner()
    await run(runner)
    assert bool(runner.calls) is expected_rings and (call.status == CallStatus.CANCELLED) is (
        not expected_rings)


@pytest.mark.parametrize("age,rings", [(29, True), (31, False)])
async def test_placer_skips_calls_that_are_too_late(session, owner, age, rings):
    call = add_call(session, owner, CallKind.CALLBACK, NOW - timedelta(minutes=age))
    runner = Runner()
    await run(runner)
    assert bool(runner.calls) is rings and (call.status == CallStatus.CANCELLED) is (not rings)


async def test_placer_one_call_at_a_time(session, owner):
    add_call(session, owner, CallKind.CALLBACK, NOW)

    class Locked:
        async def set(self, *a, **k):
            return None

    runner = Runner()
    await calls.place_due_calls({"redis": Locked()}, runner=runner)
    assert not runner.calls


# ---------------------------------------------------------------- max 3 open questions


def ask(h=None, who=None):
    return client.post(f"{P}/questions", json={"question": "again?"}, headers=h or who)


@pytest.mark.parametrize("freed", [QuestionStatus.ANSWERED, QuestionStatus.UNANSWERED,
                                   QuestionStatus.CANCELLED])
def test_open_question_cap_and_release(session, h, freed):
    ids = [ask(h).json()["id"] for _ in range(3)]
    r = ask(h)
    assert r.status_code == 409 and "3 questions" in r.json()["detail"]
    session.get(Question, uuid.UUID(ids[0])).status = freed
    session.flush()
    assert ask(h).status_code == 201  # one slot opened
    assert ask(h).status_code == 409  # and only one


def test_calling_questions_count_as_open(session, h):
    ids = [ask(h).json()["id"] for _ in range(3)]
    for i in ids:
        session.get(Question, uuid.UUID(i)).status = QuestionStatus.CALLING
    session.flush()
    assert ask(h).status_code == 409
    assert session.scalar(select(func.count()).select_from(Question)) == 3  # none leaked in


def test_open_question_cap_is_per_tenant(h):
    other = {"X-Agent-Key": mkkey("o", ("calls:request",), headers=OTHER)["key"]}
    for _ in range(3):
        assert ask(h).status_code == 201
    assert ask(h).status_code == 409
    assert ask(other).status_code == 201  # B is not blocked by A's backlog


def test_rejected_question_leaves_no_trace(session, h):
    for _ in range(3):
        ask(h)
    rows = session.scalar(select(func.count()).select_from(ScheduledCall))
    assert ask(h).status_code == 409
    assert session.scalar(select(func.count()).select_from(ScheduledCall)) == rows


def test_answer_is_accepted_once(session, h):
    qid = ask(h).json()["id"]
    q = session.get(Question, uuid.UUID(qid))
    q.status = QuestionStatus.CALLING
    session.flush()
    voice = agent("voice", ("questions:answer",))
    first = client.post(f"{P}/questions/answer", json={"answer": "yes"}, headers=voice)
    assert first.status_code == 200
    again = client.post(f"{P}/questions/answer", json={"answer": "no, forged"}, headers=voice)
    assert again.status_code == 404
    session.refresh(q)
    assert q.answer == "yes"


# ---------------------------------------------------------------- callbacks: minimum delay, dedup


@pytest.mark.parametrize("minutes,expected", [(-1, 422), (0, 422), (4, 422), (5, 201),
                                              (240, 201), (241, 422), (10**9, 422)])
def test_callback_delay_bounds(h, minutes, expected):
    r = client.post(f"{P}/callbacks", json={"minutes": minutes}, headers=h)
    assert r.status_code == expected


def test_callback_repeats_never_duplicate_or_use_up_the_limit(session, tenant_id, h):
    put(max_calls_per_day=1)
    first = client.post(f"{P}/callbacks", json={"minutes": 10}, headers=h)
    assert first.status_code == 201
    for minutes in (10, 20, 30, 10, 45):
        r = client.post(f"{P}/callbacks", json={"minutes": minutes}, headers=h)
        assert r.status_code == 200 and r.json()["id"] == first.json()["id"]
    rows = session.scalars(select(ScheduledCall).where(ScheduledCall.kind == CallKind.CALLBACK))
    assert [c.status for c in rows] == [CallStatus.PENDING]


def test_call_now_repeats_move_one_row(session, h):
    ids = {client.post(f"{P}/calls/now", json={"reason": f"r{i}"}, headers=h).json()["id"]
           for i in range(5)}
    assert len(ids) == 1
    rows = list(session.scalars(select(ScheduledCall).where(ScheduledCall.kind == CallKind.NOTIFY)))
    assert len(rows) == 1 and rows[0].reason == "r4"


# ---------------------------------------------------------------- hand-offs per hour

VOICE_TASK = {"title": "Request: email Sam", "source": "voice", "project": "for-jarvis"}


@pytest.fixture
def hand(session, monkeypatch):
    client.get(f"{P}/tasks", headers=OWNER)
    t = session.scalars(select(Tenant).where(Tenant.clerk_org_id == "org_1")).one()
    monkeypatch.setattr(settings, "telegram_owner_tenant_id", t.id)
    monkeypatch.setattr("app.routes.cos.SessionLocal", lambda: nullcontext(session))
    sent = []
    monkeypatch.setattr(openclaw, "send", lambda tid, text, transport=None: sent.append(1) or True)
    return t, sent


def handed_off(session, tenant, n, minutes_ago, action="request_handed_off"):
    at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    session.add_all(AuditEvent(tenant_id=tenant.id, lead_id=None, actor="agent:voice",
                               action=action, data={}, at=at) for _ in range(n))
    session.flush()


@pytest.mark.parametrize("n,minutes_ago,other_action,expected", [
    (10, 55, None, "limited"),    # 10 inside the hour: refused
    (9, 55, None, "sent"),        # 9 inside the hour: the 10th is allowed
    (10, 65, None, "sent"),       # all outside the hour: allowed
    (5, 55, None, "sent"),
    (20, 55, "task_created", "sent"),  # other audit actions do not count
])
def test_handoff_hourly_limit_boundary(session, hand, n, minutes_ago, other_action, expected):
    tenant, sent = hand
    handed_off(session, tenant, n, minutes_ago, other_action or "request_handed_off")
    r = client.post(f"{P}/tasks", json=VOICE_TASK, headers=agent("voice", ("tasks:write",)))
    assert r.status_code == 201 and r.json()["handoff"] == expected
    if expected == "limited":
        assert r.json()["status"] == "pending" and not sent
        assert len(audits(session, "request_handed_off")) == n  # no event for a refused one
    else:
        assert r.json()["status"] == "in_progress" and len(sent) == 1


def test_handoff_limit_is_per_tenant(session, hand):
    _, sent = hand
    other = Tenant(clerk_org_id="org_elsewhere", name="x")
    session.add(other)
    session.flush()
    handed_off(session, other, 50, 5)  # someone else's traffic never blocks the owner
    r = client.post(f"{P}/tasks", json=VOICE_TASK, headers=agent("voice", ("tasks:write",)))
    assert r.json()["handoff"] == "sent" and len(sent) == 1


def test_handoff_retry_is_idempotent(session, hand):
    tenant, sent = hand
    h = agent("voice", ("tasks:write",))
    first = client.post(f"{P}/tasks", json=VOICE_TASK, headers=h)
    retries = [client.post(f"{P}/tasks", json=VOICE_TASK | {"title": "REQUEST:  Email   sam"},
                           headers=h) for _ in range(3)]
    assert first.json()["handoff"] == "sent"
    assert all(r.status_code == 200 and r.json()["duplicate"] and r.json()["handoff"] is None
               for r in retries)
    assert len(sent) == 1 and len(audits(session, "request_handed_off")) == 1


def test_openclaw_request_carries_a_stable_idempotency_key(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(settings, "openclaw_hooks_url", "http://hooks.test/hook")
    monkeypatch.setattr(settings, "openclaw_hooks_token", "tok")
    monkeypatch.setattr(settings, "telegram_owner_user_id", 42)
    tid = uuid.uuid4()
    transport = httpx.MockTransport(handler)
    assert openclaw.send(tid, "a", transport) and openclaw.send(tid, "a", transport)
    assert openclaw.send(uuid.uuid4(), "a", transport)
    keys = [hd["idempotency-key"] for hd, _ in seen]
    assert keys[0] == keys[1] == f"cos-request-{tid}" and keys[2] != keys[0]
    assert all(hd["authorization"] == "Bearer tok" for hd, _ in seen)
    assert all(b["agentId"] == "main" and b["to"] == "42" for _, b in seen)
    monkeypatch.setattr(settings, "openclaw_hooks_token", "")
    assert openclaw.send(tid, "a", transport) is False and len(seen) == 3  # no config: no call


# ---------------------------------------------------------------- other idempotent writes


def test_task_retries_do_not_duplicate(session, h):
    first = client.post(f"{P}/tasks", json={"title": "Pay rent", "source": "agent"}, headers=h)
    assert first.status_code == 201
    for title in ("pay rent", "  PAY   Rent ", "Pay rent"):
        r = client.post(f"{P}/tasks", json={"title": title, "source": "agent"}, headers=h)
        assert r.status_code == 200 and r.json()["duplicate"]
        assert r.json()["id"] == first.json()["id"]
    assert session.scalar(select(func.count()).select_from(Task)) == 1
    assert len(audits(session, "task_created")) == 1
    # a finished task frees the title again
    client.patch(f"{P}/tasks/{first.json()['id']}", json={"status": "done"}, headers=h)
    again = client.post(f"{P}/tasks", json={"title": "Pay rent", "source": "agent"}, headers=h)
    assert again.status_code == 201 and again.json()["id"] != first.json()["id"]
    done = session.get(Task, uuid.UUID(first.json()["id"]))
    assert done.status == TaskStatus.DONE


def test_renaming_into_an_open_duplicate_is_refused(h):
    a = client.post(f"{P}/tasks", json={"title": "A", "source": "agent"}, headers=h).json()
    client.post(f"{P}/tasks", json={"title": "B", "source": "agent"}, headers=h)
    r = client.patch(f"{P}/tasks/{a['id']}", json={"title": " b "}, headers=h)
    assert r.status_code == 409


def test_agent_key_names_cannot_be_duplicated_while_active():
    mkkey("dup", ("tasks:read",))
    r = client.post(f"{P}/agent-keys", json={"name": "dup", "scopes": ["tasks:read"]},
                    headers=OWNER)
    assert r.status_code == 409
