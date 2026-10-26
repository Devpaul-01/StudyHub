"""
tests/integration/test_badges_routes.py

routes/student/badges.py had no route-level integration tests (only
services/badge_service.py's business logic was unit-tested, and
test_posts_comments_reputation_badges.py exercises badge-awarding only
as a side effect of mark_solution, not any badges.py route itself).
This batch covers get_available_badges, get_my_badges, get_badge_progress,
get_badge_details, feature_badge, top_earners, check_all_badges, and
get_user_badges.

Badge/UserBadge rows are created directly rather than via
services.badge_service.seed_badges() + real criteria, except where a
test specifically wants the real seeded definitions (check_all_badges'
"nothing earned yet" path).
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


def _badge(db_session, **overrides):
    from models import Badge

    defaults = dict(
        name="Test Badge",
        description="A badge for testing",
        icon="star",
        category="milestones",
        criteria={"posts_count": 1},
        rarity="common",
        awarded_count=0,
        is_active=True,
    )
    defaults.update(overrides)
    badge = Badge(**defaults)
    db_session.add(badge)
    db_session.flush()
    return badge


def _user_badge(db_session, user, badge, **overrides):
    from models import UserBadge

    defaults = dict(
        user_id=user.id,
        badge_id=badge.id,
        earned_at=datetime.datetime.utcnow(),
        is_featured=False,
    )
    defaults.update(overrides)
    ub = UserBadge(**defaults)
    db_session.add(ub)
    db_session.flush()
    return ub


# ============================================================================
# GET /badges/available
# ============================================================================

class TestGetAvailableBadges:
    def test_lists_active_badges_with_earned_flag(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        earned = _badge(db_session, name="Earned Badge")
        unearned = _badge(db_session, name="Unearned Badge")
        _user_badge(db_session, user, earned)

        resp = client.get("/student/badges/available", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        by_name = {b["name"]: b for b in data["badges"]}
        assert by_name["Earned Badge"]["has_earned"] is True
        assert by_name["Unearned Badge"]["has_earned"] is False
        assert data["earned"] == 1

    def test_inactive_badges_excluded(self, client, db_session, make_user, auth_headers):
        user = make_user()
        _badge(db_session, name="Retired Badge", is_active=False)

        resp = client.get("/student/badges/available", headers=auth_headers(user))

        names = [b["name"] for b in resp.get_json()["data"]["badges"]]
        assert "Retired Badge" not in names

    def test_category_filter(self, client, db_session, make_user, auth_headers):
        user = make_user()
        _badge(db_session, name="Social Badge", category="social")
        _badge(db_session, name="Milestone Badge", category="milestones")

        resp = client.get(
            "/student/badges/available?category=social", headers=auth_headers(user)
        )

        names = [b["name"] for b in resp.get_json()["data"]["badges"]]
        assert names == ["Social Badge"]


# ============================================================================
# GET /badges/my-badges
# ============================================================================

class TestGetMyBadges:
    def test_empty_when_none_earned(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/badges/my-badges", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["badges"] == []
        assert data["total_earned"] == 0

    def test_lists_earned_badges_grouped_by_rarity(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        rare_badge = _badge(db_session, name="Rare One", rarity="rare")
        _user_badge(db_session, user, rare_badge)

        resp = client.get("/student/badges/my-badges", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert data["total_earned"] == 1
        assert "rare" in data["by_rarity"]
        assert data["by_rarity"]["rare"][0]["name"] == "Rare One"

    def test_featured_badges_listed_separately(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        badge = _badge(db_session, name="Featured One")
        _user_badge(db_session, user, badge, is_featured=True)

        resp = client.get("/student/badges/my-badges", headers=auth_headers(user))

        featured = resp.get_json()["data"]["featured"]
        assert len(featured) == 1
        assert featured[0]["name"] == "Featured One"


# ============================================================================
# GET /badges/<id>/details
# ============================================================================

class TestGetBadgeDetails:
    def test_nonexistent_badge_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/badges/999999/details", headers=auth_headers(user))

        assert resp.status_code == 404

    def test_unearned_badge_includes_progress(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user(total_posts=2)
        badge = _badge(db_session, name="Poster", criteria={"posts_count": 5})

        resp = client.get(
            f"/student/badges/{badge.id}/details", headers=auth_headers(user)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["has_earned"] is False
        assert data["progress"] is not None

    def test_earned_badge_lists_recent_earners(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        other = make_user()
        badge = _badge(db_session, name="Shared Badge")
        _user_badge(db_session, user, badge)
        _user_badge(db_session, other, badge)

        resp = client.get(
            f"/student/badges/{badge.id}/details", headers=auth_headers(user)
        )

        data = resp.get_json()["data"]
        assert data["has_earned"] is True
        assert data["progress"] is None
        earner_usernames = {e["username"] for e in data["recent_earners"]}
        assert user.username in earner_usernames
        assert other.username in earner_usernames


# ============================================================================
# POST /badges/feature/<id>
# ============================================================================

class TestFeatureBadge:
    def test_unearned_badge_returns_404(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        badge = _badge(db_session, name="Not Mine")

        resp = client.post(
            f"/student/badges/feature/{badge.id}",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.status_code == 404

    def test_toggles_featured_state(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        badge = _badge(db_session, name="Toggle Me")
        ub = _user_badge(db_session, user, badge)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp1 = client.post(f"/student/badges/feature/{badge.id}", headers=headers)
        assert resp1.get_json()["data"]["is_featured"] is True

        resp2 = client.post(f"/student/badges/feature/{badge.id}", headers=headers)
        assert resp2.get_json()["data"]["is_featured"] is False

    def test_max_three_featured_badges_enforced(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        badges = [_badge(db_session, name=f"B{i}") for i in range(4)]
        for b in badges[:3]:
            _user_badge(db_session, user, b, is_featured=True)
        _user_badge(db_session, user, badges[3], is_featured=False)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(f"/student/badges/feature/{badges[3].id}", headers=headers)

        assert resp.status_code == 400


# ============================================================================
# GET /badges/top-earners
# ============================================================================

class TestTopEarners:
    def test_empty_when_no_badges_awarded(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/badges/top-earners", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"] == []

    def test_ranks_by_badge_count_with_viewer_overlay(
        self, client, db_session, make_user, auth_headers
    ):
        viewer = make_user()
        top_earner = make_user()
        for i in range(3):
            _user_badge(db_session, top_earner, _badge(db_session, name=f"Earned{i}"))

        resp = client.get("/student/badges/top-earners", headers=auth_headers(viewer))

        data = resp.get_json()["data"]
        assert data[0]["user"]["id"] == top_earner.id
        assert data[0]["stats"]["total_badges"] == 3
        assert data[0]["is_you"] is False


# ============================================================================
# POST /badges/check-all
# ============================================================================

class TestCheckAllBadges:
    def test_no_badges_earned_yet(self, client, db_session, make_user, auth_headers, csrf_headers):
        # NOTE: deliberately does NOT use the seeded_badges fixture --
        # services.badge_service.BADGE_DEFINITIONS includes an "Early
        # Adopter" badge whose criteria ({"early_adopter": True}) is
        # satisfied by literally the first user ever created in the DB
        # (the cutoff is computed relative to the earliest joined_at, so
        # the earliest user trivially joined before their own cutoff) --
        # which this test's lone user always is in an isolated test DB.
        # A badge with an ordinary, clearly-unmet numeric criterion is
        # used instead so this test only asserts what it says it does.
        user = make_user(total_posts=0)
        _badge(db_session, name="Unreachable", criteria={"posts_count": 999})

        resp = client.post(
            "/student/badges/check-all",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.status_code == 200
        assert "No new badges" in resp.get_json()["message"]

    def test_qualifying_user_earns_badge(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        from models import UserBadge

        user = make_user(total_posts=100)
        badge = _badge(db_session, name="Prolific Poster", criteria={"posts_count": 5})

        resp = client.post(
            "/student/badges/check-all",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.status_code == 201
        body = resp.get_json()
        names = [b["name"] for b in body["data"]["badges"]]
        assert "Prolific Poster" in names
        assert UserBadge.query.filter_by(user_id=user.id, badge_id=badge.id).first() is not None


# ============================================================================
# GET /badges/user-badges/<id>
# ============================================================================

class TestGetUserBadges:
    def test_lists_specified_users_badges(
        self, client, db_session, make_user, auth_headers
    ):
        viewer = make_user()
        target = make_user()
        badge = _badge(db_session, name="Their Badge")
        _user_badge(db_session, target, badge)

        resp = client.get(
            f"/student/badges/user-badges/{target.id}", headers=auth_headers(viewer)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total_earned"] == 1
        assert data["badges"][0]["name"] == "Their Badge"

    def test_nonexistent_user_returns_empty(self, client, make_user, auth_headers):
        viewer = make_user()

        resp = client.get(
            "/student/badges/user-badges/999999", headers=auth_headers(viewer)
        )

        assert resp.status_code == 200
        assert resp.get_json()["data"]["badges"] == []
