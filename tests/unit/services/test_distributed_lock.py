"""
Tests for services/distributed_lock.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.9. This is the one Redis-backed
module in the codebase that deliberately fails CLOSED instead of open --
a regression that made it fail open would silently reintroduce the
duplicate-job-execution bug this module exists to prevent.
"""

from services.distributed_lock import DistributedLock


def test_acquire_succeeds_when_key_not_held(fakeredis_client):
    lock = DistributedLock("sh:1:test:lock:a", ttl_seconds=60)
    assert lock.acquire() is True
    assert lock.acquired is True
    assert fakeredis_client.get("sh:1:test:lock:a") == lock.owner_token


def test_acquire_fails_when_already_held_by_another_owner(fakeredis_client):
    lock_a = DistributedLock("sh:1:test:lock:b", ttl_seconds=60)
    lock_a.acquire()

    lock_b = DistributedLock("sh:1:test:lock:b", ttl_seconds=60)
    assert lock_b.acquire() is False
    assert lock_b.acquired is False


def test_acquire_fails_closed_on_redis_error(raising_redis_client):
    """The single highest-priority assertion in this section: on ANY
    Redis error, acquire() must return False -- never True, and never
    'proceed anyway' the way every other Redis consumer in this codebase
    fails open."""
    lock = DistributedLock("sh:1:test:lock:c", ttl_seconds=60)
    result = lock.acquire()
    assert result is False
    assert lock.acquired is False


def test_release_deletes_when_still_owner(fakeredis_client):
    lock = DistributedLock("sh:1:test:lock:d", ttl_seconds=60)
    lock.acquire()
    released = lock.release()
    assert released is True
    assert fakeredis_client.get("sh:1:test:lock:d") is None


def test_release_does_not_delete_when_no_longer_owner(fakeredis_client):
    """Core correctness property: if the lock's TTL expired and another
    instance re-acquired it, the original (now-late) owner's release()
    must NOT delete the new owner's lock."""
    lock_a = DistributedLock("sh:1:test:lock:e", ttl_seconds=60)
    lock_a.acquire()

    # Simulate TTL expiry + re-acquisition by a different instance.
    fakeredis_client.set("sh:1:test:lock:e", "someone-elses-token", ex=60)

    released = lock_a.release()
    assert released is False
    assert fakeredis_client.get("sh:1:test:lock:e") == "someone-elses-token"


def test_release_noop_when_never_acquired(fakeredis_client):
    lock = DistributedLock("sh:1:test:lock:f", ttl_seconds=60)
    # acquire() never called.
    assert lock.release() is False


def test_release_fails_gracefully_on_redis_error(raising_redis_client):
    lock = DistributedLock("sh:1:test:lock:g", ttl_seconds=60)
    lock.acquired = True  # simulate a prior successful acquire
    lock.owner_token = "x"
    result = lock.release()
    assert result is False  # must not raise


def test_context_manager_normal_use(fakeredis_client):
    with DistributedLock("sh:1:test:lock:h", ttl_seconds=60) as lock:
        assert lock.acquired is True
    assert fakeredis_client.get("sh:1:test:lock:h") is None  # released on exit


def test_context_manager_releases_on_exception(fakeredis_client):
    try:
        with DistributedLock("sh:1:test:lock:i", ttl_seconds=60) as lock:
            assert lock.acquired is True
            raise ValueError("boom")
    except ValueError:
        pass
    assert fakeredis_client.get("sh:1:test:lock:i") is None  # still released
