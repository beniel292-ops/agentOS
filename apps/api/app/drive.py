"""Read-only Google Drive client (drive.readonly scope only, httpx only).

The owner signs in once with scripts/drive_auth.py. Nothing here logs or puts tokens, queries
or file contents in an error message.
"""
import json
import re
import time
from datetime import UTC, datetime, timedelta

import httpx

from app.config import settings

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
API = "https://www.googleapis.com/drive/v3/files"
LIST_FIELDS = "files(id,name,mimeType,modifiedTime,webViewLink,owners(displayName))"
META_FIELDS = "id,name,mimeType,modifiedTime,webViewLink,size"
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
MAX_PLAIN_BYTES = 2 * 1024 * 1024
EXPORTS = {  # Google-native type -> export mime type
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

_token: dict = {"value": None, "exp": 0.0}


class DriveError(Exception):
    pass


def configured() -> bool:
    return (settings.google_oauth_client_path.is_file()
            and settings.google_drive_token_path.is_file())


def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def _access_token(http: httpx.Client) -> str:
    if _token["value"] and time.monotonic() < _token["exp"]:
        return _token["value"]
    try:
        client = json.loads(settings.google_oauth_client_path.read_text())["installed"]
        refresh = json.loads(settings.google_drive_token_path.read_text())["refresh_token"]
        r = http.post(client["token_uri"], data={
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": client["client_id"], "client_secret": client["client_secret"],
        })
        r.raise_for_status()
        body = r.json()
        _token["value"] = body["access_token"]
        _token["exp"] = time.monotonic() + int(body.get("expires_in", 3600)) - 60
    except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
        _token["value"] = None
        raise DriveError("Drive authorisation failed") from None
    return _token["value"]


def _get(http: httpx.Client, url: str, params: dict) -> httpx.Response:
    try:
        r = http.get(url, params=params,
                     headers={"Authorization": f"Bearer {_access_token(http)}"})
        r.raise_for_status()
        return r
    except httpx.HTTPError:
        raise DriveError("Drive request failed") from None


def _list(params: dict, transport: httpx.BaseTransport | None) -> list[dict]:
    with httpx.Client(transport=transport, timeout=20) as http:
        r = _get(http, API, {**params, "fields": LIST_FIELDS})
    try:
        return r.json()["files"]
    except (ValueError, KeyError, TypeError):
        raise DriveError("Drive response invalid") from None


def search(text: str, limit: int = 10, transport: httpx.BaseTransport | None = None) -> list[dict]:
    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    return _list({
        "q": f"fullText contains '{escaped}' and trashed = false",
        "pageSize": _clamp(limit, 1, 25),  # no orderBy: Drive rejects it with fullText
    }, transport)


def recent(
    days: int = 7, limit: int = 20, transport: httpx.BaseTransport | None = None
) -> list[dict]:
    since = datetime.now(UTC) - timedelta(days=_clamp(days, 1, 30))
    return _list({
        "q": f"'me' in owners and trashed = false and modifiedTime > "
             f"'{since.strftime('%Y-%m-%dT%H:%M:%SZ')}'",
        "orderBy": "modifiedTime desc",
        "pageSize": _clamp(limit, 1, 50),
    }, transport)


def read_text(
    file_id: str, max_chars: int = 20000, transport: httpx.BaseTransport | None = None
) -> dict:
    if not FILE_ID.fullmatch(file_id):
        raise ValueError("invalid file id")
    max_chars = _clamp(max_chars, 1000, 50000)
    url = f"{API}/{file_id}"
    with httpx.Client(transport=transport, timeout=20) as http:
        try:
            meta = _get(http, url, {"fields": META_FIELDS}).json()
            mime = meta["mimeType"]
            size = int(meta.get("size", 0))
        except (ValueError, KeyError, TypeError):
            raise DriveError("Drive response invalid") from None
        out = {k: meta.get(k) for k in ("id", "name", "mimeType", "modifiedTime", "webViewLink")}
        if mime in EXPORTS:
            text = _get(http, f"{url}/export", {"mimeType": EXPORTS[mime]}).text
        elif (mime.startswith("text/") or mime == "application/json") and size <= MAX_PLAIN_BYTES:
            text = _get(http, url, {"alt": "media"}).text
        else:
            return {**out, "text": None, "truncated": False, "unsupported": True}
    return {**out, "text": text[:max_chars], "truncated": len(text) > max_chars}
