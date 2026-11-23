"""Leaderboard rate limits are per user, not per shared IP."""
import datetime

import jwt as pyjwt
from flask import current_app


def test_students_on_one_ip_have_independent_budgets(client, make_user, auth_headers, fakeredis_client):
    """Campus NAT: A using up their budget must not lock out B."""
    a, b = make_user(), make_user()
    codes_a = [client.get("/student/leaderboard/stats", headers=auth_headers(a)).status_code for _ in range(301)]
    codes_b = [client.get("/student/leaderboard/stats", headers=auth_headers(b)).status_code for _ in range(5)]

    assert codes_a[:300] == [200] * 300 and codes_a[300] == 429     # 300/min, then limited
    assert codes_b == [200] * 5                                      # B is unaffected


def test_filters_endpoint_is_rate_limited(client, make_user, auth_headers, fakeredis_client):
    u = make_user()
    codes = [client.get("/student/leaderboard/filters", headers=auth_headers(u)).status_code for _ in range(301)]
    assert codes[-1] == 429


def test_forged_token_cannot_choose_another_users_bucket(client, make_user, auth_headers, fakeredis_client):
    """A token signed with the wrong key is not trusted: it falls back to the
    IP bucket instead of landing in the victim's per-user bucket."""
    victim = make_user()
    forged = pyjwt.encode(
        {"user_id": victim.id, "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=5)},
        "not-the-real-secret", algorithm="HS256",
    )
    from services.rate_limit_service import user_or_ip_key

    with current_app.test_request_context("/", headers={"Authorization": f"Bearer {forged}"}):
        key = user_or_ip_key()
    assert not key.startswith("user:")


def test_valid_token_maps_to_a_user_key(make_user, auth_headers):
    user = make_user()
    from services.rate_limit_service import user_or_ip_key

    with current_app.test_request_context("/", headers=auth_headers(user)):
        assert user_or_ip_key() == f"user:{user.id}"


def test_expired_token_falls_back_to_ip(make_user):
    user = make_user()
    expired = pyjwt.encode(
        {"user_id": user.id, "exp": datetime.datetime.utcnow() - datetime.timedelta(minutes=1)},
        current_app.config["SECRET_KEY"], algorithm="HS256",
    )
    from services.rate_limit_service import user_or_ip_key

    with current_app.test_request_context("/", headers={"Authorization": f"Bearer {expired}"}):
        assert not user_or_ip_key().startswith("user:")


def test_storage_uri_credentials_are_redacted_for_logging():
    from services.rate_limit_service import _redact_uri
    assert _redact_uri("rediss://default:s3cret@host:6379/0") == "rediss://***@host:6379/0"
    assert _redact_uri("memory://") == "memory://"
