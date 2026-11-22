"""Audit CON-18: user_or_ip_key must work BEFORE token_required has run."""
import pytest

from services.rate_limit_service import user_or_ip_key, ip_key

pytestmark = pytest.mark.integration


def test_user_key_comes_from_the_verified_token_even_without_g(app, make_user, auth_headers):
    u = make_user()
    with app.test_request_context("/x", headers=auth_headers(u), environ_base={"REMOTE_ADDR": "10.0.0.9"}):
        assert user_or_ip_key() == f"user:{u.id}"


def test_two_users_behind_one_ip_get_separate_buckets(app, make_user, auth_headers):
    a, b = make_user(), make_user()
    env = {"REMOTE_ADDR": "10.0.0.9"}
    with app.test_request_context("/x", headers=auth_headers(a), environ_base=env):
        ka = user_or_ip_key()
    with app.test_request_context("/x", headers=auth_headers(b), environ_base=env):
        kb = user_or_ip_key()
    assert ka != kb


def test_invalid_or_missing_token_falls_back_to_ip(app):
    with app.test_request_context("/x", headers={"Authorization": "Bearer not.a.jwt"},
                                  environ_base={"REMOTE_ADDR": "10.0.0.9"}):
        assert user_or_ip_key().startswith("ip:") or "10.0.0.9" in user_or_ip_key()
    with app.test_request_context("/x", environ_base={"REMOTE_ADDR": "10.0.0.9"}):
        assert "10.0.0.9" in user_or_ip_key()
