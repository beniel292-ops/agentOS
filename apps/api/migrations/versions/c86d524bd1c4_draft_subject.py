"""draft subject

Revision ID: c86d524bd1c4
Revises: 9d6c5346b01c
Create Date: 2026-11-12 20:24:14.971662

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c86d524bd1c4'
down_revision: str | Sequence[str] | None = '9d6c5346b01c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('drafts', sa.Column('subject', sa.Text(), server_default=sa.text("''"), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('drafts', 'subject')
