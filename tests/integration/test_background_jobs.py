"""
tests/integration/test_background_jobs.py

Covers INTEGRATION_TEST_IMPLEMENTATION_PLAN.md §3.7 + Q5's confirmed
default: job BODIES are called directly against the real (SQLite) DB
inside a Flask app context; job ENQUEUEING is verified by mocking
.enqueue and asserting call args — no real worker process, no real
Redis-backed RQ execution.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


class TestMaintenanceJobBodies:
    def test_cleanup_expired_activity_feed_deletes_only_expired_rows(
        self, db_session, make_user, make_activity_feed_row
    ):
        from services.jobs.maintenance_jobs import cleanup_expired_activity_feed_job
        from models import ActivityFeed

        user = make_user(status="approved")
        now = datetime.datetime.utcnow()

        expired = make_activity_feed_row(
            user, expires_at=now - datetime.timedelta(hours=1)
        )
        still_valid = make_activity_feed_row(
            user, expires_at=now + datetime.timedelta(hours=1)
        )

        expired_id = expired.id
        still_valid_id = still_valid.id

        result = cleanup_expired_activity_feed_job()

        assert result["rows_deleted"] == 1

        # FIX: cleanup_expired_activity_feed_job() bulk-deletes via
        # `.delete(synchronize_session=False)` (see maintenance_jobs.py's
        # own comment on why — batched deletes, not a per-row ORM
        # cascade). synchronize_session=False deliberately skips
        # expiring/evicting matching objects already loaded in this
        # session's identity map, so the `expired` instance created by
        # make_activity_feed_row() above is left in a stale "looks alive"
        # state in the identity map. Query.get(expired.id) checks the
        # identity map before hitting the database, finds that stale
        # object, and Query.get()'s own refresh-on-access path then
        # raises ObjectDeletedError instead of returning None (an
        # arguably-surprising interaction between bulk deletes and
        # Query.get() as opposed to a fresh Query.filter_by(...).first(),
        # which always issues a real SELECT and simply returns None for
        # zero rows).
        #
        # Expire the identity map for these specific rows first so the
        # subsequent lookups issue real SELECTs against the database
        # instead of consulting stale in-memory objects — this is what
        # the test actually intends to assert ("the row is gone from the
        # database"), not "this specific Python object instance is
        # marked deleted".
        db_session.expire(expired)
        db_session.expire(still_valid)

        assert ActivityFeed.query.get(expired_id) is None
        assert ActivityFeed.query.get(still_valid_id) is not None

    def test_cleanup_is_idempotent_on_repeated_run(
        self, db_session, make_user, make_activity_feed_row
    ):
        from services.jobs.maintenance_jobs import cleanup_expired_activity_feed_job

        user = make_user(status="approved")
        make_activity_feed_row(
            user, expires_at=datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        )

        first = cleanup_expired_activity_feed_job()
        assert first["rows_deleted"] == 1

        second = cleanup_expired_activity_feed_job()
        assert second["rows_deleted"] == 0

    def test_alert_stale_ai_conversations_is_read_only(
        self, db_session, make_user, make_ai_conversation
    ):
        from services.jobs.maintenance_jobs import alert_stale_ai_conversations_job
        from models import AIConversation

        user = make_user(status="approved")
        stale = make_ai_conversation(
            user,
            is_archived=True,
            total_messages=42,
            last_message_at=datetime.datetime.utcnow() - datetime.timedelta(days=200),
        )
        fresh = make_ai_conversation(
            user,
            is_archived=True,
            total_messages=5,
            last_message_at=datetime.datetime.utcnow() - datetime.timedelta(days=1),
        )

        before_count = AIConversation.query.count()
        result = alert_stale_ai_conversations_job()
        after_count = AIConversation.query.count()

        assert after_count == before_count  # never deletes
        assert result["stale_count"] >= 1

        # The fresh (non-stale) conversation must be untouched/unaffected.
        still_there = AIConversation.query.get(fresh.id)
        assert still_there is not None
        assert still_there.is_archived is True


class TestEmailJobEnqueueing:
    def test_password_reset_enqueues_job_not_synchronous_send(
        self, client, make_user, monkeypatch
    ):
        """
        POST /student/validate-user should enqueue a real job (via
        services/job_queue.py::email_queue) rather than blocking on a
        synchronous SMTP send — confirmed by mocking .enqueue and
        asserting it was actually invoked with the right recipient.
        """
        from services.job_queue import email_queue

        user = make_user(status="approved")

        calls = []

        def _fake_enqueue(func, **kwargs):
            calls.append(kwargs)
            class _FakeJob:
                id = "fake-id"
            return _FakeJob()

        monkeypatch.setattr(email_queue, "enqueue", _fake_enqueue)

        resp = client.post("/student/validate-user", json={"data": user.email})
        assert resp.status_code == 200, resp.get_json()
        assert len(calls) == 1
        assert calls[0]["to_email"] == user.email

    def test_send_email_job_propagates_mail_send_failure(self, app, monkeypatch):
        """
        services/jobs/email_jobs.py::send_email_job deliberately does NOT
        catch exceptions from mail.send() — this is what makes RQ's own
        retry mechanism engage. A regression that added a try/except here
        would silently defeat retries.
        """
        from services.jobs.email_jobs import send_email_job
        from extensions import mail

        def _raise(*a, **kw):
            raise RuntimeError("simulated SMTP failure")

        monkeypatch.setattr(mail, "send", _raise)

        with pytest.raises(RuntimeError):
            send_email_job(
                to_email="fail@example.com",
                subject="Test",
                html_content="<p>hi</p>",
            )


class TestSchedulerLockedJobs:
    def test_run_locked_skips_work_when_lock_not_acquired(self, fakeredis_client):
        """
        scheduler.py::_run_locked must not call work_fn() when the
        distributed lock is already held by "another instance" — simulated
        here by pre-acquiring the lock key directly in fakeredis before
        calling _run_locked.
        """
        from scheduler import _run_locked

        lock_key = "sh:1:sched:lock:test_job"
        fakeredis_client.set(lock_key, "someone-else-owns-this", nx=True, ex=600)

        called = {"count": 0}

        def _work():
            called["count"] += 1

        _run_locked("test_job", _work)

        assert called["count"] == 0

    def test_run_locked_calls_work_fn_when_lock_acquired(self, fakeredis_client):
        from scheduler import _run_locked

        called = {"count": 0}

        def _work():
            called["count"] += 1

        _run_locked("test_job_2", _work)

        assert called["count"] == 1

