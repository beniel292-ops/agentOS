import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings

API_DIR = Path(__file__).resolve().parents[1]


def _test_url() -> str:
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    return make_url(settings.database_url).set(database="agentos_test").render_as_string(False)


@pytest.fixture(scope="session")
def engine():
    url = _test_url()
    parsed = make_url(url)
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": parsed.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{parsed.database}"'))
    admin.dispose()

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")

    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with engine.connect() as connection:
        outer = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as s:
            yield s
        outer.rollback()
