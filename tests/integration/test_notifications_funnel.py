"""
tests/integration/test_notifications_funnel.py

Covers INTEGRATION_TEST_IMPLEMENTATION_PLAN.md §3.6 — one well-chosen
integration test on services/notification_service.py::notify()'s funnel,
called from a REAL route, rather than duplicating coverage across the
~15 call sites that all funnel through it.

The unread count is an indexed COUNT(*), not a Redis counter, so these tests
assert what is actually stored and returned (audit NOTIF-07/08).
"""

import pytest

pytestmark = pytest.mark.integration


class TestNotificationFunnel:
    def test_connection_request_creates_notification(
        self, client, make_user, auth_headers, csrf_headers, fakeredis_client
    ):
        from models import Notification

        requester = make_user(status="approved")
        target = make_user(status="approved")

        headers = {**auth_headers(requester), **csrf_headers(client)}
        resp = client.post(f"/student/connections/request/{target.id}", headers=headers)
        assert resp.status_code in (200, 201)

        # "connection_request" on the pending branch, "instant_connection" if the
        # fixture combination auto-accepts: exactly one of them must exist.
        types = sorted(
            n.notification_type for n in Notification.query.filter_by(user_id=target.id).all()
        )
        assert types in (["connection_request"], ["instant_connection"])

        # No Redis counter is written any more: the count comes from the rows.
        assert not [k for k in fakeredis_client.keys("*") if "notif:unread" in k]

    def test_get_notifications_route_returns_created_notification(
        self, client, make_user, make_notification, auth_headers
    ):
        user = make_user(status="approved")
        make_notification(user, title="Hello", notification_type="test")

        headers = auth_headers(user)
        resp = client.get("/student/profile/notifications/all", headers=headers)
        assert resp.status_code == 200
        body = resp.get_json()
        titles = [n["title"] for n in body["data"]["notifications"]]
        assert "Hello" in titles

    def test_mark_all_read_marks_every_row_and_unread_count_reaches_zero(
        self, client, make_user, make_notification, auth_headers, csrf_headers
    ):
        from models import Notification

        user = make_user(status="approved")
        for i in range(3):
            make_notification(user, title=f"N{i}", is_read=False)

        headers = {**auth_headers(user), **csrf_headers(client)}
        resp = client.post("/student/profile/notifications/mark-all-read", headers=headers)
        assert resp.status_code == 200
        assert resp.get_json()["data"]["marked_count"] == 3

        assert Notification.query.filter_by(user_id=user.id, is_read=False).count() == 0
        listing = client.get("/student/profile/notifications/all", headers=auth_headers(user))
        assert listing.get_json()["data"]["unread_count"] == 0
