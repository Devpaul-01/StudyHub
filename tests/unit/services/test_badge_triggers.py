"""Tests for services/badge_triggers.py and services/jobs/badge_jobs.py."""

from unittest.mock import MagicMock, patch

import pytest

from extensions import db
from models import UserBadge
from services import badge_triggers, job_queue
from services.jobs import badge_jobs


def test_badge_queue_name_and_timeout():
    assert job_queue.badge_queue.name == "sh:1:rq:badges"
    assert job_queue.badge_queue._default_timeout == job_queue.BADGE_JOB_TIMEOUT_SECONDS


def test_schedule_enqueues_only_after_commit(db_session):
    fake_queue = MagicMock()
    with patch.object(job_queue, "badge_queue", fake_queue):
        badge_triggers.schedule_badge_check(42)
        fake_queue.enqueue.assert_not_called()
        db.session.commit()
    fake_queue.enqueue.assert_called_once()
    assert fake_queue.enqueue.call_args.args[0] is badge_jobs.check_badges_job
    assert fake_queue.enqueue.call_args.kwargs["user_id"] == 42


def test_schedule_is_dropped_on_rollback(db_session):
    fake_queue = MagicMock()
    with patch.object(job_queue, "badge_queue", fake_queue):
        badge_triggers.schedule_badge_check(42)
        db.session.rollback()
        db.session.commit()
    fake_queue.enqueue.assert_not_called()


def test_schedule_ignores_missing_user(db_session):
    fake_queue = MagicMock()
    with patch.object(job_queue, "badge_queue", fake_queue):
        badge_triggers.schedule_badge_check(None)
        db.session.commit()
    fake_queue.enqueue.assert_not_called()


def test_enqueue_failure_is_swallowed(db_session):
    fake_queue = MagicMock()
    fake_queue.enqueue.side_effect = ConnectionError("redis down")
    with patch.object(job_queue, "badge_queue", fake_queue):
        badge_triggers.schedule_badge_check(1)
        db.session.commit()                      # must not raise


def test_job_awards_badges_and_is_idempotent(db_session, seeded_badges, make_user):
    user = make_user(total_posts=1)
    with patch("services.jobs.badge_jobs.record_live_activity") as activity, \
         patch("services.badge_service.notification_service.notify_badge_earned"):
        first = badge_jobs.check_badges_job(user_id=user.id)
        second = badge_jobs.check_badges_job(user_id=user.id)

    assert first >= 1 and second == 0
    assert UserBadge.query.filter_by(user_id=user.id).count() == first
    assert activity.call_count == first


def test_job_reraises_so_rq_retry_engages(db_session, make_user):
    user = make_user()
    with patch("services.jobs.badge_jobs.badge_service.check_all_badges_for_user",
               side_effect=RuntimeError("db blip")):
        with pytest.raises(RuntimeError):
            badge_jobs.check_badges_job(user_id=user.id)
