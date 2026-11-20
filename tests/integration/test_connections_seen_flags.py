"""Audit CON-07: per-party seen flags."""
import datetime
import pytest

from extensions import db
from models import Connection

pytestmark = pytest.mark.integration


def H(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


def _count(client, auth_headers, user, which):
    return client.get(f"/student/connections/unseen/{which}", headers=auth_headers(user)).get_json()["data"]["count"]


def test_requester_sees_acceptance_even_if_receiver_already_marked_seen(
    client, make_user, make_connection, auth_headers, csrf_headers
):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="pending", receiver_seen=False, requester_seen=True)
    db.session.commit()

    assert _count(client, auth_headers, b, "received") == 1
    client.post("/student/connections/mark-received-seen", headers=H(client, auth_headers, csrf_headers, b))
    assert _count(client, auth_headers, b, "received") == 0

    client.post(f"/student/connections/accept/{conn.id}", headers=H(client, auth_headers, csrf_headers, b))

    # This was 0 before the fix (shared flag).
    assert _count(client, auth_headers, a, "sent") == 1
    client.post("/student/connections/mark-sent-seen", headers=H(client, auth_headers, csrf_headers, a))
    assert _count(client, auth_headers, a, "sent") == 0


def test_requester_marking_sent_seen_does_not_hide_receivers_badge(
    client, make_user, make_connection, auth_headers, csrf_headers
):
    a, b = make_user(status="approved"), make_user(status="approved")
    make_connection(a, b, status="pending", receiver_seen=False, requester_seen=True)
    db.session.commit()

    client.post("/student/connections/mark-sent-seen", headers=H(client, auth_headers, csrf_headers, a))

    assert _count(client, auth_headers, b, "received") == 1


def test_rejection_is_unseen_for_requester(client, make_user, make_connection, auth_headers, csrf_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="pending", receiver_seen=True, requester_seen=True)
    db.session.commit()

    client.post(f"/student/connections/reject/{conn.id}", headers=H(client, auth_headers, csrf_headers, b))

    assert _count(client, auth_headers, a, "sent") == 1


def test_resent_request_is_unseen_for_receiver(client, make_user, make_connection, auth_headers, csrf_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="rejected", receiver_seen=True, requester_seen=True)
    conn.responded_at = datetime.datetime.utcnow() - datetime.timedelta(hours=30)
    db.session.commit()

    r = client.post(f"/student/connections/request/{b.id}", headers=H(client, auth_headers, csrf_headers, a))
    assert r.status_code == 201

    assert _count(client, auth_headers, b, "received") == 1


def test_removed_endpoints_are_gone(client, make_user, auth_headers, csrf_headers):
    a = make_user(status="approved")
    assert client.get("/student/connections/unseen/all", headers=auth_headers(a)).status_code == 404
    assert client.post("/student/connections/mark-all-seen", headers=H(client, auth_headers, csrf_headers, a)).status_code == 404
