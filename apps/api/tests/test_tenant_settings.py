import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import AuditEvent, Tenant
from tests.auth_helpers import token

pytestmark = pytest.mark.usefixtures("clerk")
client = TestClient(app)

BODY = {"agency_facts": "SEO from 25k/month", "tone": "warm"}


def auth(**kw):
    return {"Authorization": f"Bearer {token(**kw)}"}


def test_get_defaults():
    r = client.get("/v1/tenant/settings", headers=auth(role="member"))
    assert r.status_code == 200
    assert r.json() == {
        "agency_facts": "No details provided yet.",
        "tone": "friendly and professional",
    }


def test_owner_put_then_get_and_audit(session):
    r = client.put("/v1/tenant/settings", json=BODY, headers=auth())
    assert r.status_code == 200 and r.json() == BODY
    assert client.get("/v1/tenant/settings", headers=auth(role="member")).json() == BODY
    ev = session.scalars(select(AuditEvent).where(AuditEvent.action == "settings_updated")).one()
    assert ev.lead_id is None and ev.actor.startswith("user:")
    assert ev.data == {"fields": ["agency_facts", "tone"]}
    assert "SEO" not in str(ev.data)


def test_member_forbidden():
    client.get("/v1/me", headers=auth())  # provision tenant
    r = client.put("/v1/tenant/settings", json=BODY, headers=auth(role="member", sub="u2"))
    assert r.status_code == 403


def test_other_keys_preserved(session):
    client.get("/v1/me", headers=auth())
    tenant = session.scalars(select(Tenant)).one()
    tenant.settings = {"other": 1}
    session.flush()
    assert client.put("/v1/tenant/settings", json=BODY, headers=auth()).status_code == 200
    session.flush()
    session.refresh(tenant)
    assert tenant.settings == {"other": 1, **BODY}


def test_validation_limits():
    r = client.put("/v1/tenant/settings", json=BODY | {"tone": "x" * 201}, headers=auth())
    assert r.status_code == 422
