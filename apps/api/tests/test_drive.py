import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import drive
from app.config import settings
from app.main import app
from app.models import AuditEvent, Tenant
from tests.test_cos import MEMBER, OTHER, OWNER, agent

DOC = "application/vnd.google-apps.document"
FID = "abcdefghij1234567890"


@pytest.fixture
def files(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"installed": {
        "client_id": "cid", "client_secret": "csec", "token_uri": "https://oauth.test/token"}}))
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"refresh_token": "rt"}))
    monkeypatch.setattr(settings, "google_oauth_client_path", client)
    monkeypatch.setattr(settings, "google_drive_token_path", token)
    drive._token.update(value=None, exp=0.0)
    return client, token


def transport(handler, calls=None):
    def inner(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth.test":
            if calls is not None:
                calls.append("token")
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if calls is not None:
            calls.append(str(request.url))
        return handler(request)

    return httpx.MockTransport(inner)


def q(request):
    return parse_qs(request.url.query.decode())


def test_search_escapes_query_and_clamps(files):
    seen = {}

    def h(r):
        seen.update(q(r))
        assert r.headers["authorization"] == "Bearer at"
        return httpx.Response(200, json={"files": [{"id": "1"}]})

    assert drive.search("it's a \\ test", 99, transport(h)) == [{"id": "1"}]
    assert seen["q"] == ["fullText contains 'it\\'s a \\\\ test' and trashed = false"]
    assert seen["pageSize"] == ["25"]
    assert "orderBy" not in seen


def test_recent_filter(files):
    seen = {}

    def h(r):
        seen.update(q(r))
        return httpx.Response(200, json={"files": []})

    drive.recent(999, 999, transport(h))
    assert seen["q"][0].startswith("'me' in owners and trashed = false and modifiedTime > '")
    assert seen["q"][0].endswith("Z'")
    assert seen["orderBy"] == ["modifiedTime desc"]
    assert seen["pageSize"] == ["50"]


def meta_handler(meta, body="hello"):
    def h(r):
        p = q(r)
        if r.url.path.endswith("/export"):
            return httpx.Response(200, text=body)
        if p.get("alt") == ["media"]:
            return httpx.Response(200, text=body)
        return httpx.Response(200, json={"id": FID, "name": "n", **meta})

    return h


def test_read_doc_export(files):
    calls = []
    t = transport(meta_handler({"mimeType": DOC}, "doc text"), calls)
    out = drive.read_text(FID, transport=t)
    assert out["text"] == "doc text" and out["truncated"] is False
    assert any("/export" in c and "text%2Fplain" in c for c in calls)


def test_read_plain_file_and_big_plain_unsupported(files):
    out = drive.read_text(FID, transport=transport(meta_handler({"mimeType": "text/markdown"})))
    assert out["text"] == "hello"
    big = {"mimeType": "text/plain", "size": str(3 * 1024 * 1024)}
    out = drive.read_text(FID, transport=transport(meta_handler(big)))
    assert out["text"] is None and out["unsupported"] is True


def test_read_pdf_unsupported(files):
    out = drive.read_text(FID, transport=transport(meta_handler({"mimeType": "application/pdf"})))
    assert out["text"] is None and out["unsupported"] is True and out["id"] == FID


def test_read_truncates(files):
    t = transport(meta_handler({"mimeType": DOC}, "x" * 5000))
    out = drive.read_text(FID, max_chars=1, transport=t)  # clamped up to 1000
    assert len(out["text"]) == 1000 and out["truncated"] is True


@pytest.mark.parametrize("bad", ["short", "../../etc/passwd/xx", "a" * 201, "ab cdefghijk"])
def test_read_bad_id(files, bad):
    with pytest.raises(ValueError):
        drive.read_text(bad, transport=transport(meta_handler({})))


def test_token_cached_and_errors_are_generic(files):
    calls = []
    t = transport(lambda r: httpx.Response(200, json={"files": []}), calls)
    drive.search("a", transport=t)
    drive.search("b", transport=t)
    assert calls.count("token") == 1
    drive._token.update(value=None, exp=0.0)

    def boom(r):
        return httpx.Response(500, text="secret-body")

    with pytest.raises(drive.DriveError) as e:
        drive.search("a", transport=transport(boom))
    assert "secret" not in str(e.value)


def test_token_failure_is_drive_error(files):
    t = httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(drive.DriveError):
        drive.search("a", transport=t)


def test_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_path", tmp_path / "nope.json")
    assert drive.configured() is False


# ---------------------------------------------------------------- routes

client = TestClient(app)
P = "/v1/cos/drive"


@pytest.fixture
def owner_tenant(session, monkeypatch, clerk):
    client.get("/v1/cos/tasks", headers=OWNER)  # creates the org's tenant
    t = session.scalars(select(Tenant).where(Tenant.clerk_org_id == "org_1")).one()
    monkeypatch.setattr(settings, "telegram_owner_tenant_id", t.id)
    return t


@pytest.fixture
def fake_drive(monkeypatch, files):
    monkeypatch.setattr(drive, "search", lambda text, limit=10: [{"id": "a"}])
    monkeypatch.setattr(drive, "recent", lambda days=7, limit=20: [{"id": "a"}, {"id": "b"}])
    monkeypatch.setattr(drive, "read_text", lambda fid, mc=20000: {"id": fid, "text": "hi"})


def test_routes_work_and_audit(session, owner_tenant, fake_drive):
    r = client.get(f"{P}/search", params={"q": "secret plan"}, headers=OWNER)
    assert r.status_code == 200 and r.json() == {"files": [{"id": "a"}], "untrusted": True}
    r = client.get(f"{P}/recent", params={"days": 3}, headers=OWNER)
    assert len(r.json()["files"]) == 2
    r = client.get(f"{P}/files/{FID}/text", headers=OWNER)
    assert r.json()["text"] == "hi" and r.json()["untrusted"] is True
    ev = {e.action: e for e in session.scalars(
        select(AuditEvent).where(AuditEvent.action.like("drive_%")))}
    assert ev["drive_search"].data == {"count": 1}
    assert ev["drive_recent"].data == {"days": 3, "count": 2}
    assert ev["drive_read"].data == {"id": FID}
    assert "secret plan" not in json.dumps([e.data for e in ev.values()])
    assert all(e.tenant_id == owner_tenant.id and e.lead_id is None for e in ev.values())


def test_agent_scope_enforced(owner_tenant, fake_drive):
    no = agent("nodrive", ("tasks:read",))
    assert client.get(f"{P}/search", params={"q": "x"}, headers=no).status_code == 403
    yes = agent("withdrive", ("drive:read",))
    assert client.get(f"{P}/search", params={"q": "x"}, headers=yes).status_code == 200


def test_member_cannot_use(owner_tenant, fake_drive):
    assert client.get(f"{P}/search", params={"q": "x"}, headers=MEMBER).status_code == 403


def test_other_tenant_404(owner_tenant, fake_drive):
    assert client.get(f"{P}/search", params={"q": "x"}, headers=OTHER).status_code == 404
    assert client.get(f"{P}/recent", headers=OTHER).status_code == 404


def test_not_configured_503(owner_tenant, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_path", tmp_path / "none.json")
    r = client.get(f"{P}/search", params={"q": "x"}, headers=OWNER)
    assert r.status_code == 503 and r.json() == {"detail": "Drive not connected"}


def test_errors_map(owner_tenant, fake_drive, monkeypatch):
    def boom(*a, **k):
        raise drive.DriveError("x")

    monkeypatch.setattr(drive, "search", boom)
    assert client.get(f"{P}/search", params={"q": "x"}, headers=OWNER).status_code == 502


def test_bad_id_is_422(owner_tenant, files):
    assert client.get(f"{P}/files/bad/text", headers=OWNER).status_code == 422
    assert client.get(f"{P}/search", params={"q": ""}, headers=OWNER).status_code == 422
