"""drop unused profile tables and columns

Revision ID: d4e6f8a0b213
Revises: a3c5d7e9f102
Create Date: 2026-11-22 00:00:01.000000

Second half of the profile production-readiness migration (contract step).
Run it AFTER the application code that no longer reads or writes these
objects is deployed; a still-running old version would fail on the missing
columns.

Dropped, all verified unreferenced by the application:

* table profile_change_history          - never written or read
* student_profiles.full_name            - a copy of users.name that nothing read
* student_profiles.registered_at        - never read (users.joined_at exists)
* users.study_schedule                  - replaced by
  onboarding_details.study_schedule (copied there by the previous revision)

(The other unused student_profiles columns - pin, username, status,
date_of_birth, guardian_* - were dropped by the earlier auth/onboarding
migration c3a1d94e7b52.)

Every drop checks that the object exists first. The downgrade re-creates the
columns/table empty (the dropped data is not recoverable) with nullable
columns, so the previous code path can run again.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e6f8a0b213'
down_revision = 'a3c5d7e9f102'
branch_labels = None
depends_on = None


STUDENT_PROFILE_COLUMNS = ("registered_at", "full_name")


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "profile_change_history" in tables:
        op.drop_table("profile_change_history")

    if "student_profiles" in tables:
        existing = {c["name"] for c in insp.get_columns("student_profiles")}
        # Indexes on a dropped column must go before the column.
        for index in insp.get_indexes("student_profiles"):
            if set(index["column_names"]) & set(STUDENT_PROFILE_COLUMNS):
                op.drop_index(index["name"], table_name="student_profiles")
        with op.batch_alter_table("student_profiles") as batch:
            for name in STUDENT_PROFILE_COLUMNS:
                if name in existing:
                    batch.drop_column(name)

    if "users" in tables and "study_schedule" in {c["name"] for c in insp.get_columns("users")}:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("study_schedule")


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "users" in tables and "study_schedule" not in {c["name"] for c in insp.get_columns("users")}:
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("study_schedule", sa.JSON(), nullable=True))

    if "student_profiles" in tables:
        existing = {c["name"] for c in insp.get_columns("student_profiles")}
        columns = {
            "registered_at": sa.Column("registered_at", sa.DateTime(), nullable=True),
            "full_name":     sa.Column("full_name", sa.String(120), nullable=True),
        }
        with op.batch_alter_table("student_profiles") as batch:
            for name, column in columns.items():
                if name not in existing:
                    batch.add_column(column)

    if "profile_change_history" not in tables:
        op.create_table(
            "profile_change_history",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("field_changed", sa.String(100), nullable=False),
            sa.Column("old_value", sa.String(500)),
            sa.Column("new_value", sa.String(500)),
            sa.Column("change_type", sa.String(50)),
            sa.Column("ip_address", sa.String(50)),
            sa.Column("user_agent", sa.String(200)),
            sa.Column("changed_at", sa.DateTime()),
        )
        op.create_index("ix_profile_change_history_user_id", "profile_change_history", ["user_id"])
        op.create_index("ix_profile_change_history_change_type", "profile_change_history", ["change_type"])
