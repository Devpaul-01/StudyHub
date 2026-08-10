"""
services/distributed_lock.py

Generic Redis-backed distributed lock. Introduced for the horizontal-
scaling refactor of scheduler.py (Document: "StudyHub — Horizontal Scaling
Refactor for WebSocket Services & Scheduler", §12-§18), but deliberately
job-agnostic — any future singleton-execution requirement (a one-time
migration script, an admin action that must not run concurrently from two
requests) can reuse this instead of hand-rolling another lock.

DELIBERATE EXCEPTION TO THIS APP'S FAIL-OPEN DEFAULT:
Every other Redis consumer in this codebase (cache_service.py,
counter_cache_service.py, rate_limit_service.py) fails open — a Redis
outage degrades the feature, never blocks the request. This module does
the opposite on purpose: if Redis is unreachable, acquire() returns a
lock that reports NOT acquired. The caller is expected to skip the
protected operation entirely rather than run it unprotected.

Why: this lock exists specifically to prevent duplicate execution of
operations where duplicate execution is the actual danger (see
scheduler.py's job bodies — leaderboard_service.take_snapshot() has a real
TOCTOU race with no unique DB constraint backing it up). Failing open here
would silently defeat the reason the lock exists in the first place. See
the design doc §5/§7 for the full reasoning and the specific race this
closes.

SAFETY PROPERTIES:
  - Acquire is atomic: SET key value NX EX ttl_seconds (single Redis
    command, no separate EXISTS-then-SET race).
  - Release is atomic compare-and-delete via a Lua script: a lock is only
    ever deleted by the exact owner_token that acquired it, so a lock
    whose TTL already expired and was re-acquired by a different instance
    can never be deleted out from under that new owner by the original
    (now-late) caller's release() call.
  - owner_token is unique per acquisition attempt (host:pid:uuid4), not
    just per-process, so even the same process re-acquiring a lock that
    it previously held (and which expired) gets a fresh token each time.
"""

from __future__ import annotations

import logging
import os
import socket as _socket
import uuid

from extensions import redis_client

logger = logging.getLogger(__name__)


# Lua script for atomic compare-and-delete release. Passed to redis-py's
# `.eval()`. KEYS[1] = lock key, ARGV[1] = owner_token this caller believes
# it holds. Only deletes if the stored value still matches.
_RELEASE_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""

# Lua script for atomic extend (used only if a caller wants a heartbeat-
# style long-running-job pattern instead of a single big TTL). Only
# extends if this caller still owns the lock.
_EXTEND_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("EXPIRE", KEYS[1], ARGV[2])
else
    return 0
end
"""


def _make_owner_token() -> str:
    """host:pid:random — unique per acquisition attempt, human-readable
    enough to show up usefully in logs when diagnosing which instance
    holds a lock."""
    host = _socket.gethostname()
    pid = os.getpid()
    return f"{host}:{pid}:{uuid.uuid4().hex[:12]}"


class DistributedLock:
    """
    Usage:

        with DistributedLock("sh:1:sched:lock:weekly_snapshot", ttl_seconds=600) as lock:
            if not lock.acquired:
                logger.info("[SCHED_JOB_SKIPPED_LOCK_HELD] job_id=weekly_snapshot")
                return
            ... do the work ...

    Or without the context manager:

        lock = DistributedLock("sh:1:sched:lock:x", ttl_seconds=600)
        if lock.acquire():
            try:
                ...
            finally:
                lock.release()
    """

    def __init__(self, key: str, ttl_seconds: int):
        self.key = key
        self.ttl_seconds = ttl_seconds
        self.owner_token = _make_owner_token()
        self.acquired = False

    def acquire(self) -> bool:
        """
        Attempt to acquire the lock. Returns True/False; never raises.

        On any Redis error (timeout, connection refused, etc.), logs a
        warning and returns False — see module docstring: this is the one
        place in the app's Redis usage that fails CLOSED, deliberately.
        """
        try:
            got = redis_client.set(
                self.key, self.owner_token, nx=True, ex=self.ttl_seconds
            )
            self.acquired = bool(got)
            if self.acquired:
                logger.info(
                    "[LOCK_ACQUIRED] key=%s owner=%s ttl=%ss",
                    self.key, self.owner_token, self.ttl_seconds,
                )
            else:
                logger.info(
                    "[LOCK_ACQUIRE_FAILED] key=%s owner=%s reason=already_held",
                    self.key, self.owner_token,
                )
            return self.acquired
        except Exception:
            logger.warning(
                "[LOCK_ACQUIRE_FAILED] key=%s owner=%s reason=redis_error "
                "— failing CLOSED (job will not run this cycle)",
                self.key, self.owner_token, exc_info=True,
            )
            self.acquired = False
            return False

    def release(self) -> bool:
        """
        Release the lock, but only if this instance still owns it (atomic
        compare-and-delete via Lua — see module docstring). Never raises.
        Safe to call even if acquire() was never called or failed.
        """
        if not self.acquired:
            return False
        try:
            result = redis_client.eval(_RELEASE_SCRIPT, 1, self.key, self.owner_token)
            released = bool(result)
            if released:
                logger.info("[LOCK_RELEASED] key=%s owner=%s", self.key, self.owner_token)
            else:
                # Someone else's TTL-expiry-then-reacquire beat us here —
                # correct behavior is to do nothing, not force-delete.
                logger.warning(
                    "[LOCK_RELEASE_SKIPPED] key=%s owner=%s reason=no_longer_owner "
                    "(lock TTL likely expired and was re-acquired by another instance "
                    "before this release() call ran)",
                    self.key, self.owner_token,
                )
            self.acquired = False
            return released
        except Exception:
            logger.warning(
                "[LOCK_RELEASE_FAILED] key=%s owner=%s reason=redis_error — "
                "lock will expire naturally via its TTL",
                self.key, self.owner_token, exc_info=True,
            )
            return False

    def extend(self, additional_seconds: int) -> bool:
        """
        Optional heartbeat-style extension for long-running jobs that want
        a shorter base TTL plus periodic renewal instead of one large
        up-front TTL. Not used by scheduler.py's current jobs (their TTLs
        are set generously up front instead — see scheduler.py), but
        available for future jobs with highly variable runtime. Atomic;
        only extends if still the owner. Never raises.
        """
        if not self.acquired:
            return False
        try:
            result = redis_client.eval(
                _EXTEND_SCRIPT, 1, self.key, self.owner_token, additional_seconds
            )
            return bool(result)
        except Exception:
            logger.warning(
                "[LOCK_EXTEND_FAILED] key=%s owner=%s", self.key, self.owner_token,
                exc_info=True,
            )
            return False

    def __enter__(self) -> "DistributedLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
