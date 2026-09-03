"""
Repository-root conftest.py.

config.py's Config class reads SECRET_KEY and DATABASE_NEW_URL (as
DATABASE_URL) at CLASS-DEFINITION time (i.e. import time) and raises
ValueError immediately if either is unset. These must exist in the
environment before config.py is imported by *anything*, including
pytest's own test collection phase, which is why this file lives at
the repository root rather than under tests/unit/ — a root conftest.py
is guaranteed to run before pytest imports any test module.

Values here are placeholders only:
  - SECRET_KEY: any string satisfies config.py's "is it set" check; no
    real JWT is ever verified against a production secret in this suite.
  - DATABASE_NEW_URL: immediately overridden by the `app` fixture in
    tests/unit/conftest.py to "sqlite:///:memory:" before db.create_all()
    runs, so this value is never actually connected to.
  - SCHEDULER_ENABLED=false: this suite never calls the real
    app.py::create_app(), so nothing reads this at runtime — set
    defensively anyway, since config.py's Config class evaluates it at
    import time regardless of whether anything downstream uses it.
  - REDIS_URL points at a port nothing listens on. extensions.py's
    _create_redis_client() attempts a real connection at import time and
    catches every failure, setting redis_client = None on failure — this
    exercises that exact fail-open path deterministically rather than by
    accident, and every Redis-touching test in this suite monkeypatches
    a fakeredis instance in over the None anyway.
"""
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_NEW_URL", "sqlite:///:memory:")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("REDIS_URL", "redis://localhost:6399/0")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("FLASK_ENV", "testing")
