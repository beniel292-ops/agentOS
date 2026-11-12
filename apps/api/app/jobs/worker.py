from arq.connections import RedisSettings

from app.config import settings


async def ping(ctx) -> str:
    return "pong"


class WorkerSettings:
    functions = [ping]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
