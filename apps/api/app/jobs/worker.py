import logging
from datetime import UTC, datetime, timedelta

from arq import Retry, cron
from arq.connections import RedisSettings
from sqlalchemy import and_, or_, select
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db import SessionLocal
from app.jobs import queue
from app.models import Lead, LeadStatus
from app.telegram import notify as tg_notify
from app.telegram.client import TelegramError, get_client
from app.workflow import process

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


class WorkerSettings:
    functions = [ping, process_lead, notify]
    cron_jobs = [cron(requeue_stuck_leads), cron(evening_summary)]  # once a minute each
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # Don't keep finished-job results: otherwise the same _job_id is blocked for an hour
    # and requeue_stuck_leads couldn't retry a lead that is still NEW.
    keep_result = 0
    max_tries = process.MAX_TRIES
    job_timeout = 600  # AI calls may fall back across several models
