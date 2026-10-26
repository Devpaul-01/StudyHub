"""
tests/integration/test_reputation_routes.py

routes/student/reputation.py had zero route-level tests. Covers
get_rising_stars (thin wrapper around leaderboard_service), get_my_reputation,
get_reputation_history (incl. action filter and pagination), reputation_stats,
and get_reputation_levels (public, no auth).
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


def _history(db_session, user, **overrides):
    from models import ReputationHistory

    defaults = dict(
        user_id=user.id,
        action="post_liked",
        points_change=5,
        reputation_before=0,
        reputation_after=5,
        related_type="post",
        related_id=1,
        created_at=datetime.datetime.utcnow(),
    )
    defaults.update(overrides)
    row = ReputationHistory(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


# ============================================================================
# GET /reputation/rising-stars
# ============================================================================

class TestRisingStars:
    def test_empty_when_no_qualifying_users(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/reputation/rising-stars", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["rising_stars"] == []

    def test_user_with_recent_gain_appears(
        self, client, db_session, make_user, auth_headers
    ):
        viewer = make_user()
        riser = make_user(reputation=50)
        _history(db_session, riser, points_change=50, reputation_after=50)

        resp = client.get("/student/reputation/rising-stars", headers=auth_headers(viewer))

        ids = [entry["user"]["id"] for entry in resp.get_json()["data"]["rising_stars"]]
        assert riser.id in ids


# ============================================================================
# GET /reputation/me
# ============================================================================

class TestGetMyReputation:
    def test_returns_level_and_rank(self, client, make_user, auth_headers):
        user = make_user(reputation=25, status="approved")

        resp = client.get("/student/reputation/me", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]["reputation"]
        assert data["points"] == 25
        assert data["level"]["name"] == "Newbie"
        assert data["rank"]["global"] >= 1

    def test_includes_recent_changes(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user(reputation=10, status="approved")
        _history(db_session, user, action="post_liked", points_change=10, reputation_after=10)

        resp = client.get("/student/reputation/me", headers=auth_headers(user))

        changes = resp.get_json()["data"]["recent_changes"]
        assert len(changes) == 1
        assert changes[0]["action"] == "post_liked"

    def test_top_level_user_has_no_next_level(self, client, make_user, auth_headers):
        user = make_user(reputation=5000, status="approved")

        resp = client.get("/student/reputation/me", headers=auth_headers(user))

        data = resp.get_json()["data"]["reputation"]
        assert data["level"]["name"] == "Master"
        assert data["next_level"] is None


# ============================================================================
# GET /reputation/history
# ============================================================================

class TestGetReputationHistory:
    def test_empty_history(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/reputation/history", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["history"] == []
        assert data["summary"]["net_change"] == 0

    def test_lists_own_history_only(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        other = make_user()
        _history(db_session, user, action="post_liked", points_change=5, reputation_after=5)
        _history(db_session, other, action="post_liked", points_change=5, reputation_after=5)

        resp = client.get("/student/reputation/history", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert len(data["history"]) == 1

    def test_action_filter(self, client, db_session, make_user, auth_headers):
        user = make_user()
        _history(db_session, user, action="post_liked", points_change=5, reputation_after=5)
        _history(db_session, user, action="solution_marked", points_change=10, reputation_after=15)

        resp = client.get(
            "/student/reputation/history?action=solution_marked", headers=auth_headers(user)
        )

        data = resp.get_json()["data"]["history"]
        assert len(data) == 1
        assert data[0]["action"] == "solution_marked"

    def test_summary_totals_gains_and_losses(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        _history(db_session, user, action="post_liked", points_change=10, reputation_after=10)
        _history(db_session, user, action="content_disliked", points_change=-2, reputation_after=8)

        resp = client.get("/student/reputation/history", headers=auth_headers(user))

        summary = resp.get_json()["data"]["summary"]
        assert summary["total_gained"] == 10
        assert summary["total_lost"] == 2
        assert summary["net_change"] == 8

    def test_pagination_fields_present(self, client, db_session, make_user, auth_headers):
        user = make_user()
        for i in range(3):
            _history(db_session, user, points_change=1, reputation_after=i + 1)

        resp = client.get(
            "/student/reputation/history?page=1&per_page=2", headers=auth_headers(user)
        )

        pagination = resp.get_json()["data"]["pagination"]
        assert pagination["page"] == 1
        assert pagination["per_page"] == 2
        assert pagination["total"] == 3
        assert pagination["pages"] == 2


# ============================================================================
# GET /reputation/stats
# ============================================================================

class TestReputationStats:
    def test_returns_platform_aggregates(self, client, make_user, auth_headers):
        user = make_user(reputation=100, status="approved")

        resp = client.get("/student/reputation/stats", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "active_students" in data
        assert "average_reputation" in data


# ============================================================================
# GET /reputation/levels (public, no auth)
# ============================================================================

class TestGetReputationLevels:
    def test_works_without_auth(self, client):
        resp = client.get("/student/reputation/levels")

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert len(data["levels"]) > 0
        assert len(data["actions"]) > 0
