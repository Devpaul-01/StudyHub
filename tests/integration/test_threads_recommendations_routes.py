"""
tests/integration/test_threads_recommendations_routes.py

The two heavier scoring-based endpoints in
routes/student/threads/discovery.py, previously untested:
get_recommended_threads and get_help_suggestions. The rest of that
file's endpoints were covered in a previous batch.
"""

import pytest

pytestmark = pytest.mark.integration


def _onboarding(db_session, user, **overrides):
    from models import OnboardingDetails

    defaults = dict(
        user_id=user.id,
        email=user.email,
        subjects=[],
        study_preferences=[],
        help_subjects=[],
        strong_subjects=[],
        study_schedule={},
    )
    defaults.update(overrides)
    ob = OnboardingDetails(**defaults)
    db_session.add(ob)
    db_session.flush()
    return ob


# ============================================================================
# GET /threads/recommended
# ============================================================================

class TestGetRecommendedThreads:
    def test_empty_when_no_qualifying_threads(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/threads/recommended", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["recommendations"] == []

    def test_same_department_thread_is_recommended(
        self, client, make_user, make_student_profile, make_thread, auth_headers
    ):
        viewer = make_user()
        make_student_profile(viewer, department="CS")
        creator = make_user()
        make_thread(
            creator, department="CS", member_count=2, max_members=10, is_open=True
        )

        resp = client.get("/student/threads/recommended", headers=auth_headers(viewer))

        data = resp.get_json()["data"]
        assert data["total_found"] == 1
        assert "Your department" in data["recommendations"][0]["reasons"]

    def test_already_member_thread_excluded(
        self, client, make_user, make_student_profile, make_thread, make_thread_member, auth_headers
    ):
        viewer = make_user()
        make_student_profile(viewer, department="CS")
        creator = make_user()
        thread = make_thread(
            creator, department="CS", member_count=2, max_members=10, is_open=True
        )
        make_thread_member(thread, viewer)

        resp = client.get("/student/threads/recommended", headers=auth_headers(viewer))

        ids = [r["id"] for r in resp.get_json()["data"]["recommendations"]]
        assert thread.id not in ids

    def test_personalization_reflects_onboarding_and_friends(
        self, client, db_session, make_user, make_connection, auth_headers
    ):
        viewer = make_user()
        friend = make_user()
        make_connection(viewer, friend, status="accepted")

        resp = client.get("/student/threads/recommended", headers=auth_headers(viewer))

        data = resp.get_json()["data"]
        assert data["personalization"]["has_onboarding"] is False
        assert data["personalization"]["has_friends"] is True


# ============================================================================
# GET /threads/help/suggestions
# ============================================================================

class TestGetHelpSuggestions:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG FOUND BY THIS TEST: when the viewer has no onboarding "
            "details, get_help_suggestions() calls "
            "error_response(\"Complete your onboarding to get help "
            "suggestions\", data={'redirect': '/student/onboard'}) -- but "
            "error_response()'s actual signature (routes/student/helpers.py) "
            "is `error_response(message, status_code=400, errors=None)`, "
            "with no `data` parameter at all. The call raises TypeError("
            "\"error_response() got an unexpected keyword argument 'data'\"), "
            "which the route's own bare `except Exception` catches and "
            "turns into a generic 'Failed to load help suggestions' 400 -- "
            "so a user who hasn't onboarded never actually sees the "
            "intended message or the /student/onboard redirect hint; they "
            "just see an opaque failure. strict=True so this flips to "
            "XPASS once the call site is fixed to use `errors=` (or "
            "error_response() itself gains a `data` parameter)."
        ),
    )
    def test_no_onboarding_returns_error_with_redirect(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/threads/help/suggestions", headers=auth_headers(user))

        body = resp.get_json()
        assert body["status"] == "error"
        assert body["data"]["redirect"] == "/student/onboard"

    def test_no_strong_subjects_returns_empty_suggestions(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        _onboarding(db_session, user, strong_subjects=[])

        resp = client.get("/student/threads/help/suggestions", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["suggestions"] == []

    def test_matching_candidate_is_suggested(
        self, client, db_session, make_user, auth_headers
    ):
        viewer = make_user()
        _onboarding(db_session, viewer, strong_subjects=["calculus"])

        candidate = make_user()
        _onboarding(db_session, candidate, help_subjects=["calculus"])

        resp = client.get("/student/threads/help/suggestions", headers=auth_headers(viewer))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total_found"] == 1
        suggestion = data["suggestions"][0]
        assert suggestion["user"]["id"] == candidate.id
        assert "calculus" in suggestion["match_details"]["can_help_with"]

    def test_non_overlapping_candidate_excluded(
        self, client, db_session, make_user, auth_headers
    ):
        viewer = make_user()
        _onboarding(db_session, viewer, strong_subjects=["calculus"])

        candidate = make_user()
        _onboarding(db_session, candidate, help_subjects=["literature"])

        resp = client.get("/student/threads/help/suggestions", headers=auth_headers(viewer))

        assert resp.get_json()["data"]["suggestions"] == []

    def test_existing_connection_excluded(
        self, client, db_session, make_user, make_connection, auth_headers
    ):
        viewer = make_user()
        _onboarding(db_session, viewer, strong_subjects=["calculus"])

        candidate = make_user()
        _onboarding(db_session, candidate, help_subjects=["calculus"])
        make_connection(viewer, candidate, status="accepted")

        resp = client.get("/student/threads/help/suggestions", headers=auth_headers(viewer))

        assert resp.get_json()["data"]["suggestions"] == []
