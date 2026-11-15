"""questions: an agent asks the owner something by phone (call kind QUESTION)

Revision ID: c8e2f5a97d13
Revises: b4d9a6e27f31
Create Date: 2026-11-15 14:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c8e2f5a97d13'
down_revision: str | Sequence[str] | None = 'b4d9a6e27f31'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ('morning', 'evening', 'callback', 'notify')
NEW = (*OLD, 'question')
CHECKS = (('call_kind', 'scheduled_calls'), ('checkin_kind', 'checkins'))


def _constraints(kinds: tuple[str, ...]) -> None:
    # The CHECKs are created by the Enum types; autogenerate can't see changes to them.
    values = ", ".join(f"'{k}'" for k in kinds)
    for name, table in CHECKS:
        op.drop_constraint(name, table, type_='check')
        op.create_check_constraint(name, table, f"kind IN ({values})")


def upgrade() -> None:
    op.create_table(
        'questions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('asked_by', sa.Text(), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('context', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('pending', 'calling', 'answered', 'unanswered', 'cancelled',
                                    name='question_status', native_enum=False,
                                    create_constraint=True, validate_strings=True, length=20),
                  nullable=False),
        sa.Column('answer', sa.Text(), nullable=True),
        sa.Column('call_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('char_length(question) BETWEEN 1 AND 300',
                           name='ck_questions_question_len'),
        sa.CheckConstraint('char_length(context) <= 1000', name='ck_questions_context_len'),
        sa.CheckConstraint('char_length(answer) <= 2000', name='ck_questions_answer_len'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='fk_questions_tenant_id',
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['call_id'], ['scheduled_calls.id'], name='fk_questions_call_id',
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name='pk_questions'),
    )
    op.create_index('ix_questions_tenant_id_status', 'questions', ['tenant_id', 'status'])
    op.add_column('scheduled_calls', sa.Column('question_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_scheduled_calls_question_id', 'scheduled_calls', 'questions',
                          ['question_id'], ['id'], ondelete='SET NULL')
    _constraints(NEW)


def downgrade() -> None:
    # The old CHECK cannot hold 'question' at all: drop those rows first.
    op.execute("DELETE FROM checkins WHERE kind = 'question'")
    op.execute("DELETE FROM scheduled_calls WHERE kind = 'question'")
    _constraints(OLD)
    op.drop_constraint('fk_scheduled_calls_question_id', 'scheduled_calls', type_='foreignkey')
    op.drop_column('scheduled_calls', 'question_id')
    op.drop_table('questions')  # its index and constraints go with the table
