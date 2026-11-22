"""
tests/integration/test_homework_lifecycle.py

Regression tests for audit findings F-03 (soft cancel / history kept), F-05
(one live offer per helper), F-11 (state machine), F-12 (atomic transitions)
and F-24 (notification deep links).
"""

import datetime

import pytest

from services import homework_lifecycle as lifecycle

pytestmark = pytest.mark.integration


def _sub(db_session, assignment, requester, helper, status="pending", **kw):
    from models import HomeworkSubmission

    row = HomeworkSubmission(
        requester_id=requester.id, helper_id=helper.id,
        assignment_id=assignment.id if assignment else None,
        title=kw.pop("title", "Title"), subject=kw.pop("subject", "Math"),
        status=status, **kw,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _status(sub_id):
    from models import HomeworkSubmission

    row = HomeworkSubmission.query.get(sub_id)
    return str(getattr(row.status, "value", row.status))


def _notifications(user, ntype):
    from models import Notification

    return Notification.query.filter_by(user_id=user.id, notification_type=ntype).all()


def _hdr(client, user, auth_headers, csrf_headers):
    return {**auth_headers(user), **csrf_headers(client)}


# ============================================================================
# Transition table (pure)
# ============================================================================

class TestTransitionTable:
    def test_terminal_states_have_no_exits(self):
        for terminal in ("completed", "cancelled", "declined"):
            assert lifecycle.SUBMISSION_TRANSITIONS[terminal] == frozenset()

    def test_reviewed_can_complete_or_be_revised(self):
        assert lifecycle.can_transition("reviewed", "completed")
        assert lifecycle.can_transition("reviewed", "submitted")

    def test_pending_cannot_jump_to_completed(self):
        assert not lifecycle.can_transition("pending", "completed")

    def test_unknown_states_are_rejected(self):
        assert not lifecycle.can_transition("banana", "completed")
        assert not lifecycle.can_transition(None, "completed")


# ============================================================================
# (state x action) matrix through the real routes  (F-11 verification)
# ============================================================================

ACTIONS = {
    "submit":          ("POST",   "submit-solution", "helper",    {"solution_text": "answer"}),
    "feedback_done":   ("POST",   "give-feedback",   "requester", {"mark_complete": True}),
    "feedback_review": ("POST",   "give-feedback",   "requester", {"mark_complete": False}),
    "cancel_req":      ("DELETE", "cancel",          "requester", None),
    "cancel_helper":   ("DELETE", "cancel",          "helper",    None),
}

# expected HTTP status per (state, action)
MATRIX = {
    "pending":   {"submit": 200, "feedback_done": 400, "feedback_review": 400, "cancel_req": 200, "cancel_helper": 200},
    "submitted": {"submit": 200, "feedback_done": 200, "feedback_review": 200, "cancel_req": 200, "cancel_helper": 403},
    "reviewed":  {"submit": 200, "feedback_done": 200, "feedback_review": 400, "cancel_req": 409, "cancel_helper": 403},
    "completed": {"submit": 400, "feedback_done": 400, "feedback_review": 400, "cancel_req": 409, "cancel_helper": 403},
    "cancelled": {"submit": 400, "feedback_done": 400, "feedback_review": 400, "cancel_req": 409, "cancel_helper": 403},
}


@pytest.mark.parametrize(
    "state,action",
    [(s, a) for s, row in MATRIX.items() for a in row],
)
def test_state_action_matrix(
    state, action, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
):
    owner, helper = make_user(), make_user()
    a = make_assignment(owner, is_shared_for_help=True)
    sub = _sub(db_session, a, owner, helper, status=state, solution_text="s")
    method, suffix, actor, body = ACTIONS[action]
    user = owner if actor == "requester" else helper

    resp = client.open(
        f"/student/homework/submission/{sub.id}/{suffix}",
        method=method, json=body, headers=_hdr(client, user, auth_headers, csrf_headers),
    )

    assert resp.status_code == MATRIX[state][action], resp.get_data(as_text=True)


# ============================================================================
# F-03
# ============================================================================

class TestSoftCancel:
    def test_requester_cannot_erase_completed_help(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        sub = _sub(db_session, a, owner, helper, status="completed", feedback_text="great")

        resp = client.delete(
            f"/student/homework/submission/{sub.id}/cancel",
            headers=_hdr(client, owner, auth_headers, csrf_headers),
        )

        assert resp.status_code == 409
        assert _status(sub.id) == "completed"

    def test_cancel_pending_keeps_row_and_notifies_with_deep_link(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        sub = _sub(db_session, a, owner, helper)

        client.delete(
            f"/student/homework/submission/{sub.id}/cancel",
            headers=_hdr(client, owner, auth_headers, csrf_headers),
        )

        assert _status(sub.id) == "cancelled"
        notes = _notifications(helper, "homework_help_cancelled")
        assert len(notes) == 1
        assert notes[0].related_type == "homework_submission"
        assert notes[0].related_id == sub.id

    def test_delete_assignment_retains_completed_help_and_notifies_open_helpers(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, done_helper, open_helper = make_user(), make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        done = _sub(db_session, a, owner, done_helper, status="completed", feedback_text="ty")
        pending = _sub(db_session, a, owner, open_helper, status="pending")
        done_id, pending_id = done.id, pending.id

        resp = client.delete(
            f"/student/assignments/{a.id}", headers=_hdr(client, owner, auth_headers, csrf_headers)
        )

        from models import HomeworkSubmission

        assert resp.status_code == 200
        kept = HomeworkSubmission.query.get(done_id)
        assert kept.assignment_id is None and kept.feedback_text == "ty"
        assert _status(done_id) == "completed"
        assert _status(pending_id) == "cancelled"
        assert len(_notifications(open_helper, "homework_help_cancelled")) == 1
        assert _notifications(done_helper, "homework_help_cancelled") == []

    def test_cancelled_hidden_from_default_lists_and_stats(
        self, client, db_session, make_user, make_assignment, auth_headers
    ):
        owner, helper = make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        _sub(db_session, a, owner, helper, status="cancelled")
        _sub(db_session, a, owner, make_user(), status="pending")

        listing = client.get("/student/homework/my-help-requests", headers=auth_headers(owner))
        only_cancelled = client.get(
            "/student/homework/my-help-requests?status=cancelled", headers=auth_headers(owner)
        )
        stats = client.get("/student/homework/stats", headers=auth_headers(owner))

        assert len(listing.get_json()["data"]["help_requests"]) == 1
        assert len(only_cancelled.get_json()["data"]["help_requests"]) == 1
        assert stats.get_json()["data"]["help_received"]["total"] == 1


# ============================================================================
# F-05
# ============================================================================

class TestOfferUniqueness:
    def _offer(self, client, helper, a, auth_headers, csrf_headers):
        return client.post(
            f"/student/homework/{a.id}/offer-help", json={},
            headers=_hdr(client, helper, auth_headers, csrf_headers),
        )

    def test_second_offer_refused_single_live_row(
        self, client, make_user, make_assignment, make_connection, auth_headers, csrf_headers
    ):
        from models import HomeworkSubmission

        owner, helper = make_user(), make_user()
        make_connection(owner, helper)
        a = make_assignment(owner, is_shared_for_help=True)

        first = self._offer(client, helper, a, auth_headers, csrf_headers)
        second = self._offer(client, helper, a, auth_headers, csrf_headers)

        assert first.status_code == 201 and second.status_code == 400
        assert HomeworkSubmission.query.filter_by(assignment_id=a.id, helper_id=helper.id).count() == 1

    def test_race_loser_hits_unique_index_and_gets_clean_400(
        self, client, db_session, make_user, make_assignment, make_connection,
        auth_headers, csrf_headers, monkeypatch,
    ):
        """Simulate two requests passing the check-then-insert guard together."""
        import sqlalchemy as sa
        from models import HomeworkSubmission
        from routes.student import homework_system

        owner, helper = make_user(), make_user()
        make_connection(owner, helper)
        a = make_assignment(owner, is_shared_for_help=True)
        _sub(db_session, a, owner, helper, status="pending")
        db_session.commit()  # the route rolls back on the race; keep the seed data
        # blind the application-level guard so only the DB constraint remains
        monkeypatch.setattr(homework_system.lifecycle, "not_cancelled", lambda: sa.false())

        resp = self._offer(client, helper, a, auth_headers, csrf_headers)

        assert resp.status_code == 400
        assert HomeworkSubmission.query.filter_by(assignment_id=a.id, helper_id=helper.id).count() == 1

    def test_can_reoffer_after_cancel(
        self, client, db_session, make_user, make_assignment, make_connection,
        auth_headers, csrf_headers,
    ):
        owner, helper = make_user(), make_user()
        make_connection(owner, helper)
        a = make_assignment(owner, is_shared_for_help=True)
        _sub(db_session, a, owner, helper, status="cancelled")

        resp = self._offer(client, helper, a, auth_headers, csrf_headers)

        assert resp.status_code == 201

    def test_offer_on_completed_assignment_refused(
        self, client, make_user, make_assignment, make_connection, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        make_connection(owner, helper)
        a = make_assignment(owner, is_shared_for_help=True, status="completed")

        assert self._offer(client, helper, a, auth_headers, csrf_headers).status_code == 400


# ============================================================================
# F-11
# ============================================================================

class TestLifecycleRules:
    def test_unshare_cancels_pending_offers_keeps_submitted_and_still_lists_them(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, h1, h2 = make_user(), make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        pending = _sub(db_session, a, owner, h1, status="pending")
        submitted = _sub(db_session, a, owner, h2, status="submitted", solution_text="s")

        resp = client.post(
            f"/student/assignments/{a.id}/quick-actions", json={"action": "unshare"},
            headers=_hdr(client, owner, auth_headers, csrf_headers),
        )

        assert resp.status_code == 200
        assert _status(pending.id) == "cancelled"
        assert _status(submitted.id) == "submitted"
        assert len(_notifications(h1, "homework_help_cancelled")) == 1
        helpers = client.get(f"/student/homework/{a.id}/helpers", headers=auth_headers(owner))
        assert helpers.status_code == 200
        assert len(helpers.get_json()["data"]["helpers"]) == 1

    def test_helper_cannot_submit_after_owner_unshared(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        sub = _sub(db_session, a, owner, helper, status="pending")
        client.post(
            f"/student/assignments/{a.id}/quick-actions", json={"action": "unshare"},
            headers=_hdr(client, owner, auth_headers, csrf_headers),
        )

        resp = client.post(
            f"/student/homework/submission/{sub.id}/submit-solution", json={"solution_text": "x"},
            headers=_hdr(client, helper, auth_headers, csrf_headers),
        )

        assert resp.status_code == 400

    @pytest.mark.parametrize("via", ["put", "quick"])
    def test_completing_assignment_closes_pending_offers(
        self, via, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        sub = _sub(db_session, a, owner, helper, status="pending")
        headers = _hdr(client, owner, auth_headers, csrf_headers)

        if via == "put":
            client.put(f"/student/assignments/{a.id}", json={"status": "completed"}, headers=headers)
        else:
            client.post(f"/student/assignments/{a.id}/quick-actions",
                        json={"action": "mark_complete"}, headers=headers)

        assert _status(sub.id) == "cancelled"
        assert len(_notifications(helper, "homework_help_cancelled")) == 1

    def test_reviewed_can_be_completed_with_streak_credit(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        sub = _sub(db_session, a, owner, helper, status="submitted", solution_text="s")
        headers = _hdr(client, owner, auth_headers, csrf_headers)
        url = f"/student/homework/submission/{sub.id}/give-feedback"

        client.post(url, json={"mark_complete": False, "rating": 4}, headers=headers)
        assert _status(sub.id) == "reviewed"
        assert helper.total_helps_given == 0

        resp = client.post(url, json={"mark_complete": True}, headers=headers)

        assert resp.status_code == 200
        assert _status(sub.id) == "completed"
        from models import User
        assert User.query.get(helper.id).total_helps_given == 1

    def test_helper_can_revise_after_review(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        sub = _sub(db_session, a, owner, helper, status="reviewed", solution_text="old")

        resp = client.post(
            f"/student/homework/submission/{sub.id}/submit-solution", json={"solution_text": "new"},
            headers=_hdr(client, helper, auth_headers, csrf_headers),
        )

        assert resp.status_code == 200
        assert _status(sub.id) == "submitted"

    @pytest.mark.parametrize("who,path", [
        ("helper", "submit-solution"), ("requester", "give-feedback"),
    ])
    def test_blocked_participants_cannot_act(
        self, who, path, client, db_session, make_user, make_assignment, make_connection,
        auth_headers, csrf_headers,
    ):
        owner, helper = make_user(), make_user()
        make_connection(owner, helper, status="blocked")
        a = make_assignment(owner, is_shared_for_help=True)
        sub = _sub(db_session, a, owner, helper, status="submitted", solution_text="s")
        user = helper if who == "helper" else owner

        resp = client.post(
            f"/student/homework/submission/{sub.id}/{path}",
            json={"solution_text": "x"} if who == "helper" else {},
            headers=_hdr(client, user, auth_headers, csrf_headers),
        )

        assert resp.status_code == 403
        assert _status(sub.id) == "submitted"


# ============================================================================
# F-12 / F-24
# ============================================================================

class TestAtomicity:
    def test_claim_has_exactly_one_winner(self, db_session, make_user, make_assignment):
        owner, helper = make_user(), make_user()
        a = make_assignment(owner)
        sub = _sub(db_session, a, owner, helper, status="submitted")

        first = lifecycle.claim_submission(sub.id, {"submitted"}, status="completed")
        second = lifecycle.claim_submission(sub.id, {"submitted"}, status="completed")

        assert (first, second) == (True, False)

    def test_losing_a_race_gives_409_and_no_side_effects(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers, monkeypatch
    ):
        from models import User

        owner, helper = make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        sub = _sub(db_session, a, owner, helper, status="submitted", solution_text="s")
        db_session.commit()  # the route rolls back on the lost race; keep the seed data
        # another request already took the transition
        monkeypatch.setattr(lifecycle, "claim_submission", lambda *a, **k: False)

        resp = client.post(
            f"/student/homework/submission/{sub.id}/give-feedback", json={"rating": 5},
            headers=_hdr(client, owner, auth_headers, csrf_headers),
        )

        assert resp.status_code == 409
        assert _notifications(helper, "homework_feedback_received") == []
        assert User.query.get(helper.id).total_helps_given == 0

    def test_feedback_notification_has_deep_link_and_fires_once(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        sub = _sub(db_session, a, owner, helper, status="submitted", solution_text="s")
        headers = _hdr(client, owner, auth_headers, csrf_headers)
        url = f"/student/homework/submission/{sub.id}/give-feedback"

        client.post(url, json={"rating": 5}, headers=headers)
        client.post(url, json={"rating": 5}, headers=headers)  # replay

        notes = _notifications(helper, "homework_feedback_received")
        assert len(notes) == 1
        assert (notes[0].related_type, notes[0].related_id) == ("homework_submission", sub.id)

    def test_notification_failure_does_not_fail_the_action(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers, monkeypatch
    ):
        from services import notification_service

        owner, helper = make_user(), make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        sub = _sub(db_session, a, owner, helper, status="pending")

        def boom(*args, **kwargs):
            raise RuntimeError("push down")

        monkeypatch.setattr(notification_service, "notify_homework_solution_submitted", boom)

        resp = client.post(
            f"/student/homework/submission/{sub.id}/submit-solution", json={"solution_text": "x"},
            headers=_hdr(client, helper, auth_headers, csrf_headers),
        )

        assert resp.status_code == 200
        assert _status(sub.id) == "submitted"
