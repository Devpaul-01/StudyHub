"""
tests/integration/test_auth_password_reset_and_misc.py

routes/student/auth.py has 24 endpoints; the existing integration suite
(test_auth_lifecycle.py) only covers register/login/logout/refresh-token
and profile/me/data. This file covers the password-reset flow
(verify-reset peek + set-password consume, per that module's own
documented split between a read-only token peek and the actual
single-use consumption), plus check-username and verify-auth, none of
which had any test at any level before this.
"""

import datetime

import pytest
from freezegun import freeze_time

pytestmark = pytest.mark.integration


# ============================================================================
# GET /student/reset-password, GET /student/verify-reset/<token>,
# GET /student/set-password — plain template renders
# ============================================================================

class TestPasswordResetPageRenders:
    def test_reset_password_request_page_renders(self, client):
        resp = client.get("/student/reset-password")
        assert resp.status_code == 200

    def test_verify_reset_page_renders_for_get(self, client):
        resp = client.get("/student/verify-reset/some-token")
        assert resp.status_code == 200

    def test_set_password_page_renders_for_get(self, client):
        resp = client.get("/student/set-password")
        assert resp.status_code == 200


# ============================================================================
# POST /student/verify-reset/<token> — read-only peek, does NOT consume
# ============================================================================

class TestVerifyResetToken:
    def test_valid_token_returns_success_with_email_and_redirect(
        self, client, make_user, make_password_reset_token
    ):
        user = make_user(email="reset-me@example.com")
        reset_row = make_password_reset_token(user)

        resp = client.post(f"/student/verify-reset/{reset_row.token}")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["email"] == "reset-me@example.com"
        assert body["data"]["token"] == reset_row.token
        assert "redirect_url" in body["data"]

    def test_valid_token_is_not_marked_used_by_the_peek(
        self, client, db_session, make_user, make_password_reset_token
    ):
        """
        The whole point of this route being split from set_password() is
        that verifying/previewing the link must not burn the single-use
        token — only set_password() actually consumes it.
        """
        user = make_user()
        reset_row = make_password_reset_token(user)

        client.post(f"/student/verify-reset/{reset_row.token}")

        from models import PasswordResetToken
        refreshed = PasswordResetToken.query.filter_by(token=reset_row.token).first()
        assert refreshed.used is False

    def test_nonexistent_token_returns_error(self, client):
        resp = client.post("/student/verify-reset/this-token-does-not-exist")

        assert resp.status_code == 400
        assert "invalid or has expired" in resp.get_json()["message"]

    def test_expired_token_returns_error(self, client, make_user, make_password_reset_token):
        user = make_user()
        reset_row = make_password_reset_token(
            user, expires_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
        )

        resp = client.post(f"/student/verify-reset/{reset_row.token}")

        assert resp.status_code == 400
        assert "invalid or has expired" in resp.get_json()["message"]

    def test_already_used_token_returns_error(self, client, make_user, make_password_reset_token):
        user = make_user()
        reset_row = make_password_reset_token(user, used=True)

        resp = client.post(f"/student/verify-reset/{reset_row.token}")

        assert resp.status_code == 400
        assert "invalid or has expired" in resp.get_json()["message"]


# ============================================================================
# POST /student/set-password — actually consumes the token
# ============================================================================

class TestSetPassword:
    def test_missing_token_returns_error(self, client):
        resp = client.post("/student/set-password", json={
            "password": "newpass123", "confirm_password": "newpass123",
        })

        assert resp.status_code == 400
        assert "Reset token is required" in resp.get_json()["message"]

    def test_nonexistent_token_returns_error(self, client):
        resp = client.post("/student/set-password", json={
            "token": "does-not-exist",
            "password": "newpass123", "confirm_password": "newpass123",
        })

        assert resp.status_code == 400

    def test_expired_token_returns_error(self, client, make_user, make_password_reset_token):
        user = make_user()
        reset_row = make_password_reset_token(
            user, expires_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
        )

        resp = client.post("/student/set-password", json={
            "token": reset_row.token,
            "password": "newpass123", "confirm_password": "newpass123",
        })

        assert resp.status_code == 400

    def test_mismatched_passwords_returns_error(self, client, make_user, make_password_reset_token):
        user = make_user()
        reset_row = make_password_reset_token(user)

        resp = client.post("/student/set-password", json={
            "token": reset_row.token,
            "password": "newpass123", "confirm_password": "different456",
        })

        assert resp.status_code == 400
        assert "do not match" in resp.get_json()["message"]

    def test_password_too_short_returns_error(self, client, make_user, make_password_reset_token):
        user = make_user()
        reset_row = make_password_reset_token(user)

        resp = client.post("/student/set-password", json={
            "token": reset_row.token,
            "password": "ab1", "confirm_password": "ab1",
        })

        assert resp.status_code == 400
        assert "at least 6 characters" in resp.get_json()["message"]

    def test_valid_submission_updates_password_and_consumes_token(
        self, client, db_session, make_user, make_password_reset_token
    ):
        user = make_user(email="resetme2@example.com")
        reset_row = make_password_reset_token(user)

        resp = client.post("/student/set-password", json={
            "token": reset_row.token,
            "password": "brandnewpass123", "confirm_password": "brandnewpass123",
        })

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["redirect_url"] == "/student/login"

        from models import PasswordResetToken, User
        refreshed_token = PasswordResetToken.query.filter_by(token=reset_row.token).first()
        assert refreshed_token.used is True

        refreshed_user = User.query.filter_by(email="resetme2@example.com").first()
        from werkzeug.security import check_password_hash
        assert check_password_hash(refreshed_user.pin, "brandnewpass123")

    def test_reusing_an_already_consumed_token_is_rejected(
        self, client, make_user, make_password_reset_token
    ):
        """Single-use enforcement: a second set-password call with the
        same token (already consumed by the first) must be rejected."""
        user = make_user()
        reset_row = make_password_reset_token(user)

        first = client.post("/student/set-password", json={
            "token": reset_row.token,
            "password": "firstpass123", "confirm_password": "firstpass123",
        })
        assert first.status_code == 200

        second = client.post("/student/set-password", json={
            "token": reset_row.token,
            "password": "secondpass456", "confirm_password": "secondpass456",
        })

        assert second.status_code == 400


# ============================================================================
# POST /student/check-username
# ============================================================================

class TestCheckUsername:
    def test_no_data_returns_error(self, client):
        resp = client.post("/student/check-username", json={})
        assert resp.status_code == 400

    def test_empty_username_returns_error(self, client):
        resp = client.post("/student/check-username", json={"username": "   "})
        assert resp.status_code == 400
        assert "required" in resp.get_json()["message"].lower()

    @pytest.mark.parametrize("bad_username", ["ab", "has spaces", "Has-Caps!", "x" * 21])
    def test_invalid_format_returns_error(self, client, bad_username):
        resp = client.post("/student/check-username", json={"username": bad_username})

        assert resp.status_code == 400
        assert "Invalid username format" in resp.get_json()["message"]

    def test_taken_username_returns_error(self, client, make_user):
        make_user(username="alreadytaken")

        resp = client.post("/student/check-username", json={"username": "alreadytaken"})

        assert resp.status_code == 400
        assert "taken" in resp.get_json()["message"].lower()

    def test_available_username_returns_success(self, client):
        resp = client.post("/student/check-username", json={"username": "brandnewname123"})

        assert resp.status_code == 200
        assert resp.get_json()["data"]["available"] is True

    def test_username_check_is_case_insensitive_against_existing_users(self, client, make_user):
        make_user(username="lowercaseuser")

        resp = client.post("/student/check-username", json={"username": "LOWERCASEUSER"})

        # Route lowercases the input before querying, so an existing
        # lowercase username must still be detected as taken.
        assert resp.status_code == 400
        assert "taken" in resp.get_json()["message"].lower()


# ============================================================================
# GET /student/verify-auth
# ============================================================================

class TestVerifyAuth:
    def test_no_cookie_returns_401_not_authenticated(self, client):
        resp = client.get("/student/verify-auth")

        assert resp.status_code == 401
        assert resp.get_json()["authenticated"] is False

    def test_invalid_token_cookie_returns_401(self, client):
        client.set_cookie("access_token", "not-a-real-jwt", domain="test.local")

        resp = client.get("/student/verify-auth")

        assert resp.status_code == 401
        assert resp.get_json()["authenticated"] is False

    def test_valid_token_cookie_returns_authenticated_with_user_data(self, client, make_user):
        from routes.student.helpers import _build_access_token

        user = make_user(username="verifyauthuser", email="verifyauth@example.com")
        token = _build_access_token(user)
        client.set_cookie("access_token", token, domain="test.local")

        resp = client.get("/student/verify-auth")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["authenticated"] is True
        assert body["data"]["user"]["username"] == "verifyauthuser"
        assert body["data"]["user"]["email"] == "verifyauth@example.com"

    def test_expired_token_cookie_returns_401_with_should_refresh(self, client, make_user):
        from routes.student.helpers import _build_access_token

        user = make_user()
        with freeze_time("2026-01-01 12:00:00"):
            token = _build_access_token(user)  # 30-minute expiry

        client.set_cookie("access_token", token, domain="test.local")

        with freeze_time("2026-01-01 12:31:00"):  # 31 minutes later
            resp = client.get("/student/verify-auth")

        assert resp.status_code == 401
        body = resp.get_json()
        assert body["authenticated"] is False
        assert body["should_refresh"] is True
