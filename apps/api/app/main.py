import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db import engine

app = FastAPI(title="AgentOS API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["*"])


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
