"""homework feature: baseline schema + integrity constraints

Revision ID: d3a7c5e91b24
Revises: b17c0a9d2e41

Audit findings F-03, F-04, F-05, F-26 (FEATURE_PRODUCTION_READINESS_AUDIT.md).

Until now nothing in this repository created or evolved ``assignments``,
``homework_submissions``, ``activity_feed`` or ``weekly_champions`` (they were
created out-of-band), and ``homework_submissions.feedback_rating`` had no
migration at all. This revision is written to be SAFE TO RUN AGAINST EITHER:

* an empty database   -> creates the four tables in full;
* an existing database (hand-managed, any drift) -> only adds what is
  missing. It never drops or rewrites existing columns/data except where
  stated below. Every step checks the live schema first, so re-running it is
  a no-op.

What it does, in order
  1. Create any of the four tables that do not exist.
  2. Add ``homework_submissions.feedback_rating`` if missing (F-04).
  3. Normalise legacy values so constraints can be applied:
     lower-case/trim difficulty & status, map unknown values to safe
     defaults, ``mind_blown`` -> ``mindblown``, out-of-range ratings -> NULL.
  4. De-duplicate (assignment_id, helper_id): the most advanced submission
     stays live, the others are marked ``cancelled`` (kept, not deleted)
     (F-05). This must precede the unique index.
  5. Partial UNIQUE index on (assignment_id, helper_id) WHERE
     status <> 'cancelled', and composite indexes for the hot queries (F-26).
  6. PostgreSQL only: FK ``ON DELETE`` rules and CHECK constraints
     (added NOT VALID, then VALIDATEd). SQLite cannot ALTER constraints; on
     SQLite the model's ``create_all`` already carries them for new DBs.

Operational notes
  * On a large PostgreSQL table build the indexes CONCURRENTLY out-of-band
    first (same names); step 5 skips indexes that already exist.
  * ``downgrade`` removes only what this revision adds (indexes, checks);
    it does not drop tables or data.
"""
from alembic import op
import sqlalchemy as sa

revision = "d3a7c5e91b24"
down_revision = "b17c0a9d2e41"
branch_labels = None
depends_on = None


STATUS_RANK = {
    "completed": 6, "reviewed": 5, "submitted": 4,
    "accepted": 3, "pending": 2, "declined": 1,
}

SUBMISSION_STATUSES = (
    "pending", "accepted", "submitted", "reviewed", "completed", "declined", "cancelled",
)

# (table, column, referred table, ON DELETE rule)
FK_RULES = [
    ("assignments", "user_id", "users", "CASCADE"),
    ("homework_submissions", "assignment_id", "assignments", "SET NULL"),
    ("homework_submissions", "requester_id", "users", "CASCADE"),
    ("homework_submissions", "helper_id", "users", "CASCADE"),
    ("activity_feed", "user_id", "users", "CASCADE"),
    ("weekly_champions", "user_id", "users", "CASCADE"),
]

# (table, name, expression)
CHECKS = [
    ("assignments", "ck_assignments_status",
     "status IN ('not_started', 'in_progress', 'completed')"),
    ("assignments", "ck_assignments_difficulty",
     "difficulty IN ('easy', 'medium', 'hard')"),
    ("homework_submissions", "ck_hw_submissions_difficulty",
     "difficulty IN ('easy', 'medium', 'hard')"),
    ("homework_submissions", "ck_hw_submissions_reaction",
     "reaction_type IN ('thanks', 'lifesaver', 'mindblown', 'perfect')"),
    ("homework_submissions", "ck_hw_submissions_rating",
     "feedback_rating BETWEEN 1 AND 5"),
]

NEW_INDEXES = [
    ("assignments", "ix_assignments_user_status", ["user_id", "status"]),
    ("assignments", "ix_assignments_shared_status_user",
     ["is_shared_for_help", "status", "user_id"]),
    ("homework_submissions", "ix_hw_sub_helper_status", ["helper_id", "status"]),
    ("homework_submissions", "ix_hw_sub_requester_status", ["requester_id", "status"]),
]
UNIQUE_INDEX = "uq_hw_sub_active_assignment_helper"


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def _insp():
    return sa.inspect(op.get_bind())


def _has_table(name):
    return name in _insp().get_table_names()


def _has_column(table, column):
    return any(c["name"] == column for c in _insp().get_columns(table))


def _index_names(table):
    return {i["name"] for i in _insp().get_indexes(table)}


def _is_pg():
    return op.get_bind().dialect.name == "postgresql"


# ----------------------------------------------------------------------------
# 1. tables
# ----------------------------------------------------------------------------

def _create_missing_tables():
    if not _has_table("assignments"):
        op.create_table(
            "assignments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("resources", sa.JSON()),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("subject", sa.String(100)),
            sa.Column("description", sa.Text()),
            sa.Column("due_date", sa.DateTime(), nullable=False),
            sa.Column("estimated_hours", sa.Float()),
            sa.Column("time_spent_minutes", sa.Integer()),
            sa.Column("difficulty", sa.String(20)),
            sa.Column("status", sa.String(20)),
            sa.Column("priority_score", sa.Float()),
            sa.Column("is_shared_for_help", sa.Boolean()),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("completed_at", sa.DateTime()),
        )
        op.create_index("ix_assignments_user_id", "assignments", ["user_id"])
        op.create_index("ix_assignments_subject", "assignments", ["subject"])
        op.create_index("ix_assignments_due_date", "assignments", ["due_date"])
        op.create_index("ix_assignments_status", "assignments", ["status"])
        op.create_index("ix_assignments_is_shared_for_help", "assignments", ["is_shared_for_help"])

    if not _has_table("homework_submissions"):
        op.create_table(
            "homework_submissions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("assignment_id", sa.Integer(), sa.ForeignKey("assignments.id", ondelete="SET NULL")),
            sa.Column("requester_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("helper_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("subject", sa.String(100)),
            sa.Column("difficulty", sa.String(20)),
            sa.Column("solution_text", sa.Text()),
            sa.Column("solution_resources", sa.JSON()),
            sa.Column("submitted_at", sa.DateTime()),
            sa.Column("feedback_text", sa.Text()),
            sa.Column("feedback_resources", sa.JSON()),
            sa.Column("feedback_at", sa.DateTime()),
            sa.Column("status", sa.Enum(*SUBMISSION_STATUSES, name="homeworksubmissionstatus",
                                        native_enum=False, create_constraint=False)),
            sa.Column("reaction_type", sa.String(50)),
            sa.Column("response_time_seconds", sa.Integer()),
            sa.Column("reaction_at", sa.DateTime()),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("is_marked_helpful", sa.Boolean()),
            sa.Column("feedback_rating", sa.Integer()),
        )
        op.create_index("ix_homework_submissions_assignment_id", "homework_submissions", ["assignment_id"])
        op.create_index("ix_homework_submissions_requester_id", "homework_submissions", ["requester_id"])
        op.create_index("ix_homework_submissions_helper_id", "homework_submissions", ["helper_id"])
        op.create_index("ix_homework_submissions_subject", "homework_submissions", ["subject"])
        op.create_index("ix_homework_submissions_status", "homework_submissions", ["status"])
        op.create_index("ix_homework_submissions_created_at", "homework_submissions", ["created_at"])

    if not _has_table("activity_feed"):
        op.create_table(
            "activity_feed",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("activity_type", sa.String(50), nullable=False),
            sa.Column("activity_data", sa.JSON()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )
        op.create_index("idx_activity_user_id", "activity_feed", ["user_id"])
        op.create_index("idx_activity_type", "activity_feed", ["activity_type"])
        op.create_index("idx_activity_created_at", "activity_feed", ["created_at"])
        op.create_index("idx_activity_expires_at", "activity_feed", ["expires_at"])

    if not _has_table("weekly_champions"):
        op.create_table(
            "weekly_champions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject", sa.String(100)),
            sa.Column("help_count", sa.Integer(), nullable=False),
            sa.Column("week_start", sa.Date(), nullable=False),
            sa.Column("week_end", sa.Date(), nullable=False),
            sa.Column("avg_response_time_minutes", sa.String(50)),
            sa.Column("champion_type", sa.String(50), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("idx_champion_user_id", "weekly_champions", ["user_id"])
        op.create_index("idx_champion_week", "weekly_champions", ["week_start", "week_end"])
        op.create_index("idx_champion_type", "weekly_champions", ["champion_type"])
        op.create_index("idx_champion_subject", "weekly_champions", ["subject"])


# ----------------------------------------------------------------------------
# 3. data normalisation
# ----------------------------------------------------------------------------

def _normalise_data():
    conn = op.get_bind()
    run = lambda sql: conn.execute(sa.text(sql))  # noqa: E731

    run("UPDATE assignments SET difficulty = LOWER(TRIM(difficulty)) "
        "WHERE difficulty IS NOT NULL AND difficulty <> LOWER(TRIM(difficulty))")
    run("UPDATE assignments SET difficulty = 'medium' "
        "WHERE difficulty IS NOT NULL AND difficulty NOT IN ('easy', 'medium', 'hard')")
    run("UPDATE assignments SET status = LOWER(TRIM(status)) "
        "WHERE status IS NOT NULL AND status <> LOWER(TRIM(status))")
    run("UPDATE assignments SET status = 'not_started' "
        "WHERE status IS NOT NULL AND status NOT IN ('not_started', 'in_progress', 'completed')")

    run("UPDATE homework_submissions SET difficulty = LOWER(TRIM(difficulty)) "
        "WHERE difficulty IS NOT NULL AND difficulty <> LOWER(TRIM(difficulty))")
    run("UPDATE homework_submissions SET difficulty = NULL "
        "WHERE difficulty IS NOT NULL AND difficulty NOT IN ('easy', 'medium', 'hard')")
    run("UPDATE homework_submissions SET reaction_type = 'mindblown' WHERE reaction_type = 'mind_blown'")
    run("UPDATE homework_submissions SET reaction_type = NULL "
        "WHERE reaction_type IS NOT NULL "
        "AND reaction_type NOT IN ('thanks', 'lifesaver', 'mindblown', 'perfect')")
    run("UPDATE homework_submissions SET feedback_rating = NULL "
        "WHERE feedback_rating IS NOT NULL AND (feedback_rating < 1 OR feedback_rating > 5)")


# ----------------------------------------------------------------------------
# 4. de-duplication
# ----------------------------------------------------------------------------

def _dedupe_submissions():
    """Keep the most advanced live submission per (assignment, helper);
    mark the rest 'cancelled'. Nothing is deleted."""
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, assignment_id, helper_id, status FROM homework_submissions "
        "WHERE assignment_id IS NOT NULL AND (status IS NULL OR status <> 'cancelled')"
    )).fetchall()

    groups = {}
    for row in rows:
        groups.setdefault((row[1], row[2]), []).append(row)

    losers = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: (STATUS_RANK.get(r[3] or "pending", 0), r[0]), reverse=True)
        losers.extend(r[0] for r in members[1:])

    for sub_id in losers:
        conn.execute(
            sa.text("UPDATE homework_submissions SET status = 'cancelled' WHERE id = :id"),
            {"id": sub_id},
        )


# ----------------------------------------------------------------------------
# 5. indexes
# ----------------------------------------------------------------------------

def _create_indexes():
    for table, name, cols in NEW_INDEXES:
        if name not in _index_names(table):
            op.create_index(name, table, cols)

    if UNIQUE_INDEX not in _index_names("homework_submissions"):
        op.create_index(
            UNIQUE_INDEX, "homework_submissions", ["assignment_id", "helper_id"],
            unique=True,
            postgresql_where=sa.text("status <> 'cancelled'"),
            sqlite_where=sa.text("status <> 'cancelled'"),
        )


# ----------------------------------------------------------------------------
# 6. PostgreSQL-only: FK rules and CHECK constraints
# ----------------------------------------------------------------------------

def _apply_fk_rules():
    insp = _insp()
    for table, column, ref_table, rule in FK_RULES:
        for fk in insp.get_foreign_keys(table):
            if fk["constrained_columns"] != [column] or fk["referred_table"] != ref_table:
                continue
            current = ((fk.get("options") or {}).get("ondelete") or "").upper()
            if current == rule:
                break
            if fk.get("name"):
                op.drop_constraint(fk["name"], table, type_="foreignkey")
            op.create_foreign_key(
                f"{table}_{column}_fkey", table, ref_table, [column], ["id"], ondelete=rule
            )
            break


def _apply_checks():
    conn = op.get_bind()
    existing = {}
    for table in ("assignments", "homework_submissions"):
        existing[table] = {c["name"] for c in _insp().get_check_constraints(table)}
    for table, name, expression in CHECKS:
        if name in existing[table]:
            continue
        conn.execute(sa.text(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression}) NOT VALID"
        ))
        conn.execute(sa.text(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}"))


# ----------------------------------------------------------------------------

def upgrade():
    _create_missing_tables()

    if not _has_column("homework_submissions", "feedback_rating"):
        op.add_column("homework_submissions", sa.Column("feedback_rating", sa.Integer(), nullable=True))

    _normalise_data()
    _dedupe_submissions()
    _create_indexes()

    if _is_pg():
        _apply_fk_rules()
        _apply_checks()


def downgrade():
    if _is_pg():
        for table, name, _expr in CHECKS:
            op.get_bind().execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))

    names = {t: _index_names(t) for t in ("assignments", "homework_submissions")}
    if UNIQUE_INDEX in names["homework_submissions"]:
        op.drop_index(UNIQUE_INDEX, table_name="homework_submissions")
    for table, name, _cols in NEW_INDEXES:
        if name in names[table]:
            op.drop_index(name, table_name=table)
