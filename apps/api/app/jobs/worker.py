import logging
from datetime import UTC, datetime, timedelta

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db import SessionLocal
from app.models import Lead, LeadStatus

log = logging.getLogger(__name__)
STUCK_AFTER = timedelta(minutes=2)
SWEEP_LIMIT = 100


async def ping(ctx) -> str:
    return "pong"


async def process_lead(ctx, lead_id: str) -> None:
    # Placeholder: the AI extract/score/draft step arrives in Prompt 6.
    log.info("process_lead received %s", lead_id)


def _stuck_lead_ids() -> list[str]:
    # System job: intentionally spans all tenants and filters only by status and age.
    with SessionLocal() as session:
        cutoff = datetime.now(UTC) - STUCK_AFTER
        ids = session.scalars(
            select(Lead.id)
            .where(Lead.status == LeadStatus.NEW, Lead.created_at < cutoff)
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
