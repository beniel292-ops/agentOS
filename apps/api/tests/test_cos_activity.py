import uuid

import pytest
from fastapi.testclient import TestClient

from app.cos_auth import SCOPES
from app.main import app
from app.models import AuditEvent
from tests.test_cos import MEMBER, OTHER, OWNER, agent  # noqa: F401

pytestmark = pytest.mark.usefixtures("clerk")
client = TestClient(app)
URL = "/v1/cos/activity"


def tenant_of(h):
    return client.get("/v1/me", headers=h).json()["tenant"]["id"]


def ev(session, tenant, action="task_created", actor="user:1", lead_id=None, **data):
    e = AuditEvent(tenant_id=tenant, lead_id=lead_id, actor=actor, action=action, data=data)
    session.add(e)
    session.flush()
    return e.id


def ids(r):
    assert r.status_code == 200, r.text
    return [x["id"] for x in r.json()]


def test_owner_sees_own_tenant_newest_first(session):
    t = tenant_of(OWNER)
    a = ev(session, t, k=1)
    b = ev(session, t, actor="agent:hermes")
    r = client.get(URL, headers=OWNER)
    assert ids(r) == [b, a]
    assert set(r.json()[0]) == {"id", "actor", "action", "data", "at"}
    assert r.json()[1]["data"] == {"k": 1}


def test_permissions():
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers=MEMBER).status_code == 403
    assert client.get(URL, headers=agent("god", sorted(SCOPES))).status_code == 403


def test_other_tenant_and_lead_events_excluded(session):
    t = tenant_of(OWNER)
    other = tenant_of(OTHER)
    ev(session, other)
    ev(session, t, lead_id=uuid.uuid4())
    mine = ev(session, t)
    assert ids(client.get(URL, headers=OWNER)) == [mine]


def test_view_agents(session):
    t = tenant_of(OWNER)
    ev(session, t, actor="user:1")
    a = ev(session, t, actor="agent:voice")
    assert ids(client.get(URL, params={"view": "agents"}, headers=OWNER)) == [a]


def test_view_security(session):
    t = tenant_of(OWNER)
    ev(session, t, action="task_created")
    s1 = ev(session, t, action="cos_stopped")
    s2 = ev(session, t, action="agent_key_created", actor="agent:x")
    assert ids(client.get(URL, params={"view": "security"}, headers=OWNER)) == [s2, s1]


def test_bad_view_and_limits():
    assert client.get(URL, params={"view": "x"}, headers=OWNER).status_code == 422
    assert client.get(URL, params={"limit": 0}, headers=OWNER).status_code == 422
    assert client.get(URL, params={"limit": 201}, headers=OWNER).status_code == 422


def test_paging(session):
    t = tenant_of(OWNER)
    made = [ev(session, t) for _ in range(5)]
    first = ids(client.get(URL, params={"limit": 2}, headers=OWNER))
    assert first == [made[4], made[3]]
    second = ids(client.get(URL, params={"limit": 2, "before": first[-1]}, headers=OWNER))
    assert second == [made[2], made[1]]
    rest = ids(client.get(URL, params={"before": second[-1]}, headers=OWNER))
    assert rest == [made[0]]


def test_works_while_stopped(session):
    t = tenant_of(OWNER)
    assert client.post("/v1/cos/stop", headers=OWNER).status_code == 200
    r = client.get(URL, headers=OWNER)
    assert r.status_code == 200 and r.json()[0]["action"] == "cos_stopped"
    assert t
