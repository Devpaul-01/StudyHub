"""
Google OAuth callback (audit AUD-10). The Google client is stubbed; no network.
"""
import time

import pytest

from models import User

pytestmark = pytest.mark.integration

SENTINEL = "PENDING_VERIFICATION"


@pytest.fixture
def google(monkeypatch, db_session):
    """Control what Google 'returns' for the signed-in account."""
    state = {"info": {"id": "sub-1", "email": "g@example.com", "name": "G User", "verified_email": True},
             "authorized": True, "ok": True}

    class _Resp:
        def __init__(self, ok, payload):
            self.ok, self._payload = ok, payload

        def json(self):
            return self._payload

    class _Google:
        @property
        def authorized(self):
            return state["authorized"]

        def get(self, url):
            return _Resp(state["ok"], state["info"])

    monkeypatch.setattr("routes.student.auth.google", _Google())
    return state


def _cb(client):
    return client.get("/google/callback")


def _cookies(resp):
    return [c.split("=")[0] for c in resp.headers.getlist("Set-Cookie")]


class TestNewUser:
    def test_creates_account_sets_fresh_proof_and_redirects_to_onboarding(self, client, google):
        resp = _cb(client)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/student/onboard/g%40example.com")
        user = User.query.filter_by(email="g@example.com").one()
        assert user.status == "pending_onboarding" and user.email_verified is True and user.google_id == "sub-1"
        with client.session_transaction() as sess:
            assert sess["google_email"] == "g@example.com" and isinstance(sess["google_proof_at"], int)

    def test_unverified_google_email_is_refused(self, client, google):
        google["info"]["verified_email"] = False
        resp = _cb(client)
        assert "error=email_not_verified" in resp.headers["Location"]
        assert User.query.filter_by(email="g@example.com").first() is None
        with client.session_transaction() as sess:
            assert "google_email" not in sess

    def test_missing_verified_flag_is_refused(self, client, google):
        del google["info"]["verified_email"]
        assert "error=email_not_verified" in _cb(client).headers["Location"]

    def test_empty_name_falls_back_to_email_prefix(self, client, google):
        google["info"]["name"] = ""
        _cb(client)
        assert User.query.filter_by(email="g@example.com").one().name == "g"

    @pytest.mark.parametrize("state", [{"authorized": False}, {"ok": False}])
    def test_oauth_failures_redirect_to_login(self, client, google, state):
        google.update(state)
        assert "error=oauth_failed" in _cb(client).headers["Location"]


class TestExistingUsers:
    def test_approved_google_account_is_logged_in(self, client, make_user, google):
        make_user(email="g@example.com", google_id="sub-1")
        resp = _cb(client)
        assert resp.headers["Location"].endswith("/student/profile/homepage")
        assert "access_token" in _cookies(resp) and "refresh_token" in _cookies(resp)

    def test_sub_mismatch_is_refused(self, client, make_user, google):
        make_user(email="g@example.com", google_id="someone-else")
        resp = _cb(client)
        assert "error=oauth_failed" in resp.headers["Location"]
        assert "access_token" not in _cookies(resp)

    def test_password_created_account_is_not_taken_over(self, client, make_user, google):
        make_user(email="g@example.com", google_id=None)  # real password hash, no google_id
        resp = _cb(client)
        assert "error=account_exists_use_password" in resp.headers["Location"]
        assert "access_token" not in _cookies(resp)
        assert User.query.filter_by(email="g@example.com").one().google_id is None

    def test_unfinished_password_signup_is_claimed_and_marked_verified(self, client, db_session, make_user, google):
        user = make_user(email="g@example.com", username=None, pin=SENTINEL, google_id=None,
                         status="pending_verification", email_verified=False)
        resp = _cb(client)
        assert resp.headers["Location"].endswith("/student/complete-registration?email=g%40example.com")
        db_session.refresh(user)
        assert user.google_id == "sub-1" and user.email_verified is True

    def test_pending_onboarding_user_continues_with_a_proof(self, client, make_user, google):
        make_user(email="g@example.com", username=None, pin=SENTINEL, google_id="sub-1", status="pending_onboarding")
        assert _cb(client).headers["Location"].endswith("/student/onboard/g%40example.com")
        with client.session_transaction() as sess:
            assert sess["google_email"] == "g@example.com"

    def test_suspended_account_gets_no_session_and_no_proof(self, client, make_user, google):
        make_user(email="g@example.com", google_id="sub-1", status="suspended")
        resp = _cb(client)
        assert "error=account_inactive" in resp.headers["Location"]
        assert "access_token" not in _cookies(resp)
        with client.session_transaction() as sess:
            assert "google_email" not in sess


class TestProofLifetime:
    def _onboard(self, client, email):
        return client.post(f"/student/onboard/{email}", json={"department": "Biology"})

    def test_fresh_proof_is_accepted(self, client, make_user):
        make_user(email="p@example.com", username=None, pin=SENTINEL, status="pending_onboarding")
        with client.session_transaction() as sess:
            sess["google_email"], sess["google_proof_at"] = "p@example.com", int(time.time())
        assert self._onboard(client, "p@example.com").status_code == 200

    def test_expired_proof_is_rejected(self, client, make_user):
        make_user(email="old@example.com", username=None, pin=SENTINEL, status="pending_onboarding")
        with client.session_transaction() as sess:
            sess["google_email"] = "old@example.com"
            sess["google_proof_at"] = int(time.time()) - 31 * 60
        assert self._onboard(client, "old@example.com").status_code == 401

    def test_legacy_proof_without_timestamp_is_rejected(self, client, make_user):
        make_user(email="nots@example.com", username=None, pin=SENTINEL, status="pending_onboarding")
        with client.session_transaction() as sess:
            sess["google_email"] = "nots@example.com"
        assert self._onboard(client, "nots@example.com").status_code == 401
