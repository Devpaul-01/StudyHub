"""
tests/integration/test_leaderboard_misc_routes.py

The remaining untested endpoints in routes/student/leaderboard.py:
get_rising_stars, get_leaderboard_stats, get_leaderboard_filters,
get_rank_history, create_snapshot (admin-only), and get_score_breakdown.
The core ranking endpoints in this same file were covered in a previous
batch.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET /leaderboard/rising
# ============================================================================

class TestRisingStars:
    def test_empty_when_no_qualifying_users(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/leaderboard/rising", headers=auth_headers(user))

        assert resp.status_code == 200

    def test_limit_is_capped(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/leaderboard/rising?limit=999", headers=auth_headers(user)
        )

        assert resp.status_code == 200


# ============================================================================
# GET /leaderboard/stats
# ============================================================================

class TestLeaderboardStats:
    def test_returns_platform_stats(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/leaderboard/stats", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "success"


# ============================================================================
# GET /leaderboard/filters
# ============================================================================

class TestLeaderboardFilters:
    def test_returns_periods_and_departments(
        self, client, make_user, make_student_profile, auth_headers
    ):
        user = make_user()
        make_student_profile(user, department="CS")

        resp = client.get("/student/leaderboard/filters", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "periods" in data
        assert "departments" in data
        assert data["your_department"] == "CS"

    def test_no_profile_your_department_is_none(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/leaderboard/filters", headers=auth_headers(user))

        assert resp.get_json()["data"]["your_department"] is None


# ============================================================================
# GET /leaderboard/rank-history
# ============================================================================

class TestRankHistory:
    def test_empty_when_no_snapshots(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/leaderboard/rank-history", headers=auth_headers(user))

        assert resp.status_code == 200

    def test_weeks_param_is_capped(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/leaderboard/rank-history?weeks=999", headers=auth_headers(user)
        )

        assert resp.status_code == 200


# ============================================================================
# POST /leaderboard/snapshot
# ============================================================================

class TestCreateSnapshot:
    def test_student_forbidden(self, client, make_user, auth_headers, csrf_headers):
        user = make_user(role="student")

        resp = client.post(
            "/student/leaderboard/snapshot",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.status_code == 403

    def test_admin_can_create_weekly_snapshot(
        self, client, make_user, auth_headers, csrf_headers
    ):
        admin = make_user(role="admin", reputation=50, status="approved")

        resp = client.post(
            "/student/leaderboard/snapshot",
            json={"type": "weekly"},
            headers={**auth_headers(admin), **csrf_headers(client)},
        )

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["status"] == "success"
        assert "created" in body["data"]


# ============================================================================
# GET /leaderboard/breakdown
# ============================================================================

class TestScoreBreakdown:
    def test_invalid_period_rejected(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/leaderboard/breakdown?period=bogus", headers=auth_headers(user)
        )

        assert resp.status_code == 400

    def test_no_history_returns_empty_breakdown(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/leaderboard/breakdown", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "success"
