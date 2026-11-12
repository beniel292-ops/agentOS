from alembic import context
from sqlalchemy import create_engine

from app.config import settings
from app.models.base import Base

config = context.config
target_metadata = Base.metadata


def db_url() -> str:
    # an explicit sqlalchemy.url (tests, -x, Neon) wins over Settings
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with create_engine(db_url()).connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
