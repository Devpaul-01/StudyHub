"""
services/counter_cache_service.py

Redis-backed atomic counters for unread notification/message counts.
See studyhub-redis-caching-implementation-plan.md §4.7, §5.6, §17.1.

Separated from cache_service.py rather than folded into it because
counters use a genuinely different operation set (INCR/DECR against
redis_client directly, not the get/set JSON round-tripping cache_service.py
provides) and mixing the two APIs in one file would blur the "plain
cache-aside" vs. "atomic counter" distinction the plan is careful to keep
explicit throughout §4.7/§7.1.

Why INCR/DECR and not "read the cached count, add one, write it back":
the latter races under concurrent requests (two simultaneous notify() calls
for the same user could both read the same stale value and both write the
same post-increment value, losing one increment). INCR/DECRBY are native
Redis atomic operations — exactly the class of problem they exist to
solve. See plan §7.1.

Self-healing reseed (plan §5.6): because these counters are maintained by
increment/decrement at named funnel points rather than recomputed per read,
a missed increment/decrement anywhere (a bug, or a code path that hasn't
been migrated to go through the funnel point yet — see plan §5.3) would
silently corrupt the count permanently, since increments/decrements
compound. The defense: every counter carries a TTL. If the key is missing
(never set, or expired), the next read recomputes from the real source of
truth via a caller-supplied query function and reseeds the counter. This
bounds the lifetime of any invalidation bug to at most one TTL window,
regardless of cause.

Same fails-open contract as cache_service.py (plan §8): every Redis
operation catches its own exceptions. A Redis outage degrades this module
to "always recompute from the source-of-truth query function," never to a
raised error.

NOTE ON DESIGN: get_unread_notification_count / get_unread_message_count
take the source-of-truth query as an explicit callable (`recompute_fn`)
rather than importing models.py directly. This keeps this module free of
any dependency on the actual Notification/Message model shape — the
caller (routes/student/notifications.py, routes/student/messages.py)
already knows how to run `Notification.query.filter_by(user_id=user_id,
is_read=False).count()`; this module's job is only the cache/counter
mechanics around that query, matching cache_service.py's existing
"no Flask, no ORM coupling" layering discipline.
"""

import logging

from extensions import redis_client

logger = logging.getLogger(__name__)

_UNREAD_NOTIF_TTL_SECONDS = 3600
_UNREAD_MSG_TTL_SECONDS = 3600


def _notif_key(user_id):
    return f"sh:1:notif:unread:{user_id}"


def _msg_key(user_id):
    return f"sh:1:msg:unread:{user_id}"


def _get_counter(key, ttl_seconds, recompute_fn):
    """
    Shared reseed-on-miss logic for both counter types (plan §5.6).

    recompute_fn: zero-arg callable returning the true current count via a
    direct DB query (e.g. `lambda: Notification.query.filter_by(
    user_id=user_id, is_read=False).count()`), used only on a cache miss.
    """
    try:
        cached = redis_client.get(key)
    except Exception:
        logger.warning("Redis GET failed for counter key=%r — recomputing", key, exc_info=True)
        cached = None

    if cached is not None:
        try:
            return int(cached)
        except (TypeError, ValueError):
            logger.warning("Corrupt counter value for key=%r — recomputing", key)

    # Miss, expired, Redis error, or corrupt value — recompute from source
    # of truth and reseed.
    actual = recompute_fn()
    try:
        redis_client.set(key, actual, ex=ttl_seconds)
    except Exception:
        logger.warning("Redis SET failed while reseeding counter key=%r", key, exc_info=True)
    return actual


def _increment(key, ttl_seconds, by=1):
    """
    Atomically increment a counter, seeding its TTL if this is the key's
    first write (INCRBY on a nonexistent key creates it with no TTL, so we
    set an expiry right after — a tiny window where the key exists without
    a TTL is acceptable here since a missing TTL only means the safety-net
    reseed doesn't trigger until a later expiry is set, not a correctness
    problem for the counter's value itself).
    Never raises — see module docstring.
    """
    try:
        pipe = redis_client.pipeline()
        pipe.incrby(key, by)
        pipe.expire(key, ttl_seconds)
        pipe.execute()
    except Exception:
        logger.warning("Redis INCRBY failed for key=%r — counter not updated", key, exc_info=True)


def _decrement(key, ttl_seconds, by=1):
    """
    Atomically decrement a counter. Uses the same expire-after-write
    approach as _increment so a decrement on an unseeded key still ends up
    with a TTL (and, per §5.6, a decrement below zero self-corrects at the
    next reseed rather than being specially guarded against here — the
    reseed's real COUNT(*) is always the ultimate authority).
    Never raises — see module docstring.
    """
    try:
        pipe = redis_client.pipeline()
        pipe.decrby(key, by)
        pipe.expire(key, ttl_seconds)
        pipe.execute()
    except Exception:
        logger.warning("Redis DECRBY failed for key=%r — counter not updated", key, exc_info=True)


# ============================================================================
# Notifications
# ============================================================================

def get_unread_notification_count(user_id, recompute_fn):
    """
    Return the cached unread-notification count for user_id, reseeding from
    recompute_fn() on a miss. See _get_counter / plan §5.6.
    """
    return _get_counter(_notif_key(user_id), _UNREAD_NOTIF_TTL_SECONDS, recompute_fn)


def increment_unread_notification_count(user_id, by=1):
    """Call at the notification-creation funnel point —
    services/notification_service.py::notify(), per plan §17.6."""
    _increment(_notif_key(user_id), _UNREAD_NOTIF_TTL_SECONDS, by)


def decrement_unread_notification_count(user_id, by=1):
    """Call at every mark-read/delete site for unread notifications —
    routes/student/notifications.py, per plan §17.6. `by` should be the
    exact row count the caller's own UPDATE/DELETE already returned
    (bulk operations), or 1 for a single-notification action."""
    _decrement(_notif_key(user_id), _UNREAD_NOTIF_TTL_SECONDS, by)


# ============================================================================
# Messages
# ============================================================================

def get_unread_message_count(user_id, recompute_fn):
    """
    Return the cached unread-message count for user_id, reseeding from
    recompute_fn() on a miss. See _get_counter / plan §5.6.
    """
    return _get_counter(_msg_key(user_id), _UNREAD_MSG_TTL_SECONDS, recompute_fn)


def increment_unread_message_count(user_id, by=1):
    """Call at the message-creation funnel point —
    services/websocket_messages.py::handle_send_message, per plan §17.6."""
    _increment(_msg_key(user_id), _UNREAD_MSG_TTL_SECONDS, by)


def decrement_unread_message_count(user_id, by=1):
    """Call at every mark-read site for unread messages —
    routes/student/messages.py, per plan §17.6. `by` should be the exact
    row count the caller's own bulk UPDATE already returned, or 1 for a
    single-message action."""
    _decrement(_msg_key(user_id), _UNREAD_MSG_TTL_SECONDS, by)
