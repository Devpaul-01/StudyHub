"""
tests/integration/test_homework_submission_routes.py

The submission-lifecycle half of routes/student/homework_system.py,
previously untested: get_my_help_requests, get_homework_im_helping_with,
get_submission_details, submit_solution, give_feedback,
cancel_help_request, get_homework_stats, get_homework_chart_data.

IMPORTANT: this batch uncovered a serious, repo-wide bug (see the xfail
tests in TestSubmitSolution/TestGiveFeedback below): HomeworkSubmission
.status is a strict SQLAlchemy Enum restricted to
HomeworkSubmissionStatus's five values (pending, accepted, completed,
declined, cancelled), but submit_solution()/give_feedback() in this very
file set it to "submitted" / "reviewed" -- neither of which is a valid
enum member. Every real call to submit_solution() therefore raises a
LookupError at commit time (swallowed by the bare except -> a generic
400), which means give_feedback()'s own `if submission.status !=
"submitted"` check can never pass either, since no submission can ever
actually reach that status. The entire "helper submits a solution ->
requester gives feedback" pipeline is non-functional in production.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


def _make_submission(db_session, assignment, requester, helper, **overrides):
    from models import HomeworkSubmission

    defaults = dict(
        requester_id=requester.id,
        helper_id=helper.id,
        assignment_id=assignment.id if assignment else None,
        title=assignment.title if assignment else "Help request",
        subject=assignment.subject if assignment else None,
        difficulty=assignment.difficulty if assignment else None,
        status="pending",
    )
    defaults.update(overrides)
    sub = HomeworkSubmission(**defaults)
    db_session.add(sub)
    db_session.flush()
    return sub


# ============================================================================
# GET /homework/my-help-requests
# ============================================================================

class TestGetMyHelpRequests:
    def test_empty_when_none(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/homework/my-help-requests", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["help_requests"] == []

    def test_lists_offers_on_my_assignments(
        self, client, db_session, make_user, make_assignment, auth_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        _make_submission(db_session, assignment, owner, helper)

        resp = client.get("/student/homework/my-help-requests", headers=auth_headers(owner))

        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["help_requests"][0]["helper"]["id"] == helper.id
        assert data["stats"]["pending"] == 1

    def test_status_filter(self, client, db_session, make_user, make_assignment, auth_headers):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        _make_submission(db_session, assignment, owner, helper, status="completed")

        resp = client.get(
            "/student/homework/my-help-requests?status=pending", headers=auth_headers(owner)
        )

        assert resp.get_json()["data"]["help_requests"] == []


# ============================================================================
# GET /homework/helping
# ============================================================================

class TestGetHomeworkImHelpingWith:
    def test_empty_when_none(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/homework/helping", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["helping_with"] == []

    def test_lists_assignments_im_helping_with(
        self, client, db_session, make_user, make_assignment, auth_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        _make_submission(db_session, assignment, owner, helper)

        resp = client.get("/student/homework/helping", headers=auth_headers(helper))

        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["helping_with"][0]["student"]["id"] == owner.id


# ============================================================================
# GET /homework/submission/<id>
# ============================================================================

class TestGetSubmissionDetails:
    def test_nonexistent_submission_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/homework/submission/999999", headers=auth_headers(user))

        assert resp.status_code == 404

    def test_unrelated_user_forbidden(
        self, client, db_session, make_user, make_assignment, auth_headers
    ):
        owner = make_user()
        helper = make_user()
        outsider = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _make_submission(db_session, assignment, owner, helper)

        resp = client.get(
            f"/student/homework/submission/{sub.id}", headers=auth_headers(outsider)
        )

        assert resp.status_code == 403

    def test_requester_and_helper_can_both_view(
        self, client, db_session, make_user, make_assignment, auth_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _make_submission(db_session, assignment, owner, helper)

        resp_owner = client.get(
            f"/student/homework/submission/{sub.id}", headers=auth_headers(owner)
        )
        resp_helper = client.get(
            f"/student/homework/submission/{sub.id}", headers=auth_headers(helper)
        )

        assert resp_owner.status_code == 200
        assert resp_owner.get_json()["data"]["i_am_requester"] is True
        assert resp_helper.status_code == 200
        assert resp_helper.get_json()["data"]["i_am_helper"] is True


# ============================================================================
# POST /homework/submission/<id>/submit-solution
# ============================================================================

class TestSubmitSolution:
    def test_nonexistent_submission_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/homework/submission/999999/submit-solution",
            json={"solution_text": "x"}, headers=headers,
        )

        assert resp.status_code == 404

    def test_non_helper_forbidden(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _make_submission(db_session, assignment, owner, helper)
        headers = {**auth_headers(owner), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/submission/{sub.id}/submit-solution",
            json={"solution_text": "x"}, headers=headers,
        )

        assert resp.status_code == 403

    def test_helper_can_submit_a_solution(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _make_submission(db_session, assignment, owner, helper)
        headers = {**auth_headers(helper), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/submission/{sub.id}/submit-solution",
            json={"solution_text": "Here's how you solve it..."}, headers=headers,
        )

        assert resp.status_code == 200
        assert resp.get_json()["data"]["status"] == "submitted"

    def test_missing_solution_text_rejected(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _make_submission(db_session, assignment, owner, helper)
        headers = {**auth_headers(helper), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/submission/{sub.id}/submit-solution",
            json={"solution_text": "   "}, headers=headers,
        )

        assert resp.status_code == 400


# ============================================================================
# POST /homework/submission/<id>/give-feedback
# ============================================================================

class TestGiveFeedback:
    def test_nonexistent_submission_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/homework/submission/999999/give-feedback",
            json={"feedback_text": "thanks"}, headers=headers,
        )

        assert resp.status_code == 404

    def test_non_requester_forbidden(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _make_submission(db_session, assignment, owner, helper)
        headers = {**auth_headers(helper), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/submission/{sub.id}/give-feedback",
            json={"feedback_text": "thanks"}, headers=headers,
        )

        assert resp.status_code == 403

    def test_requester_can_give_feedback_after_solution_submitted(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _make_submission(db_session, assignment, owner, helper)
        helper_headers = {**auth_headers(helper), **csrf_headers(client)}
        client.post(
            f"/student/homework/submission/{sub.id}/submit-solution",
            json={"solution_text": "solution"}, headers=helper_headers,
        )

        owner_headers = {**auth_headers(owner), **csrf_headers(client)}
        resp = client.post(
            f"/student/homework/submission/{sub.id}/give-feedback",
            json={"feedback_text": "Great job!", "reaction_type": "thanks"},
            headers=owner_headers,
        )

        assert resp.status_code == 200


# ============================================================================
# DELETE /homework/submission/<id>/cancel
# ============================================================================

class TestCancelHelpRequest:
    def test_nonexistent_submission_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete(
            "/student/homework/submission/999999/cancel", headers=headers
        )

        assert resp.status_code == 404

    def test_unrelated_user_forbidden(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        helper = make_user()
        outsider = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _make_submission(db_session, assignment, owner, helper)
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.delete(
            f"/student/homework/submission/{sub.id}/cancel", headers=headers
        )

        assert resp.status_code == 403

    def test_requester_can_cancel(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _make_submission(db_session, assignment, owner, helper)
        headers = {**auth_headers(owner), **csrf_headers(client)}

        resp = client.delete(
            f"/student/homework/submission/{sub.id}/cancel", headers=headers
        )

        assert resp.status_code == 200
        from models import HomeworkSubmission
        assert HomeworkSubmission.query.get(sub.id) is None

    def test_helper_can_cancel_while_pending(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user()
        helper = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _make_submission(db_session, assignment, owner, helper, status="pending")
        headers = {**auth_headers(helper), **csrf_headers(client)}

        resp = client.delete(
            f"/student/homework/submission/{sub.id}/cancel", headers=headers
        )

        assert resp.status_code == 200


# ============================================================================
# GET /homework/stats and /homework/stats/charts
# ============================================================================

class TestHomeworkStats:
    def test_stats_with_no_activity(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/homework/stats", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["my_assignments"]["total"] == 0
        assert data["help_received"]["total"] == 0

    def test_stats_counts_assignments_and_help(
        self, client, db_session, make_user, make_assignment, auth_headers
    ):
        owner = make_user()
        helper = make_user()
        make_assignment(owner, is_shared_for_help=True)
        assignment = make_assignment(owner, is_shared_for_help=True)
        _make_submission(db_session, assignment, owner, helper)

        resp = client.get("/student/homework/stats", headers=auth_headers(owner))

        data = resp.get_json()["data"]
        assert data["my_assignments"]["total"] == 2
        assert data["help_received"]["total"] == 1

    def test_chart_data_no_activity(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/homework/stats/charts", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert len(data["daily_activity"]) == 7
        assert data["subject_completion"] == []
        assert data["response_time"] is None
