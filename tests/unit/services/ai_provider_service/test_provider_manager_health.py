"""
Tests for MultiProviderManager's health-tracking logic and
_build_call_queue in services/ai_provider_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.6's own implementation note:
"Tests must construct a FRESH MultiProviderManager() instance per test...
never assert against the real module-level provider_manager directly."
Every test here builds its own manager and sets .providers directly,
bypassing _load_providers()'s env-var reading entirely.
"""

import datetime

import pytest
from freezegun import freeze_time

from services import ai_provider_service
from services.ai_provider_service import MultiProviderManager


def _fake_provider(name, provider_id="groq", supports_vision=False, models=None):
    models = models or ["model-a", "model-b", "model-c"]
    return {
        "name": name,
        "api_key": "fake-key",
        "base_url": "https://fake.example.com/v1",
        "text_model": models[0],
        "vision_model": models[0] if supports_vision else None,
        "supports_vision": supports_vision,
        "type": provider_id,
        "text_model_fallbacks": list(models),
        "vision_model_fallbacks": list(models) if supports_vision else [],
        "_provider_id": provider_id,
        "_vision_models": set(models) if supports_vision else set(),
    }


@pytest.fixture
def fresh_manager():
    """A MultiProviderManager with NO real env-var-derived providers --
    __init__ still calls _load_providers() (harmless: no keys are set in
    the test environment, so it returns []), and this fixture then
    injects a small, controlled provider list directly."""
    mgr = MultiProviderManager()
    mgr.providers = [
        _fake_provider("groq_0", provider_id="groq"),
        _fake_provider("gemini_0", provider_id="gemini", supports_vision=True),
    ]
    return mgr


# ============================================================================
# is_redis_state_enabled
# ============================================================================

def test_is_redis_state_enabled_default_true(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER_REDIS_STATE_ENABLED", raising=False)
    assert MultiProviderManager.is_redis_state_enabled() is True


def test_is_redis_state_enabled_false_case_insensitive(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "FALSE")
    assert MultiProviderManager.is_redis_state_enabled() is False


def test_is_redis_state_enabled_other_values_true(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    assert MultiProviderManager.is_redis_state_enabled() is True


# ============================================================================
# _is_key_cooling — Redis-backed path
# ============================================================================

def test_is_key_cooling_redis_never_marked(fresh_manager, fakeredis_client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    assert fresh_manager._is_key_cooling("groq_0") is False


def test_is_key_cooling_redis_marked_failed(fresh_manager, fakeredis_client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    fresh_manager.mark_provider_failed("groq_0", "simulated failure")
    assert fresh_manager._is_key_cooling("groq_0") is True


# ============================================================================
# _is_key_cooling — in-memory fallback path
# ============================================================================

def test_is_key_cooling_in_memory_never_failed(fresh_manager, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "false")
    assert fresh_manager._is_key_cooling("groq_0") is False


def test_is_key_cooling_in_memory_recently_failed(fresh_manager, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "false")
    with freeze_time("2026-01-01 12:00:00"):
        fresh_manager.mark_provider_failed("groq_0", "fail")
    with freeze_time("2026-01-01 12:05:00"):  # 5 min later, within the 1h cooldown
        assert fresh_manager._is_key_cooling("groq_0") is True


def test_is_key_cooling_in_memory_expired_prunes_entry(fresh_manager, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "false")
    with freeze_time("2026-01-01 12:00:00"):
        fresh_manager.mark_provider_failed("groq_0", "fail")
    with freeze_time("2026-01-01 14:00:00"):  # 2h later, past the 1h cooldown
        assert fresh_manager._is_key_cooling("groq_0") is False
        assert "groq_0" not in fresh_manager.failed_providers


# ============================================================================
# provider-type failure tracking / blacklist
# ============================================================================

def test_provider_type_failure_below_threshold_not_blacklisted(fresh_manager, fakeredis_client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    fresh_manager._record_provider_type_failure("groq")
    fresh_manager._record_provider_type_failure("groq")
    assert fresh_manager._is_provider_type_blacklisted("groq") is False


def test_provider_type_failure_at_threshold_blacklists(fresh_manager, fakeredis_client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    for _ in range(MultiProviderManager.PROVIDER_FAILURE_THRESHOLD):
        fresh_manager._record_provider_type_failure("groq")
    assert fresh_manager._is_provider_type_blacklisted("groq") is True


def test_provider_type_failure_window_sliding(fresh_manager, fakeredis_client, monkeypatch):
    """Failures spread across MORE than the 5-minute window must NOT
    accumulate toward the blacklist threshold, even with the same total
    count as a within-window burst."""
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    window = MultiProviderManager.PROVIDER_FAILURE_WINDOW

    with freeze_time("2026-01-01 12:00:00"):
        fresh_manager._record_provider_type_failure("groq")
    with freeze_time(datetime.datetime(2026, 1, 1, 12, 0, 0) + datetime.timedelta(seconds=window + 10)):
        fresh_manager._record_provider_type_failure("groq")
    with freeze_time(datetime.datetime(2026, 1, 1, 12, 0, 0) + datetime.timedelta(seconds=2 * (window + 10))):
        fresh_manager._record_provider_type_failure("groq")
        # Only the most recent failure is within its own window at this
        # point -- never 3 simultaneously in-window -- so no blacklist.
        assert fresh_manager._is_provider_type_blacklisted("groq") is False


def test_provider_type_blacklist_in_memory_expires_and_resets_counters(fresh_manager, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "false")
    with freeze_time("2026-01-01 12:00:00"):
        for _ in range(MultiProviderManager.PROVIDER_FAILURE_THRESHOLD):
            fresh_manager._record_provider_type_failure("groq")
        assert fresh_manager._is_provider_type_blacklisted("groq") is True

    duration = MultiProviderManager.PROVIDER_BLACKLIST_DURATION
    with freeze_time(datetime.datetime(2026, 1, 1, 12, 0, 0) + datetime.timedelta(seconds=duration + 60)):
        assert fresh_manager._is_provider_type_blacklisted("groq") is False
        assert "groq" not in fresh_manager._blacklisted_types
        assert "groq" not in fresh_manager._provider_type_failures


# ============================================================================
# evict_model
# ============================================================================

def test_evict_model_redis_removes_from_cache_and_slots(fresh_manager, fakeredis_client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    fresh_manager._redis_set(
        f"{fresh_manager._REDIS_MODELS_PREFIX}groq", ["model-a", "model-b", "model-c"],
        fresh_manager.MODEL_CACHE_TTL_SECONDS,
    )

    fresh_manager.evict_model("groq", "model-b")

    cached = fresh_manager._redis_get(f"{fresh_manager._REDIS_MODELS_PREFIX}groq")
    assert "model-b" not in cached
    for p in fresh_manager.providers:
        if p["_provider_id"] == "groq":
            assert "model-b" not in p["text_model_fallbacks"]


def test_evict_model_not_in_cached_list_is_noop(fresh_manager, fakeredis_client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    fresh_manager._redis_set(
        f"{fresh_manager._REDIS_MODELS_PREFIX}groq", ["model-a"], fresh_manager.MODEL_CACHE_TTL_SECONDS
    )
    # Should not raise, should not corrupt the cached list.
    fresh_manager.evict_model("groq", "model-does-not-exist")
    cached = fresh_manager._redis_get(f"{fresh_manager._REDIS_MODELS_PREFIX}groq")
    assert cached == ["model-a"]


def test_evict_model_in_memory_fallback(fresh_manager, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "false")
    fresh_manager.evict_model("groq", "model-b")
    for p in fresh_manager.providers:
        if p["_provider_id"] == "groq":
            assert "model-b" not in p["text_model_fallbacks"]


# ============================================================================
# _build_call_queue (module-level function reading the global
# provider_manager -- monkeypatched to our fresh, controlled instance,
# per this file's own module docstring reasoning)
# ============================================================================

def test_build_call_queue_healthy_providers_all_models(fresh_manager, monkeypatch):
    monkeypatch.setattr(ai_provider_service, "provider_manager", fresh_manager)
    queue = ai_provider_service._build_call_queue(needs_vision=False)
    # 2 providers x 3 models each = 6 entries, provider-then-model order.
    assert len(queue) == 6
    assert queue[0]["provider"]["name"] == "groq_0"
    assert queue[0]["model"] == "model-a"


def test_build_call_queue_excludes_blacklisted_provider_type(fresh_manager, fakeredis_client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    monkeypatch.setattr(ai_provider_service, "provider_manager", fresh_manager)
    for _ in range(MultiProviderManager.PROVIDER_FAILURE_THRESHOLD):
        fresh_manager._record_provider_type_failure("groq")

    queue = ai_provider_service._build_call_queue(needs_vision=False)
    assert all(entry["provider_id"] != "groq" for entry in queue)


def test_build_call_queue_excludes_cooling_key(fresh_manager, fakeredis_client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    monkeypatch.setattr(ai_provider_service, "provider_manager", fresh_manager)
    fresh_manager.mark_provider_failed("groq_0", "fail")

    queue = ai_provider_service._build_call_queue(needs_vision=False)
    assert all(entry["provider"]["name"] != "groq_0" for entry in queue)


def test_build_call_queue_needs_vision_excludes_non_vision_providers(fresh_manager, monkeypatch):
    monkeypatch.setattr(ai_provider_service, "provider_manager", fresh_manager)
    queue = ai_provider_service._build_call_queue(needs_vision=True)
    # Only gemini_0 supports vision.
    assert all(entry["provider"]["name"] == "gemini_0" for entry in queue)
    assert len(queue) == 3  # gemini's 3 vision-fallback models


def test_build_call_queue_construction_never_makes_network_call(monkeypatch):
    """Smoke-test guard against a future regression reintroducing an
    import-time/construction-time network call into __init__."""
    import requests
    call_log = []
    monkeypatch.setattr(requests, "get", lambda *a, **kw: call_log.append(("get", a, kw)))
    monkeypatch.setattr(requests, "post", lambda *a, **kw: call_log.append(("post", a, kw)))
    MultiProviderManager()
    assert call_log == []
