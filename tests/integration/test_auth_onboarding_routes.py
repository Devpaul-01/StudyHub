"""
tests/integration/test_auth_onboarding_routes.py

GET/POST /student/onboard/<email> and GET/POST /student/complete-registration.

Both POST routes authorise the caller through _is_request_authorized_for_email():
either a matching session["google_email"] (new Google user, no JWT yet) or a
matching access_token cookie. Google OAuth /start and /callback need a real
OAuth round trip and are covered in test_auth_google_callback.py with the
OAuth client stubbed.

The legacy /onboard/suggestions*, /onboard/request-all* routes and the
verification-JWT proof were removed (audit AUD-09/16); the authenticated
replacements are tested in test_connections_onboarding_routes.py.
"""
import json
import time

import pytest
from werkzeug.security import check_password_hash

from models import OnboardingDetails, User, StudentProfile

pytestmark = pytest.mark.integration

VALID_ANSWERS = {
    "name": "Updated Name",
    "department": "Computer Science",
    "class_level": "200 Level",
    "subjects": ["Math", "Physics"],
    "learning_style": "visual",
    "study_preferences": ["group-study"],
    "help_subjects": ["Math"],
    "strong_subjects": ["Physics"],
    "study_schedule": {"Monday": ["evening"]},
    "session_length": "1-hour",
}


def _google_proof(client, email):
    with client.session_transaction() as sess:
        sess["google_email"] = email
        sess["google_proof_at"] = int(time.time())


def _cookie_login(client, user):
    from routes.student.helpers import _build_access_token
    client.set_cookie("access_token", _build_access_token(user))


def _registering(make_user, email, status="pending_verification", **kw):
    return make_user(email=email, username=None, pin="PENDING_VERIFICATION",
                     status=status, email_verified=kw.pop("email_verified", True), **kw)


# ============================================================================
# GET/POST /onboard/<email>
# ============================================================================

class TestOnboardGet:
    def test_renders_onboard_page(self, client):
        assert client.get("/student/onboard/someone@example.com").status_code == 200


class TestOnboardPost:
    def test_requires_authorization(self, client, make_user):
        user = make_user(email="noauth@example.com", status="pending_onboarding")
        resp = client.post(f"/student/onboard/{user.email}", json={"name": "New Name"})
        assert resp.status_code == 401

    def test_non_json_body_rejected(self, client, make_user):
        user = _registering(make_user, "nodat@example.com", status="pending_onboarding")
        _google_proof(client, user.email)
        resp = client.post(f"/student/onboard/{user.email}", data=json.dumps(VALID_ANSWERS),
                           content_type="text/plain")
        assert resp.status_code == 400

    def test_unknown_user_rejected(self, client):
        _google_proof(client, "ghost@example.com")
        resp = client.post("/student/onboard/ghost@example.com", json=VALID_ANSWERS)
        assert resp.status_code == 404

    def test_saves_details_issues_tokens_and_returns_valid_redirect(
        self, client, db_session, make_user, make_student_profile
    ):
        user = _registering(make_user, "onboarding@example.com", status="pending_onboarding")
        make_student_profile(user)
        with client.session_transaction() as sess:
            sess["google_email"] = user.email
            sess["google_name"] = "Google Name"
            sess["google_id"] = "google-sub-123"
            sess["google_proof_at"] = int(time.time())

        resp = client.post(f"/student/onboard/{user.email}", json=VALID_ANSWERS)

        assert resp.status_code == 200
        assert resp.get_json()["data"]["redirect"] == "/student/complete-registration?email=onboarding%40example.com"
        db_session.refresh(user)
        assert user.name == "Updated Name" and user.status == "pending_verification"
        details = OnboardingDetails.query.filter_by(user_id=user.id).one()
        assert details.department == "Computer Science" and details.subjects == ["Math", "Physics"]
        assert details.class_level == "200 Level"
        assert StudentProfile.query.filter_by(user_id=user.id).one().class_name == "200 Level"
        assert any(c.startswith("access_token=") for c in resp.headers.getlist("Set-Cookie"))
        with client.session_transaction() as sess:
            assert not {"google_email", "google_name", "google_id", "google_proof_at"} & set(sess)

    def test_email_match_is_case_insensitive(self, client, make_user):
        user = _registering(make_user, "case@example.com", status="pending_onboarding")
        _google_proof(client, "CASE@example.com")
        assert client.post("/student/onboard/CASE@Example.com", json=VALID_ANSWERS).status_code == 200

    def test_resubmission_updates_the_same_row(self, client, make_user, csrf_headers):
        user = _registering(make_user, "reonboard@example.com", status="pending_onboarding")
        _google_proof(client, user.email)
        client.post(f"/student/onboard/{user.email}", json={**VALID_ANSWERS, "department": "Biology"})
        # The first response logged the user in, so the second request carries
        # an access cookie and must pass the normal double-submit CSRF check.
        resp = client.post(f"/student/onboard/{user.email}", json={**VALID_ANSWERS, "department": "Chemistry"},
                           headers=csrf_headers(client))
        assert resp.status_code == 200
        rows = OnboardingDetails.query.filter_by(user_id=user.id).all()
        assert len(rows) == 1 and rows[0].department == "Chemistry"

    def test_approved_user_updates_preferences_without_status_change_or_new_tokens(
        self, client, db_session, make_user, make_student_profile, csrf_headers
    ):
        user = make_user(email="approved@example.com", status="approved")
        make_student_profile(user)
        _cookie_login(client, user)
        resp = client.post(f"/student/onboard/{user.email}", json=VALID_ANSWERS, headers=csrf_headers(client))
        assert resp.status_code == 200
        db_session.refresh(user)
        assert user.status == "approved"
        assert OnboardingDetails.query.filter_by(user_id=user.id).one().department == "Computer Science"
        assert not any(c.startswith(("access_token=", "refresh_token=")) for c in resp.headers.getlist("Set-Cookie"))

    def test_pending_verification_user_stays_pending(self, client, db_session, make_user):
        user = _registering(make_user, "stay@example.com", status="pending_verification")
        _google_proof(client, user.email)
        assert client.post(f"/student/onboard/{user.email}", json=VALID_ANSWERS).status_code == 200
        db_session.refresh(user)
        assert user.status == "pending_verification"

    def test_unverified_email_cannot_onboard(self, client, make_user):
        user = _registering(make_user, "unver@example.com", email_verified=False)
        _google_proof(client, user.email)
        assert client.post(f"/student/onboard/{user.email}", json=VALID_ANSWERS).status_code == 403

    def test_suspended_account_rejected(self, client, make_user):
        user = make_user(email="susp@example.com", status="suspended")
        _google_proof(client, user.email)
        assert client.post(f"/student/onboard/{user.email}", json=VALID_ANSWERS).status_code == 403

    def test_cookie_authenticated_request_needs_csrf_header(self, client, make_user):
        user = _registering(make_user, "csrf@example.com")
        _cookie_login(client, user)
        assert client.post(f"/student/onboard/{user.email}", json=VALID_ANSWERS).status_code == 403

    @pytest.mark.parametrize("field,value", [
        ("class_level", "200"),
        ("learning_style", "telepathic"),
        ("session_length", "forever"),
        ("study_preferences", ["nope"]),
        ("study_schedule", {"Funday": ["morning"]}),
        ("study_schedule", {"Monday": ["midnight"]}),
        ("study_schedule", ["Monday"]),
        ("subjects", "Math"),
        ("subjects", [1, 2]),
        ("subjects", ["x"] * 31),
        ("subjects", ["y" * 101]),
        ("name", 123),
        ("name", "n" * 101),
        ("department", ["CS"]),
        ("department", "d" * 101),
    ])
    def test_invalid_answers_rejected_with_field_error(self, client, make_user, field, value):
        user = _registering(make_user, f"bad-{abs(hash((field, str(value))))}@example.com", status="pending_onboarding")
        _google_proof(client, user.email)
        resp = client.post(f"/student/onboard/{user.email}", json={**VALID_ANSWERS, field: value})
        assert resp.status_code == 400
        assert field in resp.get_json()["errors"]
        assert OnboardingDetails.query.filter_by(user_id=user.id).first() is None

    def test_free_text_department_and_empty_optionals_are_accepted(self, client, make_user):
        user = _registering(make_user, "other@example.com", status="pending_onboarding")
        _google_proof(client, user.email)
        resp = client.post(f"/student/onboard/{user.email}", json={"department": "Marine Biology"})
        assert resp.status_code == 200

    def test_duplicates_are_removed_from_lists(self, client, make_user):
        user = _registering(make_user, "dups@example.com", status="pending_onboarding")
        _google_proof(client, user.email)
        client.post(f"/student/onboard/{user.email}", json={**VALID_ANSWERS, "subjects": ["Math", " Math ", "", "Art"]})
        assert OnboardingDetails.query.filter_by(user_id=user.id).one().subjects == ["Math", "Art"]


# ============================================================================
# GET/POST /complete-registration
# ============================================================================

class TestCompleteRegistrationGet:
    def test_renders_blank_page_with_no_params(self, client):
        assert client.get("/student/complete-registration").status_code == 200

    def test_token_param_is_no_longer_special(self, client):
        assert client.get("/student/complete-registration?token=garbage").status_code == 200

    def test_onboarding_is_optional_so_pending_onboarding_shows_the_form(self, client, make_user):
        user = _registering(make_user, "skip@example.com", status="pending_onboarding")
        resp = client.get(f"/student/complete-registration?email={user.email}")
        assert resp.status_code == 200

    def test_approved_user_redirects_to_homepage(self, client, make_user):
        user = make_user(email="done@example.com", status="approved")
        resp = client.get(f"/student/complete-registration?email={user.email}")
        assert resp.status_code == 302 and "/student/profile/homepage" in resp.headers["Location"]

    def test_pending_verification_user_shows_form(self, client, make_user):
        user = _registering(make_user, "pv@example.com")
        assert client.get(f"/student/complete-registration?email={user.email}").status_code == 200


def _complete(client, email, username="newuser", password="secret123", confirm=None, **extra):
    return client.post("/student/complete-registration", json={
        "email": email, "username": username, "password": password,
        "confirm_password": password if confirm is None else confirm, **extra,
    })


class TestCompleteRegistrationPost:
    def test_requires_authorization(self, client, make_user):
        user = _registering(make_user, "noauthcr@example.com")
        assert _complete(client, user.email).status_code == 401

    def test_text_plain_body_rejected(self, client, make_user):
        user = _registering(make_user, "tp@example.com")
        _google_proof(client, user.email)
        body = json.dumps({"email": user.email, "username": "tpuser", "password": "secret123", "confirm_password": "secret123"})
        resp = client.post("/student/complete-registration", data=body, content_type="text/plain")
        assert resp.status_code == 400
        assert User.query.get(user.id).username is None

    def test_missing_fields_rejected(self, client):
        assert client.post("/student/complete-registration", json={"email": "x@example.com"}).status_code == 400

    def test_non_string_fields_rejected(self, client, make_user):
        user = _registering(make_user, "ns@example.com")
        _google_proof(client, user.email)
        resp = client.post("/student/complete-registration", json={
            "email": user.email, "username": ["a"], "password": "secret123", "confirm_password": "secret123"})
        assert resp.status_code == 400

    def test_mismatched_passwords_rejected(self, client, make_user):
        user = _registering(make_user, "mm@example.com")
        _google_proof(client, user.email)
        assert _complete(client, user.email, confirm="different1").status_code == 400

    def test_short_password_rejected(self, client, make_user):
        user = _registering(make_user, "sp@example.com")
        _google_proof(client, user.email)
        assert _complete(client, user.email, password="abc").status_code == 400

    @pytest.mark.parametrize("username", ["ab", "UPPER space", "has-dash", "x" * 21])
    def test_invalid_username_format_rejected(self, client, make_user, username):
        user = _registering(make_user, f"iu{abs(hash(username))}@example.com")
        _google_proof(client, user.email)
        assert _complete(client, user.email, username=username).status_code == 400

    def test_username_already_taken_rejected(self, client, make_user):
        make_user(email="holder@example.com", username="taken123")
        user = _registering(make_user, "wants@example.com")
        _google_proof(client, user.email)
        resp = _complete(client, user.email, username="taken123")
        assert resp.status_code == 400 and "taken" in resp.get_json()["message"].lower()

    def test_unverified_email_rejected(self, client, make_user):
        user = _registering(make_user, "unv@example.com", email_verified=False)
        _google_proof(client, user.email)
        assert _complete(client, user.email).status_code == 403

    def test_suspended_account_rejected(self, client, make_user):
        user = make_user(email="susp2@example.com", status="suspended", email_verified=True)
        _google_proof(client, user.email)
        assert _complete(client, user.email).status_code == 403

    def test_approved_account_credentials_cannot_be_overwritten(self, client, db_session, make_user, csrf_headers):
        user = make_user(email="victim@example.com", username="original1", status="approved")
        old_hash = user.pin
        _cookie_login(client, user)
        resp = client.post("/student/complete-registration", headers=csrf_headers(client), json={
            "email": user.email, "username": "attacker1", "password": "attackerpw1", "confirm_password": "attackerpw1"})
        assert resp.status_code == 409
        db_session.refresh(user)
        assert user.username == "original1" and user.pin == old_hash

    def test_cookie_authenticated_request_needs_csrf_header(self, client, make_user):
        user = _registering(make_user, "csrfcr@example.com")
        _cookie_login(client, user)
        assert _complete(client, user.email).status_code == 403

    def test_success_via_google_session_sets_credentials_and_logs_in(
        self, client, db_session, make_user, make_student_profile
    ):
        user = _registering(make_user, "complete2@example.com")
        make_student_profile(user)
        _google_proof(client, user.email)

        resp = _complete(client, user.email, username="complete2")

        assert resp.status_code == 200
        db_session.refresh(user)
        assert user.status == "approved" and user.username == "complete2"
        assert check_password_hash(user.pin, "secret123")
        cookies = resp.headers.getlist("Set-Cookie")
        assert any(c.startswith("access_token=") for c in cookies)
        assert any(c.startswith("refresh_token=") for c in cookies)
        with client.session_transaction() as sess:
            assert "google_email" not in sess

    def test_success_without_onboarding_for_pending_onboarding_user(self, client, db_session, make_user):
        user = _registering(make_user, "noonb@example.com", status="pending_onboarding")
        _google_proof(client, user.email)
        assert _complete(client, user.email, username="noonb").status_code == 200
        db_session.refresh(user)
        assert user.status == "approved"
        assert OnboardingDetails.query.filter_by(user_id=user.id).first() is None

    def test_success_via_access_cookie_with_csrf(self, client, db_session, make_user, csrf_headers):
        user = _registering(make_user, "cookie-ok@example.com")
        _cookie_login(client, user)
        resp = client.post("/student/complete-registration", headers=csrf_headers(client), json={
            "email": user.email, "username": "cookieok", "password": "secret123", "confirm_password": "secret123"})
        assert resp.status_code == 200
        db_session.refresh(user)
        assert user.status == "approved"

    def test_username_unique_race_is_reported_not_500(self, client, db_session, make_user, monkeypatch):
        user = _registering(make_user, "race@example.com")
        _google_proof(client, user.email)
        from sqlalchemy.exc import IntegrityError
        real_commit = db_session.commit
        calls = {"n": 0}

        def flaky_commit():
            calls["n"] += 1
            raise IntegrityError("stmt", {}, Exception("unique username"))

        monkeypatch.setattr("routes.student.auth.db.session.commit", flaky_commit)
        resp = _complete(client, user.email, username="racer1")
        assert resp.status_code == 400 and "taken" in resp.get_json()["message"].lower()
