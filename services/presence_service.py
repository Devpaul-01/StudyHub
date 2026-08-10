"""
services/presence_service.py

Redis-backed WebSocket presence: "is this user connected right now" and
"which thread is this user actively viewing right now." Replaces the
process-local `online_users` / `socket_to_user` dicts in
MessageWebSocketManager and the `user_active_thread` dict in
ThreadWebSocketManager — both of which were being read to make decisions
(online badges, a new message's initial delivery status) that need to be
correct regardless of which application instance a given user's socket
happens to be connected to. See the horizontal-scaling design doc §6.3 for
the full design rationale.

Per this codebase's established layering rule (see online_status_service.py
and cache_service.py's own docstrings): no Flask imports, no
request/session/g. This is pure presence mechanics over Redis; the
WebSocket managers own the Socket.IO-specific parts (join_room, sid
lifecycle) and call into this module for the distributed bookkeeping.

FAILS OPEN, matching cache_service.py / counter_cache_service.py's
established precedent for ephemeral/cosmetic state: every function below
catches its own Redis exceptions. A Redis outage degrades presence to
"nobody appears online" and "nobody has an active thread" — stale badges,
slightly-less-accurate message read-ticks — never a raised exception into
a WebSocket event handler or an HTTP request. This is the OPPOSITE
philosophy from distributed_lock.py's deliberate fail-closed behavior;
presence being "wrong for a bit" is cosmetic, duplicate scheduled job
execution is not — see that module's docstring for the contrast.

KEY DESIGN — why a SET-of-sids cross-checked against per-sid TTL keys,
rather than one structure:
Redis has no per-member TTL within a Set/Hash, only whole-key TTL. A user
can have multiple simultaneous sockets (multi-device — see design doc
Scenario G), so "presence" can't be a single TTL'd key per user without
one device's disconnect (or crash) incorrectly flipping the user offline
while other devices are still live. The split below —
sh:1:ws:sock:{sid} (TTL'd, authoritative "is THIS socket alive") plus
sh:1:ws:user:{user_id} (a plain index Set, no TTL) — means "is user X
online" is answered by checking whether ANY of their indexed sids still
has a live sock: key, self-healing the index (SREM'ing stale entries) on
the same read with no separate sweep job required for correctness.
"""

from __future__ import annotations

import logging

from extensions import redis_client

logger = logging.getLogger(__name__)

# Comfortably longer than Socket.IO's typical ping_interval/ping_timeout
# (on the order of 20-25s by default), so a handful of missed heartbeat
# cycles don't cause a false "offline" before the explicit disconnect
# handler (which fires immediately in the overwhelming majority of cases)
# has a chance to run. Refreshed well before this by the periodic local
# sweep each WebSocket manager runs (every 45s — see websocket_messages.py
# / websocket_threads.py) — see design doc §6.3 for why the refresh is a
# self-contained backend sweep rather than depending on an unconfirmed
# frontend ping loop. Tunable; flagged as an open question in the design
# doc.
PRESENCE_TTL_SECONDS = 120


def _sock_key(sid: str) -> str:
    return f"sh:1:ws:sock:{sid}"


def _user_sockets_key(user_id: int) -> str:
    return f"sh:1:ws:user:{user_id}"


def _active_thread_key(user_id: int) -> str:
    return f"sh:1:ws:active_thread:{user_id}"


# ============================================================================
# CONNECTION PRESENCE
# ============================================================================

def register_connection(user_id: int, sid: str, instance_id: str) -> None:
    """
    Call on connect/authenticate. Two Redis ops (a pipeline, one round
    trip): mark this specific socket alive with a TTL, and index it under
    the user's known-sockets set (untimed — see module docstring, this
    index is cross-checked against sock: keys on every read, never
    trusted alone).

    Never raises.
    """
    try:
        pipe = redis_client.pipeline()
        pipe.set(_sock_key(sid), f"{instance_id}:{user_id}", ex=PRESENCE_TTL_SECONDS)
        pipe.sadd(_user_sockets_key(user_id), sid)
        pipe.execute()
        logger.info(
            "[PRESENCE_REGISTERED] user_id=%s sid=%s instance=%s ttl=%ss",
            user_id, sid, instance_id, PRESENCE_TTL_SECONDS,
        )
    except Exception:
        logger.warning(
            "[PRESENCE_REGISTER_FAILED] user_id=%s sid=%s — presence not recorded, "
            "will degrade to 'offline' for this connection until Redis recovers",
            user_id, sid, exc_info=True,
        )


def remove_connection(user_id: int, sid: str) -> None:
    """
    Call on disconnect. Immediate cleanup in the common case — the TTL on
    sock: keys is a safety net for the crash case (process dies without
    running this), not the primary removal mechanism. Never raises.
    """
    try:
        pipe = redis_client.pipeline()
        pipe.delete(_sock_key(sid))
        pipe.srem(_user_sockets_key(user_id), sid)
        pipe.execute()
        logger.info("[PRESENCE_REMOVED] user_id=%s sid=%s", user_id, sid)
    except Exception:
        logger.warning(
            "[PRESENCE_REMOVE_FAILED] user_id=%s sid=%s — stale entry will "
            "self-heal via TTL expiry (%ss) or the next is_user_online lazy cleanup",
            user_id, sid, PRESENCE_TTL_SECONDS, exc_info=True,
        )


def touch_connection(sid: str) -> None:
    """
    Refresh a single socket's TTL without needing to know its user_id.
    Called by each manager's periodic local sweep (see design doc §6.3) —
    logged at DEBUG only, per the "don't flood logs with heartbeat
    traffic" observability guidance, since this fires every ~45s per
    locally-held socket. Never raises.
    """
    try:
        redis_client.expire(_sock_key(sid), PRESENCE_TTL_SECONDS)
        logger.debug("[PRESENCE_TOUCH] sid=%s", sid)
    except Exception:
        logger.debug("[PRESENCE_TOUCH_FAILED] sid=%s", sid, exc_info=True)


def is_user_online(user_id: int) -> bool:
    """
    True if at least one of this user's known sockets still has a live
    sock: key. Lazily prunes any sid found stale (its sock: key expired
    or was never set, e.g. after a crash) from the index set as a side
    effect — no separate sweep job needed to keep the index from growing
    unboundedly stale.

    Fails open to False on Redis error (see module docstring) — never
    raises.
    """
    try:
        sids = redis_client.smembers(_user_sockets_key(user_id))
        if not sids:
            return False

        sock_keys = [_sock_key(sid) for sid in sids]
        alive_flags = redis_client.mget(sock_keys)

        stale = [sid for sid, alive in zip(sids, alive_flags) if alive is None]
        if stale:
            try:
                redis_client.srem(_user_sockets_key(user_id), *stale)
                logger.debug(
                    "[PRESENCE_STALE_PRUNED] user_id=%s pruned_sids=%s",
                    user_id, stale,
                )
            except Exception:
                pass  # pruning failure just means we redo this check next time

        return any(alive is not None for alive in alive_flags)
    except Exception:
        logger.debug("[PRESENCE_CHECK_FAILED] user_id=%s — treating as offline", user_id, exc_info=True)
        return False


def get_online_user_ids(user_ids: list[int]) -> set[int]:
    """
    Batch version of is_user_online — avoids N round trips when checking
    many users at once (e.g. broadcast_status_change's connection list,
    or annotating a thread member list with online badges). Uses one
    pipeline for all the SMEMBERS calls and one MGET for all the sock:
    keys combined, rather than looping the single-user function.

    Fails open to an empty set on Redis error — never raises.
    """
    if not user_ids:
        return set()

    try:
        pipe = redis_client.pipeline()
        for uid in user_ids:
            pipe.smembers(_user_sockets_key(uid))
        per_user_sids = pipe.execute()

        all_sids: list[str] = []
        sid_owner: dict[str, int] = {}
        for uid, sids in zip(user_ids, per_user_sids):
            for sid in sids:
                all_sids.append(sid)
                sid_owner[sid] = uid

        if not all_sids:
            return set()

        alive_flags = redis_client.mget([_sock_key(sid) for sid in all_sids])

        online: set[int] = set()
        for sid, alive in zip(all_sids, alive_flags):
            if alive is not None:
                online.add(sid_owner[sid])

        return online
    except Exception:
        logger.debug("[PRESENCE_BATCH_CHECK_FAILED] user_count=%s — treating all as offline", len(user_ids), exc_info=True)
        return set()


def get_local_sids_for_touch_hint() -> None:
    """Intentionally absent — each manager keeps its OWN process-local
    list of sids it currently holds, for use by its periodic touch sweep.
    That list is legitimately process-local bookkeeping (never read for
    cross-instance correctness, only used to know what to re-EXPIRE), so
    it does not belong in this Flask-free, per-process-agnostic module.
    See MessageWebSocketManager / ThreadWebSocketManager's own
    `self.online_users` (now repurposed as exactly this: a local sid
    registry for the touch sweep, not a cross-instance source of truth)."""
    raise NotImplementedError("See docstring — this function is documentation, not code.")


# ============================================================================
# ACTIVE-THREAD TRACKING
# ============================================================================

def set_active_thread(user_id: int, thread_id: int) -> None:
    """
    Call on join_thread_room. Replaces
    ThreadWebSocketManager.user_active_thread[user_id] = thread_id.
    Preserves the exact prior semantics: single value per user, last
    join wins across tabs/devices (not per-socket tracking) — matching
    the original in-memory dict's behavior exactly, not expanding it.
    Never raises.
    """
    try:
        redis_client.set(_active_thread_key(user_id), thread_id, ex=PRESENCE_TTL_SECONDS)
        logger.debug("[ACTIVE_THREAD_SET] user_id=%s thread_id=%s", user_id, thread_id)
    except Exception:
        logger.warning(
            "[ACTIVE_THREAD_SET_FAILED] user_id=%s thread_id=%s — new messages in this "
            "thread may get a less-accurate initial status until this recovers",
            user_id, thread_id, exc_info=True,
        )


def clear_active_thread(user_id: int, expected_thread_id: int | None = None) -> None:
    """
    Call on leave_thread_room and on disconnect. If expected_thread_id is
    given, only clears when the currently-stored value matches (so a
    disconnect on a stale/background tab doesn't clobber a still-active
    foreground tab's active-thread value for the same user — best-effort;
    exact precision isn't required here since the TTL self-heals any
    staleness within PRESENCE_TTL_SECONDS regardless, per the design
    doc's "ephemeral, OK to have brief staleness" classification for this
    state). Never raises.
    """
    try:
        if expected_thread_id is not None:
            current = redis_client.get(_active_thread_key(user_id))
            if current is not None and str(current) != str(expected_thread_id):
                return  # a different (newer) active-thread value is in place; leave it
        redis_client.delete(_active_thread_key(user_id))
        logger.debug("[ACTIVE_THREAD_CLEARED] user_id=%s", user_id)
    except Exception:
        logger.debug("[ACTIVE_THREAD_CLEAR_FAILED] user_id=%s", user_id, exc_info=True)


def get_active_thread(user_id: int) -> int | None:
    """
    Returns the thread_id the user is currently viewing, or None. Fails
    open to None on Redis error (§ design doc: worst case is a message
    gets 'delivered' instead of 'read' as its initial status — cosmetic).
    Never raises.
    """
    try:
        val = redis_client.get(_active_thread_key(user_id))
        return int(val) if val is not None else None
    except Exception:
        logger.debug("[ACTIVE_THREAD_GET_FAILED] user_id=%s", user_id, exc_info=True)
        return None


def get_active_threads_batch(user_ids: list[int]) -> dict[int, int]:
    """
    Batch version of get_active_thread — one MGET instead of N individual
    GETs. Used by websocket_threads.py::send_thread_message, which needs
    this for every OTHER member on every single message send (a genuinely
    hot path), matching the same N+1-avoidance reasoning as
    get_online_user_ids above and the batch-load pattern already
    established throughout this codebase's REST routes (crud.py,
    discovery.py, membership.py).

    Returns {user_id: thread_id} — only for users who currently have an
    active thread set; a user with none simply has no entry (callers use
    .get(uid) and treat a missing key the same as None, matching
    get_active_thread's single-user return shape).

    Fails open to an empty dict on Redis error — never raises.
    """
    if not user_ids:
        return {}

    try:
        keys = [_active_thread_key(uid) for uid in user_ids]
        values = redis_client.mget(keys)
        return {
            uid: int(val)
            for uid, val in zip(user_ids, values)
            if val is not None
        }
    except Exception:
        logger.debug(
            "[ACTIVE_THREAD_BATCH_GET_FAILED] user_count=%s", len(user_ids), exc_info=True
        )
        return {}


def touch_active_thread(user_id: int) -> None:
    """Refresh TTL, called from the same periodic sweep as touch_connection.
    Never raises."""
    try:
        redis_client.expire(_active_thread_key(user_id), PRESENCE_TTL_SECONDS)
    except Exception:
        logger.debug("[ACTIVE_THREAD_TOUCH_FAILED] user_id=%s", user_id, exc_info=True)
