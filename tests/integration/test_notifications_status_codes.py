"""
Honest HTTP statuses and idempotent single-row operations (audit NOTIF-21 / NOTIF-08).
A missing row is a 404 and an unexpected failure is a 500 - monitoring and
clients can't tell those apart from validation errors if everything is a 400.
"""
from unittest.mock import patch

import pytest

from models import Notification

pytestmark = pytest.mark.integration

BASE = "/student/profile/notifications"


@pytest.fixture
def h(client, auth_headers, csrf_headers):
    return lambda user: {**auth_headers(user), **csrf_headers(client)}


def test_mark_read_unknown_id_is_404(client, make_user, h):
    user = make_user(status="approved")
    assert client.post(f"{BASE}/999999/mark-read", headers=h(user)).status_code == 404


def test_delete_unknown_id_is_404(client, make_user, h):
    user = make_user(status="approved")
    assert client.delete(f"{BASE}/999999", headers=h(user)).status_code == 404


def test_other_users_notification_is_404_not_403_and_untouched(client, make_user, make_notification, h):
    owner, intruder = make_user(status="approved"), make_user(status="approved")
    note = make_notification(owner, is_read=False)

    assert client.post(f"{BASE}/{note.id}/mark-read", headers=h(intruder)).status_code == 404
    assert client.delete(f"{BASE}/{note.id}", headers=h(intruder)).status_code == 404
    assert Notification.query.get(note.id).is_read is False


def test_marking_twice_is_a_harmless_noop(client, make_user, make_notification, h):
    user = make_user(status="approved")
    note = make_notification(user, is_read=False)
    first = client.post(f"{BASE}/{note.id}/mark-read", headers=h(user))
    second = client.post(f"{BASE}/{note.id}/mark-read", headers=h(user))
    assert first.status_code == second.status_code == 200
    assert "already" not in first.get_json()["message"].lower()
    assert "already" in second.get_json()["message"].lower()


def test_deleting_twice_gives_one_success_and_one_404(client, make_user, make_notification, h):
    user = make_user(status="approved")
    note_id = make_notification(user).id
    assert client.delete(f"{BASE}/{note_id}", headers=h(user)).status_code == 200
    assert client.delete(f"{BASE}/{note_id}", headers=h(user)).status_code == 404


def test_unexpected_failure_is_a_500(client, make_user, auth_headers):
    user = make_user(status="approved")
    with patch("routes.student.notifications.Notification") as model:
        model.query.filter_by.side_effect = RuntimeError("db down")
        resp = client.get(f"{BASE}/unread-count", headers=auth_headers(user))
    assert resp.status_code == 500
