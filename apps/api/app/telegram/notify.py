"""Build and send Telegram notifications, at most once each (notifications table).

All text is plain text. Only the configured owner tenant is notified.
"""
import logging
import uuid
from datetime import UTC, datetime, time, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import (
    CallKind,
    CallStatus,
    CosSettings,
    Draft,
    Lead,
    LeadAnalysis,
    LeadStatus,
    Notification,
    Opportunity,
    ScheduledCall,
    Task,
    TaskStatus,
)
from app.routes.cos import get_or_create_settings, today_open_tasks
from app.telegram.client import Button, TelegramClient, TelegramError

log = logging.getLogger(__name__)
MIN_RELEVANCE = 60
Built = tuple[str, list[Button] | None] | None


def now() -> datetime:
    """Module-level clock so tests can monkeypatch it."""
    return datetime.now(UTC)


def _v(value) -> str:
    return str(value) if value not in (None, "") else "-"


def web_url(path: str) -> str | None:
    """Telegram rejects localhost URL buttons, so there is no button for a local web app."""
    base = settings.web_app_url.rstrip("/")
    host = urlsplit(base).hostname or ""
    if host in ("localhost", "::1") or host.startswith("127.") or not base.startswith("http"):
        return None
    return base + path


def _url_button(label: str, url: str | None) -> list[Button]:
    return [{"text": label, "url": url}] if url else []


# ---------------------------------------------------------------- builders


def _lead(session: Session, tenant_id: uuid.UUID, ref: str, status: LeadStatus) -> Lead | None:
    try:
        lead_id = uuid.UUID(ref)
    except ValueError:
        return None
    lead = session.scalars(
        select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
    ).first()
    return lead if lead is not None and lead.status == status else None


def build_draft_ready(session: Session, tenant_id: uuid.UUID, ref: str) -> Built:
    lead = _lead(session, tenant_id, ref, LeadStatus.DRAFT_READY)
    if lead is None:
        return None
    a = session.scalars(select(LeadAnalysis).where(LeadAnalysis.lead_id == lead.id)).first()
    d = session.scalars(select(Draft).where(Draft.lead_id == lead.id)).first()
    if a is None or d is None:
        return None
    who = f"{lead.name} ({lead.company}) <{lead.email}>" if lead.company else (
        f"{lead.name} <{lead.email}>"
    )
    text = (
        f"📩 New lead: {who}\nWants: {a.intent}\n"
        f"Service: {_v(a.service)} · Budget: {_v(a.budget)} · Urgency: {_v(a.urgency)} · "
        f"Fit: {a.fit_score}/100\n\nSubject: {d.subject}\n\n{d.body}"
    )
    buttons = [
        {"text": "✅ Approve", "callback_data": f"ap:{lead.id}"},
        {"text": "❌ Reject", "callback_data": f"rj:{lead.id}"},
        *_url_button("✏️ Edit in app", web_url("/leads")),
    ]
    return text, buttons


def build_lead_failed(session: Session, tenant_id: uuid.UUID, ref: str) -> Built:
    lead = _lead(session, tenant_id, ref, LeadStatus.FAILED)
    if lead is None:
        return None
    text = f"⚠️ Couldn't process lead {lead.name}: {_v(lead.last_error)}"
    return text, _url_button("Open leads", web_url("/leads")) or None


def _local(dt: datetime, tz: str) -> datetime:
    return dt.astimezone(ZoneInfo(tz))


def build_callback_scheduled(session: Session, tenant_id: uuid.UUID, ref: str) -> Built:
    call_id, _, due = ref.partition(":")
    try:
        call = session.scalars(select(ScheduledCall).where(
            ScheduledCall.id == uuid.UUID(call_id), ScheduledCall.tenant_id == tenant_id
        )).first()
        ref_due = datetime.fromisoformat(due)
    except ValueError:
        return None
    # stale: cancelled, done, or moved again (a newer ref covers that one)
    if call is None or call.status != CallStatus.PENDING or call.due_at != ref_due:
        return None
    tz = get_or_create_settings(session, tenant_id).timezone
    minutes = max(0, round((call.due_at - now()).total_seconds() / 60))
    return f"📞 I'll call you at {_local(call.due_at, tz):%H:%M} (in {minutes} min).", None


def build_opportunity(session: Session, tenant_id: uuid.UUID, ref: str) -> Built:
    try:
        opp = session.scalars(select(Opportunity).where(
            Opportunity.id == uuid.UUID(ref), Opportunity.tenant_id == tenant_id
        )).first()
    except ValueError:
        return None
    if opp is None or opp.relevance < MIN_RELEVANCE:
        return None
    text = (
        f"💡 {opp.title}\nSource: {opp.source} · Relevance: {opp.relevance}/100\n"
        f"Value: {_v(opp.estimated_value)} · Deadline: {_v(opp.deadline)}\n"
        f"Next action: {_v(opp.next_action)}\nVerification: {opp.verification_status.value}"
    )
    url = opp.url if opp.url and opp.url.startswith(("http://", "https://")) else None
    return text, _url_button("Open", url) or None


def _next_call(session: Session, tenant_id: uuid.UUID) -> ScheduledCall | None:
    return session.scalars(
        select(ScheduledCall).where(
            ScheduledCall.tenant_id == tenant_id, ScheduledCall.status == CallStatus.PENDING,
            ScheduledCall.due_at >= now(),
        ).order_by(ScheduledCall.due_at).limit(1)
    ).first()


def next_call_text(session: Session, tenant_id: uuid.UUID, tz: str) -> str:
    call = _next_call(session, tenant_id)
    if call is None:
        return "none"
    kind = "call" if call.kind == CallKind.CALLBACK else f"{call.kind.value} call"
    return f"{kind} at {_local(call.due_at, tz):%a %H:%M}"


def build_evening_summary(session: Session, tenant_id: uuid.UUID, ref: str) -> Built:
    s = get_or_create_settings(session, tenant_id)
    tz = ZoneInfo(s.timezone)
    day = _local(now(), s.timezone).date()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    done = session.scalar(select(func.count()).select_from(Task).where(
        Task.tenant_id == tenant_id, Task.status == TaskStatus.DONE,
        Task.completed_at >= start, Task.completed_at < end,
    ))
    opps = session.scalar(select(func.count()).select_from(Opportunity).where(
        Opportunity.tenant_id == tenant_id, Opportunity.created_at >= start,
        Opportunity.created_at < end,
    ))
    open_tasks = len(today_open_tasks(session, tenant_id))
    text = (
        f"🌙 Evening summary ({day})\nDone today: {done}\nStill open: {open_tasks}\n"
        f"New opportunities: {opps}\nNext call: {next_call_text(session, tenant_id, s.timezone)}"
    )
    return text, None


BUILDERS = {
    "draft_ready": build_draft_ready,
    "lead_failed": build_lead_failed,
    "callback_scheduled": build_callback_scheduled,
    "opportunity": build_opportunity,
    "evening_summary": build_evening_summary,
}


# ---------------------------------------------------------------- sending


def send(
    factory: sessionmaker[Session], client: TelegramClient | None, kind: str, ref: str
) -> bool:
    """Send one notification at most once. Returns True if a message was sent.

    Dedup: the (tenant, kind, ref) row is INSERTed and COMMITTED before the Telegram call, so a
    concurrent or repeated job gets no row back (ON CONFLICT DO NOTHING) and sends nothing.
    If the send fails the row is deleted so a retry can send again. A crash between the commit
    and the send loses that one notification (at-most-once, never twice).
    Raises TelegramError after cleanup so the job can retry.
    """
    tenant_id = settings.telegram_owner_tenant_id
    builder = BUILDERS.get(kind)
    if client is None or tenant_id is None or builder is None:
        return False
    with factory() as session:
        built = builder(session, tenant_id, ref)
        if built is None:
            return False
        text, buttons = built
        row_id = session.scalar(
            insert(Notification).values(tenant_id=tenant_id, kind=kind, ref=ref)
            .on_conflict_do_nothing(index_elements=["tenant_id", "kind", "ref"])
            .returning(Notification.id)
        )
        session.commit()
        if row_id is None:
            return False  # already sent (or being sent) by someone else
        try:
            message_id = client.send_message(settings.telegram_owner_user_id, text, buttons)
        except TelegramError:
            session.execute(delete(Notification).where(Notification.id == row_id))
            session.commit()
            raise
        session.execute(
            update(Notification).where(Notification.id == row_id)
            .values(telegram_message_id=message_id)
        )
        session.commit()
        return True


def evening_due(session: Session, tenant_id: uuid.UUID) -> str | None:
    """The local date (the dedup ref) if the tenant's local time is in its evening minute."""
    s: CosSettings = get_or_create_settings(session, tenant_id)
    local = _local(now(), s.timezone)
    if (local.hour, local.minute) == (s.evening_time.hour, s.evening_time.minute):
        return str(local.date())
    return None
