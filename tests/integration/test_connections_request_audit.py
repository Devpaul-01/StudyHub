"""Audit CON-03 / CON-04 / CON-09 / CON-19: send_connection_request behaviour."""
import datetime
import pytest

from models import Connection, Notification
from extensions import db

pytestmark = pytest.mark.integration


def H(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


def _age_rejection(conn, hours=30):
    conn.responded_at = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
    db.session.commit()


def test_rerequest_after_rejection_succeeds_in_original_direction(client, make_user, make_connection, auth_headers, csrf_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="rejected")
    _age_rejection(conn)

    r = client.post(f"/student/connections/request/{b.id}", json={"message": "second try"},
                    headers=H(client, auth_headers, csrf_headers, a))

    assert r.status_code == 201, r.get_json()
    db.session.expire_all()
    row = Connection.query.get(conn.id)
    assert (row.requester_id, row.receiver_id) == (a.id, b.id)
    assert row.status == "pending"
    assert row.responded_at is not None
    assert row.request_message == "second try"


def test_rerequest_by_original_receiver_swaps_direction_so_it_can_be_accepted(client, make_user, make_connection, auth_headers, csrf_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="rejected")   # a asked, b rejected
    _age_rejection(conn)

    r = client.post(f"/student/connections/request/{a.id}", headers=H(client, auth_headers, csrf_headers, b))
    assert r.status_code == 201, r.get_json()

    db.session.expire_all()
    row = Connection.query.get(conn.id)
    assert (row.requester_id, row.receiver_id) == (b.id, a.id)
    # The notification goes to the party who can actually accept.
    assert Notification.query.filter_by(user_id=a.id, notification_type="connection_request").count() == 1
    # ...and `a` can accept it.
    acc = client.post(f"/student/connections/accept/{conn.id}", headers=H(client, auth_headers, csrf_headers, a))
    assert acc.status_code == 200


def test_rerequest_during_cooldown_is_429(client, make_user, make_connection, auth_headers, csrf_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="rejected")
    _age_rejection(conn, hours=1)

    r = client.post(f"/student/connections/request/{b.id}", headers=H(client, auth_headers, csrf_headers, a))
    assert r.status_code == 429
    assert "23 hours" in r.get_json()["message"]


def test_message_is_sanitized_and_length_limited(client, make_user, auth_headers, csrf_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    h = H(client, auth_headers, csrf_headers, a)

    too_long = client.post(f"/student/connections/request/{b.id}", json={"message": "x" * 301}, headers=h)
    assert too_long.status_code == 400
    wrong_type = client.post(f"/student/connections/request/{b.id}", json={"message": 123}, headers=h)
    assert wrong_type.status_code == 400
    assert Connection.query.count() == 0

    ok = client.post(f"/student/connections/request/{b.id}",
                     json={"message": "<img src=x onerror=alert(1)>hi there"}, headers=h)
    assert ok.status_code in (200, 201)
    row = Connection.query.one()
    assert row.request_message == "hi there"
    assert row.requester_notes is None  # CON-19: never mixed into private notes


def test_reverse_pending_request_is_accepted_atomically(client, make_user, make_connection, auth_headers, csrf_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="pending")  # a asked b

    r = client.post(f"/student/connections/request/{a.id}", headers=H(client, auth_headers, csrf_headers, b))

    assert r.status_code == 201
    assert r.get_json()["data"]["connection_status"] == "accepted"
    db.session.expire_all()
    assert Connection.query.get(conn.id).status == "accepted"
    assert Notification.query.filter_by(user_id=a.id, notification_type="connection_accepted").count() == 1


def test_unexpected_failure_returns_500_and_sends_no_notification(client, make_user, auth_headers, csrf_headers, monkeypatch):
    a, b = make_user(status="approved"), make_user(status="approved")
    import routes.student.connections.crud as crud

    def boom(*args, **kwargs):
        raise RuntimeError("db exploded")
    monkeypatch.setattr(crud, "gather_user_data", boom)

    r = client.post(f"/student/connections/request/{b.id}", headers=H(client, auth_headers, csrf_headers, a))
    assert r.status_code == 500
    assert Notification.query.count() == 0
    assert Connection.query.count() == 0


def test_notification_is_not_sent_when_commit_loses_the_race(client, make_user, auth_headers, csrf_headers, monkeypatch):
    """CON-09: a rolled-back insert must not leave a notification / counter / emit behind."""
    from sqlalchemy.exc import IntegrityError
    a, b = make_user(status="approved"), make_user(status="approved")
    calls = []
    import services.notification_service as ns
    monkeypatch.setattr(ns, "_broadcast", lambda *a_, **k: calls.append("emit"))

    real_commit = db.session.commit
    state = {"n": 0}

    def flaky_commit():
        state["n"] += 1
        if state["n"] == 1:
            raise IntegrityError("insert", {}, Exception("uq_connections_pair"))
        return real_commit()
    monkeypatch.setattr(db.session, "commit", flaky_commit)

    r = client.post(f"/student/connections/request/{b.id}", headers=H(client, auth_headers, csrf_headers, a))

    assert r.status_code == 200  # reported as "already ..." (lost race)
    assert calls == []
    assert Notification.query.count() == 0
