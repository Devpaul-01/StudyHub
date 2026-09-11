"""
tests/integration/conftest.py

Integration-test fixtures. Composes (does not redefine) the fixtures
already established in tests/unit/conftest.py per
INTEGRATION_TEST_IMPLEMENTATION_PLAN.md §2.1's explicit instruction not to
build a second, parallel test architecture: db_session's row-wipe
isolation pattern, fakeredis_client, raising_redis_client, and every
tests/unit/factories.py factory fixture are imported and reused verbatim.

The one thing that genuinely cannot be reused from tests/unit/conftest.py
is the `app` fixture itself — that fixture deliberately builds a minimal
hand-rolled Flask app (documented in its own docstring as excluding
CORS/SocketIO/Sentry/blueprint registration as "out of unit-test scope").
Integration tests need exactly the opposite: the real create_app() from
app.py, with real blueprints, the real CSRF before_request hook
(routes/student/__init__.py::enforce_csrf), and the real rate limiter —
that IS the thing under test here. So this file defines its OWN `app`
fixture (session-scoped, named `app` — pytest fixture resolution means
this file's `app` shadows tests/unit/conftest.py's `app` for every test
collected under tests/integration/, which is exactly the intended
override), and every fixture that depends on `app` transitively gets the
real one automatically.

DECISIONS LOCKED IN (see bundle header for full reasoning):
  - SQLite in-memory, not Postgres. Two tests are marked skip() for the
    Postgres-only behavior this can't exercise (uq_connections_pair,
    JSONB/GIN) — see test_connections_flow.py.
  - fakeredis, not a real Redis service container.
  - RQ jobs called directly; .enqueue mocked where the call site itself is
    under test.
  - AI providers never called for real.
"""

import os
import sys

# Must be set before `import app` (which imports config.py, which reads
# these at class-definition/import time) — same requirement the
# repository-root conftest.py already documents for the unit suite.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_NEW_URL", "sqlite:///:memory:")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("REDIS_URL", "redis://localhost:6399/0")
# Integration tests DO want rate limiting active — this is one of the
# things genuinely under test (INTEGRATION_TEST_IMPLEMENTATION_PLAN.md
# §3.10: "no existing test of any kind currently proves rate limiting or
# CSRF actually work end-to-end"). The repo-root conftest.py sets this to
# "false" via setdefault(), which only applies if nothing set it first —
# since THIS file's os.environ[...] runs at collection time for
# tests/integration/, and is a direct assignment (not setdefault), it wins
# regardless of import order.
os.environ["RATE_LIMIT_ENABLED"] = "true"
os.environ["FLASK_ENV"] = "testing"

import importlib
from unittest.mock import Mock

import pytest
from flask.testing import FlaskClient


# ============================================================================
# APP / DB FIXTURES — the real create_app(), against in-memory SQLite
# ============================================================================

@pytest.fixture(scope="session")
def app():
    """
    The REAL Flask app from app.py::create_app(), pointed at an in-memory
    SQLite DB instead of whatever DATABASE_NEW_URL/production Postgres
    config would otherwise resolve to, with the scheduler and Sentry
    disabled.

    Session-scoped for the same reason tests/unit/conftest.py's `app` is:
    app *configuration* doesn't need re-creating per test, only DB
    *contents* need isolation (db_session below, reused unchanged).

    IMPORTANT DIFFERENCE FROM UNIT TESTS: this actually calls
    app.py::create_app(), which:
      - registers every real blueprint (student_bp with its full
        route tree, admin_bp)
      - wires the real CSRF before_request hook
        (routes/student/__init__.py::enforce_csrf)
      - calls services/rate_limit_service.py::init_app(app), so
        @limiter.limit(...) decorators are genuinely active
      - constructs a real Flask-SocketIO instance via
        services/websocket_messages.py::init_message_websocket and
        services/websocket_threads.py::init_thread_websocket

    None of that is mocked or stubbed — it's the actual application
    object. What's overridden via config, AFTER create_app() returns, is
    only: the DB URI (swapped to SQLite in-memory), SECRET_KEY (already
    set via env before import), and confirming SCHEDULER_ENABLED is off
    (also set via env before import, so create_app() itself never calls
    scheduler.init_scheduler() — see app.py's own gate on this).

    Sentry: SENTRY_DSN is deliberately left unset in this test environment
    (never set above), so services/error_tracking.py::init_app() takes its
    own documented "not configured, skip" early-return path — no
    monkeypatching needed, this is already how the real code behaves with
    no DSN.
    """
    import models  # noqa: F401  — see tests/unit/conftest.py's identical
                                  # comment: must import before create_all()
                                  # so SQLAlchemy's metaclass registers
                                  # every table on db.metadata.

    from sqlalchemy.pool import StaticPool
    from app import create_app
    from config import TestingConfig
    from extensions import db

    flask_app, socketio = create_app(config_class=TestingConfig)

    flask_app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        # Identical Postgres-only connect_args strip as tests/unit/conftest.py
        # — Config.SQLALCHEMY_ENGINE_OPTIONS hardcodes sslmode=require /
        # PgBouncer statement_timeout, which raises TypeError against
        # SQLite's DBAPI.
        SQLALCHEMY_ENGINE_OPTIONS={
            "poolclass": StaticPool,
            "connect_args": {"check_same_thread": False},
        },
        TESTING=True,
        SERVER_NAME="test.local",  # needed for url_for(..., _external=True)
                                    # call sites (auth.py's verification/reset
                                    # links) to resolve outside a real request
        SECRET_KEY="test-secret-key-not-for-production",
        MAIL_DEFAULT_SENDER="test@example.com",
        MAIL_SUPPRESS_SEND=True,
        SCHEDULER_ENABLED=False,
        # Rate limiting genuinely on for this suite — see module docstring.
        RATE_LIMIT_ENABLED=True,
        RATELIMIT_STORAGE_URI="memory://",  # Flask-Limiter's OWN storage,
                                             # independent of fakeredis —
                                             # see note in
                                             # test_rate_limiting.py on why
                                             # this is intentionally NOT
                                             # patched to fakeredis.
        WTF_CSRF_ENABLED=False,  # this app has its own hand-rolled
                                  # double-submit CSRF (enforce_csrf), not
                                  # Flask-WTF — this key is a no-op here,
                                  # kept only defensively in case any
                                  # future dependency reads it.
    )

    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.drop_all()


@pytest.fixture(autouse=True)
def app_context(app):
    """Pushes an app context for every integration test automatically —
    identical role to tests/unit/conftest.py's fixture of the same name."""
    with app.app_context():
        yield


@pytest.fixture(autouse=True)
def _reset_rate_limiter_storage(app):
    """
    FIX: the `app` fixture above is session-scoped (deliberately — see
    its own docstring on why app *configuration* doesn't need recreating
    per test), which means Flask-Limiter's in-memory storage
    (RATELIMIT_STORAGE_URI="memory://") is also a single, long-lived
    instance shared across every integration test in the whole run, not
    reset between tests the way db_session resets table contents.

    Any two tests that hit the same rate-limited route from the same key
    (IP, for the pre-auth SENSITIVE_AUTH-tier routes — see ip_key() in
    services/rate_limit_service.py) within the same fixed window
    therefore share one running counter. Concretely:
    test_auth_lifecycle.py alone POSTs to /student/login six times
    (across TestLoginAndStreak, TestRefreshTokenRotation, TestLogout,
    TestCSRFEnforcement) before test_rate_limiting.py ever runs — all
    from the same test-client IP, all counted against the same 5-per-
    minute budget — so by the time
    TestSensitiveAuthRateLimit::test_login_rate_limit_returns_429_after_tier_limit
    runs, the very first request in that test already gets 429 instead
    of the expected first-five-succeed-then-429 pattern the test is
    actually trying to verify.

    Resetting the limiter's storage before every test isolates each
    test's rate-limit budget from every other test's, which is the
    correct behavior for a suite where any individual test may
    legitimately want to hit a real per-minute ceiling — matching how
    db_session already isolates database state per test despite `app`
    itself being session-scoped.

    NOTE: app.extensions["limiter"] is a *set* on this Flask-Limiter
    version, not the Limiter instance itself (confirmed empirically) —
    so this resets via the module-level `limiter` singleton
    (services/rate_limit_service.py) directly, the same object
    init_rate_limiter(app) wires up inside create_app().
    """
    from services.rate_limit_service import limiter as _limiter

    try:
        _limiter.storage.reset()
    except Exception:
        # Fail open — a storage backend that doesn't support reset()
        # should never block test collection/execution.
        pass
    yield


@pytest.fixture
def db_session(app, app_context):
    """
    Reuses tests/unit/conftest.py's exact delete-all-rows-at-teardown
    isolation strategy verbatim (not re-implemented) — see that file's own
    extensive docstring for why the SAVEPOINT-splicing pattern from the
    original unit-test plan was tried and empirically abandoned in favor
    of this simpler approach. That reasoning is dialect-independent (it
    doesn't rely on SQLite-only behavior), so it applies unchanged here.
    """
    from extensions import db

    yield db.session

    db.session.rollback()
    db.session.remove()
    with db.engine.begin() as conn:
        for table in reversed(db.metadata.sorted_tables):
            conn.execute(table.delete())


# ============================================================================
# FIX: Werkzeug's FlaskClient.get_cookie()/set_cookie() default `domain` to
# "localhost" (see werkzeug.test.Client's signature) — but this app's
# `app` fixture sets SERVER_NAME="test.local" (required for
# url_for(..., _external=True) call sites like the verification/reset
# links in auth.py), so every cookie this app's responses set is actually
# stored in the test client's cookie jar under domain "test.local", not
# "localhost".
#
# Every un-patched `client.get_cookie("access_token")` call across this
# suite was therefore ALWAYS returning None regardless of whether the
# server-side Set-Cookie header was correctly issued (confirmed via a
# minimal repro: the Set-Cookie header is present and correct on the
# response; only the test-side jar lookup was looking under the wrong
# domain key). This silently broke:
#   - every assertion of the shape `client.get_cookie(...) is not None`
#     (test_auth_lifecycle.py's login/verify-email/refresh/logout tests)
#   - the csrf_headers fixture below, which reads
#     `client.get_cookie("csrf_token")` to build the X-CSRF-Token header
#     for every mutating-route test in the suite — with that read always
#     returning None, csrf_headers() always returned {}, so every
#     mutating request sent no X-CSRF-Token header and was rejected by
#     the real enforce_csrf hook with 403 "CSRF token missing or
#     invalid" — this is what was actually behind the large block of
#     403s across test_connections_flow.py, test_posts_comments_*.py,
#     test_notifications_funnel.py, test_file_upload_validation.py, and
#     test_ai_no_real_calls.py's canned-response test.
#
# Fix: a thin FlaskClient subclass that defaults `domain` to the app's
# own SERVER_NAME instead of Werkzeug's hardcoded "localhost" default,
# for both get_cookie and set_cookie. This is scoped to test
# infrastructure only — no application code changes needed, since the
# server was never wrong here (the Set-Cookie headers it issues have no
# explicit Domain attribute at all, which is correct: an unqualified
# cookie already matches the request's own host).
# ============================================================================

class _DomainAwareTestClient(FlaskClient):
    def get_cookie(self, key, domain=None, *args, **kwargs):
        if domain is None:
            domain = self.application.config.get("SERVER_NAME") or "localhost"
        return super().get_cookie(key, domain=domain, *args, **kwargs)

    def set_cookie(self, key, value="", *, domain=None, **kwargs):
        if domain is None:
            domain = self.application.config.get("SERVER_NAME") or "localhost"
        return super().set_cookie(key, value, domain=domain, **kwargs)


@pytest.fixture
def client(app):
    """Flask test client — the actual HTTP boundary integration tests
    exercise (routes, not services directly).

    Uses _DomainAwareTestClient (see above) so get_cookie()/set_cookie()
    calls anywhere in this suite transparently match the domain the
    app's own SERVER_NAME config actually issues cookies under, without
    every call site needing to remember to pass domain="test.local"
    explicitly.
    """
    app.test_client_class = _DomainAwareTestClient
    return app.test_client()


# ============================================================================
# REDIS FIXTURES — reused unchanged from tests/unit/conftest.py
# ============================================================================

_MODULES_WITH_BOUND_REDIS_CLIENT = [
    "services.cache_service",
    "services.counter_cache_service",
    "services.distributed_lock",
    "services.presence_service",
    "services.websocket_rate_limiter",
]


@pytest.fixture
def fakeredis_client(monkeypatch):
    """Identical to tests/unit/conftest.py's fixture of the same name —
    duplicated here (not imported) only because pytest fixtures are
    resolved per test-directory conftest chain and this repo's existing
    tests/unit/conftest.py is not itself an importable package fixture
    module from tests/integration/'s perspective without adding it to
    pytest's plugin path. If your pytest version/config already makes
    tests/unit/conftest.py fixtures visible to tests/integration/ (some
    layouts do via rootdir-relative conftest discovery), delete this
    duplicate and rely on that instead — kept here for safety since I
    cannot execute pytest in this environment to confirm collection
    behavior against your exact directory layout."""
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
    """Identical to tests/unit/conftest.py's fixture of the same name —
    see fakeredis_client's docstring above for why it's duplicated rather
    than imported."""
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
# FACTORY FIXTURES — reused unchanged from tests/unit/factories.py
# ============================================================================
# Every factory below is a thin wrapper over the existing
# tests/unit/factories.py functions, bound to THIS file's db_session.
# These must be redeclared (not imported as fixtures) because pytest
# fixtures are resolved by name within the active conftest chain — but
# the underlying factory FUNCTIONS are imported, never re-implemented.

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
    from services import badge_service
    badge_service.seed_badges()
    return db_session


# ============================================================================
# NEW FACTORY FIXTURES — gaps named in INTEGRATION_TEST_IMPLEMENTATION_PLAN.md
# §2.2 ("Gaps requiring new factories"). Additive to
# tests/integration/factories_ext.py, not to tests/unit/factories.py itself
# — kept out of the unit-test file so the unit suite's own file stays
# untouched by this integration-test pass, per the instruction to treat
# tests/unit/* as reused, not modified.
# ============================================================================

@pytest.fixture
def make_onboarding_details(db_session):
    from tests.integration.factories_ext import make_onboarding_details as _f
    return lambda user, **kw: _f(db_session, user, **kw)


@pytest.fixture
def make_study_buddy_request(db_session):
    from tests.integration.factories_ext import make_study_buddy_request as _f
    return lambda requester, receiver, **kw: _f(db_session, requester, receiver, **kw)


@pytest.fixture
def make_study_buddy_match(db_session):
    from tests.integration.factories_ext import make_study_buddy_match as _f
    return lambda user1, user2, **kw: _f(db_session, user1, user2, **kw)


@pytest.fixture
def make_badge(db_session):
    from tests.integration.factories_ext import make_badge as _f
    return lambda **kw: _f(db_session, **kw)


@pytest.fixture
def make_notification(db_session):
    from tests.integration.factories_ext import make_notification as _f
    return lambda user, **kw: _f(db_session, user, **kw)


# ============================================================================
# AI-PROVIDER STUBS — never call a real provider (per your explicit answer)
# ============================================================================

@pytest.fixture
def no_ai_providers(monkeypatch):
    """
    Forces provider_manager.get_working_provider(...) to return None for
    the duration of the test — this is the GENUINE no-key-configured
    behavior (ai_provider_service.py's _load_providers() already returns
    an empty provider list when no *_API_KEY env vars are set, which is
    exactly this test environment's real state), made explicit and
    deterministic rather than relying on env vars happening to be unset.
    Use for routes that must be tested on their graceful-degradation path
    (e.g. Learnora chat with "no provider available").
    """
    from services import ai_provider_service
    monkeypatch.setattr(
        ai_provider_service.provider_manager, "get_working_provider", lambda **kw: None
    )
    return None


@pytest.fixture
def canned_ai_response(monkeypatch):
    """
    Patches call_ai_response (the non-streaming consolidated entry point —
    see services/ai_provider_service.py's own module docstring for why
    this is the one callers should use for a single complete string) to
    return a fixed, canned string with no HTTP call at all. Use for routes
    that need a *successful* AI call to proceed (e.g. ask_learnora_about_post)
    without ever touching requests.post.
    """
    from services import ai_provider_service

    def _fake_call_ai_response(messages, needs_vision=False, max_retries=2, **kw):
        return "This is a canned AI response for integration testing.", {
            "attempts": 1, "provider": "fake_provider", "errors": []
        }

    monkeypatch.setattr(ai_provider_service, "call_ai_response", _fake_call_ai_response)
    return _fake_call_ai_response


# ============================================================================
# AUTH HELPERS — building a real, valid JWT cookie/header pair against the
# real _build_access_token(), so route tests hit the REAL token_required
# decorator, not a bypassed/mocked one.
# ============================================================================

@pytest.fixture
def auth_headers():
    """
    Returns a function(user) -> dict of headers carrying a real, validly
    signed Bearer token for `user`, built via the actual
    routes/student/helpers.py::_build_access_token — i.e. this is not a
    mock of authentication, it's a real token that the real
    role_required()/token_required() decorator will decode and accept.
    """
    from routes.student.helpers import _build_access_token

    def _make(user):
        token = _build_access_token(user)
        return {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture
def csrf_headers():
    """
    Returns a function(client) -> dict of headers needed to pass the real
    double-submit CSRF check (routes/student/__init__.py::enforce_csrf)
    on a mutating request.

    FIX: enforce_csrf's check is a pure double-submit comparison — it
    only verifies that the `csrf_token` cookie and the `X-CSRF-Token`
    header are both present and equal to each other (see that
    function's own `cookie_value != header_value` check); it never
    validates the value against anything stored server-side. The
    previous version of this fixture only ever *read* whatever
    `csrf_token` cookie happened to already be on the client — which
    works for tests that log in for real via POST /student/login (the
    only route that calls set_auth_cookies, the sole place csrf_token
    gets set), but every test in this suite actually authenticates via
    the auth_headers fixture (a directly-built JWT Bearer token, with no
    HTTP request and therefore no cookie side effect at all). For those
    tests — the overwhelming majority of mutating-route tests — the
    `csrf_token` cookie was simply never present, so this fixture always
    returned {} and every one of those requests was correctly rejected
    by the real enforce_csrf hook with a 403.

    Since the value itself is never checked against anything besides the
    header, this fixture can mint its own token, set it as the
    `csrf_token` cookie directly on the client (matching what
    set_auth_cookies would have set, without requiring a real login
    round-trip), and return the same value as the X-CSRF-Token header —
    genuinely exercising the double-submit comparison enforce_csrf
    performs, just without depending on a prior /student/login call.
    """
    import secrets

    def _make(client):
        token = secrets.token_urlsafe(32)
        client.set_cookie("csrf_token", token)
        return {"X-CSRF-Token": token}

    return _make

