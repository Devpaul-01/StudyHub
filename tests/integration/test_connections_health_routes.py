"""
tests/integration/test_connections_health_routes.py

routes/student/connections/health.py's detail/online-listing endpoints
previously had zero tests (the notes GET/update pair was already
covered in test_connections_blocking_and_notes_routes.py). This batch
covers get_connection_details, get_online_connections,
get_online_connections_count, and get_online_connections_by_department.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET /connections/<id>/details
# ============================================================================

class TestGetConnectionDetails:
    def test_nonexistent_connection_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/999999/details", headers=auth_headers(user))

        assert resp.status_code == 404

    def test_unrelated_user_forbidden(
        self, client, make_user, make_connection, auth_headers
    ):
        requester = make_user()
        receiver = make_user()
        outsider = make_user()
        conn = make_connection(requester, receiver, status="accepted")

        resp = client.get(
            f"/student/connections/{conn.id}/details", headers=auth_headers(outsider)
        )

        assert resp.status_code == 403

    def test_requester_sees_own_notes_and_partner_info(
        self, client, make_user, make_connection, auth_headers
    ):
        requester = make_user()
        receiver = make_user()
        conn = make_connection(
            requester, receiver, status="accepted", requester_notes="great partner"
        )

        resp = client.get(
            f"/student/connections/{conn.id}/details", headers=auth_headers(requester)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["connection"]["notes"] == "great partner"
        assert data["connection"]["is_requester"] is True
        assert data["partner"]["id"] == receiver.id
        assert data["mutual_connections"]["count"] == 0
        assert data["shared_threads"]["count"] == 0
        assert "health" in data
        assert data["study_history"]["sessions"] == []

    def test_includes_message_interaction_metrics(
        self, client, db_session, make_user, make_connection, make_message, auth_headers
    ):
        requester = make_user()
        receiver = make_user()
        conn = make_connection(requester, receiver, status="accepted")
        make_message(requester, receiver, body="hi there")

        resp = client.get(
            f"/student/connections/{conn.id}/details", headers=auth_headers(requester)
        )

        data = resp.get_json()["data"]
        assert data["interaction_metrics"]["total_messages"] == 1
        assert data["interaction_metrics"]["messages_sent"] == 1
        assert data["interaction_metrics"]["last_message"]["from_me"] is True

    def test_mutual_connection_is_counted(
        self, client, make_user, make_connection, auth_headers
    ):
        requester = make_user()
        receiver = make_user()
        shared = make_user()
        conn = make_connection(requester, receiver, status="accepted")
        make_connection(requester, shared, status="accepted")
        make_connection(receiver, shared, status="accepted")

        resp = client.get(
            f"/student/connections/{conn.id}/details", headers=auth_headers(requester)
        )

        data = resp.get_json()["data"]
        assert data["mutual_connections"]["count"] == 1
        assert data["mutual_connections"]["sample"][0]["id"] == shared.id


# ============================================================================
# GET /connections/online
# ============================================================================

class TestGetOnlineConnections:
    def test_no_connections_returns_empty(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/online", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"] == []
        assert resp.get_json()["total"] == 0

    def test_recently_active_connection_is_listed(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        other = make_user(last_active=datetime.datetime.utcnow())
        make_connection(me, other, status="accepted")

        resp = client.get("/student/connections/online", headers=auth_headers(me))

        assert resp.status_code == 200
        body = resp.get_json()
        ids = [c["user"]["id"] for c in body["data"]]
        assert other.id in ids
        assert body["data"][0]["user"]["is_online"] is True

    def test_stale_connection_not_listed(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        other = make_user(
            last_active=datetime.datetime.utcnow() - datetime.timedelta(days=5)
        )
        make_connection(me, other, status="accepted")

        resp = client.get("/student/connections/online", headers=auth_headers(me))

        assert resp.get_json()["data"] == []

    def test_time_window_param_widens_the_cutoff(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        other = make_user(
            last_active=datetime.datetime.utcnow() - datetime.timedelta(minutes=90)
        )
        make_connection(me, other, status="accepted")

        resp = client.get(
            "/student/connections/online?time_window=120", headers=auth_headers(me)
        )

        ids = [c["user"]["id"] for c in resp.get_json()["data"]]
        assert other.id in ids


# ============================================================================
# GET /connections/online/count
# ============================================================================

class TestGetOnlineConnectionsCount:
    def test_no_connections_returns_zero(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/online/count", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["count"] == 0

    def test_counts_only_online_connections(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        online_friend = make_user(last_active=datetime.datetime.utcnow())
        offline_friend = make_user(
            last_active=datetime.datetime.utcnow() - datetime.timedelta(days=3)
        )
        make_connection(me, online_friend, status="accepted")
        make_connection(me, offline_friend, status="accepted")

        resp = client.get("/student/connections/online/count", headers=auth_headers(me))

        data = resp.get_json()["data"]
        assert data["count"] == 1
        assert data["total_connections"] == 2


# ============================================================================
# GET /connections/online/department
# ============================================================================

class TestGetOnlineConnectionsByDepartment:
    def test_no_department_set_returns_message(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/connections/online/department", headers=auth_headers(user)
        )

        assert resp.status_code == 200
        assert resp.get_json()["data"] == []

    def test_same_department_online_connection_is_listed(
        self, client, make_user, make_student_profile, make_connection, auth_headers
    ):
        me = make_user()
        make_student_profile(me, department="CS")
        other = make_user(last_active=datetime.datetime.utcnow())
        make_student_profile(other, department="CS")
        make_connection(me, other, status="accepted")

        resp = client.get(
            "/student/connections/online/department", headers=auth_headers(me)
        )

        body = resp.get_json()
        ids = [c["user"]["id"] for c in body["data"]]
        assert other.id in ids
        assert body["data"][0]["same_department"] is True

    def test_different_department_online_connection_excluded(
        self, client, make_user, make_student_profile, make_connection, auth_headers
    ):
        me = make_user()
        make_student_profile(me, department="CS")
        other = make_user(last_active=datetime.datetime.utcnow())
        make_student_profile(other, department="Physics")
        make_connection(me, other, status="accepted")

        resp = client.get(
            "/student/connections/online/department", headers=auth_headers(me)
        )

        ids = [c["user"]["id"] for c in resp.get_json()["data"]]
        assert other.id not in ids
