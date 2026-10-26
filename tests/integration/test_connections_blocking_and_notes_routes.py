"""
tests/integration/test_connections_blocking_and_notes_routes.py

routes/student/connections/{blocking.py,health.py} had zero tests at
any level. This batch covers all of blocking.py's 4 endpoints (block,
unblock, and both blocked-list variants) plus health.py's connection-
notes pair (GET/PUT notes). health.py's get_connection_details and the
online-connections endpoints (which pull in compatibility scoring and
presence/online-status data) are a separate, more involved area left
for a future pass.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# POST /connections/block/<id> and /connections/unblock/<id>
# ============================================================================

class TestBlockUnblock:
    def test_block_self_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(f"/student/connections/block/{user.id}", headers=headers)

        assert resp.status_code == 400

    def test_block_nonexistent_user_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/connections/block/999999", headers=headers)

        assert resp.status_code == 400

    def test_block_creates_a_blocked_connection(self, client, make_user, auth_headers, csrf_headers):
        me = make_user()
        other = make_user()
        headers = {**auth_headers(me), **csrf_headers(client)}

        resp = client.post(f"/student/connections/block/{other.id}", headers=headers)

        assert resp.status_code == 200
        from models import Connection
        conn = Connection.query.filter_by(blocked_by_id=me.id).first()
        assert conn is not None
        assert conn.status == "blocked"

    def test_unblock_removes_the_connection(self, client, make_user, auth_headers, csrf_headers):
        me = make_user()
        other = make_user()
        headers = {**auth_headers(me), **csrf_headers(client)}
        client.post(f"/student/connections/block/{other.id}", headers=headers)

        resp = client.post(f"/student/connections/unblock/{other.id}", headers=headers)

        assert resp.status_code == 200
        from models import Connection
        assert Connection.query.filter_by(blocked_by_id=me.id).first() is None

    def test_unblock_by_non_blocker_forbidden(self, client, make_user, auth_headers, csrf_headers):
        me = make_user()
        other = make_user()
        headers = {**auth_headers(me), **csrf_headers(client)}
        client.post(f"/student/connections/block/{other.id}", headers=headers)
        other_headers = {**auth_headers(other), **csrf_headers(client)}

        resp = client.post(f"/student/connections/unblock/{me.id}", headers=other_headers)

        assert resp.status_code == 403

    def test_unblock_when_not_blocked_returns_404(self, client, make_user, auth_headers, csrf_headers):
        me = make_user()
        other = make_user()
        headers = {**auth_headers(me), **csrf_headers(client)}

        resp = client.post(f"/student/connections/unblock/{other.id}", headers=headers)

        assert resp.status_code == 404


# ============================================================================
# GET /connections/blocked and /connections/blocked/list
# ============================================================================

class TestListBlockedUsers:
    def test_blocked_list_empty_initially(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/blocked", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["blocked_users"] == []

    def test_blocked_list_includes_blocked_user(self, client, make_user, auth_headers, csrf_headers):
        me = make_user()
        other = make_user()
        headers = {**auth_headers(me), **csrf_headers(client)}
        client.post(f"/student/connections/block/{other.id}", headers=headers)

        resp = client.get("/student/connections/blocked", headers=auth_headers(me))

        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["blocked_users"][0]["id"] == other.id

    def test_blocked_list_detailed_matches_blocked_list(
        self, client, make_user, auth_headers, csrf_headers
    ):
        me = make_user()
        other = make_user()
        headers = {**auth_headers(me), **csrf_headers(client)}
        client.post(f"/student/connections/block/{other.id}", headers=headers)

        resp = client.get("/student/connections/blocked/list", headers=auth_headers(me))

        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["blocked_users"][0]["id"] == other.id
        assert "connection_id" in data["blocked_users"][0]

    def test_blocking_does_not_affect_the_other_users_list(
        self, client, make_user, auth_headers, csrf_headers
    ):
        me = make_user()
        other = make_user()
        headers = {**auth_headers(me), **csrf_headers(client)}
        client.post(f"/student/connections/block/{other.id}", headers=headers)

        resp = client.get("/student/connections/blocked", headers=auth_headers(other))

        assert resp.get_json()["data"]["blocked_users"] == []


# ============================================================================
# GET /connections/<id>/notes and PUT /connections/<id>/notes/update
# ============================================================================

class TestConnectionNotes:
    def test_requester_gets_their_own_notes(self, client, db_session, make_user, auth_headers):
        requester = make_user()
        receiver = make_user()
        from models import Connection
        conn = Connection(
            requester_id=requester.id, receiver_id=receiver.id,
            status="accepted", requester_notes="met at orientation",
        )
        db_session.add(conn)
        db_session.flush()

        resp = client.get(f"/student/connections/{conn.id}/notes", headers=auth_headers(requester))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["notes"] == "met at orientation"
        assert data["is_requester"] is True
        assert data["partner"]["id"] == receiver.id

    def test_receiver_gets_receiver_notes_not_requester_notes(
        self, client, db_session, make_user, auth_headers
    ):
        requester = make_user()
        receiver = make_user()
        from models import Connection
        conn = Connection(
            requester_id=requester.id, receiver_id=receiver.id, status="accepted",
            requester_notes="requester's note", receiver_notes="receiver's note",
        )
        db_session.add(conn)
        db_session.flush()

        resp = client.get(f"/student/connections/{conn.id}/notes", headers=auth_headers(receiver))

        data = resp.get_json()["data"]
        assert data["notes"] == "receiver's note"
        assert data["is_requester"] is False

    def test_unrelated_user_forbidden(self, client, db_session, make_user, auth_headers):
        requester = make_user()
        receiver = make_user()
        outsider = make_user()
        from models import Connection
        conn = Connection(requester_id=requester.id, receiver_id=receiver.id, status="accepted")
        db_session.add(conn)
        db_session.flush()

        resp = client.get(f"/student/connections/{conn.id}/notes", headers=auth_headers(outsider))

        assert resp.status_code == 403

    def test_nonexistent_connection_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/999999/notes", headers=auth_headers(user))

        assert resp.status_code == 404

    def test_update_notes_as_requester(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        from models import Connection
        conn = Connection(requester_id=requester.id, receiver_id=receiver.id, status="accepted")
        db_session.add(conn)
        db_session.flush()
        headers = {**auth_headers(requester), **csrf_headers(client)}

        resp = client.put(
            f"/student/connections/{conn.id}/notes/update",
            json={"notes": "Great study partner"}, headers=headers,
        )

        assert resp.status_code == 200
        assert Connection.query.get(conn.id).requester_notes == "Great study partner"
        assert Connection.query.get(conn.id).receiver_notes is None

    def test_update_notes_too_long_rejected(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        from models import Connection
        conn = Connection(requester_id=requester.id, receiver_id=receiver.id, status="accepted")
        db_session.add(conn)
        db_session.flush()
        headers = {**auth_headers(requester), **csrf_headers(client)}

        resp = client.put(
            f"/student/connections/{conn.id}/notes/update",
            json={"notes": "x" * 501}, headers=headers,
        )

        assert resp.status_code == 400

    def test_update_notes_unrelated_user_forbidden(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        requester = make_user()
        receiver = make_user()
        outsider = make_user()
        from models import Connection
        conn = Connection(requester_id=requester.id, receiver_id=receiver.id, status="accepted")
        db_session.add(conn)
        db_session.flush()
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.put(
            f"/student/connections/{conn.id}/notes/update",
            json={"notes": "sneaky"}, headers=headers,
        )

        assert resp.status_code == 403
