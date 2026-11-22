"""reputation_history.idempotency_key

Adds a nullable idempotency_key column with a unique index so one-time
reputation awards (post milestones, solution marks, helpful marks) cannot be
paid out twice for the same event.

Idempotent: the schema was historically created by db.create_all(), so each
step checks the live schema first. Existing rows keep a NULL key; NULLs do not
collide in the unique index on PostgreSQL or SQLite.

Revision ID: c5d2f8a41b97
Revises: d4e6f8a0b213
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa

revision = "c5d2f8a41b97"
down_revision = "d4e6f8a0b213"
branch_labels = None
depends_on = None

TABLE = "reputation_history"
INDEX = "uq_rep_history_idempotency_key"


def upgrade():
    insp = sa.inspect(op.get_bind())
    if "idempotency_key" not in {c["name"] for c in insp.get_columns(TABLE)}:
        op.add_column(TABLE, sa.Column("idempotency_key", sa.String(length=120), nullable=True))
    if INDEX not in {i["name"] for i in insp.get_indexes(TABLE)}:
        op.create_index(INDEX, TABLE, ["idempotency_key"], unique=True)


def downgrade():
    insp = sa.inspect(op.get_bind())
    if INDEX in {i["name"] for i in insp.get_indexes(TABLE)}:
        op.drop_index(INDEX, table_name=TABLE)
    if "idempotency_key" in {c["name"] for c in insp.get_columns(TABLE)}:
        op.drop_column(TABLE, "idempotency_key")
