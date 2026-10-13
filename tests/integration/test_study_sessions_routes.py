"""
tests/integration/test_study_sessions_routes.py

routes/student/study_sessions.py has 25 endpoints and previously had
zero tests at any level. This file covers the core scheduling
lifecycle built around StudySessionCalendar: request -> confirm (or
decline) -> cancel, plus the upcoming/all listing endpoints and the
templates endpoint. The live-session (Pomodoro, goals, AI ask,
resources) endpoints are a separate, more complex surface -- out of
scope for this batch.

All WebSocket emits in study_sessions.py go through a module-local
_emit() helper wrapped in try/except specifically so a missing/
unconfigured SocketIO instance never fails the HTTP request (see that
helper's own docstring) -- these tests rely on that documented
behavior rather than mocking sockets.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


def _iso(dt):
    return dt.isoformat()


# ============================================================================
# GET /study-session/templates
# ============================================================================

class TestSessionTemplates:
    def test_returns_templates_list(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/study-session/templates", headers=auth_headers(user))

        assert resp.status_code == 200
        assert "templates" in resp.get_json()["data"]


# ============================================================================
# POST /study-session/request
# ============================================================================

class TestRequestStudySession:
    def test_requires_connection_to_request_session(
        self, client, make_user, auth_headers, csrf_headers
    ):
        requester = make_user()
        stranger = make_user()
        headers = {**auth_headers(requester), **csrf_headers(client)}
        proposed = _iso(datetime.datetime.utcnow() + datetime.timedelta(days=1))

        resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": stranger.id, "title": "Calc study", "proposed_times": [proposed]},
            headers=headers,
        )

        assert resp.status_code == 403
        assert "Must be connected" in resp.get_json()["message"]

    def test_connected_user_can_request_session(
        self, client, db_session, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        headers = {**auth_headers(requester), **csrf_headers(client)}
        proposed = _iso(datetime.datetime.utcnow() + datetime.timedelta(days=1))

        resp = client.post(
            "/student/study-session/request",
            json={
                "receiver_id": receiver.id,
                "title": "Calc study session",
                "subject": "Calculus",
                "proposed_times": [proposed],
            },
            headers=headers,
        )

        assert resp.status_code == 201
        session_id = resp.get_json()["data"]["session_id"]

        from models import StudySessionCalendar
        row = StudySessionCalendar.query.get(session_id)
        assert row.requester_id == requester.id
        assert row.receiver_id == receiver.id
        assert row.status == "pending"

    def test_missing_receiver_id_returns_error(self, client, make_user, auth_headers, csrf_headers):
        requester = make_user()
        headers = {**auth_headers(requester), **csrf_headers(client)}

        resp = client.post(
            "/student/study-session/request",
            json={"title": "X", "proposed_times": [_iso(datetime.datetime.utcnow())]},
            headers=headers,
        )

        assert resp.status_code == 400
        assert "receiver_id required" in resp.get_json()["message"]

    def test_empty_proposed_times_returns_error(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        headers = {**auth_headers(requester), **csrf_headers(client)}

        resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "proposed_times": []},
            headers=headers,
        )

        assert resp.status_code == 400
        assert "At least one proposed time" in resp.get_json()["message"]

    def test_also_creates_a_notification_message(
        self, client, db_session, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        headers = {**auth_headers(requester), **csrf_headers(client)}
        proposed = _iso(datetime.datetime.utcnow() + datetime.timedelta(days=1))

        resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [proposed]},
            headers=headers,
        )

        message_id = resp.get_json()["data"]["message_id"]
        from models import Message
        msg = Message.query.get(message_id)
        assert msg is not None
        assert msg.receiver_id == receiver.id


# ============================================================================
# POST /study-session/<id>/confirm
# ============================================================================

class TestConfirmStudySession:
    def test_receiver_confirms_with_a_proposed_time(
        self, client, db_session, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        proposed_dt = datetime.datetime.utcnow().replace(second=0, microsecond=0) + datetime.timedelta(days=2)

        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        create_resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [_iso(proposed_dt)]},
            headers=req_headers,
        )
        session_id = create_resp.get_json()["data"]["session_id"]

        confirm_headers = {**auth_headers(receiver), **csrf_headers(client)}
        resp = client.post(
            f"/student/study-session/{session_id}/confirm",
            json={"confirmed_time": _iso(proposed_dt)},
            headers=confirm_headers,
        )

        assert resp.status_code == 200
        from models import StudySessionCalendar
        row = StudySessionCalendar.query.get(session_id)
        assert row.status == "confirmed"
        assert row.confirmed_at is not None

    def test_requester_cannot_confirm_their_own_request(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        proposed_dt = datetime.datetime.utcnow() + datetime.timedelta(days=1)

        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        create_resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [_iso(proposed_dt)]},
            headers=req_headers,
        )
        session_id = create_resp.get_json()["data"]["session_id"]

        resp = client.post(
            f"/student/study-session/{session_id}/confirm",
            json={"confirmed_time": _iso(proposed_dt)},
            headers=req_headers,
        )

        assert resp.status_code == 403

    def test_confirmed_time_not_among_proposed_rejected(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        proposed_dt = datetime.datetime.utcnow() + datetime.timedelta(days=1)
        other_dt = datetime.datetime.utcnow() + datetime.timedelta(days=5)

        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        create_resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [_iso(proposed_dt)]},
            headers=req_headers,
        )
        session_id = create_resp.get_json()["data"]["session_id"]

        confirm_headers = {**auth_headers(receiver), **csrf_headers(client)}
        resp = client.post(
            f"/student/study-session/{session_id}/confirm",
            json={"confirmed_time": _iso(other_dt)},
            headers=confirm_headers,
        )

        assert resp.status_code == 400
        assert "must be one of the proposed times" in resp.get_json()["message"]

    def test_missing_confirmed_time_returns_error(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        proposed_dt = datetime.datetime.utcnow() + datetime.timedelta(days=1)

        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        create_resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [_iso(proposed_dt)]},
            headers=req_headers,
        )
        session_id = create_resp.get_json()["data"]["session_id"]

        confirm_headers = {**auth_headers(receiver), **csrf_headers(client)}
        resp = client.post(
            f"/student/study-session/{session_id}/confirm", json={}, headers=confirm_headers
        )

        assert resp.status_code == 400

    def test_nonexistent_session_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/study-session/999999/confirm",
            json={"confirmed_time": _iso(datetime.datetime.utcnow())},
            headers=headers,
        )

        assert resp.status_code == 404


# ============================================================================
# POST /study-session/<id>/decline
# ============================================================================

class TestDeclineStudySession:
    def test_receiver_declines_pending_request(
        self, client, db_session, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        proposed_dt = datetime.datetime.utcnow() + datetime.timedelta(days=1)

        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        create_resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [_iso(proposed_dt)]},
            headers=req_headers,
        )
        session_id = create_resp.get_json()["data"]["session_id"]

        decline_headers = {**auth_headers(receiver), **csrf_headers(client)}
        resp = client.post(
            f"/student/study-session/{session_id}/decline",
            json={"reason": "Can't make it"},
            headers=decline_headers,
        )

        assert resp.status_code == 200
        from models import StudySessionCalendar
        row = StudySessionCalendar.query.get(session_id)
        assert row.status == "declined"
        assert row.decline_reason == "Can't make it"

    def test_requester_cannot_decline(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        proposed_dt = datetime.datetime.utcnow() + datetime.timedelta(days=1)

        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        create_resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [_iso(proposed_dt)]},
            headers=req_headers,
        )
        session_id = create_resp.get_json()["data"]["session_id"]

        resp = client.post(
            f"/student/study-session/{session_id}/decline", json={}, headers=req_headers
        )

        assert resp.status_code == 403


# ============================================================================
# POST /study-session/<id>/cancel
# ============================================================================

class TestCancelStudySession:
    def test_requester_cancels_pending_session(
        self, client, db_session, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        proposed_dt = datetime.datetime.utcnow() + datetime.timedelta(days=1)

        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        create_resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [_iso(proposed_dt)]},
            headers=req_headers,
        )
        session_id = create_resp.get_json()["data"]["session_id"]

        resp = client.post(
            f"/student/study-session/{session_id}/cancel",
            json={"reason": "Schedule conflict"},
            headers=req_headers,
        )

        assert resp.status_code == 200
        from models import StudySessionCalendar
        row = StudySessionCalendar.query.get(session_id)
        assert row.status == "cancelled"
        assert row.cancelled_by == requester.id

    def test_receiver_cannot_cancel(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        proposed_dt = datetime.datetime.utcnow() + datetime.timedelta(days=1)

        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        create_resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [_iso(proposed_dt)]},
            headers=req_headers,
        )
        session_id = create_resp.get_json()["data"]["session_id"]

        recv_headers = {**auth_headers(receiver), **csrf_headers(client)}
        resp = client.post(
            f"/student/study-session/{session_id}/cancel", json={}, headers=recv_headers
        )

        assert resp.status_code == 403

    def test_cannot_cancel_already_cancelled_session(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        proposed_dt = datetime.datetime.utcnow() + datetime.timedelta(days=1)

        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        create_resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [_iso(proposed_dt)]},
            headers=req_headers,
        )
        session_id = create_resp.get_json()["data"]["session_id"]
        client.post(f"/student/study-session/{session_id}/cancel", json={}, headers=req_headers)

        resp = client.post(f"/student/study-session/{session_id}/cancel", json={}, headers=req_headers)

        assert resp.status_code == 400


# ============================================================================
# GET /study-session/upcoming
# ============================================================================

class TestUpcomingStudySessions:
    def test_empty_when_no_confirmed_sessions(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/study-session/upcoming", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["upcoming_sessions"] == []
        assert data["total"] == 0

    def test_includes_confirmed_future_session_for_both_participants(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        proposed_dt = datetime.datetime.utcnow().replace(second=0, microsecond=0) + datetime.timedelta(days=1)

        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        create_resp = client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [_iso(proposed_dt)]},
            headers=req_headers,
        )
        session_id = create_resp.get_json()["data"]["session_id"]

        confirm_headers = {**auth_headers(receiver), **csrf_headers(client)}
        client.post(
            f"/student/study-session/{session_id}/confirm",
            json={"confirmed_time": _iso(proposed_dt)},
            headers=confirm_headers,
        )

        for viewer, headers in [(requester, req_headers), (receiver, confirm_headers)]:
            resp = client.get("/student/study-session/upcoming", headers=headers)
            data = resp.get_json()["data"]
            assert data["total"] == 1
            assert data["upcoming_sessions"][0]["id"] == session_id

    def test_excludes_pending_unconfirmed_sessions(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        make_connection(requester, receiver, status="accepted")
        proposed_dt = datetime.datetime.utcnow() + datetime.timedelta(days=1)

        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        client.post(
            "/student/study-session/request",
            json={"receiver_id": receiver.id, "title": "Study", "proposed_times": [_iso(proposed_dt)]},
            headers=req_headers,
        )

        resp = client.get("/student/study-session/upcoming", headers=req_headers)

        assert resp.get_json()["data"]["upcoming_sessions"] == []
