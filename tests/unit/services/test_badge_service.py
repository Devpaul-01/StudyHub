"""
Tests for services/badge_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.3. Boundary-value testing per
criterion (exact match / one below / well above), since several of
these are easy to get backwards (>= vs >) and would silently over- or
under-award a real achievement system.
"""

import datetime
from unittest.mock import patch

import pytest

from services import badge_service
from models import Badge, UserBadge


# ============================================================================
# _user_qualifies — one boundary sweep per criteria key
# ============================================================================

def test_qualifies_posts_count(db_session, make_user):
    exact = make_user(total_posts=5)
    below = make_user(total_posts=4)
    above = make_user(total_posts=50)
    criteria = {"posts_count": 5}
    assert badge_service._user_qualifies(exact, criteria) is True
    assert badge_service._user_qualifies(below, criteria) is False
    assert badge_service._user_qualifies(above, criteria) is True


def test_qualifies_helpful_count(db_session, make_user):
    exact = make_user(total_helpful=10)
    below = make_user(total_helpful=9)
    criteria = {"helpful_count": 10}
    assert badge_service._user_qualifies(exact, criteria) is True
    assert badge_service._user_qualifies(below, criteria) is False


def test_qualifies_solutions_count(db_session, make_user, make_post, make_comment):
    user = make_user()
    post = make_post(user)
    for _ in range(10):
        make_comment(post, user, is_solution=True)
    make_comment(post, user, is_solution=False)  # should not count

    assert badge_service._user_qualifies(user, {"solutions_count": 10}) is True
    assert badge_service._user_qualifies(user, {"solutions_count": 11}) is False


def test_qualifies_login_streak(db_session, make_user):
    exact = make_user(login_streak=7)
    below = make_user(login_streak=6)
    criteria = {"login_streak": 7}
    assert badge_service._user_qualifies(exact, criteria) is True
    assert badge_service._user_qualifies(below, criteria) is False


def test_qualifies_connections_count(db_session, make_user, make_connection):
    user = make_user()
    others = [make_user() for _ in range(10)]
    for other in others:
        make_connection(user, other, status="accepted")
    pending_other = make_user()
    make_connection(user, pending_other, status="pending")  # should not count

    assert badge_service._user_qualifies(user, {"connections_count": 10}) is True
    assert badge_service._user_qualifies(user, {"connections_count": 11}) is False


def test_qualifies_threads_created(db_session, make_user, make_thread):
    user = make_user()
    for _ in range(5):
        make_thread(user)
    assert badge_service._user_qualifies(user, {"threads_created": 5}) is True
    assert badge_service._user_qualifies(user, {"threads_created": 6}) is False


def test_qualifies_thread_leader(db_session, make_user, make_thread):
    user = make_user()
    make_thread(user, member_count=10)
    assert badge_service._user_qualifies(user, {"thread_leader": True}) is True

    user2 = make_user()
    make_thread(user2, member_count=9)
    assert badge_service._user_qualifies(user2, {"thread_leader": True}) is False


def test_qualifies_threads_large(db_session, make_user, make_thread):
    user = make_user()
    for _ in range(10):
        make_thread(user, member_count=10)
    assert badge_service._user_qualifies(user, {"threads_large": 10}) is True
    assert badge_service._user_qualifies(user, {"threads_large": 11}) is False


def test_qualifies_reputation(db_session, make_user):
    exact = make_user(reputation=1000)
    below = make_user(reputation=999)
    criteria = {"reputation": 1000}
    assert badge_service._user_qualifies(exact, criteria) is True
    assert badge_service._user_qualifies(below, criteria) is False


def test_qualifies_early_adopter(db_session, make_user):
    first = make_user(joined_at=datetime.datetime(2026, 1, 1))
    within_window = make_user(joined_at=datetime.datetime(2026, 1, 20))
    outside_window = make_user(joined_at=datetime.datetime(2026, 3, 1))

    assert badge_service._user_qualifies(first, {"early_adopter": True}) is True
    assert badge_service._user_qualifies(within_window, {"early_adopter": True}) is True
    assert badge_service._user_qualifies(outside_window, {"early_adopter": True}) is False


def test_qualifies_department_rank(db_session, make_user, make_student_profile):
    top = make_user(reputation=500, status="approved")
    make_student_profile(top, department="CS")
    second = make_user(reputation=300, status="approved")
    make_student_profile(second, department="CS")
    third = make_user(reputation=100, status="approved")
    make_student_profile(third, department="CS")

    # top: 0 users above it in dept -> rank 1
    assert badge_service._user_qualifies(top, {"department_rank": 3}) is True
    # third: 2 users above it -> rank 3
    assert badge_service._user_qualifies(third, {"department_rank": 3}) is True
    assert badge_service._user_qualifies(third, {"department_rank": 2}) is False


def test_qualifies_department_rank_no_profile_returns_false(db_session, make_user):
    user = make_user(reputation=500)
    assert badge_service._user_qualifies(user, {"department_rank": 3}) is False


def test_qualifies_unknown_criteria_key_returns_false(db_session, make_user):
    user = make_user()
    assert badge_service._user_qualifies(user, {"nonexistent_criterion": 1}) is False


# ============================================================================
# check_and_award_badge
# ============================================================================

def test_check_and_award_badge_awards_when_qualified(db_session, seeded_badges, make_user):
    user = make_user(total_posts=1)
    with patch("services.badge_service.notification_service.notify_badge_earned") as mock_notify, \
         patch("services.badge_service.cache_service.delete") as mock_delete:
        result = badge_service.check_and_award_badge(user.id, "First Post")

    assert result is not None
    assert isinstance(result, UserBadge)
    badge = Badge.query.filter_by(name="First Post").first()
    assert badge.awarded_count == 1
    mock_notify.assert_called_once()
    mock_delete.assert_called_once_with(f"sh:1:badge:progress:{user.id}")


def test_check_and_award_badge_idempotent_already_earned(db_session, seeded_badges, make_user):
    user = make_user(total_posts=1)
    with patch("services.badge_service.notification_service.notify_badge_earned"), \
         patch("services.badge_service.cache_service.delete"):
        first = badge_service.check_and_award_badge(user.id, "First Post")
        second = badge_service.check_and_award_badge(user.id, "First Post")

    assert first is not None
    assert second is None
    assert UserBadge.query.filter_by(user_id=user.id, badge_id=first.badge_id).count() == 1
    badge = Badge.query.filter_by(name="First Post").first()
    assert badge.awarded_count == 1  # not incremented twice


def test_check_and_award_badge_not_qualified_returns_none(db_session, seeded_badges, make_user):
    user = make_user(total_posts=0)
    result = badge_service.check_and_award_badge(user.id, "First Post")
    assert result is None
    assert UserBadge.query.filter_by(user_id=user.id).count() == 0


def test_check_and_award_badge_unknown_badge_name_returns_none(db_session, seeded_badges, make_user):
    user = make_user()
    result = badge_service.check_and_award_badge(user.id, "Not A Real Badge")
    assert result is None


def test_check_and_award_badge_commit_false_does_not_commit(db_session, seeded_badges, make_user):
    user = make_user(total_posts=1)
    with patch("services.badge_service.notification_service.notify_badge_earned"), \
         patch("services.badge_service.cache_service.delete"), \
         patch("extensions.db.session.commit") as mock_commit:
        badge_service.check_and_award_badge(user.id, "First Post", commit=False)
    mock_commit.assert_not_called()


def test_check_and_award_badge_commit_true_commits(db_session, seeded_badges, make_user):
    user = make_user(total_posts=1)
    with patch("services.badge_service.notification_service.notify_badge_earned"), \
         patch("services.badge_service.cache_service.delete"), \
         patch("extensions.db.session.commit") as mock_commit:
        badge_service.check_and_award_badge(user.id, "First Post", commit=True)
    mock_commit.assert_called_once()


def test_check_and_award_badge_skip_cache_invalidation(db_session, seeded_badges, make_user):
    user = make_user(total_posts=1)
    with patch("services.badge_service.notification_service.notify_badge_earned"), \
         patch("services.badge_service.cache_service.delete") as mock_delete:
        badge_service.check_and_award_badge(user.id, "First Post", skip_cache_invalidation=True)
    mock_delete.assert_not_called()


# ============================================================================
# check_all_badges_for_user
# ============================================================================

def test_check_all_badges_batches_commit_and_cache_invalidation(db_session, seeded_badges, make_user):
    """User qualifies for a handful of badges -- commit and cache
    invalidation must each happen exactly ONCE, not once per badge."""
    user = make_user(
        total_posts=100,          # First Post, Prolific Writer, Content Creator
        login_streak=100,         # 7-Day, 30-Day, Unstoppable
        reputation=0,
    )
    with patch("services.badge_service.notification_service.notify_badge_earned"), \
         patch("services.badge_service.cache_service.delete") as mock_delete, \
         patch("extensions.db.session.commit") as mock_commit:
        awarded = badge_service.check_all_badges_for_user(user.id)

    assert len(awarded) >= 3  # at least the posts-based badges
    mock_commit.assert_called_once()
    mock_delete.assert_called_once_with(f"sh:1:badge:progress:{user.id}")


def test_check_all_badges_zero_qualifying_no_commit_no_invalidation(db_session, seeded_badges, make_user):
    # An explicit earlier user establishes "first user" well outside the
    # early_adopter 30-day window, so the subject below doesn't trivially
    # qualify for Early Adopter merely by being the only/earliest row in
    # this test's isolated DB state.
    make_user(joined_at=datetime.datetime(2020, 1, 1))
    user = make_user(
        total_posts=0, login_streak=0, reputation=0, total_helpful=0,
        joined_at=datetime.datetime(2026, 6, 1),
    )
    with patch("services.badge_service.notification_service.notify_badge_earned"), \
         patch("services.badge_service.cache_service.delete") as mock_delete, \
         patch("extensions.db.session.commit") as mock_commit:
        awarded = badge_service.check_all_badges_for_user(user.id)

    assert awarded == []
    mock_commit.assert_not_called()
    mock_delete.assert_not_called()


# ============================================================================
# calculate_badge_progress
# ============================================================================

def test_calculate_badge_progress_trackable_type(db_session, seeded_badges, make_user):
    user = make_user(total_posts=25)
    badge = Badge.query.filter_by(name="Prolific Writer").first()  # posts_count: 50

    progress = badge_service.calculate_badge_progress(user.id, badge.id)

    assert progress["current"] == 25
    assert progress["required"] == 50
    assert progress["percentage"] == 50.0
    assert progress["type"] == "posts"
    assert progress["remaining"] == 25
    assert "message" not in progress


def test_calculate_badge_progress_caps_percentage_at_100(db_session, seeded_badges, make_user):
    user = make_user(total_posts=999)
    badge = Badge.query.filter_by(name="First Post").first()  # posts_count: 1

    progress = badge_service.calculate_badge_progress(user.id, badge.id)
    assert progress["percentage"] == 100
    assert progress["remaining"] == 0  # floored, never negative


def test_calculate_badge_progress_untrackable_type_special_shape(db_session, seeded_badges, make_user):
    user = make_user()
    badge = Badge.query.filter_by(name="Thread Leader").first()  # thread_leader: True

    progress = badge_service.calculate_badge_progress(user.id, badge.id)

    assert progress["type"] == "special"
    assert "message" in progress
    assert "remaining" not in progress  # dropped per BadgeProgress.to_dict()'s conditional


def test_calculate_badge_progress_missing_badge_or_user_returns_none(db_session, seeded_badges, make_user):
    user = make_user()
    assert badge_service.calculate_badge_progress(user.id, 999999) is None
    badge = Badge.query.first()
    assert badge_service.calculate_badge_progress(999999, badge.id) is None
