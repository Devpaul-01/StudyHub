"""
tests/integration/test_connections_crud_routes.py

routes/student/connections/crud.py has 25 endpoints and is the largest
untested route file in the codebase (2138 lines). The existing
integration suite (test_connections_flow.py) covers only
POST /connections/request/<id> (compatibility-score auto-accept
branching). This file covers the rest of the core connection lifecycle,
previously entirely untested at any level:

  - POST /connections/accept/<request_id>
  - POST /connections/reject/<request_id>
  - DELETE /connections/cancel/<request_id>
  - DELETE /connections/remove/<user_id>
  - GET /connections/status/<user_id>
  - POST /connections/settings
  - GET /connections/list
  - GET /connections/requests/received
  - GET /connections/requests/sent

All of these are student_bp routes behind token_required, so they DO
need csrf_headers for the mutating ones (POST/DELETE), unlike
routes/admin.py.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# POST /connections/accept/<request_id>
# ============================================================================

class TestAcceptConnection:
    def test_receiver_accepts_pending_request(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        conn = make_connection(requester, receiver, status="pending", responded_at=None)
        headers = {**auth_headers(receiver), **csrf_headers(client)}

        resp = client.post(f"/student/connections/accept/{conn.id}", headers=headers)

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["connected_user"]["id"] == requester.id

        from models import Connection
        refreshed = Connection.query.get(conn.id)
        assert refreshed.status == "accepted"
        assert refreshed.responded_at is not None

    def test_requester_cannot_accept_their_own_sent_request(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        conn = make_connection(requester, receiver, status="pending", responded_at=None)
        headers = {**auth_headers(requester), **csrf_headers(client)}

        resp = client.post(f"/student/connections/accept/{conn.id}", headers=headers)

        assert resp.status_code == 400
        assert "Not authorized" in resp.get_json()["message"]

    def test_already_accepted_request_cannot_be_accepted_again(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        conn = make_connection(requester, receiver, status="accepted")
        headers = {**auth_headers(receiver), **csrf_headers(client)}

        resp = client.post(f"/student/connections/accept/{conn.id}", headers=headers)

        assert resp.status_code == 400
        assert "not pending" in resp.get_json()["message"].lower()

    def test_nonexistent_request_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/connections/accept/999999", headers=headers)

        assert resp.status_code == 400
        assert "not found" in resp.get_json()["message"].lower()


# ============================================================================
# POST /connections/reject/<request_id>
# ============================================================================

class TestRejectConnection:
    def test_receiver_rejects_pending_request(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        conn = make_connection(requester, receiver, status="pending", responded_at=None)
        headers = {**auth_headers(receiver), **csrf_headers(client)}

        resp = client.post(f"/student/connections/reject/{conn.id}", headers=headers)

        assert resp.status_code == 200

        from models import Connection
        refreshed = Connection.query.get(conn.id)
        assert refreshed.status == "rejected"

    def test_non_receiver_cannot_reject(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        stranger = make_user()
        conn = make_connection(requester, receiver, status="pending", responded_at=None)
        headers = {**auth_headers(stranger), **csrf_headers(client)}

        resp = client.post(f"/student/connections/reject/{conn.id}", headers=headers)

        assert resp.status_code == 403


# ============================================================================
# DELETE /connections/cancel/<request_id>
# ============================================================================

class TestCancelConnectionRequest:
    def test_requester_cancels_their_own_pending_request(
        self, client, db_session, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        conn = make_connection(requester, receiver, status="pending", responded_at=None)
        conn_id = conn.id
        headers = {**auth_headers(requester), **csrf_headers(client)}

        resp = client.delete(f"/student/connections/cancel/{conn_id}", headers=headers)

        assert resp.status_code == 200

        from models import Connection
        assert Connection.query.get(conn_id) is None

    def test_receiver_cannot_cancel_a_request_sent_to_them(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        conn = make_connection(requester, receiver, status="pending", responded_at=None)
        headers = {**auth_headers(receiver), **csrf_headers(client)}

        resp = client.delete(f"/student/connections/cancel/{conn.id}", headers=headers)

        assert resp.status_code == 403

    def test_cannot_cancel_an_already_accepted_connection(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        conn = make_connection(requester, receiver, status="accepted")
        headers = {**auth_headers(requester), **csrf_headers(client)}

        resp = client.delete(f"/student/connections/cancel/{conn.id}", headers=headers)

        assert resp.status_code == 400


# ============================================================================
# DELETE /connections/remove/<user_id>
# ============================================================================

class TestRemoveConnection:
    def test_removes_an_accepted_connection_regardless_of_direction(
        self, client, db_session, make_user, make_connection, auth_headers, csrf_headers
    ):
        a = make_user()
        b = make_user()
        conn = make_connection(a, b, status="accepted")
        conn_id = conn.id
        headers = {**auth_headers(a), **csrf_headers(client)}

        resp = client.delete(f"/student/connections/remove/{b.id}", headers=headers)

        assert resp.status_code == 200

        from models import Connection
        assert Connection.query.get(conn_id) is None

    def test_removing_nonexistent_connection_returns_404(self, client, make_user, auth_headers, csrf_headers):
        a = make_user()
        b = make_user()
        headers = {**auth_headers(a), **csrf_headers(client)}

        resp = client.delete(f"/student/connections/remove/{b.id}", headers=headers)

        assert resp.status_code == 404

    def test_cannot_remove_a_still_pending_request_via_this_endpoint(
        self, client, make_user, make_connection, auth_headers, csrf_headers
    ):
        """This endpoint only matches status=='accepted' -- a pending
        request between the two users must not be found/removed here."""
        a = make_user()
        b = make_user()
        make_connection(a, b, status="pending", responded_at=None)
        headers = {**auth_headers(a), **csrf_headers(client)}

        resp = client.delete(f"/student/connections/remove/{b.id}", headers=headers)

        assert resp.status_code == 404


# ============================================================================
# GET /connections/status/<user_id>
# ============================================================================

class TestConnectionStatus:
    def test_self_returns_self_status(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(f"/student/connections/status/{user.id}", headers=auth_headers(user))

        body = resp.get_json()
        assert body["data"]["status"] == "self"
        assert body["data"]["can_connect"] is False

    def test_no_connection_returns_none_and_can_connect_true(self, client, make_user, auth_headers):
        a = make_user()
        b = make_user()

        resp = client.get(f"/student/connections/status/{b.id}", headers=auth_headers(a))

        body = resp.get_json()
        assert body["data"]["status"] == "none"
        assert body["data"]["can_connect"] is True

    def test_pending_sent_from_requesters_perspective(
        self, client, make_user, make_connection, auth_headers
    ):
        a = make_user()
        b = make_user()
        make_connection(a, b, status="pending", responded_at=None)

        resp = client.get(f"/student/connections/status/{b.id}", headers=auth_headers(a))

        assert resp.get_json()["data"]["status"] == "pending_sent"

    def test_pending_received_from_receivers_perspective(
        self, client, make_user, make_connection, auth_headers
    ):
        a = make_user()
        b = make_user()
        make_connection(a, b, status="pending", responded_at=None)

        resp = client.get(f"/student/connections/status/{a.id}", headers=auth_headers(b))

        assert resp.get_json()["data"]["status"] == "pending_received"

    def test_accepted_returns_connected_and_can_message_true(
        self, client, make_user, make_connection, auth_headers
    ):
        a = make_user()
        b = make_user()
        make_connection(a, b, status="accepted")

        resp = client.get(f"/student/connections/status/{b.id}", headers=auth_headers(a))

        body = resp.get_json()["data"]
        assert body["status"] == "connected"
        assert body["can_message"] is True


# ============================================================================
# POST /connections/settings
# ============================================================================

class TestConnectionsSettings:
    def test_updates_enable_sound_setting(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/connections/settings", json={"enable_sound": False}, headers=headers
        )

        assert resp.status_code == 200

        from models import User
        refreshed = User.query.get(user.id)
        assert refreshed.connection_settings.get("enable_sound") is False

    def test_defaults_enable_sound_to_true_when_not_provided(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/connections/settings", json={}, headers=headers)

        assert resp.status_code == 200
        from models import User
        refreshed = User.query.get(user.id)
        assert refreshed.connection_settings.get("enable_sound") is True


# ============================================================================
# GET /connections/list
# ============================================================================

class TestListConnections:
    def test_empty_when_no_accepted_connections(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/list", headers=auth_headers(user))

        body = resp.get_json()
        assert body["data"] == []
        assert body["total"] == 0

    def test_lists_accepted_connections_in_either_direction(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        friend_a = make_user(username="friend_a")  # me is requester
        friend_b = make_user(username="friend_b")  # me is receiver
        make_connection(me, friend_a, status="accepted")
        make_connection(friend_b, me, status="accepted")

        resp = client.get("/student/connections/list", headers=auth_headers(me))

        body = resp.get_json()
        assert body["total"] == 2
        returned_ids = {c["user"]["id"] for c in body["data"]}
        assert returned_ids == {friend_a.id, friend_b.id}

    def test_excludes_pending_and_rejected_connections(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        pending_user = make_user()
        rejected_user = make_user()
        make_connection(me, pending_user, status="pending", responded_at=None)
        make_connection(me, rejected_user, status="rejected")

        resp = client.get("/student/connections/list", headers=auth_headers(me))

        assert resp.get_json()["total"] == 0

    def test_includes_health_score_and_suggestion_fields(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        friend = make_user()
        make_connection(me, friend, status="accepted")

        resp = client.get("/student/connections/list", headers=auth_headers(me))

        entry = resp.get_json()["data"][0]
        assert "health_score" in entry
        assert 0 <= entry["health_score"] <= 100
        assert "suggestion" in entry


# ============================================================================
# GET /connections/requests/received
# ============================================================================

class TestReceivedConnectionRequests:
    def test_empty_when_no_pending_requests(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/requests/received", headers=auth_headers(user))

        body = resp.get_json()
        assert body["data"] == []
        assert body["total"] == 0

    def test_returns_pending_requests_sent_to_me(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        requester = make_user(username="the_requester")
        make_connection(requester, me, status="pending", responded_at=None)

        resp = client.get("/student/connections/requests/received", headers=auth_headers(me))

        body = resp.get_json()
        assert body["total"] == 1
        assert body["data"][0]["user"]["id"] == requester.id

    def test_excludes_requests_i_sent_to_others(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        other = make_user()
        make_connection(me, other, status="pending", responded_at=None)  # I'm the requester

        resp = client.get("/student/connections/requests/received", headers=auth_headers(me))

        assert resp.get_json()["total"] == 0

    def test_excludes_accepted_requests(self, client, make_user, make_connection, auth_headers):
        me = make_user()
        requester = make_user()
        make_connection(requester, me, status="accepted")

        resp = client.get("/student/connections/requests/received", headers=auth_headers(me))

        assert resp.get_json()["total"] == 0


# ============================================================================
# GET /connections/requests/sent
# ============================================================================

class TestSentConnectionRequests:
    def test_returns_pending_requests_i_sent(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        target = make_user(username="the_target")
        make_connection(me, target, status="pending", responded_at=None)

        resp = client.get("/student/connections/requests/sent", headers=auth_headers(me))

        body = resp.get_json()
        assert body["total"] == 1

    def test_excludes_requests_sent_to_me(self, client, make_user, make_connection, auth_headers):
        me = make_user()
        requester = make_user()
        make_connection(requester, me, status="pending", responded_at=None)  # they sent, not me

        resp = client.get("/student/connections/requests/sent", headers=auth_headers(me))

        assert resp.get_json()["total"] == 0
