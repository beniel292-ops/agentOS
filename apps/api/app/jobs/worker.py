import logging
from datetime import UTC, datetime, timedelta

from arq import Retry, cron, func
from arq.connections import RedisSettings
from sqlalchemy import and_, or_, select
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.cos.calls import CALL_TIMEOUT, place_due_calls
from app.db import SessionLocal
from app.jobs import queue
from app.models import Lead, LeadStatus
from app.telegram import notify as tg_notify
from app.telegram.client import TelegramError, get_client
from app.workflow import process, send

log = logging.getLogger(__name__)
STUCK_AFTER = timedelta(minutes=2)  # NEW leads whose job was never queued or got lost
CRASHED_AFTER = timedelta(minutes=15)  # PROCESSING leads whose worker died (updated_at is stale)
SWEEP_LIMIT = 100


async def ping(ctx) -> str:
    return "pong"


async def process_lead(ctx, lead_id: str, force: bool = False) -> None:
    kind = await run_in_threadpool(process.run, SessionLocal, lead_id, ctx["job_try"], force)
    if kind:  # after the commit; enqueue_notify never raises
        await queue.enqueue_notify(kind, lead_id)


async def send_reply(ctx, lead_id: str) -> None:
    kind = await run_in_threadpool(send.run, SessionLocal, lead_id, ctx["job_try"])
    if kind:  # after the commit; enqueue_notify never raises
        await queue.enqueue_notify(kind, lead_id)


def _send_notification(kind: str, ref: str) -> None:
    tg_notify.send(SessionLocal, get_client(), kind, ref)


async def notify(ctx, kind: str, ref: str) -> None:
    try:
        await run_in_threadpool(_send_notification, kind, ref)
    except TelegramError as e:  # the dedup row is already removed, so a retry may resend
        log.warning("notify %s failed: %s", kind, e)
        if ctx["job_try"] < process.MAX_TRIES:
            raise Retry(defer=30 * ctx["job_try"]) from None


def _evening_ref() -> str | None:
    tenant_id = settings.telegram_owner_tenant_id
    with SessionLocal() as session:
        ref = tg_notify.evening_due(session, tenant_id)
        session.commit()  # settings row may have just been created
        return ref


async def evening_summary(ctx) -> None:
    """Every minute; dedup per local date makes the summary go out once a day."""
    if not settings.telegram_enabled:
        return
    ref = await run_in_threadpool(_evening_ref)
    if ref:
        try:
            await run_in_threadpool(_send_notification, "evening_summary", ref)
        except TelegramError as e:
            log.warning("evening summary failed: %s", e)  # next minute is outside the window


def _stuck_lead_ids() -> list[str]:
    # System job: intentionally spans all tenants and filters only by status and age.
    with SessionLocal() as session:
        now = datetime.now(UTC)
        ids = session.scalars(
            select(Lead.id)
            .where(
                or_(
                    and_(Lead.status == LeadStatus.NEW, Lead.created_at < now - STUCK_AFTER),
                    and_(
                        Lead.status == LeadStatus.PROCESSING,
                        Lead.updated_at < now - CRASHED_AFTER,
                    ),
                )
            )
            .order_by(Lead.created_at)
            .limit(SWEEP_LIMIT)
        )
        return [str(i) for i in ids]


async def requeue_stuck_leads(ctx) -> None:
    for lead_id in await run_in_threadpool(_stuck_lead_ids):
        # arq ignores a job whose _job_id is already queued or running
        await ctx["redis"].enqueue_job("process_lead", lead_id, _job_id=f"process_lead:{lead_id}")


def _unsent_approved_ids() -> list[str]:
    # System job: spans all tenants, filters by status only. send.run re-checks tenant + draft.
    with SessionLocal() as session:
        ids = session.scalars(
            select(Lead.id)
            .where(
                Lead.status == LeadStatus.APPROVED,
                or_(Lead.last_error.is_(None), Lead.last_error.not_like("send:%")),
            )
            .order_by(Lead.updated_at)
            .limit(SWEEP_LIMIT)
        )
        return [str(i) for i in ids]


async def requeue_approved_leads(ctx) -> None:
    """Every minute: the guaranteed path that gets approved replies sent."""
    if not settings.email_configured:
        return
    for lead_id in await run_in_threadpool(_unsent_approved_ids):
        await ctx["redis"].enqueue_job("send_reply", lead_id, _job_id=f"send_reply:{lead_id}")


class WorkerSettings:
    # place_due_calls is also a plain function so POST /v1/cos/calls/now can trigger it at once
    # (arq registers cron jobs only as "cron:<name>"); the Redis call lock prevents double dialing.
    functions = [
        ping, process_lead, send_reply, notify,
        func(place_due_calls, timeout=CALL_TIMEOUT + 300),
    ]
    # once a minute each. place_due_calls is also enqueued by name ("call me now"); it can run a
    # whole phone call, so it gets its own timeout (arq registers cron jobs as functions too).
    cron_jobs = [
        cron(requeue_stuck_leads), cron(requeue_approved_leads), cron(evening_summary),
        cron(place_due_calls, timeout=CALL_TIMEOUT + 300),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # Don't keep finished-job results: otherwise the same _job_id is blocked for an hour
    # and requeue_stuck_leads couldn't retry a lead that is still NEW.
    keep_result = 0
    max_tries = process.MAX_TRIES
    job_timeout = 600  # AI calls may fall back across several models
