"""
tests/integration/test_connections_discovery_routes.py

routes/student/connections/discovery.py had zero tests at any level.
This batch covers get_mutual_connections, connection_suggestions,
search_users, discover_mutual_connections (incl. its generic-suggestion
fallback), connection_suggestions_flat, and get_available_connections.
Deeper compatibility-scoring and AI-provider endpoints in this package
(overview, etc.) are left for a future pass.
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
# GET /connections/mutual/<user_id>
# ============================================================================

class TestGetMutualConnections:
    def test_self_rejected(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(f"/student/connections/mutual/{user.id}", headers=auth_headers(user))

        assert resp.get_json()["status"] == "error"

    def test_nonexistent_user_rejected(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/mutual/999999", headers=auth_headers(user))

        assert resp.get_json()["status"] == "error"

    def test_no_mutuals_returns_empty(self, client, make_user, auth_headers):
        me = make_user()
        other = make_user()

        resp = client.get(f"/student/connections/mutual/{other.id}", headers=auth_headers(me))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["mutual_connections"] == []
        assert data["count"] == 0

    def test_shared_accepted_connection_is_a_mutual(
        self, client, db_session, make_user, make_connection, auth_headers
    ):
        me = make_user()
        other = make_user()
        shared = make_user()
        make_connection(me, shared, status="accepted")
        make_connection(shared, other, status="accepted")

        resp = client.get(f"/student/connections/mutual/{other.id}", headers=auth_headers(me))

        data = resp.get_json()["data"]
        assert data["count"] == 1
        assert data["mutual_connections"][0]["user"]["id"] == shared.id


# ============================================================================
# GET /connections/suggestions
# ============================================================================

class TestConnectionSuggestions:
    def test_no_profile_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/suggestions", headers=auth_headers(user))

        assert resp.status_code == 404

    def test_same_department_candidate_suggested_as_study_partner(
        self, client, db_session, make_user, make_student_profile, auth_headers
    ):
        me = make_user()
        make_student_profile(me, department="CS", class_name="200 Level")
        _onboarding(db_session, me, subjects=["python"])

        candidate = make_user()
        make_student_profile(candidate, department="CS", class_name="200 Level")
        _onboarding(db_session, candidate, subjects=["python"])

        resp = client.get("/student/connections/suggestions", headers=auth_headers(me))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        ids = [p["user"]["id"] for p in data["study_partners"]]
        assert candidate.id in ids

    def test_higher_level_same_department_suggested_as_mentor(
        self, client, db_session, make_user, make_student_profile, auth_headers
    ):
        me = make_user()
        make_student_profile(me, department="CS", class_name="100 Level")
        _onboarding(db_session, me, help_subjects=["calculus"])

        mentor = make_user(reputation=600)
        make_student_profile(mentor, department="CS", class_name="400 Level")
        _onboarding(db_session, mentor, strong_subjects=["calculus"])

        resp = client.get("/student/connections/suggestions", headers=auth_headers(me))

        data = resp.get_json()["data"]
        ids = [m["user"]["id"] for m in data["mentors"]]
        assert mentor.id in ids


# ============================================================================
# GET /connections/search
# ============================================================================

class TestSearchUsers:
    def test_short_query_rejected(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/search?search=a", headers=auth_headers(user))

        assert resp.status_code == 400

    def test_finds_matching_user_by_username(self, client, make_user, auth_headers):
        me = make_user()
        target = make_user(username="findme123")

        resp = client.get("/student/connections/search?search=findme", headers=auth_headers(me))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        ids = [u["id"] for u in data["users"]]
        assert target.id in ids

    def test_blocked_user_excluded_from_results(
        self, client, make_user, auth_headers, csrf_headers
    ):
        me = make_user()
        target = make_user(username="blockme456")
        headers = {**auth_headers(me), **csrf_headers(client)}
        client.post(f"/student/connections/block/{target.id}", headers=headers)

        resp = client.get("/student/connections/search?search=blockme", headers=auth_headers(me))

        data = resp.get_json()["data"]
        ids = [u["id"] for u in data["users"]]
        assert target.id not in ids


# ============================================================================
# GET /connections/mutuals/discover
# ============================================================================

class TestDiscoverMutualConnections:
    def test_no_connections_falls_back_to_generic_suggestions(
        self, client, make_user, make_student_profile, auth_headers
    ):
        me = make_user()
        make_student_profile(me, department="CS")
        other = make_user(reputation=600)
        make_student_profile(other, department="CS")

        resp = client.get("/student/connections/mutuals/discover", headers=auth_headers(me))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["discovery_type"] == "generic"

    def test_returns_users_reachable_through_a_mutual_connection(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        friend = make_user()
        friend_of_friend = make_user()
        make_connection(me, friend, status="accepted")
        make_connection(friend, friend_of_friend, status="accepted")

        resp = client.get("/student/connections/mutuals/discover", headers=auth_headers(me))

        data = resp.get_json()["data"]
        assert data["discovery_type"] == "mutual"
        ids = [d["user"]["id"] for d in data["discoveries"]]
        assert friend_of_friend.id in ids
        assert friend.id not in ids

    def test_min_mutuals_filter_excludes_below_threshold(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        friend = make_user()
        friend_of_friend = make_user()
        make_connection(me, friend, status="accepted")
        make_connection(friend, friend_of_friend, status="accepted")

        resp = client.get(
            "/student/connections/mutuals/discover?min_mutuals=2", headers=auth_headers(me)
        )

        data = resp.get_json()["data"]
        ids = [d["user"]["id"] for d in data["discoveries"]]
        assert friend_of_friend.id not in ids


# ============================================================================
# GET /connections/suggestions/flat
# ============================================================================

class TestConnectionSuggestionsFlat:
    def test_no_profile_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/suggestions/flat", headers=auth_headers(user))

        assert resp.status_code == 404

    def test_same_department_and_class_candidate_suggested(
        self, client, make_user, make_student_profile, auth_headers
    ):
        me = make_user()
        make_student_profile(me, department="CS", class_name="200 Level")
        candidate = make_user()
        make_student_profile(candidate, department="CS", class_name="200 Level")

        resp = client.get("/student/connections/suggestions/flat", headers=auth_headers(me))

        assert resp.status_code == 200
        body = resp.get_json()
        ids = [s["user"]["id"] for s in body["data"]]
        assert candidate.id in ids

    def test_limit_is_respected(self, client, make_user, make_student_profile, auth_headers):
        me = make_user()
        make_student_profile(me, department="CS", class_name="200 Level")
        for _ in range(5):
            c = make_user()
            make_student_profile(c, department="CS", class_name="200 Level")

        resp = client.get(
            "/student/connections/suggestions/flat?limit=2", headers=auth_headers(me)
        )

        body = resp.get_json()
        assert len(body["data"]) <= 2


# ============================================================================
# GET /connections/available-now
# ============================================================================

class TestGetAvailableConnections:
    def test_no_connections_returns_empty(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/available-now", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["available_now"] == []

    def test_recently_active_connection_is_listed(
        self, client, db_session, make_user, make_connection, auth_headers
    ):
        import datetime
        me = make_user()
        other = make_user(last_active=datetime.datetime.utcnow())
        make_connection(me, other, status="accepted")

        resp = client.get("/student/connections/available-now", headers=auth_headers(me))

        data = resp.get_json()["data"]
        ids = [u["user"]["id"] for u in data["available_now"]]
        assert other.id in ids

    def test_stale_connection_not_listed(
        self, client, db_session, make_user, make_connection, auth_headers
    ):
        import datetime
        me = make_user()
        other = make_user(last_active=datetime.datetime.utcnow() - datetime.timedelta(days=10))
        make_connection(me, other, status="accepted")

        resp = client.get("/student/connections/available-now", headers=auth_headers(me))

        data = resp.get_json()["data"]
        ids = [u["user"]["id"] for u in data["available_now"]]
        assert other.id not in ids
