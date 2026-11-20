"""connections audit hardening

Revision ID: c4d9a7e21f58
Revises: b17c0a9d2e41

Implements the schema side of FEATURE_PRODUCTION_READINESS_AUDIT.md:

* CON-05  de-duplicate connection pairs (blocked > accepted > pending >
          rejected, oldest id wins ties) and create the Postgres pair
          indexes (uq_connections_pair, idx_connections_pair_status) with
          IF NOT EXISTS so it is safe on databases that already have them.
* CON-07  connections.receiver_seen / requester_seen (back-filled from the
          legacy is_seen flag).
* CON-19  connections.request_message (back-filled for pending/rejected rows).
* CON-16  help_request_volunteers table, back-filled from the legacy JSON
          help_requests.volunteers column.
* CON-17  at most one *active* help request per user (older duplicates are
          expired first).

Every step is idempotent (inspector / IF NOT EXISTS) because some
environments were built with db.create_all() and already have part of this.

Legacy columns (connections.is_seen, connections.connection_type,
help_requests.volunteers, help_requests.broadcast_sent) are intentionally
NOT dropped here: ship the code first, drop them in a follow-up release.
"""
from alembic import op
import sqlalchemy as sa

revision = "c4d9a7e21f58"
down_revision = "b17c0a9d2e41"
branch_labels = None
depends_on = None


def _insp():
    return sa.inspect(op.get_bind())


def _has_table(name):
    return name in _insp().get_table_names()


def _has_column(table, column):
    return column in {c["name"] for c in _insp().get_columns(table)}


def _has_index(table, name):
    return name in {i["name"] for i in _insp().get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ---------------------------------------------------------------- connections
    if _has_table("connections"):
        # CON-05: remove duplicate pairs before the unique index is created.
        if is_pg:
            bind.execute(sa.text("""
                DELETE FROM connections c
                USING (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY LEAST(requester_id, receiver_id),
                                            GREATEST(requester_id, receiver_id)
                               ORDER BY CASE status
                                            WHEN 'blocked'  THEN 0
                                            WHEN 'accepted' THEN 1
                                            WHEN 'pending'  THEN 2
                                            ELSE 3 END,
                                        id
                           ) AS rn
                    FROM connections
                ) d
                WHERE c.id = d.id AND d.rn > 1
            """))

        # CON-07 / CON-19: new columns.
        if not _has_column("connections", "receiver_seen"):
            op.add_column("connections", sa.Column(
                "receiver_seen", sa.Boolean(), nullable=False, server_default=sa.false()))
            bind.execute(sa.text(
                "UPDATE connections SET receiver_seen = COALESCE(is_seen, FALSE)"))
        if not _has_column("connections", "requester_seen"):
            op.add_column("connections", sa.Column(
                "requester_seen", sa.Boolean(), nullable=False, server_default=sa.false()))
            bind.execute(sa.text(
                "UPDATE connections SET requester_seen = COALESCE(is_seen, FALSE)"))
        if not _has_column("connections", "request_message"):
            op.add_column("connections", sa.Column("request_message", sa.Text(), nullable=True))
            bind.execute(sa.text("""
                UPDATE connections
                SET request_message = requester_notes
                WHERE status IN ('pending', 'rejected')
                  AND requester_notes IS NOT NULL
                  AND requester_notes NOT LIKE 'Instant connection%'
                  AND requester_notes NOT LIKE 'Connected during onboarding%'
            """))

        # CON-05: pair indexes (Postgres only: LEAST/GREATEST).
        if is_pg:
            with op.get_context().autocommit_block():
                op.execute(
                    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_connections_pair_status "
                    "ON connections (LEAST(requester_id, receiver_id), "
                    "GREATEST(requester_id, receiver_id), status)"
                )
                op.execute(
                    "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_connections_pair "
                    "ON connections (LEAST(requester_id, receiver_id), "
                    "GREATEST(requester_id, receiver_id))"
                )

    # ------------------------------------------------------------- help requests
    if _has_table("help_requests"):
        if not _has_table("help_request_volunteers"):
            op.create_table(
                "help_request_volunteers",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("help_request_id", sa.Integer(),
                          sa.ForeignKey("help_requests.id", ondelete="CASCADE"), nullable=False),
                sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
                sa.Column("created_at", sa.DateTime(), nullable=False,
                          server_default=sa.func.now()),
                sa.UniqueConstraint("help_request_id", "user_id", name="uq_help_request_volunteer"),
            )
            op.create_index("ix_help_request_volunteers_help_request_id",
                            "help_request_volunteers", ["help_request_id"])
            op.create_index("ix_help_request_volunteers_user_id",
                            "help_request_volunteers", ["user_id"])

            # Back-fill from the legacy JSON list (portable: done in Python).
            rows = bind.execute(sa.text(
                "SELECT id, volunteers FROM help_requests WHERE volunteers IS NOT NULL"
            )).fetchall()
            import json
            for hr_id, raw in rows:
                try:
                    items = raw if isinstance(raw, list) else json.loads(raw or "[]")
                except Exception:
                    continue
                seen = set()
                for item in items:
                    uid = item.get("user_id") if isinstance(item, dict) else item
                    if not isinstance(uid, int) or uid in seen:
                        continue
                    seen.add(uid)
                    exists = bind.execute(
                        sa.text("SELECT 1 FROM users WHERE id = :u"), {"u": uid}).first()
                    if exists:
                        bind.execute(sa.text(
                            "INSERT INTO help_request_volunteers (help_request_id, user_id, created_at) "
                            "VALUES (:h, :u, CURRENT_TIMESTAMP)"), {"h": hr_id, "u": uid})

        # CON-17: one active help request per user. Expire older duplicates first.
        if not _has_index("help_requests", "uq_help_requests_one_active_per_user"):
            bind.execute(sa.text("""
                UPDATE help_requests SET status = 'expired'
                WHERE status = 'active' AND id NOT IN (
                    SELECT MAX(id) FROM help_requests WHERE status = 'active'
                    GROUP BY requester_id
                )
            """))
            op.create_index(
                "uq_help_requests_one_active_per_user", "help_requests", ["requester_id"],
                unique=True,
                postgresql_where=sa.text("status = 'active'"),
                sqlite_where=sa.text("status = 'active'"),
            )


def downgrade():
    bind = op.get_bind()
    if _has_table("help_requests") and _has_index("help_requests", "uq_help_requests_one_active_per_user"):
        op.drop_index("uq_help_requests_one_active_per_user", table_name="help_requests")
    if _has_table("help_request_volunteers"):
        op.drop_table("help_request_volunteers")
    if _has_table("connections"):
        for col in ("request_message", "requester_seen", "receiver_seen"):
            if _has_column("connections", col):
                op.drop_column("connections", col)
    # De-duplicated rows and the Postgres pair indexes are intentionally kept.
