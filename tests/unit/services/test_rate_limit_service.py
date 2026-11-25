"""
Tests for services/rate_limit_service.py.

Scope note: this file tests ip_key/user_or_ip_key/_is_exempt_path only.
init_app() mutates real Flask app config and registers error handlers
against a live Limiter instance -- that's application-bootstrap wiring,
which UNIT_TEST_IMPLEMENTATION_PLAN.md §4 already places out of
unit-test scope for the analogous app.py::create_app() case. The
key-function logic tested here is the actual per-request business
logic Document 4's rate-limit design depends on.
"""

from services import rate_limit_service


def test_ip_key_returns_remote_address(app):
    with app.test_request_context("/", environ_overrides={"REMOTE_ADDR": "203.0.113.5"}):
        assert rate_limit_service.ip_key() == "203.0.113.5"


def test_user_or_ip_key_ignores_an_unverified_g_current_user_id(app):
    """The per-user key comes from the verified token, not from g: g.current_user_id
    is only set by token_required, which runs *after* the limiter (audit NOTIF-09).
    Token-based behaviour is covered in tests/integration/test_rate_limit_user_key.py."""
    with app.test_request_context("/", environ_overrides={"REMOTE_ADDR": "198.51.100.9"}):
        from flask import g
        g.current_user_id = 482
        assert rate_limit_service.user_or_ip_key() == "198.51.100.9"


def test_user_or_ip_key_falls_back_to_ip_when_no_user(app):
    with app.test_request_context("/", environ_overrides={"REMOTE_ADDR": "198.51.100.9"}):
        assert rate_limit_service.user_or_ip_key() == "198.51.100.9"


def test_is_exempt_path_health_endpoints():
    assert rate_limit_service._is_exempt_path("/health") is True
    assert rate_limit_service._is_exempt_path("/ping") is True
    assert rate_limit_service._is_exempt_path("/ready") is True


def test_is_exempt_path_normal_route_not_exempt():
    assert rate_limit_service._is_exempt_path("/api/posts") is False
