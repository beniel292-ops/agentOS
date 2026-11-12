from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.jobs.queue import close_pool
from app.routes import intake, leads, me


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_pool()


app = FastAPI(title="AgentOS API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(me.router)
app.include_router(leads.router)
app.include_router(intake.router)


def db_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def redis_ok() -> bool:
    try:
        return bool(redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2).ping())
    except Exception:
        return False


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "db": db_ok(), "redis": redis_ok()}
