"""Send one email through Resend. Errors never contain the API key or the message body."""
import httpx

from app.config import settings

URL = "https://api.resend.com/emails"


class SendError(Exception):
    def __init__(self, message: str, permanent: bool):
        super().__init__(message)
        self.permanent = permanent


def send_email(to: str, subject: str, text: str, idempotency_key: str) -> str:
    """Returns the message id. 4xx except 429 is permanent; 429, 5xx, network are temporary."""
    try:
        r = httpx.post(
            URL,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Idempotency-Key": idempotency_key,
            },
            json={"from": settings.resend_from, "to": [to], "subject": subject, "text": text},
            timeout=20,
        )
    except httpx.HTTPError as e:
        raise SendError(f"network error ({type(e).__name__})", permanent=False) from None
    if r.status_code < 300:
        try:
            return str(r.json()["id"])
        except (ValueError, KeyError, TypeError):
            raise SendError("unexpected response from Resend", permanent=False) from None
    detail = ""
    try:
        body = r.json()
        detail = f"{body.get('name', '')} {body.get('message', '')}".strip()
    except (ValueError, AttributeError):
        pass
    permanent = r.status_code < 500 and r.status_code != 429
    raise SendError(f"HTTP {r.status_code} {detail}"[:200].strip(), permanent=permanent)
