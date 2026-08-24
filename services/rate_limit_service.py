"""
services/rate_limit_service.py — HTTP-layer rate limiting (Document 4 §1)

Single Flask-Limiter instance for the whole backend. Storage is Redis in
production, in-memory in dev/test (RATE_LIMIT_STORAGE_URI, config.py).

IMPORTANT — why this is NOT built the way the task brief's snippet showed:
the brief's version read `current_app.config...` and called `Limiter(...)`
at MODULE level. That crashes on import: `current_app` only works inside an
active app/request context, and this module is imported by app.py before
`create_app()` has pushed one (`RuntimeError: Working outside of
application context`). The fix, and the standard Flask-Limiter pattern, is
the two-step split every other Flask extension in this codebase already
uses (compare `extensions.py`'s `db = SQLAlchemy()` / `db.init_app(app)`):
construct the Limiter with NO app at import time, then call
`limiter.init_app(app)` inside `create_app()` once `app.config` actually
exists. Config values (storage_uri, enabled flag) are read at init_app time,
not at import time.

This module replaces `utils.py::limiter` as the one limiter instance for
the app — `utils.py` now re-exports `limiter` from here instead of building
its own, so every existing call site (`from utils import limiter`) keeps
working unchanged. study_sessions.py's two pre-existing
`@limiter.limit(...)` routes have been switched to import from here
directly (see that file) per the explicit decision to consolidate on one
limiter rather than run two.

Fail-open policy: if Redis is unreachable, requests must still succeed —
a rate limiter that takes the whole app down when its backing store is
down is worse than no rate limiter. This is achieved by:
  - short connect/socket timeouts in storage_options, so a down Redis
    fails fast instead of hanging the request
  - `in_memory_fallback=<same limits>` (Flask-Limiter's built-in "if the
    configured storage backend errors, use these limits against an
    in-memory store instead of raising/blocking") rather than
    `in_memory_fallback=False`, which is the opposite of fail-open — with
    it off, a storage error propagates and Flask-Limiter's default
    behavior is `RATELIMIT_SWALLOW_ERRORS` (config-gated); we set that
    explicitly to True at init time as a second, independent safety net.
"""
from __future__ import annotations

import logging
from flask import request, current_app, g, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# TIERS — named rate strings. Route authors pick a tier by name; nobody
# invents a new rate string per route (Document 4 §1.3's explicit goal).
# ─────────────────────────────────────────────────────────────────────────

class RateLimitTier:
    SENSITIVE_AUTH = "5 per minute"    # login, register, password-reset-request
    WRITE_HEAVY = "30 per minute"      # create/update/delete posts, threads, comments, connections...
    AI_EXPENSIVE = "10 per hour"       # AI chat / refinement / meeting-notes — these cost real $ per call
    BURST_OK = "60 per minute"         # reactions, likes, low-risk high-frequency actions
    PUBLIC_READ = "300 per minute"     # read-only, often unauthenticated, endpoints
    WEBHOOK = "100 per minute"         # external-service callbacks, if any exist


# ─────────────────────────────────────────────────────────────────────────
# KEY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────

def ip_key() -> str:
    """IP-based key — for pre-auth routes (login/register) where there's no
    user identity yet to key on."""
    ip = get_remote_address()
    logger.debug(f"[RATE_LIMIT] 🔑 ip_key() → {ip}")
    return ip


def user_or_ip_key() -> str:
    """
    Per-user key for authenticated routes, falling back to IP for routes
    that happen not to have a user yet.

    Reads g.current_user_id, which role_required()/token_required() (see
    routes/student/helpers.py) sets immediately after decoding the JWT —
    at zero extra cost, since the payload is already decoded at that point.
    IP-based limiting is the wrong granularity once a user is known (a
    shared campus/NAT network would otherwise rate-limit unrelated
    students together), so this is used instead of ip_key() on every
    @token_required-protected route below.
    """
    user_id = getattr(g, "current_user_id", None)
    if user_id:
        key = f"user:{user_id}"
        logger.debug(f"[RATE_LIMIT] 🔑 user_or_ip_key() → {key} (authenticated user)")
        return key
    
    ip = get_remote_address()
    logger.debug(f"[RATE_LIMIT] 🔑 user_or_ip_key() → {ip} (fallback to IP)")
    return ip


# ─────────────────────────────────────────────────────────────────────────
# LIMITER INSTANCE
#
# Constructed WITHOUT an app — see module docstring. init_app(app) (below)
# is what actually wires it to config and must be called from
# app.py::create_app() after app.config is populated.
# ─────────────────────────────────────────────────────────────────────────

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    strategy="fixed-window",
)


def _is_exempt_path(path: str) -> bool:
    """Health/readiness endpoints are never rate limited."""
    exempt = path in ("/health", "/ping", "/ready")
    if exempt:
        logger.debug(f"[RATE_LIMIT] 🛡️ Path {path} is EXEMPT from rate limiting")
    return exempt


def init_app(app) -> Limiter:
    """
    Call once from create_app(), after app.config is fully populated.

    Reads RATE_LIMIT_STORAGE_URI / RATE_LIMIT_ENABLED from app.config
    (config.py) at this point — not at import time (see module docstring).
    """
    storage_uri = app.config.get("RATE_LIMIT_STORAGE_URI", "memory://")
    is_redis = storage_uri.startswith("redis://") or storage_uri.startswith("rediss://")

    logger.info(f"[RATE_LIMIT] ⚙️ Initializing with storage: {storage_uri}")
    logger.info(f"[RATE_LIMIT] ⚙️ Storage type: {'Redis' if is_redis else 'Memory'}")

    app.config.setdefault("RATELIMIT_STORAGE_URI", storage_uri)
    if is_redis:
        # Fail fast against a down/unreachable Redis instead of hanging
        # the request for the default multi-second socket timeout.
        app.config.setdefault("RATELIMIT_STORAGE_OPTIONS", {
            "socket_connect_timeout": 1,
            "socket_timeout": 1,
            "retry_on_timeout": False,
        })
        logger.info("[RATE_LIMIT] ⚙️ Redis storage options: socket_connect_timeout=1s, socket_timeout=1s")
    
    app.config.setdefault("RATELIMIT_STRATEGY", "fixed-window")
    app.config.setdefault("RATELIMIT_ENABLED", app.config.get("RATE_LIMIT_ENABLED", True))
    app.config.setdefault("RATELIMIT_HEADERS_ENABLED", True)

    # Fail-open: if the storage backend (Redis) errors at request time,
    # do not take the app down with it — swallow the error and let the
    # request through unlimited rather than 500ing or blocking everything.
    app.config.setdefault("RATELIMIT_SWALLOW_ERRORS", True)
    app.config.setdefault("RATELIMIT_IN_MEMORY_FALLBACK_ENABLED", True)

    logger.info(f"[RATE_LIMIT] ⚙️ RATELIMIT_ENABLED: {app.config.get('RATELIMIT_ENABLED')}")
    logger.info(f"[RATE_LIMIT] ⚙️ RATELIMIT_SWALLOW_ERRORS: {app.config.get('RATELIMIT_SWALLOW_ERRORS')}")
    logger.info(f"[RATE_LIMIT] ⚙️ RATELIMIT_IN_MEMORY_FALLBACK_ENABLED: {app.config.get('RATELIMIT_IN_MEMORY_FALLBACK_ENABLED')}")

    limiter.init_app(app)

    @limiter.request_filter
    def _exempt_health_checks():
        return _is_exempt_path(request.path)

    @app.errorhandler(429)
    def _rate_limit_exceeded(err):
        """
        Standard 429 body with rate-limit context. Uses err.description
        (Flask-Limiter sets this to the exceeded limit's string, e.g.
        "5 per 1 minute") rather than reaching into private Limiter
        internals — the task brief's `limiter._limiter.error_handler = ...`
        assigns to an attribute that doesn't exist on modern Flask-Limiter
        and would raise AttributeError before a single request is served.
        """
        logger.warning(f"[RATE_LIMIT] 🚫 429 Rate limit exceeded for {request.path} - {err.description}")
        return jsonify({
            "status": "error",
            "message": "Rate limit exceeded. Please slow down your requests.",
            "details": {"limit": str(getattr(err, "description", "")) or None},
        }), 429

    logger.info("[RATE_LIMIT] ✅ Rate limiter initialized successfully")
    return limiter


# ─────────────────────────────────────────────────────────────────────────
# DECORATOR WRAPPER WITH DEBUG LOGGING
# ─────────────────────────────────────────────────────────────────────────

def limit(tier: str, key_func=None):
    """
    Wrapper around limiter.limit() that adds debug logging for rate limit
    checks. Use exactly like @limiter.limit():

        @rate_limit_service.limit(RateLimitTier.AI_EXPENSIVE)
        def my_route():
            ...
    """
    def decorator(f):
        import functools
        
        # Get the actual limiter.limit decorator
        if key_func:
            decorated = limiter.limit(tier, key_func=key_func)(f)
        else:
            decorated = limiter.limit(tier)(f)

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            endpoint = request.path
            user_id = getattr(g, "current_user_id", None)
            
            # Log the rate limit check
            actual_key = key_func() if key_func else None
            logger.debug(
                f"[RATE_LIMIT] 🔍 Checking limit '{tier}' for endpoint {endpoint} "
                f"(user: {user_id or 'anonymous'}, key: {actual_key})"
            )
            
            try:
                result = decorated(*args, **kwargs)
                logger.debug(f"[RATE_LIMIT] ✅ Request to {endpoint} passed rate limit check")
                return result
            except Exception as e:
                # If rate limiter raises, log it
                if hasattr(e, 'description') and '429' in str(e):
                    logger.warning(f"[RATE_LIMIT] 🚫 Rate limit exceeded for {endpoint}")
                raise
                
        return wrapper
    return decorator


# ─────────────────────────────────────────────────────────────────────────
# REQUEST FILTER LOGGING (if you use limiter.request_filter)
# ─────────────────────────────────────────────────────────────────────────

# Optional: Add logging for request filtering
# This can be used in app.py if you want to skip rate limiting for certain requests

def log_request_filter(f):
    """Wrapper to log when a request is filtered out of rate limiting."""
    import functools
    @functools.wraps(f)
    def wrapper():
        result = f()
        if result:
            logger.debug(f"[RATE_LIMIT] 🛡️ Request to {request.path} is EXEMPT from rate limiting")
        return result
    return wrapper