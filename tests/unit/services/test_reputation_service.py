"""
Tests for services/reputation_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.4. The single most important
assertion in this file is that award_reputation does NOT commit --
the module's own docstring documents this as a deliberate behavior
change from the pre-migration version, with real call sites elsewhere
in the codebase that were fixed alongside it.
"""

from unittest.mock import patch

import pytest

from services import reputation_service
from models import ReputationHistory


# ============================================================================
# award_reputation
# ============================================================================

def test_award_reputation_known_action_increments_and_records_history(db_session, make_user):
    user = make_user(reputation=10)
    with patch("services.reputation_service.notification_service.notify_level_up"), \
         patch("services.reputation_service.cache_service.delete"), \
         patch("services.reputation_service.cache_service.delete_pattern"):
        history = reputation_service.award_reputation(user.id, "post_10_likes")

    assert user.reputation == 15  # 10 + 5
    assert history.action == "post_10_likes"
    assert history.points_change == 5
    assert history.reputation_before == 10
    assert history.reputation_after == 15


def test_award_reputation_does_not_commit(db_session, make_user):
    """The single most important assertion in this file -- services never
    commit (Document 2 §5's convention), deliberately changed here from
    the pre-migration behavior per the module's own docstring."""
    user = make_user(reputation=10)
    with patch("services.reputation_service.notification_service.notify_level_up"), \
         patch("services.reputation_service.cache_service.delete"), \
         patch("services.reputation_service.cache_service.delete_pattern"), \
         patch("extensions.db.session.commit") as mock_commit:
        reputation_service.award_reputation(user.id, "post_10_likes")
    mock_commit.assert_not_called()


def test_award_reputation_custom_points(db_session, make_user):
    user = make_user(reputation=10)
    with patch("services.reputation_service.notification_service.notify_level_up"), \
         patch("services.reputation_service.cache_service.delete"), \
         patch("services.reputation_service.cache_service.delete_pattern"):
        history = reputation_service.award_reputation(user.id, None, custom_points=7)

    assert user.reputation == 17
    assert history.action == "custom"
    assert history.points_change == 7


def test_award_reputation_no_action_no_custom_points_returns_none(db_session, make_user):
    user = make_user(reputation=10)
    result = reputation_service.award_reputation(user.id, "not_a_real_action")
    assert result is None
    assert user.reputation == 10
    assert ReputationHistory.query.filter_by(user_id=user.id).count() == 0


def test_award_reputation_unknown_user_returns_none(db_session):
    result = reputation_service.award_reputation(999999, "post_10_likes")
    assert result is None


def test_award_reputation_clamps_to_zero_floor(db_session, make_user):
    user = make_user(reputation=3)
    with patch("services.reputation_service.notification_service.notify_level_up"), \
         patch("services.reputation_service.cache_service.delete"), \
         patch("services.reputation_service.cache_service.delete_pattern"):
        reputation_service.award_reputation(user.id, None, custom_points=-50)
    assert user.reputation == 0


def test_award_reputation_level_up_crossing_notifies(db_session, make_user):
    """49 -> 51 crosses Newbie -> Learner (boundary at 50/51)."""
    user = make_user(reputation=49)
    with patch("services.reputation_service.notification_service.notify_level_up") as mock_notify, \
         patch("services.reputation_service.cache_service.delete"), \
         patch("services.reputation_service.cache_service.delete_pattern"):
        reputation_service.award_reputation(user.id, None, custom_points=2)

    mock_notify.assert_called_once()
    call_args = mock_notify.call_args[0]
    assert call_args[0] == user.id
    assert call_args[1]["name"] == "Learner"


def test_award_reputation_same_level_does_not_notify(db_session, make_user):
    user = make_user(reputation=10)
    with patch("services.reputation_service.notification_service.notify_level_up") as mock_notify, \
         patch("services.reputation_service.cache_service.delete"), \
         patch("services.reputation_service.cache_service.delete_pattern"):
        reputation_service.award_reputation(user.id, None, custom_points=5)  # 10 -> 15, still Newbie

    mock_notify.assert_not_called()


def test_award_reputation_downward_crossing_notifies(db_session, make_user):
    """Documented, deliberately-locked-in quirk: the level comparison has
    no directionality check (`old_level["name"] != new_level["name"]`),
    so a downward crossing fires notify_level_up exactly like an upward
    one. Pinned down explicitly rather than left an accidental,
    unverified behavior."""
    user = make_user(reputation=55)  # Learner (51-200)
    with patch("services.reputation_service.notification_service.notify_level_up") as mock_notify, \
         patch("services.reputation_service.cache_service.delete"), \
         patch("services.reputation_service.cache_service.delete_pattern"):
        reputation_service.award_reputation(user.id, None, custom_points=-10)  # 55 -> 45, Newbie

    mock_notify.assert_called_once()
    assert mock_notify.call_args[0][1]["name"] == "Newbie"


def test_award_reputation_invalidates_caches_only_after_commit(db_session, make_user):
    user = make_user(reputation=10)
    with patch("services.reputation_service.notification_service.notify_level_up"), \
         patch("services.reputation_service.cache_service.delete") as mock_delete, \
         patch("services.reputation_service.cache_service.bump_user_version") as mock_bump, \
         patch("services.reputation_service.cache_service.delete_pattern") as mock_pattern:
        reputation_service.award_reputation(user.id, "post_10_likes")
        # Nothing is invalidated while the transaction is still open ...
        mock_delete.assert_not_called()
        mock_bump.assert_not_called()
        db_session.commit()

    # ... and exactly the expected keys are invalidated once it commits,
    # without any keyspace SCAN.
    delete_keys = {c.args[0] for c in mock_delete.call_args_list}
    assert f"sh:1:rep:me:{user.id}" in delete_keys
    assert f"sh:1:an:overview:{user.id}" in delete_keys
    assert f"sh:1:badge:progress:{user.id}" in delete_keys
    mock_bump.assert_called_once_with(user.id)
    mock_pattern.assert_not_called()


def test_award_reputation_cache_not_invalidated_on_rollback(db_session, make_user):
    user = make_user(reputation=10)
    with patch("services.reputation_service.notification_service.notify_level_up"), \
         patch("services.reputation_service.cache_service.bump_user_version") as mock_bump:
        reputation_service.award_reputation(user.id, "post_10_likes")
        db_session.rollback()
        db_session.commit()
    mock_bump.assert_not_called()


# ============================================================================
# idempotency / ledger integrity / reversal
# ============================================================================

def test_award_reputation_idempotency_key_pays_once(db_session, make_user):
    user = make_user(reputation=0)
    first = reputation_service.award_reputation(
        user.id, "comment_marked_helpful", "comment", 1, idempotency_key="k:1")
    second = reputation_service.award_reputation(
        user.id, "comment_marked_helpful", "comment", 1, idempotency_key="k:1")
    db_session.commit()

    assert first.duplicate is False
    assert second.duplicate is True and second.id == first.id
    assert second.leveled_up is False
    assert user.reputation == 3
    assert ReputationHistory.query.filter_by(user_id=user.id).count() == 1


def test_award_reputation_without_key_is_repeatable(db_session, make_user):
    user = make_user(reputation=0)
    reputation_service.award_reputation(user.id, "comment_marked_helpful")
    reputation_service.award_reputation(user.id, "comment_marked_helpful")
    assert user.reputation == 6


def test_award_reputation_records_effective_delta_after_floor(db_session, make_user):
    from sqlalchemy import func
    from extensions import db
    user = make_user(reputation=3)
    history = reputation_service.award_reputation(user.id, "content_reported")  # -10
    db_session.commit()

    assert user.reputation == 0
    assert history.points_change == -3          # effective, not requested
    assert history.reputation_before == 3 and history.reputation_after == 0
    ledger = db.session.query(func.sum(ReputationHistory.points_change)).filter_by(user_id=user.id).scalar()
    assert 3 + ledger == user.reputation


def test_award_reputation_takes_a_row_lock_on_the_user(db_session, make_user):
    user = make_user(reputation=0)
    with patch.object(reputation_service, "_lock_user", wraps=reputation_service._lock_user) as lock:
        reputation_service.award_reputation(user.id, "comment_marked_helpful")
    lock.assert_called_once_with(user.id)


def test_award_reputation_zero_custom_points_is_ignored(db_session, make_user):
    user = make_user(reputation=5)
    assert reputation_service.award_reputation(user.id, None, custom_points=0) is None
    assert user.reputation == 5


def test_reverse_reputation_appends_compensating_row_once(db_session, make_user):
    user = make_user(reputation=0)
    reputation_service.award_reputation(user.id, "comment_marked_solution", "comment", 9)
    assert user.reputation == 15

    rev = reputation_service.reverse_reputation(user.id, "comment_marked_solution", "comment", 9)
    assert rev.points_change == -15 and user.reputation == 0
    assert rev.action == "comment_marked_solution_reversed"

    # nothing left to reverse -> no-op
    assert reputation_service.reverse_reputation(user.id, "comment_marked_solution", "comment", 9) is None
    assert user.reputation == 0
    assert ReputationHistory.query.filter_by(user_id=user.id).count() == 2


def test_reverse_reputation_handles_repeated_award_cycles(db_session, make_user):
    user = make_user(reputation=0)
    for _ in range(2):
        reputation_service.award_reputation(user.id, "comment_marked_solution", "comment", 9)
        reputation_service.reverse_reputation(user.id, "comment_marked_solution", "comment", 9)
    assert user.reputation == 0


def test_reverse_reputation_without_award_returns_none(db_session, make_user):
    user = make_user(reputation=10)
    assert reputation_service.reverse_reputation(user.id, "comment_marked_solution", "comment", 1) is None
    assert user.reputation == 10


def test_awardable_actions_excludes_unwired_actions():
    active = reputation_service.awardable_actions()
    assert set(active) == {
        "post_10_likes", "post_50_likes", "post_100_likes",
        "comment_marked_solution", "comment_marked_helpful",
    }
    assert reputation_service.action_points("comment_marked_solution") == 15


# ============================================================================
# check_and_award_milestone
# ============================================================================

def test_check_and_award_milestone_10_likes(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user, positive_reactions_count=10)
    with patch("services.reputation_service.award_reputation") as mock_award:
        reputation_service.check_and_award_milestone(user.id, post_id=post.id)
    mock_award.assert_called_once_with(
        user.id, "post_10_likes", "post", post.id,
        idempotency_key=f"post_10_likes:post:{post.id}")


def test_check_and_award_milestone_50_likes_also_pays_skipped_10(db_session, make_user, make_post):
    """A counter that jumped past 10 (simultaneous likes) still pays it."""
    user = make_user()
    post = make_post(user, positive_reactions_count=50)
    with patch("services.reputation_service.award_reputation") as mock_award:
        reputation_service.check_and_award_milestone(user.id, post_id=post.id)
    assert [c.args[1] for c in mock_award.call_args_list] == ["post_10_likes", "post_50_likes"]


def test_check_and_award_milestone_100_likes(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user, positive_reactions_count=100)
    with patch("services.reputation_service.award_reputation") as mock_award:
        reputation_service.check_and_award_milestone(user.id, post_id=post.id)
    assert [c.args[1] for c in mock_award.call_args_list] == [
        "post_10_likes", "post_50_likes", "post_100_likes"]


def test_check_and_award_milestone_below_threshold_awards_nothing(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user, positive_reactions_count=9)
    with patch("services.reputation_service.award_reputation") as mock_award:
        assert reputation_service.check_and_award_milestone(user.id, post_id=post.id) == []
    mock_award.assert_not_called()


def test_milestone_toggle_around_boundary_pays_once(db_session, make_user, make_post):
    """Regression: like/unlike/like at the 10-like boundary used to re-award
    +5 on every re-crossing."""
    user = make_user(reputation=0)
    post = make_post(user, positive_reactions_count=10)
    for count in (10, 9, 10, 9, 10):
        post.positive_reactions_count = count
        reputation_service.check_and_award_milestone(user.id, post_id=post.id)
        db_session.commit()
    assert user.reputation == 5
    assert ReputationHistory.query.filter_by(user_id=user.id, action="post_10_likes").count() == 1


def test_milestone_skips_legacy_rows_without_idempotency_key(db_session, make_user, make_post):
    """Rows written before keys existed must not be paid again."""
    user = make_user(reputation=5)
    post = make_post(user, positive_reactions_count=11)
    db_session.add(ReputationHistory(
        user_id=user.id, action="post_10_likes", points_change=5,
        related_type="post", related_id=post.id,
        reputation_before=0, reputation_after=5))
    db_session.commit()
    assert reputation_service.check_and_award_milestone(user.id, post_id=post.id) == []
    assert user.reputation == 5


def test_check_and_award_milestone_ignores_other_users_post(db_session, make_user, make_post):
    author = make_user()
    other_user = make_user()
    post = make_post(author, positive_reactions_count=10)
    with patch("services.reputation_service.award_reputation") as mock_award:
        reputation_service.check_and_award_milestone(other_user.id, post_id=post.id)
    mock_award.assert_not_called()


def test_check_and_award_milestone_noop_with_no_ids(db_session, make_user):
    user = make_user()
    with patch("services.reputation_service.award_reputation") as mock_award:
        # Should not raise
        reputation_service.check_and_award_milestone(user.id)
    mock_award.assert_not_called()


def test_level_up_schedules_a_badge_check(db_session, make_user):
    user = make_user(reputation=48)
    with patch("services.reputation_service.notification_service.notify_level_up"), \
         patch("services.reputation_service.schedule_badge_check") as sched:
        reputation_service.award_reputation(user.id, "post_10_likes")      # 48 -> 53 (Newbie -> Learner)
    sched.assert_called_once_with(user.id)


def test_no_level_change_does_not_schedule_a_badge_check(db_session, make_user):
    user = make_user(reputation=10)
    with patch("services.reputation_service.schedule_badge_check") as sched:
        reputation_service.award_reputation(user.id, "comment_marked_helpful")
    sched.assert_not_called()
