"""weekly_champions: unique (week_start, champion_type, subject)

Revision ID: e6c1b4d7a903
Revises: d3a7c5e91b24

Audit F-15. The weekly champions job upserts on this key, so duplicate
execution converges instead of duplicating rows. Overall/speed champions
store subject = '' (not NULL, which a unique index would treat as distinct).

Idempotent: skips if the index exists. Existing NULL subjects become ''.
The table had no writer before this revision, so duplicates are not
expected; if any exist the lowest ids are removed (derived data - the job
recomputes it).
"""
from alembic import op
import sqlalchemy as sa

revision = "e6c1b4d7a903"
down_revision = "d3a7c5e91b24"
branch_labels = None
depends_on = None

INDEX = "uq_champion_week_type_subject"


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "weekly_champions" not in insp.get_table_names():
        return  # created (with the index) only by the previous revision's models; nothing to do

    conn.execute(sa.text("UPDATE weekly_champions SET subject = '' WHERE subject IS NULL"))
    conn.execute(sa.text(
        "DELETE FROM weekly_champions WHERE id NOT IN ("
        "  SELECT MAX(id) FROM weekly_champions GROUP BY week_start, champion_type, subject)"
    ))
    if INDEX not in {i["name"] for i in sa.inspect(conn).get_indexes("weekly_champions")}:
        op.create_index(INDEX, "weekly_champions", ["week_start", "champion_type", "subject"], unique=True)


def downgrade():
    insp = sa.inspect(op.get_bind())
    if "weekly_champions" in insp.get_table_names() and INDEX in {
        i["name"] for i in insp.get_indexes("weekly_champions")
    }:
        op.drop_index(INDEX, table_name="weekly_champions")
