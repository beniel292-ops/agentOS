"""Voice tools for Jarvis calls: a small MCP server (Streamable HTTP, loopback only).

Run: `uv run python -m app.mcp_server`. It never touches the database: every tool calls the
AgentOS HTTP API with the scoped voice key (X-Agent-Key). Tool arguments come from a small
voice model, so they are untrusted: length-limited and stripped of control characters.
Results are short plain sentences; a tool never raises to the model.
"""
import functools
import logging
import re
import sys
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP

from app.config import settings

log = logging.getLogger(__name__)
HOST = "127.0.0.1"
PORT = urlsplit(settings.voice_mcp_url).port or 8787
MAX_RESULT = 300
PRIORITY = {"high": 1, "normal": 2, "low": 3}
PRIORITY_NAME = {v: k for k, v in PRIORITY.items()}
STATUSES = ("done", "pending", "in_progress", "dropped")
SENT = "Sent to Jarvis. He'll message you on Telegram and call you when it's done."
LIMITED = "Too many requests this hour; I saved it — ask Jarvis on Telegram later."
SAVED = "I saved it. Ask Jarvis on Telegram later."
STOPPED = "The Chief of Staff is stopped, so I can't send it."
DUPLICATE = "That request is already with Jarvis."

mcp = FastMCP("jarvis-tasks", host=HOST, port=PORT)
# Task ids in the order of the last list_tasks result, so "2" means the second one.
_last_list: list[tuple[str, str]] = []


def _clean(value: Any, limit: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(value))
    return re.sub(r"\s+", " ", text).strip()[:limit]


async def _api(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
    async with httpx.AsyncClient(
        base_url=settings.agentos_api_url, headers={"X-Agent-Key": settings.voice_agent_key},
        timeout=10,
    ) as client:
        r = await client.request(method, path, **kwargs)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def _safe(fn):
    """Whatever goes wrong, the model gets one short sentence."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs) -> str:
        try:
            return (await fn(*args, **kwargs))[:MAX_RESULT]
        except Exception as e:  # network down, bad JSON, ...
            log.warning("tool %s failed: %s", fn.__name__, type(e).__name__)
            return "Sorry, I can't reach my task list right now."

    return wrapper


def _refusal(status: int, body: Any) -> str:
    detail = str(body.get("detail", "")) if isinstance(body, dict) else ""
    if status == 423:
        return "I'm switched off right now."
    if status == 429:
        return "I've reached today's call limit."
    if status == 409:
        if "quiet" in detail.lower():
            return "That is in quiet hours. Pick a later time."
        return "That is too close to another call. Pick a later time."
    if status in (401, 403):
        return "I'm not allowed to do that."
    if status == 422:
        return "I couldn't use that. Try different words."
    return "Sorry, something went wrong."


async def _today() -> tuple[int, list[dict]]:
    status, body = await _api("GET", "/v1/cos/tasks", params={"due": "today", "limit": 30})
    if status != 200 or not isinstance(body, list):
        return status, []
    _last_list[:] = [(t["id"], t["title"]) for t in body]
    return 200, body


@mcp.tool()
@_safe
async def list_tasks() -> str:
    """Read today's open tasks, numbered."""
    status, tasks = await _today()
    if status != 200:
        return _refusal(status, None)
    if not tasks:
        return "No tasks for today."
    lines = [
        f"{i}. {_clean(t['title'], 50)} (priority {PRIORITY_NAME.get(t['priority'], 'normal')})"
        for i, t in enumerate(tasks, 1)
    ]
    text = "\n".join(lines)
    while len(text) > MAX_RESULT - 20:  # leave room for the "more" line
        lines.pop()
        text = "\n".join(lines) + f"\n...and {len(tasks) - len(lines)} more."
    return text


@mcp.tool()
@_safe
async def add_task(title: str, priority: str = "normal", due: str = "today") -> str:
    """Add one task. priority is high, normal or low. due is today, tomorrow or none."""
    title = _clean(title, 200)
    if not title:
        return "Tell me the task first."
    body: dict[str, Any] = {
        "title": title, "source": "voice",
        "priority": PRIORITY.get(_clean(priority, 10).lower(), 2),
    }
    days = {"today": 0, "tomorrow": 1}.get(_clean(due, 10).lower())
    if days is not None:
        body["due_date"] = (datetime.now().astimezone() + timedelta(days=days)).date().isoformat()
    status, resp = await _api("POST", "/v1/cos/tasks", json=body)
    if status not in (200, 201):
        return _refusal(status, resp)
    return f"Already on your list: {title}" if resp.get("duplicate") else f"Added: {title}"


@mcp.tool()
@_safe
async def update_task(task: str, status: str) -> str:
    """Change one task. task is its number from the list, or a few words of its title.
    status is done, pending, in_progress or dropped."""
    new = _clean(status, 20).lower().replace(" ", "_")
    if new not in STATUSES:
        return "Status must be done, pending, in progress or dropped."
    q = _clean(task, 100)
    number = re.fullmatch(r"(?:task|number|no\.?|#)?\s*(\d{1,3})\.?", q, re.IGNORECASE)
    if number:
        if not _last_list:
            code, _ = await _today()
            if code != 200:
                return _refusal(code, None)
        i = int(number.group(1))
        matches = [_last_list[i - 1]] if 1 <= i <= len(_last_list) else []
    else:
        code, _ = await _today()
        if code != 200:
            return _refusal(code, None)
        needle = q.casefold()
        matches = [t for t in _last_list if needle and needle in t[1].casefold()]
        exact = [t for t in matches if t[1].casefold() == needle]
        matches = exact if len(exact) == 1 else matches
    if not matches:
        return "I couldn't find that task."
    if len(matches) > 1:
        names = [_clean(t[1], 50) for t in matches[:3]]
        return f"Which one: {' or '.join(names)}?"
    task_id, title = matches[0]
    code, resp = await _api("PATCH", f"/v1/cos/tasks/{task_id}", json={"status": new})
    if code != 200:
        return _refusal(code, resp)
    return f"Marked {_clean(title, 80)} as {new.replace('_', ' ')}."


@mcp.tool()
@_safe
async def schedule_callback(minutes: int) -> str:
    """Book a call back in this many minutes (5 to 240)."""
    if not 5 <= minutes <= 240:
        return "Pick between 5 and 240 minutes."
    status, resp = await _api("POST", "/v1/cos/callbacks", json={"minutes": minutes})
    if status not in (200, 201):
        return _refusal(status, resp)
    due = datetime.fromisoformat(resp["due_at"]).astimezone()
    return f"I'll call you at {due:%H:%M}."


@mcp.tool()
@_safe
async def ask_jarvis(request: str) -> str:
    """Send Beniel's request to Jarvis, his main assistant, to do it now (research, files, messages to Beniel, anything you can't do). Before calling, read the request back in one sentence and ask 'Shall I send this to Jarvis?'; only call after he says yes."""  # noqa: E501
    text = _clean(request, 191)
    if not text:
        return "Tell me what to send."
    status, resp = await _api("POST", "/v1/cos/tasks", json={
        "title": f"Request: {text}", "project": "for-jarvis", "source": "voice",
    })
    if status == 423:
        return STOPPED
    if status not in (200, 201):
        return _refusal(status, resp)
    if resp.get("duplicate"):
        return DUPLICATE
    return {"sent": SENT, "limited": LIMITED}.get(resp.get("handoff"), SAVED)


def main() -> None:
    if not settings.voice_agent_key:
        sys.exit("VOICE_AGENT_KEY is not set")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
