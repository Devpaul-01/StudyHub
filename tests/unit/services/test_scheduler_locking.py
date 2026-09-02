"""
Tests for scheduler.py::_run_locked.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.9: DistributedLock itself is
mocked here (this test is specifically about _run_locked's own
branching logic, already covered separately in
test_distributed_lock.py), while the skip-counter persistence uses real
fakeredis-backed cache_service, since that part IS real Redis-backed
state worth verifying.

Per SUPPLEMENTARY_AGENT_INSTRUCTIONS.md §2: scheduler.py is the one
P1 §8 file where the full source was actually supplied (confirmed
directly), so _run_locked is tested against the real function rather
than reconstructed from the plan's inline quote.
"""

from unittest.mock import Mock

import scheduler


class _FakeLock:
    """Stands in for DistributedLock as a context manager, with a
    controllable .acquired flag -- matches the plan's own guidance to
    mock the lock class here rather than drive real Redis contention."""

    def __init__(self, acquired):
        self._acquired = acquired
        self.acquired = acquired

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_run_locked_acquired_calls_work_fn_and_resets_skip_counter(fakeredis_client, monkeypatch):
    monkeypatch.setattr(scheduler, "DistributedLock", lambda *a, **kw: _FakeLock(acquired=True))
    work_fn = Mock()

    scheduler._run_locked("test_job", work_fn)

    work_fn.assert_called_once()
    from services import cache_service
    assert cache_service.get("sh:1:sched:skipcount:test_job") == 0


def test_run_locked_not_acquired_skips_work_fn_and_increments_counter(fakeredis_client, monkeypatch):
    monkeypatch.setattr(scheduler, "DistributedLock", lambda *a, **kw: _FakeLock(acquired=False))
    work_fn = Mock()

    scheduler._run_locked("test_job_2", work_fn)

    work_fn.assert_not_called()
    from services import cache_service
    assert cache_service.get("sh:1:sched:skipcount:test_job_2") == 1


def test_run_locked_repeated_skips_accumulate(fakeredis_client, monkeypatch):
    monkeypatch.setattr(scheduler, "DistributedLock", lambda *a, **kw: _FakeLock(acquired=False))
    work_fn = Mock()

    for _ in range(3):
        scheduler._run_locked("test_job_3", work_fn)

    from services import cache_service
    assert cache_service.get("sh:1:sched:skipcount:test_job_3") == 3
    work_fn.assert_not_called()


def test_run_locked_alert_threshold_logs_error(fakeredis_client, monkeypatch, caplog):
    monkeypatch.setattr(scheduler, "DistributedLock", lambda *a, **kw: _FakeLock(acquired=False))
    work_fn = Mock()

    import logging
    with caplog.at_level(logging.ERROR, logger="scheduler"):
        for _ in range(scheduler._CONSECUTIVE_SKIP_ALERT_THRESHOLD):
            scheduler._run_locked("test_job_4", work_fn)

    assert any("SCHED_JOB_REPEATEDLY_SKIPPED" in record.message for record in caplog.records)


def test_run_locked_successful_run_resets_counter_after_prior_skips(fakeredis_client, monkeypatch):
    """A run that successfully acquires the lock resets the skip counter
    to 0, even if it had been building up from prior skips."""
    work_fn = Mock()

    monkeypatch.setattr(scheduler, "DistributedLock", lambda *a, **kw: _FakeLock(acquired=False))
    for _ in range(3):
        scheduler._run_locked("test_job_5", work_fn)

    from services import cache_service
    assert cache_service.get("sh:1:sched:skipcount:test_job_5") == 3

    monkeypatch.setattr(scheduler, "DistributedLock", lambda *a, **kw: _FakeLock(acquired=True))
    scheduler._run_locked("test_job_5", work_fn)

    assert cache_service.get("sh:1:sched:skipcount:test_job_5") == 0
    work_fn.assert_called_once()
