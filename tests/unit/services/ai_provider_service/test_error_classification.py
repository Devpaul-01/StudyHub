"""
Tests for the error-classification portion of services/ai_provider_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.6. Every branch of
classify_provider_error's lookup table is tested independently -- a
swapped status set would silently change which failures start a cooldown
vs. don't, with real cost/availability implications.

GATEWAY MIGRATION: KEY_FAULT no longer has any real status mapped to it --
see the "GATEWAY ERROR CLASSIFICATION" comment in ai_provider_service.py.
401/403 (invalid/revoked gateway key) are hard misconfigurations now
classified NON_RETRYABLE, and 429 (the gateway's own admission control) is
PROVIDER_TRANSIENT, not KEY_FAULT -- there's only one gateway key, so
"cool this key, try another" no longer applies to it the way it did when
each vendor had its own key.
"""

import pytest
import requests

from services.ai_provider_service import (
    ProviderCallError,
    classify_provider_error,
    _is_bad_model_signal,
    _wrap_request_exception,
)


# ============================================================================
# classify_provider_error
# ============================================================================

def test_classify_key_fault_statuses_now_empty():
    """No gateway status classifies as KEY_FAULT anymore -- there's only
    one gateway key, so there's no "rotate to a different key" reaction
    left to trigger. Kept as an explicit test (rather than deleted) so a
    future status-set change is a deliberate decision, not a silent one."""
    from services.ai_provider_service import _KEY_FAULT_STATUSES
    assert _KEY_FAULT_STATUSES == set()


@pytest.mark.parametrize("status", [401, 403], ids=lambda s: f"status-{s}")
def test_classify_invalid_gateway_key_statuses_are_non_retryable(status):
    """401/403 mean the gateway key itself is missing/invalid/revoked --
    a hard misconfiguration, not a per-key fault to rotate away from."""
    err = ProviderCallError("x", status=status, provider_id="p")
    assert classify_provider_error(err) == "NON_RETRYABLE"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504], ids=lambda s: f"status-{s}")
def test_classify_provider_transient_statuses(status):
    """429 (gateway admission control) and 5xx (gateway's own upstream
    failover exhausted) are worth a short retry later -- see
    MultiProviderManager's cooldown -- but don't rotate to a different
    key (there is only one)."""
    err = ProviderCallError("x", status=status, provider_id="p")
    assert classify_provider_error(err) == "PROVIDER_TRANSIENT"


@pytest.mark.parametrize(
    "code", ["ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND", "ECONNRESET"]
)
def test_classify_network_error_code_is_transient(code):
    err = ProviderCallError("x", status=None, provider_id="p", network_error_code=code)
    assert classify_provider_error(err) == "PROVIDER_TRANSIENT"


@pytest.mark.parametrize(
    "message",
    ["connection refused", "Connection timed out", "timed out", "connection reset"],
)
def test_classify_network_message_fallback_is_transient(message):
    err = ProviderCallError(message, status=None, provider_id="p")
    assert classify_provider_error(err) == "PROVIDER_TRANSIENT"


def test_classify_bad_model_requires_400_and_signal():
    err = ProviderCallError(
        "x", status=400, provider_id="p",
        parsed_body={"error": {"message": "model 'foo' not found"}},
    )
    assert classify_provider_error(err) == "BAD_MODEL"


def test_classify_400_without_bad_model_signal_is_non_retryable():
    err = ProviderCallError(
        "x", status=400, provider_id="p",
        parsed_body={"error": {"message": "missing required field 'messages'"}},
    )
    assert classify_provider_error(err) == "NON_RETRYABLE"


def test_classify_unmatched_status_defaults_non_retryable():
    err = ProviderCallError("x", status=418, provider_id="p")
    assert classify_provider_error(err) == "NON_RETRYABLE"


def test_classify_non_provider_call_error_defensive_non_retryable():
    assert classify_provider_error(RuntimeError("not a ProviderCallError")) == "NON_RETRYABLE"


# ============================================================================
# _is_bad_model_signal
# ============================================================================

def test_bad_model_signal_none_body():
    assert _is_bad_model_signal(None) is False


def test_bad_model_signal_not_a_dict():
    assert _is_bad_model_signal("not a dict") is False


def test_bad_model_signal_no_error_key():
    assert _is_bad_model_signal({"something_else": 1}) is False


def test_bad_model_signal_error_not_a_dict():
    assert _is_bad_model_signal({"error": "just a string"}) is False


@pytest.mark.parametrize(
    "message",
    [
        "The model does not exist",
        "model not found",
        "invalid model specified",
        "model has been decommissioned",
        "unknown model requested",
        "unsupported model for this endpoint",
    ],
)
def test_bad_model_signal_positive_matches(message):
    assert _is_bad_model_signal({"error": {"message": message}}) is True


def test_bad_model_signal_has_model_word_but_no_failure_phrase():
    assert _is_bad_model_signal({"error": {"message": "the model responded successfully"}}) is False


def test_bad_model_signal_has_failure_phrase_but_no_model_word():
    assert _is_bad_model_signal({"error": {"message": "endpoint not found"}}) is False


# ============================================================================
# _wrap_request_exception
# ============================================================================

class _FakeResponse:
    def __init__(self, status_code, text="", json_body=None):
        self.status_code = status_code
        self.text = text
        self._json_body = json_body

    def json(self):
        if self._json_body is None:
            raise ValueError("not json")
        return self._json_body


def test_wrap_http_error_with_real_response_and_valid_json():
    resp = _FakeResponse(500, text='{"error": "boom"}', json_body={"error": "boom"})
    exc = requests.exceptions.HTTPError("500 error")
    exc.response = resp

    wrapped = _wrap_request_exception(exc, "groq")

    assert wrapped.status == 500
    assert wrapped.parsed_body == {"error": "boom"}
    assert wrapped.provider_id == "groq"


def test_wrap_http_error_with_non_json_body():
    resp = _FakeResponse(500, text="Internal Server Error")
    exc = requests.exceptions.HTTPError("500 error")
    exc.response = resp

    wrapped = _wrap_request_exception(exc, "groq")

    assert wrapped.status == 500
    assert wrapped.parsed_body is None


def test_wrap_connection_error_no_response():
    exc = requests.exceptions.ConnectionError("connection refused")
    wrapped = _wrap_request_exception(exc, "groq")
    assert wrapped.status is None
    assert wrapped.network_error_code is None


def test_wrap_generic_exception_same_network_level_shape():
    exc = TimeoutError("generic timeout")
    wrapped = _wrap_request_exception(exc, "groq")
    assert wrapped.status is None
    assert wrapped.network_error_code is None


@pytest.mark.parametrize("body", [
    {"error": {"code": "model_not_found"}},
    {"error": {"code": "model-not-found"}},
    {"error": {"type": "invalid_request_error", "code": "model_not_found",
               "message": "The model `x` does not exist"}},
    {"error": {"code": "model_does_not_exist"}},
])
def test_classify_bad_model_matches_underscore_and_hyphen_codes(body):
    """Structured error codes join words with _ or -, not spaces."""
    err = ProviderCallError("x", status=400, provider_id="p", parsed_body=body)
    assert classify_provider_error(err) == "BAD_MODEL"


def test_classify_underscore_not_found_still_requires_model_word():
    err = ProviderCallError(
        "x", status=400, provider_id="p",
        parsed_body={"error": {"code": "resource_not_found"}},
    )
    assert classify_provider_error(err) == "NON_RETRYABLE"
