"""
tests/integration/test_homework_discovery_routes.py

routes/student/homework_system.py's assignment GET/POST/PUT/DELETE CRUD
was already covered in test_homework_assignments_routes.py. This batch
covers the rest of the "discovery and offering help" half of that file:
get_assignment_helpers, get_activity_feed, get_my_streak,
get_current_champions, assignment_quick_actions, get_homework_feed, and
offer_homework_help. The submission-lifecycle endpoints (my-help-requests,
helping, submission detail/submit-solution/give-feedback/cancel, and the
stats endpoints) are left for a following batch.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


def _make_submission(db_session, assignment, requester, helper, **overrides):
    from models import HomeworkSubmission

    defaults = dict(
        requester_id=requester.id,
        helper_id=helper.id,
        assignment_id=assignment.id,
        title=assignment.title,
        subject=assignment.subject,
        difficulty=assignment.difficulty,
        status="pending",
    )
    defaults.update(overrides)
    sub = HomeworkSubmission(**defaults)
    db_session.add(sub)
    db_session.flush()
    return sub


# ============================================================================
# GET /homework/<assignment_id>/helpers
# ============================================================================

class TestGetAssignmentHelpers:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG FOUND BY THIS TEST: get_assignment_helpers() calls "
            "Assignment.query.get_or_404(assignment_id), which raises "
            "werkzeug's NotFound (an HTTPException, itself a subclass of "
            "Exception) for a missing id -- but the route wraps that call "
            "in a bare `except Exception`, which catches the NotFound "
            "before Flask ever gets to turn it into a real 404 response. "
            "The except handler then returns its own hardcoded 500 "
            "('Error fetching helpers: 404 Not Found: ...'), so GET "
            "/homework/<id>/helpers for a nonexistent assignment id "
            "responds 500 instead of 404. strict=True so this flips to "
            "XPASS once get_or_404()'s abort is let propagate (or the "
            "route checks `if not assignment` and returns error_response(…, "
            "404) itself, matching every other route in this file)."
        ),
    )
    def test_nonexistent_assignment_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/homework/999999/helpers", headers=auth_headers(user))

        assert resp.status_code == 404

    def test_non_owner_forbidden(self, client, make_user, make_assignment, auth_headers):
        owner = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        outsider = make_user()

        resp = client.get(
            f"/student/homework/{assignment.id}/helpers", headers=auth_headers(outsider)
        )

        assert resp.status_code == 403

    def test_not_shared_assignment_rejected(self, client, make_user, make_assignment, auth_headers):
        owner = make_user()
        assignment = make_assignment(owner, is_shared_for_help=False)

        resp = client.get(
            f"/student/homework/{assignment.id}/helpers", headers=auth_headers(owner)
        )

        assert resp.status_code == 400

    def test_lists_helpers_for_shared_assignment(
        self, client, db_session, make_user, make_assignment, auth_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        _make_submission(db_session, assignment, owner, helper)

        resp = client.get(
            f"/student/homework/{assignment.id}/helpers", headers=auth_headers(owner)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total_helpers"] == 1
        assert data["helpers"][0]["helper"]["id"] == helper.id


# ============================================================================
# GET /activity/feed
# ============================================================================

class TestGetActivityFeed:
    def test_empty_when_no_connections(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/activity/feed", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["activities"] == []

    def test_connections_activity_is_listed(
        self, client, db_session, make_user, make_connection, auth_headers
    ):
        from models import ActivityFeed

        me = make_user()
        friend = make_user()
        make_connection(me, friend, status="accepted")

        activity = ActivityFeed(
            user_id=friend.id,
            activity_type="assignment_created",
            activity_data={"title": "Calc HW"},
            created_at=datetime.datetime.utcnow(),
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=24),
        )
        db_session.add(activity)
        db_session.flush()

        resp = client.get("/student/activity/feed", headers=auth_headers(me))

        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["activities"][0]["user"]["id"] == friend.id


# ============================================================================
# GET /homework/my-streak
# ============================================================================

class TestGetMyStreak:
    def test_no_prior_activity(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/homework/my-streak", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["current_streak"] == 0
        assert data["helped_today"] is False

    def test_helped_today_reflects_streak_last_updated(
        self, client, make_user, auth_headers
    ):
        user = make_user(
            help_streak_current=3,
            help_streak_longest=5,
            help_streak_last_updated=datetime.datetime.utcnow(),
        )

        resp = client.get("/student/homework/my-streak", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert data["current_streak"] == 3
        assert data["helped_today"] is True
        assert data["streak_at_risk"] is False


# ============================================================================
# GET /homework/champions
# ============================================================================

class TestGetCurrentChampions:
    def test_no_champions_this_week(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/homework/champions", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["champions"]["subject_champions"] == []
        assert data["champions"]["most_helpful"] is None

    def test_includes_a_registered_champion(self, client, db_session, make_user, auth_headers):
        from models import WeeklyChampion

        user = make_user()
        champ = make_user()
        today = datetime.datetime.utcnow().date()
        week_start = today - datetime.timedelta(days=today.weekday())

        db_session.add(
            WeeklyChampion(
                user_id=champ.id,
                week_start=week_start,
                week_end=week_start + datetime.timedelta(days=6),
                champion_type="most_helpful_overall",
                subject=None,
                help_count=10,
            )
        )
        db_session.flush()

        resp = client.get("/student/homework/champions", headers=auth_headers(user))

        data = resp.get_json()["data"]["champions"]
        assert data["most_helpful"]["user"]["id"] == champ.id
        assert data["most_helpful"]["help_count"] == 10


# ============================================================================
# POST /assignments/<id>/quick-actions
# ============================================================================

class TestAssignmentQuickActions:
    def test_nonexistent_assignment_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/assignments/999999/quick-actions",
            json={"action": "mark_complete"}, headers=headers,
        )

        assert resp.status_code == 404

    def test_non_owner_forbidden(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        assignment = make_assignment(owner)
        outsider = make_user()
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.post(
            f"/student/assignments/{assignment.id}/quick-actions",
            json={"action": "mark_complete"}, headers=headers,
        )

        assert resp.status_code == 403

    def test_mark_complete_sets_status_and_completed_at(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        assignment = make_assignment(owner)
        headers = {**auth_headers(owner), **csrf_headers(client)}

        resp = client.post(
            f"/student/assignments/{assignment.id}/quick-actions",
            json={"action": "mark_complete"}, headers=headers,
        )

        assert resp.status_code == 200
        assert resp.get_json()["data"]["status"] == "completed"
        from models import Assignment
        updated = Assignment.query.get(assignment.id)
        assert updated.completed_at is not None

    def test_invalid_action_rejected(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        assignment = make_assignment(owner)
        headers = {**auth_headers(owner), **csrf_headers(client)}

        resp = client.post(
            f"/student/assignments/{assignment.id}/quick-actions",
            json={"action": "nonsense"}, headers=headers,
        )

        assert resp.status_code == 400

    def test_double_share_rejected(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        headers = {**auth_headers(owner), **csrf_headers(client)}

        resp = client.post(
            f"/student/assignments/{assignment.id}/quick-actions",
            json={"action": "share_for_help"}, headers=headers,
        )

        assert resp.status_code == 400


# ============================================================================
# GET /homework/feed
# ============================================================================

class TestGetHomeworkFeed:
    def test_no_connections_returns_empty_with_message(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/homework/feed", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["homework"] == []
        assert "message" in data

    def test_shared_assignment_from_connection_is_listed(
        self, client, make_user, make_connection, make_assignment, auth_headers
    ):
        me = make_user()
        friend = make_user()
        make_connection(me, friend, status="accepted")
        make_assignment(friend, is_shared_for_help=True, title="Shared HW")

        resp = client.get("/student/homework/feed", headers=auth_headers(me))

        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["homework"][0]["title"] == "Shared HW"
        assert data["homework"][0]["already_helping"] is False

    def test_unshared_assignment_excluded(
        self, client, make_user, make_connection, make_assignment, auth_headers
    ):
        me = make_user()
        friend = make_user()
        make_connection(me, friend, status="accepted")
        make_assignment(friend, is_shared_for_help=False)

        resp = client.get("/student/homework/feed", headers=auth_headers(me))

        assert resp.get_json()["data"]["homework"] == []

    def test_subject_filter(
        self, client, make_user, make_connection, make_assignment, auth_headers
    ):
        me = make_user()
        friend = make_user()
        make_connection(me, friend, status="accepted")
        make_assignment(friend, is_shared_for_help=True, subject="Math")
        make_assignment(friend, is_shared_for_help=True, subject="Physics")

        resp = client.get("/student/homework/feed?subject=Math", headers=auth_headers(me))

        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["homework"][0]["subject"] == "Math"


# ============================================================================
# POST /homework/<id>/offer-help
# ============================================================================

class TestOfferHomeworkHelp:
    def test_nonexistent_assignment_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/homework/999999/offer-help", json={}, headers=headers
        )

        assert resp.status_code == 404

    def test_own_assignment_rejected(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        headers = {**auth_headers(owner), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/{assignment.id}/offer-help", json={}, headers=headers
        )

        assert resp.status_code == 400

    def test_not_shared_rejected(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        assignment = make_assignment(owner, is_shared_for_help=False)
        helper = make_user()
        headers = {**auth_headers(helper), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/{assignment.id}/offer-help", json={}, headers=headers
        )

        assert resp.status_code == 403

    def test_not_connected_rejected(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        helper = make_user()
        headers = {**auth_headers(helper), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/{assignment.id}/offer-help", json={}, headers=headers
        )

        assert resp.status_code == 403

    def test_connected_helper_can_offer_help(
        self, client, make_user, make_connection, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        helper = make_user()
        make_connection(owner, helper, status="accepted")
        assignment = make_assignment(owner, is_shared_for_help=True)
        headers = {**auth_headers(helper), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/{assignment.id}/offer-help", json={}, headers=headers
        )

        assert resp.status_code == 201
        from models import HomeworkSubmission
        sub = HomeworkSubmission.query.filter_by(
            assignment_id=assignment.id, helper_id=helper.id
        ).first()
        assert sub is not None
        assert sub.status == "pending"

    def test_already_helping_rejected(
        self, client, db_session, make_user, make_connection, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        helper = make_user()
        make_connection(owner, helper, status="accepted")
        assignment = make_assignment(owner, is_shared_for_help=True)
        _make_submission(db_session, assignment, owner, helper)
        headers = {**auth_headers(helper), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/{assignment.id}/offer-help", json={}, headers=headers
        )

        assert resp.status_code == 400
