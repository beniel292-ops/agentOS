"""audit_events: block TRUNCATE too (the row trigger does not fire for it)

Revision ID: d7a4b1c93e58
Revises: c8e2f5a97d13
Create Date: 2026-11-16 10:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd7a4b1c93e58'
down_revision: str | Sequence[str] | None = 'c8e2f5a97d13'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # reuses audit_events_block_mutation() from the initial schema
    op.execute(
        """
        CREATE TRIGGER audit_events_no_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION audit_events_block_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_no_truncate ON audit_events")
