"""
tests/unit/conftest.py — shared fixtures for the whole unit suite.

See UNIT_TEST_IMPLEMENTATION_PLAN.md §9 for the design this follows.
Several deliberate, documented deviations from that section's literal
code are called out in the fixtures below — each was arrived at only
after the plan's own pasted code was tried verbatim and empirically
failed against the actual installed stack (Flask 3.0.3 /
Flask-SQLAlchemy 3.1.1 / SQLAlchemy 2.0.35).
"""

import importlib
from unittest.mock import Mock

import pytest


# ============================================================================
# APP / DB FIXTURES
# ============================================================================

@pytest.fixture(scope="session")
def app():
    """
    Minimal, test-configured Flask app with real db/mail extensions
    initialized against in-memory SQLite.

    DEVIATION 1 FROM THE PLAN'S LITERAL `from app import create_app` CODE
    (documented, not silent): the real app.py::create_app()
    unconditionally wires in flask_cors, a real Flask-SocketIO instance
    (services.websocket_messages, services.websocket_threads), Sentry
    (services.error_tracking), and blueprints from routes.student /
    routes.admin. None of those modules were supplied for this
    test-writing pass, and UNIT_TEST_IMPLEMENTATION_PLAN.md §4 already
    places all of that wiring out of unit-test scope ("Application
    factory / process bootstrap... failure here is 'the app doesn't
    start,' which integration/smoke tests catch, not unit tests").
    Building the app directly here — Flask() + config.from_object +
    db.init_app + mail.init_app — is a faithful implementation of the
    plan's own stated fixture goal (§5: "a real, minimal Flask app in
    test config is simpler and more faithful than mocking Flask's
    context locals") without depending on machinery the plan itself
    excludes and that isn't available in this environment.

    Session-scoped: app *configuration* doesn't need re-creating per
    test — only DB *contents* need isolation, handled by db_session.
    """
    from flask import Flask
    from sqlalchemy.pool import StaticPool
    from config import TestingConfig
    from extensions import db, mail

    # CRITICAL: import models BEFORE db.create_all(). SQLAlchemy model
    # classes only register their tables on db.metadata when the module
    # defining them actually executes (the declarative metaclass runs at
    # class-body time). Nothing else in this fixture chain imports
    # models.py at module-import time — factories.py imports it lazily,
    # inside each factory function body, which runs long after this
    # fixture's create_all() call. Without this explicit import,
    # db.metadata is genuinely empty when create_all() runs: it succeeds
    # without error and silently creates zero tables.
    import models  # noqa: F401

    flask_app = Flask(__name__)
    flask_app.config.from_object(TestingConfig)
    flask_app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        # DISCOVERED BUG (flagged, not silently worked around in
        # config.py itself — see the implementation report): TestingConfig
        # inherits Config.SQLALCHEMY_ENGINE_OPTIONS verbatim, which
        # hardcodes Postgres-only connect_args (sslmode=require, a
        # PgBouncer statement_timeout option). TestingConfig never
        # overrides this, so pointing it at SQLite raises
        # `TypeError: 'sslmode' is an invalid keyword argument` the
        # instant db.create_all() runs.
        SQLALCHEMY_ENGINE_OPTIONS={
            "poolclass": StaticPool,
            "connect_args": {"check_same_thread": False},
        },
        TESTING=True,
        SECRET_KEY="test-secret-key-not-for-production",
        MAIL_DEFAULT_SENDER="test@example.com",
        SCHEDULER_ENABLED=False,
        RATE_LIMIT_ENABLED=False,
    )

    db.init_app(flask_app)
    mail.init_app(flask_app)

    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.drop_all()


@pytest.fixture(autouse=True)
def app_context(app):
    """
    Pushes an app context for every test automatically — nearly
    everything under test touches current_app.config, db.session, or a
    logger.
    """
    with app.app_context():
        yield


@pytest.fixture
def db_session(app, app_context):
    """
    Per-test DB isolation via delete-all-rows-at-teardown.

    Explicitly depends on `app_context` (not just `app`): pytest tears
    fixtures down in reverse dependency order, and this fixture's
    teardown needs an active Flask app context for its own DB access
    (db.engine resolves against current_app). Declaring app_context as
    an explicit parameter guarantees this fixture's teardown runs before
    app_context's own teardown pops the context (LIFO) — relying on both
    fixtures merely sharing `app` as an implicit common ancestor left the
    relative order between two independent function-scoped fixtures
    unspecified, which surfaced as a real "no such table" teardown
    failure during this suite's own development.

    DEVIATION FROM THE PLAN'S LITERAL §9 CODE (documented, not silent):
    the plan's pasted SAVEPOINT-splicing pattern (`connection.begin_nested()`
    + `db.session.bind = connection` + an `after_transaction_end`
    listener) was tried first, verbatim, and empirically failed against
    the actual installed stack — first with `AttributeError:
    'NestedTransaction' object has no attribute 'nested'` (the plan's own
    attribute name doesn't exist on this SQLAlchemy version), and after
    correcting that to the real `is_active` API, with `ResourceClosedError:
    This Connection is closed`. Root cause: Flask-SQLAlchemy 3.x's scoped
    session performs its own bind/engine lookup internally, and manually
    splicing a raw Connection into db.session.bind doesn't interact
    predictably with that machinery across a flush.

    Isolation is done here the simpler, robust way instead: let each
    test's db.session behave completely normally (real commits included
    — several functions under test, e.g. auth_service.record_login_and_commit
    and auth_service.consume_password_reset_token, commit internally, and
    tests need to observe real post-commit state), then wipe every
    table's rows via a raw engine connection at teardown, in FK-safe
    (reverse-declaration) order.
    """
    from extensions import db

    yield db.session

    db.session.rollback()
    db.session.remove()
    with db.engine.begin() as conn:
        for table in reversed(db.metadata.sorted_tables):
            conn.execute(table.delete())


# ============================================================================
# REDIS FIXTURES
# ============================================================================

# Modules confirmed (by direct source inspection) to do
# `from extensions import redis_client` at their own import time, which
# binds a module-local reference that patching extensions.redis_client
# alone does not retroactively change. presence_service.py and
# websocket_rate_limiter.py are named in the plan's own conftest code but
# were not supplied for this pass — importlib.import_module below is
# wrapped in try/except ImportError specifically so their absence is a
# silent no-op, not a fixture failure.
_MODULES_WITH_BOUND_REDIS_CLIENT = [
    "services.cache_service",
    "services.counter_cache_service",
    "services.distributed_lock",
    "services.presence_service",
    "services.websocket_rate_limiter",
]


@pytest.fixture
def fakeredis_client(monkeypatch):
    """
    Replaces extensions.redis_client, and every already-bound
    module-local reference to it, with a real fakeredis instance for the
    duration of the test.
    """
    import fakeredis
    import extensions

    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(extensions, "redis_client", fake)

    for mod_name in _MODULES_WITH_BOUND_REDIS_CLIENT:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        if hasattr(mod, "redis_client"):
            monkeypatch.setattr(mod, "redis_client", fake)

    yield fake
    fake.flushall()


@pytest.fixture
def raising_redis_client(monkeypatch):
    """
    A Mock that raises on every method call, for testing fail-open (and,
    for distributed_lock specifically, fail-CLOSED) behavior.
    """
    import redis as redis_module
    import extensions

    mock = Mock()
    err = redis_module.ConnectionError("simulated failure")
    for method in ("get", "set", "delete", "scan", "pipeline", "eval", "ping"):
        getattr(mock, method).side_effect = err

    monkeypatch.setattr(extensions, "redis_client", mock)
    for mod_name in _MODULES_WITH_BOUND_REDIS_CLIENT:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        if hasattr(mod, "redis_client"):
            monkeypatch.setattr(mod, "redis_client", mock)

    yield mock


# ============================================================================
# FACTORY FIXTURES (thin wrappers over tests/unit/factories.py)
# ============================================================================

@pytest.fixture
def make_user(db_session):
    from tests.unit.factories import make_user as _f
    return lambda **kw: _f(db_session, **kw)


@pytest.fixture
def make_student_profile(db_session):
    from tests.unit.factories import make_student_profile as _f
    return lambda user, **kw: _f(db_session, user, **kw)


@pytest.fixture
def make_post(db_session):
    from tests.unit.factories import make_post as _f
    return lambda author, **kw: _f(db_session, author, **kw)


@pytest.fixture
def make_comment(db_session):
    from tests.unit.factories import make_comment as _f
    return lambda post, author, **kw: _f(db_session, post, author, **kw)


@pytest.fixture
def make_connection(db_session):
    from tests.unit.factories import make_connection as _f
    return lambda requester, receiver, **kw: _f(db_session, requester, receiver, **kw)


@pytest.fixture
def make_thread(db_session):
    from tests.unit.factories import make_thread as _f
    return lambda creator, **kw: _f(db_session, creator, **kw)


@pytest.fixture
def make_thread_member(db_session):
    from tests.unit.factories import make_thread_member as _f
    return lambda thread, user, **kw: _f(db_session, thread, user, **kw)


@pytest.fixture
def make_activity_feed_row(db_session):
    from tests.unit.factories import make_activity_feed_row as _f
    return lambda user, **kw: _f(db_session, user, **kw)


@pytest.fixture
def make_ai_conversation(db_session):
    from tests.unit.factories import make_ai_conversation as _f
    return lambda user, **kw: _f(db_session, user, **kw)


@pytest.fixture
def make_password_reset_token(db_session):
    from tests.unit.factories import make_password_reset_token as _f
    return lambda user, **kw: _f(db_session, user, **kw)


@pytest.fixture
def make_email_verification_token(db_session):
    from tests.unit.factories import make_email_verification_token as _f
    return lambda user, **kw: _f(db_session, user, **kw)


@pytest.fixture
def make_refresh_token(db_session):
    from tests.unit.factories import make_refresh_token as _f
    return lambda user, **kw: _f(db_session, user, **kw)


@pytest.fixture
def make_assignment(db_session):
    from tests.unit.factories import make_assignment as _f
    return lambda user, **kw: _f(db_session, user, **kw)


@pytest.fixture
def make_message(db_session):
    from tests.unit.factories import make_message as _f
    return lambda sender, receiver, **kw: _f(db_session, sender, receiver, **kw)


@pytest.fixture
def make_thread_message(db_session):
    from tests.unit.factories import make_thread_message as _f
    return lambda thread, sender, **kw: _f(db_session, thread, sender, **kw)


@pytest.fixture
def seeded_badges(db_session):
    """Seeds the real BADGE_DEFINITIONS via badge_service.seed_badges()
    so check_and_award_badge/check_all_badges_for_user can find badges by
    name, per the plan's own §7.3 fixture note."""
    from services import badge_service
    badge_service.seed_badges()
    return db_session
