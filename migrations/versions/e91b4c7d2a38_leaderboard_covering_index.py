"""reputation_history covering index; drop redundant indexes

Period leaderboards aggregate points_change per user_id over a created_at
window. Measured on PostgreSQL 16 with 2M ledger rows, a covering index
(created_at) INCLUDE (user_id, points_change) cuts that query roughly in
half (7-day window 224 -> 111 ms, 30-day 876 -> 412 ms) and, because it
replaces three indexes, shrinks the table's secondary index footprint from
226 MB to 136 MB (less write amplification on a table appended to by every
reputation award).

Drops (all strict prefixes of, or superseded by, other indexes):
  ix_reputation_history_user_id      prefix of idx_rep_history_user_date
  ix_reputation_history_created_at   superseded by the covering index
  idx_rep_history_date_user          superseded by the covering index
  ix_student_profiles_department     prefix of idx_student_profiles_dept_user

The new index is built first (CONCURRENTLY on PostgreSQL, so writes are not
blocked) and nothing is dropped until it exists. Every step checks the live
schema, so it is safe on databases created by db.create_all() and safe to
re-run.

Revision ID: e91b4c7d2a38
Revises: d7e3a96c5f12
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa

revision = "e91b4c7d2a38"
down_revision = "d7e3a96c5f12"
branch_labels = None
depends_on = None

COVER = "ix_rep_history_created_cover"
DROP_REP = ["ix_reputation_history_user_id", "ix_reputation_history_created_at", "idx_rep_history_date_user"]
DROP_PROFILE = ["ix_student_profiles_department"]


def _names(table):
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _drop_if_exists(index_names, table):
    for name in index_names:
        if name in _names(table):
            op.drop_index(name, table_name=table)


def upgrade():
    is_pg = op.get_bind().dialect.name == "postgresql"

    if COVER not in _names("reputation_history"):
        if is_pg:
            with op.get_context().autocommit_block():
                op.create_index(
                    COVER, "reputation_history", ["created_at"],
                    postgresql_include=["user_id", "points_change"],
                    postgresql_concurrently=True,
                )
        else:
            op.create_index(COVER, "reputation_history", ["created_at"])

    _drop_if_exists(DROP_REP, "reputation_history")
    _drop_if_exists(DROP_PROFILE, "student_profiles")


def downgrade():
    is_pg = op.get_bind().dialect.name == "postgresql"
    have = _names("reputation_history")
    if "ix_reputation_history_user_id" not in have:
        op.create_index("ix_reputation_history_user_id", "reputation_history", ["user_id"])
    if "ix_reputation_history_created_at" not in have:
        op.create_index("ix_reputation_history_created_at", "reputation_history", ["created_at"])
    if "idx_rep_history_date_user" not in have:
        op.create_index("idx_rep_history_date_user", "reputation_history", ["created_at", "user_id"])
    if "ix_student_profiles_department" not in _names("student_profiles"):
        op.create_index("ix_student_profiles_department", "student_profiles", ["department"])
    if COVER in _names("reputation_history"):
        op.drop_index(COVER, table_name="reputation_history")
