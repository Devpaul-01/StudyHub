"""
Tests for services/online_status_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.8. The batch vs single-user
consistency test is the key one -- the whole point of the batch version
is "one query instead of N," and a regression that computed something
subtly different would be a silent correctness bug hidden behind a
performance optimization.
"""

import datetime

from services import online_status_service as oss


class _FakeUser:
    def __init__(self, in_study_session=False, last_active=None):
        self.in_study_session = in_study_session
        self.last_active = last_active


# ============================================================================
# is_user_online (pure predicate)
# ============================================================================

def test_is_user_online_study_session_always_true_even_stale():
    user = _FakeUser(in_study_session=True, last_active=None)
    assert oss.is_user_online(user) is True

    stale_user = _FakeUser(in_study_session=True, last_active=datetime.datetime(2020, 1, 1))
    assert oss.is_user_online(stale_user) is True


def test_is_user_online_within_window_true():
    recent = datetime.datetime.utcnow() - datetime.timedelta(minutes=10)
    user = _FakeUser(last_active=recent)
    assert oss.is_user_online(user) is True


def test_is_user_online_exactly_at_window_boundary_false():
    """Boundary is strict `<`, not `<=` -- exactly window_minutes ago is
    NOT online."""
    exactly_at_window = datetime.datetime.utcnow() - datetime.timedelta(minutes=30)
    user = _FakeUser(last_active=exactly_at_window)
    assert oss.is_user_online(user, window_minutes=30) is False


def test_is_user_online_no_last_active_false():
    user = _FakeUser(last_active=None)
    assert oss.is_user_online(user) is False


def test_is_user_online_none_user_false_no_exception():
    assert oss.is_user_online(None) is False


def test_is_user_online_custom_window_actually_changes_boundary():
    fifteen_min_ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=15)
    user = _FakeUser(last_active=fifteen_min_ago)
    assert oss.is_user_online(user, window_minutes=10) is False
    assert oss.is_user_online(user, window_minutes=20) is True


# ============================================================================
# _format_last_active
# ============================================================================

def test_format_last_active_minutes():
    assert oss._format_last_active(45) == "45m"


def test_format_last_active_boundary_60_switches_to_hours():
    assert oss._format_last_active(59) == "59m"
    assert oss._format_last_active(60) == "1h"


def test_format_last_active_hours():
    assert oss._format_last_active(120) == "2h"


def test_format_last_active_boundary_1440_switches_to_days():
    assert oss._format_last_active(1439) == "23h"
    assert oss._format_last_active(1440) == "1d"


def test_format_last_active_days():
    assert oss._format_last_active(2880) == "2d"


# ============================================================================
# get_user_online_status (DB-backed)
# ============================================================================

def test_get_user_online_status_never_active(db_session, make_user):
    user = make_user(last_active=None)
    status = oss.get_user_online_status(user.id)
    assert status == {"is_online": False, "in_study_session": False, "last_active": "Never"}


def test_get_user_online_status_in_study_session(db_session, make_user):
    user = make_user(in_study_session=True, last_active=datetime.datetime.utcnow())
    status = oss.get_user_online_status(user.id)
    assert status == {"is_online": True, "in_study_session": True, "last_active": None}


def test_get_user_online_status_recently_active(db_session, make_user):
    user = make_user(last_active=datetime.datetime.utcnow() - datetime.timedelta(minutes=5))
    status = oss.get_user_online_status(user.id)
    assert status["is_online"] is True
    assert status["last_active"] is None


def test_get_user_online_status_stale_formats_last_active(db_session, make_user):
    user = make_user(last_active=datetime.datetime.utcnow() - datetime.timedelta(hours=2))
    status = oss.get_user_online_status(user.id)
    assert status["is_online"] is False
    assert status["last_active"] == "2h"


def test_get_user_online_status_nonexistent_user(db_session):
    status = oss.get_user_online_status(999999)
    assert status == {"is_online": False, "in_study_session": False, "last_active": "Never"}


# ============================================================================
# get_online_status_batch — must match single-user results exactly
# ============================================================================

def test_get_online_status_batch_matches_single_user_calls(db_session, make_user):
    study_session_user = make_user(in_study_session=True, last_active=datetime.datetime.utcnow())
    recently_active = make_user(last_active=datetime.datetime.utcnow() - datetime.timedelta(minutes=5))
    stale_user = make_user(last_active=datetime.datetime.utcnow() - datetime.timedelta(hours=3))
    never_active = make_user(last_active=None)

    ids = [study_session_user.id, recently_active.id, stale_user.id, never_active.id]
    batch_results = oss.get_online_status_batch(ids)

    for uid in ids:
        assert batch_results[uid] == oss.get_user_online_status(uid)


def test_get_online_status_batch_empty_list():
    assert oss.get_online_status_batch([]) == {}
