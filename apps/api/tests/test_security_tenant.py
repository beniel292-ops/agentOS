"""Security: objects of tenant A are invisible and untouchable from tenant B."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.jobs import queue
from app.main import app
from app.models import CallStatus, Question, QuestionStatus, ScheduledCall
from tests.test_cos import NOW, OTHER, P, bearer, mkkey

pytestmark = pytest.mark.usefixtures("clerk")
client = TestClient(app)

ALL = ("tasks:read", "tasks:write", "callbacks:write", "checkins:write", "memory:read",
       "memory:write", "opportunities:read", "opportunities:write", "calls:request",
       "questions:answer", "stop")
OTHER_MEMBER = bearer("member", "user_b2", "org_b")


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr("app.routes.cos.now", lambda: NOW)

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(queue, "enqueue_place_calls", noop)


@pytest.fixture
def a(session):
    """Tenant A (org_1) with one object of every kind."""
    key = mkkey("a-agent", ALL)
    h = {"X-Agent-Key": key["key"]}
    ids = {"key": key["id"], "headers": h}
    r = client.post(f"{P}/tasks", json={"title": "Secret task", "source": "agent"}, headers=h)
    ids["task"] = r.json()["id"]
    ids["callback"] = client.post(f"{P}/callbacks", json={"minutes": 30, "reason": "a"},
                                  headers=h).json()["id"]
    ids["question"] = client.post(f"{P}/questions", json={"question": "Secret question?"},
                                  headers=h).json()["id"]
    ids["opp"] = client.post(f"{P}/opportunities", headers=h, json={
        "title": "Secret grant", "source": "web", "url": "https://a.test/x", "relevance": 50,
    }).json()["id"]
    ids["memory"] = client.post(f"{P}/memory", headers=h, json={
        "kind": "decision", "text": "Secret decision", "source": "a"}).json()["id"]
    assert client.post(f"{P}/checkins", headers=h, json={
        "kind": "morning", "started_at": NOW.isoformat(), "summary": "Secret checkin"
    }).status_code == 201
    return ids


@pytest.fixture(params=["b-agent", "b-owner"])
def b(request):
    """Tenant B principal: an agent key with every scope, or a Clerk owner of org_b."""
    if request.param == "b-owner":
        return OTHER
    return {"X-Agent-Key": mkkey("b-agent", ALL, headers=OTHER)["key"]}


def ids_of(r):
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()}


def test_lists_hide_other_tenant(a, b):
    for path in ("tasks", "calls", "opportunities", "memory", "checkins"):
        body = client.get(f"{P}/{path}", headers=b)
        if body.status_code == 403:  # a Clerk member/agent may lack the scope: still no data
            continue
        assert body.json() == [], path
    if b is OTHER:
        assert a["key"] not in ids_of(client.get(f"{P}/agent-keys", headers=b))


def test_cannot_read_or_modify_by_id(a, b):
    r = client.patch(f"{P}/tasks/{a['task']}", json={"status": "done"}, headers=b)
    assert r.status_code == 404
    r = client.patch(f"{P}/opportunities/{a['opp']}", json={"status": "dismissed"}, headers=b)
    assert r.status_code == 404
    r = client.get(f"{P}/questions/{a['question']}", headers=b)
    assert r.status_code == 404
    if b is OTHER:
        assert client.post(f"{P}/agent-keys/{a['key']}/revoke", headers=b).status_code == 404
    # nothing of A changed, and A's own key still works
    ah = a["headers"]
    task = client.get(f"{P}/tasks", headers=ah).json()[0]
    assert task["status"] == "pending" and task["title"] == "Secret task"
    assert client.get(f"{P}/opportunities", headers=ah).json()[0]["status"] == "new"
    assert client.get(f"{P}/questions/{a['question']}", headers=ah).status_code == 200


def test_cannot_cancel_or_move_other_tenants_callback(session, a, b):
    assert client.delete(f"{P}/callbacks/pending", headers=b).status_code in (403, 404)
    mine = client.post(f"{P}/callbacks", json={"minutes": 60}, headers=b)
    if mine.status_code in (200, 201):  # B gets its own row, never A's
        assert mine.json()["id"] != a["callback"] and mine.json()["moved"] is False
    rows = list(session.scalars(select(ScheduledCall).where(ScheduledCall.id == a["callback"])))
    assert len(rows) == 1 and rows[0].status == CallStatus.PENDING and rows[0].reason == "a"


def test_cannot_answer_other_tenants_question(session, a, b):
    q = session.get(Question, uuid.UUID(a["question"]))
    q.status = QuestionStatus.CALLING  # A's owner is on the phone right now
    session.flush()
    r = client.post(f"{P}/questions/answer", json={"answer": "forged"}, headers=b)
    assert r.status_code in (403, 404)
    session.refresh(q)
    assert q.status == QuestionStatus.CALLING and q.answer is None


def test_stop_in_b_does_not_stop_a(a, b):
    r = client.post(f"{P}/stop", headers=b)
    if r.status_code == 200:
        assert client.get(f"{P}/settings", headers=a["headers"]).json()["stopped"] is False
        assert client.post(f"{P}/tasks", headers=a["headers"],
                           json={"title": "still works", "source": "agent"}).status_code == 201


def test_no_dedup_oracle_across_tenants(a, b):
    """The same title / url / text in B is a fresh object, never 'duplicate' of A's."""
    task = client.post(f"{P}/tasks", headers=b, json={"title": "secret  TASK", "source": "agent"})
    opp = client.post(f"{P}/opportunities", headers=b, json={
        "title": "x", "source": "w", "url": "https://a.test/x", "relevance": 1})
    mem = client.post(f"{P}/memory", headers=b, json={
        "kind": "decision", "text": "secret decision", "source": "b"})
    for r, key in ((task, "task"), (opp, "opp"), (mem, "memory")):
        if r.status_code in (200, 201):
            assert r.status_code == 201 and r.json()["duplicate"] is False
            assert r.json()["id"] != a[key]
        else:
            assert r.status_code == 403  # no scope: not a 200 duplicate leak either


def test_clerk_member_of_b_reads_nothing_of_a(a):
    for path in ("tasks", "opportunities", "memory"):
        r = client.get(f"{P}/{path}", headers=OTHER_MEMBER)
        assert r.status_code == 200 and r.json() == [], path
    for path in ("calls", "checkins"):  # owner-only, and B's owner sees none of A's
        assert client.get(f"{P}/{path}", headers=OTHER_MEMBER).status_code == 403, path
        r = client.get(f"{P}/{path}", headers=OTHER)
        assert r.status_code == 200 and r.json() == [], path
    r = client.patch(f"{P}/tasks/{a['task']}", json={"status": "dropped"}, headers=OTHER_MEMBER)
    assert r.status_code == 404
    assert client.get(f"{P}/agent-keys", headers=OTHER_MEMBER).status_code == 403


def test_agent_key_is_pinned_to_its_tenant(a):
    """Tenant ids are never taken from the request: extra fields change nothing."""
    other = client.get("/v1/me", headers=OTHER).json()["tenant"]["id"]
    h = {"X-Agent-Key": mkkey("pinned", ("tasks:write", "tasks:read"))["key"]}
    r = client.post(f"{P}/tasks", headers=h | {"X-Tenant-Id": other}, json={
        "title": "pinned", "source": "agent", "tenant_id": other})
    assert r.status_code == 201
    assert [t["title"] for t in client.get(f"{P}/tasks", headers=OTHER).json()] == []
