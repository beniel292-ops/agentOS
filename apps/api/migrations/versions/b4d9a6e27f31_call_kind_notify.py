"""call kind NOTIFY: agent-requested "call me now" calls

Revision ID: b4d9a6e27f31
Revises: f3b8d2a41c75
Create Date: 2026-11-15 10:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b4d9a6e27f31'
down_revision: str | Sequence[str] | None = 'f3b8d2a41c75'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ('morning', 'evening', 'callback')
NEW = (*OLD, 'notify')
CHECKS = (('call_kind', 'scheduled_calls'), ('checkin_kind', 'checkins'))


def _constraints(kinds: tuple[str, ...]) -> None:
    # The CHECKs are created by the Enum types; autogenerate can't see changes to them.
    values = ", ".join(f"'{k}'" for k in kinds)
    for name, table in CHECKS:
        op.drop_constraint(name, table, type_='check')
        op.create_check_constraint(name, table, f"kind IN ({values})")


def upgrade() -> None:
    _constraints(NEW)
    op.create_index(
        'uq_scheduled_calls_pending_notify', 'scheduled_calls', ['tenant_id'], unique=True,
        postgresql_where="kind = 'notify' AND status = 'pending'",
    )


def downgrade() -> None:
    op.drop_index('uq_scheduled_calls_pending_notify', table_name='scheduled_calls')
    # The old CHECK cannot hold 'notify' at all: cancel what is pending, then drop the rows.
    op.execute("UPDATE scheduled_calls SET status = 'cancelled' "
               "WHERE kind = 'notify' AND status = 'pending'")
    op.execute("DELETE FROM checkins WHERE kind = 'notify'")
    op.execute("DELETE FROM scheduled_calls WHERE kind = 'notify'")
    _constraints(OLD)
