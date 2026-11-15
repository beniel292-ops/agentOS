"""One-time owner sign-in for read-only Google Drive (loopback OAuth + PKCE).

Usage: make drive-auth   (needs secrets/google-oauth-client.json, a "Desktop app" client)
Writes the refresh token to secrets/google-drive-token.json (0600). Prints no secrets.
"""
import base64
import hashlib
import json
import os
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.config import settings
from app.drive import SCOPE


def main() -> None:
    try:
        client = json.loads(settings.google_oauth_client_path.read_text())["installed"]
    except (OSError, ValueError, KeyError):
        sys.exit(f"Missing or invalid {settings.google_oauth_client_path}")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    state = secrets.token_urlsafe(24)
    got: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            if "state" in q:
                got.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"You can close this tab.")

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "code_challenge": challenge.decode(), "code_challenge_method": "S256",
        "state": state, "access_type": "offline", "prompt": "consent",
    })
    print("Open this link to sign in (it only works while this command is running):\n" + url,
          flush=True)
    webbrowser.open(url)
    while "state" not in got:
        server.handle_request()
    server.server_close()
    if not secrets.compare_digest(got["state"], state) or "code" not in got:
        sys.exit("Sign-in failed.")
    r = httpx.post(client["token_uri"], timeout=20, data={
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": redirect,
        "client_id": client["client_id"], "client_secret": client["client_secret"],
        "code_verifier": verifier,
    })
    refresh = r.json().get("refresh_token") if r.status_code == 200 else None
    if not refresh:
        sys.exit("Sign-in failed (no refresh token).")
    path = settings.google_drive_token_path
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"refresh_token": refresh}, f)
    os.chmod(path, 0o600)
    print("Drive connected (read-only).")


if __name__ == "__main__":
    main()
