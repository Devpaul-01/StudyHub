"""Audit CON-01: the onboarding connect routes must be authenticated and create-only."""
import datetime
import pytest

from models import Connection
from extensions import db

pytestmark = pytest.mark.integration


def _authorize(client, user):
    with client.session_transaction() as sess:
        sess["google_email"] = user.email


def _post(client, csrf_headers, url, **kw):
    return client.post(url, headers=csrf_headers(client), **kw)


@pytest.fixture
def onboarding_user(make_user):
    u = make_user(status="pending_verification")
    return u


def test_unauthenticated_connect_is_rejected(client, make_user, onboarding_user, csrf_headers):
    target = make_user(status="approved")
    r = _post(client, csrf_headers, f"/student/connections/onboard-connect/{onboarding_user.email}/{target.id}")
    assert r.status_code == 401
    assert Connection.query.count() == 0


def test_someone_elses_email_is_rejected(client, make_user, onboarding_user, csrf_headers):
    target = make_user(status="approved")
    attacker = make_user(status="approved")
    _authorize(client, attacker)
    r = _post(client, csrf_headers, f"/student/connections/onboard-connect/{onboarding_user.email}/{target.id}")
    assert r.status_code == 401
    assert Connection.query.count() == 0


def test_cannot_override_a_block(client, make_user, make_connection, onboarding_user, csrf_headers):
    victim = make_user(status="approved")
    conn = make_connection(victim, onboarding_user, status="blocked", blocked_by_id=victim.id)
    _authorize(client, onboarding_user)

    r = _post(client, csrf_headers, f"/student/connections/onboard-connect/{onboarding_user.email}/{victim.id}")

    assert r.status_code == 409
    db.session.expire_all()
    row = Connection.query.get(conn.id)
    assert row.status == "blocked" and row.blocked_by_id == victim.id


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_cannot_force_accept_existing_rows(client, make_user, make_connection, onboarding_user, csrf_headers, status):
    other = make_user(status="approved")
    conn = make_connection(other, onboarding_user, status=status)
    _authorize(client, onboarding_user)

    r = _post(client, csrf_headers, f"/student/connections/onboard-connect/{onboarding_user.email}/{other.id}")

    assert r.status_code == 409
    db.session.expire_all()
    assert Connection.query.get(conn.id).status == status


def test_established_account_cannot_use_onboarding_route(client, make_user, csrf_headers):
    old = make_user(status="approved")
    old.joined_at = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    db.session.commit()
    target = make_user(status="approved")
    _authorize(client, old)

    r = _post(client, csrf_headers, f"/student/connections/onboard-connect/{old.email}/{target.id}")

    assert r.status_code == 403
    assert Connection.query.count() == 0


def test_happy_path_creates_accepted_connection_and_notifies_after_commit(
    client, make_user, onboarding_user, csrf_headers
):
    target = make_user(status="approved")
    _authorize(client, onboarding_user)

    r = _post(client, csrf_headers, f"/student/connections/onboard-connect/{onboarding_user.email}/{target.id}")

    assert r.status_code == 201
    row = Connection.query.one()
    assert row.status == "accepted"
    assert row.requester_notes is None  # no auto-generated text in user-visible notes
    from models import Notification
    assert Notification.query.filter_by(user_id=target.id, notification_type="connection_accepted").count() == 1

    again = _post(client, csrf_headers, f"/student/connections/onboard-connect/{onboarding_user.email}/{target.id}")
    assert again.status_code == 200 and Connection.query.count() == 1


def test_connect_all_validates_and_caps_ids(client, make_user, onboarding_user, csrf_headers):
    _authorize(client, onboarding_user)
    url = f"/student/connections/onboard-connect-all/{onboarding_user.email}"

    assert _post(client, csrf_headers, url, json={"ids": []}).status_code == 400
    assert _post(client, csrf_headers, url, json={"ids": ["x"]}).status_code == 400
    assert _post(client, csrf_headers, url, json={"ids": list(range(1, 27))}).status_code == 400
    assert _post(client, csrf_headers, url, json=None).status_code == 400


def test_connect_all_is_create_only_and_skips_unapproved(client, make_user, make_connection, onboarding_user, csrf_headers):
    good = make_user(status="approved")
    blocker = make_user(status="approved")
    unapproved = make_user(status="pending_verification")
    blocked = make_connection(blocker, onboarding_user, status="blocked", blocked_by_id=blocker.id)
    _authorize(client, onboarding_user)

    r = _post(client, csrf_headers, f"/student/connections/onboard-connect-all/{onboarding_user.email}",
              json={"ids": [good.id, blocker.id, unapproved.id]})

    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["connected_ids"] == [good.id]
    assert set(data["skipped_ids"]) == {blocker.id, unapproved.id}
    db.session.expire_all()
    assert Connection.query.get(blocked.id).status == "blocked"


def test_suggestions_require_auth(client, onboarding_user):
    r = client.get(f"/student/connections/suggestions-by-email/{onboarding_user.email}")
    assert r.status_code == 401
    _authorize(client, onboarding_user)
    r = client.get(f"/student/connections/suggestions-by-email/{onboarding_user.email}")
    assert r.status_code == 200
