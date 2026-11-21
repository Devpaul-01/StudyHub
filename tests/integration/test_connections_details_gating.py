"""Audit CON-11 / CON-04: details & notes are only for accepted connections."""
import pytest

from extensions import db

pytestmark = pytest.mark.integration


def H(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


@pytest.mark.parametrize("status", ["pending", "rejected", "blocked"])
@pytest.mark.parametrize("who", ["requester", "receiver"])
def test_details_and_notes_are_404_unless_accepted(
    client, make_user, make_connection, auth_headers, csrf_headers, status, who
):
    a, b = make_user(status="approved"), make_user(status="approved")
    kwargs = {"blocked_by_id": a.id} if status == "blocked" else {}
    conn = make_connection(a, b, status=status, **kwargs)
    db.session.commit()
    me = a if who == "requester" else b

    assert client.get(f"/student/connections/{conn.id}/details", headers=auth_headers(me)).status_code == 404
    assert client.get(f"/student/connections/{conn.id}/notes", headers=auth_headers(me)).status_code == 404
    put = client.put(f"/student/connections/{conn.id}/notes/update", json={"notes": "x"},
                     headers=H(client, auth_headers, csrf_headers, me))
    assert put.status_code == 404


def test_blocked_party_loses_access_after_block(client, make_user, make_connection, auth_headers, csrf_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="accepted")
    db.session.commit()
    assert client.get(f"/student/connections/{conn.id}/details", headers=auth_headers(b)).status_code == 200

    client.post(f"/student/connections/block/{b.id}", headers=H(client, auth_headers, csrf_headers, a))

    assert client.get(f"/student/connections/{conn.id}/details", headers=auth_headers(b)).status_code == 404


def test_outsider_still_gets_403(client, make_user, make_connection, auth_headers):
    a, b, c = (make_user(status="approved") for _ in range(3))
    conn = make_connection(a, b, status="accepted")
    db.session.commit()
    assert client.get(f"/student/connections/{conn.id}/details", headers=auth_headers(c)).status_code == 403


def test_notes_are_sanitized_and_do_not_change_request_message(
    client, make_user, make_connection, auth_headers, csrf_headers
):
    a, b = make_user(status="approved"), make_user(status="approved")
    conn = make_connection(a, b, status="accepted", request_message="original hello")
    db.session.commit()
    h = H(client, auth_headers, csrf_headers, a)

    r = client.put(f"/student/connections/{conn.id}/notes/update",
                   json={"notes": "<script>alert(1)</script>study buddy"}, headers=h)
    assert r.status_code == 200
    assert r.get_json()["data"]["notes"] == "alert(1)study buddy" or "<script" not in r.get_json()["data"]["notes"]

    db.session.expire_all()
    from models import Connection
    assert Connection.query.get(conn.id).request_message == "original hello"

    assert client.put(f"/student/connections/{conn.id}/notes/update", json={"notes": "x" * 501}, headers=h).status_code == 400
