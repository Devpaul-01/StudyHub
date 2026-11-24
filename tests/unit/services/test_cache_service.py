"""
Tests for services/cache_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.7. Every function has an
explicit "fails open on any Redis exception" contract -- both the happy
path (fakeredis) and the failure path (raising mock) need coverage.
"""

import pytest
from unittest.mock import Mock

from services import cache_service


# ============================================================================
# get / set / delete — happy path
# ============================================================================

def test_get_returns_decoded_value_on_hit(fakeredis_client):
    cache_service.set("k1", {"a": 1, "b": [1, 2, 3]}, ttl_seconds=60)
    assert cache_service.get("k1") == {"a": 1, "b": [1, 2, 3]}


def test_get_returns_none_on_miss(fakeredis_client):
    assert cache_service.get("does-not-exist") is None


def test_get_returns_none_on_corrupt_json(fakeredis_client):
    fakeredis_client.set("corrupt-key", "{not valid json")
    assert cache_service.get("corrupt-key") is None


def test_get_fails_open_on_redis_exception(raising_redis_client):
    assert cache_service.get("anything") is None


def test_set_writes_with_correct_ttl(fakeredis_client):
    cache_service.set("ttl-key", "value", ttl_seconds=120)
    ttl = fakeredis_client.ttl("ttl-key")
    assert 0 < ttl <= 120


def test_set_fails_open_silently_on_exception(raising_redis_client):
    # Must not raise.
    cache_service.set("k", "v", ttl_seconds=60)


def test_delete_removes_existing_key(fakeredis_client):
    cache_service.set("del-key", "v", ttl_seconds=60)
    cache_service.delete("del-key")
    assert cache_service.get("del-key") is None


def test_delete_noop_on_nonexistent_key(fakeredis_client):
    # Must not raise.
    cache_service.delete("never-existed")


def test_delete_fails_open_on_exception(raising_redis_client):
    cache_service.delete("k")  # must not raise


def test_delete_pattern_deletes_only_matching_keys(fakeredis_client):
    cache_service.set("sh:1:lb:rank:482:daily", 1, ttl_seconds=60)
    cache_service.set("sh:1:lb:rank:482:weekly", 2, ttl_seconds=60)
    cache_service.set("sh:1:lb:rank:117:daily", 3, ttl_seconds=60)  # different user, must survive

    cache_service.delete_pattern("sh:1:lb:rank:482:*")

    assert cache_service.get("sh:1:lb:rank:482:daily") is None
    assert cache_service.get("sh:1:lb:rank:482:weekly") is None
    assert cache_service.get("sh:1:lb:rank:117:daily") == 3


def test_delete_pattern_fails_open_on_exception(raising_redis_client):
    cache_service.delete_pattern("anything:*")  # must not raise


# ============================================================================
# @cached decorator
# ============================================================================

def test_cached_decorator_cache_miss_then_hit(fakeredis_client):
    call_count = {"n": 0}

    @cache_service.cached("test:{x}", ttl_seconds=60)
    def compute(x):
        call_count["n"] += 1
        return x * 2

    assert compute(5) == 10
    assert call_count["n"] == 1

    assert compute(5) == 10  # cache hit
    assert call_count["n"] == 1  # NOT re-executed


def test_cached_decorator_missing_placeholder_falls_back_uncached(fakeredis_client):
    call_count = {"n": 0}

    @cache_service.cached("test:{x}:{y}", ttl_seconds=60)
    def compute(x):
        call_count["n"] += 1
        return x

    # Called with fewer args than the template expects -- must not raise,
    # falls back to calling the function directly without caching.
    result = compute(5)
    assert result == 5
    assert call_count["n"] == 1


def test_cached_decorator_none_result_not_cached(fakeredis_client):
    call_count = {"n": 0}

    @cache_service.cached("test:none:{x}", ttl_seconds=60)
    def compute(x):
        call_count["n"] += 1
        return None

    compute(1)
    compute(1)
    compute(1)
    assert call_count["n"] == 3  # re-executed every time, never cached


def test_cached_decorator_positional_and_keyword_args_produce_same_key(fakeredis_client):
    call_count = {"n": 0}

    @cache_service.cached("test:pk:{a}:{b}", ttl_seconds=60)
    def compute(a, b):
        call_count["n"] += 1
        return a + b

    assert compute(1, 2) == 3
    assert call_count["n"] == 1

    assert compute(a=1, b=2) == 3  # same logical call, different calling convention
    assert call_count["n"] == 1  # must hit the same cache entry, not re-execute


def test_cached_decorator_no_placeholders_fixed_key(fakeredis_client):
    call_count = {"n": 0}

    @cache_service.cached("fixed:key", ttl_seconds=60)
    def compute():
        call_count["n"] += 1
        return "result"

    compute()
    compute()
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# incr() and versioned keys
# ---------------------------------------------------------------------------

def test_incr_creates_and_increments_counter(fakeredis_client):
    assert cache_service.incr("sh:1:ver:7") == 1
    assert cache_service.incr("sh:1:ver:7") == 2
    assert cache_service.get("sh:1:ver:7") == 2


def test_incr_applies_ttl(fakeredis_client):
    cache_service.incr("sh:1:ver:8", ttl_seconds=100)
    assert 0 < fakeredis_client.ttl("sh:1:ver:8") <= 100


def test_incr_returns_none_when_redis_fails():
    from unittest.mock import patch
    with patch.object(cache_service, "redis_client") as rc:
        rc.incr.side_effect = ConnectionError("down")
        assert cache_service.incr("sh:1:ver:9") is None


def test_versioned_cache_is_invalidated_by_bumping_the_version(fakeredis_client):
    calls = []

    @cache_service.cached("sh:1:t:{uid}", ttl_seconds=60, version_key="sh:1:ver:{uid}")
    def compute(uid):
        calls.append(uid)
        return {"n": len(calls)}

    assert compute(5) == {"n": 1}
    assert compute(5) == {"n": 1}          # hit
    cache_service.incr("sh:1:ver:5")       # invalidate user 5 only
    assert compute(5) == {"n": 2}          # recomputed under the new version
    assert compute(6) == {"n": 3}          # other users keep their own versions
    assert compute(6) == {"n": 3}


# ---------------------------------------------------------------------------
# single_flight (stampede protection)
# ---------------------------------------------------------------------------

def test_single_flight_computes_once_for_concurrent_misses(fakeredis_client):
    import threading
    import time
    calls = []

    @cache_service.cached("sh:1:sf:{x}", ttl_seconds=60, single_flight=True)
    def slow(x):
        calls.append(x)
        time.sleep(0.3)
        return {"x": x}

    results = []
    threads = [threading.Thread(target=lambda: results.append(slow(1))) for _ in range(8)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]

    assert len(calls) == 1
    assert results == [{"x": 1}] * 8


def test_single_flight_releases_its_lock_when_the_computation_raises(fakeredis_client):
    @cache_service.cached("sh:1:sf2:{x}", ttl_seconds=60, single_flight=True)
    def boom(x):
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        boom(1)
    assert fakeredis_client.get("sh:1:sf2:1:computing") is None


def test_single_flight_waiter_computes_itself_if_the_holder_never_finishes(fakeredis_client, monkeypatch):
    monkeypatch.setattr(cache_service, "_SINGLE_FLIGHT_WAIT_SECONDS", 0.2)
    fakeredis_client.set("sh:1:sf3:1:computing", "1")            # a stuck/crashed holder

    @cache_service.cached("sh:1:sf3:{x}", ttl_seconds=60, single_flight=True)
    def compute(x):
        return {"x": x}

    assert compute(1) == {"x": 1}                                 # served, not blocked
    assert fakeredis_client.get("sh:1:sf3:1:computing") == "1"    # their lock is left alone


def test_single_flight_fails_open_when_redis_is_down(raising_redis_client):
    @cache_service.cached("sh:1:sf4:{x}", ttl_seconds=60, single_flight=True)
    def compute(x):
        return {"x": x}

    assert compute(1) == {"x": 1}
