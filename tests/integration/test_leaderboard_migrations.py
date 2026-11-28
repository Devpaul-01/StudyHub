"""The leaderboard Alembic revisions, run against a SQLite file with main's
migration runner. The 'old' schema is the three tables they touch, with data
that exercises the snapshot de-duplication."""
import pathlib
import subprocess
import sys

import pytest
import sqlalchemy as sa

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tests/integration/migration_runner.py"
BASE_REVISION = "d4e6f8a0b213"          # last revision before the leaderboard work
HEAD_REVISION = "e91b4c7d2a38"


def _run(db_file, action, revision):
    result = subprocess.run([sys.executable, str(RUNNER), str(db_file), action, revision],
                            capture_output=True, text=True, cwd=ROOT, timeout=180)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-3000:]


def _build_old_schema(engine):
    md = sa.MetaData()
    sa.Table("users", md, sa.Column("id", sa.Integer, primary_key=True))
    rh = sa.Table("reputation_history", md,
                  sa.Column("id", sa.Integer, primary_key=True),
                  sa.Column("user_id", sa.Integer, nullable=False),
                  sa.Column("points_change", sa.Integer, nullable=False),
                  sa.Column("created_at", sa.DateTime))
    sa.Index("ix_reputation_history_user_id", rh.c.user_id)
    sa.Index("ix_reputation_history_created_at", rh.c.created_at)
    sa.Index("idx_rep_history_date_user", rh.c.created_at, rh.c.user_id)
    sa.Index("idx_rep_history_user_date", rh.c.user_id, rh.c.created_at)
    ls = sa.Table("leaderboard_snapshots", md,
                  sa.Column("id", sa.Integer, primary_key=True),
                  sa.Column("user_id", sa.Integer, nullable=False),
                  sa.Column("snapshot_type", sa.String(20), nullable=False),
                  sa.Column("global_rank", sa.Integer),
                  sa.Column("score", sa.Integer),
                  sa.Column("created_at", sa.DateTime, nullable=False))
    sa.Index("idx_lb_snap_user_id", ls.c.user_id)
    sa.Index("idx_lb_snap_user_type_date", ls.c.user_id, ls.c.snapshot_type, ls.c.created_at)
    sp = sa.Table("student_profiles", md,
                  sa.Column("id", sa.Integer, primary_key=True),
                  sa.Column("user_id", sa.Integer, nullable=False),
                  sa.Column("department", sa.String(100)))
    sa.Index("ix_student_profiles_department", sp.c.department)
    sa.Index("idx_student_profiles_dept_user", sp.c.department, sp.c.user_id)
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO users (id) VALUES (1), (2)"))
        # user 1 has the same-day duplicate the old check-then-insert could produce
        conn.execute(sa.text(
            "INSERT INTO leaderboard_snapshots (user_id, snapshot_type, global_rank, score, created_at) VALUES "
            "(1,'weekly',1,0,'2026-11-15 00:05:00'),(1,'weekly',1,0,'2026-11-15 00:05:03'),"
            "(1,'weekly',2,0,'2026-11-08 00:05:00'),(2,'weekly',2,0,'2026-11-15 00:05:00')"))


def _index_names(engine, table):
    return {i["name"] for i in sa.inspect(engine).get_indexes(table)}


def _columns(engine, table):
    return {c["name"]: c for c in sa.inspect(engine).get_columns(table)}


@pytest.fixture()
def upgraded(tmp_path):
    db_file = tmp_path / "leaderboard_migration.db"
    engine = sa.create_engine(f"sqlite:///{db_file}")
    _build_old_schema(engine)
    _run(db_file, "stamp", BASE_REVISION)
    _run(db_file, "upgrade", HEAD_REVISION)
    return db_file, sa.create_engine(f"sqlite:///{db_file}")


def test_idempotency_key_is_added_with_a_unique_index(upgraded):
    _, engine = upgraded
    assert "idempotency_key" in _columns(engine, "reputation_history")
    assert "uq_rep_history_idempotency_key" in _index_names(engine, "reputation_history")


def test_snapshot_dates_are_backfilled_and_same_day_duplicates_removed(upgraded):
    _, engine = upgraded
    with engine.connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT user_id, snapshot_date FROM leaderboard_snapshots ORDER BY user_id, snapshot_date")).all()
    assert len(rows) == 3                                  # 4 rows minus the duplicate
    assert all(r.snapshot_date is not None for r in rows)
    assert _columns(engine, "leaderboard_snapshots")["snapshot_date"]["nullable"] is False
    assert "uq_lb_snap_user_type_day" in _index_names(engine, "leaderboard_snapshots")


def test_the_database_now_rejects_a_same_day_duplicate(upgraded):
    _, engine = upgraded
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO leaderboard_snapshots (user_id, snapshot_type, global_rank, score, created_at, snapshot_date) "
                "VALUES (2,'weekly',9,0,'2026-11-15 12:00:00','2026-11-15')"))


def test_redundant_indexes_are_dropped_and_the_covering_one_exists(upgraded):
    _, engine = upgraded
    rep = _index_names(engine, "reputation_history")
    assert "ix_rep_history_created_cover" in rep
    assert not rep & {"ix_reputation_history_user_id", "ix_reputation_history_created_at", "idx_rep_history_date_user"}
    assert "idx_rep_history_user_date" in rep                     # kept: per-user history lookups
    assert "idx_lb_snap_user_id" not in _index_names(engine, "leaderboard_snapshots")
    prof = _index_names(engine, "student_profiles")
    assert "ix_student_profiles_department" not in prof and "idx_student_profiles_dept_user" in prof


def test_upgrade_is_repeatable_and_downgrade_restores_the_old_shape(upgraded):
    db_file, engine = upgraded
    _run(db_file, "upgrade", HEAD_REVISION)                       # already at head: no-op, no error
    _run(db_file, "downgrade", BASE_REVISION)
    engine = sa.create_engine(f"sqlite:///{db_file}")
    assert "idempotency_key" not in _columns(engine, "reputation_history")
    assert "snapshot_date" not in _columns(engine, "leaderboard_snapshots")
    assert {"ix_reputation_history_user_id", "ix_reputation_history_created_at",
            "idx_rep_history_date_user"} <= _index_names(engine, "reputation_history")
    assert "idx_lb_snap_user_id" in _index_names(engine, "leaderboard_snapshots")
