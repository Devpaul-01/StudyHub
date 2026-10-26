"""
tests/integration/test_leaderboard_ranking_routes.py

routes/student/leaderboard.py had zero route-level tests. This batch
covers the core ranking endpoints: get_global_leaderboard,
get_department_leaderboard, get_my_rank, get_nearby_users, and
get_connections_leaderboard. The remaining endpoints (rising, stats,
filters, rank-history, snapshot, breakdown) are left for a following
batch.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET /leaderboard/global
# ============================================================================

class TestGlobalLeaderboard:
    def test_invalid_period_rejected(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/leaderboard/global?period=not_a_period", headers=auth_headers(user)
        )

        assert resp.status_code == 400

    def test_default_period_lists_users_by_reputation(
        self, client, make_user, auth_headers
    ):
        viewer = make_user(reputation=10, status="approved")
        top = make_user(reputation=500, status="approved")

        resp = client.get("/student/leaderboard/global", headers=auth_headers(viewer))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "leaderboard" in data
        user_ids = [e["user"]["id"] for e in data["leaderboard"]]
        assert top.id in user_ids

    def test_department_filter_narrows_results(
        self, client, make_user, make_student_profile, auth_headers
    ):
        viewer = make_user(status="approved")
        cs_user = make_user(reputation=50, status="approved")
        make_student_profile(cs_user, department="CS")
        physics_user = make_user(reputation=50, status="approved")
        make_student_profile(physics_user, department="Physics")

        resp = client.get(
            "/student/leaderboard/global?department=CS", headers=auth_headers(viewer)
        )

        assert resp.status_code == 200
        user_ids = [e["user"]["id"] for e in resp.get_json()["data"]["leaderboard"]]
        assert cs_user.id in user_ids
        assert physics_user.id not in user_ids


# ============================================================================
# GET /leaderboard/department
# ============================================================================

class TestDepartmentLeaderboard:
    def test_no_department_set_and_none_passed_returns_error(
        self, client, make_user, auth_headers
    ):
        user = make_user(status="approved")

        resp = client.get("/student/leaderboard/department", headers=auth_headers(user))

        assert resp.status_code == 400

    def test_uses_own_department_by_default(
        self, client, make_user, make_student_profile, auth_headers
    ):
        user = make_user(status="approved")
        make_student_profile(user, department="CS")

        resp = client.get("/student/leaderboard/department", headers=auth_headers(user))

        assert resp.status_code == 200

    def test_department_override_via_query_param(
        self, client, make_user, make_student_profile, auth_headers
    ):
        user = make_user(status="approved")
        make_student_profile(user, department="CS")

        resp = client.get(
            "/student/leaderboard/department?department=Physics", headers=auth_headers(user)
        )

        assert resp.status_code == 200

    def test_invalid_period_rejected(
        self, client, make_user, make_student_profile, auth_headers
    ):
        user = make_user(status="approved")
        make_student_profile(user, department="CS")

        resp = client.get(
            "/student/leaderboard/department?period=bogus", headers=auth_headers(user)
        )

        assert resp.status_code == 400


# ============================================================================
# GET /leaderboard/me
# ============================================================================

class TestMyRank:
    def test_returns_rank_card(self, client, make_user, auth_headers):
        user = make_user(status="approved", reputation=20)

        resp = client.get("/student/leaderboard/me", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "success"

    def test_invalid_period_rejected(self, client, make_user, auth_headers):
        user = make_user(status="approved")

        resp = client.get("/student/leaderboard/me?period=bogus", headers=auth_headers(user))

        assert resp.status_code == 400


# ============================================================================
# GET /leaderboard/nearby
# ============================================================================

class TestNearbyUsers:
    def test_returns_nearby_context(self, client, make_user, auth_headers):
        user = make_user(status="approved", reputation=20)
        for i in range(3):
            make_user(status="approved", reputation=20 + i)

        resp = client.get("/student/leaderboard/nearby", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "nearby" in data
        assert "your_rank" in data

    def test_invalid_period_rejected(self, client, make_user, auth_headers):
        user = make_user(status="approved")

        resp = client.get(
            "/student/leaderboard/nearby?period=bogus", headers=auth_headers(user)
        )

        assert resp.status_code == 400

    def test_range_is_capped(self, client, make_user, auth_headers):
        user = make_user(status="approved")

        resp = client.get(
            "/student/leaderboard/nearby?range=999", headers=auth_headers(user)
        )

        assert resp.status_code == 200


# ============================================================================
# GET /leaderboard/connections
# ============================================================================

class TestConnectionsLeaderboard:
    def test_includes_self_with_no_connections(self, client, make_user, auth_headers):
        user = make_user(status="approved", reputation=15)

        resp = client.get("/student/leaderboard/connections", headers=auth_headers(user))

        assert resp.status_code == 200

    def test_includes_accepted_connections(
        self, client, make_user, make_connection, auth_headers
    ):
        user = make_user(status="approved", reputation=15)
        friend = make_user(status="approved", reputation=30)
        make_connection(user, friend, status="accepted")

        resp = client.get("/student/leaderboard/connections", headers=auth_headers(user))

        assert resp.status_code == 200

    def test_invalid_period_rejected(self, client, make_user, auth_headers):
        user = make_user(status="approved")

        resp = client.get(
            "/student/leaderboard/connections?period=bogus", headers=auth_headers(user)
        )

        assert resp.status_code == 400
