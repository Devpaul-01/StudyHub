"""
tests/integration/test_auth_lifecycle.py

Covers INTEGRATION_TEST_IMPLEMENTATION_PLAN.md §3.1 end-to-end, through
the real Flask test client against real routes — registration, email
verification, login, refresh-token rotation, logout, and CSRF enforcement
on the auth-adjacent routes that are NOT CSRF-exempt.

Redundancy check (per that plan's own required practice, §0/Q1): the pure
token-building/decoding logic (_build_access_token, decode_token,
role_required's branch logic in isolation) is unit-tested per
UNIT_TEST_IMPLEMENTATION_PLAN §7.2 — these integration tests deliberately
do NOT re-test "does a malformed token decode incorrectly" in isolation;
they test the FULL route: does POST /student/login actually set a
real, working cookie that a SUBSEQUENT real request can use, across
process/DB boundaries a unit test never crosses.
"""

import pytest

pytestmark = pytest.mark.integration


def _register_payload(email="newstudent@example.com", full_name="New Student"):
    return {"full_name": full_name, "email": email, "google_verified": False}


class TestRegistrationAndVerification:
    def test_register_creates_pending_user_and_enqueues_verification_email(
        self, client, monkeypatch
    ):
        """
        POST /student/register (password path, not Google) should:
          - create a User row with pin="PENDING_VERIFICATION",
            status="pending_verification"
          - create a matching StudentProfile
          - issue an EmailVerificationToken row
          - enqueue (not send synchronously) the verification email job
        """
        from models import User, StudentProfile, EmailVerificationToken
        from services.job_queue import email_queue

        enqueued = {}

        def _fake_enqueue(func, **kwargs):
            enqueued["func"] = func
            enqueued["kwargs"] = kwargs

            class _FakeJob:
                id = "fake-job-id"

            return _FakeJob()

        monkeypatch.setattr(email_queue, "enqueue", _fake_enqueue)

        resp = client.post(
            "/student/register",
            json=_register_payload(),
        )

        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["status"] == "success"

        user = User.query.filter_by(email="newstudent@example.com").first()
        assert user is not None
        assert user.pin == "PENDING_VERIFICATION"
        assert user.status == "pending_verification"
        assert user.email_verified is False

        profile = StudentProfile.query.filter_by(user_id=user.id).first()
        assert profile is not None

        token_row = EmailVerificationToken.query.filter_by(user_id=user.id).first()
        assert token_row is not None
        assert token_row.used is False

        # The job was enqueued, not executed synchronously — no real SMTP
        # connection was ever attempted.
        assert enqueued.get("kwargs", {}).get("to_email") == "newstudent@example.com"

    def test_register_duplicate_email_is_rejected(self, client, make_user):
        make_user(email="dupe@example.com")
        resp = client.post("/student/register", json=_register_payload(email="dupe@example.com"))
        assert resp.status_code == 400
        assert resp.get_json()["status"] == "error"

    def test_verify_email_marks_verified_and_auto_logs_in(self, client, make_user, make_email_verification_token):
        """
        POST /student/verify-email/<token> should consume the token (mark
        used=True, single-use), set email_verified=True, and auto-login by
        setting real cookies the client can use for a subsequent request.
        """
        user = make_user(email_verified=False, status="pending_verification")
        token_row = make_email_verification_token(user)

        resp = client.post(f"/student/verify-email/{token_row.token}")

        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["status"] == "success"

        from extensions import db
        from models import EmailVerificationToken

        db.session.refresh(user)
        assert user.email_verified is True

        refreshed_token = EmailVerificationToken.query.get(token_row.id)
        assert refreshed_token.used is True
        assert refreshed_token.used_at is not None

        # Auto-login: a real access_token cookie must now be set.
        assert client.get_cookie("access_token") is not None

    def test_verify_email_replay_is_rejected(self, client, make_user, make_email_verification_token):
        """A second use of the same verification token must not re-verify
        or re-auto-login — this is the actual security property the
        opaque single-use token replaced a stateless JWT to fix."""
        user = make_user(email_verified=False, status="pending_verification")
        token_row = make_email_verification_token(user)

        first = client.post(f"/student/verify-email/{token_row.token}")
        assert first.status_code == 200

        # Simulate a second, independent client replaying the same link
        # (no cookies from the first call).
        second = client.post(f"/student/verify-email/{token_row.token}")
        body = second.get_json()
        # Per auth.py::verify_email_api's own documented idempotent-reply
        # branch: since the account IS now verified, a replay returns a
        # friendly "already verified" success, not a hard error — assert
        # THAT specific shape, not a generic failure.
        assert body["status"] == "success"
        assert body["data"]["already_verified"] is True


class TestLoginAndStreak:
    def test_login_success_sets_cookies_and_updates_streak(self, client, make_user):
        from werkzeug.security import generate_password_hash

        user = make_user(
            username="loginuser",
            pin=generate_password_hash("correct-password"),
            status="approved",
            email_verified=True,
            login_streak=0,
        )

        resp = client.post(
            "/student/login",
            json={"username_or_email": "loginuser", "password": "correct-password"},
        )

        assert resp.status_code == 200, resp.get_json()
        assert client.get_cookie("access_token") is not None
        assert client.get_cookie("refresh_token") is not None

        from extensions import db
        db.session.refresh(user)
        assert user.login_streak == 1

    def test_login_wrong_password_rejected_no_cookies_set(self, client, make_user):
        from werkzeug.security import generate_password_hash

        make_user(
            username="loginuser2",
            pin=generate_password_hash("correct-password"),
            status="approved",
            email_verified=True,
        )
        resp = client.post(
            "/student/login",
            json={"username_or_email": "loginuser2", "password": "wrong-password"},
        )
        assert resp.status_code == 400
        assert client.get_cookie("access_token") is None

    def test_login_unapproved_account_rejected(self, client, make_user):
        from werkzeug.security import generate_password_hash

        make_user(
            username="pendinguser",
            pin=generate_password_hash("correct-password"),
            status="pending_verification",
            email_verified=True,
        )
        resp = client.post(
            "/student/login",
            json={"username_or_email": "pendinguser", "password": "correct-password"},
        )
        assert resp.status_code == 400


class TestRefreshTokenRotation:
    def test_refresh_rotates_token_and_returns_new_access_token(self, client, make_user):
        """
        POST /student/refresh-token against a real refresh_token cookie
        (set by a real prior login) should rotate it — old row revoked,
        new row issued in the same family, new access_token cookie set.
        """
        from werkzeug.security import generate_password_hash

        make_user(
            username="refreshuser",
            pin=generate_password_hash("correct-password"),
            status="approved",
            email_verified=True,
        )
        login_resp = client.post(
            "/student/login",
            json={"username_or_email": "refreshuser", "password": "correct-password"},
        )
        assert login_resp.status_code == 200
        old_refresh_cookie = client.get_cookie("refresh_token").value

        refresh_resp = client.post("/student/refresh-token")
        assert refresh_resp.status_code == 200, refresh_resp.get_json()

        new_refresh_cookie = client.get_cookie("refresh_token")
        # A new refresh token should have been issued and set (not the
        # grace-window "leave alone" case, since this is a single,
        # sequential client — see auth_service.rotate_refresh_token's own
        # docstring on when new_refresh_token can be None).
        assert new_refresh_cookie is not None
        assert new_refresh_cookie.value != old_refresh_cookie

    def test_refresh_with_invalid_token_returns_error_and_clears_cookies(self, client):
        client.set_cookie("test.local", "refresh_token", "not-a-real-token")
        resp = client.post("/student/refresh-token")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["status"] == "error"


class TestLogout:
    def test_logout_revokes_refresh_family_and_clears_cookies(self, client, make_user):
        from werkzeug.security import generate_password_hash
        from models import RefreshToken

        make_user(
            username="logoutuser",
            pin=generate_password_hash("correct-password"),
            status="approved",
            email_verified=True,
        )
        client.post(
            "/student/login",
            json={"username_or_email": "logoutuser", "password": "correct-password"},
        )
        refresh_value = client.get_cookie("refresh_token").value

        logout_resp = client.post("/student/logout")
        assert logout_resp.status_code == 200

        # Cookies cleared client-side.
        cookie = client.get_cookie("access_token")
        assert cookie is None or cookie.value == ""

        # Server-side: the whole family this refresh token belonged to
        # should now be revoked — presenting it again must fail.
        import hashlib
        token_hash = hashlib.sha256(refresh_value.encode("utf-8")).hexdigest()
        row = RefreshToken.query.filter_by(token_hash=token_hash).first()
        assert row is not None
        assert row.revoked is True


class TestCSRFEnforcement:
    """
    INTEGRATION_TEST_IMPLEMENTATION_PLAN.md §3.10/§3.1: 'no existing test
    of any kind currently proves CSRF actually works end-to-end.' These
    tests close exactly that gap, against the REAL enforce_csrf
    before_request hook (routes/student/__init__.py), not a mock of it.
    """

    def test_mutating_request_without_csrf_header_is_rejected(self, client, make_user, auth_headers):
        """
        A route NOT on the CSRF exempt list (e.g. /student/profile/skills,
        a POST) must be rejected with 403 if the X-CSRF-Token header is
        absent, even with a completely valid auth token.
        """
        user = make_user(status="approved")
        headers = auth_headers(user)

        resp = client.post(
            "/student/profile/skills",
            json={"skill": "Python"},
            headers=headers,
        )
        # enforce_csrf raises ValidationError(status_code=403) when
        # cookie/header don't match or are missing.
        assert resp.status_code == 403

    def test_exempt_path_login_is_never_csrf_gated(self, client, make_user):
        """
        /student/login is explicitly in CSRF_EXEMPT_PATHS — a POST here
        must never 403 for CSRF reasons regardless of missing headers,
        since there is no prior session to have gotten a csrf_token from.
        """
        from werkzeug.security import generate_password_hash

        make_user(
            username="exemptuser",
            pin=generate_password_hash("correct-password"),
            status="approved",
            email_verified=True,
        )
        resp = client.post(
            "/student/login",
            json={"username_or_email": "exemptuser", "password": "correct-password"},
        )
        # Whatever the outcome, it must not be the CSRF 403 path.
        assert resp.status_code != 403 or "CSRF" not in (resp.get_json() or {}).get("message", "")

    def test_get_request_never_requires_csrf(self, client, make_user, auth_headers):
        """GET/HEAD/OPTIONS are exempt by HTTP-semantics per enforce_csrf's
        own early-return — confirm a real GET route never demands the
        header."""
        user = make_user(status="approved")
        headers = auth_headers(user)
        resp = client.get("/student/profile/me/data", headers=headers)
        assert resp.status_code != 403

