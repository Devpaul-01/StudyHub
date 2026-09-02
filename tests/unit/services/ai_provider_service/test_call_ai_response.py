"""
Tests for services/ai_provider_service.py::call_ai_response.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.6. Every classification-to-
reaction mapping is verified independently -- a swapped reaction (e.g.
cooling a key on PROVIDER_TRANSIENT) would incorrectly penalize healthy
keys for provider-wide outages.
"""

from unittest.mock import Mock, patch

import pytest
import requests

from services import ai_provider_service
from services.ai_provider_service import MultiProviderManager


def _fake_provider(name, provider_id="groq", models=None):
    models = models or ["model-a", "model-b"]
    return {
        "name": name,
        "api_key": "fake-key",
        "base_url": "https://fake.example.com/v1",
        "text_model": models[0],
        "vision_model": None,
        "supports_vision": False,
        "type": provider_id,
        "text_model_fallbacks": list(models),
        "vision_model_fallbacks": [],
        "_provider_id": provider_id,
        "_vision_models": set(),
    }


@pytest.fixture
def two_provider_manager(monkeypatch):
    mgr = MultiProviderManager()
    mgr.providers = [
        _fake_provider("groq_0", provider_id="groq"),
        _fake_provider("gemini_0", provider_id="gemini"),
    ]
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


def test_call_ai_response_empty_queue_no_http_call(monkeypatch):
    mgr = MultiProviderManager()
    mgr.providers = []
    monkeypatch.setattr(ai_provider_service, "provider_manager", mgr)

    with patch("services.ai_provider_service.requests.post") as mock_post:
        text, diagnostics = ai_provider_service.call_ai_response([{"role": "user", "content": "hi"}])

    assert text is None
    assert "no working provider available" in diagnostics["errors"][0]
    mock_post.assert_not_called()


def test_call_ai_response_first_entry_succeeds(two_provider_manager):
    with patch(
        "services.ai_provider_service.requests.post",
        return_value=_mock_response(200, _success_body("first try works")),
    ):
        text, diagnostics = ai_provider_service.call_ai_response([{"role": "user", "content": "hi"}])

    assert text == "first try works"
    assert diagnostics["attempts"] == 1


def test_call_ai_response_key_fault_marks_provider_failed_and_advances(two_provider_manager):
    responses = [
        _mock_response(401, text="unauthorized"),   # groq_0/model-a -> KEY_FAULT
        _mock_response(200, _success_body("second entry succeeds")),
    ]
    with patch("services.ai_provider_service.requests.post", side_effect=responses), \
         patch.object(two_provider_manager, "mark_provider_failed") as mock_mark_failed:
        text, diagnostics = ai_provider_service.call_ai_response(
            [{"role": "user", "content": "hi"}], max_retries=3
        )

    assert text == "second entry succeeds"
    assert diagnostics["attempts"] == 2
    mock_mark_failed.assert_called_once()
    assert mock_mark_failed.call_args[0][0] == "groq_0"


def test_call_ai_response_provider_transient_does_not_cool_key(two_provider_manager):
    responses = [
        _mock_response(503, text="service unavailable"),  # PROVIDER_TRANSIENT
        _mock_response(200, _success_body("recovered on next entry")),
    ]
    with patch("services.ai_provider_service.requests.post", side_effect=responses), \
         patch.object(two_provider_manager, "mark_provider_failed") as mock_mark_failed:
        text, diagnostics = ai_provider_service.call_ai_response(
            [{"role": "user", "content": "hi"}], max_retries=3
        )

    assert text == "recovered on next entry"
    mock_mark_failed.assert_not_called()  # must NOT cool the key for a provider-wide outage


def test_call_ai_response_bad_model_evicts_model_not_key(two_provider_manager):
    responses = [
        _mock_response(400, json_body={"error": {"message": "model not found"}}),  # BAD_MODEL
        _mock_response(200, _success_body("next model in queue")),
    ]
    with patch("services.ai_provider_service.requests.post", side_effect=responses), \
         patch.object(two_provider_manager, "mark_provider_failed") as mock_mark_failed, \
         patch.object(two_provider_manager, "evict_model") as mock_evict:
        text, diagnostics = ai_provider_service.call_ai_response(
            [{"role": "user", "content": "hi"}], max_retries=3
        )

    assert text == "next model in queue"
    mock_mark_failed.assert_not_called()
    mock_evict.assert_called_once_with("groq", "model-a")


def test_call_ai_response_non_retryable_stops_immediately(two_provider_manager):
    """Must not burn through the rest of the queue on a request that
    will never succeed anywhere."""
    responses = [
        _mock_response(418, text="I'm a teapot"),  # NON_RETRYABLE
    ]
    with patch("services.ai_provider_service.requests.post", side_effect=responses) as mock_post:
        text, diagnostics = ai_provider_service.call_ai_response(
            [{"role": "user", "content": "hi"}], max_retries=5
        )

    assert text is None
    assert mock_post.call_count == 1  # did not try any further queue entries


def test_call_ai_response_respects_max_retries_cap(two_provider_manager):
    """4 queue entries total (2 providers x 2 models), but max_retries=1
    caps attempts at max_retries+1 == 2, not all 4."""
    responses = [
        _mock_response(503, text="down"),
        _mock_response(503, text="still down"),
        _mock_response(200, _success_body("would have worked")),
    ]
    with patch("services.ai_provider_service.requests.post", side_effect=responses) as mock_post:
        text, diagnostics = ai_provider_service.call_ai_response(
            [{"role": "user", "content": "hi"}], max_retries=1
        )

    assert text is None
    assert mock_post.call_count == 2
    assert diagnostics["attempts"] == 2
