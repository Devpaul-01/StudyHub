"""The auth/onboarding cleanup migration upgrades an old schema and is a no-op on a current one."""
import importlib.util
import pathlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = pathlib.Path(__file__).parents[3] / "migrations/versions/c3a1d94e7b52_auth_and_onboarding_cleanup.py"


def _load():
    spec = importlib.util.spec_from_file_location("auth_cleanup_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, fn_name="upgrade"):
    module = _load()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(module, fn_name)()


OLD_SCHEMA = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(120), last_login DATETIME)",
    """CREATE TABLE student_profiles (id INTEGER PRIMARY KEY, user_id INTEGER, pin VARCHAR(200) NOT NULL,
        username VARCHAR(50), full_name VARCHAR(120), date_of_birth DATE, guardian_name VARCHAR(120),
        guardian_contact VARCHAR(50), status VARCHAR(50))""",
    "CREATE UNIQUE INDEX ix_student_profiles_username ON student_profiles (username)",
    """CREATE TABLE onboarding_details (id INTEGER PRIMARY KEY, user_id INTEGER, email VARCHAR(120) NOT NULL,
        department VARCHAR(50))""",
    "CREATE UNIQUE INDEX uq_onboarding_email ON onboarding_details (email)",
    """CREATE TABLE password_reset_tokens (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
        token VARCHAR(500) NOT NULL, created_at DATETIME, expires_at DATETIME NOT NULL, used BOOLEAN, used_at DATETIME)""",
    "CREATE UNIQUE INDEX ix_password_reset_tokens_token ON password_reset_tokens (token)",
    """CREATE TABLE email_verification_tokens (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
        token VARCHAR(500) NOT NULL, created_at DATETIME, expires_at DATETIME NOT NULL, used BOOLEAN, used_at DATETIME)""",
    "CREATE UNIQUE INDEX ix_email_verification_tokens_token ON email_verification_tokens (token)",
    "CREATE TABLE refresh_tokens (id INTEGER PRIMARY KEY, user_id INTEGER, revoked BOOLEAN)",
    "INSERT INTO users (id, email) VALUES (1, 'a@example.com')",
    "INSERT INTO password_reset_tokens (user_id, token, expires_at, used) VALUES (1, 'raw-secret', '2099-01-01', 0)",
    "INSERT INTO refresh_tokens (user_id, revoked) VALUES (1, NULL)",
]


@pytest.fixture
def old_db():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for stmt in OLD_SCHEMA:
            conn.execute(sa.text(stmt))
    return engine


def _cols(engine, table):
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def test_upgrade_converts_an_old_schema(old_db):
    _run(old_db)

    assert "token_hash" in _cols(old_db, "password_reset_tokens")
    assert "token" not in _cols(old_db, "password_reset_tokens")
    assert "token_hash" in _cols(old_db, "email_verification_tokens")
    # raw tokens are not carried over
    with old_db.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM password_reset_tokens")).scalar() == 0
        assert conn.execute(sa.text("SELECT revoked FROM refresh_tokens")).scalar() in (0, False)
    assert _cols(old_db, "student_profiles") == {"id", "user_id", "full_name"}
    assert "email" not in _cols(old_db, "onboarding_details")
    assert "last_login" not in _cols(old_db, "users")
    assert "google_id" in _cols(old_db, "users")


def test_upgrade_is_idempotent(old_db):
    _run(old_db)
    _run(old_db)  # second run finds everything already converted


def test_upgrade_creates_missing_token_tables():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(120))"))
    _run(engine)
    assert {"token_hash", "expires_at", "used"} <= _cols(engine, "password_reset_tokens")
    assert {"token_hash"} <= _cols(engine, "email_verification_tokens")


def test_upgrade_is_a_noop_on_a_database_built_from_the_current_models(app, db_session):
    from extensions import db
    _run(db.engine)


def test_downgrade_restores_dropped_columns_empty(old_db):
    _run(old_db)
    _run(old_db, "downgrade")
    assert {"pin", "username", "date_of_birth"} <= _cols(old_db, "student_profiles")
    assert "last_login" in _cols(old_db, "users")
