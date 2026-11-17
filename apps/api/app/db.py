from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# 10 s: Neon scales to zero when idle and takes a few seconds to wake up.
# Keepalives: after the Mac sleeps, a dead connection errors within about a minute instead of
# hanging a worker job forever (seen 2026-11-17: the queue stalled for hours).
engine = create_engine(
    settings.database_url, pool_pre_ping=True,
    connect_args={"connect_timeout": 10, "keepalives": 1, "keepalives_idle": 30,
                  "keepalives_interval": 10, "keepalives_count": 3},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """One session per request: commit on success, roll back on error."""
    with SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_session_factory() -> sessionmaker[Session]:
    """Dependency so tests can swap the factory used by routes that commit explicitly."""
    return SessionLocal
