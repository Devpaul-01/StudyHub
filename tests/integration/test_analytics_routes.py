"""
tests/integration/test_analytics_routes.py

routes/student/analytics.py had zero test coverage. Covers every route:
GET /analytics/overview, /analytics/activity-heatmap,
/analytics/engagement, /analytics/impact, /analytics/insights,
/analytics/comparison, /analytics/post/<id>, /analytics/weekly-summary,
and /analytics/export.

Redis is unreachable in this test environment (connection refused), and
cache_service fails open on that (treats it as a cache miss, logs a
warning, never raises) -- confirmed by the existing suite's established
pattern elsewhere -- so every route's cache-aside read/write here is
effectively a no-op and each test exercises the real query path, not a
cached result from an earlier test.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


def _activity(db_session, user, **overrides):
    from models import UserActivity

    defaults = dict(
        user_id=user.id,
        activity_date=datetime.date.today(),
        posts_created=0,
        comments_created=0,
        threads_joined=0,
        messages_sent=0,
        helpful_count=0,
        activity_score=0,
    )
    defaults.update(overrides)
    row = UserActivity(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def _post_view(db_session, user, post, **overrides):
    from models import PostView

    defaults = dict(
        user_id=user.id,
        post_id=post.id,
        viewed_at=datetime.datetime.utcnow(),
    )
    defaults.update(overrides)
    row = PostView(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def _reputation_entry(db_session, user, **overrides):
    from models import ReputationHistory

    defaults = dict(
        user_id=user.id,
        action="test_action",
        points_change=10,
        created_at=datetime.datetime.utcnow(),
        reputation_before=0,
        reputation_after=10,
    )
    defaults.update(overrides)
    row = ReputationHistory(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def _bookmark(db_session, user, post, **overrides):
    from models import Bookmark

    defaults = dict(post_id=post.id, student_id=user.id)
    defaults.update(overrides)
    row = Bookmark(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


# ============================================================================
# GET /analytics/overview
# ============================================================================

class TestAnalyticsOverview:
    def test_requires_auth(self, client):
        resp = client.get("/student/analytics/overview")

        assert resp.status_code == 401

    def test_basic_shape_for_new_user(self, client, make_user, auth_headers):
        user = make_user(total_posts=0, reputation=0, login_streak=0)

        resp = client.get("/student/analytics/overview", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["hero_stats"]["monthly_views"] == 0
        assert data["current_stats"]["total_posts"] == 0
        assert data["quick_facts"]["department"] is None

    def test_counts_this_weeks_views_and_reputation_change(
        self, client, db_session, make_user, make_post, auth_headers
    ):
        user = make_user()
        post = make_post(user)
        viewer = make_user()
        _post_view(db_session, viewer, post)
        _reputation_entry(db_session, user, points_change=15)

        resp = client.get("/student/analytics/overview", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert data["hero_stats"]["monthly_views"] == 1
        assert data["hero_stats"]["reputation_change"] == 15

    def test_department_rank_present_with_profile(
        self, client, make_user, make_student_profile, auth_headers
    ):
        user = make_user(reputation=10)
        make_student_profile(user, department="Physics")

        resp = client.get("/student/analytics/overview", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert data["hero_stats"]["department_rank"] == 1
        assert data["quick_facts"]["department"] == "Physics"


# ============================================================================
# GET /analytics/activity-heatmap
# ============================================================================

class TestActivityHeatmap:
    def test_requires_auth(self, client):
        resp = client.get("/student/analytics/activity-heatmap")

        assert resp.status_code == 401

    def test_default_90_days_filled_with_zeros(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/analytics/activity-heatmap", headers=auth_headers(user)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["summary"]["total_days"] == 90
        assert len(data["heatmap"]) == 91  # inclusive of today
        assert data["summary"]["active_days"] == 0

    def test_days_param_is_capped_at_365(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/analytics/activity-heatmap?days=9000", headers=auth_headers(user)
        )

        data = resp.get_json()["data"]
        assert data["summary"]["total_days"] == 365

    def test_includes_recorded_activity_and_intensity_level(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        _activity(db_session, user, activity_score=20)

        resp = client.get(
            "/student/analytics/activity-heatmap?days=7", headers=auth_headers(user)
        )

        data = resp.get_json()["data"]
        today_entry = next(
            d for d in data["heatmap"] if d["date"] == datetime.date.today().isoformat()
        )
        assert today_entry["score"] == 20
        assert today_entry["level"] == 3
        assert data["summary"]["active_days"] == 1


# ============================================================================
# GET /analytics/engagement
# ============================================================================

class TestEngagementStats:
    def test_requires_auth(self, client):
        resp = client.get("/student/analytics/engagement")

        assert resp.status_code == 401

    def test_no_posts_no_comments_returns_zeroed_stats(self, client, make_user, auth_headers):
        user = make_user(total_helpful=0)

        resp = client.get("/student/analytics/engagement", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["posts"]["total_created"] == 0
        assert data["posts"]["best_post"] is None
        assert data["comments"]["total_created"] == 0
        assert data["threads"]["created"] == 0

    def test_best_post_is_the_highest_combined_score(
        self, client, make_user, make_post, auth_headers
    ):
        user = make_user()
        make_post(
            user, title="Low", positive_reactions_count=1, comments_count=0
        )
        best = make_post(
            user, title="High", positive_reactions_count=10, comments_count=5
        )

        resp = client.get("/student/analytics/engagement", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert data["posts"]["total_created"] == 2
        assert data["posts"]["best_post"]["id"] == best.id
        assert data["posts"]["best_post"]["title"] == "High"

    def test_comments_marked_as_solution_are_counted(
        self, client, make_user, make_post, make_comment, auth_headers
    ):
        author = make_user()
        post = make_post(author)
        commenter = make_user()
        make_comment(post, commenter, is_solution=True)
        make_comment(post, commenter, is_solution=False)

        resp = client.get("/student/analytics/engagement", headers=auth_headers(commenter))

        data = resp.get_json()["data"]
        assert data["comments"]["total_created"] == 2
        assert data["comments"]["marked_solution"] == 1


# ============================================================================
# GET /analytics/impact
# ============================================================================

class TestImpactMetrics:
    def test_requires_auth(self, client):
        resp = client.get("/student/analytics/impact")

        assert resp.status_code == 401

    def test_counts_distinct_viewers_excluding_self(
        self, client, db_session, make_user, make_post, auth_headers
    ):
        author = make_user()
        post = make_post(author)
        viewer1 = make_user()
        viewer2 = make_user()
        _post_view(db_session, viewer1, post)
        _post_view(db_session, viewer2, post)
        # The author viewing their own post should not count.
        _post_view(db_session, author, post)

        resp = client.get("/student/analytics/impact", headers=auth_headers(author))

        data = resp.get_json()["data"]
        assert data["impact"]["people_reached"] == 2

    def test_questions_answered_and_solved(
        self, client, make_user, make_post, make_comment, auth_headers
    ):
        asker = make_user()
        question = make_post(asker, post_type="question")
        helper = make_user()
        make_comment(question, helper, is_solution=True)

        resp = client.get("/student/analytics/impact", headers=auth_headers(helper))

        data = resp.get_json()["data"]
        assert data["impact"]["questions_answered"] == 1
        assert data["impact"]["questions_solved"] == 1

    def test_times_bookmarked_excludes_self_bookmarks(
        self, client, db_session, make_user, make_post, auth_headers
    ):
        author = make_user()
        post = make_post(author)
        other = make_user()
        _bookmark(db_session, other, post)
        _bookmark(db_session, author, post, post_id=post.id)

        resp = client.get("/student/analytics/impact", headers=auth_headers(author))

        data = resp.get_json()["data"]
        assert data["impact"]["times_bookmarked"] == 1

    def test_impact_score_is_the_weighted_sum(self, client, make_user, auth_headers):
        user = make_user(total_helpful=2)

        resp = client.get("/student/analytics/impact", headers=auth_headers(user))

        data = resp.get_json()["data"]
        # people_reached=0, questions_solved=0, resources_shared=0,
        # total_helpful=2 -> impact_score = 0 + 0 + 2*5 + 0 = 10
        assert data["impact_score"] == 10


# ============================================================================
# GET /analytics/insights
# ============================================================================

class TestInsights:
    def test_requires_auth(self, client):
        resp = client.get("/student/analytics/insights")

        assert resp.status_code == 401

    def test_new_user_gets_no_insights(self, client, make_user, auth_headers):
        user = make_user(login_streak=0)

        resp = client.get("/student/analytics/insights", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["insights"] == []
        assert "generated_at" in data

    def test_login_streak_consistency_insight(self, client, make_user, auth_headers):
        user = make_user(login_streak=10)

        resp = client.get("/student/analytics/insights", headers=auth_headers(user))

        data = resp.get_json()["data"]
        types = [i["type"] for i in data["insights"]]
        assert "consistency" in types


# ============================================================================
# GET /analytics/comparison
# ============================================================================

class TestComparisonStats:
    def test_requires_auth(self, client):
        resp = client.get("/student/analytics/comparison")

        assert resp.status_code == 401

    def test_handles_zero_platform_average_without_crashing(
        self, client, make_user, auth_headers
    ):
        # No other approved users exist yet besides this one -- the route
        # must not divide by zero against get_average_user_stats()'s own
        # possibly-zero averages.
        user = make_user(total_posts=0, reputation=0, total_helpful=0, status="pending_verification")

        resp = client.get("/student/analytics/comparison", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["your_stats"]["posts"] == 0

    def test_above_average_user_is_flagged_above(self, client, make_user, auth_headers):
        make_user(total_posts=1, reputation=10, total_helpful=1, status="approved")
        star = make_user(total_posts=100, reputation=1000, total_helpful=100, status="approved")

        resp = client.get("/student/analytics/comparison", headers=auth_headers(star))

        data = resp.get_json()["data"]
        assert data["comparison"]["posts"]["status"] == "above"
        assert data["comparison"]["reputation"]["status"] == "above"


# ============================================================================
# GET /analytics/post/<post_id>
# ============================================================================

class TestPostAnalytics:
    def test_requires_auth(self, client, make_user, make_post):
        author = make_user()
        post = make_post(author)

        resp = client.get(f"/student/analytics/post/{post.id}")

        assert resp.status_code == 401

    def test_nonexistent_post_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/analytics/post/999999", headers=auth_headers(user))

        assert resp.status_code == 404

    def test_other_users_post_is_forbidden(self, client, make_user, make_post, auth_headers):
        author = make_user()
        post = make_post(author)
        other = make_user()

        resp = client.get(f"/student/analytics/post/{post.id}", headers=auth_headers(other))

        assert resp.status_code == 403

    def test_own_post_returns_metrics_and_timeline(
        self, client, db_session, make_user, make_post, auth_headers
    ):
        author = make_user()
        post = make_post(
            author, views_count=5, positive_reactions_count=2, comments_count=1
        )
        viewer = make_user()
        _post_view(db_session, viewer, post)

        resp = client.get(f"/student/analytics/post/{post.id}", headers=auth_headers(author))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["post"]["id"] == post.id
        assert data["metrics"]["views"] == 5
        assert data["metrics"]["reactions"] == 2
        assert len(data["timeline"]) == 1
        assert data["timeline"][0]["views"] == 1


# ============================================================================
# GET /analytics/weekly-summary
# ============================================================================

class TestWeeklySummary:
    def test_requires_auth(self, client):
        resp = client.get("/student/analytics/weekly-summary")

        assert resp.status_code == 401

    def test_summary_reflects_this_weeks_activity(
        self, client, db_session, make_user, make_post, auth_headers
    ):
        user = make_user()
        make_post(user)
        _reputation_entry(db_session, user, points_change=25)

        resp = client.get("/student/analytics/weekly-summary", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["summary"]["reputation_earned"] == 25
        assert data["summary"]["posts_created"] == 1
        assert "25" in data["message"]

    def test_old_reputation_entries_are_excluded(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        old = datetime.datetime.utcnow() - datetime.timedelta(days=30)
        _reputation_entry(db_session, user, points_change=99, created_at=old)

        resp = client.get("/student/analytics/weekly-summary", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert data["summary"]["reputation_earned"] == 0


# ============================================================================
# GET /analytics/export
# ============================================================================

class TestExportAnalytics:
    def test_requires_auth(self, client):
        resp = client.get("/student/analytics/export")

        assert resp.status_code == 401

    def test_default_overview_export(self, client, make_user, auth_headers):
        user = make_user(total_posts=3, reputation=50)

        resp = client.get("/student/analytics/export", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["export_type"] == "overview"
        metrics = {r["metric"]: r["value"] for r in data["records"]}
        assert metrics["Total Posts"] == 3
        assert metrics["Total Reputation"] == 50

    def test_invalid_type_falls_back_to_overview(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/analytics/export?type=not-a-real-type", headers=auth_headers(user)
        )

        data = resp.get_json()["data"]
        assert data["export_type"] == "overview"

    def test_activity_export(self, client, db_session, make_user, auth_headers):
        user = make_user()
        _activity(db_session, user, posts_created=2, activity_score=15)

        resp = client.get(
            "/student/analytics/export?type=activity", headers=auth_headers(user)
        )

        data = resp.get_json()["data"]
        assert data["export_type"] == "activity"
        assert len(data["records"]) == 1
        assert data["records"][0]["posts_created"] == 2

    def test_reputation_export(self, client, db_session, make_user, auth_headers):
        user = make_user()
        _reputation_entry(db_session, user, action="post_created", points_change=5)

        resp = client.get(
            "/student/analytics/export?type=reputation", headers=auth_headers(user)
        )

        data = resp.get_json()["data"]
        assert data["export_type"] == "reputation"
        assert data["records"][0]["action"] == "post_created"
        assert data["records"][0]["points_change"] == 5

    def test_posts_export(self, client, make_user, make_post, auth_headers):
        user = make_user()
        make_post(user, title="Exported Post")

        resp = client.get(
            "/student/analytics/export?type=posts", headers=auth_headers(user)
        )

        data = resp.get_json()["data"]
        assert data["export_type"] == "posts"
        assert data["records"][0]["title"] == "Exported Post"
