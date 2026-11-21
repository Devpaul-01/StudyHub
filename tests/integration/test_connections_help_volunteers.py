"""Audit CON-06 / CON-16 / CON-17: help-request volunteering."""
import datetime
import pytest
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import HelpRequest, HelpRequestVolunteer, Notification

pytestmark = pytest.mark.integration


def H(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


@pytest.fixture
def help_request(make_user):
    requester = make_user(status="approved")
    hr = HelpRequest(
        requester_id=requester.id, subject="Calculus", status="active",
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1),
    )
    db.session.add(hr)
    db.session.commit()
    return hr


def test_stranger_cannot_volunteer_and_gets_same_404_as_missing(client, make_user, help_request, auth_headers, csrf_headers):
    stranger = make_user(status="approved")
    r = client.post(f"/student/connections/help/{help_request.id}/volunteer",
                    headers=H(client, auth_headers, csrf_headers, stranger))
    missing = client.post("/student/connections/help/999999/volunteer",
                          headers=H(client, auth_headers, csrf_headers, stranger))
    assert r.status_code == 404 and missing.status_code == 404
    assert HelpRequestVolunteer.query.count() == 0
    assert Notification.query.count() == 0


@pytest.mark.parametrize("status", ["pending", "rejected", "blocked"])
def test_non_accepted_connection_cannot_volunteer(client, make_user, make_connection, help_request, auth_headers, csrf_headers, status):
    other = make_user(status="approved")
    kw = {"blocked_by_id": help_request.requester_id} if status == "blocked" else {}
    make_connection(help_request.requester, other, status=status, **kw)
    db.session.commit()
    r = client.post(f"/student/connections/help/{help_request.id}/volunteer",
                    headers=H(client, auth_headers, csrf_headers, other))
    assert r.status_code == 404


def test_connection_can_volunteer_once(client, make_user, make_connection, help_request, auth_headers, csrf_headers):
    helper = make_user(status="approved")
    make_connection(help_request.requester, helper, status="accepted")
    db.session.commit()
    url = f"/student/connections/help/{help_request.id}/volunteer"

    first = client.post(url, headers=H(client, auth_headers, csrf_headers, helper))
    assert first.status_code == 200 and first.get_json()["data"]["total_volunteers"] == 1
    assert Notification.query.filter_by(user_id=help_request.requester_id, notification_type="help_volunteer").count() == 1

    again = client.post(url, headers=H(client, auth_headers, csrf_headers, helper))
    assert again.status_code == 400
    assert HelpRequestVolunteer.query.count() == 1  # UNIQUE constraint, not a read-then-write


def test_unique_constraint_rejects_duplicate_rows(help_request, make_user):
    helper = make_user(status="approved")
    db.session.add(HelpRequestVolunteer(help_request_id=help_request.id, user_id=helper.id))
    db.session.commit()
    db.session.add(HelpRequestVolunteer(help_request_id=help_request.id, user_id=helper.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_cannot_volunteer_for_own_or_expired_request(client, make_user, make_connection, help_request, auth_headers, csrf_headers):
    own = client.post(f"/student/connections/help/{help_request.id}/volunteer",
                      headers=H(client, auth_headers, csrf_headers, help_request.requester))
    assert own.status_code == 400

    helper = make_user(status="approved")
    make_connection(help_request.requester, helper, status="accepted")
    help_request.expires_at = datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
    db.session.commit()
    expired = client.post(f"/student/connections/help/{help_request.id}/volunteer",
                          headers=H(client, auth_headers, csrf_headers, helper))
    assert expired.status_code == 400
    db.session.expire_all()
    assert HelpRequest.query.get(help_request.id).status == "expired"


def test_notification_failure_does_not_undo_the_volunteer_row(client, make_user, make_connection, help_request, auth_headers, csrf_headers, monkeypatch):
    helper = make_user(status="approved")
    make_connection(help_request.requester, helper, status="accepted")
    db.session.commit()
    import services.notification_service as ns
    monkeypatch.setattr(ns, "notify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("push down")))

    r = client.post(f"/student/connections/help/{help_request.id}/volunteer",
                    headers=H(client, auth_headers, csrf_headers, helper))

    assert r.status_code == 200
    assert HelpRequestVolunteer.query.count() == 1


def test_get_volunteers_owner_only_and_reports_effective_status(client, make_user, make_connection, help_request, auth_headers, csrf_headers):
    helper = make_user(status="approved")
    make_connection(help_request.requester, helper, status="accepted")
    db.session.commit()
    client.post(f"/student/connections/help/{help_request.id}/volunteer",
                headers=H(client, auth_headers, csrf_headers, helper))

    denied = client.get(f"/student/connections/help/{help_request.id}/volunteers", headers=auth_headers(helper))
    assert denied.status_code == 403

    help_request.expires_at = datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
    db.session.commit()
    ok = client.get(f"/student/connections/help/{help_request.id}/volunteers",
                    headers=auth_headers(help_request.requester)).get_json()["data"]
    assert ok["total_volunteers"] == 1 and ok["status"] == "expired"
    assert ok["volunteers"][0]["user_id"] == helper.id


def test_help_request_detail_visibility(client, make_user, make_connection, help_request, auth_headers):
    helper, stranger = make_user(status="approved"), make_user(status="approved")
    make_connection(help_request.requester, helper, status="accepted")
    db.session.commit()
    ok = client.get(f"/student/connections/help/{help_request.id}", headers=auth_headers(helper))
    assert ok.status_code == 200 and ok.get_json()["data"]["subject"] == "Calculus"
    assert client.get(f"/student/connections/help/{help_request.id}", headers=auth_headers(stranger)).status_code == 404
