"""Security: authorization matrix for the Chief of Staff API (/v1/cos) and lead routes."""
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.cos_auth import MEMBER_SCOPES, SCOPES
from app.intake import sha256_hex
from app.main import app
from app.models import AgentKey
from tests.test_cos import MEMBER, NOW, OWNER, P, mkkey

pytestmark = pytest.mark.usefixtures("clerk")
client = TestClient(app)

# the real production scope sets
VOICE = {"tasks:read", "tasks:write", "callbacks:write", "stop", "questions:answer"}
OPENCLAW = {"callbacks:write", "calls:request", "memory:read", "memory:write",
            "opportunities:read", "stop", "tasks:read", "tasks:write", "drive:read"}
HERMES = {"calls:request", "memory:read", "opportunities:read", "opportunities:write",
          "tasks:read", "drive:read"}
KEYS = {"voice": VOICE, "openclaw": OPENCLAW, "hermes": HERMES}

OWNER_ONLY = "owner"
FID = "abcdefghij1234"
# (method, path template) -> scope required, or OWNER_ONLY (Clerk owner, never an agent key).
# Pinned by hand so that a new /v1/cos route fails test_table_matches_app until it is classified.
ROUTES = {
    ("POST", "/agent-keys"): OWNER_ONLY,
    ("GET", "/agent-keys"): OWNER_ONLY,
    ("POST", "/agent-keys/{key_id}/revoke"): OWNER_ONLY,
    ("GET", "/settings"): "tasks:read",
    ("PUT", "/settings"): OWNER_ONLY,
    ("POST", "/stop"): "stop",
    ("POST", "/resume"): OWNER_ONLY,
    ("GET", "/tasks"): "tasks:read",
    ("POST", "/tasks"): "tasks:write",
    ("PATCH", "/tasks/{task_id}"): "tasks:write",
    ("POST", "/callbacks"): "callbacks:write",
    ("DELETE", "/callbacks/pending"): "callbacks:write",
    ("GET", "/calls"): "checkins:read",
    ("POST", "/calls/now"): "calls:request",
    ("POST", "/questions"): "calls:request",
    ("POST", "/questions/answer"): "questions:answer",
    ("GET", "/questions/{question_id}"): "calls:request",
    ("POST", "/checkins"): "checkins:write",
    ("GET", "/checkins"): "checkins:read",
    ("POST", "/opportunities"): "opportunities:write",
    ("GET", "/opportunities"): "opportunities:read",
    ("PATCH", "/opportunities/{opp_id}"): "opportunities:write",
    ("POST", "/memory"): "memory:write",
    ("GET", "/memory"): "memory:read",
    ("GET", "/drive/search"): "drive:read",
    ("GET", "/drive/recent"): "drive:read",
    ("GET", "/drive/files/{file_id}/text"): "drive:read",
}
# Routes intentionally reachable by an agent key with ZERO scopes. Must stay empty.
OPEN_WITHOUT_SCOPE: set[tuple[str, str]] = set()


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr("app.routes.cos.now", lambda: NOW)


def app_routes(prefix: str | None = None):
    # the OpenAPI schema lists every route, however the routers are nested
    for path, ops in app.openapi()["paths"].items():
        if prefix is None or path.startswith(prefix):
            for method in sorted(ops):
                yield method.upper(), path


def fill(path: str) -> str:
    path = re.sub(r"\{(key_id|task_id|opp_id|question_id)\}", str(uuid.uuid4()), path)
    return path.replace("{file_id}", FID).replace("{lead_id}", str(uuid.uuid4()))


def call(method: str, path: str, headers: dict | None = None):
    # `{}` is a body that fails validation: if auth passed we see 4xx, never a real write
    return client.request(method, fill(path), headers=headers or {}, json={})


def db_key(session, scopes, name="k", revoked=False) -> dict:
    """An agent key written straight to the DB (the API refuses keys with no scopes)."""
    tenant_id = client.get("/v1/me", headers=OWNER).json()["tenant"]["id"]
    plain = "cos_" + uuid.uuid4().hex
    session.add(AgentKey(tenant_id=tenant_id, name=name, scopes=sorted(scopes),
                         key_hash=sha256_hex(plain), key_prefix=plain[:12],
                         revoked_at=NOW if revoked else None))
    session.flush()
    return {"X-Agent-Key": plain}


COS = [(m, p.removeprefix(P)) for m, p in app_routes(P)]


def test_table_matches_app():
    assert sorted(COS) == sorted(ROUTES), "classify new /v1/cos routes in ROUTES"
    assert all(s == OWNER_ONLY or s in SCOPES for s in ROUTES.values())


@pytest.mark.parametrize("method,path", COS)
def test_no_credentials_rejected(method, path):
    assert call(method, P + path).status_code in (401, 403)


@pytest.mark.parametrize("bad", ["cos_garbage", "", " ", "cos_", "' OR 1=1 --", "x" * 5000])
@pytest.mark.parametrize("method,path", COS)
def test_garbage_agent_key_is_401(method, path, bad):
    assert call(method, P + path, {"X-Agent-Key": bad}).status_code == 401


@pytest.mark.parametrize("method,path", COS)
def test_revoked_agent_key_is_401(session, method, path):
    h = db_key(session, SCOPES, revoked=True)
    assert call(method, P + path, h).status_code == 401


def test_key_prefix_and_hash_are_not_credentials():
    k = mkkey("pfx", ("tasks:read",))
    assert client.get(f"{P}/tasks", headers={"X-Agent-Key": k["key_prefix"]}).status_code == 401
    # the stored value is sha256(key): presenting the hash must not work either
    assert client.get(
        f"{P}/tasks", headers={"X-Agent-Key": sha256_hex(k["key"])}
    ).status_code == 401
    assert client.get(f"{P}/tasks", headers={"X-Agent-Key": k["key"]}).status_code == 200


def test_key_listing_never_leaks_secrets():
    mkkey("leak", ("tasks:read",))
    rows = client.get(f"{P}/agent-keys", headers=OWNER).json()
    assert rows and all("key" not in r and "key_hash" not in r for r in rows)


@pytest.mark.parametrize("method,path", COS)
def test_zero_scope_key_reaches_nothing(session, method, path):
    h = db_key(session, set())
    r = call(method, P + path, h)
    if (method, path) in OPEN_WITHOUT_SCOPE:
        assert r.status_code != 403
    else:
        assert r.status_code == 403


@pytest.mark.parametrize("who", KEYS)
def test_scope_matrix(session, who):
    """Each route is refused (403) exactly when the key lacks its scope; owner routes always."""
    h = db_key(session, KEYS[who], name=who)
    wrong = []
    for (method, path), need in ROUTES.items():
        status = call(method, P + path, h).status_code
        forbidden = need == OWNER_ONLY or need not in KEYS[who]
        if (status == 403) != forbidden or status in (401, 500):
            wrong.append((method, path, status, "forbidden" if forbidden else "allowed"))
    assert not wrong


# The brief's explicit expectations, spelled out so a change to ROUTES cannot hide a regression.
FORBIDDEN = [
    ("voice", "POST", "/calls/now"), ("voice", "POST", "/questions"),
    ("voice", "GET", "/questions/{question_id}"), ("voice", "GET", "/drive/search"),
    ("voice", "GET", "/drive/recent"), ("voice", "GET", "/drive/files/{file_id}/text"),
    ("voice", "POST", "/opportunities"), ("voice", "GET", "/opportunities"),
    ("hermes", "POST", "/tasks"), ("hermes", "PATCH", "/tasks/{task_id}"),
    ("hermes", "POST", "/stop"), ("hermes", "POST", "/questions/answer"),
    ("openclaw", "POST", "/questions/answer"), ("openclaw", "POST", "/opportunities"),
    ("openclaw", "PATCH", "/opportunities/{opp_id}"),
    *[(who, m, p) for who in KEYS for (m, p), need in ROUTES.items() if need == OWNER_ONLY],
    # call transcripts and notes: nobody but the owner, whatever else the key may read
    *[(who, "GET", p) for who in KEYS for p in ("/checkins", "/calls")],
]


@pytest.mark.parametrize("who,method,path", FORBIDDEN)
def test_explicitly_forbidden(session, who, method, path):
    h = db_key(session, KEYS[who], name=who)
    assert call(method, P + path, h).status_code == 403


def test_transcript_routes_are_owner_only():
    assert all("checkins:read" not in scopes for scopes in KEYS.values())
    assert "checkins:read" in SCOPES and "checkins:read" not in MEMBER_SCOPES
    assert "drive:read" not in MEMBER_SCOPES
    for path in ("checkins", "calls"):
        assert client.get(f"{P}/{path}", headers=OWNER).status_code == 200
        assert client.get(f"{P}/{path}", headers=MEMBER).status_code == 403


def test_agents_hold_no_owner_or_unknown_scopes():
    for scopes in KEYS.values():
        assert scopes <= SCOPES


# ---------------------------------------------------------------- everything outside /v1/cos

NON_COS = [(m, p) for m, p in app_routes() if not p.startswith(P) and p != "/health"]


def test_lead_routes_exist():
    paths = {p for _, p in NON_COS}
    assert "/v1/leads/{lead_id}/retry" in paths and "/v1/leads/{lead_id}/draft-anyway" in paths


@pytest.mark.parametrize("method,path", NON_COS)
def test_agent_key_is_useless_outside_cos(session, method, path):
    """Agents may never retry/draft/approve leads, edit agency settings, or use intake keys.
    Even a key holding every scope gets nothing: these routes only accept a Clerk session."""
    h = db_key(session, SCOPES, name="god")
    r = call(method, path, h)
    assert r.status_code in (401, 403, 404, 422) and not r.is_success
    assert r.status_code != 422 or path == "/v1/leads"  # public intake: 422/401 on its own key
    if path != "/v1/leads":
        assert r.status_code == 401


def test_agent_key_cannot_read_leads_even_with_bearer_missing(session):
    h = db_key(session, SCOPES, name="god")
    assert client.get("/v1/leads", headers=h).status_code == 401
    assert client.get("/v1/me", headers=h).status_code == 401


# ---------------------------------------------------------------- infrastructure


def test_redis_needs_a_password_and_the_app_sends_it(monkeypatch):
    from pathlib import Path

    from app.config import settings
    from app.jobs import queue

    root = Path(__file__).resolve().parents[3]
    compose = (root / "docker-compose.yml").read_text()
    assert "--requirepass" in compose and "REDIS_PASSWORD" in compose
    example = (root / ".env.example").read_text()
    assert "\nREDIS_PASSWORD=\n" in example and "VOICE_MCP_TOKEN=\n" in example
    monkeypatch.setattr(settings, "redis_url", "redis://:s3cret@localhost:6380/0")
    assert queue._redis_settings().password == "s3cret"
