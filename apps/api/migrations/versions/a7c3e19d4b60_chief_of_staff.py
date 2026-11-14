"""chief of staff: agent keys, settings, tasks, calls, check-ins, opportunities, memory

Revision ID: a7c3e19d4b60
Revises: e5a1f3c9b7d2
Create Date: 2026-11-14 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c3e19d4b60'
down_revision: str | Sequence[str] | None = 'e5a1f3c9b7d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True,
                   validate_strings=True, length=20)


def _id() -> sa.Column:
    return sa.Column('id', sa.Uuid(), nullable=False)


def _tenant() -> sa.Column:
    return sa.Column('tenant_id', sa.Uuid(), nullable=False)


def _created() -> sa.Column:
    return sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                     nullable=False)


def _fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=f'fk_{table}_tenant_id',
                                   ondelete='CASCADE')


CALL_KINDS = ('morning', 'evening', 'callback')


def upgrade() -> None:
    op.create_table(
        'agent_keys', _id(), _tenant(),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('scopes', sa.ARRAY(sa.Text()), nullable=False),
        sa.Column('key_hash', sa.Text(), nullable=False),
        sa.Column('key_prefix', sa.Text(), nullable=False),
        _created(),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        _fk('agent_keys'),
        sa.PrimaryKeyConstraint('id', name='pk_agent_keys'),
        sa.UniqueConstraint('key_hash', name='uq_agent_keys_key_hash'),
    )
    op.create_index('uq_agent_keys_tenant_id_name_active', 'agent_keys', ['tenant_id', 'name'],
                    unique=True, postgresql_where=sa.text('revoked_at IS NULL'))

    op.create_table(
        'cos_settings', _tenant(),
        sa.Column('stopped', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('stopped_by', sa.Text(), nullable=True),
        sa.Column('timezone', sa.Text(), server_default=sa.text("'Asia/Kolkata'"), nullable=False),
        sa.Column('morning_time', sa.Time(), server_default=sa.text("'08:30'"), nullable=False),
        sa.Column('evening_time', sa.Time(), server_default=sa.text("'21:00'"), nullable=False),
        sa.Column('quiet_start', sa.Time(), server_default=sa.text("'22:30'"), nullable=False),
        sa.Column('quiet_end', sa.Time(), server_default=sa.text("'07:30'"), nullable=False),
        sa.Column('max_calls_per_day', sa.Integer(), server_default=sa.text('6'), nullable=False),
        sa.Column('min_gap_minutes', sa.Integer(), server_default=sa.text('10'), nullable=False),
        sa.CheckConstraint('max_calls_per_day BETWEEN 1 AND 20', name='ck_cos_settings_max_calls'),
        sa.CheckConstraint('min_gap_minutes BETWEEN 5 AND 240', name='ck_cos_settings_min_gap'),
        _fk('cos_settings'),
        sa.PrimaryKeyConstraint('tenant_id', name='pk_cos_settings'),
    )

    op.create_table(
        'tasks', _id(), _tenant(),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('title_norm', sa.Text(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('status', _enum('task_status', 'pending', 'in_progress', 'done', 'dropped'),
                  nullable=False),
        sa.Column('priority', sa.Integer(), server_default=sa.text('2'), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('project', sa.Text(), nullable=True),
        sa.Column('source', _enum('task_source', 'voice', 'telegram', 'dashboard', 'agent'),
                  nullable=False),
        _created(),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('char_length(title) BETWEEN 1 AND 300', name='ck_tasks_title_len'),
        sa.CheckConstraint('priority BETWEEN 1 AND 3', name='ck_tasks_priority'),
        _fk('tasks'),
        sa.PrimaryKeyConstraint('id', name='pk_tasks'),
    )
    op.create_index('ix_tasks_tenant_id_status_due_date', 'tasks',
                    ['tenant_id', 'status', 'due_date'])
    op.create_index('uq_tasks_tenant_id_title_norm_open', 'tasks', ['tenant_id', 'title_norm'],
                    unique=True, postgresql_where=sa.text("status IN ('pending','in_progress')"))

    op.create_table(
        'scheduled_calls', _id(), _tenant(),
        sa.Column('kind', _enum('call_kind', *CALL_KINDS), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', _enum('call_status', 'pending', 'placed', 'completed', 'missed',
                                  'cancelled'), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Text(), nullable=False),
        _created(),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        _fk('scheduled_calls'),
        sa.PrimaryKeyConstraint('id', name='pk_scheduled_calls'),
    )
    op.create_index('uq_scheduled_calls_pending_callback', 'scheduled_calls', ['tenant_id'],
                    unique=True,
                    postgresql_where=sa.text("kind = 'callback' AND status = 'pending'"))
    op.create_index('uq_scheduled_calls_daily_slot', 'scheduled_calls',
                    ['tenant_id', 'kind', sa.text("((due_at AT TIME ZONE 'UTC')::date)")],
                    unique=True,
                    postgresql_where=sa.text(
                        "kind IN ('morning','evening') AND status <> 'cancelled'"))
    op.create_index('ix_scheduled_calls_tenant_id_due_at', 'scheduled_calls',
                    ['tenant_id', 'due_at'])

    op.create_table(
        'checkins', _id(), _tenant(),
        sa.Column('kind', _enum('checkin_kind', *CALL_KINDS), nullable=False),
        sa.Column('call_id', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('transcript', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        _created(),
        _fk('checkins'),
        sa.PrimaryKeyConstraint('id', name='pk_checkins'),
    )
    op.create_index('ix_checkins_tenant_id_started_at', 'checkins', ['tenant_id', 'started_at'])

    op.create_table(
        'opportunities', _id(), _tenant(),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('relevance', sa.Integer(), nullable=False),
        sa.Column('estimated_value', sa.Text(), nullable=True),
        sa.Column('deadline', sa.Date(), nullable=True),
        sa.Column('next_action', sa.Text(), nullable=True),
        sa.Column('verification_status',
                  _enum('opportunity_verification', 'unverified', 'verified', 'rejected'),
                  nullable=False),
        sa.Column('status', _enum('opportunity_status', 'new', 'shown', 'saved', 'dismissed'),
                  nullable=False),
        sa.Column('created_by', sa.Text(), nullable=False),
        _created(),
        sa.CheckConstraint('char_length(title) BETWEEN 1 AND 300',
                           name='ck_opportunities_title_len'),
        sa.CheckConstraint('relevance BETWEEN 0 AND 100', name='ck_opportunities_relevance'),
        _fk('opportunities'),
        sa.PrimaryKeyConstraint('id', name='pk_opportunities'),
    )
    op.create_index('uq_opportunities_tenant_id_url', 'opportunities', ['tenant_id', 'url'],
                    unique=True, postgresql_where=sa.text('url IS NOT NULL'))
    op.create_index('ix_opportunities_tenant_id_status', 'opportunities', ['tenant_id', 'status'])

    op.create_table(
        'memory_notes', _id(), _tenant(),
        sa.Column('kind', _enum('memory_kind', 'preference', 'project', 'decision', 'deadline',
                                'summary'), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=False),
        _created(),
        sa.CheckConstraint('char_length(text) BETWEEN 1 AND 2000', name='ck_memory_notes_text_len'),
        _fk('memory_notes'),
        sa.PrimaryKeyConstraint('id', name='pk_memory_notes'),
        sa.UniqueConstraint('tenant_id', 'content_hash',
                            name='uq_memory_notes_tenant_id_content_hash'),
    )
    op.create_index('ix_memory_notes_tenant_id_kind', 'memory_notes', ['tenant_id', 'kind'])


def downgrade() -> None:
    for table in ('memory_notes', 'opportunities', 'checkins', 'scheduled_calls', 'tasks',
                  'cos_settings', 'agent_keys'):
        op.drop_table(table)  # indexes and constraints go with the table
