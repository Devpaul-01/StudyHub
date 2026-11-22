"""PostgreSQL-only tests.

These exercise behaviour SQLite cannot reproduce (row locks, true concurrent
transactions). They are skipped unless TEST_POSTGRES_URL points at an EMPTY,
throwaway database whose name contains "test", e.g.

    createdb studyhub_pg_test
    TEST_POSTGRES_URL=postgresql+psycopg2://user:pw@localhost/studyhub_pg_test \\
        pytest tests/postgres
"""
import os
import sys

import pytest

URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.fixture(scope="session")
def pg_app():
    if not URL:
        pytest.skip("TEST_POSTGRES_URL not set")
    from sqlalchemy.engine import make_url
    if "test" not in (make_url(URL).database or ""):
        pytest.fail("Refusing to run: TEST_POSTGRES_URL database name must contain 'test'")

    # The repo-level conftest configures a SQLite app; build a minimal app
    # on the real models instead.
    from flask import Flask
    from extensions import db
    import models  # noqa: F401  (register tables)

    app = Flask("pg-tests")
    app.config.update(SQLALCHEMY_DATABASE_URI=URL, TESTING=True)
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def pg_db(pg_app):
    from extensions import db
    with pg_app.app_context():
        yield db


@pytest.fixture()
def make_pg_user(pg_db):
    """Create and commit an approved student; returns the new user id."""
    import datetime
    import itertools
    import uuid
    from models import User

    counter = itertools.count(1)
    tag = uuid.uuid4().hex[:8]

    def _make(reputation=0):
        n = next(counter)
        u = User(username=f"pg{tag}{n}", email=f"pg{tag}{n}@x.com", pin="x", name="User",
                 role="student", status="approved", email_verified=True,
                 reputation=reputation, joined_at=datetime.datetime.utcnow())
        pg_db.session.add(u)
        pg_db.session.commit()
        return u.id

    return _make
