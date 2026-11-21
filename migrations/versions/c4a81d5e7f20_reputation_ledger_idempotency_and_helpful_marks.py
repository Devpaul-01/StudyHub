"""reputation ledger idempotency key, helpful-mark soft delete, reputation floor

Revision ID: c4a81d5e7f20
Revises: b17c0a9d2e41

Schema drift fix: ``comment_helpful_marks.is_active`` exists on the model
but was never migrated. Every step is guarded with an inspector check so
the revision is safe on databases that were built by ``db.create_all()``
(and may already have some of these objects) as well as on older ones.
"""
from alembic import op
import sqlalchemy as sa

revision = "c4a81d5e7f20"
down_revision = "b17c0a9d2e41"
branch_labels = None
depends_on = None


def _columns(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def _indexes(insp, table):
    return {i["name"] for i in insp.get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1. comment_helpful_marks.is_active (existing rows are active marks).
    if "is_active" not in _columns(insp, "comment_helpful_marks"):
        with op.batch_alter_table("comment_helpful_marks") as batch:
            batch.add_column(sa.Column(
                "is_active", sa.Boolean(), nullable=False,
                server_default=sa.true(),
            ))

    # 2. reputation_history.idempotency_key + unique index.
    if "idempotency_key" not in _columns(insp, "reputation_history"):
        with op.batch_alter_table("reputation_history") as batch:
            batch.add_column(sa.Column("idempotency_key", sa.String(200), nullable=True))
    insp = sa.inspect(bind)
    if "uq_reputation_history_idempotency_key" not in _indexes(insp, "reputation_history"):
        op.create_index(
            "uq_reputation_history_idempotency_key",
            "reputation_history", ["idempotency_key"], unique=True,
        )

    # 3. Redundant single-column index (covered by idx_rep_history_user_date).
    if "ix_reputation_history_user_id" in _indexes(insp, "reputation_history"):
        op.drop_index("ix_reputation_history_user_id", table_name="reputation_history")

    # 4. Non-negative reputation backstop. Clamp any legacy negatives first
    #    so the constraint can be created.
    bind.execute(sa.text("UPDATE users SET reputation = 0 WHERE reputation < 0"))
    existing = {c["name"] for c in insp.get_check_constraints("users")}
    if "ck_users_reputation_non_negative" not in existing:
        with op.batch_alter_table("users") as batch:
            batch.create_check_constraint(
                "ck_users_reputation_non_negative", "reputation >= 0"
            )


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_reputation_non_negative", type_="check")
    op.drop_index("uq_reputation_history_idempotency_key", table_name="reputation_history")
    op.create_index("ix_reputation_history_user_id", "reputation_history", ["user_id"])
    with op.batch_alter_table("reputation_history") as batch:
        batch.drop_column("idempotency_key")
    with op.batch_alter_table("comment_helpful_marks") as batch:
        batch.drop_column("is_active")
