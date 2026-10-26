"""
tests/integration/test_homework_assignments_routes.py

routes/student/homework_system.py has 19 endpoints and previously had
zero tests at any level. This file covers the personal-assignment CRUD
surface (GET/POST/PUT/DELETE /assignments), which is the core,
self-contained part of this file. The homework-marketplace endpoints
(offer-help, submissions, feedback, champions, activity feed, stats)
build on top of this and are a separate, larger area left for a later
pass.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


def _future_iso(days=3):
    return (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat()


# ============================================================================
# GET /assignments
# ============================================================================

class TestGetMyAssignments:
    def test_empty_when_no_assignments(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/assignments", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["assignments"] == []
        assert data["total"] == 0

    def test_default_status_active_excludes_completed(
        self, client, make_user, make_assignment, auth_headers
    ):
        user = make_user()
        make_assignment(user, title="Active one", status="not_started")
        make_assignment(user, title="Done one", status="completed")

        resp = client.get("/student/assignments", headers=auth_headers(user))

        titles = [a["title"] for a in resp.get_json()["data"]["assignments"]]
        assert "Active one" in titles
        assert "Done one" not in titles

    def test_status_all_includes_completed(
        self, client, make_user, make_assignment, auth_headers
    ):
        user = make_user()
        make_assignment(user, title="Done one", status="completed")

        resp = client.get("/student/assignments?status=all", headers=auth_headers(user))

        titles = [a["title"] for a in resp.get_json()["data"]["assignments"]]
        assert "Done one" in titles

    def test_only_returns_current_users_assignments(
        self, client, make_user, make_assignment, auth_headers
    ):
        me = make_user()
        other = make_user()
        make_assignment(other, title="Not mine")
        make_assignment(me, title="Mine")

        resp = client.get("/student/assignments?status=all", headers=auth_headers(me))

        titles = [a["title"] for a in resp.get_json()["data"]["assignments"]]
        assert titles == ["Mine"]

    def test_subject_filter(self, client, make_user, make_assignment, auth_headers):
        user = make_user()
        make_assignment(user, title="Calc HW", subject="Calculus")
        make_assignment(user, title="Chem HW", subject="Chemistry")

        resp = client.get("/student/assignments?subject=Calculus", headers=auth_headers(user))

        titles = [a["title"] for a in resp.get_json()["data"]["assignments"]]
        assert titles == ["Calc HW"]

    def test_includes_priority_score_and_urgency_fields(
        self, client, make_user, make_assignment, auth_headers
    ):
        user = make_user()
        make_assignment(user, title="Urgent", due_date=datetime.datetime.utcnow() + datetime.timedelta(hours=2))

        resp = client.get("/student/assignments", headers=auth_headers(user))

        entry = resp.get_json()["data"]["assignments"][0]
        assert "priority_score" in entry
        assert "urgency_level" in entry
        assert entry["is_overdue"] is False

    def test_overdue_assignment_flagged(self, client, make_user, make_assignment, auth_headers):
        user = make_user()
        make_assignment(
            user, title="Late one", status="in_progress",
            due_date=datetime.datetime.utcnow() - datetime.timedelta(hours=5),
        )

        resp = client.get("/student/assignments", headers=auth_headers(user))

        entry = resp.get_json()["data"]["assignments"][0]
        assert entry["is_overdue"] is True


# ============================================================================
# POST /assignments
# ============================================================================

class TestCreateAssignment:
    def test_happy_path_creates_assignment_with_computed_priority_score(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/assignments",
            json={
                "title": "Calculus Problem Set 5",
                "subject": "Calculus",
                "due_date": _future_iso(),
                "difficulty": "hard",
            },
            headers=headers,
        )

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["data"]["title"] == "Calculus Problem Set 5"
        assert isinstance(body["data"]["priority_score"], (int, float))

        from models import Assignment
        row = Assignment.query.get(body["data"]["id"])
        assert row.user_id == user.id
        assert row.status == "not_started"

    def test_missing_title_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/assignments", json={"due_date": _future_iso()}, headers=headers
        )

        assert resp.status_code == 400
        assert "Title is required" in resp.get_json()["message"]

    def test_missing_due_date_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/assignments", json={"title": "X"}, headers=headers)

        assert resp.status_code == 400
        assert "Due date is required" in resp.get_json()["message"]

    def test_past_due_date_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}
        past = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).isoformat()

        resp = client.post(
            "/student/assignments", json={"title": "X", "due_date": past}, headers=headers
        )

        assert resp.status_code == 400
        assert "future" in resp.get_json()["message"].lower()

    def test_invalid_due_date_format_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/assignments", json={"title": "X", "due_date": "not-a-date"}, headers=headers
        )

        assert resp.status_code == 400

    def test_resource_missing_url_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/assignments",
            json={
                "title": "X", "due_date": _future_iso(),
                "resources": [{"type": "pdf"}],  # missing url
            },
            headers=headers,
        )

        assert resp.status_code == 400
        assert "must have a url" in resp.get_json()["message"]

    def test_resources_not_a_list_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/assignments",
            json={"title": "X", "due_date": _future_iso(), "resources": "not-a-list"},
            headers=headers,
        )

        assert resp.status_code == 400


# ============================================================================
# PUT /assignments/<id>
# ============================================================================

class TestUpdateAssignment:
    def test_owner_updates_title_and_status(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        user = make_user()
        assignment = make_assignment(user, title="Old title")
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            f"/student/assignments/{assignment.id}",
            json={"title": "New title", "status": "in_progress"},
            headers=headers,
        )

        assert resp.status_code == 200
        from models import Assignment
        row = Assignment.query.get(assignment.id)
        assert row.title == "New title"
        assert row.status == "in_progress"

    def test_marking_completed_sets_completed_at(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        user = make_user()
        assignment = make_assignment(user, status="in_progress")
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            f"/student/assignments/{assignment.id}", json={"status": "completed"}, headers=headers
        )

        assert resp.status_code == 200
        from models import Assignment
        row = Assignment.query.get(assignment.id)
        assert row.completed_at is not None

    def test_reopening_a_completed_assignment_clears_completed_at(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        user = make_user()
        assignment = make_assignment(
            user, status="completed", completed_at=datetime.datetime.utcnow()
        )
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            f"/student/assignments/{assignment.id}", json={"status": "in_progress"}, headers=headers
        )

        assert resp.status_code == 200
        from models import Assignment
        row = Assignment.query.get(assignment.id)
        assert row.completed_at is None

    def test_non_owner_cannot_update(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        other = make_user()
        assignment = make_assignment(owner)
        headers = {**auth_headers(other), **csrf_headers(client)}

        resp = client.put(
            f"/student/assignments/{assignment.id}", json={"title": "Hijacked"}, headers=headers
        )

        assert resp.status_code == 403

    def test_nonexistent_assignment_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            "/student/assignments/999999", json={"title": "X"}, headers=headers
        )

        assert resp.status_code == 404

    def test_resources_not_a_list_rejected(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        user = make_user()
        assignment = make_assignment(user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            f"/student/assignments/{assignment.id}",
            json={"resources": "not-a-list"},
            headers=headers,
        )

        assert resp.status_code == 400


# ============================================================================
# DELETE /assignments/<id>
# ============================================================================

class TestDeleteAssignment:
    def test_owner_deletes_assignment(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        user = make_user()
        assignment = make_assignment(user)
        assignment_id = assignment.id
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete(f"/student/assignments/{assignment_id}", headers=headers)

        assert resp.status_code == 200
        from models import Assignment
        assert Assignment.query.get(assignment_id) is None

    def test_also_deletes_associated_help_requests(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)

        from models import HomeworkSubmission
        submission = HomeworkSubmission(
            assignment_id=assignment.id, requester_id=owner.id, helper_id=helper.id,
            title="Help with calc", status="pending",
        )
        db_session.add(submission)
        db_session.flush()
        submission_id = submission.id

        headers = {**auth_headers(owner), **csrf_headers(client)}
        resp = client.delete(f"/student/assignments/{assignment.id}", headers=headers)

        assert resp.status_code == 200
        assert HomeworkSubmission.query.get(submission_id) is None

    def test_non_owner_cannot_delete(
        self, client, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        other = make_user()
        assignment = make_assignment(owner)
        headers = {**auth_headers(other), **csrf_headers(client)}

        resp = client.delete(f"/student/assignments/{assignment.id}", headers=headers)

        assert resp.status_code == 403

    def test_nonexistent_assignment_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete("/student/assignments/999999", headers=headers)

        assert resp.status_code == 404
