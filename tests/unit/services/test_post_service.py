"""
Tests for services/post_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.3.

Note on check_helpful_milestones: per reconciliation_service.py's own
docstring elsewhere in this codebase, PostReaction.reaction_type="helpful"
is never actually written by any live route (react_to_post hardcodes
"like"), so this function is currently dead in production. The tests
below still verify its internal logic is correct by directly inserting
PostReaction rows with reaction_type="helpful" -- bypassing the fact
that no route creates them -- so the contract stays documented and the
tests become meaningful again immediately if a "mark helpful" path is
ever reintroduced. This is a real discrepancy worth knowing about, not
a testing mistake.
"""

import datetime
from unittest.mock import patch

import pytest

from services import post_service
from models import Mention, UserActivity, PostReaction


# ============================================================================
# extract_public_id
# ============================================================================

def test_extract_public_id_standard_url():
    url = "https://res.cloudinary.com/demo/image/upload/v1234567/folder/photo.jpg"
    assert post_service.extract_public_id(url) == "folder/photo"


def test_extract_public_id_strips_query_params():
    url = "https://res.cloudinary.com/demo/image/upload/v1234567/folder/photo.jpg?foo=bar"
    assert post_service.extract_public_id(url) == "folder/photo"


def test_extract_public_id_no_version_segment_documented_actual_behavior():
    """No explicit fallback is documented for a missing version segment
    -- pin down the real behavior rather than assume one."""
    url = "https://example.com/no/version/here/photo.jpg"
    result = post_service.extract_public_id(url)
    # re.split on a pattern that never matches returns the original
    # (extension-stripped) string as the sole list element.
    assert result == "https://example.com/no/version/here/photo"


# ============================================================================
# detect_and_create_mentions
# ============================================================================

def test_detect_mentions_creates_mention_and_notifies(db_session, make_user, make_post):
    creator = make_user(username="alice")
    mentioned = make_user(username="bobby")
    post = make_post(creator)

    with patch("services.post_service.notification_service.notify") as mock_notify:
        result = post_service.detect_and_create_mentions(
            "hey @bobby check this out", creator.id, "post", post.id
        )

    assert result == [mentioned.id]
    assert Mention.query.filter_by(mentioned_user_id=mentioned.id, mentioned_in_id=post.id).count() == 1
    mock_notify.assert_called_once()


def test_detect_mentions_self_mention_excluded(db_session, make_user, make_post):
    creator = make_user(username="alice")
    post = make_post(creator)

    result = post_service.detect_and_create_mentions("hey @alice look", creator.id, "post", post.id)

    assert result == []
    assert Mention.query.count() == 0


def test_detect_mentions_nonexistent_user_no_error(db_session, make_user, make_post):
    creator = make_user(username="alice")
    post = make_post(creator)

    result = post_service.detect_and_create_mentions("hey @ghostuser look", creator.id, "post", post.id)

    assert result == []


def test_detect_mentions_duplicate_within_same_call_deduped_via_autoflush(db_session, make_user, make_post):
    """Same @username appears twice in the text within ONE call. Verified
    empirically (this test's first version assumed the opposite and was
    wrong): SQLAlchemy's default autoflush means the second dedup query
    sees the first match's still-pending INSERT before it's explicitly
    flushed or committed, so only ONE Mention row is created despite two
    textual occurrences -- not two, as a naive "nothing flushed yet"
    assumption would predict."""
    creator = make_user(username="alice")
    mentioned = make_user(username="bobby")
    post = make_post(creator)

    with patch("services.post_service.notification_service.notify"):
        result = post_service.detect_and_create_mentions(
            "@bobby are you there? cc @bobby", creator.id, "post", post.id
        )

    assert len(result) == 1
    assert Mention.query.filter_by(mentioned_user_id=mentioned.id, mentioned_in_id=post.id).count() == 1


def test_detect_mentions_username_length_bounds(db_session, make_user, make_post):
    creator = make_user(username="alice")
    # 2-char username is below the {3,20} regex range -- not matched at all.
    short_user = make_user(username="bo")
    post = make_post(creator)

    result = post_service.detect_and_create_mentions("hey @bo there", creator.id, "post", post.id)
    assert result == []


def test_detect_mentions_empty_text_returns_empty_no_queries(db_session, make_user):
    creator = make_user()
    assert post_service.detect_and_create_mentions("", creator.id, "post", 1) == []
    assert post_service.detect_and_create_mentions(None, creator.id, "post", 1) == []


# ============================================================================
# check_spam
# ============================================================================

def test_check_spam_post_below_threshold(db_session, make_user, make_post):
    user = make_user()
    for _ in range(9):
        make_post(user, posted_at=datetime.datetime.utcnow())
    is_spam, reason = post_service.check_spam(user.id, "post")
    assert is_spam is False


def test_check_spam_post_at_threshold_flagged(db_session, make_user, make_post):
    user = make_user()
    for _ in range(10):
        make_post(user, posted_at=datetime.datetime.utcnow())
    is_spam, reason = post_service.check_spam(user.id, "post")
    assert is_spam is True
    assert reason == "Too many posts in short time"


def test_check_spam_post_outside_window_not_counted(db_session, make_user, make_post):
    user = make_user()
    old_time = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
    for _ in range(9):
        make_post(user, posted_at=datetime.datetime.utcnow())
    make_post(user, posted_at=old_time)  # 10th post, but outside the 1h window

    is_spam, reason = post_service.check_spam(user.id, "post")
    assert is_spam is False


def test_check_spam_comment_threshold_is_30_not_10(db_session, make_user, make_post, make_comment):
    user = make_user()
    post = make_post(user)
    for _ in range(29):
        make_comment(post, user, posted_at=datetime.datetime.utcnow())
    assert post_service.check_spam(user.id, "comment")[0] is False

    make_comment(post, user, posted_at=datetime.datetime.utcnow())  # 30th
    assert post_service.check_spam(user.id, "comment")[0] is True


def test_check_spam_unrecognized_content_type_no_check(db_session, make_user):
    user = make_user()
    is_spam, reason = post_service.check_spam(user.id, "thread_message")
    assert is_spam is False
    assert reason is None


# ============================================================================
# update_user_activity
# ============================================================================

def test_update_user_activity_creates_row_with_zeroed_counters(db_session, make_user):
    user = make_user()
    activity = post_service.update_user_activity(user.id, "post")
    assert activity.posts_created == 1
    assert activity.activity_score == 5
    assert activity.comments_created == 0


def test_update_user_activity_post_increments_correctly(db_session, make_user):
    user = make_user()
    post_service.update_user_activity(user.id, "post")
    activity = post_service.update_user_activity(user.id, "post")
    assert activity.posts_created == 2
    assert activity.activity_score == 10


def test_update_user_activity_comment_increments_correctly(db_session, make_user):
    user = make_user()
    activity = post_service.update_user_activity(user.id, "comment")
    assert activity.comments_created == 1
    assert activity.activity_score == 2


def test_update_user_activity_unrecognized_type_row_created_no_counter_bump(db_session, make_user):
    user = make_user()
    activity = post_service.update_user_activity(user.id, "login")
    assert activity.posts_created == 0
    assert activity.comments_created == 0
    assert activity.activity_score == 0


# ============================================================================
# check_helpful_milestones — see module docstring re: currently-dead code path
# ============================================================================

def test_check_helpful_milestones_exactly_10_awards_contributor_badge(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user)
    for _ in range(10):
        db_session.add(PostReaction(post_id=post.id, student_id=make_user().id, reaction_type="helpful"))
    db_session.flush()

    with patch("services.post_service.badge_service.check_and_award_badge") as mock_award:
        post_service.check_helpful_milestones(user.id)

    mock_award.assert_called_once_with(user.id, "Helpful Contributor")


def test_check_helpful_milestones_exactly_50_awards_hero_badge(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user)
    for _ in range(50):
        db_session.add(PostReaction(post_id=post.id, student_id=make_user().id, reaction_type="helpful"))
    db_session.flush()

    with patch("services.post_service.badge_service.check_and_award_badge") as mock_award:
        post_service.check_helpful_milestones(user.id)

    mock_award.assert_called_once_with(user.id, "Helpful Hero")


def test_check_helpful_milestones_past_threshold_no_retrigger(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user)
    for _ in range(11):
        db_session.add(PostReaction(post_id=post.id, student_id=make_user().id, reaction_type="helpful"))
    db_session.flush()

    with patch("services.post_service.badge_service.check_and_award_badge") as mock_award:
        post_service.check_helpful_milestones(user.id)

    mock_award.assert_not_called()


def test_check_helpful_milestones_nonexistent_user_noop(db_session):
    with patch("services.post_service.badge_service.check_and_award_badge") as mock_award:
        post_service.check_helpful_milestones(999999)
    mock_award.assert_not_called()


# ============================================================================
# popular_tags — cache-slicing correctness
# ============================================================================

def test_popular_tags_caches_full_uncapped_list(db_session, make_user, make_post, fakeredis_client):
    user = make_user()
    make_post(user, tags=["python", "python", "flask"])
    make_post(user, tags=["python"])

    result = post_service.popular_tags(limit=1)
    assert len(result) == 1
    assert result[0]["tag"] == "python"
    assert result[0]["count"] == 3


def test_popular_tags_different_limit_on_cache_hit_reslices_not_stale(
    db_session, make_user, make_post, fakeredis_client
):
    """Regression test for the documented bug this design specifically
    fixes: a cache hit with a DIFFERENT limit than a previous caller used
    must still return the correctly capped result, not the first
    caller's stale limit."""
    user = make_user()
    make_post(user, tags=["a", "a", "a"])
    make_post(user, tags=["b", "b"])
    make_post(user, tags=["c"])

    first = post_service.popular_tags(limit=1)
    assert len(first) == 1  # only "a"

    # Delete underlying posts so a cache MISS would recompute to an empty
    # list -- makes a stale/wrong-limit bug trivially distinguishable
    # from a correct re-slice of the cached full result.
    from models import Post
    Post.query.delete()
    db_session.commit()

    second = post_service.popular_tags(limit=3)
    assert len(second) == 3  # served from cache, correctly re-sliced to 3
    assert [t["tag"] for t in second] == ["a", "b", "c"]
