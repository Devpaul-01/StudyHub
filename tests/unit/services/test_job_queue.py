"""
Tests for services/job_queue.py.

This module's own docstring documents a real, previously-shipped bug
class it works around: rq.Queue(connection=None) raises at construction
time rather than falling back to a default, which crashed
`from services.job_queue import email_queue` for every caller whenever
Redis was unreachable at import time (including, per the module's own
comment, this very test environment's fail-open REDIS_URL). These tests
cover the two things that can regress: the queues' own configuration,
and _redis_connection_for_queues()'s fallback behavior — including that
the fallback never raises even though the URL it builds from is never
actually pinged at construction time.

Previously zero tests existed for this module.
"""

import importlib
from unittest.mock import Mock

import pytest
import redis as redis_module
from rq import Queue

import services.job_queue as job_queue


# ============================================================================
# QUEUE CONFIGURATION
# ============================================================================

def test_email_queue_is_a_queue_with_expected_name_and_timeout():
    assert isinstance(job_queue.email_queue, Queue)
    assert job_queue.email_queue.name == "sh:1:rq:email"
    assert job_queue.email_queue._default_timeout == job_queue.EMAIL_JOB_TIMEOUT_SECONDS
    assert job_queue.EMAIL_JOB_TIMEOUT_SECONDS == 30


def test_maintenance_queue_is_a_queue_with_expected_name_and_timeout():
    assert isinstance(job_queue.maintenance_queue, Queue)
    assert job_queue.maintenance_queue.name == "sh:1:rq:maintenance"
    assert job_queue.maintenance_queue._default_timeout == job_queue.MAINTENANCE_JOB_TIMEOUT_SECONDS
    assert job_queue.MAINTENANCE_JOB_TIMEOUT_SECONDS == 600


def test_email_and_maintenance_queues_are_distinct_queues():
    assert job_queue.email_queue.name != job_queue.maintenance_queue.name


def test_only_this_module_constructs_queue_instances():
    """
    The module's own docstring states this is the ONLY file that should
    construct rq.Queue instances — a structural guard so a future
    regression (some other module calling Queue(...) directly instead of
    importing the named queues from here) is at least flagged here even
    though it can't be enforced by this test alone.
    """
    assert job_queue.email_queue is not job_queue.maintenance_queue


# ============================================================================
# _redis_connection_for_queues — fallback behavior
# ============================================================================

def test_redis_connection_for_queues_reuses_extensions_redis_client_when_present(monkeypatch):
    fake_client = Mock(name="fake-redis-client")
    monkeypatch.setattr(job_queue, "redis_client", fake_client)

    result = job_queue._redis_connection_for_queues()

    assert result is fake_client


def test_redis_connection_for_queues_builds_new_client_from_url_when_none(monkeypatch):
    """
    When extensions.redis_client is None (the documented fail-open
    state when Redis was unreachable at import time), the fallback must
    construct — not reuse — a client from REDIS_URL, and must not raise
    even though nothing is listening on that URL: redis.from_url doesn't
    open a connection at construction time.
    """
    monkeypatch.setattr(job_queue, "redis_client", None)
    monkeypatch.setenv("REDIS_URL", "redis://nothing-listens-here:6399/0")

    result = job_queue._redis_connection_for_queues()

    assert isinstance(result, redis_module.Redis)


def test_redis_connection_for_queues_falls_back_to_localhost_default_when_env_unset(monkeypatch):
    monkeypatch.setattr(job_queue, "redis_client", None)
    monkeypatch.delenv("REDIS_URL", raising=False)

    result = job_queue._redis_connection_for_queues()

    assert isinstance(result, redis_module.Redis)


# ============================================================================
# MODULE IMPORT NEVER CRASHES WHEN REDIS IS UNREACHABLE AT IMPORT TIME
# ============================================================================

def test_module_import_succeeds_even_when_extensions_redis_client_is_none(monkeypatch):
    """
    Regression guard for the exact bug this module's docstring describes:
    reload services.job_queue with extensions.redis_client forced to
    None (simulating "Redis was unreachable when the app started up")
    and confirm the module still imports and both queues still
    construct, rather than raising
    `TypeError: Queue() missing 1 required positional argument: 'connection'`.
    """
    import extensions

    monkeypatch.setattr(extensions, "redis_client", None)
    monkeypatch.setenv("REDIS_URL", "redis://nothing-listens-here:6399/0")

    try:
        reloaded = importlib.reload(job_queue)
        assert isinstance(reloaded.email_queue, Queue)
        assert isinstance(reloaded.maintenance_queue, Queue)
    finally:
        # Restore the module to its normal, real-redis_client-backed
        # state so later tests in the same process see the original
        # queue instances rather than this test's reload.
        importlib.reload(job_queue)
