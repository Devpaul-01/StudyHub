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
        mock_delete.assert_not_called()          # invalidation waits for the commit
        db_session.commit()

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


def test_check_and_award_badge_never_commits(db_session, seeded_badges, make_user):
    user = make_user(total_posts=1)
    with patch("services.badge_service.notification_service.notify_badge_earned"), \
         patch("services.badge_service.cache_service.delete"), \
         patch("extensions.db.session.commit") as mock_commit:
        badge_service.check_and_award_badge(user.id, "First Post")
    mock_commit.assert_not_called()


def test_check_and_award_badge_has_no_commit_parameter():
    import inspect
    assert "commit" not in inspect.signature(badge_service.check_and_award_badge).parameters


def test_check_and_award_badge_missing_badge_warns_and_returns_none(db_session, make_user, caplog):
    user = make_user(total_posts=1)          # note: badges NOT seeded
    with caplog.at_level("WARNING"):
        assert badge_service.check_and_award_badge(user.id, "First Post") is None
    assert "has seed_badges() been run" in caplog.text


def test_check_and_award_badge_inactive_badge_not_awarded(db_session, seeded_badges, make_user):
    user = make_user(total_posts=1)
    Badge.query.filter_by(name="First Post").update({"is_active": False})
    db_session.commit()
    assert badge_service.check_and_award_badge(user.id, "First Post") is None


def test_duplicate_award_is_swallowed_by_savepoint(db_session, seeded_badges, make_user):
    """A concurrent award (UNIQUE violation) must not poison the caller's
    transaction: other pending work in the same transaction still commits."""
    user = make_user(total_posts=1)
    other = make_user()
    badge = Badge.query.filter_by(name="First Post").first()
    db_session.add(UserBadge(user_id=user.id, badge_id=badge.id))
    db_session.commit()

    real_first = UserBadge.query.filter_by

    class _Empty:
        def first(self):
            return None

    with patch("services.badge_service.notification_service.notify_badge_earned") as mock_notify, \
         patch("services.badge_service.UserBadge") as ub_cls:
        # existence pre-check "misses" (the race), insert then collides
        ub_cls.query.filter_by.return_value = _Empty()
        ub_cls.side_effect = lambda **kw: UserBadge(**kw)
        other.bio = "pending work in same transaction"
        result = badge_service.check_and_award_badge(user.id, "First Post")
        db_session.commit()

    assert result is None
    mock_notify.assert_not_called()
    assert UserBadge.query.filter_by(user_id=user.id, badge_id=badge.id).count() == 1
    assert Badge.query.filter_by(name="First Post").first().awarded_count == 0
    assert other.bio == "pending work in same transaction"


def test_awarded_count_uses_atomic_sql_increment(db_session, seeded_badges, make_user):
    from sqlalchemy import event
    from extensions import db
    user = make_user(total_posts=1)
    statements = []

    def grab(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE BADGES"):
            statements.append(" ".join(statement.split()))

    event.listen(db.engine, "before_cursor_execute", grab)
    try:
        with patch("services.badge_service.notification_service.notify_badge_earned"):
            badge_service.check_and_award_badge(user.id, "First Post")
            db_session.commit()
    finally:
        event.remove(db.engine, "before_cursor_execute", grab)

    assert any("awarded_count=(badges.awarded_count + ?)" in x for x in statements), statements


def test_check_and_award_badge_skip_cache_invalidation(db_session, seeded_badges, make_user):
    user = make_user(total_posts=1)
    with patch("services.badge_service.notification_service.notify_badge_earned"), \
         patch("services.badge_service.cache_service.delete") as mock_delete:
        badge_service.check_and_award_badge(user.id, "First Post", skip_cache_invalidation=True)
    mock_delete.assert_not_called()


# ============================================================================
# check_all_badges_for_user
# ============================================================================

def test_check_all_badges_never_commits_and_invalidates_cache_once_after_commit(
        db_session, seeded_badges, make_user):
    """User qualifies for a handful of badges -- no commit inside the
    service, and the cache is invalidated exactly ONCE (after the caller's
    commit), not once per badge."""
    user = make_user(
        total_posts=100,          # First Post, Prolific Writer, Content Creator
        login_streak=100,         # 7-Day, 30-Day, Unstoppable
        reputation=0,
    )
    with patch("services.badge_service.notification_service.notify_badge_earned"), \
         patch("services.badge_service.cache_service.delete") as mock_delete, \
         patch("extensions.db.session.commit") as mock_commit:
        awarded = badge_service.check_all_badges_for_user(user.id)
        mock_commit.assert_not_called()
    assert len(awarded) >= 3  # at least the posts-based badges
    mock_delete.assert_not_called()
    db_session.commit()
    mock_delete.assert_called_once_with(f"sh:1:badge:progress:{user.id}")
    assert UserBadge.query.filter_by(user_id=user.id).count() == len(awarded)


def test_check_all_badges_skips_already_earned(db_session, seeded_badges, make_user):
    user = make_user(total_posts=1)
    with patch("services.badge_service.notification_service.notify_badge_earned") as mock_notify:
        first = badge_service.check_all_badges_for_user(user.id)
        db_session.commit()
        second = badge_service.check_all_badges_for_user(user.id)
    assert any(b.name == "First Post" for b in first)
    assert second == []
    assert mock_notify.call_count == len(first)


def test_check_all_badges_query_count_does_not_scale_with_badge_count(
        db_session, seeded_badges, make_user):
    from sqlalchemy import event
    from extensions import db
    make_user(joined_at=datetime.datetime(2020, 1, 1))
    user = make_user(total_posts=0, login_streak=0, reputation=0, total_helpful=0,
                     joined_at=datetime.datetime(2026, 6, 1))
    count = []
    def grab(*a, **k): count.append(1)
    event.listen(db.engine, "before_cursor_execute", grab)
    try:
        badge_service.check_all_badges_for_user(user.id)
    finally:
        event.remove(db.engine, "before_cursor_execute", grab)
    # 18 badges: user + badges + earned set + a handful of criteria
    # queries. The old per-badge Badge/UserBadge/User lookups needed 50+.
    assert len(count) < 30, len(count)


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


# ============================================================================
# seeding, definitions and criteria edge cases
# ============================================================================

def test_seed_badges_creates_all_definitions_and_is_idempotent(db_session):
    first = badge_service.seed_badges()
    assert first["created"] == len(badge_service.BADGE_DEFINITIONS)
    second = badge_service.seed_badges()
    assert second == {"created": 0, "updated": 0, "unchanged": len(badge_service.BADGE_DEFINITIONS)}
    assert Badge.query.count() == len(badge_service.BADGE_DEFINITIONS)


def test_seed_badges_updates_changed_definition_but_keeps_counters(db_session):
    badge_service.seed_badges()
    badge = Badge.query.filter_by(name="Reputation Master").first()
    badge.description = "stale text"
    badge.criteria = {"reputation": 1000}
    badge.awarded_count = 7
    badge.is_active = False
    db_session.commit()

    result = badge_service.seed_badges()

    badge = Badge.query.filter_by(name="Reputation Master").first()
    assert result["updated"] == 1
    assert badge.criteria == {"reputation": 1001}
    assert "Master level" in badge.description
    assert badge.awarded_count == 7 and badge.is_active is False


def test_badge_definitions_have_no_unpersisted_color_and_unique_names():
    names = [d["name"] for d in badge_service.BADGE_DEFINITIONS]
    assert len(names) == len(set(names))
    assert all("color" not in d for d in badge_service.BADGE_DEFINITIONS)


def test_reputation_master_matches_master_tier_boundary(db_session, make_user):
    from services.reputation_levels import get_reputation_level_name
    criteria = next(d for d in badge_service.BADGE_DEFINITIONS
                    if d["name"] == "Reputation Master")["criteria"]
    expert = make_user(reputation=1000)
    master = make_user(reputation=1001)
    assert get_reputation_level_name(1000) != "Master"
    assert badge_service._user_qualifies(expert, criteria) is False
    assert get_reputation_level_name(1001) == "Master"
    assert badge_service._user_qualifies(master, criteria) is True


def test_early_adopter_uses_configured_launch_date(db_session, make_user, monkeypatch):
    make_user(joined_at=datetime.datetime(2020, 1, 1))        # earliest user is irrelevant now
    monkeypatch.setenv("PLATFORM_LAUNCH_DATE", "2026-01-01")
    inside = make_user(joined_at=datetime.datetime(2026, 1, 25))
    outside = make_user(joined_at=datetime.datetime(2026, 2, 5))
    assert badge_service._user_qualifies(inside, {"early_adopter": True}) is True
    assert badge_service._user_qualifies(outside, {"early_adopter": True}) is False


def test_early_adopter_invalid_launch_date_falls_back(db_session, make_user, monkeypatch):
    monkeypatch.setenv("PLATFORM_LAUNCH_DATE", "not-a-date")
    first = make_user(joined_at=datetime.datetime(2026, 1, 1))
    assert badge_service._user_qualifies(first, {"early_adopter": True}) is True


def test_early_adopter_user_without_joined_at_does_not_crash(db_session, make_user):
    make_user(joined_at=datetime.datetime(2026, 1, 1))
    user = make_user()
    user.joined_at = None
    assert badge_service._user_qualifies(user, {"early_adopter": True}) is False


def test_department_rank_requires_approved_user_with_department(db_session, make_user, make_student_profile):
    pending = make_user(reputation=500, status="pending")
    make_student_profile(pending, department="CS")
    assert badge_service._user_qualifies(pending, {"department_rank": 3}) is False

    nodept = make_user(reputation=500, status="approved")
    make_student_profile(nodept, department=None)
    assert badge_service._user_qualifies(nodept, {"department_rank": 3}) is False
