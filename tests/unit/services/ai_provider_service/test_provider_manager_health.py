"""
Tests for MultiProviderManager's cooldown logic, _load_gateway_provider,
and _build_call_queue in services/ai_provider_service.py.

GATEWAY MIGRATION: rewritten from the pre-gateway version. There is no
per-key cooldown dict, no provider-type failure/blacklist tracking, and no
Redis-backed model-discovery cache anymore — the gateway owns rotation,
circuit breaking, and model discovery server-side now. What's left here:
  - a single, short, Redis-backed (cross-instance) cooldown over the one
    gateway slot (_is_key_cooling / mark_provider_failed)
  - _load_gateway_provider's env-var reading (base URL, key, project id,
    optional model-list overrides)
  - evict_model's process-local catalog mutation
  - _build_call_queue, now returning at most one entry

Tests still construct a FRESH MultiProviderManager() instance per test and
never assert against the real module-level provider_manager directly.
"""

import pytest
from freezegun import freeze_time

from services import ai_provider_service
from services.ai_provider_service import MultiProviderManager


def _fake_gateway_provider(supports_vision=True, models=None):
    models = models or ["gemini:model-a", "groq:model-b", "mistral:model-c"]
    return {
        "name": "llm-gateway",
        "api_key": "fake-gateway-key",
        "base_url": "https://fake-gateway.example.com/v1",
        "text_model": models[0],
        "vision_model": models[0] if supports_vision else None,
        "supports_vision": supports_vision,
        "type": "gateway",
        "text_model_fallbacks": list(models),
        "vision_model_fallbacks": list(models) if supports_vision else [],
        "_provider_id": "gateway",
        "project_id": "proj_test",
    }


@pytest.fixture
def fresh_manager():
    """A MultiProviderManager with NO real env-var-derived provider --
    __init__ still calls _load_gateway_provider() (harmless: no
    LLM_GATEWAY_BASE_URL/API_KEY are set in the test environment, so it
    returns []), and this fixture then injects a controlled provider
    directly."""
    mgr = MultiProviderManager()
    mgr.providers = [_fake_gateway_provider()]
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
# _load_gateway_provider — env-var reading
# ============================================================================

def test_load_gateway_provider_missing_env_returns_empty(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    mgr = MultiProviderManager()
    assert mgr.providers == []
    assert mgr.get_working_provider() is None


def test_load_gateway_provider_reads_base_url_key_and_project_id(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example.com")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk_test_123")
    monkeypatch.setenv("LLM_GATEWAY_PROJECT_ID", "proj_abc")
    mgr = MultiProviderManager()

    assert len(mgr.providers) == 1
    provider = mgr.providers[0]
    # No trailing slash duplication, "/v1" appended exactly once.
    assert provider["base_url"] == "https://gateway.example.com/v1"
    assert provider["api_key"] == "sk_test_123"
    assert provider["project_id"] == "proj_abc"


def test_load_gateway_provider_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example.com/")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk_test_123")
    mgr = MultiProviderManager()
    assert mgr.providers[0]["base_url"] == "https://gateway.example.com/v1"


def test_load_gateway_provider_model_list_env_override(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example.com")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk_test_123")
    monkeypatch.setenv("LLM_GATEWAY_TEXT_MODELS", "groq:llama-3.3-70b-versatile, mistral:mistral-medium-latest")
    mgr = MultiProviderManager()
    provider = mgr.providers[0]
    assert provider["text_model_fallbacks"] == ["groq:llama-3.3-70b-versatile", "mistral:mistral-medium-latest"]
    assert provider["text_model"] == "groq:llama-3.3-70b-versatile"


# ============================================================================
# _is_key_cooling / mark_provider_failed — Redis-backed path
# ============================================================================

def test_is_key_cooling_redis_never_marked(fresh_manager, fakeredis_client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    assert fresh_manager._is_key_cooling("llm-gateway") is False


def test_is_key_cooling_redis_marked_failed(fresh_manager, fakeredis_client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "true")
    fresh_manager.mark_provider_failed("llm-gateway", "simulated failure")
    assert fresh_manager._is_key_cooling("llm-gateway") is True


# ============================================================================
# _is_key_cooling / mark_provider_failed — in-memory fallback path
# ============================================================================

def test_is_key_cooling_in_memory_never_failed(fresh_manager, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "false")
    assert fresh_manager._is_key_cooling("llm-gateway") is False


def test_is_key_cooling_in_memory_recently_failed(fresh_manager, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "false")
    with freeze_time("2026-01-01 12:00:00"):
        fresh_manager.mark_provider_failed("llm-gateway", "fail")
    with freeze_time("2026-01-01 12:00:05"):  # 5s later, within the (short) cooldown
        assert fresh_manager._is_key_cooling("llm-gateway") is True


def test_is_key_cooling_in_memory_expires(fresh_manager, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "false")
    with freeze_time("2026-01-01 12:00:00"):
        fresh_manager.mark_provider_failed("llm-gateway", "fail")
    # Default cooldown is short (20s) -- unlike the pre-gateway 1h cooldown,
    # since there's no other key to rotate to while this one cools down.
    with freeze_time("2026-01-01 12:01:00"):  # 60s later
        assert fresh_manager._is_key_cooling("llm-gateway") is False


def test_cooldown_period_configurable_via_env(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_COOLDOWN_SECONDS", "5")
    mgr = MultiProviderManager()
    assert mgr.cooldown_period == 5


def test_get_working_provider_returns_none_while_cooling(fresh_manager, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "false")
    fresh_manager.mark_provider_failed("llm-gateway", "fail")
    assert fresh_manager.get_working_provider() is None


def test_get_working_provider_needs_vision_without_vision_model(monkeypatch):
    mgr = MultiProviderManager()
    mgr.providers = [_fake_gateway_provider(supports_vision=False)]
    assert mgr.get_working_provider(needs_vision=True) is None
    assert mgr.get_working_provider(needs_vision=False) is not None


def test_rotate_is_a_harmless_noop(fresh_manager):
    """No second provider to rotate to anymore -- exists only so existing
    callers that still call it between a failed attempt and their next
    get_working_provider() call keep working unchanged."""
    before = list(fresh_manager.providers)
    fresh_manager.rotate()
    assert fresh_manager.providers == before


# ============================================================================
# evict_model
# ============================================================================

def test_evict_model_removes_from_fallback_lists(fresh_manager):
    fresh_manager.evict_model("gateway", "groq:model-b")
    provider = fresh_manager.providers[0]
    assert "groq:model-b" not in provider["text_model_fallbacks"]
    assert "groq:model-b" not in provider["vision_model_fallbacks"]


def test_evict_model_not_present_is_noop(fresh_manager):
    before = list(fresh_manager.providers[0]["text_model_fallbacks"])
    fresh_manager.evict_model("gateway", "does-not-exist")
    assert fresh_manager.providers[0]["text_model_fallbacks"] == before


def test_evict_primary_model_promotes_next_fallback(fresh_manager):
    provider = fresh_manager.providers[0]
    primary = provider["text_model"]
    fresh_manager.evict_model("gateway", primary)
    assert provider["text_model"] != primary
    assert provider["text_model"] == provider["text_model_fallbacks"][0]


def test_evict_last_vision_model_disables_vision(fresh_manager):
    mgr = MultiProviderManager()
    mgr.providers = [_fake_gateway_provider(supports_vision=True, models=["gemini:only-model"])]
    mgr.evict_model("gateway", "gemini:only-model")
    provider = mgr.providers[0]
    assert provider["vision_model"] is None
    assert provider["supports_vision"] is False


# ============================================================================
# _build_call_queue (module-level function reading the global
# provider_manager -- monkeypatched to our fresh, controlled instance)
# ============================================================================

def test_build_call_queue_single_entry_with_primary_model(fresh_manager, monkeypatch):
    monkeypatch.setattr(ai_provider_service, "provider_manager", fresh_manager)
    queue = ai_provider_service._build_call_queue(needs_vision=False)
    # Exactly one entry -- the gateway walks the rest of the chain itself
    # via `fallback_models`, see _fallback_models_for.
    assert len(queue) == 1
    assert queue[0]["provider"]["name"] == "llm-gateway"
    assert queue[0]["model"] == fresh_manager.providers[0]["text_model"]


def test_build_call_queue_empty_when_no_gateway_configured(monkeypatch):
    mgr = MultiProviderManager()
    mgr.providers = []
    monkeypatch.setattr(ai_provider_service, "provider_manager", mgr)
    assert ai_provider_service._build_call_queue(needs_vision=False) == []


def test_build_call_queue_excludes_cooling_gateway(fresh_manager, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_REDIS_STATE_ENABLED", "false")
    monkeypatch.setattr(ai_provider_service, "provider_manager", fresh_manager)
    fresh_manager.mark_provider_failed("llm-gateway", "fail")
    assert ai_provider_service._build_call_queue(needs_vision=False) == []


def test_build_call_queue_needs_vision_without_vision_model_is_empty(monkeypatch):
    mgr = MultiProviderManager()
    mgr.providers = [_fake_gateway_provider(supports_vision=False)]
    monkeypatch.setattr(ai_provider_service, "provider_manager", mgr)
    assert ai_provider_service._build_call_queue(needs_vision=True) == []


def test_build_call_queue_construction_never_makes_network_call(monkeypatch):
    """Smoke-test guard against a future regression reintroducing an
    import-time/construction-time network call into __init__."""
    import requests
    call_log = []
    monkeypatch.setattr(requests, "get", lambda *a, **kw: call_log.append(("get", a, kw)))
    monkeypatch.setattr(requests, "post", lambda *a, **kw: call_log.append(("post", a, kw)))
    MultiProviderManager()
    assert call_log == []
