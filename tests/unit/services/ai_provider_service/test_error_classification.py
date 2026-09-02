"""
Tests for the error-classification portion of services/ai_provider_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.6. Every branch of
classify_provider_error's lookup table is tested independently -- a
swapped status set (e.g. 402 landing in PROVIDER_TRANSIENT instead of
KEY_FAULT) would silently change which failures cool a key vs. don't,
with real cost/security implications.
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

@pytest.mark.parametrize("status", [401, 402, 403, 429], ids=lambda s: f"status-{s}")
def test_classify_key_fault_statuses(status):
    err = ProviderCallError("x", status=status, provider_id="p")
    assert classify_provider_error(err) == "KEY_FAULT"


@pytest.mark.parametrize("status", [500, 502, 503, 504], ids=lambda s: f"status-{s}")
def test_classify_provider_transient_statuses(status):
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
