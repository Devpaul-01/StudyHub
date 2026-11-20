"""
Registration, resend, login and forgot-password behaviour
(audit AUD-03, AUD-06, AUD-11, AUD-12, AUD-13).
"""
import re

import pytest

from models import EmailVerificationToken, PasswordResetToken, User
from services import auth_service

pytestmark = pytest.mark.integration

SENTINEL = "PENDING_VERIFICATION"


@pytest.fixture
def mail(monkeypatch, db_session):
    """Capture queued emails: returns a list of dicts (to, html)."""
    from services.job_queue import email_queue
    sent = []

    class _Job:
        id = "job-1"

    def fake_enqueue(func, *args, **kwargs):
        sent.append({"to": kwargs.get("to_email"), "html": kwargs.get("html_content", ""),
                     "subject": kwargs.get("subject", "")})
        return _Job()

    monkeypatch.setattr(email_queue, "enqueue", fake_enqueue)
    return sent


def _link(message):
    return re.search(r'href="([^"]+)"', message["html"]).group(1)


def _signup(client, email="new@example.com", name="New Person"):
    return client.post("/student/register", json={"full_name": name, "email": email})


class TestRegister:
    def test_creates_account_and_emails_a_link(self, client, mail):
        resp = _signup(client)
        assert resp.status_code == 200
        user = User.query.filter_by(email="new@example.com").one()
        assert user.status == "pending_verification" and user.email_verified is False
        assert len(mail) == 1 and mail[0]["to"] == "new@example.com"
        assert "/student/verify-email/" in _link(mail[0])
        assert EmailVerificationToken.query.filter_by(user_id=user.id).count() == 1

    def test_links_ignore_forged_host_headers(self, app, client, mail):
        app.config["PUBLIC_BASE_URL"] = "https://studyhub.example"
        _signup(client, email="host@example.com")
        # (a forged header on a later request must not matter either)
        client.post("/student/register", json={"full_name": "Host", "email": "host@example.com"},
                    headers={"Host": "evil.example", "X-Forwarded-Host": "evil.example"})
        assert all(_link(m).startswith("https://studyhub.example/student/verify-email/") for m in mail)

    def test_same_answer_for_new_unfinished_and_active_accounts(self, client, make_user, mail):
        make_user(email="active@example.com")
        make_user(email="unfinished@example.com", username=None, pin=SENTINEL,
                  status="pending_verification", email_verified=False)
        answers = {
            e: _signup(client, email=e).get_json()
            for e in ("fresh@example.com", "active@example.com", "unfinished@example.com")
        }
        assert len({str(a) for a in answers.values()}) == 1

    def test_active_account_receives_nothing_and_no_duplicate_is_created(self, client, make_user, mail):
        make_user(email="active2@example.com")
        _signup(client, email="active2@example.com")
        assert mail == [] and User.query.filter_by(email="active2@example.com").count() == 1

    def test_email_is_normalised(self, client, mail):
        _signup(client, email="  MiXed@Example.COM ")
        assert User.query.filter_by(email="mixed@example.com").count() == 1

    @pytest.mark.parametrize("payload", [
        {"full_name": "A", "email": "not-an-email"},
        {"full_name": "", "email": "a@example.com"},
        {"full_name": ["x"], "email": "a@example.com"},
        {"full_name": "A", "email": 5},
        {"full_name": "n" * 101, "email": "a@example.com"},
        {"full_name": "A", "email": "a" * 120 + "@example.com"},
    ])
    def test_invalid_input_rejected(self, client, mail, payload):
        before = User.query.count()
        assert client.post("/student/register", json=payload).status_code == 400
        assert User.query.count() == before and mail == []

    def test_google_verified_claim_is_ignored(self, client, mail):
        with client.session_transaction() as sess:
            sess["google_email"] = "g@example.com"
            sess["google_proof_at"] = 2 ** 31
        client.post("/student/register", json={"full_name": "G", "email": "g@example.com", "google_verified": True})
        assert User.query.filter_by(email="g@example.com").one().email_verified is False

    def test_queue_failure_does_not_strand_the_account(self, client, monkeypatch, mail):
        from services.job_queue import email_queue
        monkeypatch.setattr(email_queue, "enqueue", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("redis down")))
        resp = _signup(client, email="retry@example.com")
        assert resp.status_code == 503
        assert User.query.filter_by(email="retry@example.com").count() == 1

        # Redis is back: signing up again sends a fresh link instead of failing.
        monkeypatch.setattr(email_queue, "enqueue",
                            lambda f, *a, **k: mail.append({"to": k["to_email"], "html": k["html_content"], "subject": ""}) or type("J", (), {"id": 1})())
        assert _signup(client, email="retry@example.com").status_code == 200
        assert len(mail) == 1

    def test_unfinished_signup_gets_a_new_link_and_the_old_one_stops_working(self, client, mail):
        _signup(client, email="again@example.com")
        _signup(client, email="again@example.com")
        old, new = (_link(m).rsplit("/", 1)[1] for m in mail)
        assert client.post(f"/student/verify-email/{old}").status_code == 400
        assert client.post(f"/student/verify-email/{new}").status_code == 200


class TestResendVerification:
    def test_pending_user_gets_a_link(self, client, make_user, mail):
        make_user(email="p@example.com", username=None, pin=SENTINEL, status="pending_verification", email_verified=True)
        resp = client.post("/student/resend-verification", json={"email": "p@example.com"})
        assert resp.status_code == 200 and len(mail) == 1

    def test_unknown_and_active_accounts_get_the_same_answer_and_no_email(self, client, make_user, mail):
        make_user(email="act@example.com")
        a = client.post("/student/resend-verification", json={"email": "act@example.com"}).get_json()
        b = client.post("/student/resend-verification", json={"email": "ghost@example.com"}).get_json()
        assert a == b and mail == []

    def test_invalid_email_rejected(self, client):
        assert client.post("/student/resend-verification", json={"email": "nope"}).status_code == 400

    def test_per_email_limit(self, client, make_user, mail, fakeredis_client):
        make_user(email="lim@example.com", username=None, pin=SENTINEL, status="pending_verification")
        codes = [client.post("/student/resend-verification", json={"email": "lim@example.com"}).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]


class TestResumeAfterVerifiedButUnfinished:
    """The state the audit found permanently stuck (AUD-03)."""

    def test_forgot_password_then_login_finishes_registration(self, client, db_session, make_user, mail):
        user = make_user(email="stuck@example.com", username=None, pin=SENTINEL,
                         status="pending_verification", email_verified=True)
        assert client.post("/student/validate-user", json={"data": "stuck@example.com"}).status_code == 200
        raw = _link(mail[0]).split("/verify-reset/")[1]

        assert client.post("/student/set-password", json={
            "token": raw, "password": "chosen123", "confirm_password": "chosen123"}).status_code == 200

        login = client.post("/student/login", json={"username_or_email": "stuck@example.com", "password": "chosen123"})
        assert login.status_code == 200
        assert login.get_json()["data"]["redirect"].startswith("/student/complete-registration")

        done = client.post("/student/complete-registration", headers=_csrf(client), json={
            "email": "stuck@example.com", "username": "finally1", "password": "chosen123", "confirm_password": "chosen123"})
        assert done.status_code == 200
        db_session.refresh(user)
        assert user.status == "approved" and user.username == "finally1"

    def test_resend_link_resumes_with_a_session(self, client, make_user, mail):
        make_user(email="resume@example.com", username=None, pin=SENTINEL,
                  status="pending_verification", email_verified=True)
        client.post("/student/resend-verification", json={"email": "resume@example.com"})
        token = _link(mail[0]).rsplit("/", 1)[1]
        resp = client.post(f"/student/verify-email/{token}")
        assert resp.status_code == 200
        assert any(c.startswith("access_token=") for c in resp.headers.getlist("Set-Cookie"))


def _csrf(client):
    client.set_cookie("csrf_token", "c" * 16)
    return {"X-CSRF-Token": "c" * 16}


class TestLogin:
    def _login(self, client, who, password="testpassword123"):
        return client.post("/student/login", json={"username_or_email": who, "password": password})

    def test_unknown_wrong_password_and_passwordless_accounts_are_indistinguishable(self, client, make_user):
        make_user(username="real", email="real@example.com")
        make_user(username=None, email="nopw@example.com", pin=SENTINEL, status="pending_verification", email_verified=True)
        make_user(username="unver", email="unver@example.com", email_verified=False)
        responses = [
            self._login(client, "ghost@example.com"),
            self._login(client, "real", "wrong-password"),
            self._login(client, "nopw@example.com"),
            self._login(client, "unver", "wrong-password"),
        ]
        assert {r.status_code for r in responses} == {400}
        assert len({r.get_json()["message"] for r in responses}) == 1

    def test_state_is_only_described_after_the_password_was_proven(self, client, make_user):
        make_user(username="unver2", email="unver2@example.com", email_verified=False)
        resp = self._login(client, "unver2")
        assert "verify your email" in resp.get_json()["message"].lower()

    def test_suspended_account_cannot_log_in(self, client, make_user):
        make_user(username="susp", status="suspended")
        assert self._login(client, "susp").status_code == 400

    def test_non_string_credentials_rejected(self, client):
        assert client.post("/student/login", json={"username_or_email": ["a"], "password": "x"}).status_code == 400
        assert client.post("/student/login", json={"username_or_email": "a", "password": 5}).status_code == 400

    def test_failed_attempts_are_limited_per_account_and_reset_on_success(self, client, make_user, fakeredis_client):
        make_user(username="victim", email="victim@example.com")
        for _ in range(10):
            assert self._login(client, "victim", "bad-password").status_code == 400
        assert self._login(client, "victim", "bad-password").status_code == 429
        # even the right password is refused while locked: no guessing oracle
        assert self._login(client, "victim").status_code == 429

    def test_success_resets_the_counter(self, client, make_user, fakeredis_client):
        make_user(username="resetme", email="resetme@example.com")
        for _ in range(5):
            self._login(client, "resetme", "bad")
        assert self._login(client, "resetme").status_code == 200
        for _ in range(9):
            assert self._login(client, "resetme", "bad").status_code == 400


class TestForgotPassword:
    def test_unknown_and_known_look_the_same(self, client, make_user, mail):
        make_user(email="known@example.com", username="knownuser")
        a = client.post("/student/validate-user", json={"data": "known@example.com"})
        b = client.post("/student/validate-user", json={"data": "ghost@example.com"})
        assert a.status_code == b.status_code == 200 and a.get_json() == b.get_json()
        assert len(mail) == 1

    def test_username_works_and_link_uses_one_hour_text(self, client, make_user, mail):
        make_user(email="uname@example.com", username="byname")
        client.post("/student/validate-user", json={"data": "ByName"})
        assert len(mail) == 1 and "1 hour" in mail[0]["html"]

    def test_unverified_registration_gets_a_verification_link_not_a_reset_link(self, client, make_user, mail):
        make_user(email="unv@example.com", username=None, pin=SENTINEL,
                  status="pending_verification", email_verified=False)
        client.post("/student/validate-user", json={"data": "unv@example.com"})
        assert len(mail) == 1 and "/verify-email/" in _link(mail[0])
        assert PasswordResetToken.query.count() == 0

    def test_per_account_limit_blocks_mail_flooding(self, client, make_user, mail, fakeredis_client):
        make_user(email="flood@example.com", username="flooded")
        codes = [client.post("/student/validate-user", json={"data": "flood@example.com"}).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429] and len(mail) == 3

    @pytest.mark.parametrize("payload", [{}, {"data": ""}, {"data": 5}, {"data": ["a"]}])
    def test_bad_input_rejected(self, client, payload):
        assert client.post("/student/validate-user", json=payload).status_code == 400

    def test_queue_failure_still_gives_the_generic_answer(self, client, make_user, monkeypatch):
        from services.job_queue import email_queue
        make_user(email="q@example.com", username="qq")
        monkeypatch.setattr(email_queue, "enqueue", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
        assert client.post("/student/validate-user", json={"data": "q@example.com"}).status_code == 200
