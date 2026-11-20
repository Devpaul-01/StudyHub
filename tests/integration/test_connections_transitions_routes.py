"""Audit CON-08 / CON-09: lifecycle endpoints are atomic and notify after commit."""
import pytest

from models import Connection, Notification
from extensions import db
import routes.student.connections.crud as crud

pytestmark = pytest.mark.integration


def H(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


def test_accept_that_loses_a_race_to_block_is_409_and_block_survives(
    client, make_user, make_connection, auth_headers, csrf_headers, monkeypatch
):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="pending")
    db.session.commit()

    # Simulate: the requester blocks between the route's read and its write.
    real = crud.accept_pending_connection

    def racing_accept(connection_id, receiver_id):
        from services import connection_service as cs
        cs.block_connection(a.id, b.id)
        db.session.commit()  # the "other transaction" commits first
        return real(connection_id, receiver_id)
    monkeypatch.setattr(crud, "accept_pending_connection", racing_accept)

    r = client.post(f"/student/connections/accept/{conn.id}", headers=H(client, auth_headers, csrf_headers, b))

    assert r.status_code == 409
    db.session.expire_all()
    row = Connection.query.get(conn.id)
    assert row.status == "blocked" and row.blocked_by_id == a.id


def test_cancel_cannot_delete_a_connection_accepted_in_the_meantime(
    client, make_user, make_connection, auth_headers, csrf_headers, monkeypatch
):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="pending")
    db.session.commit()

    real = crud.cancel_pending_connection

    def racing_cancel(connection_id, requester_id):
        from services import connection_service as cs
        cs.accept_pending_connection(connection_id, b.id)  # receiver wins the race
        db.session.commit()
        return real(connection_id, requester_id)
    monkeypatch.setattr(crud, "cancel_pending_connection", racing_cancel)

    r = client.delete(f"/student/connections/cancel/{conn.id}", headers=H(client, auth_headers, csrf_headers, a))

    assert r.status_code == 409
    db.session.expire_all()
    assert Connection.query.get(conn.id).status == "accepted"


def test_accept_sets_requester_unseen_and_notifies_after_commit(
    client, make_user, make_connection, auth_headers, csrf_headers
):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="pending")

    r = client.post(f"/student/connections/accept/{conn.id}", headers=H(client, auth_headers, csrf_headers, b))

    assert r.status_code == 200
    db.session.expire_all()
    row = Connection.query.get(conn.id)
    assert row.status == "accepted" and row.requester_seen is False
    assert Notification.query.filter_by(user_id=a.id, notification_type="connection_accepted").count() == 1


def test_remove_missing_connection_is_404(client, make_user, auth_headers, csrf_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    r = client.delete(f"/student/connections/remove/{b.id}", headers=H(client, auth_headers, csrf_headers, a))
    assert r.status_code == 404
