"""
Tests for services/reconciliation_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.7. The single most important
behavioral distinction: member_count drift is flagged but the DB column
is NEVER touched (auto_corrected=False and no mutation), while
message_count drift IS both flagged and corrected. A regression that
accidentally started auto-correcting the capacity-gating counter is
exactly what these tests exist to catch.
"""

import datetime

from services import reconciliation_service as recon
from models import Comment, Bookmark, PostView, PostReaction, ThreadMember, ThreadMessage


def test_reconcile_post_counters_no_drift_not_flagged(db_session, make_user, make_post):
    user = make_user(total_posts=1)   # consistent with the one post below
    make_post(user, comments_count=0, bookmark_count=0, views_count=0, positive_reactions_count=0)

    report = recon.reconcile_denormalized_counts()

    assert report.has_drift() is False


def test_reconcile_post_counters_stale_value_corrected_in_db(db_session, make_user, make_post, make_comment):
    user = make_user()
    post = make_post(user, comments_count=0)  # stale: says 0
    make_comment(post, user)
    make_comment(post, user)  # 2 real, uncounted comments

    report = recon.reconcile_denormalized_counts()

    assert report.corrected_count >= 1
    from models import Post
    refreshed = Post.query.get(post.id)
    assert refreshed.comments_count == 2  # actually corrected in the DB


def test_reconcile_positive_reactions_only_counts_like_type(db_session, make_user, make_post):
    user = make_user()
    post = make_post(user, positive_reactions_count=0)
    other1, other2 = make_user(), make_user()
    db_session.add(PostReaction(post_id=post.id, student_id=other1.id, reaction_type="like"))
    db_session.add(PostReaction(post_id=post.id, student_id=other2.id, reaction_type="helpful"))  # must NOT count
    db_session.flush()

    recon.reconcile_denormalized_counts()

    from models import Post
    refreshed = Post.query.get(post.id)
    assert refreshed.positive_reactions_count == 1  # only the "like" reaction


def test_reconcile_thread_member_count_drift_alert_only_never_auto_corrected(
    db_session, make_user, make_thread, make_thread_member
):
    """The core behavioral distinction this module exists to enforce."""
    creator = make_user()
    other = make_user()
    thread = make_thread(creator, member_count=99)  # deliberately wrong stored value
    make_thread_member(thread, creator)
    make_thread_member(thread, other)
    # 2 real ThreadMember rows, but stored member_count claims 99

    report = recon.reconcile_denormalized_counts()

    member_drifts = [d for d in report.drifts_found if d.model == "Thread" and d.column == "member_count"]
    assert len(member_drifts) == 1
    assert member_drifts[0].auto_corrected is False

    from models import Thread
    refreshed = Thread.query.get(thread.id)
    assert refreshed.member_count == 99  # UNCHANGED -- never auto-corrected


def test_reconcile_thread_message_count_drift_is_auto_corrected(
    db_session, make_user, make_thread
):
    creator = make_user()
    thread = make_thread(creator, message_count=50)  # deliberately wrong
    db_session.add(ThreadMessage(thread_id=thread.id, sender_id=creator.id, text_content="hi"))
    db_session.add(ThreadMessage(thread_id=thread.id, sender_id=creator.id, text_content="hi2"))
    db_session.flush()

    report = recon.reconcile_denormalized_counts()

    message_drifts = [d for d in report.drifts_found if d.model == "Thread" and d.column == "message_count"]
    assert len(message_drifts) == 1
    assert message_drifts[0].auto_corrected is True

    from models import Thread
    refreshed = Thread.query.get(thread.id)
    assert refreshed.message_count == 2  # corrected


def test_reconcile_thread_message_count_excludes_deleted_messages(db_session, make_user, make_thread):
    creator = make_user()
    thread = make_thread(creator, message_count=0)
    db_session.add(ThreadMessage(thread_id=thread.id, sender_id=creator.id, text_content="live"))
    db_session.add(ThreadMessage(thread_id=thread.id, sender_id=creator.id, text_content="gone", is_deleted=True))
    db_session.flush()

    recon.reconcile_denormalized_counts()

    from models import Thread
    refreshed = Thread.query.get(thread.id)
    assert refreshed.message_count == 1  # only the non-deleted one


def test_reconcile_denormalized_counts_no_drift_anywhere(db_session, make_user, make_post, make_thread):
    user = make_user(total_posts=1)
    make_post(user, comments_count=0, bookmark_count=0, views_count=0, positive_reactions_count=0)
    # make_thread's factory does not insert a backing ThreadMember row --
    # member_count=0 matches that (zero real members, zero stored),
    # unlike member_count=1 which would itself be a drift (stored=1,
    # actual=0) and defeat the point of this "genuinely clean" case.
    make_thread(user, member_count=0, message_count=0)

    report = recon.reconcile_denormalized_counts()

    assert report.has_drift() is False
    assert report.corrected_count == 0
    assert report.alerted_only_count == 0
    assert report.counters_checked > 0


# ============================================================================
# reputation & badge counters
# ============================================================================

def test_badge_awarded_count_drift_is_auto_corrected(db_session, make_user):
    from models import Badge, UserBadge
    user = make_user()
    badge = Badge(name="B", description="d", icon="i", category="c", rarity="common",
                  criteria={}, awarded_count=5)           # stale: only 1 real award
    db_session.add(badge)
    db_session.flush()
    db_session.add(UserBadge(user_id=user.id, badge_id=badge.id))
    db_session.commit()

    report = recon.reconcile_denormalized_counts()

    drift = [d for d in report.drifts_found if d.model == "Badge" and d.column == "awarded_count"]
    assert len(drift) == 1 and drift[0].auto_corrected
    assert (drift[0].stored_value, drift[0].actual_value) == (5, 1)
    assert Badge.query.get(badge.id).awarded_count == 1


def test_helpful_counters_count_active_marks_only(db_session, make_user, make_post, make_comment):
    from models import CommentHelpfulMark
    author, a, b = make_user(total_posts=1), make_user(), make_user()
    comment = make_comment(make_post(author), author)
    db_session.add_all([
        CommentHelpfulMark(comment_id=comment.id, user_id=a.id, is_active=True),
        CommentHelpfulMark(comment_id=comment.id, user_id=b.id, is_active=False),  # soft-deleted
    ])
    comment.helpful_count = 2           # stale
    author.total_helpful = 2            # stale
    db_session.commit()

    report = recon.reconcile_denormalized_counts()

    assert report.corrected_count >= 2
    db_session.refresh(comment)
    db_session.refresh(author)
    assert comment.helpful_count == 1 and author.total_helpful == 1


def test_reputation_ledger_mismatch_is_alerted_never_corrected(db_session, make_user):
    from models import ReputationHistory
    user = make_user(reputation=120)                    # legacy balance, no history
    db_session.commit()

    report = recon.reconcile_denormalized_counts()

    drift = [d for d in report.drifts_found if d.column == "reputation" and d.row_id == user.id]
    assert len(drift) == 1 and drift[0].auto_corrected is False
    assert (drift[0].stored_value, drift[0].actual_value) == (120, 0)
    db_session.refresh(user)
    assert user.reputation == 120                       # never silently rewritten


def test_reputation_matching_ledger_is_clean(db_session, make_user):
    from services.reputation_service import award_reputation
    user = make_user(reputation=0)
    award_reputation(user.id, "comment_marked_helpful")
    db_session.commit()
    report = recon.reconcile_denormalized_counts()
    assert not [d for d in report.drifts_found if d.column == "reputation"]


def test_total_posts_mismatch_is_alert_only(db_session, make_user, make_post):
    user = make_user(total_posts=5)
    make_post(user)
    db_session.commit()
    report = recon.reconcile_denormalized_counts()
    drift = [d for d in report.drifts_found if d.column == "total_posts"]
    assert len(drift) == 1 and drift[0].auto_corrected is False
    db_session.refresh(user)
    assert user.total_posts == 5
