"""Audit CON-25: 'A connected with B' activity is opt-in."""
import pytest

from extensions import db
from models import ActivityFeed

pytestmark = pytest.mark.integration


def H(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


def _accept(client, auth_headers, csrf_headers, make_connection, acceptor, requester):
    conn = make_connection(requester, acceptor, status="pending")
    db.session.commit()
    r = client.post(f"/student/connections/accept/{conn.id}", headers=H(client, auth_headers, csrf_headers, acceptor))
    assert r.status_code == 200


def test_accepting_does_not_publish_activity_by_default(client, make_user, make_connection, auth_headers, csrf_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    _accept(client, auth_headers, csrf_headers, make_connection, b, a)
    assert ActivityFeed.query.filter_by(activity_type="connection_accepted").count() == 0


def test_accepting_publishes_activity_when_user_opted_in(client, make_user, make_connection, auth_headers, csrf_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    b.privacy_settings = {"show_in_activity_feed": True, "show_connections_in_activity_feed": True}
    db.session.commit()
    _accept(client, auth_headers, csrf_headers, make_connection, b, a)
    assert ActivityFeed.query.filter_by(activity_type="connection_accepted").count() == 1


def test_visibility_settings_round_trip_and_old_clients_do_not_reset_it(client, make_user, auth_headers, csrf_headers):
    u = make_user(status="approved")
    url = "/student/profile/visibility-settings"
    client.post(url, json={"show_connections_in_activity_feed": True}, headers=H(client, auth_headers, csrf_headers, u))
    got = client.get(url, headers=auth_headers(u)).get_json()["data"]
    assert got["show_connections_in_activity_feed"] is True

    # An older client that omits the key must not silently turn it off.
    client.post(url, json={"set_profile_private": False}, headers=H(client, auth_headers, csrf_headers, u))
    got = client.get(url, headers=auth_headers(u)).get_json()["data"]
    assert got["show_connections_in_activity_feed"] is True
