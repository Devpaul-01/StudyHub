"""
tests/integration/test_auth_misc_routes.py

routes/student/auth.py had partial coverage from test_auth_lifecycle.py
(register, login, logout, refresh-token) and test_auth_password_reset_and_misc.py
(check-username, verify-reset, set-password, verify-auth, reset-password GET).
This batch covers the remaining non-Google-OAuth routes: GET users/me,
GET google_temp_info, POST clear-session, GET auth/me, GET features/demo
(template rendering only), GET onboard/suggestions-by-email/<email>,
POST validate-user, and GET/POST verify-email/<token>. Google OAuth
start/callback are left untested -- they require a real OAuth round
trip this test environment cannot simulate.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET users/me  (note: route string has no leading slash -- still mounted
# under /student since Flask treats it relative to the blueprint)
# ============================================================================

class TestCurrentUser:
    def test_returns_full_profile(self, client, make_user, auth_headers):
        user = make_user(bio="Hello there")

        resp = client.get("/student/users/me", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]["user"]
        assert data["id"] == user.id
        assert data["bio"] == "Hello there"

    def test_requires_auth(self, client):
        resp = client.get("/student/users/me")

        assert resp.status_code == 401


# ============================================================================
# GET /google_temp_info
# ============================================================================

class TestGoogleTempInfo:
    def test_returns_none_when_no_session_data(self, client):
        resp = client.get("/student/google_temp_info")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["email"] is None
        assert body["name"] is None

    def test_returns_session_values_when_set(self, client):
        with client.session_transaction() as sess:
            sess["google_email"] = "gtest@example.com"
            sess["google_name"] = "G Test"

        resp = client.get("/student/google_temp_info")

        body = resp.get_json()
        assert body["email"] == "gtest@example.com"
        assert body["name"] == "G Test"


# ============================================================================
# POST /clear-session
# ============================================================================

class TestClearSession:
    def test_clears_google_oauth_session_keys(self, client):
        with client.session_transaction() as sess:
            sess["google_email"] = "gtest@example.com"
            sess["google_name"] = "G Test"
            sess["google_id"] = "12345"

        resp = client.post("/student/clear-session")

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "success"

        with client.session_transaction() as sess:
            assert "google_email" not in sess
            assert "google_name" not in sess
            assert "google_id" not in sess


# ============================================================================
# GET /auth/me
# ============================================================================

class TestGetCurrentUserShort:
    def test_returns_minimal_profile(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/auth/me", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]["user"]
        assert data["id"] == user.id
        assert data["username"] == user.username

    def test_requires_auth(self, client):
        resp = client.get("/student/auth/me")

        assert resp.status_code == 401


# ============================================================================
# GET /features, GET/POST /demo  (template rendering, no auth)
# ============================================================================

class TestStaticPages:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG FOUND BY THIS TEST: features() does "
            "`return render_template(\"features.html\")`, but no "
            "features.html exists anywhere in the codebase (confirmed: "
            "`find . -iname features.html` finds nothing). Every call to "
            "GET /student/features raises jinja2.exceptions.TemplateNotFound, "
            "uncaught by this route (no try/except), which Flask turns "
            "into a 500 in production -- this page is completely broken. "
            "strict=True so this flips to XPASS once features.html is "
            "added back (or the route is removed if the page is no "
            "longer meant to exist)."
        ),
    )
    def test_features_page_renders(self, client):
        resp = client.get("/student/features")

        assert resp.status_code == 200

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG FOUND BY THIS TEST: demo() does "
            "`return render_template(\"demo.html\")`, but no demo.html "
            "exists anywhere in the codebase either -- same unreachable "
            "TemplateNotFound crash as GET /student/features above. "
            "strict=True so this flips to XPASS once demo.html is added "
            "back (or the route is removed)."
        ),
    )
    def test_demo_page_renders_get(self, client):
        resp = client.get("/student/demo")

        assert resp.status_code == 200

    @pytest.mark.xfail(
        strict=True,
        reason="Same missing demo.html template as the GET case above.",
    )
    def test_demo_page_renders_post(self, client):
        resp = client.post("/student/demo")

        assert resp.status_code == 200


# ============================================================================
# GET /onboard/suggestions-by-email/<email>
# ============================================================================

class TestOnboardSuggestionsByEmail:
    def test_unknown_email_returns_error(self, client):
        resp = client.get(
            "/student/onboard/suggestions-by-email/nobody@example.com"
        )

        assert resp.get_json()["status"] == "error"

    def test_known_email_returns_fallback_top_users(self, client, make_user):
        target = make_user(status="pending_onboarding")
        make_user(reputation=500, status="approved")

        resp = client.get(
            f"/student/onboard/suggestions-by-email/{target.email}"
        )

        assert resp.status_code == 200
        matches = resp.get_json()["data"]["matches"]
        assert len(matches) >= 1
        assert "match_score" in matches[0]


# ============================================================================
# POST /validate-user
# ============================================================================

class TestValidateUser:
    def test_missing_data_rejected(self, client):
        resp = client.post("/student/validate-user", json={})

        assert resp.get_json()["status"] == "error"

    def test_unknown_user_rejected(self, client):
        resp = client.post(
            "/student/validate-user", json={"data": "nobody@example.com"}
        )

        assert resp.get_json()["status"] == "error"

    def test_known_email_enqueues_reset_email(self, client, make_user, monkeypatch):
        from services.job_queue import email_queue

        user = make_user(email="resetme@example.com")

        enqueued = {}

        def _fake_enqueue(func, **kwargs):
            enqueued["kwargs"] = kwargs
            class _FakeJob:
                id = "fake-job-id"
            return _FakeJob()

        monkeypatch.setattr(email_queue, "enqueue", _fake_enqueue)

        resp = client.post(
            "/student/validate-user", json={"data": "resetme@example.com"}
        )

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "success"
        assert enqueued.get("kwargs", {}).get("to_email") == "resetme@example.com"

    def test_known_username_also_works(self, client, make_user, monkeypatch):
        from services.job_queue import email_queue

        user = make_user(username="resetmeuser")
        monkeypatch.setattr(
            email_queue, "enqueue",
            lambda func, **kwargs: type("J", (), {"id": "x"})()
        )

        resp = client.post(
            "/student/validate-user", json={"data": "resetmeuser"}
        )

        assert resp.get_json()["status"] == "success"


# ============================================================================
# GET/POST /verify-email/<token>
# ============================================================================

class TestVerifyEmail:
    def test_get_renders_page(self, client):
        resp = client.get("/student/verify-email/some-token")

        assert resp.status_code == 200

    def test_post_invalid_token_rejected(self, client):
        resp = client.post("/student/verify-email/not-a-real-token")

        assert resp.get_json()["status"] == "error"

    def test_post_valid_token_verifies_and_auto_logs_in(
        self, client, db_session, make_user, make_email_verification_token
    ):
        user = make_user(email_verified=False, status="pending_verification")
        token_row = make_email_verification_token(user)

        resp = client.post(f"/student/verify-email/{token_row.token}")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert body["data"]["email"] == user.email
        db_session.refresh(user)
        assert user.email_verified is True
        # Auto-login sets cookies
        assert "access_token" in resp.headers.get("Set-Cookie", "") or resp.headers.getlist("Set-Cookie")

    def test_post_already_verified_token_replay_is_idempotent(
        self, client, make_user, make_email_verification_token
    ):
        user = make_user(email_verified=True, status="approved")
        token_row = make_email_verification_token(user, used=True)

        resp = client.post(f"/student/verify-email/{token_row.token}")

        body = resp.get_json()
        assert body["status"] == "success"
        assert body["data"]["already_verified"] is True
