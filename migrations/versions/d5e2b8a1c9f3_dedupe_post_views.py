"""deduplicate post_views and enforce one row per viewer per post

The old ``unique_daily_view`` constraint was ``(user_id, post_id, viewed_at)``;
because ``viewed_at`` has microsecond precision it never fired, and concurrent
view requests from one user created duplicate rows (analytics audit AN-05).

This migration

1. removes duplicate ``post_views`` rows, keeping the earliest row per
   (user_id, post_id),
2. recomputes ``posts.views_count`` for the posts that lost rows so it matches
   the number of distinct viewers,
3. drops ``unique_daily_view`` and adds ``uq_post_views_user_post``
   (``user_id, post_id``).

Every step first inspects the database, so it is safe on a database created
from the current models (``db.create_all()``) and on one that predates them.

Revision ID: d5e2b8a1c9f3
Revises: c3a1d94e7b52
"""
from alembic import op
import sqlalchemy as sa

revision = "d5e2b8a1c9f3"
down_revision = "c3a1d94e7b52"
branch_labels = None
depends_on = None

NEW_UNIQUE = "uq_post_views_user_post"
OLD_UNIQUE = "unique_daily_view"


def _unique_names(table):
    insp = sa.inspect(op.get_bind())
    names = {u["name"] for u in insp.get_unique_constraints(table) if u.get("name")}
    names |= {i["name"] for i in insp.get_indexes(table) if i.get("unique")}
    return names


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "post_views" not in insp.get_table_names():
        return

    affected = [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT DISTINCT post_id FROM post_views "
                "GROUP BY user_id, post_id HAVING COUNT(*) > 1"
            )
        )
    ]

    bind.execute(
        sa.text(
            "DELETE FROM post_views WHERE id NOT IN "
            "(SELECT MIN(id) FROM post_views GROUP BY user_id, post_id)"
        )
    )

    for post_id in affected:
        bind.execute(
            sa.text(
                "UPDATE posts SET views_count = "
                "(SELECT COUNT(*) FROM post_views WHERE post_views.post_id = :pid) "
                "WHERE id = :pid"
            ),
            {"pid": post_id},
        )

    existing = _unique_names("post_views")
    with op.batch_alter_table("post_views") as batch:
        if OLD_UNIQUE in existing:
            batch.drop_constraint(OLD_UNIQUE, type_="unique")
        if NEW_UNIQUE not in existing:
            batch.create_unique_constraint(NEW_UNIQUE, ["user_id", "post_id"])


def downgrade():
    insp = sa.inspect(op.get_bind())
    if "post_views" not in insp.get_table_names():
        return
    existing = _unique_names("post_views")
    with op.batch_alter_table("post_views") as batch:
        if NEW_UNIQUE in existing:
            batch.drop_constraint(NEW_UNIQUE, type_="unique")
        if OLD_UNIQUE not in existing:
            batch.create_unique_constraint(OLD_UNIQUE, ["user_id", "post_id", "viewed_at"])
