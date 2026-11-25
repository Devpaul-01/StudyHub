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


def test_user_or_ip_key_uses_user_id_when_set(app):
    with app.test_request_context("/"):
        from flask import g
        g.current_user_id = 482
        assert rate_limit_service.user_or_ip_key() == "user:482"


def test_user_or_ip_key_falls_back_to_ip_when_no_user(app):
    with app.test_request_context("/", environ_overrides={"REMOTE_ADDR": "198.51.100.9"}):
        assert rate_limit_service.user_or_ip_key() == "198.51.100.9"


def test_is_exempt_path_health_endpoints():
    assert rate_limit_service._is_exempt_path("/health") is True
    assert rate_limit_service._is_exempt_path("/ping") is True
    assert rate_limit_service._is_exempt_path("/ready") is True


def test_is_exempt_path_normal_route_not_exempt():
    assert rate_limit_service._is_exempt_path("/api/posts") is False


# --- user_or_ip_key must work BEFORE token_required has run (AN-15) ----------

def _token(app, user_id, **extra):
    import datetime
    import jwt
    payload = {"user_id": user_id, "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=5), **extra}
    return jwt.encode(payload, app.config["SECRET_KEY"], algorithm="HS256")


def test_user_or_ip_key_reads_user_from_bearer_token_without_g(app):
    with app.test_request_context("/", headers={"Authorization": f"Bearer {_token(app, 77)}"}):
        assert rate_limit_service.user_or_ip_key() == "user:77"


def test_user_or_ip_key_reads_user_from_access_token_cookie(app):
    with app.test_request_context("/", headers={"Cookie": f"access_token={_token(app, 78)}"}):
        assert rate_limit_service.user_or_ip_key() == "user:78"


def test_user_or_ip_key_ignores_forged_or_expired_tokens(app):
    import datetime
    import jwt
    forged = jwt.encode({"user_id": 5}, "not-the-secret", algorithm="HS256")
    expired = jwt.encode(
        {"user_id": 5, "exp": datetime.datetime.utcnow() - datetime.timedelta(minutes=1)},
        app.config["SECRET_KEY"], algorithm="HS256",
    )
    for tok in (forged, expired, "garbage"):
        with app.test_request_context(
            "/", headers={"Authorization": f"Bearer {tok}"}, environ_overrides={"REMOTE_ADDR": "198.51.100.7"}
        ):
            assert rate_limit_service.user_or_ip_key() == "198.51.100.7"
