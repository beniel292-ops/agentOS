from unittest.mock import patch

from app import sentry
from app.config import settings


def test_init_without_dsn_does_nothing(monkeypatch):
    monkeypatch.setattr(settings, "sentry_dsn", "")
    with patch("app.sentry.sentry_sdk") as sdk:
        sentry.init("api")
    sdk.init.assert_not_called()


def test_init_with_dsn(monkeypatch):
    monkeypatch.setattr(settings, "sentry_dsn", "https://k@example.ingest.sentry.io/1")
    with patch("app.sentry.sentry_sdk") as sdk:
        sentry.init("worker")
    kwargs = sdk.init.call_args.kwargs
    assert kwargs["send_default_pii"] is False
    assert kwargs["include_local_variables"] is False
    assert kwargs["traces_sample_rate"] == 0
    assert kwargs["before_send"] is sentry.before_send
    sdk.set_tag.assert_called_once_with("component", "worker")


def test_before_send_strips_sensitive_request_data():
    event = {
        "request": {
            "headers": {"Authorization": "Bearer x", "X-Agent-Key": "k", "Accept": "*/*"},
            "cookies": {"s": "1"},
            "data": {"message": "hi"},
        }
    }
    out = sentry.before_send(event, {})
    assert out["request"]["headers"] == {"Accept": "*/*"}
    assert "cookies" not in out["request"]
    assert "data" not in out["request"]
