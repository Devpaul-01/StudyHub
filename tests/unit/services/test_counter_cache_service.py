"""
Tests for services/counter_cache_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.7. The self-healing reseed
pattern is the module's central design -- any form of cache
unreliability (missing, corrupt, Redis-down) must converge on the same
safe behavior: fall back to the real count via recompute_fn.
"""

from unittest.mock import Mock

from services import counter_cache_service


def test_get_counter_hit_does_not_call_recompute(fakeredis_client):
    fakeredis_client.set("sh:1:notif:unread:1", "5", ex=3600)
    recompute = Mock(return_value=999)

    result = counter_cache_service.get_unread_notification_count(1, recompute)

    assert result == 5
    recompute.assert_not_called()


def test_get_counter_miss_calls_recompute_and_reseeds(fakeredis_client):
    recompute = Mock(return_value=7)
    result = counter_cache_service.get_unread_notification_count(1, recompute)

    assert result == 7
    recompute.assert_called_once()
    assert fakeredis_client.get("sh:1:notif:unread:1") == "7"


def test_get_counter_corrupt_value_treated_as_miss(fakeredis_client):
    fakeredis_client.set("sh:1:notif:unread:1", "not-an-integer", ex=3600)
    recompute = Mock(return_value=3)

    result = counter_cache_service.get_unread_notification_count(1, recompute)

    assert result == 3
    recompute.assert_called_once()


def test_get_counter_redis_error_treated_as_miss(raising_redis_client):
    recompute = Mock(return_value=42)
    result = counter_cache_service.get_unread_notification_count(1, recompute)
    assert result == 42
    recompute.assert_called_once()


def test_increment_creates_key_with_ttl(fakeredis_client):
    counter_cache_service.increment_unread_notification_count(1)
    assert fakeredis_client.get("sh:1:notif:unread:1") == "1"
    ttl = fakeredis_client.ttl("sh:1:notif:unread:1")
    assert ttl > 0


def test_increment_by_custom_amount(fakeredis_client):
    counter_cache_service.increment_unread_message_count(1, by=5)
    assert fakeredis_client.get("sh:1:msg:unread:1") == "5"


def test_decrement_allows_negative_by_design(fakeredis_client):
    """Documented, deliberate behavior: a decrement below zero is NOT
    clamped -- it self-corrects at the next reseed. This test locks in
    that this is intentional, not a bug to silently fix."""
    counter_cache_service.decrement_unread_notification_count(1, by=3)
    assert fakeredis_client.get("sh:1:notif:unread:1") == "-3"


def test_increment_fails_open_on_redis_error(raising_redis_client):
    # Must not raise.
    counter_cache_service.increment_unread_notification_count(1)


def test_decrement_fails_open_on_redis_error(raising_redis_client):
    counter_cache_service.decrement_unread_notification_count(1)  # must not raise


def test_unread_message_count_uses_separate_key_namespace(fakeredis_client):
    recompute_notif = Mock(return_value=1)
    recompute_msg = Mock(return_value=2)

    counter_cache_service.get_unread_notification_count(5, recompute_notif)
    counter_cache_service.get_unread_message_count(5, recompute_msg)

    assert fakeredis_client.get("sh:1:notif:unread:5") == "1"
    assert fakeredis_client.get("sh:1:msg:unread:5") == "2"
