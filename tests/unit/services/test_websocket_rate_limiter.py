"""
Tests for services/websocket_rate_limiter.py.

Covers all three classes: RateLimiter (process-local sliding window),
TypingStatusManager (process-local, with auto-expiry), and
RedisFixedWindowLimiter (the distributed, cross-instance limiter the
module's docstring says was the actual point of this file's refactor —
websocket_threads.py's per-user thread-message rate limit).

This module is explicitly named in tests/unit/conftest.py's own
_MODULES_WITH_BOUND_REDIS_CLIENT list (fixtures for it were anticipated)
but — per that same conftest comment — no test file was ever actually
written for it. This fills that gap.
"""

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from services.websocket_rate_limiter import (
    RateLimiter,
    TypingStatusManager,
    RedisFixedWindowLimiter,
)


# ============================================================================
# RateLimiter — process-local sliding window
# ============================================================================

def test_rate_limiter_first_request_allowed_with_correct_remaining():
    limiter = RateLimiter()

    allowed, remaining = limiter.check_rate_limit("user_1_messages", limit=5, window=60)

    assert allowed is True
    assert remaining == 4


def test_rate_limiter_blocks_once_limit_is_reached():
    limiter = RateLimiter()
    key = "user_1_messages"

    for _ in range(3):
        allowed, _ = limiter.check_rate_limit(key, limit=3, window=60)
        assert allowed is True

    allowed, remaining = limiter.check_rate_limit(key, limit=3, window=60)

    assert allowed is False
    assert remaining == 0


def test_rate_limiter_different_keys_are_independent():
    limiter = RateLimiter()

    for _ in range(3):
        limiter.check_rate_limit("user_1_messages", limit=3, window=60)

    # A different key's budget must be untouched by user_1's usage.
    allowed, remaining = limiter.check_rate_limit("user_2_messages", limit=3, window=60)

    assert allowed is True
    assert remaining == 2


def test_rate_limiter_requests_outside_window_expire_and_free_up_budget():
    limiter = RateLimiter()
    key = "user_1_messages"

    with freeze_time("2026-01-01 12:00:00"):
        for _ in range(3):
            limiter.check_rate_limit(key, limit=3, window=60)
        blocked, _ = limiter.check_rate_limit(key, limit=3, window=60)
        assert blocked is False

    # 61 seconds later — outside the 60s window, so the earlier
    # timestamps must be pruned and the budget freed up again.
    with freeze_time("2026-01-01 12:01:01"):
        allowed, remaining = limiter.check_rate_limit(key, limit=3, window=60)
        assert allowed is True
        assert remaining == 2


def test_rate_limiter_cleanup_old_entries_removes_stale_keys():
    limiter = RateLimiter()

    with freeze_time("2026-01-01 12:00:00"):
        limiter.check_rate_limit("stale_key", limit=5, window=60)

    with freeze_time("2026-01-01 14:00:00"):  # 2 hours later, past max_age=3600
        limiter.cleanup_old_entries(max_age=3600)

    assert "stale_key" not in limiter.limits


def test_rate_limiter_cleanup_old_entries_keeps_recent_keys():
    limiter = RateLimiter()
    limiter.check_rate_limit("fresh_key", limit=5, window=60)

    limiter.cleanup_old_entries(max_age=3600)

    assert "fresh_key" in limiter.limits


def test_rate_limiter_reset_user_limits_only_clears_that_users_keys():
    limiter = RateLimiter()
    limiter.check_rate_limit("user_1_messages", limit=5, window=60)
    limiter.check_rate_limit("user_1_reactions", limit=5, window=60)
    limiter.check_rate_limit("user_2_messages", limit=5, window=60)

    limiter.reset_user_limits(1)

    assert "user_1_messages" not in limiter.limits
    assert "user_1_reactions" not in limiter.limits
    assert "user_2_messages" in limiter.limits


def test_rate_limiter_get_stats_counts_keys_and_timestamps():
    limiter = RateLimiter()
    limiter.check_rate_limit("key_a", limit=10, window=60)
    limiter.check_rate_limit("key_a", limit=10, window=60)
    limiter.check_rate_limit("key_b", limit=10, window=60)

    stats = limiter.get_stats()

    assert stats["total_keys"] == 2
    assert stats["total_timestamps"] == 3


# ============================================================================
# TypingStatusManager
# ============================================================================

def test_typing_status_manager_set_then_get_includes_user():
    mgr = TypingStatusManager(timeout=10)
    mgr.set_typing("thread_1", user_id=42)

    assert mgr.get_typing_users("thread_1") == [42]


def test_typing_status_manager_unknown_conversation_returns_empty_list():
    mgr = TypingStatusManager(timeout=10)
    assert mgr.get_typing_users("nonexistent_thread") == []


def test_typing_status_manager_remove_typing_clears_user_and_empty_conversation():
    mgr = TypingStatusManager(timeout=10)
    mgr.set_typing("thread_1", user_id=42)

    mgr.remove_typing("thread_1", user_id=42)

    assert mgr.get_typing_users("thread_1") == []
    assert "thread_1" not in mgr.typing_status


def test_typing_status_manager_expired_entries_excluded_from_result():
    mgr = TypingStatusManager(timeout=10)

    with freeze_time("2026-01-01 12:00:00"):
        mgr.set_typing("thread_1", user_id=42)

    with freeze_time("2026-01-01 12:00:11"):  # 11s later, past the 10s timeout
        result = mgr.get_typing_users("thread_1")

    assert result == []
    # Note: get_typing_users() deletes the expired *user* entry but,
    # unlike remove_typing()/cleanup_expired(), does not also drop the
    # now-empty conversation_key dict left behind — a minor inconsistency,
    # not a functional bug (the next get_typing_users() call still
    # correctly returns [] for it, and cleanup_expired() sweeps it up).
    assert mgr.typing_status.get("thread_1") == {}


def test_typing_status_manager_fresh_and_expired_users_both_present():
    mgr = TypingStatusManager(timeout=10)

    with freeze_time("2026-01-01 12:00:00"):
        mgr.set_typing("thread_1", user_id=1)  # will expire

    with freeze_time("2026-01-01 12:00:05"):
        mgr.set_typing("thread_1", user_id=2)  # still fresh at the check below

    with freeze_time("2026-01-01 12:00:11"):  # user 1 is 11s old, user 2 is 6s old
        result = mgr.get_typing_users("thread_1")

    assert result == [2]


def test_typing_status_manager_cleanup_expired_removes_only_stale_users():
    mgr = TypingStatusManager(timeout=10)

    with freeze_time("2026-01-01 12:00:00"):
        mgr.set_typing("thread_1", user_id=1)

    with freeze_time("2026-01-01 12:00:05"):
        mgr.set_typing("thread_1", user_id=2)

    with freeze_time("2026-01-01 12:00:11"):
        mgr.cleanup_expired()

    assert 1 not in mgr.typing_status.get("thread_1", {})
    assert 2 in mgr.typing_status["thread_1"]


def test_typing_status_manager_remove_user_from_all_clears_every_conversation():
    mgr = TypingStatusManager(timeout=10)
    mgr.set_typing("thread_1", user_id=99)
    mgr.set_typing("thread_2", user_id=99)
    mgr.set_typing("thread_2", user_id=5)  # a different user, must survive

    mgr.remove_user_from_all(99)

    assert "thread_1" not in mgr.typing_status  # emptied -> cleaned up
    assert mgr.get_typing_users("thread_2") == [5]


# ============================================================================
# RedisFixedWindowLimiter
# ============================================================================

def test_redis_fixed_window_limiter_allows_under_limit(fakeredis_client):
    limiter = RedisFixedWindowLimiter()

    allowed, remaining = limiter.check_rate_limit("thread_msg_1", limit=5, window=60)

    assert allowed is True
    assert remaining == 4


def test_redis_fixed_window_limiter_blocks_over_limit(fakeredis_client):
    limiter = RedisFixedWindowLimiter()
    key = "thread_msg_1"

    for _ in range(3):
        limiter.check_rate_limit(key, limit=3, window=60)

    allowed, remaining = limiter.check_rate_limit(key, limit=3, window=60)

    assert allowed is False
    assert remaining == 0


def test_redis_fixed_window_limiter_different_keys_independent(fakeredis_client):
    limiter = RedisFixedWindowLimiter()

    for _ in range(3):
        limiter.check_rate_limit("thread_msg_1", limit=3, window=60)

    allowed, remaining = limiter.check_rate_limit("thread_msg_2", limit=3, window=60)

    assert allowed is True
    assert remaining == 2


def test_redis_fixed_window_limiter_sets_expiry_on_the_bucket_key(fakeredis_client):
    limiter = RedisFixedWindowLimiter()

    limiter.check_rate_limit("thread_msg_1", limit=5, window=60)

    matching_keys = fakeredis_client.keys("sh:1:ws:msgrate:thread_msg_1:*")
    assert len(matching_keys) == 1
    ttl = fakeredis_client.ttl(matching_keys[0])
    assert 0 < ttl <= 60


def test_redis_fixed_window_limiter_fails_open_on_redis_error(raising_redis_client):
    """
    Per the class's own docstring: on any Redis error, check() must
    return (allowed=True, remaining=None) rather than blocking the
    event or raising — the opposite of distributed_lock.py's
    deliberate fail-closed behavior.
    """
    limiter = RedisFixedWindowLimiter()

    allowed, remaining = limiter.check_rate_limit("thread_msg_1", limit=5, window=60)

    assert allowed is True
    assert remaining is None


def test_redis_fixed_window_limiter_custom_key_prefix_is_used(fakeredis_client):
    limiter = RedisFixedWindowLimiter(key_prefix="sh:1:ws:customrate")

    limiter.check_rate_limit("some_key", limit=5, window=60)

    assert fakeredis_client.keys("sh:1:ws:customrate:some_key:*")
    assert not fakeredis_client.keys("sh:1:ws:msgrate:some_key:*")
