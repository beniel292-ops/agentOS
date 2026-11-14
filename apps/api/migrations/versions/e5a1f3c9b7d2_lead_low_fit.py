"""lead status LOW_FIT

Revision ID: e5a1f3c9b7d2
Revises: c86d524bd1c4
Create Date: 2026-11-14 10:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5a1f3c9b7d2'
down_revision: str | Sequence[str] | None = 'c86d524bd1c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ('NEW', 'PROCESSING', 'DRAFT_READY', 'APPROVED', 'REJECTED', 'SENT', 'FAILED')
NEW = (*OLD, 'LOW_FIT')


def _constraint(statuses: tuple[str, ...]) -> None:
    # The CHECK is created by the Enum type (name 'leadstatus'); autogenerate can't see changes to it.
    op.drop_constraint('leadstatus', 'leads', type_='check')
    values = ", ".join(f"'{s}'" for s in statuses)
    op.create_check_constraint('leadstatus', 'leads', f"status IN ({values})")


def upgrade() -> None:
    _constraint(NEW)


def downgrade() -> None:
    # LOW_FIT leads are rejected-by-system in the old model; map them (with an audit row,
    # audit_events is append-only but inserts are fine) so the narrower CHECK can be restored.
    op.execute("""
        INSERT INTO audit_events (tenant_id, lead_id, actor, action, from_state, to_state, data)
        SELECT tenant_id, id, 'migration', 'status_change', 'LOW_FIT', 'REJECTED',
               '{"reason": "downgrade removed LOW_FIT"}'::jsonb
        FROM leads WHERE status = 'LOW_FIT'
    """)
    op.execute("UPDATE leads SET status = 'REJECTED' WHERE status = 'LOW_FIT'")
    _constraint(OLD)
