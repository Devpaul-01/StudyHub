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


def test_award_reputation_invalidates_expected_cache_keys(db_session, make_user):
    user = make_user(reputation=10)
    with patch("services.reputation_service.notification_service.notify_level_up"), \
         patch("services.reputation_service.cache_service.delete") as mock_delete, \
         patch("services.reputation_service.cache_service.delete_pattern") as mock_delete_pattern:
        reputation_service.award_reputation(user.id, "post_10_likes")

    delete_keys = {c.args[0] for c in mock_delete.call_args_list}
    assert f"sh:1:rep:me:{user.id}" in delete_keys
    assert f"sh:1:an:overview:{user.id}" in delete_keys
    pattern_keys = {c.args[0] for c in mock_delete_pattern.call_args_list}
    assert f"sh:1:lb:rank:{user.id}:*" in pattern_keys
    assert f"sh:1:lb:breakdown:{user.id}:*" in pattern_keys


# ============================================================================
# check_and_award_milestone
# ============================================================================

def test_check_and_award_milestone_exact_10_likes(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user, positive_reactions_count=10)
    with patch("services.reputation_service.award_reputation") as mock_award:
        reputation_service.check_and_award_milestone(user.id, post_id=post.id)
    mock_award.assert_called_once_with(user.id, "post_10_likes", "post", post.id)


def test_check_and_award_milestone_exact_50_likes(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user, positive_reactions_count=50)
    with patch("services.reputation_service.award_reputation") as mock_award:
        reputation_service.check_and_award_milestone(user.id, post_id=post.id)
    mock_award.assert_called_once_with(user.id, "post_50_likes", "post", post.id)


def test_check_and_award_milestone_exact_100_likes(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user, positive_reactions_count=100)
    with patch("services.reputation_service.award_reputation") as mock_award:
        reputation_service.check_and_award_milestone(user.id, post_id=post.id)
    mock_award.assert_called_once_with(user.id, "post_100_likes", "post", post.id)


def test_check_and_award_milestone_does_not_retrigger_past_threshold(db_session, make_user, make_post):
    """Exact-equality-on-milestone-value behavior: 11 likes (just past the
    10-like threshold) must NOT re-trigger post_10_likes."""
    user = make_user()
    post = make_post(user, positive_reactions_count=11)
    with patch("services.reputation_service.award_reputation") as mock_award:
        reputation_service.check_and_award_milestone(user.id, post_id=post.id)
    mock_award.assert_not_called()


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
