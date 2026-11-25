"""The post_views migration removes duplicates, fixes counters and adds the unique constraint."""
import importlib.util
import pathlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = pathlib.Path(__file__).parents[3] / "migrations/versions/d5e2b8a1c9f3_dedupe_post_views.py"


def _run(engine, fn_name="upgrade"):
    spec = importlib.util.spec_from_file_location("post_views_dedupe_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, fn_name)()


@pytest.fixture
def old_db():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE posts (id INTEGER PRIMARY KEY, views_count INTEGER)"))
        conn.execute(sa.text(
            "CREATE TABLE post_views (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "post_id INTEGER NOT NULL, viewed_at DATETIME, "
            "CONSTRAINT unique_daily_view UNIQUE (user_id, post_id, viewed_at))"
        ))
        conn.execute(sa.text("INSERT INTO posts VALUES (1, 1), (2, 5)"))
        conn.execute(sa.text(
            "INSERT INTO post_views (user_id, post_id, viewed_at) VALUES "
            "(10, 1, '2026-01-01 10:00:00.000001'), (10, 1, '2026-01-01 10:00:00.000002'), "
            "(10, 1, '2026-01-01 10:00:00.000003'), (11, 1, '2026-01-02 10:00:00'), "
            "(10, 2, '2026-01-03 10:00:00')"
        ))
    return engine


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT id, user_id, post_id FROM post_views ORDER BY id")).fetchall()


def test_duplicates_removed_earliest_kept_and_counters_fixed(old_db):
    _run(old_db)

    assert _rows(old_db) == [(1, 10, 1), (4, 11, 1), (5, 10, 2)]
    with old_db.connect() as conn:
        counts = dict(conn.execute(sa.text("SELECT id, views_count FROM posts")).fetchall())
    assert counts == {1: 2, 2: 5}  # post 1 recomputed, untouched post 2 left alone


def test_unique_constraint_replaced_and_enforced(old_db):
    _run(old_db)

    names = {u["name"] for u in sa.inspect(old_db).get_unique_constraints("post_views")}
    assert names == {"uq_post_views_user_post"}
    with pytest.raises(sa.exc.IntegrityError):
        with old_db.begin() as conn:
            conn.execute(sa.text("INSERT INTO post_views (user_id, post_id, viewed_at) VALUES (10, 1, '2027-01-01')"))


def test_upgrade_is_idempotent(old_db):
    _run(old_db)
    _run(old_db)
    assert len(_rows(old_db)) == 3


def test_downgrade_restores_old_constraint(old_db):
    _run(old_db)
    _run(old_db, "downgrade")
    names = {u["name"] for u in sa.inspect(old_db).get_unique_constraints("post_views")}
    assert names == {"unique_daily_view"}
