"""Tiny Telegram Bot API client. Plain text only (no parse_mode): lead content is untrusted.

The bot token is part of every request URL, so nothing here logs URLs or httpx exceptions
(their text contains the URL). Errors carry only the method name and a status/type.
"""
import logging

import httpx

from app.config import settings

# httpx logs "HTTP Request: POST https://api.telegram.org/bot<token>/..." at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log = logging.getLogger(__name__)
MAX_TEXT = 4096  # Telegram counts UTF-16 code units
_disabled_logged = False
_client = None

Button = dict  # {"text": ..., "callback_data": ...} or {"text": ..., "url": ...}


class TelegramError(Exception):
    """Message holds only the method name and a status or exception type: never URL or token."""


def truncate(text: str) -> str:
    def units(t: str) -> int:
        return len(t.encode("utf-16-le")) // 2

    if units(text) <= MAX_TEXT:
        return text
    text = text[: MAX_TEXT - 1]
    while units(text) > MAX_TEXT - 1:
        text = text[:-1]
    return text + "…"


def _markup(buttons: list[Button] | None) -> dict:
    # one button per row; an empty keyboard removes buttons when editing
    return {"inline_keyboard": [[b] for b in buttons or []]}


class TelegramClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None):
        self._base = f"https://api.telegram.org/bot{token}/"
        self._http = httpx.Client(transport=transport, timeout=15)

    def _call(self, method: str, payload: dict, timeout: float | None = None):
        try:
            r = self._http.post(
                self._base + method, json=payload, **({"timeout": timeout} if timeout else {})
            )
        except httpx.HTTPError as e:
            raise TelegramError(f"{method}: {type(e).__name__}") from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or not body.get("ok"):
            raise TelegramError(f"{method}: HTTP {r.status_code}")
        return body["result"]

    def send_message(self, chat_id: int, text: str, buttons: list[Button] | None = None) -> int:
        payload = {"chat_id": chat_id, "text": truncate(text), "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = _markup(buttons)
        return self._call("sendMessage", payload)["message_id"]

    def edit_message(
        self, chat_id: int, message_id: int, text: str, buttons: list[Button] | None = None
    ) -> None:
        self._call("editMessageText", {
            "chat_id": chat_id, "message_id": message_id, "text": truncate(text),
            "disable_web_page_preview": True, "reply_markup": _markup(buttons),
        })

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload, timeout=timeout + 10)


def get_client() -> TelegramClient | None:
    """None (logged once) when Telegram is not configured: every feature then does nothing."""
    global _disabled_logged
    if not settings.telegram_enabled:
        if not _disabled_logged:
            _disabled_logged = True
            log.info("Telegram approvals bot not configured; Telegram features are disabled")
        return None
    global _client
    if _client is None:
        _client = TelegramClient(settings.telegram_approvals_bot_token)
    return _client
