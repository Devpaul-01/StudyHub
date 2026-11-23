"""leaderboard_snapshots: snapshot_date + one snapshot per user/type/day

take_snapshot() guarded against re-runs with a check-then-insert that two
concurrent callers could both pass, producing duplicate rows. This adds a
snapshot_date column and a unique index so the database enforces it.

Steps (each checks the live schema, so it is safe on databases created by
db.create_all() and safe to re-run):
  1. add snapshot_date (nullable)
  2. backfill it from created_at
  3. delete duplicate rows, keeping the earliest per user/type/day. Snapshots
     are derived rank data, so removing a same-day duplicate loses nothing.
  4. make it NOT NULL
  5. create the unique index
  6. drop idx_lb_snap_user_id, which is a strict prefix of
     idx_lb_snap_user_type_date and therefore redundant

Run this BEFORE deploying code that writes snapshot_date.

Revision ID: d7e3a96c5f12
Revises: c5d2f8a41b97
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa

revision = "d7e3a96c5f12"
down_revision = "c5d2f8a41b97"
branch_labels = None
depends_on = None

TABLE = "leaderboard_snapshots"
UNIQUE = "uq_lb_snap_user_type_day"
REDUNDANT = "idx_lb_snap_user_id"


def _cols(insp):
    return {c["name"]: c for c in insp.get_columns(TABLE)}


def _indexes(insp):
    return {i["name"] for i in insp.get_indexes(TABLE)}


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "snapshot_date" not in _cols(insp):
        op.add_column(TABLE, sa.Column("snapshot_date", sa.Date(), nullable=True))

    op.execute(sa.text(f"UPDATE {TABLE} SET snapshot_date = date(created_at) WHERE snapshot_date IS NULL"))
    op.execute(sa.text(
        f"DELETE FROM {TABLE} WHERE id NOT IN ("
        f"SELECT MIN(id) FROM {TABLE} GROUP BY user_id, snapshot_type, snapshot_date)"
    ))

    insp = sa.inspect(bind)
    if _cols(insp)["snapshot_date"]["nullable"]:
        with op.batch_alter_table(TABLE) as batch:
            batch.alter_column("snapshot_date", existing_type=sa.Date(), nullable=False)

    insp = sa.inspect(bind)
    if UNIQUE not in _indexes(insp):
        op.create_index(UNIQUE, TABLE, ["user_id", "snapshot_type", "snapshot_date"], unique=True)
    if REDUNDANT in _indexes(insp):
        op.drop_index(REDUNDANT, table_name=TABLE)


def downgrade():
    insp = sa.inspect(op.get_bind())
    if UNIQUE in _indexes(insp):
        op.drop_index(UNIQUE, table_name=TABLE)
    if REDUNDANT not in _indexes(insp):
        op.create_index(REDUNDANT, TABLE, ["user_id"])
    if "snapshot_date" in _cols(insp):
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column("snapshot_date")
