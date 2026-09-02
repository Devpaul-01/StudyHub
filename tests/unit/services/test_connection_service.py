"""
Tests for services/connection_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.2. block_connection's "does not
swap requester_id/receiver_id" assertion is the regression test for a
documented historical bug ("previously corrupted the original
connection-request history") -- treated with P0-level rigor per the
plan's own explicit note, despite living in a P1 module.
"""

from services import connection_service
from models import Connection


# ============================================================================
# calculate_compatibility_score — cap enforcement per component
# ============================================================================

def test_compatibility_score_all_zero():
    data = {
        "shared_subjects": [],
        "complementary_skills": {"they_can_help_with": [], "you_can_help_with": []},
        "schedule_overlap": 0,
        "department_match": False,
    }
    assert connection_service.calculate_compatibility_score(data) == 0


def test_compatibility_score_shared_subjects_capped_at_30():
    data = {
        "shared_subjects": ["a", "b", "c", "d", "e", "f"],  # 6 * 10 = 60, capped at 30
        "complementary_skills": {"they_can_help_with": [], "you_can_help_with": []},
        "schedule_overlap": 0,
        "department_match": False,
    }
    assert connection_service.calculate_compatibility_score(data) == 30


def test_compatibility_score_they_can_help_capped_at_40():
    data = {
        "shared_subjects": [],
        "complementary_skills": {"they_can_help_with": ["a", "b", "c"], "you_can_help_with": []},  # 3*20=60 -> 40
        "schedule_overlap": 0,
        "department_match": False,
    }
    assert connection_service.calculate_compatibility_score(data) == 40


def test_compatibility_score_schedule_overlap_capped_at_20():
    data = {
        "shared_subjects": [],
        "complementary_skills": {"they_can_help_with": [], "you_can_help_with": []},
        "schedule_overlap": 100,  # 0.2 * 100 = 20
        "department_match": False,
    }
    assert connection_service.calculate_compatibility_score(data) == 20


def test_compatibility_score_department_match_flat_10():
    data = {
        "shared_subjects": [],
        "complementary_skills": {"they_can_help_with": [], "you_can_help_with": []},
        "schedule_overlap": 0,
        "department_match": True,
    }
    assert connection_service.calculate_compatibility_score(data) == 10


def test_compatibility_score_all_maxed_equals_exactly_100():
    data = {
        "shared_subjects": ["a", "b", "c", "d"],
        "complementary_skills": {"they_can_help_with": ["a", "b", "c"], "you_can_help_with": []},
        "schedule_overlap": 100,
        "department_match": True,
    }
    assert connection_service.calculate_compatibility_score(data) == 100


# ============================================================================
# calculate_schedule_overlap
# ============================================================================

def test_schedule_overlap_identical_all_days_all_slots():
    schedule = {
        "Monday": ["morning", "afternoon", "evening"],
        "Tuesday": ["morning", "afternoon", "evening"],
        "Wednesday": ["morning", "afternoon", "evening"],
        "Thursday": ["morning", "afternoon", "evening"],
        "Friday": ["morning", "afternoon", "evening"],
        "Saturday": ["morning", "afternoon", "evening"],
        "Sunday": ["morning", "afternoon", "evening"],
    }
    assert connection_service.calculate_schedule_overlap(schedule, schedule) == 100


def test_schedule_overlap_no_overlap():
    s1 = {"Monday": ["morning"]}
    s2 = {"Monday": ["evening"]}
    assert connection_service.calculate_schedule_overlap(s1, s2) == 0


def test_schedule_overlap_missing_day_counts_as_zero_but_still_totals_slots():
    s1 = {"Monday": ["morning", "afternoon", "evening"]}
    s2 = {"Tuesday": ["morning", "afternoon", "evening"]}  # no shared days at all
    assert connection_service.calculate_schedule_overlap(s1, s2) == 0


def test_schedule_overlap_none_schedule_returns_zero_no_exception():
    assert connection_service.calculate_schedule_overlap(None, {"Monday": ["morning"]}) == 0
    assert connection_service.calculate_schedule_overlap({"Monday": ["morning"]}, None) == 0
    assert connection_service.calculate_schedule_overlap(None, None) == 0


def test_schedule_overlap_wrong_casing_treated_as_non_overlapping():
    """Documented contract: day keys are matched case-sensitively; a
    lowercase 'monday' does not match 'Monday' and is silently treated
    as non-overlapping, not an error."""
    s1 = {"monday": ["morning"]}  # wrong case
    s2 = {"Monday": ["morning"]}
    assert connection_service.calculate_schedule_overlap(s1, s2) == 0


# ============================================================================
# is_user_blocked
# ============================================================================

def test_is_user_blocked_no_connection(db_session, make_user):
    a, b = make_user(), make_user()
    assert connection_service.is_user_blocked(a.id, b.id) == (False, False)


def test_is_user_blocked_blocked_by_a(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    make_connection(a, b, status="blocked", blocked_by_id=a.id)
    assert connection_service.is_user_blocked(a.id, b.id) == (True, False)


def test_is_user_blocked_blocked_by_b(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    make_connection(a, b, status="blocked", blocked_by_id=b.id)
    assert connection_service.is_user_blocked(a.id, b.id) == (False, True)


def test_is_user_blocked_non_blocked_status_returns_false_false(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    make_connection(a, b, status="accepted")
    assert connection_service.is_user_blocked(a.id, b.id) == (False, False)


# ============================================================================
# block_connection / unblock_connection
# ============================================================================

def test_block_connection_no_prior_connection_creates_row(db_session, make_user):
    blocker, blocked = make_user(), make_user()
    conn = connection_service.block_connection(blocker.id, blocked.id)
    db_session.commit()

    assert conn.status == "blocked"
    assert conn.blocked_by_id == blocker.id


def test_block_connection_reuses_existing_row_without_swapping_requester_receiver(
    db_session, make_user, make_connection
):
    """Regression test for a documented historical bug: block_connection
    must NOT swap requester_id/receiver_id when reusing an existing
    connection row, even when the blocker is the original receiver."""
    original_requester, original_receiver = make_user(), make_user()
    conn = make_connection(original_requester, original_receiver, status="accepted")
    conn_id = conn.id

    # The RECEIVER (not the requester) is the one blocking here.
    connection_service.block_connection(original_receiver.id, original_requester.id)
    db_session.commit()

    refreshed = Connection.query.get(conn_id)
    assert refreshed.requester_id == original_requester.id  # unchanged
    assert refreshed.receiver_id == original_receiver.id    # unchanged
    assert refreshed.status == "blocked"
    assert refreshed.blocked_by_id == original_receiver.id


def test_unblock_connection_default_deletes_row(db_session, make_user, make_connection):
    blocker, blocked = make_user(), make_user()
    conn = make_connection(blocker, blocked, status="blocked", blocked_by_id=blocker.id)
    conn_id = conn.id

    success, error = connection_service.unblock_connection(blocker.id, blocked.id)
    db_session.commit()

    assert success is True
    assert error is None
    assert Connection.query.get(conn_id) is None


def test_unblock_connection_restore_to_accepted_keeps_row(db_session, make_user, make_connection):
    blocker, blocked = make_user(), make_user()
    conn = make_connection(blocker, blocked, status="blocked", blocked_by_id=blocker.id)
    conn_id = conn.id

    success, error = connection_service.unblock_connection(
        blocker.id, blocked.id, restore_to_accepted=True
    )
    db_session.commit()

    assert success is True
    refreshed = Connection.query.get(conn_id)
    assert refreshed is not None
    assert refreshed.status == "accepted"
    assert refreshed.blocked_by_id is None


def test_unblock_connection_wrong_user_not_authorized(db_session, make_user, make_connection):
    blocker, blocked = make_user(), make_user()
    make_connection(blocker, blocked, status="blocked", blocked_by_id=blocker.id)

    # `blocked` (who was blocked, not the one who blocked) tries to unblock.
    success, error = connection_service.unblock_connection(blocked.id, blocker.id)

    assert success is False
    assert error == "Not authorized"


def test_unblock_connection_not_blocked_returns_error(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    make_connection(a, b, status="accepted")

    success, error = connection_service.unblock_connection(a.id, b.id)

    assert success is False
    assert error == "User is not blocked"


# ============================================================================
# can_message
# ============================================================================

def test_can_message_self_always_false(db_session, make_user):
    user = make_user()
    assert connection_service.can_message(user.id, user.id) is False


def test_can_message_accepted_connection_true(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    make_connection(a, b, status="accepted")
    assert connection_service.can_message(a.id, b.id) is True


def test_can_message_pending_connection_false(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    make_connection(a, b, status="pending")
    assert connection_service.can_message(a.id, b.id) is False


def test_can_message_no_connection_false(db_session, make_user):
    a, b = make_user(), make_user()
    assert connection_service.can_message(a.id, b.id) is False


# ============================================================================
# get_mutual_connection_count — canonical (min,max) pair-ordering cache key
# ============================================================================

def test_get_mutual_connection_count_computes_correctly(db_session, make_user, make_connection, fakeredis_client):
    user1, user2 = make_user(), make_user()
    mutual1, mutual2 = make_user(), make_user()
    only_user1_friend = make_user()

    make_connection(user1, mutual1, status="accepted")
    make_connection(user2, mutual1, status="accepted")
    make_connection(user1, mutual2, status="accepted")
    make_connection(user2, mutual2, status="accepted")
    make_connection(user1, only_user1_friend, status="accepted")

    count = connection_service.get_mutual_connection_count(user1.id, user2.id)
    assert count == 2


def test_get_mutual_connection_count_same_cache_key_regardless_of_arg_order(
    db_session, make_user, make_connection, fakeredis_client
):
    """Canonical (min,max) pair ordering: calling with args reversed must
    hit the SAME cache entry, not create a second one for the same
    logical pair -- the specific bug this design avoids."""
    user1, user2 = make_user(), make_user()
    mutual = make_user()
    make_connection(user1, mutual, status="accepted")
    make_connection(user2, mutual, status="accepted")

    connection_service.get_mutual_connection_count(user1.id, user2.id)

    low_id, high_id = min(user1.id, user2.id), max(user1.id, user2.id)
    cache_key = f"sh:1:conn:mutual:{low_id}:{high_id}"
    assert fakeredis_client.get(cache_key) is not None

    # Reversed call must be a cache HIT against the same key -- delete all
    # underlying Connection rows first so a cache MISS would recompute to
    # zero and be trivially distinguishable from a real hit.
    Connection.query.delete()
    db_session.commit()

    count_reversed = connection_service.get_mutual_connection_count(user2.id, user1.id)
    assert count_reversed == 1  # served from cache, not recomputed as 0
