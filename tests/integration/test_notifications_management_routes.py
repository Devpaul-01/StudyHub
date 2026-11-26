"""
tests/integration/test_notifications_management_routes.py

routes/student/notifications.py had partial coverage from
test_notifications_funnel.py (get_notifications, mark_all_notifications_read).
This batch covers the rest: mark_notification_read, delete_notification,
delete_all_notifications (with type/read_only filters), and the
notification settings GET/POST pair, plus the type-filter branch of
get_notifications not exercised by the funnel test.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET /profile/notifications/all -- type filter
# ============================================================================

class TestGetNotificationsTypeFilter:
    def test_type_filter_restricts_results(
        self, client, make_user, make_notification, auth_headers
    ):
        user = make_user(status="approved")
        make_notification(user, title="Badge", notification_type="badge_earned")
        make_notification(user, title="Comment", notification_type="comment")

        resp = client.get(
            "/student/profile/notifications/all?type=badges", headers=auth_headers(user)
        )

        assert resp.status_code == 200
        titles = [n["title"] for n in resp.get_json()["data"]["notifications"]]
        assert titles == ["Badge"]

    def test_unread_only_filter(
        self, client, make_user, make_notification, auth_headers
    ):
        user = make_user(status="approved")
        make_notification(user, title="Read one", is_read=True)
        make_notification(user, title="Unread one", is_read=False)

        resp = client.get(
            "/student/profile/notifications/all?unread_only=true", headers=auth_headers(user)
        )

        titles = [n["title"] for n in resp.get_json()["data"]["notifications"]]
        assert titles == ["Unread one"]

    def test_disabled_setting_does_not_hide_existing_history(
        self, client, db_session, make_user, make_notification, auth_headers
    ):
        """The setting stops NEW notifications (see test_notification_funnel_rules);
        what already exists stays visible and the unread count stays truthful."""
        user = make_user(status="approved")
        user.notification_settings = {"enable_notification": False}
        db_session.add(user)
        db_session.flush()
        make_notification(user, title="Earlier")

        resp = client.get("/student/profile/notifications/all", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert [n["title"] for n in data["notifications"]] == ["Earlier"]
        assert data["unread_count"] == 1
        assert data["enable_notification"] is False


# ============================================================================
# POST /profile/notifications/<id>/mark-read
# ============================================================================

class TestMarkNotificationRead:
    def test_nonexistent_notification_returns_error(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user(status="approved")

        resp = client.post(
            "/student/profile/notifications/999999/mark-read",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.get_json()["status"] == "error"

    def test_other_users_notification_not_found(
        self, client, make_user, make_notification, auth_headers, csrf_headers
    ):
        owner = make_user(status="approved")
        other = make_user(status="approved")
        notif = make_notification(owner, title="Private")

        resp = client.post(
            f"/student/profile/notifications/{notif.id}/mark-read",
            headers={**auth_headers(other), **csrf_headers(client)},
        )

        assert resp.get_json()["status"] == "error"

    def test_marks_unread_notification_as_read(
        self, client, db_session, make_user, make_notification, auth_headers, csrf_headers, fakeredis_client
    ):
        user = make_user(status="approved")
        notif = make_notification(user, title="Mark me", is_read=False)

        resp = client.post(
            f"/student/profile/notifications/{notif.id}/mark-read",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.status_code == 200
        db_session.refresh(notif)
        assert notif.is_read is True
        assert notif.read_at is not None

    def test_already_read_notification_short_circuits(
        self, client, make_user, make_notification, auth_headers, csrf_headers
    ):
        user = make_user(status="approved")
        notif = make_notification(user, title="Already read", is_read=True)

        resp = client.post(
            f"/student/profile/notifications/{notif.id}/mark-read",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.status_code == 200
        assert "already" in resp.get_json()["message"].lower()


# ============================================================================
# DELETE /profile/notifications/<id>
# ============================================================================

class TestDeleteNotification:
    def test_nonexistent_notification_returns_error(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user(status="approved")

        resp = client.delete(
            "/student/profile/notifications/999999",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.get_json()["status"] == "error"

    def test_deletes_own_notification(
        self, client, db_session, make_user, make_notification, auth_headers, csrf_headers, fakeredis_client
    ):
        from models import Notification

        user = make_user(status="approved")
        notif = make_notification(user, title="Delete me")
        notif_id = notif.id

        resp = client.delete(
            f"/student/profile/notifications/{notif_id}",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.status_code == 200
        assert db_session.get(Notification, notif_id) is None

    def test_cannot_delete_other_users_notification(
        self, client, db_session, make_user, make_notification, auth_headers, csrf_headers
    ):
        from models import Notification

        owner = make_user(status="approved")
        other = make_user(status="approved")
        notif = make_notification(owner, title="Not yours")

        resp = client.delete(
            f"/student/profile/notifications/{notif.id}",
            headers={**auth_headers(other), **csrf_headers(client)},
        )

        assert resp.get_json()["status"] == "error"
        assert db_session.get(Notification, notif.id) is not None


# ============================================================================
# DELETE /profile/notifications/delete-all
# ============================================================================

class TestDeleteAllNotifications:
    def test_deletes_all_for_user_only(
        self, client, db_session, make_user, make_notification, auth_headers, csrf_headers, fakeredis_client
    ):
        from models import Notification

        user = make_user(status="approved")
        other = make_user(status="approved")
        make_notification(user, title="Mine 1")
        make_notification(user, title="Mine 2")
        make_notification(other, title="Not mine")

        resp = client.delete(
            "/student/profile/notifications/delete-all",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.status_code == 200
        assert resp.get_json()["data"]["deleted_count"] == 2
        assert Notification.query.filter_by(user_id=user.id).count() == 0
        assert Notification.query.filter_by(user_id=other.id).count() == 1

    def test_read_only_filter_leaves_unread_intact(
        self, client, db_session, make_user, make_notification, auth_headers, csrf_headers
    ):
        from models import Notification

        user = make_user(status="approved")
        make_notification(user, title="Read", is_read=True)
        make_notification(user, title="Unread", is_read=False)

        resp = client.delete(
            "/student/profile/notifications/delete-all?read_only=true",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.get_json()["data"]["deleted_count"] == 1
        remaining = Notification.query.filter_by(user_id=user.id).all()
        assert len(remaining) == 1
        assert remaining[0].title == "Unread"

    def test_type_filter_restricts_deletion(
        self, client, db_session, make_user, make_notification, auth_headers, csrf_headers
    ):
        from models import Notification

        user = make_user(status="approved")
        make_notification(user, title="Badge", notification_type="badge_earned")
        make_notification(user, title="Comment", notification_type="comment")

        resp = client.delete(
            "/student/profile/notifications/delete-all?type=badges",
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.get_json()["data"]["deleted_count"] == 1
        remaining = Notification.query.filter_by(user_id=user.id).all()
        assert len(remaining) == 1
        assert remaining[0].title == "Comment"


# ============================================================================
# GET/POST /profile/notifications/settings
# ============================================================================

class TestNotificationSettings:
    def test_get_settings_endpoint_was_removed(self, client, make_user, auth_headers):
        """Nothing called it and it advertised unimplemented options (NOTIF-20).
        The two live settings come back from GET /all."""
        user = make_user(status="approved")
        resp = client.get("/student/profile/notifications/settings", headers=auth_headers(user))
        assert resp.status_code == 405

    def test_all_returns_the_two_live_settings(self, client, make_user, auth_headers):
        user = make_user(status="approved")
        data = client.get("/student/profile/notifications/all", headers=auth_headers(user)).get_json()["data"]
        assert data["enable_notification"] is True
        assert data["enable_notification_sound"] is True
        for dead in ("notification_category", "send_email_notification", "email_frequency"):
            assert dead not in data

    def test_post_toggles_setting(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user(status="approved")

        resp = client.post(
            "/student/profile/notifications/settings",
            json={"setting": "enable_notification_sound"},
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["previous_value"] is True
        assert data["new_value"] is False

        db_session.refresh(user)
        assert user.notification_settings["enable_notification_sound"] is False

    def test_post_toggle_twice_returns_to_original(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user(status="approved")
        headers = {**auth_headers(user), **csrf_headers(client)}

        client.post(
            "/student/profile/notifications/settings",
            json={"setting": "enable_notification"}, headers=headers,
        )
        resp = client.post(
            "/student/profile/notifications/settings",
            json={"setting": "enable_notification"}, headers=headers,
        )

        assert resp.get_json()["data"]["new_value"] is True

    def test_invalid_setting_name_rejected(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user(status="approved")

        resp = client.post(
            "/student/profile/notifications/settings",
            json={"setting": "not_a_real_setting"},
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.get_json()["status"] == "error"

    def test_missing_setting_name_rejected(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user(status="approved")

        resp = client.post(
            "/student/profile/notifications/settings",
            json={},
            headers={**auth_headers(user), **csrf_headers(client)},
        )

        assert resp.get_json()["status"] == "error"


class TestExplicitSettingValue:
    """Audit NOTIF-19: the client states the value it wants, so retries and
    double submits are idempotent instead of flipping the setting back."""

    URL = "/student/profile/notifications/settings"

    def test_explicit_value_is_idempotent(self, client, db_session, make_user, auth_headers, csrf_headers):
        user = make_user(status="approved")
        headers = {**auth_headers(user), **csrf_headers(client)}
        results = [
            client.post(self.URL, json={"setting": "enable_notification_sound", "value": False}, headers=headers)
            .get_json()["data"]["new_value"]
            for _ in range(3)
        ]
        assert results == [False, False, False]
        db_session.refresh(user)
        assert user.notification_settings["enable_notification_sound"] is False

    def test_explicit_true_after_false(self, client, make_user, auth_headers, csrf_headers):
        user = make_user(status="approved")
        headers = {**auth_headers(user), **csrf_headers(client)}
        client.post(self.URL, json={"setting": "enable_notification", "value": False}, headers=headers)
        resp = client.post(self.URL, json={"setting": "enable_notification", "value": True}, headers=headers)
        assert resp.get_json()["data"]["new_value"] is True

    @pytest.mark.parametrize("bad", ["false", 0, 1, None, [], {}])
    def test_non_boolean_value_rejected(self, client, make_user, auth_headers, csrf_headers, bad):
        user = make_user(status="approved")
        resp = client.post(
            self.URL, json={"setting": "enable_notification", "value": bad},
            headers={**auth_headers(user), **csrf_headers(client)},
        )
        assert resp.status_code == 400

    def test_changing_one_key_keeps_the_other(self, client, db_session, make_user, auth_headers, csrf_headers):
        user = make_user(status="approved")
        headers = {**auth_headers(user), **csrf_headers(client)}
        client.post(self.URL, json={"setting": "enable_notification", "value": False}, headers=headers)
        client.post(self.URL, json={"setting": "enable_notification_sound", "value": False}, headers=headers)
        db_session.refresh(user)
        assert user.notification_settings["enable_notification"] is False
        assert user.notification_settings["enable_notification_sound"] is False
