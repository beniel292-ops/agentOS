import logging
from datetime import UTC, datetime, timedelta

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import and_, or_, select
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db import SessionLocal
from app.models import Lead, LeadStatus
from app.workflow import process

log = logging.getLogger(__name__)
STUCK_AFTER = timedelta(minutes=2)  # NEW leads whose job was never queued or got lost
CRASHED_AFTER = timedelta(minutes=15)  # PROCESSING leads whose worker died (updated_at is stale)
SWEEP_LIMIT = 100


async def ping(ctx) -> str:
    return "pong"


async def process_lead(ctx, lead_id: str, force: bool = False) -> None:
    await run_in_threadpool(process.run, SessionLocal, lead_id, ctx["job_try"], force)


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
    functions = [ping, process_lead]
    cron_jobs = [cron(requeue_stuck_leads)]  # second=0 default: once a minute
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # Don't keep finished-job results: otherwise the same _job_id is blocked for an hour
    # and requeue_stuck_leads couldn't retry a lead that is still NEW.
    keep_result = 0
    max_tries = process.MAX_TRIES
    job_timeout = 600  # AI calls may fall back across several models
