import sentry_sdk

from app.config import settings

_DROPPED_HEADERS = {"authorization", "x-agent-key", "cookie", "x-api-key"}


def before_send(event: dict, hint: dict) -> dict:
    """Lead messages and keys must never leave the Mac: strip request bodies, cookies, headers."""
    request = event.get("request")
    if request:
        for key in ("data", "cookies", "query_string"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                k: v for k, v in headers.items() if k.lower() not in _DROPPED_HEADERS
            }
    return event


def init(component: str) -> None:
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        send_default_pii=False,
        traces_sample_rate=0,
        max_request_body_size="never",
        include_local_variables=False,  # stack-frame locals can hold lead messages and drafts
        before_send=before_send,
    )
    sentry_sdk.set_tag("component", component)
