"""
services/cache_service.py

Document 4 §2.2 (Role B — response/query caching). This module is now backed
by Redis (see extensions.py::redis_client) rather than the in-memory dict it
started as — per Document 4 §2.5's "degrades gracefully when REDIS_URL
unset" design, this module's *API* (the @cached decorator, key_template
interpolation, TTL semantics) is unchanged by that swap; every existing
caller (e.g. services/post_service.py::popular_tags) needed zero changes.
See studyhub-redis-caching-implementation-plan.md §6.2 for the full
rationale behind every design choice below.

Per Document 2 §2's layering rule: this lives in services/ and has no Flask
import, no `request`/`session`/`g`. It's meant to decorate service-layer
functions (e.g. services/post_service.py::popular_tags), not routes
directly — the HTTP layer shouldn't need to know or care whether a given
call was served from cache.

FAILS OPEN, ALWAYS (plan §8): every function below catches every exception
a Redis client call can raise. A Redis outage must degrade every route that
uses this module to its pre-caching behavior — recompute on every call —
never to a raised error. No route or service function calling
get()/set()/delete()/delete_pattern() needs its own try/except; the
boundary is entirely inside this file.
"""

import functools
import json
import logging

from extensions import redis_client

logger = logging.getLogger(__name__)

# Bump this to invalidate every cached key at once, with zero explicit
# delete calls, whenever the *shape* of a cached value changes in a way
# that would make an old cached blob unsafe to deserialize or misleading
# to serve. See plan §3.1. Not currently interpolated into any key by this
# module itself — callers that want versioned keys include "sh:{version}:"
# in their own key_template/key strings (see plan §3.2's key patterns).
CACHE_SCHEMA_VERSION = 1


# ============================================================================
# STORAGE
#
# Values are JSON-encoded before being written to Redis and JSON-decoded on
# read. json.dumps/json.loads is used rather than pickle deliberately: every
# cached value in the plan is already a plain dict/list/int (route response
# bodies, dataclass .to_dict() outputs, or bare integers for the counters)
# with nothing JSON can't represent natively, and pickle would carry a
# deserialization-of-untrusted-data risk if this Redis instance is ever
# exposed more broadly than this app's own containers. See plan §6.2.
# ============================================================================


def get(key):
    """
    Retrieve a cached value by key.

    Returns the cached value, or None on a miss (the key was never set, it
    expired, the stored value wasn't valid JSON, or Redis itself is
    unreachable). Every one of those cases is treated identically by
    callers — a cache miss — which is what makes the cache-aside pattern
    (get() -> None -> recompute -> set()) safe to use unconditionally.
    """
    try:
        raw = redis_client.get(key)
    except Exception:
        logger.warning(
            "Redis GET failed for key=%r — treating as cache miss", key, exc_info=True
        )
        return None

    if raw is None:
        return None

    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Redis value for key=%r was not valid JSON — treating as cache miss", key
        )
        return None


def set(key, value, ttl_seconds):
    """Store a value under key, expiring ttl_seconds from now.

    Never raises. On failure the write is silently dropped — see plan §8:
    a cache write failure must never fail the request that triggered it.
    """
    try:
        redis_client.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception:
        logger.warning(
            "Redis SET failed for key=%r — write silently dropped", key, exc_info=True
        )


def delete(key):
    """Remove a key if present. No-op if it isn't, and no-op on Redis
    failure — see plan §8: invalidation failures degrade to "stale until
    TTL expiry," not to a failed request. Every hard-invalidated cache in
    the plan's §5 already carries a TTL as a backstop for exactly this
    failure mode.
    """
    try:
        redis_client.delete(key)
    except Exception:
        logger.warning("Redis DELETE failed for key=%r", key, exc_info=True)


def delete_pattern(prefix):
    """
    Delete every key matching prefix (a glob-style pattern, e.g.
    "sh:1:lb:rank:482:*"). NEW — not in the original in-memory version,
    because a plain dict had no equivalent operation worth naming; required
    for the pattern-delete calls in plan §5 (e.g. invalidating every
    period/department combination of one user's rank cache in one call).

    Uses SCAN, not KEYS: KEYS is O(N) over the entire keyspace and blocks
    the single-threaded Redis server for the duration; SCAN is
    cursor-based and non-blocking, which matters once this keyspace has
    real production traffic behind it.

    A partial failure partway through the scan/delete loop degrades to
    "some of those keys are stale until their own TTL" — exactly what the
    TTL backstop on every hard-invalidated cache already exists to bound.
    Never raises.
    """
    try:
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor=cursor, match=prefix, count=200)
            if keys:
                redis_client.delete(*keys)
            if cursor == 0:
                break
    except Exception:
        logger.warning(
            "Redis pattern delete failed for prefix=%r", prefix, exc_info=True
        )


def clear():
    """Drop every cached entry belonging to this application's namespace
    (not the whole Redis DB, in case Redis is ever shared with another
    application). Mainly useful for tests.
    """
    delete_pattern(f"sh:{CACHE_SCHEMA_VERSION}:*")


# ============================================================================
# DECORATOR
# ============================================================================

def cached(key_template, ttl_seconds):
    """
    Decorator for service-layer functions. Unchanged in behavior from the
    original in-memory version — it only ever calls the get()/set()
    functions above, both of which keep their exact original signatures,
    so this decorator required no changes for the Redis swap.

    key_template supports {arg_name} interpolation against the decorated
    function's actual call arguments (bound via inspect-free
    functools-friendly binding — positional and keyword args are matched
    to the wrapped function's parameter names), e.g.:

        @cached("leaderboard:{period}:{department}", ttl_seconds=60)
        def get_global_leaderboard(period, department=None, ...): ...

    On a cache hit, returns the previously-cached result without executing
    the function body at all — including skipping any DB queries inside
    it. On a miss, executes normally, stores the return value with the
    given TTL, then returns it.

    Values are now JSON-serialized on the way into Redis (see the module
    docstring) — this means the decorated function's return value must be
    JSON-serializable (a plain dict/list/str/int/float/bool/None, or
    something with an equivalent .to_dict() already called before
    returning). This is true of every candidate in the caching plan.

    A key_template with no `{}` placeholders is valid and just becomes a
    fixed key — e.g. @cached("popular_tags", ttl_seconds=300) for a
    function with no arguments that vary the result.
    """
    def decorator(func):
        # Map positional args to parameter names so key_template can
        # reference them the same way regardless of how the caller passed
        # them (positionally or by keyword).
        param_names = func.__code__.co_varnames[:func.__code__.co_argcount]

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            bound = dict(zip(param_names, args))
            bound.update(kwargs)
            try:
                key = key_template.format(**bound)
            except (KeyError, IndexError):
                # A referenced placeholder wasn't supplied (e.g. called
                # with fewer args than the template expects). Fail open —
                # don't cache this call rather than raising, since a
                # caching bug shouldn't be able to break the underlying
                # function.
                return func(*args, **kwargs)

            cached_value = get(key)
            if cached_value is not None:
                return cached_value

            result = func(*args, **kwargs)
            if result is not None:
                set(key, result, ttl_seconds)
            return result

        # Expose the underlying function and manual cache controls for
        # callers/tests that need to bypass or invalidate deliberately.
        wrapper.__wrapped_func__ = func
        wrapper.cache_delete = lambda *a, **kw: delete(
            key_template.format(**{**dict(zip(param_names, a)), **kw})
        )
        return wrapper
    return decorator
