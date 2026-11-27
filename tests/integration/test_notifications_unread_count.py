"""GET /profile/notifications/unread-count (audit NOTIF-07)."""
import pytest

pytestmark = pytest.mark.integration

URL = "/student/profile/notifications/unread-count"


def test_counts_only_the_callers_unread_rows(client, make_user, make_notification, auth_headers):
    me, other = make_user(status="approved"), make_user(status="approved")
    for _ in range(3):
        make_notification(me, is_read=False)
    make_notification(me, is_read=True)
    make_notification(other, is_read=False)

    resp = client.get(URL, headers=auth_headers(me))

    assert resp.status_code == 200
    assert resp.get_json()["data"]["unread_count"] == 3


def test_count_is_not_capped_by_the_list_limit(client, make_user, make_notification, auth_headers):
    me = make_user(status="approved")
    for i in range(505):
        make_notification(me, title=f"n{i}", is_read=False)
    resp = client.get(URL, headers=auth_headers(me))
    assert resp.get_json()["data"]["unread_count"] == 505


def test_payload_is_tiny(client, make_user, make_notification, auth_headers):
    me = make_user(status="approved")
    for i in range(50):
        make_notification(me, title=f"n{i}", body="x" * 80)
    assert len(client.get(URL, headers=auth_headers(me)).data) < 200


def test_requires_authentication(client):
    assert client.get(URL).status_code in (401, 403)


@pytest.mark.parametrize("settings, expected", [
    (None, True), ({}, True), ({"enable_notification_sound": True}, True), ({"enable_notification_sound": False}, False),
])
def test_reports_the_sound_preference(client, db_session, make_user, auth_headers, settings, expected):
    """The client needs this at startup to decide whether to chime for live pushes."""
    me = make_user(status="approved")
    me.notification_settings = settings
    db_session.add(me)
    db_session.flush()
    data = client.get(URL, headers=auth_headers(me)).get_json()["data"]
    assert data["enable_notification_sound"] is expected
