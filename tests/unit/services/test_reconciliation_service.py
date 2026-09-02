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
    user = make_user()
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
    user = make_user()
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
