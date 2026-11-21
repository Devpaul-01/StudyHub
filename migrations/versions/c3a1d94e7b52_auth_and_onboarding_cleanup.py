"""auth and onboarding cleanup

Brings an existing database in line with the authentication/onboarding audit
(AUD-14, AUD-20, data cleanup):

* password_reset_tokens / email_verification_tokens: the raw ``token`` column is
  replaced by ``token_hash`` (SHA-256). Raw tokens cannot be converted, so any
  outstanding links are discarded (they live at most 5 hours); users simply
  request a new one.
* refresh_tokens: ``revoked`` becomes NOT NULL.
* drop columns that were never read or are mirrored elsewhere:
  student_profiles.{pin, username, status, date_of_birth, guardian_name,
  guardian_contact}, onboarding_details.email, users.last_login
* onboarding_details.department grows to 100 characters (the API accepts 100)
* users.google_id is added (unique) when it is missing

Every step first inspects the database, so the migration is safe to run on a
database that was created from the current models (``db.create_all()``) and on
one that predates them.

Revision ID: c3a1d94e7b52
Revises: b17c0a9d2e41
"""
from alembic import op
import sqlalchemy as sa

revision = "c3a1d94e7b52"
down_revision = "b17c0a9d2e41"
branch_labels = None
depends_on = None

DROPPED_COLUMNS = {
    "student_profiles": ["pin", "username", "status", "date_of_birth", "guardian_name", "guardian_contact"],
    "onboarding_details": ["email"],
    "users": ["last_login"],
}


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table):
    insp = _inspector()
    if table not in insp.get_table_names():
        return {}
    return {c["name"]: c for c in insp.get_columns(table)}


def _indexes_and_uniques(table):
    insp = _inspector()
    names = {i["name"] for i in insp.get_indexes(table)}
    names |= {u["name"] for u in insp.get_unique_constraints(table) if u.get("name")}
    return names


def _drop_index_on(table, column):
    """Drop indexes/unique constraints that mention ``column`` (needed before dropping it)."""
    insp = _inspector()
    for idx in insp.get_indexes(table):
        if column in idx["column_names"]:
            op.drop_index(idx["name"], table_name=table)
    for uq in insp.get_unique_constraints(table):
        if column in uq["column_names"] and uq.get("name"):
            op.drop_constraint(uq["name"], table, type_="unique")


def _upgrade_one_time_token_table(table, ttl_note):
    cols = _columns(table)
    if not cols:
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("used_at", sa.DateTime()),
        )
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])
        op.create_index(f"ix_{table}_token_hash", table, ["token_hash"], unique=True)
        op.create_index(f"ix_{table}_expires_at", table, ["expires_at"])
        return

    if "token_hash" in cols:
        return  # already current

    # Raw tokens cannot be hashed after the fact: discard outstanding links.
    op.execute(sa.text(f"DELETE FROM {table}"))
    if "token" in cols:
        _drop_index_on(table, "token")
        with op.batch_alter_table(table) as batch:
            batch.drop_column("token")
    with op.batch_alter_table(table) as batch:
        batch.add_column(sa.Column("token_hash", sa.String(64), nullable=False))
        batch.alter_column("used", existing_type=sa.Boolean(), nullable=False,
                           existing_server_default=None, server_default=sa.false())
    op.create_index(f"ix_{table}_token_hash", table, ["token_hash"], unique=True)
    if f"ix_{table}_expires_at" not in _indexes_and_uniques(table):
        op.create_index(f"ix_{table}_expires_at", table, ["expires_at"])


def upgrade():
    # --- one-time tokens: hashed storage -------------------------------------------------
    _upgrade_one_time_token_table("password_reset_tokens", "1 hour")
    _upgrade_one_time_token_table("email_verification_tokens", "5 hours")

    # --- refresh tokens ------------------------------------------------------------------
    rt = _columns("refresh_tokens")
    if rt and rt["revoked"]["nullable"]:
        op.execute(sa.text("UPDATE refresh_tokens SET revoked = :f WHERE revoked IS NULL").bindparams(f=False))
        with op.batch_alter_table("refresh_tokens") as batch:
            batch.alter_column("revoked", existing_type=sa.Boolean(), nullable=False)

    # --- columns that were never read / are mirrored on users ------------------------------
    for table, columns in DROPPED_COLUMNS.items():
        existing = _columns(table)
        for column in columns:
            if column in existing:
                _drop_index_on(table, column)
                with op.batch_alter_table(table) as batch:
                    batch.drop_column(column)

    # --- department length -----------------------------------------------------------------
    ob = _columns("onboarding_details")
    if ob and getattr(ob["department"]["type"], "length", None) not in (None, 100):
        with op.batch_alter_table("onboarding_details") as batch:
            batch.alter_column("department", existing_type=sa.String(50), type_=sa.String(100))

    # --- users.google_id ---------------------------------------------------------------------
    users = _columns("users")
    if users and "google_id" not in users:
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("google_id", sa.String(255), nullable=True))
        op.create_index("ix_users_google_id", "users", ["google_id"], unique=True)


def downgrade():
    # Dropped columns come back empty and nullable. Token hashes cannot be turned
    # back into raw tokens, so the one-time token tables keep the hashed layout.
    for table, columns in DROPPED_COLUMNS.items():
        existing = _columns(table)
        types = {
            "date_of_birth": sa.Date(), "last_login": sa.DateTime(),
            "guardian_name": sa.String(120), "guardian_contact": sa.String(50),
            "pin": sa.String(200), "username": sa.String(50),
            "status": sa.String(50), "email": sa.String(120),
        }
        for column in columns:
            if existing and column not in existing:
                with op.batch_alter_table(table) as batch:
                    batch.add_column(sa.Column(column, types[column], nullable=True))
