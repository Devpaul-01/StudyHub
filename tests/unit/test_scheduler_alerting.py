"""Alerting for low-frequency scheduler jobs (weekly/monthly snapshots)."""
import datetime

import freezegun
import pytest

import scheduler
from services import cache_service

JOB = "weekly_leaderboard_snapshot"
WEEK = scheduler.WEEKLY_SECONDS


@pytest.fixture()
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(scheduler, "_alert", lambda message: sent.append(message))
    return sent


def _hold_lock(fakeredis_client):
    fakeredis_client.set(f"sh:1:sched:lock:{JOB}", "other-instance")


def test_success_records_last_run_and_clears_retry_counter(fakeredis_client, alerts):
    scheduler._retry_attempts[JOB] = 2
    scheduler._run_locked(JOB, lambda: None, cadence_seconds=WEEK)

    assert isinstance(cache_service.get(f"sh:1:sched:lastrun:{JOB}"), float)
    assert JOB not in scheduler._retry_attempts


def test_normal_weekly_skips_while_another_instance_runs_never_alert(fakeredis_client, alerts):
    with freezegun.freeze_time("2026-11-01 00:05:00") as clock:
        for _ in range(10):                       # ten weeks; the other instance wins each time
            fakeredis_client.delete(f"sh:1:sched:lock:{JOB}")
            scheduler._run_locked(JOB, lambda: None, cadence_seconds=WEEK)       # winner: records success
            for _ in range(3):                                                    # three losing instances
                _hold_lock(fakeredis_client)
                scheduler._run_locked(JOB, lambda: None, cadence_seconds=WEEK)
            clock.tick(datetime.timedelta(days=7))
    assert alerts == []


def test_alerts_once_when_the_job_has_not_succeeded_for_two_cadences(fakeredis_client, alerts):
    with freezegun.freeze_time("2026-11-01 00:05:00") as clock:
        scheduler._run_locked(JOB, lambda: None, cadence_seconds=WEEK)           # last success
        for week in range(1, 5):                                                  # nobody succeeds afterwards
            clock.tick(datetime.timedelta(days=7))
            _hold_lock(fakeredis_client)                                          # lock stuck by a dead owner
            scheduler._run_locked(JOB, lambda: None, cadence_seconds=WEEK)

    assert len(alerts) == 1
    assert "SCHED_JOB_OVERDUE" in alerts[0] and JOB in alerts[0]


def test_redis_blocked_run_alerts_immediately_and_schedules_a_retry(monkeypatch, alerts):
    from services import distributed_lock

    class Down:
        def set(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(distributed_lock, "redis_client", Down())
    scheduled = []
    monkeypatch.setattr(scheduler.scheduler, "add_job", lambda **kw: scheduled.append(kw))
    ran = []
    scheduler._retry_attempts.pop(JOB, None)

    scheduler._run_locked(JOB, lambda: ran.append(1), cadence_seconds=WEEK, retry_fn=lambda: None)

    assert ran == []
    assert len(alerts) == 1 and "SCHED_JOB_BLOCKED_REDIS" in alerts[0]
    assert len(scheduled) == 1 and scheduled[0]["id"] == f"{JOB}:retry" and scheduled[0]["trigger"] == "date"
    scheduler._retry_attempts.pop(JOB, None)


def test_retries_are_bounded(monkeypatch, alerts):
    from services import distributed_lock

    class Down:
        def set(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(distributed_lock, "redis_client", Down())
    scheduled = []
    monkeypatch.setattr(scheduler.scheduler, "add_job", lambda **kw: scheduled.append(kw))
    scheduler._retry_attempts.pop(JOB, None)

    for _ in range(scheduler._MAX_REDIS_RETRIES + 3):
        scheduler._run_locked(JOB, lambda: None, cadence_seconds=WEEK, retry_fn=lambda: None)

    assert len(scheduled) == scheduler._MAX_REDIS_RETRIES
    scheduler._retry_attempts.pop(JOB, None)


def test_jobs_without_a_cadence_keep_the_original_skip_counter(fakeredis_client):
    fakeredis_client.set("sh:1:sched:lock:legacy_job", "other")
    scheduler._run_locked("legacy_job", lambda: None)
    assert cache_service.get("sh:1:sched:skipcount:legacy_job") == 1
