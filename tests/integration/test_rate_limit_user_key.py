"""
tests/integration/test_rate_limit_user_key.py

Regression tests for audit finding F-13: `user_or_ip_key` read
`g.current_user_id`, which the auth decorator only sets AFTER Flask-Limiter has
already evaluated the key, so every "per-user" limit silently collapsed to a
per-IP limit.
"""

import jwt
import pytest

pytestmark = pytest.mark.integration


def _key(app, headers=None, cookies=None):
    from services.rate_limit_service import user_or_ip_key

    headers = dict(headers or {})
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    with app.test_request_context(
        "/student/homework/assignments",
        headers=headers,
        environ_base={"REMOTE_ADDR": "10.1.1.1"},
    ):
        return user_or_ip_key()


class TestUserOrIpKey:
    def test_bearer_token_yields_per_user_key(self, app, make_user, auth_headers):
        user = make_user()
        assert _key(app, auth_headers(user)) == f"user:{user.id}"

    def test_two_users_same_ip_get_different_keys(self, app, make_user, auth_headers):
        a, b = make_user(), make_user()
        assert _key(app, auth_headers(a)) != _key(app, auth_headers(b))

    def test_cookie_token_is_used(self, app, make_user, auth_headers):
        user = make_user()
        token = auth_headers(user)["Authorization"].split(" ", 1)[1]
        assert _key(app, cookies={"access_token": token}) == f"user:{user.id}"

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Authorization": "Bearer not-a-jwt"},
            {"Authorization": "Basic abc"},
            {"Authorization": "Bearer "},
        ],
    )
    def test_missing_or_malformed_token_falls_back_to_ip(self, app, headers):
        assert _key(app, headers) == "10.1.1.1"

    def test_token_signed_with_wrong_secret_falls_back_to_ip(self, app, make_user):
        user = make_user()
        forged = jwt.encode({"user_id": user.id}, "not-the-secret", algorithm="HS256")
        assert _key(app, {"Authorization": f"Bearer {forged}"}) == "10.1.1.1"

    def test_does_not_mark_request_as_authenticated(self, app, make_user, auth_headers):
        """The key function must not set g.current_user_id — that is the auth
        decorator's marker that the user exists and has the right role."""
        from flask import g

        from services.rate_limit_service import user_or_ip_key

        user = make_user()
        with app.test_request_context(
            "/", headers=auth_headers(user), environ_base={"REMOTE_ADDR": "10.1.1.1"}
        ):
            user_or_ip_key()
            assert getattr(g, "current_user_id", None) is None


class TestPerUserBucketsEndToEnd:
    def test_one_user_exhausting_limit_does_not_block_another_on_same_ip(
        self, client, make_user, auth_headers, csrf_headers
    ):
        """WRITE_HEAVY is 30/min. User A burns the budget from the test client's
        single IP; user B must still be served (400 from validation, not 429)."""
        a, b = make_user(), make_user()
        csrf = csrf_headers(client)  # one cookie/header pair, reused for both users
        a_headers = {**auth_headers(a), **csrf}
        b_headers = {**auth_headers(b), **csrf}

        statuses = [
            client.post("/student/assignments", json={}, headers=a_headers).status_code
            for _ in range(35)
        ]
        assert statuses[0] == 400
        assert 429 in statuses

        resp = client.post("/student/assignments", json={}, headers=b_headers)
        assert resp.status_code == 400


class TestMemoryStorageWarning:
    def test_warns_when_memory_storage_outside_test_and_debug(self, app, monkeypatch, caplog):
        import logging

        from flask import Flask

        import services.rate_limit_service as rls

        fake = Flask("rl-warning")
        fake.config.update(
            RATE_LIMIT_STORAGE_URI="memory://", RATE_LIMIT_ENABLED=True, TESTING=False
        )
        fake.debug = False
        monkeypatch.setattr(rls.limiter, "init_app", lambda a: None)
        monkeypatch.setattr(rls.limiter, "request_filter", lambda f: f)

        records = []

        class _H(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _H(level=logging.ERROR)
        rls.logger.addHandler(handler)
        try:
            rls.init_app(fake)
        finally:
            rls.logger.removeHandler(handler)
        assert any("memory://" in r.getMessage() for r in records)

    def test_no_warning_when_testing(self, app, monkeypatch):
        import logging

        from flask import Flask

        import services.rate_limit_service as rls

        fake = Flask("rl-quiet")
        fake.config.update(
            RATE_LIMIT_STORAGE_URI="memory://", RATE_LIMIT_ENABLED=True, TESTING=True
        )
        monkeypatch.setattr(rls.limiter, "init_app", lambda a: None)
        monkeypatch.setattr(rls.limiter, "request_filter", lambda f: f)
        records = []

        class _H(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _H(level=logging.ERROR)
        rls.logger.addHandler(handler)
        try:
            rls.init_app(fake)
        finally:
            rls.logger.removeHandler(handler)
        assert not records
