import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.jobs import worker
from app.main import app
from app.models import AuditEvent, Lead, Tenant
from tests.auth_helpers import token

pytestmark = pytest.mark.usefixtures("clerk")
client = TestClient(app)

LEAD = {"name": "Ann", "email": "Ann@Example.com", "company": "Acme", "message": "Need a site"}


def tok_b(role="admin"):
    return token(o={"id": "org_2", "rol": role, "slg": "b"}, sub="user_b")


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def enqueue(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr("app.routes.intake.enqueue_process_lead", mock)
    return mock


def make_key(tok=None) -> str:
    r = client.post("/v1/intake-key", headers=auth(tok or token()))
    assert r.status_code == 200
    return r.json()["key"]


def post_lead(key, body=LEAD, **headers):
    h = {"X-Intake-Key": key} if key else {}
    return client.post("/v1/leads", json=body, headers=h | headers)


def count(session, model, *where):
    return session.scalar(select(func.count()).select_from(model).where(*where))


# --- intake key ---

def test_owner_creates_key_member_forbidden(session):
    r = client.post("/v1/intake-key", headers=auth(token()))
    assert r.status_code == 200
    key = r.json()["key"]
    assert key.startswith("aos_") and r.json()["prefix"] == key[:12]
    member = auth(token(role="member", sub="u2"))
    assert client.post("/v1/intake-key", headers=member).status_code == 403


def test_db_stores_hash_not_plaintext(session):
    key = make_key()
    tenant = session.scalars(select(Tenant)).one()
    cols = [str(getattr(tenant, c.name)) for c in Tenant.__table__.columns]
    assert not any(key in v for v in cols)
    assert tenant.intake_key_hash and tenant.intake_key_prefix == key[:12]
    audit = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "intake_key_rotated")
    ).one()
    assert audit.lead_id is None and audit.actor.startswith("user:") and key not in str(audit.data)


def test_rotation_invalidates_old_key(session, enqueue):
    old = make_key()
    new = make_key()
    assert old != new
    assert post_lead(old).status_code == 401
    assert post_lead(new).status_code == 201


def test_get_intake_key_shows_prefix_only():
    assert client.get("/v1/intake-key", headers=auth(token())).json() == {"prefix": None}
    key = make_key()
    r = client.get("/v1/intake-key", headers=auth(token(role="member", sub="u2")))
    assert r.json() == {"prefix": key[:12]}


# --- public intake ---

def test_missing_or_wrong_key_is_401(session, enqueue):
    make_key()
    a, b = post_lead(None), post_lead("aos_wrong")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    enqueue.assert_not_called()


def test_valid_key_creates_lead(session, enqueue):
    key = make_key()
    r = post_lead(key)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "NEW" and body["duplicate"] is False
    lead = session.get(Lead, uuid.UUID(body["id"]))
    tenant = session.scalars(select(Tenant)).one()
    assert lead.tenant_id == tenant.id and lead.source == "api"
    events = session.scalars(select(AuditEvent).where(AuditEvent.action == "lead_created")).all()
    assert len(events) == 1
    e = events[0]
    assert (e.lead_id, e.tenant_id, e.actor, e.from_state, e.to_state, e.data) == (
        lead.id, tenant.id, "intake:api", None, "NEW", {"source": "api"})
    enqueue.assert_awaited_once_with(lead.id)


def test_same_idempotency_key_is_duplicate(session, enqueue):
    key = make_key()
    first = post_lead(key, **{"Idempotency-Key": "abc"})
    second = post_lead(key, {**LEAD, "message": "different"}, **{"Idempotency-Key": "abc"})
    assert first.status_code == 201 and second.status_code == 200
    assert second.json() == {**first.json(), "duplicate": True}
    assert count(session, Lead) == 1 and enqueue.await_count == 1
    assert count(session, AuditEvent, AuditEvent.action == "lead_created") == 1


def test_derived_key_dedupes_and_new_message_creates(session, enqueue):
    key = make_key()
    assert post_lead(key).status_code == 201
    same = post_lead(key, {**LEAD, "email": "ann@example.com", "message": "  Need a site "})
    assert same.status_code == 200 and same.json()["duplicate"] is True
    assert post_lead(key, {**LEAD, "message": "Another"}).status_code == 201
    assert count(session, Lead) == 2 and enqueue.await_count == 2


@pytest.mark.parametrize("patch", [{"email": "nope"}, {"message": ""}, {"name": ""}])
def test_invalid_body_is_422(session, enqueue, patch):
    key = make_key()
    assert post_lead(key, {**LEAD, **patch}).status_code == 422
    assert count(session, Lead) == 0


def test_key_creates_lead_in_own_tenant_only(session, enqueue):
    key_a = make_key()
    key_b = make_key(tok_b())
    lead_b = post_lead(key_b).json()["id"]
    lead_a = post_lead(key_a, {**LEAD, "message": "A"}).json()["id"]
    tenants = {t.clerk_org_id: t.id for t in session.scalars(select(Tenant))}
    assert session.get(Lead, uuid.UUID(lead_a)).tenant_id == tenants["org_1"]
    assert session.get(Lead, uuid.UUID(lead_b)).tenant_id == tenants["org_2"]
    # same idempotency key in two tenants does not collide
    assert post_lead(key_a, **{"Idempotency-Key": "k"}).status_code == 201
    assert post_lead(key_b, **{"Idempotency-Key": "k"}).status_code == 201


def test_enqueue_failure_still_succeeds(session, monkeypatch):
    # the real enqueue swallows Redis errors; make arq's pool creation fail
    async def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.jobs.queue.create_pool", boom)
    monkeypatch.setattr("app.jobs.queue._pool", None)
    key = make_key()
    r = post_lead(key)
    assert r.status_code == 201 and count(session, Lead) == 1


# --- test form ---

def test_test_lead_with_clerk_token(session, enqueue):
    r = client.post("/v1/leads/test", json=LEAD, headers=auth(token()))
    assert r.status_code == 201
    lead = session.get(Lead, uuid.UUID(r.json()["id"]))
    assert lead.source == "test_form"
    e = session.scalars(select(AuditEvent).where(AuditEvent.lead_id == lead.id)).one()
    assert e.actor.startswith("user:") and e.action == "lead_created"
    enqueue.assert_awaited_once()
    again = client.post("/v1/leads/test", json=LEAD, headers=auth(token()))
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert enqueue.await_count == 1
    assert client.post("/v1/leads/test", json=LEAD).status_code == 401


# --- list ---

def test_list_leads_scoped_newest_first_with_limit(session, enqueue):
    client.get("/v1/me", headers=auth(token()))
    client.get("/v1/me", headers=auth(tok_b()))
    t = {x.clerk_org_id: x.id for x in session.scalars(select(Tenant))}
    now = datetime.now(UTC)
    for i in range(3):
        session.add(Lead(tenant_id=t["org_1"], source="x", name=f"A{i}", email="a@e.com",
                         message="m", idempotency_key=f"a{i}", created_at=now - timedelta(hours=i)))
    session.add(Lead(tenant_id=t["org_2"], source="x", name="B", email="b@e.com",
                     message="m", idempotency_key="b"))
    session.flush()
    r = client.get("/v1/leads", headers=auth(token()))
    assert [x["name"] for x in r.json()] == ["A0", "A1", "A2"]
    r = client.get("/v1/leads?limit=2", headers=auth(token()))
    assert [x["name"] for x in r.json()] == ["A0", "A1"]
    assert client.get("/v1/leads?limit=201", headers=auth(token())).status_code == 422
    assert client.get("/v1/leads").status_code == 401


# --- sweep ---

async def test_requeue_stuck_leads(session, monkeypatch):
    tenant = Tenant(clerk_org_id="org_s", name="S")
    session.add(tenant)
    session.flush()
    now = datetime.now(UTC)

    def lead(key, age_min, status="NEW"):
        session.add(Lead(tenant_id=tenant.id, source="x", name=key, email="e@e.com", message="m",
                         idempotency_key=key, status=status,
                         created_at=now - timedelta(minutes=age_min)))

    lead("old", 5)
    lead("fresh", 1)
    lead("processing", 10, "PROCESSING")
    session.flush()
    old_id = session.scalar(select(Lead.id).where(Lead.idempotency_key == "old"))
    monkeypatch.setattr(worker, "SessionLocal", lambda: nullcontext(session))
    redis = AsyncMock()
    await worker.requeue_stuck_leads({"redis": redis})
    redis.enqueue_job.assert_awaited_once_with(
        "process_lead", str(old_id), _job_id=f"process_lead:{old_id}")
