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


async def enqueue_process_lead(lead_id: uuid.UUID | str, force: bool = False) -> None:
    """Queue processing for a lead. Never raises: the requeue sweep covers failures."""
    global _pool
    try:
        if _pool is None:
            _pool = await create_pool(_redis_settings())
        await _pool.enqueue_job(
            "process_lead", str(lead_id), force=force, _job_id=f"process_lead:{lead_id}"
        )
    except Exception as e:
        _pool = None
        log.warning("Could not enqueue lead %s (%s); sweep will retry", lead_id, type(e).__name__)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
