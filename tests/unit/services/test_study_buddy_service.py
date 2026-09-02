"""
Tests for services/study_buddy_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.5. Cap enforcement per
component, same style as connection_service.calculate_compatibility_score.
"""

import datetime

from services import study_buddy_service


class _FakeUser:
    def __init__(self, last_active=None):
        self.last_active = last_active


def test_match_score_all_components_zero():
    u1, u2 = _FakeUser(), _FakeUser()
    score = study_buddy_service.calculate_match_score(u1, u2, None, None)
    assert score == 0


def test_match_score_subject_overlap_capped_at_40():
    u1, u2 = _FakeUser(), _FakeUser()
    prefs1 = {"needs_help": ["math", "physics", "chem", "bio", "cs"], "good_at": []}
    prefs2 = {"good_at": ["math", "physics", "chem", "bio", "cs"], "needs_help": []}
    # 5 overlaps * 10 = 50, capped at 40
    score = study_buddy_service.calculate_match_score(u1, u2, prefs1, prefs2)
    assert score == 40


def test_match_score_availability_capped_at_30():
    u1, u2 = _FakeUser(), _FakeUser()
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    prefs1 = {"available_days": days}
    prefs2 = {"available_days": days}
    # 7 overlapping days * 5 = 35, capped at 30
    score = study_buddy_service.calculate_match_score(u1, u2, prefs1, prefs2)
    assert score == 30


def test_match_score_department_match_flat_10_uses_preloaded_profiles():
    u1, u2 = _FakeUser(), _FakeUser()

    class _Profile:
        def __init__(self, department):
            self.department = department

    score = study_buddy_service.calculate_match_score(
        u1, u2, None, None, profile1=_Profile("CS"), profile2=_Profile("CS")
    )
    assert score == 10


def test_match_score_department_mismatch_no_points():
    u1, u2 = _FakeUser(), _FakeUser()

    class _Profile:
        def __init__(self, department):
            self.department = department

    score = study_buddy_service.calculate_match_score(
        u1, u2, None, None, profile1=_Profile("CS"), profile2=_Profile("Math")
    )
    assert score == 0


def test_match_score_activity_level_both_active():
    now = datetime.datetime.utcnow()
    u1 = _FakeUser(last_active=now)
    u2 = _FakeUser(last_active=now)
    score = study_buddy_service.calculate_match_score(u1, u2, None, None)
    assert score == 10  # 5 + 5


def test_match_score_activity_level_stale_no_points():
    old = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    u1 = _FakeUser(last_active=old)
    u2 = _FakeUser(last_active=old)
    score = study_buddy_service.calculate_match_score(u1, u2, None, None)
    assert score == 0


def test_match_score_success_rate_capped_at_10():
    u1, u2 = _FakeUser(), _FakeUser()
    # 10 successes * 2 = 20, capped at 10
    score = study_buddy_service.calculate_match_score(
        u1, u2, None, None, user2_success_count=10
    )
    assert score == 10


def test_match_score_total_never_exceeds_100():
    now = datetime.datetime.utcnow()
    u1 = _FakeUser(last_active=now)
    u2 = _FakeUser(last_active=now)

    class _Profile:
        department = "CS"

    prefs1 = {"needs_help": ["a", "b", "c", "d", "e"], "good_at": [], "available_days": ["mon"] * 1 + list("abcdefg")}
    prefs2 = {"good_at": ["a", "b", "c", "d", "e"], "needs_help": [], "available_days": list("abcdefg")}

    score = study_buddy_service.calculate_match_score(
        u1, u2, prefs1, prefs2, profile1=_Profile(), profile2=_Profile(), user2_success_count=99
    )
    assert score <= 100
