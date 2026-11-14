"""Telegram approvals + Chief of Staff bot (long polling): `uv run python -m app.telegram.bot`.

Only the owner (private chat, user id == chat id) is served. No free text ever reaches an AI.
Approving only records the decision; sending email is a later step (rule 2: only the worker sends).
"""
import logging
import re
import signal
import threading
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import SessionLocal
from app.models import (
    AuditEvent,
    Draft,
    DraftDecision,
    Lead,
    LeadStatus,
    User,
    UserRole,
)
from app.routes.cos import get_or_create_settings, today_open_tasks
from app.telegram.client import TelegramClient, TelegramError, get_client
from app.telegram.notify import next_call_text
from app.workflow.transitions import ConcurrentTransition, InvalidTransition, transition

log = logging.getLogger(__name__)
CALLBACK_RE = re.compile(r"^(ap|rj):([0-9a-f-]{36})$")
HELP = (
    "AgentOS approvals bot: new lead drafts for your approval, plus Chief of Staff updates.\n\n"
    "/status - running or stopped, drafts waiting, today's tasks, next call\n"
    "/tasks - today's open tasks\n"
    "/stop - kill switch: stop Chief of Staff calls\n"
    "/resume - turn them back on\n"
    "/help - this message"
)
HINT = "I only understand commands. Try /help."


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- commands


def _status(session: Session, tenant_id: uuid.UUID) -> str:
    s = get_or_create_settings(session, tenant_id)
    drafts = session.scalar(select(func.count()).select_from(Lead).where(
        Lead.tenant_id == tenant_id, Lead.status == LeadStatus.DRAFT_READY
    ))
    return (
        f"Chief of Staff: {'STOPPED' if s.stopped else 'running'}\n"
        f"Drafts waiting approval: {drafts}\n"
        f"Open tasks today: {len(today_open_tasks(session, tenant_id))}\n"
        f"Next call: {next_call_text(session, tenant_id, s.timezone)}"
    )


def _tasks(session: Session, tenant_id: uuid.UUID) -> str:
    tasks = today_open_tasks(session, tenant_id)
    if not tasks:
        return "No open tasks for today."
    lines = [f"{i}. [P{t.priority}] {t.title}" for i, t in enumerate(tasks[:30], 1)]
    more = f"\n...and {len(tasks) - 30} more" if len(tasks) > 30 else ""
    return "Today's open tasks:\n" + "\n".join(lines) + more


def _set_stopped(session: Session, tenant_id: uuid.UUID, user_id: int, stop: bool) -> str:
    actor = f"telegram:{user_id}"
    s = get_or_create_settings(session, tenant_id)
    if stop:
        s.stopped, s.stopped_at, s.stopped_by = True, _now(), actor
    else:
        s.stopped, s.stopped_at, s.stopped_by = False, None, None
    session.add(AuditEvent(
        tenant_id=tenant_id, lead_id=None, actor=actor,
        action="cos_stopped" if stop else "cos_resumed", data={},
    ))
    return "Stopped. Use /resume to turn Chief of Staff back on." if stop else "Resumed."


def handle_message(
    factory: sessionmaker[Session], client: TelegramClient, message: dict, user_id: int
) -> None:
    tenant_id = settings.telegram_owner_tenant_id
    text = message.get("text") or ""
    command = text.split(maxsplit=1)[0].split("@")[0].lower() if text.startswith("/") else ""
    if command in ("/start", "/help"):
        reply = HELP
    elif command in ("/status", "/tasks", "/stop", "/resume"):
        with factory() as session:
            if command == "/status":
                reply = _status(session, tenant_id)
            elif command == "/tasks":
                reply = _tasks(session, tenant_id)
            else:
                reply = _set_stopped(session, tenant_id, user_id, command == "/stop")
            session.commit()
    else:
        reply = HINT
    client.send_message(user_id, reply)


# ---------------------------------------------------------------- approve / reject


def _decide(
    factory: sessionmaker[Session], lead_id: uuid.UUID, approve: bool, user_id: int
) -> tuple[str, LeadStatus | None]:
    """Returns (outcome, current status): outcome is stopped | missing | handled | done."""
    tenant_id = settings.telegram_owner_tenant_id
    with factory() as session:
        if get_or_create_settings(session, tenant_id).stopped:
            session.rollback()
            return "stopped", None
        lead = session.scalars(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id).with_for_update()
        ).first()
        if lead is None:
            return "missing", None
        draft = session.scalars(
            select(Draft).where(Draft.lead_id == lead.id, Draft.tenant_id == tenant_id)
            .with_for_update()
        ).first()
        if lead.status != LeadStatus.DRAFT_READY or draft is None or draft.decision is not None:
            return "handled", lead.status
        owner_id = session.scalar(
            select(User.id).where(User.tenant_id == tenant_id, User.role == UserRole.OWNER)
            .order_by(User.created_at).limit(1)
        )
        draft.decision = DraftDecision.APPROVED if approve else DraftDecision.REJECTED
        draft.decided_at, draft.decided_by = _now(), owner_id
        if not approve:
            draft.reject_reason = "Rejected from Telegram"
        try:
            transition(
                session, lead, LeadStatus.APPROVED if approve else LeadStatus.REJECTED,
                f"telegram:{user_id}",
            )
        except (InvalidTransition, ConcurrentTransition):
            session.rollback()
            return "handled", None
        session.commit()
        return "done", None


def handle_callback(
    factory: sessionmaker[Session], client: TelegramClient, cq: dict, user_id: int
) -> None:
    m = CALLBACK_RE.match(cq.get("data") or "")
    msg = cq.get("message") or {}
    try:
        lead_id = uuid.UUID(m.group(2)) if m else None
    except ValueError:
        lead_id = None
    if lead_id is None or str(lead_id) != m.group(2) or "message_id" not in msg:
        return  # not ours / malformed: ignore
    approve = m.group(1) == "ap"
    outcome, status = _decide(factory, lead_id, approve, user_id)
    chat_id, message_id = user_id, msg["message_id"]
    if outcome == "stopped":
        client.answer_callback(cq["id"], "Stopped — resume first")
    elif outcome == "missing":
        client.answer_callback(cq["id"], "Lead not found")
    elif outcome == "handled":
        client.answer_callback(cq["id"], "Already handled")
        shown = status or "changed"
        client.edit_message(chat_id, message_id, f"Already handled (status: {shown}).")
    elif approve:
        client.edit_message(
            chat_id, message_id, "✅ Approved. It will be sent once email sending is set up."
        )
        client.answer_callback(cq["id"], "Approved")
    else:
        client.edit_message(chat_id, message_id, "❌ Rejected.")
        client.answer_callback(cq["id"], "Rejected")


# ---------------------------------------------------------------- dispatch + loop


def handle_update(factory: sessionmaker[Session], client: TelegramClient, update: dict) -> None:
    """Allowlist first (owner, private chat, chat id == owner id), then act. Never raises."""
    owner = settings.telegram_owner_user_id
    try:
        kind = "callback_query" if "callback_query" in update else (
            "message" if "message" in update else None
        )
        if kind is None:  # edited messages, channel posts, ...
            return
        item = update[kind]
        user_id = (item.get("from") or {}).get("id")
        chat = (item.get("message") if kind == "callback_query" else item) or {}
        chat = chat.get("chat") or {}
        if not (user_id == owner and chat.get("type") == "private" and chat.get("id") == owner):
            log.info("rejected update from user %s", user_id)
            return
        if kind == "message":
            handle_message(factory, client, item, user_id)
        else:
            handle_callback(factory, client, item, user_id)
    except Exception as e:  # noqa: BLE001 - one bad update must not stop the loop
        log.warning("update failed: %s", type(e).__name__)  # never log content or URLs


def run(factory: sessionmaker[Session], client: TelegramClient, stop: threading.Event,
        polling: threading.Event) -> None:
    offset, backoff = None, 1
    while not stop.is_set():
        try:
            polling.set()
            updates = client.get_updates(offset)
            polling.clear()
            backoff = 1
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            polling.clear()
            detail = str(e) if isinstance(e, TelegramError) else type(e).__name__  # no token
            log.warning("polling failed: %s; retry in %ss", detail, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, 30)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            handle_update(factory, client, u)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop, polling = threading.Event(), threading.Event()

    def on_signal(signum, _frame):
        stop.set()
        if polling.is_set():  # interrupt the blocking long poll; otherwise finish the update
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    client = get_client()
    try:
        if client is None:
            stop.wait()  # idle so `make dev` keeps the process slot quiet
        else:
            log.info("Telegram bot started")
            run(SessionLocal, client, stop, polling)
    except KeyboardInterrupt:
        pass
    log.info("Telegram bot stopped")


if __name__ == "__main__":
    main()
