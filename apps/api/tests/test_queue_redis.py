"""Real-Redis check for the enqueue path (mocked elsewhere). Skips if Redis isn't reachable."""

import uuid

import pytest
from arq import create_pool

from app.jobs import queue


@pytest.fixture
async def redis():
    try:
        pool = await create_pool(queue._redis_settings())
    except Exception:
        pytest.skip("Redis not reachable")
    yield pool
    await pool.aclose()


async def test_enqueue_creates_one_job_per_lead(redis):
    lead_id = uuid.uuid4()
    job_key = f"arq:job:process_lead:{lead_id}"
    try:
        await queue.enqueue_process_lead(lead_id)
        await queue.enqueue_process_lead(lead_id)  # duplicate: arq keeps one job
        assert await redis.exists(job_key) == 1
        assert queue._pool is not None  # no failure path taken
    finally:
        await redis.delete(job_key)
        await redis.zrem("arq:queue", f"process_lead:{lead_id}")
        await queue.close_pool()
