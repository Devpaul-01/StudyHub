"""
services/cache_service.py

Document 4 §2.2 (Role B — response/query caching), simplified for this phase:
in-memory dict storage with TTL expiration instead of Redis. Per Document 4
§2.5's "degrades gracefully when REDIS_URL unset" design, this module's
*API* (the @cached decorator, key_template interpolation, TTL semantics) is
exactly what a future Redis-backed version will expose too — swapping the
storage backend later should mean changing what's inside this file, not
changing any of its callers.

Per Document 2 §2's layering rule: this lives in services/ and has no Flask
import, no `request`/`session`/`g`. It's meant to decorate service-layer
functions (e.g. services/post_service.py::popular_tags), not routes
directly — the HTTP layer shouldn't need to know or care whether a given
call was served from cache.

NOT thread-safe beyond CPython's GIL protecting individual dict
get/set/del operations — good enough for the single-process deployment
this codebase currently requires (see ARCHITECTURE_NOTES.md, referenced
throughout Document 4). If/when this app runs multiple workers, this
in-memory store stops being "shared across the entire backend" (each
worker gets its own cache) — that's exactly the ceiling Document 4 §2.1
Role C/D is about, and exactly why this module is designed to be
Redis-swappable rather than assumed-permanent.
"""

import time
import functools
import threading

# ============================================================================
# STORAGE
#
# _store maps cache key -> (expires_at_epoch_seconds, value).
# A real Redis backend would replace this dict (and the lock) with Redis
# SETEX/GET calls; the decorator below and its call sites would not change.
# ============================================================================

_store = {}
_lock = threading.Lock()


def _now():
    return time.time()


def get(key):
    """
    Retrieve a cached value by key.

    Returns the cached value, or None on a miss (either the key was never
    set, or it expired). Expired entries are lazily evicted here rather
    than via a background sweep — simplest correct behavior for an
    in-memory dict of this expected size (a handful of distinct cached
    function results, not a general-purpose cache with many keys).
    """
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if _now() >= expires_at:
            del _store[key]
            return None
        return value


def set(key, value, ttl_seconds):
    """Store a value under key, expiring ttl_seconds from now."""
    with _lock:
        _store[key] = (_now() + ttl_seconds, value)


def delete(key):
    """Remove a key if present. No-op if it isn't. Used for manual invalidation."""
    with _lock:
        _store.pop(key, None)


def clear():
    """Drop every cached entry. Mainly useful for tests."""
    with _lock:
        _store.clear()


# ============================================================================
# DECORATOR
# ============================================================================

def cached(key_template, ttl_seconds):
    """
    Decorator for service-layer functions.

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

    Values are stored as-is (real Python objects, not JSON-serialized) in
    this in-memory phase, since everything stays in one process — the
    JSON-serialization requirement from Document 4 §2.2 only becomes
    necessary once this moves to a real out-of-process Redis backend
    (flagged here so that migration doesn't get missed later).

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
