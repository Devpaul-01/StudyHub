"""
Tests for services/presence_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.8. The lazy-prune-on-read design
is the module's central documented mechanism -- worth a direct test
with fakeredis rather than trusting it by inspection.
"""

from services import presence_service as ps


# ============================================================================
# register_connection / remove_connection / is_user_online
# ============================================================================

def test_is_user_online_no_registered_sockets(fakeredis_client):
    assert ps.is_user_online(1) is False


def test_register_then_online(fakeredis_client):
    ps.register_connection(1, "sid-a", "instance-1")
    assert ps.is_user_online(1) is True


def test_remove_connection_makes_offline(fakeredis_client):
    ps.register_connection(1, "sid-a", "instance-1")
    ps.remove_connection(1, "sid-a")
    assert ps.is_user_online(1) is False


def test_is_user_online_prunes_stale_sid_from_index(fakeredis_client):
    """The sock: key expired/was never set (simulated here by deleting it
    directly, leaving the index Set stale) -- is_user_online must both
    report offline AND prune the stale sid from the index set as a
    side effect."""
    ps.register_connection(1, "sid-a", "instance-1")
    # Simulate the sock: TTL key expiring while the index set entry lingers.
    fakeredis_client.delete(ps._sock_key("sid-a"))

    assert ps.is_user_online(1) is False
    assert "sid-a" not in fakeredis_client.smembers(ps._user_sockets_key(1))


def test_is_user_online_multiple_sockets_some_stale_some_live(fakeredis_client):
    ps.register_connection(1, "sid-live", "instance-1")
    ps.register_connection(1, "sid-stale", "instance-1")
    fakeredis_client.delete(ps._sock_key("sid-stale"))

    assert ps.is_user_online(1) is True  # live one wins
    remaining = fakeredis_client.smembers(ps._user_sockets_key(1))
    assert "sid-stale" not in remaining
    assert "sid-live" in remaining


def test_register_and_remove_fail_open_on_redis_error(raising_redis_client):
    # Must not raise.
    ps.register_connection(1, "sid-a", "instance-1")
    ps.remove_connection(1, "sid-a")


def test_is_user_online_fails_open_on_redis_error(raising_redis_client):
    assert ps.is_user_online(1) is False


# ============================================================================
# get_online_user_ids (batch)
# ============================================================================

def test_get_online_user_ids_mixed(fakeredis_client):
    ps.register_connection(1, "sid-1", "i")
    ps.register_connection(2, "sid-2", "i")
    fakeredis_client.delete(ps._sock_key("sid-2"))  # user 2's socket goes stale

    online = ps.get_online_user_ids([1, 2, 3])
    assert online == {1}


def test_get_online_user_ids_empty_list_no_redis_call(fakeredis_client):
    assert ps.get_online_user_ids([]) == set()


def test_get_online_user_ids_fails_open_to_empty_set(raising_redis_client):
    assert ps.get_online_user_ids([1, 2]) == set()


# ============================================================================
# active-thread tracking
# ============================================================================

def test_set_and_get_active_thread(fakeredis_client):
    ps.set_active_thread(5, 100)
    assert ps.get_active_thread(5) == 100


def test_get_active_thread_none_when_unset(fakeredis_client):
    assert ps.get_active_thread(5) is None


def test_clear_active_thread_no_expected_id(fakeredis_client):
    ps.set_active_thread(5, 100)
    ps.clear_active_thread(5)
    assert ps.get_active_thread(5) is None


def test_clear_active_thread_matching_expected_id_clears(fakeredis_client):
    ps.set_active_thread(5, 100)
    ps.clear_active_thread(5, expected_thread_id=100)
    assert ps.get_active_thread(5) is None


def test_clear_active_thread_mismatched_expected_id_does_not_clear(fakeredis_client):
    """A disconnect on a stale/background tab must not clobber a
    still-active foreground tab's newer active-thread value."""
    ps.set_active_thread(5, 200)  # newer value already in place
    ps.clear_active_thread(5, expected_thread_id=100)  # stale tab's old value
    assert ps.get_active_thread(5) == 200


def test_get_active_threads_batch_only_includes_users_with_a_value(fakeredis_client):
    ps.set_active_thread(1, 10)
    ps.set_active_thread(2, 20)
    # user 3 has no active thread set

    result = ps.get_active_threads_batch([1, 2, 3])

    assert result == {1: 10, 2: 20}
    assert 3 not in result  # absent, not present-with-None


def test_get_active_threads_batch_empty_list():
    assert ps.get_active_threads_batch([]) == {}


def test_active_thread_functions_fail_open_on_redis_error(raising_redis_client):
    ps.set_active_thread(5, 100)  # must not raise
    assert ps.get_active_thread(5) is None
    ps.clear_active_thread(5)  # must not raise
    assert ps.get_active_threads_batch([1, 2]) == {}
