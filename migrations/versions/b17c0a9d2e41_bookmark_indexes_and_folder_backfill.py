"""bookmark composite indexes + backfill folder_id from legacy folder strings

Revision ID: b17c0a9d2e41
Revises: f75e60334a86
"""
from alembic import op
import sqlalchemy as sa

revision = "b17c0a9d2e41"
down_revision = "f75e60334a86"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Indexes (on Postgres with a large table, run these CONCURRENTLY
    #    outside a transaction — see §11 rollout notes).
    op.create_index("idx_bookmarks_student_time", "bookmarks",
                    ["student_id", "bookmarked_at", "id"])
    op.create_index("idx_bookmarks_student_folder_time", "bookmarks",
                    ["student_id", "folder_id", "bookmarked_at", "id"])

    conn = op.get_bind()

    # 2. Normalise NULL/blank legacy folder strings.
    conn.execute(sa.text(
        "UPDATE bookmarks SET folder = 'Saved' WHERE folder IS NULL OR TRIM(folder) = ''"
    ))

    # 2b. Keyset pagination needs a non-NULL sort key.
    conn.execute(sa.text(
        "UPDATE bookmarks SET bookmarked_at = CURRENT_TIMESTAMP WHERE bookmarked_at IS NULL"
    ))

    # 3. One BookmarkFolder per (user, folder-string) that doesn't exist yet.
    #    NOTE: check bookmark_folders' NOT NULL columns in your DB; the
    #    model has defaults at the ORM level only, so supply them here.
    conn.execute(sa.text("""
        INSERT INTO bookmark_folders
            (user_id, name, icon, color, position, is_default, bookmark_count, created_at)
        SELECT DISTINCT b.student_id, b.folder, '📁', '#6B7280', 0,
               (b.folder = 'Saved'), 0, CURRENT_TIMESTAMP
        FROM bookmarks b
        WHERE b.folder_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM bookmark_folders f
              WHERE f.user_id = b.student_id AND f.name = b.folder
          )
    """))

    # 4. Point legacy bookmarks at their folder rows.
    conn.execute(sa.text("""
        UPDATE bookmarks
        SET folder_id = (
            SELECT f.id FROM bookmark_folders f
            WHERE f.user_id = bookmarks.student_id AND f.name = bookmarks.folder
        )
        WHERE folder_id IS NULL
    """))

    # 5. Recompute folder counters from truth.
    conn.execute(sa.text("""
        UPDATE bookmark_folders
        SET bookmark_count = (
            SELECT COUNT(*) FROM bookmarks b WHERE b.folder_id = bookmark_folders.id
        )
    """))

    # 6. Recompute Post.bookmark_count (the weekly reconciliation would do
    #    this eventually; doing it once here makes launch-day numbers right).
    conn.execute(sa.text("""
        UPDATE posts
        SET bookmark_count = (SELECT COUNT(*) FROM bookmarks b WHERE b.post_id = posts.id)
    """))


def downgrade():
    op.drop_index("idx_bookmarks_student_folder_time", table_name="bookmarks")
    op.drop_index("idx_bookmarks_student_time", table_name="bookmarks")
    # Data backfill is intentionally not reversed.
