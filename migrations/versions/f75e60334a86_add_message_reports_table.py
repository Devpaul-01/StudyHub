"""add message_reports table

Revision ID: f75e60334a86
Revises:
Create Date: 2026-10-27 00:00:00.000000

Batch 5: new table backing the MessageReport model (see models.py for
the full "why" — routes/student/messages.py's report_message() route
has always existed but never persisted a report anywhere, only logged
it). This is the project's first-ever Alembic migration: no
migrations/ directory existed before this (Migrate() was instantiated
in app.py but `flask db init` had never been run), so this file also
establishes revision 0 for everything that comes after it. It makes no
assumption about any other table already matching models.py exactly —
it only creates the one new table this batch needs.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f75e60334a86'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'message_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('reported_by', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('reviewed_by', sa.Integer(), nullable=True),
        sa.Column('review_notes', sa.Text(), nullable=True),
        sa.Column('action_taken', sa.String(length=100), nullable=True),
        sa.Column('reported_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id']),
        sa.ForeignKeyConstraint(['reported_by'], ['users.id']),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('message_reports', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_message_reports_message_id'), ['message_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_message_reports_reported_by'), ['reported_by'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_message_reports_status'), ['status'], unique=False
        )


def downgrade():
    with op.batch_alter_table('message_reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_message_reports_status'))
        batch_op.drop_index(batch_op.f('ix_message_reports_reported_by'))
        batch_op.drop_index(batch_op.f('ix_message_reports_message_id'))
    op.drop_table('message_reports')
