"""
tests/integration/test_homework_hardening.py

Regression tests for audit findings F-07 (submit_solution ordering / wrong
name), F-09 (submission payload validation) and F-22 (faults surfaced as 500,
no exception text leaked).
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


def _submission(db_session, assignment, requester, helper, **overrides):
    from models import HomeworkSubmission

    sub = HomeworkSubmission(
        requester_id=requester.id,
        helper_id=helper.id,
        assignment_id=assignment.id,
        title=assignment.title,
        subject=assignment.subject,
        difficulty=assignment.difficulty,
        status=overrides.pop("status", "pending"),
        **overrides,
    )
    db_session.add(sub)
    db_session.flush()
    return sub


def _submit(client, helper, sub, auth_headers, csrf_headers, **kwargs):
    headers = {**auth_headers(helper), **csrf_headers(client)}
    return client.post(
        f"/student/homework/submission/{sub.id}/submit-solution", headers=headers, **kwargs
    )


def _activity_rows(activity_type):
    from models import ActivityFeed

    return ActivityFeed.query.filter_by(activity_type=activity_type).all()


# ============================================================================
# F-07 / F-09: submit_solution
# ============================================================================

class TestSubmitSolutionOrdering:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"json": {"solution_text": "   "}},
            {"json": {}},
            {"json": {"solution_text": "x" * 20_001}},
            {"json": {"solution_text": "ok", "resources": [{"url": "javascript:alert(1)", "type": "image"}]}},
            {"data": "not json", "content_type": "text/plain"},
        ],
    )
    def test_invalid_body_has_no_side_effects(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers, kwargs
    ):
        from models import HomeworkSubmission

        owner, helper = make_user(), make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _submission(db_session, assignment, owner, helper)

        resp = _submit(client, helper, sub, auth_headers, csrf_headers, **kwargs)

        assert resp.status_code == 400
        assert _activity_rows("submitted_solution") == []
        fresh = HomeworkSubmission.query.get(sub.id)
        assert fresh.response_time_seconds is None
        assert fresh.status == "pending"
        assert fresh.solution_text is None

    def test_valid_submit_creates_one_event_naming_the_requester(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner = make_user(name="Olivia Owner")
        helper = make_user(name="Henry Helper")
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _submission(db_session, assignment, owner, helper)

        resp = _submit(
            client, helper, sub, auth_headers, csrf_headers, json={"solution_text": "Do this."}
        )

        assert resp.status_code == 200
        rows = _activity_rows("submitted_solution")
        assert len(rows) == 1
        assert rows[0].user_id == helper.id
        assert rows[0].activity_data["requester_name"] == "Olivia Owner"

    def test_resubmitting_does_not_duplicate_the_event(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _submission(db_session, assignment, owner, helper)

        for text in ("first", "second"):
            resp = _submit(
                client, helper, sub, auth_headers, csrf_headers, json={"solution_text": text}
            )
            assert resp.status_code == 200

        assert len(_activity_rows("submitted_solution")) == 1

    def test_response_time_measured_to_first_real_solution(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        from models import HomeworkSubmission

        owner, helper = make_user(), make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _submission(
            db_session, assignment, owner, helper,
            created_at=datetime.datetime.utcnow() - datetime.timedelta(seconds=600),
        )

        _submit(client, helper, sub, auth_headers, csrf_headers, json={"solution_text": ""})
        assert HomeworkSubmission.query.get(sub.id).response_time_seconds is None

        _submit(client, helper, sub, auth_headers, csrf_headers, json={"solution_text": "real"})
        assert 590 <= HomeworkSubmission.query.get(sub.id).response_time_seconds <= 660


# ============================================================================
# F-09: give_feedback validation
# ============================================================================

class TestFeedbackValidation:
    def _submitted(self, db_session, make_user, make_assignment):
        owner, helper = make_user(), make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)
        sub = _submission(db_session, assignment, owner, helper, status="submitted",
                          solution_text="s")
        return owner, helper, sub

    @pytest.mark.parametrize(
        "body",
        [
            {"rating": 9},
            {"rating": 0},
            {"rating": "abc"},
            {"rating": 3.5},
            {"rating": True},
            {"reaction_type": "banana"},
            {"feedback_text": "x" * 5_001},
            {"feedback_text": 12},
            {"mark_complete": "yes"},
            {"feedback_resources": [{"url": "javascript:alert(1)", "type": "image"}]},
        ],
    )
    def test_invalid_feedback_rejected_and_unchanged(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers, body
    ):
        from models import HomeworkSubmission

        owner, _, sub = self._submitted(db_session, make_user, make_assignment)
        headers = {**auth_headers(owner), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/submission/{sub.id}/give-feedback", json=body, headers=headers
        )

        assert resp.status_code == 400
        fresh = HomeworkSubmission.query.get(sub.id)
        assert fresh.status == "submitted"
        assert fresh.feedback_at is None

    def test_non_json_body_is_400(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, _, sub = self._submitted(db_session, make_user, make_assignment)
        headers = {**auth_headers(owner), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/submission/{sub.id}/give-feedback",
            data="nope", content_type="text/plain", headers=headers,
        )

        assert resp.status_code == 400

    def test_valid_feedback_still_completes(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        from models import HomeworkSubmission

        owner, _, sub = self._submitted(db_session, make_user, make_assignment)
        headers = {**auth_headers(owner), **csrf_headers(client)}

        resp = client.post(
            f"/student/homework/submission/{sub.id}/give-feedback",
            json={"rating": 5, "reaction_type": "thanks", "feedback_text": "Thanks!"},
            headers=headers,
        )

        assert resp.status_code == 200
        fresh = HomeworkSubmission.query.get(sub.id)
        assert fresh.feedback_rating == 5
        assert fresh.reaction_type == "thanks"
        assert str(getattr(fresh.status, "value", fresh.status)) == "completed"


# ============================================================================
# F-22: unexpected faults are 500s and never leak exception text
# ============================================================================

class TestServerFaultHandling:
    SECRET = "boom-secret-internal-detail"

    def test_unexpected_fault_is_500_and_generic(
        self, client, make_user, auth_headers, csrf_headers, monkeypatch
    ):
        from routes.student import homework_system

        def explode(*a, **k):
            raise RuntimeError(self.SECRET)

        monkeypatch.setattr(homework_system.homework_service, "calculate_priority_score", explode)
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/assignments",
            json={"title": "T", "due_date": (datetime.datetime.utcnow() + datetime.timedelta(days=2)).isoformat()},
            headers=headers,
        )

        assert resp.status_code == 500
        assert self.SECRET not in resp.get_data(as_text=True)
        assert resp.get_json()["message"] == "Failed to create assignment"

    def test_helpers_endpoint_does_not_echo_exception(
        self, client, db_session, make_user, make_assignment, auth_headers, monkeypatch
    ):
        from routes.student import homework_system

        owner = make_user()
        assignment = make_assignment(owner, is_shared_for_help=True)

        secret = self.SECRET

        class Boom:
            @property
            def query(self):
                raise RuntimeError(secret)

        monkeypatch.setattr(homework_system, "HomeworkSubmission", Boom())

        resp = client.get(
            f"/student/homework/{assignment.id}/helpers", headers=auth_headers(owner)
        )

        assert resp.status_code == 500
        assert self.SECRET not in resp.get_data(as_text=True)

    def test_fault_is_logged_with_traceback(
        self, app, client, make_user, auth_headers, csrf_headers, monkeypatch
    ):
        import logging

        from routes.student import homework_system

        monkeypatch.setattr(
            homework_system.homework_service, "calculate_priority_score",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError(self.SECRET)),
        )
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Capture(level=logging.ERROR)
        app.logger.addHandler(handler)
        try:
            user = make_user()
            headers = {**auth_headers(user), **csrf_headers(client)}
            client.post(
                "/student/assignments",
                json={"title": "T", "due_date": (datetime.datetime.utcnow() + datetime.timedelta(days=2)).isoformat()},
                headers=headers,
            )
        finally:
            app.logger.removeHandler(handler)

        assert any(r.exc_info and self.SECRET in str(r.exc_info[1]) for r in records)
