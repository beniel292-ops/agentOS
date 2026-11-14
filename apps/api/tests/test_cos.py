from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cos_auth import SCOPES
from app.intake import sha256_hex
from app.main import app
from app.models import AgentKey, AuditEvent, CallKind, CallStatus, ScheduledCall
from tests.auth_helpers import token

pytestmark = pytest.mark.usefixtures("clerk")
client = TestClient(app)

NOW = datetime(2026, 11, 14, 4, 30, tzinfo=UTC)  # 10:00 in Asia/Kolkata
P = "/v1/cos"


def bearer(role="admin", sub="user_1", org="org_1"):
    t = token(role=role, sub=sub, o={"id": org, "rol": role, "slg": org})
    return {"Authorization": f"Bearer {t}"}


OWNER = bearer()
MEMBER = bearer("member", "user_2")
OTHER = bearer("admin", "user_b", "org_b")


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr("app.routes.cos.now", lambda: state["now"])
    return state


def mkkey(name="hermes", scopes=("tasks:read", "tasks:write"), headers=OWNER):
    r = client.post(f"{P}/agent-keys", json={"name": name, "scopes": list(scopes)}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def agent(name="hermes", scopes=("tasks:read", "tasks:write")):
    return {"X-Agent-Key": mkkey(name, scopes)["key"]}


def audits(session, action):
    return list(session.scalars(select(AuditEvent).where(AuditEvent.action == action)))


def task(title="Write report", h=OWNER, **kw):
    return client.post(f"{P}/tasks", json={"title": title, "source": "dashboard", **kw}, headers=h)


# ---------------------------------------------------------------- permissions


def test_no_auth_is_401():
    assert client.get(f"{P}/tasks").status_code == 401
    assert client.get(f"{P}/tasks", headers={"X-Agent-Key": "cos_nope"}).status_code == 401


def test_agent_key_scopes(session):
    h = agent("reader", ("tasks:read",))
    assert client.get(f"{P}/tasks", headers=h).status_code == 200
    assert client.post(f"{P}/tasks", json={"title": "x", "source": "agent"}, headers=h
                       ).status_code == 403
    assert client.get(f"{P}/memory", headers=h).status_code == 403


def test_revoked_key_is_401():
    k = mkkey()
    h = {"X-Agent-Key": k["key"]}
    assert client.get(f"{P}/tasks", headers=h).status_code == 200
    assert client.post(f"{P}/agent-keys/{k['id']}/revoke", headers=OWNER).status_code == 200
    assert client.get(f"{P}/tasks", headers=h).status_code == 401


def test_agent_key_can_never_do_owner_things():
    h = agent("god", sorted(SCOPES))
    assert client.post(f"{P}/agent-keys", json={"name": "x", "scopes": ["stop"]}, headers=h
                       ).status_code == 403
    assert client.get(f"{P}/agent-keys", headers=h).status_code == 403
    assert client.put(f"{P}/settings", json={"max_calls_per_day": 3}, headers=h).status_code == 403
    assert client.post(f"{P}/resume", headers=h).status_code == 403


def test_member_vs_owner():
    assert client.get(f"{P}/tasks", headers=MEMBER).status_code == 200
    assert task(h=MEMBER).status_code == 201
    assert client.get(f"{P}/agent-keys", headers=MEMBER).status_code == 403
    assert client.put(f"{P}/settings", json={"max_calls_per_day": 3}, headers=MEMBER
                      ).status_code == 403
    assert client.post(f"{P}/resume", headers=MEMBER).status_code == 403
    # members cannot write checkins/opportunities/memory; owners can
    body = {"kind": "morning", "started_at": NOW.isoformat()}
    assert client.post(f"{P}/checkins", json=body, headers=MEMBER).status_code == 403
    assert client.post(f"{P}/checkins", json=body, headers=OWNER).status_code == 201


def test_key_plaintext_never_stored(session):
    k = mkkey()
    row = session.get(AgentKey, k["id"])
    assert row.key_hash == sha256_hex(k["key"]) and k["key"] != row.key_hash
    assert k["key"].startswith("cos_") and row.key_prefix == k["key"][:12]
    assert k["key"] not in str(client.get(f"{P}/agent-keys", headers=OWNER).json())


def test_key_validation_and_duplicate_name():
    r = client.post(f"{P}/agent-keys", json={"name": "a", "scopes": ["root"]}, headers=OWNER)
    assert r.status_code == 422
    mkkey("dup")
    r = client.post(f"{P}/agent-keys", json={"name": "dup", "scopes": ["stop"]}, headers=OWNER)
    assert r.status_code == 409
    k = client.get(f"{P}/agent-keys", headers=OWNER).json()[0]
    client.post(f"{P}/agent-keys/{k['id']}/revoke", headers=OWNER)
    mkkey("dup")  # a revoked name can be reused


def test_keys_are_tenant_scoped():
    k = mkkey()
    assert client.get(f"{P}/agent-keys", headers=OTHER).json() == []
    assert client.post(f"{P}/agent-keys/{k['id']}/revoke", headers=OTHER).status_code == 404


# ---------------------------------------------------------------- kill switch


def test_kill_switch(session):
    stopper = agent("voice", ("stop",))
    full = agent("full", [s for s in SCOPES if s != "stop"])
    assert client.get(f"{P}/settings", headers=OWNER).json()["stopped"] is False

    # a key without the stop scope cannot stop; with it, it can
    assert client.post(f"{P}/stop", headers=full).status_code == 403
    r = client.post(f"{P}/stop", headers=stopper)
    assert r.status_code == 200 and r.json()["stopped"] and r.json()["stopped_by"] == "agent:voice"
    assert audits(session, "cos_stopped")[0].actor == "agent:voice"

    for resp in (
        task(h=full),
        client.patch(f"{P}/tasks/{uuid_zero()}", json={"status": "done"}, headers=full),
        client.post(f"{P}/callbacks", json={"minutes": 10}, headers=full),
        client.post(f"{P}/opportunities", json={"title": "t", "source": "s", "relevance": 5},
                    headers=full),
        client.post(f"{P}/memory", json={"kind": "project", "text": "x", "source": "s"},
                    headers=full),
        client.post(f"{P}/checkins", json={"kind": "morning", "started_at": NOW.isoformat()},
                    headers=full),
        client.put(f"{P}/settings", json={"max_calls_per_day": 3}, headers=OWNER),
    ):
        assert resp.status_code == 423, resp.text
        assert resp.json() == {"detail": "Chief of Staff is stopped"}
    # reads still work
    for path in ("tasks", "calls", "checkins", "opportunities", "memory"):
        assert client.get(f"{P}/{path}", headers=full).status_code == 200
    assert client.get(f"{P}/tasks", headers=full).status_code == 200
    assert client.get(f"{P}/settings", headers=OWNER).json()["stopped"] is True

    # only an owner can resume
    assert client.post(f"{P}/resume", headers=stopper).status_code == 403
    assert client.post(f"{P}/resume", headers=MEMBER).status_code == 403
    r = client.post(f"{P}/resume", headers=OWNER)
    assert r.status_code == 200 and r.json()["stopped"] is False
    assert audits(session, "cos_resumed")[0].actor.startswith("user:")
    assert task(h=full).status_code == 201


def uuid_zero():
    return "00000000-0000-0000-0000-000000000000"


def test_members_can_stop():
    assert client.post(f"{P}/stop", headers=MEMBER).status_code == 200


# ---------------------------------------------------------------- tasks


def test_task_create_and_audit(session):
    h = agent("hermes", ("tasks:write", "tasks:read"))
    r = task("  Write   report ", h=h, priority=1)
    assert r.status_code == 201
    body = r.json()
    assert body["duplicate"] is False and body["title"] == "Write   report"
    assert body["priority"] == 1
    ev = audits(session, "task_created")[0]
    assert ev.actor == "agent:hermes" and ev.lead_id is None and ev.data["id"] == body["id"]


def test_task_dedup_on_open_titles_only():
    first = task("Write Report").json()
    r = task("  write   REPORT ")
    assert r.status_code == 200 and r.json()["duplicate"] is True and r.json()["id"] == first["id"]
    client.patch(f"{P}/tasks/{first['id']}", json={"status": "done"}, headers=OWNER)
    again = task("write report")
    assert again.status_code == 201 and again.json()["id"] != first["id"]
    # other tenant is independent
    assert task("write report", h=OTHER).status_code == 201


def test_task_done_and_reopen(session):
    t = task().json()
    r = client.patch(f"{P}/tasks/{t['id']}", json={"status": "done"}, headers=OWNER).json()
    assert r["status"] == "done" and r["completed_at"] is not None
    r = client.patch(f"{P}/tasks/{t['id']}", json={"status": "pending", "priority": 3},
                     headers=OWNER).json()
    assert r["status"] == "pending" and r["completed_at"] is None and r["priority"] == 3
    r = client.patch(f"{P}/tasks/{t['id']}", json={"due_date": None, "notes": "n"}, headers=OWNER)
    assert r.json()["notes"] == "n"
    assert sorted(e.data["fields"][0] for e in audits(session, "task_updated")) == [
        "due_date", "status", "status"]


def test_task_reopen_conflicting_title_is_409():
    a = task("same").json()
    client.patch(f"{P}/tasks/{a['id']}", json={"status": "done"}, headers=OWNER)
    task("same")
    r = client.patch(f"{P}/tasks/{a['id']}", json={"status": "pending"}, headers=OWNER)
    assert r.status_code == 409


def test_task_validation():
    assert task("").status_code == 422
    assert task("x" * 301).status_code == 422
    assert task("x", priority=4).status_code == 422
    assert client.post(f"{P}/tasks", json={"title": "x", "source": "bogus"}, headers=OWNER
                       ).status_code == 422


def test_task_filters_use_tenant_timezone(clock):
    clock["now"] = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)  # 15 Nov 01:30 in Kolkata
    task("overdue one", due_date="2026-11-14")
    task("due today", due_date="2026-11-15")
    task("tomorrow", due_date="2026-11-16")
    task("no date")
    started = task("started").json()
    client.patch(f"{P}/tasks/{started['id']}", json={"status": "in_progress"}, headers=OWNER)
    done = task("done today", due_date="2026-11-15").json()
    client.patch(f"{P}/tasks/{done['id']}", json={"status": "done"}, headers=OWNER)

    def titles(q):
        return [t["title"] for t in client.get(f"{P}/tasks?{q}", headers=OWNER).json()]

    assert sorted(titles("due=today")) == ["due today", "overdue one", "started"]
    assert titles("due=overdue") == ["overdue one"]
    assert len(titles("due=all")) == 6
    assert titles("status=done") == ["done today"]
    # in UTC (the same instant, 14 Nov) "due today" would not be due: tz is honoured
    r = client.put(f"{P}/settings", json={"timezone": "UTC"}, headers=OWNER)
    assert r.status_code == 200
    assert sorted(titles("due=today")) == ["overdue one", "started"]


def test_task_tenant_isolation():
    t = task().json()
    assert client.patch(f"{P}/tasks/{t['id']}", json={"status": "done"}, headers=OTHER
                        ).status_code == 404
    assert client.get(f"{P}/tasks", headers=OTHER).json() == []


# ---------------------------------------------------------------- callbacks


def cb(minutes=10, h=OWNER, **kw):
    return client.post(f"{P}/callbacks", json={"minutes": minutes, **kw}, headers=h)


@pytest.mark.parametrize("minutes", [10, 20, 30, 45, 5, 240])
def test_callback_accepts(minutes, session):
    r = cb(minutes)
    assert r.status_code == 201 and r.json()["moved"] is False
    assert datetime.fromisoformat(r.json()["due_at"]) == NOW + timedelta(minutes=minutes)


@pytest.mark.parametrize("minutes", [3, 300, 0, -5])
def test_callback_rejects_out_of_range(minutes):
    assert cb(minutes).status_code == 422


def test_callback_moves_pending_one(session):
    h = agent("voice", ("callbacks:write", "tasks:read"))
    first = cb(10, h=h, reason="a").json()
    r = cb(30, h=h, reason="b")
    assert r.status_code == 200 and r.json()["moved"] is True and r.json()["id"] == first["id"]
    assert r.json()["reason"] == "b"
    calls = client.get(f"{P}/calls", headers=OWNER).json()
    assert len(calls) == 1 and datetime.fromisoformat(calls[0]["due_at"]) == NOW + timedelta(
        minutes=30)
    assert audits(session, "callback_scheduled")[0].actor == "agent:voice"
    assert audits(session, "callback_moved")[0].data["minutes"] == 30


def test_callback_cancel(session):
    assert client.delete(f"{P}/callbacks/pending", headers=OWNER).status_code == 404
    cb(10)
    r = client.delete(f"{P}/callbacks/pending", headers=OWNER)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert audits(session, "callback_cancelled")
    assert cb(10).status_code == 201  # a new one is allowed after cancel


@pytest.mark.parametrize(
    "now_utc",
    [
        datetime(2026, 11, 14, 16, 50, tzinfo=UTC),  # 22:20 IST + 20 min = 22:40, quiet
        datetime(2026, 11, 14, 19, 0, tzinfo=UTC),  # 00:30 IST, quiet (after midnight)
        datetime(2026, 11, 14, 23, 20, tzinfo=UTC),  # 04:50 IST + 20 = 05:10, quiet
    ],
)
def test_callback_quiet_hours_cross_midnight(clock, now_utc):
    clock["now"] = now_utc
    r = cb(20)
    assert r.status_code == 409 and "Quiet hours" in r.json()["detail"]


def test_callback_just_outside_quiet_hours(clock):
    clock["now"] = datetime(2026, 11, 15, 1, 40, tzinfo=UTC)  # 07:10 IST + 20 = 07:30 allowed
    assert cb(20).status_code == 201


def test_callback_max_per_day(session, clock):
    tenant_id = client.get("/v1/me", headers=OWNER).json()["tenant"]["id"]
    client.put(f"{P}/settings", json={"max_calls_per_day": 2}, headers=OWNER)
    for kind, h in ((CallKind.MORNING, 3), (CallKind.EVENING, 2)):  # two earlier calls today
        session.add(ScheduledCall(tenant_id=tenant_id, kind=kind, created_by="t",
                                  due_at=NOW - timedelta(hours=h), status=CallStatus.COMPLETED))
    session.flush()
    assert cb(10).status_code == 429
    client.put(f"{P}/settings", json={"max_calls_per_day": 3}, headers=OWNER)
    assert cb(10).status_code == 201
    # moving the pending callback is not an extra call
    assert cb(20).status_code == 200


def test_callback_min_gap(session):
    tenant_id = client.get("/v1/me", headers=OWNER).json()["tenant"]["id"]
    session.add(ScheduledCall(tenant_id=tenant_id, kind=CallKind.MORNING, created_by="t",
                              due_at=NOW - timedelta(minutes=5), status=CallStatus.PLACED))
    session.flush()
    client.put(f"{P}/settings", json={"min_gap_minutes": 60}, headers=OWNER)
    r = cb(10)
    assert r.status_code == 409 and "60 minutes" in r.json()["detail"]
    assert cb(60).status_code == 201


# ---------------------------------------------------------------- opportunities, memory, checkins


def test_opportunity_dedup_and_patch(session):
    h = agent("hermes", ("opportunities:write", "opportunities:read"))
    body = {"title": "Grant", "source": "web", "url": "https://x.test/a", "relevance": 80}
    r = client.post(f"{P}/opportunities", json=body, headers=h)
    assert r.status_code == 201 and r.json()["verification_status"] == "unverified"
    r2 = client.post(f"{P}/opportunities", json=body | {"title": "Other"}, headers=h)
    assert r2.status_code == 200 and r2.json()["duplicate"] and r2.json()["id"] == r.json()["id"]
    # no url -> never a duplicate; other tenant independent
    nourl = {"title": "N", "source": "web", "relevance": 1}
    assert client.post(f"{P}/opportunities", json=nourl, headers=h).status_code == 201
    assert client.post(f"{P}/opportunities", json=nourl, headers=h).status_code == 201
    p = client.patch(f"{P}/opportunities/{r.json()['id']}",
                     json={"status": "saved", "verification_status": "verified"}, headers=h)
    assert p.json()["status"] == "saved" and p.json()["verification_status"] == "verified"
    assert client.get(f"{P}/opportunities?status=saved", headers=h).json()[0]["title"] == "Grant"
    assert audits(session, "opportunity_created")[0].actor == "agent:hermes"
    assert client.post(f"{P}/opportunities", json=body | {"relevance": 101}, headers=h
                       ).status_code == 422


def test_opportunity_tenant_isolation():
    body = {"title": "Grant", "source": "web", "url": "https://x.test/a", "relevance": 80}
    oid = client.post(f"{P}/opportunities", json=body, headers=OWNER).json()["id"]
    assert client.patch(f"{P}/opportunities/{oid}", json={"status": "saved"}, headers=OTHER
                        ).status_code == 404
    assert client.post(f"{P}/opportunities", json=body, headers=OTHER).status_code == 201


def test_memory_dedup_by_normalized_text(session):
    h = agent("openclaw", ("memory:write", "memory:read"))
    a = client.post(f"{P}/memory", json={"kind": "preference", "text": "Likes  Tea", "source": "x"},
                    headers=h)
    b = client.post(f"{P}/memory", json={"kind": "decision", "text": " likes tea ", "source": "y"},
                    headers=h)
    assert a.status_code == 201 and b.status_code == 200 and b.json()["id"] == a.json()["id"]
    assert b.json()["duplicate"] is True
    assert len(client.get(f"{P}/memory", headers=h).json()) == 1
    assert client.get(f"{P}/memory?kind=decision", headers=h).json() == []
    assert audits(session, "memory_created")[0].actor == "agent:openclaw"
    long = {"kind": "project", "text": "x" * 2001, "source": "s"}
    assert client.post(f"{P}/memory", json=long, headers=h).status_code == 422


def test_checkins(session):
    body = {"kind": "evening", "call_id": "c1", "started_at": NOW.isoformat(),
            "transcript": "hello", "summary": "ok"}
    h = agent("voice", ("checkins:write", "tasks:read"))
    r = client.post(f"{P}/checkins", json=body, headers=h)
    assert r.status_code == 201
    assert client.get(f"{P}/checkins", headers=h).json()[0]["call_id"] == "c1"
    assert client.get(f"{P}/checkins", headers=OTHER).json() == []
    ev = audits(session, "checkin_created")[0]
    assert ev.actor == "agent:voice" and "hello" not in str(ev.data)


# ---------------------------------------------------------------- settings


def test_settings_defaults_and_validation(session):
    s = client.get(f"{P}/settings", headers=OWNER).json()
    assert s["timezone"] == "Asia/Kolkata" and s["morning_time"] == "08:30:00"
    assert s["max_calls_per_day"] == 6 and s["min_gap_minutes"] == 10
    for bad in (
        {"timezone": "Mars/Base"}, {"timezone": "../etc/passwd"}, {"max_calls_per_day": 0},
        {"max_calls_per_day": 21}, {"min_gap_minutes": 4}, {"min_gap_minutes": 241},
        {"morning_time": "25:00"}, {"quiet_start": "07:30"},
    ):
        assert client.put(f"{P}/settings", json=bad, headers=OWNER).status_code == 422, bad
    ok = {"timezone": "Europe/Berlin", "max_calls_per_day": 3, "quiet_start": "23:00"}
    r = client.put(f"{P}/settings", json=ok, headers=OWNER)
    assert r.status_code == 200 and r.json()["timezone"] == "Europe/Berlin"
    assert audits(session, "settings_updated")[-1].data == {
        "fields": ["max_calls_per_day", "quiet_start", "timezone"]
    }
