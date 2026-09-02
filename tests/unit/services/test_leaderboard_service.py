"""
Tests for services/leaderboard_service.py -- pure/period-scoring helpers
and take_snapshot's idempotency guard only, per
UNIT_TEST_IMPLEMENTATION_PLAN.md §8.1's own explicit scoping: the bulk
of this module is query-building against multiple tables with
viewer-specific caching layered on top, judged closer to integration-test
territory. get_global_leaderboard, get_nearby_users, get_my_rank,
get_connections_leaderboard, get_rising_stars are deliberately NOT
tested here -- see §8.1's own "explicitly not tested here" list.
"""

import datetime

import pytest
import freezegun

from services import leaderboard_service
from models import LeaderboardSnapshot


# ============================================================================
# validate_period
# ============================================================================

@pytest.mark.parametrize("period", ["daily", "weekly", "monthly", "all_time"])
def test_validate_period_valid_values(period):
    result_period, error = leaderboard_service.validate_period(period)
    assert result_period == period
    assert error is None


def test_validate_period_invalid_lists_valid_options():
    period, error = leaderboard_service.validate_period("yearly")
    assert period == "yearly"
    assert "Invalid period" in error
    for valid in ("daily", "weekly", "monthly", "all_time"):
        assert valid in error


# ============================================================================
# _period_start
# ============================================================================

@freezegun.freeze_time("2026-01-15 12:00:00")
def test_period_start_daily():
    result = leaderboard_service._period_start("daily")
    assert result == datetime.datetime(2026, 1, 14, 12, 0, 0)


@freezegun.freeze_time("2026-01-15 12:00:00")
def test_period_start_weekly():
    result = leaderboard_service._period_start("weekly")
    assert result == datetime.datetime(2026, 1, 8, 12, 0, 0)


@freezegun.freeze_time("2026-01-15 12:00:00")
def test_period_start_monthly():
    result = leaderboard_service._period_start("monthly")
    assert result == datetime.datetime(2025, 12, 16, 12, 0, 0)


def test_period_start_all_time_is_none():
    assert leaderboard_service._period_start("all_time") is None


# ============================================================================
# _dept_key
# ============================================================================

def test_dept_key_none_becomes_underscore():
    assert leaderboard_service._dept_key(None) == "_"


def test_dept_key_empty_string_becomes_underscore():
    assert leaderboard_service._dept_key("") == "_"


def test_dept_key_real_department_unchanged():
    assert leaderboard_service._dept_key("Computer Science") == "Computer Science"


# ============================================================================
# _build_entry
# ============================================================================

def test_build_entry_is_you_true_for_matching_id(db_session, make_user):
    user = make_user(reputation=100)
    entry = leaderboard_service._build_entry(1, user, None, 100, user.id, {})
    assert entry["is_you"] is True


def test_build_entry_is_you_false_for_different_id(db_session, make_user):
    user = make_user(reputation=100)
    other_id = user.id + 1
    entry = leaderboard_service._build_entry(1, user, None, 100, other_id, {})
    assert entry["is_you"] is False


def test_build_entry_connection_status_from_conn_map(db_session, make_user):
    user = make_user(reputation=100)
    entry = leaderboard_service._build_entry(1, user, None, 100, 999, {user.id: "accepted"})
    assert entry["connection_status"] == "accepted"


def test_build_entry_connection_status_none_when_absent(db_session, make_user):
    user = make_user(reputation=100)
    entry = leaderboard_service._build_entry(1, user, None, 100, 999, {})
    assert entry["connection_status"] is None


def test_build_entry_rank_change_passed_through(db_session, make_user):
    user = make_user(reputation=100)
    entry = leaderboard_service._build_entry(1, user, None, 100, 999, {}, rank_change=5)
    assert entry["rank_change"] == 5
    entry_none = leaderboard_service._build_entry(1, user, None, 100, 999, {}, rank_change=None)
    assert entry_none["rank_change"] is None


def test_build_entry_full_dict_shape(db_session, make_user, make_student_profile):
    user = make_user(reputation=100, login_streak=3)
    profile = make_student_profile(user, department="CS", class_name="2026")
    entry = leaderboard_service._build_entry(1, user, profile, 100, user.id, {})

    assert set(entry.keys()) == {
        "rank", "rank_change", "connection_status", "is_you", "user",
        "score", "reputation", "streaks", "stats",
    }
    assert entry["user"]["department"] == "CS"
    assert entry["user"]["class_level"] == "2026"


def test_build_entry_no_profile_department_none(db_session, make_user):
    user = make_user(reputation=100)
    entry = leaderboard_service._build_entry(1, user, None, 100, user.id, {})
    assert entry["user"]["department"] is None
    assert entry["user"]["class_level"] is None


# ============================================================================
# take_snapshot — idempotency guard
# ============================================================================

def test_take_snapshot_first_call_creates_rows(db_session, make_user, fakeredis_client):
    u1 = make_user(reputation=500, status="approved")
    u2 = make_user(reputation=300, status="approved")
    u3 = make_user(reputation=100, status="approved")

    result = leaderboard_service.take_snapshot("weekly")

    assert result["created"] == 3
    assert result["skipped"] == 0
    assert LeaderboardSnapshot.query.count() == 3

    top_snap = LeaderboardSnapshot.query.filter_by(user_id=u1.id).first()
    assert top_snap.global_rank == 1
    bottom_snap = LeaderboardSnapshot.query.filter_by(user_id=u3.id).first()
    assert bottom_snap.global_rank == 3


def test_take_snapshot_second_call_same_day_is_noop(db_session, make_user, fakeredis_client):
    make_user(reputation=500, status="approved")

    first = leaderboard_service.take_snapshot("weekly")
    second = leaderboard_service.take_snapshot("weekly")

    assert first["created"] == 1
    assert second == {"created": 0, "skipped": 0, "total_ranked": 0}
    assert LeaderboardSnapshot.query.count() == 1  # not duplicated


def test_take_snapshot_department_rank_excludes_users_without_department(
    db_session, make_user, make_student_profile, fakeredis_client
):
    with_dept_high = make_user(reputation=500, status="approved")
    make_student_profile(with_dept_high, department="CS")
    with_dept_low = make_user(reputation=200, status="approved")
    make_student_profile(with_dept_low, department="CS")
    no_dept = make_user(reputation=999, status="approved")  # highest rep, but no department

    leaderboard_service.take_snapshot("weekly")

    no_dept_snap = LeaderboardSnapshot.query.filter_by(user_id=no_dept.id).first()
    assert no_dept_snap.global_rank == 1  # still globally ranked
    assert no_dept_snap.department_rank is None  # but excluded from department ranking

    high_snap = LeaderboardSnapshot.query.filter_by(user_id=with_dept_high.id).first()
    assert high_snap.department_rank == 1
    low_snap = LeaderboardSnapshot.query.filter_by(user_id=with_dept_low.id).first()
    assert low_snap.department_rank == 2
