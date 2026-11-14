import logging
import uuid
from dataclasses import replace

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings

log = logging.getLogger(__name__)
_pool: ArqRedis | None = None


def _redis_settings() -> RedisSettings:
    # Fail fast if Redis is down; the requeue sweep picks the lead up later.
    return replace(RedisSettings.from_dsn(settings.redis_url), conn_retries=1, conn_timeout=2)


async def _enqueue(function: str, *args, job_id: str, **kwargs) -> None:
    """Queue a job. Never raises: callers have a fallback (sweep) or can afford a lost job."""
    global _pool
    try:
        if _pool is None:
            _pool = await create_pool(_redis_settings())
        await _pool.enqueue_job(function, *args, _job_id=job_id, **kwargs)
    except Exception as e:
        _pool = None
        log.warning("Could not enqueue %s %s (%s)", function, job_id, type(e).__name__)


async def enqueue_process_lead(lead_id: uuid.UUID | str, force: bool = False) -> None:
    """Queue processing for a lead. The requeue sweep covers failures."""
    await _enqueue(
        "process_lead", str(lead_id), force=force, job_id=f"process_lead:{lead_id}"
    )


async def enqueue_notify(kind: str, ref: str) -> None:
    """Queue a Telegram notification. Dedup is in the DB (notifications table)."""
    if not settings.telegram_enabled:
        return
    await _enqueue("notify", kind, ref, job_id=f"notify:{kind}:{ref}")


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
