"""
Tests for services/jobs/maintenance_jobs.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.8. These run unattended on a
schedule against production data -- idempotency and "genuinely
read-only" are the properties worth locking down with a test, not just
the return-value shape.
"""

import datetime

from services.jobs import maintenance_jobs
from models import ActivityFeed, AIConversation


def test_cleanup_no_expired_rows(db_session):
    result = maintenance_jobs.cleanup_expired_activity_feed_job()
    assert result == {"rows_deleted": 0, "batches_run": 0}


def test_cleanup_deletes_only_expired_rows(db_session, make_user, make_activity_feed_row):
    user = make_user()
    now = datetime.datetime.utcnow()
    expired = make_activity_feed_row(user, expires_at=now - datetime.timedelta(hours=1))
    not_expired = make_activity_feed_row(user, expires_at=now + datetime.timedelta(hours=1))
    # Capture plain ints before calling the function under test -- it
    # commits internally, which expires ORM objects; accessing .id on the
    # now-deleted `expired` object afterward would trigger a reload
    # against a row that's genuinely gone (ObjectDeletedError).
    expired_id, not_expired_id = expired.id, not_expired.id

    result = maintenance_jobs.cleanup_expired_activity_feed_job()

    assert result["rows_deleted"] == 1
    assert result["batches_run"] == 1
    assert ActivityFeed.query.get(expired_id) is None
    assert ActivityFeed.query.get(not_expired_id) is not None


def test_cleanup_multiple_batches(db_session, make_user, make_activity_feed_row, monkeypatch):
    monkeypatch.setattr(maintenance_jobs, "ACTIVITY_FEED_BATCH_SIZE", 5)
    user = make_user()
    now = datetime.datetime.utcnow()
    for _ in range(12):
        make_activity_feed_row(user, expires_at=now - datetime.timedelta(hours=1))

    result = maintenance_jobs.cleanup_expired_activity_feed_job()

    assert result["rows_deleted"] == 12
    assert result["batches_run"] == 3  # 5 + 5 + 2
    assert ActivityFeed.query.count() == 0


def test_cleanup_is_idempotent_on_rerun(db_session, make_user, make_activity_feed_row):
    user = make_user()
    now = datetime.datetime.utcnow()
    make_activity_feed_row(user, expires_at=now - datetime.timedelta(hours=1))

    first = maintenance_jobs.cleanup_expired_activity_feed_job()
    second = maintenance_jobs.cleanup_expired_activity_feed_job()

    assert first["rows_deleted"] == 1
    assert second == {"rows_deleted": 0, "batches_run": 0}


def test_alert_stale_conversations_no_archived_rows(db_session):
    result = maintenance_jobs.alert_stale_ai_conversations_job()
    assert result == {"stale_count": 0, "approx_total_messages": 0}


def test_alert_stale_conversations_only_counts_matching_both_conditions(
    db_session, make_user, make_ai_conversation
):
    user = make_user()
    threshold_days = maintenance_jobs.STALE_CONVERSATION_THRESHOLD_DAYS
    old_cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=threshold_days + 10)
    recent_cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=1)

    # Archived AND old -> counts.
    make_ai_conversation(user, is_archived=True, last_message_at=old_cutoff, total_messages=10)
    # Archived but recent -> does not count.
    make_ai_conversation(user, is_archived=True, last_message_at=recent_cutoff, total_messages=5)
    # Old but not archived -> does not count.
    make_ai_conversation(user, is_archived=False, last_message_at=old_cutoff, total_messages=7)

    result = maintenance_jobs.alert_stale_ai_conversations_job()

    assert result["stale_count"] == 1
    assert result["approx_total_messages"] == 10


def test_alert_stale_conversations_is_read_only(db_session, make_user, make_ai_conversation):
    user = make_user()
    old_cutoff = datetime.datetime.utcnow() - datetime.timedelta(
        days=maintenance_jobs.STALE_CONVERSATION_THRESHOLD_DAYS + 10
    )
    conv = make_ai_conversation(user, is_archived=True, last_message_at=old_cutoff, total_messages=3)

    before_count = AIConversation.query.count()
    maintenance_jobs.alert_stale_ai_conversations_job()
    after_count = AIConversation.query.count()

    assert before_count == after_count == 1
    refreshed = AIConversation.query.get(conv.id)
    assert refreshed.is_archived is True  # unchanged
    assert refreshed.total_messages == 3  # unchanged
