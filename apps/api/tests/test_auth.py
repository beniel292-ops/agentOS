import base64
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import auth
from app.config import settings
from app.db import get_db
from app.main import app
from app.models import Lead, Tenant, User

ISSUER = "https://test-app-1.clerk.accounts.dev"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeSigningKey:
    key = KEY.public_key()


@pytest.fixture(autouse=True)
def clerk(monkeypatch, session):
    pk = "pk_test_" + base64.b64encode(b"test-app-1.clerk.accounts.dev$").decode()
    monkeypatch.setattr(settings, "clerk_publishable_key", pk)
    monkeypatch.setattr(settings, "clerk_authorized_parties", ["http://localhost:3000"])
    # real verification logic runs; only the JWKS network fetch is replaced
    monkeypatch.setattr(
        auth._jwks_client(), "get_signing_key_from_jwt", lambda token: FakeSigningKey()
    )
    app.dependency_overrides[get_db] = lambda: session
    yield
    app.dependency_overrides.clear()


client = TestClient(app)


def token(*, key=KEY, org="v2", role="admin", sub="user_1", **over) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": sub, "iat": now, "nbf": now - 5, "exp": now + 60,
              "azp": "http://localhost:3000"}
    if org == "v2":
        claims |= {"v": 2, "o": {"id": "org_1", "rol": role, "slg": "acme"}}
    elif org == "v1":
        claims |= {"org_id": "org_1", "org_role": f"org:{role}", "org_slug": "acme"}
    claims |= over
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


def get(path, tok=None):
    return client.get(path, headers={"Authorization": f"Bearer {tok}"} if tok else {})


def test_settings_derive_issuer(monkeypatch):
    assert settings.clerk_issuer == ISSUER
    assert settings.clerk_jwks_url == ISSUER + "/.well-known/jwks.json"


def test_no_token():
    assert get("/v1/me").status_code == 401


@pytest.mark.parametrize(
    "tok",
    [
        token(key=OTHER_KEY),
        token(exp=int(time.time()) - 3600),
        token(iss="https://evil.example.com"),
        token(azp="https://evil.example.com"),
        "garbage",
    ],
    ids=["bad-signature", "expired", "wrong-issuer", "wrong-azp", "garbage"],
)
def test_invalid_token(tok):
    assert get("/v1/me", tok).status_code == 401


def test_no_org_is_403():
    r = get("/v1/me", token(org=None))
    assert r.status_code == 403
    assert r.json() == {"detail": "Select or create an organization"}


def test_missing_azp_is_allowed():
    assert get("/v1/me", token(azp=None)).status_code == 200


def test_first_request_provisions_owner(session):
    r = get("/v1/me", token())
    assert r.status_code == 200
    body = r.json()
    assert body["tenant"]["name"] == "acme"
    assert body["user"]["role"] == "owner"
    tenant = session.scalars(select(Tenant).where(Tenant.clerk_org_id == "org_1")).one()
    assert str(tenant.id) == body["tenant"]["id"]


def test_member_role():
    assert get("/v1/me", token(role="member")).json()["user"]["role"] == "member"


def test_second_request_reuses_and_role_updates(session):
    first = get("/v1/me", token(role="member")).json()
    second = get("/v1/me", token(role="admin")).json()
    assert first["tenant"]["id"] == second["tenant"]["id"]
    assert first["user"]["id"] == second["user"]["id"]
    assert second["user"]["role"] == "owner"
    assert session.scalar(select(func.count()).select_from(Tenant)
                          .where(Tenant.clerk_org_id == "org_1")) == 1
    assert session.scalar(select(func.count()).select_from(User)
                          .where(User.clerk_user_id == "user_1")) == 1


def test_v1_claims():
    r = get("/v1/me", token(org="v1", role="admin"))
    assert r.status_code == 200 and r.json()["user"]["role"] == "owner"
    r = get("/v1/me", token(org="v1", role="member", sub="user_2"))
    assert r.json()["user"]["role"] == "member"


def _lead(session, tenant_id) -> Lead:
    lead = Lead(tenant_id=tenant_id, source="web", name="Ann", email="ann@example.com",
                message="Hi", idempotency_key=str(uuid.uuid4()))
    session.add(lead)
    session.flush()
    return lead


def test_leads_are_tenant_scoped(session):
    mine = get("/v1/me", token()).json()["tenant"]["id"]
    other = Tenant(clerk_org_id="org_other", name="Other")
    session.add(other)
    session.flush()
    own_lead = _lead(session, uuid.UUID(mine))
    other_lead = _lead(session, other.id)

    r = get(f"/v1/leads/{own_lead.id}", token())
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "ann@example.com" and body["status"] == "NEW"
    assert set(body) == {"id", "name", "email", "company", "message", "source", "status",
                         "created_at"}
    assert get(f"/v1/leads/{other_lead.id}", token()).status_code == 404
    assert get(f"/v1/leads/{uuid.uuid4()}", token()).status_code == 404
    assert get(f"/v1/leads/{own_lead.id}").status_code == 401
