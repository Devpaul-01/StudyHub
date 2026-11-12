"""
Tests for services/ai_provider_service.py::call_ai_response.

GATEWAY MIGRATION: rewritten from the pre-gateway, multi-provider×model
queue version. There is now exactly one provider slot (the gateway), and
the gateway itself walks a `fallback_models` chain server-side inside ONE
HTTP call — so `_build_call_queue` returns at most one entry, and
call_ai_response's per-category reactions (classify_provider_error's
KEY_FAULT / PROVIDER_TRANSIENT / BAD_MODEL / NON_RETRYABLE) now only
affect the cooldown state used by the NEXT call_ai_response invocation,
not whether THIS call tries something else. See the "GATEWAY ERROR
CLASSIFICATION" comment and call_ai_response's docstring in
services/ai_provider_service.py for the full reasoning.
"""

from unittest.mock import Mock, patch

import pytest
import requests

from services import ai_provider_service
from services.ai_provider_service import MultiProviderManager


def _fake_gateway_provider(models=None):
    models = models or ["gemini:gemini-3.7-flash", "groq:llama-3.3-70b-versatile"]
    return {
        "name": "llm-gateway",
        "api_key": "fake-gateway-key",
        "base_url": "https://fake-gateway.example.com/v1",
        "text_model": models[0],
        "vision_model": None,
        "supports_vision": False,
        "type": "gateway",
        "text_model_fallbacks": list(models),
        "vision_model_fallbacks": [],
        "_provider_id": "gateway",
        "project_id": "proj_test",
    }


@pytest.fixture
def gateway_manager(monkeypatch):
    mgr = MultiProviderManager()
    mgr.providers = [_fake_gateway_provider()]
    monkeypatch.setattr(ai_provider_service, "provider_manager", mgr)
    return mgr


def _mock_response(status_code=200, json_body=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.text = text or ""
    resp.json.return_value = json_body or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


def _success_body(text="Hello from the model"):
    return {"choices": [{"message": {"content": text}}]}


def test_call_ai_response_no_gateway_configured_no_http_call(monkeypatch):
    mgr = MultiProviderManager()
    mgr.providers = []
    monkeypatch.setattr(ai_provider_service, "provider_manager", mgr)

    with patch("services.ai_provider_service.requests.post") as mock_post:
        text, diagnostics = ai_provider_service.call_ai_response([{"role": "user", "content": "hi"}])

    assert text is None
    assert "no working provider available" in diagnostics["errors"][0]
    mock_post.assert_not_called()


def test_call_ai_response_success_sends_fallback_models(gateway_manager):
    with patch(
        "services.ai_provider_service.requests.post",
        return_value=_mock_response(200, _success_body("it worked")),
    ) as mock_post:
        text, diagnostics = ai_provider_service.call_ai_response([{"role": "user", "content": "hi"}])

    assert text == "it worked"
    assert diagnostics["attempts"] == 1
    assert diagnostics["provider"] == "llm-gateway"

    # The gateway call must carry the rest of the chain as fallback_models
    # so IT walks the fallback, not this process.
    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["model"] == "gemini:gemini-3.7-flash"
    assert sent_payload["fallback_models"] == ["groq:llama-3.3-70b-versatile"]


def test_call_ai_response_provider_transient_cools_the_gateway(gateway_manager):
    """
    Single-gateway design: unlike the pre-gateway behavior, a transient
    gateway-side failure (exhausted upstream failover, admission control)
    now DOES start a cooldown — there's no other key/provider at this
    layer to rotate to, so protecting the gateway from being hammered
    again immediately is the correct reaction.
    """
    with patch(
        "services.ai_provider_service.requests.post",
        return_value=_mock_response(503, text="service unavailable"),
    ), patch.object(gateway_manager, "mark_provider_failed") as mock_mark_failed:
        text, diagnostics = ai_provider_service.call_ai_response([{"role": "user", "content": "hi"}])

    assert text is None
    assert diagnostics["attempts"] == 1
    mock_mark_failed.assert_called_once()
    assert mock_mark_failed.call_args[0][0] == "llm-gateway"


def test_call_ai_response_bad_model_evicts_model_not_the_gateway(gateway_manager):
    with patch(
        "services.ai_provider_service.requests.post",
        return_value=_mock_response(400, json_body={"error": {"message": "model not found"}}),
    ), patch.object(gateway_manager, "mark_provider_failed") as mock_mark_failed, \
       patch.object(gateway_manager, "evict_model") as mock_evict:
        text, diagnostics = ai_provider_service.call_ai_response([{"role": "user", "content": "hi"}])

    assert text is None
    mock_mark_failed.assert_not_called()  # the gateway itself is healthy
    mock_evict.assert_called_once_with("gateway", "gemini:gemini-3.7-flash")


def test_call_ai_response_non_retryable_does_not_cool_or_evict(gateway_manager):
    """A malformed-request/invalid-key style failure is a config problem,
    not a gateway-health problem — no cooldown, no model eviction."""
    with patch(
        "services.ai_provider_service.requests.post",
        return_value=_mock_response(401, text="invalid gateway key"),
    ) as mock_post, patch.object(gateway_manager, "mark_provider_failed") as mock_mark_failed, \
       patch.object(gateway_manager, "evict_model") as mock_evict:
        text, diagnostics = ai_provider_service.call_ai_response(
            [{"role": "user", "content": "hi"}], max_retries=5
        )

    assert text is None
    assert mock_post.call_count == 1  # single gateway slot, nothing else to try
    mock_mark_failed.assert_not_called()
    mock_evict.assert_not_called()


def test_call_ai_response_respects_max_retries_cap(gateway_manager):
    """Even with max_retries set high, there's only one queue entry (the
    single gateway slot), so only one HTTP call is ever made per
    invocation — fallback across models/providers happens server-side."""
    with patch(
        "services.ai_provider_service.requests.post",
        return_value=_mock_response(503, text="down"),
    ) as mock_post:
        text, diagnostics = ai_provider_service.call_ai_response(
            [{"role": "user", "content": "hi"}], max_retries=5
        )

    assert text is None
    assert mock_post.call_count == 1
    assert diagnostics["attempts"] == 1
