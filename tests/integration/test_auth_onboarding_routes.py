"""
tests/integration/test_auth_onboarding_routes.py

Covers the remaining untested onboarding-flow routes in
routes/student/auth.py: GET/POST /onboard/<email> (the main onboarding
form submission), POST /onboard/request-all/<email> (sends connection
requests during onboarding), GET /onboard/suggestions/<token> (JWT-token
variant of the suggestions-by-email route covered in Batch 40), and
GET/POST /complete-registration. Google OAuth /start and /callback are
left untested -- they require a real OAuth round trip this test
environment cannot simulate.

Both onboard() and complete_registration() authorize the caller via
_is_request_authorized_for_email(), which accepts either a matching
session["google_email"] or a matching JWT access-token cookie. Tests use
whichever proof fits the scenario -- a session value for "mid-onboarding,
no JWT yet" cases, and auth_headers() (a real JWT cookie) once a user is
far enough along to have one. complete_registration() also accepts a
verification token (utils.generate_verification_token-shaped JWT) as a
third proof, used directly here via jwt.encode with the test SECRET_KEY.
"""

import datetime

import jwt
import pytest

pytestmark = pytest.mark.integration


def _verification_token(email, secret_key="test-secret-key-not-for-production", hours=5):
    """Build a utils.generate_verification_token-shaped JWT by hand."""
    payload = {
        "email": email,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=hours),
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


# ============================================================================
# GET/POST /onboard/<email>
# ============================================================================

class TestOnboardGet:
    def test_renders_onboard_page(self, client):
        resp = client.get("/student/onboard/someone@example.com")

        assert resp.status_code == 200


class TestOnboardPost:
    def test_requires_authorization(self, client, make_user):
        user = make_user(email="noauth@example.com", status="pending_onboarding")

        resp = client.post(
            f"/student/onboard/{user.email}",
            json={"name": "New Name"},
        )

        assert resp.status_code == 401
        assert resp.get_json()["status"] == "error"

    def test_rejects_when_no_data_provided(self, client, make_user):
        user = make_user(email="nodat@example.com", status="pending_onboarding")

        with client.session_transaction() as sess:
            sess["google_email"] = user.email

        resp = client.post(f"/student/onboard/{user.email}", data="")

        assert resp.get_json()["status"] == "error"

    def test_unknown_user_rejected(self, client):
        with client.session_transaction() as sess:
            sess["google_email"] = "ghost@example.com"

        resp = client.post(
            "/student/onboard/ghost@example.com", json={"name": "Ghost"}
        )

        assert resp.get_json()["status"] == "error"

    def test_saves_onboarding_details_and_issues_tokens(
        self, client, db_session, make_user, make_student_profile
    ):
        user = make_user(email="onboarding@example.com", status="pending_onboarding")
        make_student_profile(user)

        with client.session_transaction() as sess:
            sess["google_email"] = user.email
            sess["google_name"] = "Google Name"
            sess["google_id"] = "google-sub-123"

        resp = client.post(
            f"/student/onboard/{user.email}",
            json={
                "name": "Updated Name",
                "department": "Computer Science",
                "class_level": "200",
                "subjects": ["Math", "Physics"],
                "learning_style": "visual",
                "study_preferences": ["group"],
                "help_subjects": ["Math"],
                "strong_subjects": ["Physics"],
                "study_schedule": {"monday": ["evening"]},
                "session_length": "60",
            },
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert body["data"]["redirect"] == f"/student/complete-registration/{user.email}"

        db_session.refresh(user)
        assert user.name == "Updated Name"
        assert user.status == "pending_verification"

        from models import OnboardingDetails
        details = OnboardingDetails.query.filter_by(user_id=user.id).first()
        assert details is not None
        assert details.department == "Computer Science"
        assert details.subjects == ["Math", "Physics"]

        # Auto-login issues a JWT cookie for the new user.
        assert resp.headers.getlist("Set-Cookie")

        # And clears the now-unneeded Google-OAuth session state.
        with client.session_transaction() as sess:
            assert "google_email" not in sess
            assert "google_name" not in sess
            assert "google_id" not in sess

    def test_updates_existing_onboarding_details_row(
        self, client, db_session, make_user, make_student_profile
    ):
        user = make_user(email="reonboard@example.com", status="pending_onboarding")
        make_student_profile(user)

        with client.session_transaction() as sess:
            sess["google_email"] = user.email

        # First submission creates the OnboardingDetails row.
        client.post(
            f"/student/onboard/{user.email}",
            json={"name": "First Pass", "department": "Biology"},
        )

        with client.session_transaction() as sess:
            sess["google_email"] = user.email

        # Second submission should update the same row, not create another.
        resp = client.post(
            f"/student/onboard/{user.email}",
            json={"name": "Second Pass", "department": "Chemistry"},
        )

        assert resp.status_code == 200

        from models import OnboardingDetails
        rows = OnboardingDetails.query.filter_by(user_id=user.id).all()
        assert len(rows) == 1
        assert rows[0].department == "Chemistry"


# ============================================================================
# POST /onboard/request-all/<email>
# ============================================================================

class TestOnboardRequestAll:
    """
    NEWLY DISCOVERED BUG (found while porting this file to a new branch:
    the repo's main had moved forward since this file was first written
    and added a route that shadows the one these tests target):

    routes/student/auth.py used to register both
      POST /onboard/request-all/<token>
      POST /onboard/request-all/<email>
as the same URL shape, so the token route shadowed the email route. It is
now a single route that dispatches on whether the identifier contains "@"
(emails always do, verification JWTs never do), so these tests exercise
the email path directly.
    """

    def test_requires_authorization(self, client, make_user):
        user = make_user(email="reqall1@example.com")

        resp = client.post(
            f"/student/onboard/request-all/{user.email}", json={"ids": [1]}
        )

        assert resp.status_code == 401

    def test_creates_pending_connections_for_given_ids(
        self, client, db_session, make_user
    ):
        user = make_user(email="reqall2@example.com")
        target1 = make_user(email="target1@example.com")
        target2 = make_user(email="target2@example.com")

        with client.session_transaction() as sess:
            sess["google_email"] = user.email

        resp = client.post(
            f"/student/onboard/request-all/{user.email}",
            json={"ids": [target1.id, target2.id]},
        )

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "success"

        from models import Connection
        conns = Connection.query.filter_by(requester_id=user.id).all()
        assert {c.receiver_id for c in conns} == {target1.id, target2.id}
        assert all(c.status == "pending" for c in conns)

    def test_skips_ids_with_an_existing_connection_either_direction(
        self, client, db_session, make_user, make_connection
    ):
        user = make_user(email="reqall3@example.com")
        target = make_user(email="target3@example.com")
        # Pre-existing connection in the REVERSE direction.
        make_connection(target, user, status="accepted")

        with client.session_transaction() as sess:
            sess["google_email"] = user.email

        resp = client.post(
            f"/student/onboard/request-all/{user.email}",
            json={"ids": [target.id]},
        )

        assert resp.status_code == 200

        from models import Connection
        conns = Connection.query.filter(
            ((Connection.requester_id == user.id) & (Connection.receiver_id == target.id))
            | ((Connection.requester_id == target.id) & (Connection.receiver_id == user.id))
        ).all()
        # Still just the one pre-existing row -- no duplicate created.
        assert len(conns) == 1
        assert conns[0].status == "accepted"

    def test_empty_ids_list_is_a_no_op_success(self, client, make_user):
        user = make_user(email="reqall4@example.com")

        with client.session_transaction() as sess:
            sess["google_email"] = user.email

        resp = client.post(
            f"/student/onboard/request-all/{user.email}", json={"ids": []}
        )

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "success"

    def test_unknown_email_rejected(self, client):
        with client.session_transaction() as sess:
            sess["google_email"] = "nouser@example.com"

        resp = client.post(
            "/student/onboard/request-all/nouser@example.com", json={"ids": [1]}
        )

        assert resp.get_json()["status"] == "error"


# ============================================================================
# GET /onboard/suggestions/<token>  (JWT-token variant of Batch 40's
# suggestions-by-email route)
# ============================================================================

class TestOnboardSuggestionsByToken:
    def test_invalid_token_returns_error(self, client):
        resp = client.get("/student/onboard/suggestions/not-a-real-token")

        assert resp.get_json()["status"] == "error"

    def test_expired_token_returns_error(self, client, make_user):
        user = make_user(email="expiredtok@example.com")
        token = _verification_token(user.email, hours=-1)

        resp = client.get(f"/student/onboard/suggestions/{token}")

        body = resp.get_json()
        assert body["status"] == "error"
        assert "expired" in body["message"].lower()

    def test_valid_token_unknown_user_rejected(self, client):
        token = _verification_token("ghostly@example.com")

        resp = client.get(f"/student/onboard/suggestions/{token}")

        assert resp.get_json()["status"] == "error"

    def test_valid_token_returns_fallback_top_users(self, client, make_user):
        target = make_user(email="suggtarget@example.com")
        make_user(reputation=500, status="approved")
        token = _verification_token(target.email)

        resp = client.get(f"/student/onboard/suggestions/{token}")

        assert resp.status_code == 200
        matches = resp.get_json()["data"]["matches"]
        assert len(matches) >= 1
        assert "match_score" in matches[0]


# ============================================================================
# GET/POST /complete-registration
# ============================================================================

class TestCompleteRegistrationGet:
    def test_renders_blank_page_with_no_params(self, client):
        resp = client.get("/student/complete-registration")

        assert resp.status_code == 200

    def test_invalid_token_renders_page_with_error(self, client):
        resp = client.get("/student/complete-registration?token=not-a-real-token")

        assert resp.status_code == 200

    def test_pending_onboarding_user_redirects_to_onboard(self, client, make_user):
        user = make_user(email="redir1@example.com", status="pending_onboarding")

        resp = client.get(
            f"/student/complete-registration?email={user.email}",
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/student/onboard/{user.email}")

    def test_approved_user_redirects_to_homepage(self, client, make_user):
        user = make_user(email="redir2@example.com", status="approved")

        resp = client.get(
            f"/student/complete-registration?email={user.email}",
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/student/profile/homepage")

    def test_pending_verification_user_shows_form_not_redirect(self, client, make_user):
        user = make_user(email="redir3@example.com", status="pending_verification")

        resp = client.get(
            f"/student/complete-registration?email={user.email}",
            follow_redirects=False,
        )

        assert resp.status_code == 200


class TestCompleteRegistrationPost:
    def test_requires_authorization_without_token(self, client, make_user):
        user = make_user(
            email="noauthcr@example.com", status="pending_verification",
            email_verified=True,
        )

        resp = client.post(
            "/student/complete-registration",
            json={
                "email": user.email,
                "password": "secret123",
                "confirm_password": "secret123",
                "username": "noauthcr",
            },
        )

        assert resp.status_code == 401

    def test_missing_fields_rejected(self, client):
        resp = client.post("/student/complete-registration", json={"email": "x@example.com"})

        assert resp.get_json()["status"] == "error"

    def test_mismatched_passwords_rejected(self, client, make_user):
        user = make_user(
            email="mismatch@example.com", status="pending_verification",
            email_verified=True,
        )
        token = _verification_token(user.email)

        resp = client.post(
            "/student/complete-registration",
            json={
                "token": token,
                "password": "secret123",
                "confirm_password": "different456",
                "username": "mismatchu",
            },
        )

        assert resp.get_json()["status"] == "error"

    def test_short_password_rejected(self, client, make_user):
        user = make_user(
            email="shortpw@example.com", status="pending_verification",
            email_verified=True,
        )
        token = _verification_token(user.email)

        resp = client.post(
            "/student/complete-registration",
            json={
                "token": token,
                "password": "ab1",
                "confirm_password": "ab1",
                "username": "shortpwu",
            },
        )

        assert resp.get_json()["status"] == "error"

    def test_invalid_username_format_rejected(self, client, make_user):
        user = make_user(
            email="badname@example.com", status="pending_verification",
            email_verified=True,
        )
        token = _verification_token(user.email)

        resp = client.post(
            "/student/complete-registration",
            json={
                "token": token,
                "password": "secret123",
                "confirm_password": "secret123",
                "username": "Has Caps And Spaces",
            },
        )

        assert resp.get_json()["status"] == "error"

    def test_username_already_taken_rejected(self, client, make_user):
        make_user(username="takenname")
        user = make_user(
            email="taken2@example.com", status="pending_verification",
            email_verified=True,
        )
        token = _verification_token(user.email)

        resp = client.post(
            "/student/complete-registration",
            json={
                "token": token,
                "password": "secret123",
                "confirm_password": "secret123",
                "username": "takenname",
            },
        )

        assert resp.get_json()["status"] == "error"

    def test_unverified_email_rejected_by_finalize_password(self, client, make_user):
        user = make_user(
            email="unverified@example.com", status="pending_verification",
            email_verified=False,
        )
        token = _verification_token(user.email)

        resp = client.post(
            "/student/complete-registration",
            json={
                "token": token,
                "password": "secret123",
                "confirm_password": "secret123",
                "username": "unverifiedu",
            },
        )

        assert resp.get_json()["status"] == "error"

    def test_successful_completion_via_token(self, client, db_session, make_user, make_student_profile):
        user = make_user(
            email="complete1@example.com", status="pending_verification",
            email_verified=True,
        )
        make_student_profile(user)
        token = _verification_token(user.email)

        resp = client.post(
            "/student/complete-registration",
            json={
                "token": token,
                "password": "secret123",
                "confirm_password": "secret123",
                "username": "complete1",
            },
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert body["data"]["username"] == "complete1"

        db_session.refresh(user)
        assert user.username == "complete1"
        assert user.status == "approved"

    def test_successful_completion_via_session_auth(
        self, client, db_session, make_user, make_student_profile
    ):
        user = make_user(
            email="complete2@example.com", status="pending_verification",
            email_verified=True,
        )
        make_student_profile(user)

        with client.session_transaction() as sess:
            sess["google_email"] = user.email

        resp = client.post(
            "/student/complete-registration",
            json={
                "email": user.email,
                "password": "secret123",
                "confirm_password": "secret123",
                "username": "complete2",
            },
        )

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "success"
