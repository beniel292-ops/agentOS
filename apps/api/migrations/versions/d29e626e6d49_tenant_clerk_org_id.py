"""tenant clerk org id

Revision ID: d29e626e6d49
Revises: b886f7ca08d9
Create Date: 2026-11-12 19:54:03.145629

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd29e626e6d49'
down_revision: str | Sequence[str] | None = 'b886f7ca08d9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tenants', sa.Column('clerk_org_id', sa.Text(), nullable=False))
    op.create_unique_constraint('tenants_clerk_org_id_key', 'tenants', ['clerk_org_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('tenants_clerk_org_id_key', 'tenants', type_='unique')
    op.drop_column('tenants', 'clerk_org_id')
