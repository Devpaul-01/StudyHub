"""
tests/unit/test_homework_migration.py

Exercises migrations/versions/d3a7c5e91b24 (audit F-04, F-05, F-26) against
throw-away SQLite databases: empty DB, drifted legacy DB with dirty data and
duplicates, idempotency, downgrade, and parity with the SQLAlchemy models.
PostgreSQL-only steps (FK rules, NOT VALID checks) are skipped on SQLite by
the migration itself and are not exercised here.
"""
import importlib.util
import os

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATION = os.path.join(
    ROOT, "migrations", "versions", "d3a7c5e91b24_homework_schema_baseline_and_constraints.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("hw_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("INSERT INTO users (id) VALUES (1), (2), (3)"))
    return eng


def _run(engine, fn_name):
    module = _load()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(module, fn_name)()


def _indexes(engine, table):
    return {i["name"] for i in sa.inspect(engine).get_indexes(table)}


LEGACY_SUBMISSIONS = """
CREATE TABLE homework_submissions (
    id INTEGER PRIMARY KEY, assignment_id INTEGER, requester_id INTEGER NOT NULL,
    helper_id INTEGER NOT NULL, title VARCHAR(200) NOT NULL, description TEXT,
    subject VARCHAR(100), difficulty VARCHAR(20), solution_text TEXT,
    solution_resources JSON, submitted_at DATETIME, feedback_text TEXT,
    feedback_resources JSON, feedback_at DATETIME, status VARCHAR(9),
    reaction_type VARCHAR(50), response_time_seconds INTEGER, reaction_at DATETIME,
    created_at DATETIME, is_marked_helpful BOOLEAN
)
"""
LEGACY_ASSIGNMENTS = """
CREATE TABLE assignments (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, resources JSON,
    title VARCHAR(200) NOT NULL, subject VARCHAR(100), description TEXT,
    due_date DATETIME NOT NULL, estimated_hours FLOAT, time_spent_minutes INTEGER,
    difficulty VARCHAR(20), status VARCHAR(20), priority_score FLOAT,
    is_shared_for_help BOOLEAN, created_at DATETIME, completed_at DATETIME
)
"""


def test_empty_database_gets_all_tables_and_indexes(engine):
    _run(engine, "upgrade")

    tables = set(sa.inspect(engine).get_table_names())
    assert {"assignments", "homework_submissions", "activity_feed", "weekly_champions"} <= tables
    sub_idx = _indexes(engine, "homework_submissions")
    assert "uq_hw_sub_active_assignment_helper" in sub_idx
    assert "ix_hw_sub_helper_status" in sub_idx
    assert "ix_assignments_user_status" in _indexes(engine, "assignments")
    cols = {c["name"] for c in sa.inspect(engine).get_columns("homework_submissions")}
    assert "feedback_rating" in cols


def test_upgrade_is_idempotent(engine):
    _run(engine, "upgrade")
    _run(engine, "upgrade")  # must not raise or duplicate anything

    assert "uq_hw_sub_active_assignment_helper" in _indexes(engine, "homework_submissions")


def test_unique_index_blocks_live_duplicates_but_allows_cancelled(engine):
    _run(engine, "upgrade")
    ins = sa.text(
        "INSERT INTO homework_submissions (assignment_id, requester_id, helper_id, title, status) "
        "VALUES (10, 1, 2, 't', :s)"
    )
    with engine.begin() as conn:
        conn.execute(ins, {"s": "pending"})
        conn.execute(ins, {"s": "cancelled"})
        conn.execute(ins, {"s": "cancelled"})
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(ins, {"s": "submitted"})


def test_detached_rows_do_not_conflict(engine):
    _run(engine, "upgrade")
    ins = sa.text(
        "INSERT INTO homework_submissions (assignment_id, requester_id, helper_id, title, status) "
        "VALUES (NULL, 1, 2, 't', 'completed')"
    )
    with engine.begin() as conn:
        conn.execute(ins)
        conn.execute(ins)


def test_legacy_database_is_repaired_without_data_loss(engine):
    with engine.begin() as conn:
        conn.execute(sa.text(LEGACY_ASSIGNMENTS))
        conn.execute(sa.text(LEGACY_SUBMISSIONS))
        conn.execute(sa.text(
            "INSERT INTO assignments (id, user_id, title, due_date, difficulty, status) VALUES "
            "(1, 1, 'A', '2030-01-01', 'HARD ', 'In_Progress'), "
            "(2, 1, 'B', '2030-01-01', 'weird', 'banana')"
        ))
        # duplicates for (assignment 1, helper 2): completed should win
        conn.execute(sa.text(
            "INSERT INTO homework_submissions "
            "(id, assignment_id, requester_id, helper_id, title, status, reaction_type, difficulty) VALUES "
            "(1, 1, 1, 2, 't', 'pending', 'mind_blown', 'Hard'), "
            "(2, 1, 1, 2, 't', 'completed', 'thanks', 'zzz'), "
            "(3, 1, 1, 2, 't', 'submitted', 'junk', NULL), "
            "(4, 1, 1, 3, 't', 'pending', NULL, NULL)"
        ))

    _run(engine, "upgrade")

    with engine.connect() as conn:
        a = {r[0]: (r[1], r[2]) for r in conn.execute(sa.text("SELECT id, difficulty, status FROM assignments"))}
        s = {r[0]: r[1:] for r in conn.execute(sa.text(
            "SELECT id, status, reaction_type, difficulty FROM homework_submissions"))}
        total = conn.execute(sa.text("SELECT COUNT(*) FROM homework_submissions")).scalar()

    assert a[1] == ("hard", "in_progress")
    assert a[2] == ("medium", "not_started")
    assert total == 4  # nothing deleted
    assert s[2][0] == "completed"          # most advanced stays live
    assert s[1][0] == "cancelled" and s[3][0] == "cancelled"
    assert s[4][0] == "pending"            # different helper untouched
    assert s[1][1] == "mindblown"          # legacy key repaired
    assert s[3][1] is None                 # unknown reaction cleared
    assert s[2][2] is None                 # unknown difficulty cleared
    cols = {c["name"] for c in sa.inspect(engine).get_columns("homework_submissions")}
    assert "feedback_rating" in cols
    assert "uq_hw_sub_active_assignment_helper" in _indexes(engine, "homework_submissions")


def test_downgrade_removes_only_added_indexes(engine):
    _run(engine, "upgrade")
    _run(engine, "downgrade")

    assert "uq_hw_sub_active_assignment_helper" not in _indexes(engine, "homework_submissions")
    assert "ix_hw_sub_helper_status" not in _indexes(engine, "homework_submissions")
    assert "homework_submissions" in sa.inspect(engine).get_table_names()


@pytest.mark.parametrize(
    "table", ["assignments", "homework_submissions", "activity_feed", "weekly_champions"]
)
def test_migration_matches_models(engine, table):
    """Columns and index names the migration creates == what the models declare."""
    import models  # noqa: F401  (registers tables on db.metadata)
    from extensions import db

    _run(engine, "upgrade")
    insp = sa.inspect(engine)
    model_table = db.metadata.tables[table]

    assert {c["name"] for c in insp.get_columns(table)} == {c.name for c in model_table.columns}
    assert {i["name"] for i in insp.get_indexes(table)} == {i.name for i in model_table.indexes}
