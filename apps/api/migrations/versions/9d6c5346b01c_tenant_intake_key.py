"""tenant intake key

Revision ID: 9d6c5346b01c
Revises: d29e626e6d49
Create Date: 2026-11-12 20:10:22.275409

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9d6c5346b01c'
down_revision: str | Sequence[str] | None = 'd29e626e6d49'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tenants', sa.Column('intake_key_hash', sa.Text(), nullable=True))
    op.add_column('tenants', sa.Column('intake_key_prefix', sa.Text(), nullable=True))
    op.create_unique_constraint('tenants_intake_key_hash_key', 'tenants', ['intake_key_hash'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('tenants_intake_key_hash_key', 'tenants', type_='unique')
    op.drop_column('tenants', 'intake_key_prefix')
    op.drop_column('tenants', 'intake_key_hash')
