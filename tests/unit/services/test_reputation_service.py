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


def test_award_reputation_ledger_records_the_applied_delta_when_floor_engages(db_session, make_user):
    """-10 on a 3-point user applies -3. The ledger must say -3 so that
    sum(points_change) always reconciles with users.reputation."""
    user = make_user(reputation=3)
    with patch("services.reputation_service.notification_service.notify_level_up"), \
         patch("services.reputation_service.cache_service.delete"), \
         patch("services.reputation_service.cache_service.delete_pattern"):
        history = reputation_service.award_reputation(user.id, "content_reported")

    assert user.reputation == 0
    assert history.points_change == -3
    assert history.reputation_before == 3
    assert history.reputation_after == 0


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


def test_award_reputation_does_not_invalidate_before_commit(db_session, make_user, fakeredis_client):
    """Invalidating before the caller commits lets a concurrent reader
    re-cache the pre-commit value for a full TTL."""
    user = make_user(reputation=10)
    fakeredis_client.set(f"sh:1:rep:me:{user.id}", "1")
    with patch("services.reputation_service.notification_service.notify_level_up"):
        reputation_service.award_reputation(user.id, "post_10_likes")

    assert fakeredis_client.get(f"sh:1:rep:me:{user.id}") == "1"
    assert fakeredis_client.get(f"sh:1:ver:{user.id}") is None


def test_award_reputation_invalidates_after_commit(db_session, make_user, fakeredis_client):
    user = make_user(reputation=10)
    fakeredis_client.set(f"sh:1:rep:me:{user.id}", "1")
    fakeredis_client.set(f"sh:1:an:overview:{user.id}", "1")
    with patch("services.reputation_service.notification_service.notify_level_up"):
        reputation_service.award_reputation(user.id, "post_10_likes")
    db_session.commit()

    assert fakeredis_client.get(f"sh:1:rep:me:{user.id}") is None
    assert fakeredis_client.get(f"sh:1:an:overview:{user.id}") is None
    assert fakeredis_client.get(f"sh:1:ver:{user.id}") == "1"   # per-user version bumped


def test_award_reputation_discards_invalidation_on_rollback(db_session, make_user, fakeredis_client):
    user = make_user(reputation=10)
    db_session.commit()
    fakeredis_client.set(f"sh:1:rep:me:{user.id}", "1")
    with patch("services.reputation_service.notification_service.notify_level_up"):
        reputation_service.award_reputation(user.id, "post_10_likes")
    db_session.rollback()
    db_session.commit()

    assert fakeredis_client.get(f"sh:1:rep:me:{user.id}") == "1"
    assert fakeredis_client.get(f"sh:1:ver:{user.id}") is None


def test_one_commit_invalidates_each_user_once(db_session, make_user, fakeredis_client):
    user = make_user(reputation=10)
    with patch("services.reputation_service.notification_service.notify_level_up"):
        reputation_service.award_reputation(user.id, "post_10_likes")
        reputation_service.award_reputation(user.id, "comment_marked_helpful")
    db_session.commit()
    assert fakeredis_client.get(f"sh:1:ver:{user.id}") == "1"


# ============================================================================
# check_and_award_milestone
# ============================================================================

def test_check_and_award_milestone_exact_10_likes(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user, positive_reactions_count=10)
    with patch("services.reputation_service.award_reputation") as mock_award:
        reputation_service.check_and_award_milestone(user.id, post_id=post.id)
    mock_award.assert_called_once_with(
        user.id, "post_10_likes", "post", post.id,
        idempotency_key=f"post_10_likes:post:{post.id}", once_per_related=True,
    )


def test_check_and_award_milestone_exact_50_likes(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user, positive_reactions_count=50)
    with patch("services.reputation_service.award_reputation") as mock_award:
        reputation_service.check_and_award_milestone(user.id, post_id=post.id)
    mock_award.assert_called_once_with(
        user.id, "post_50_likes", "post", post.id,
        idempotency_key=f"post_50_likes:post:{post.id}", once_per_related=True,
    )


def test_check_and_award_milestone_exact_100_likes(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user, positive_reactions_count=100)
    with patch("services.reputation_service.award_reputation") as mock_award:
        reputation_service.check_and_award_milestone(user.id, post_id=post.id)
    mock_award.assert_called_once_with(
        user.id, "post_100_likes", "post", post.id,
        idempotency_key=f"post_100_likes:post:{post.id}", once_per_related=True,
    )


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


# ============================================================================
# One-time awards (idempotency)
# ============================================================================

def _count(db_session, user_id, action):
    from models import ReputationHistory
    return db_session.query(ReputationHistory).filter_by(user_id=user_id, action=action).count()


def test_award_reputation_same_idempotency_key_pays_once(db_session, make_user):
    user = make_user(reputation=0)
    with patch("services.reputation_service.notification_service.notify_level_up"):
        first = reputation_service.award_reputation(
            user.id, "comment_marked_helpful", "comment", 1, idempotency_key="helpful:1:2")
        second = reputation_service.award_reputation(
            user.id, "comment_marked_helpful", "comment", 1, idempotency_key="helpful:1:2")
    assert first is not None and second is None
    assert user.reputation == 3
    assert _count(db_session, user.id, "comment_marked_helpful") == 1


def test_award_reputation_different_keys_each_pay(db_session, make_user):
    user = make_user(reputation=0)
    with patch("services.reputation_service.notification_service.notify_level_up"):
        reputation_service.award_reputation(user.id, "comment_marked_helpful", "comment", 1, idempotency_key="helpful:1:2")
        reputation_service.award_reputation(user.id, "comment_marked_helpful", "comment", 1, idempotency_key="helpful:1:3")
    assert user.reputation == 6


def test_once_per_related_respects_rows_written_before_keys_existed(db_session, make_user):
    """Legacy rows have no idempotency_key; they must still block a re-award."""
    from models import ReputationHistory
    user = make_user(reputation=15)
    db_session.add(ReputationHistory(
        user_id=user.id, action="comment_marked_solution", points_change=15,
        related_type="comment", related_id=7, reputation_before=0, reputation_after=15))
    db_session.flush()
    with patch("services.reputation_service.notification_service.notify_level_up"):
        result = reputation_service.award_reputation(
            user.id, "comment_marked_solution", "comment", 7,
            idempotency_key="solution:7", once_per_related=True)
    assert result is None
    assert user.reputation == 15


def test_once_per_related_still_awards_a_different_related_object(db_session, make_user):
    user = make_user(reputation=0)
    with patch("services.reputation_service.notification_service.notify_level_up"):
        reputation_service.award_reputation(user.id, "comment_marked_solution", "comment", 7,
                                            idempotency_key="solution:7", once_per_related=True)
        reputation_service.award_reputation(user.id, "comment_marked_solution", "comment", 8,
                                            idempotency_key="solution:8", once_per_related=True)
    assert user.reputation == 30


def test_milestone_pays_once_when_the_count_passes_through_the_threshold_repeatedly(db_session, make_user, make_post):
    user = make_user(reputation=0)
    post = make_post(user, positive_reactions_count=10)
    with patch("services.reputation_service.notification_service.notify_level_up"):
        for _ in range(4):                       # like -> unlike -> like ... leaves count at 10 each time
            reputation_service.check_and_award_milestone(user.id, post_id=post.id)
    assert _count(db_session, user.id, "post_10_likes") == 1
    assert user.reputation == 5
