"""Audit CON-17: help-request broadcast validation, lifecycle and side-effect ordering."""
import datetime
import pytest

from extensions import db
from models import HelpRequest, Notification

pytestmark = pytest.mark.integration


def H(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


def _post(client, auth_headers, csrf_headers, user, payload):
    return client.post("/student/connections/help/broadcast", json=payload,
                       headers=H(client, auth_headers, csrf_headers, user))


@pytest.fixture
def network(make_user, make_connection, make_onboarding_details):
    me = make_user(status="approved")
    strong = make_user(status="approved")
    studying = make_user(status="approved")
    irrelevant = make_user(status="approved")
    stranger = make_user(status="approved")
    for friend in (strong, studying, irrelevant):
        make_connection(me, friend, status="accepted")
    make_onboarding_details(strong, subjects=["Physics"], strong_subjects=["Calculus"])
    make_onboarding_details(studying, subjects=["Calculus II"], strong_subjects=[])
    make_onboarding_details(irrelevant, subjects=["History"], strong_subjects=["Art"])
    make_onboarding_details(stranger, subjects=["Calculus"], strong_subjects=["Calculus"])
    db.session.commit()
    return dict(me=me, strong=strong, studying=studying, irrelevant=irrelevant, stranger=stranger)


def test_only_relevant_connections_are_notified(client, network, auth_headers, csrf_headers):
    r = _post(client, auth_headers, csrf_headers, network["me"], {"subject": "Calculus", "message": "exam tomorrow"})

    assert r.status_code == 200
    assert r.get_json()["data"]["notified_count"] == 2
    notified = {n.user_id for n in Notification.query.filter_by(notification_type="help_request").all()}
    assert notified == {network["strong"].id, network["studying"].id}   # not irrelevant, not stranger
    link = Notification.query.filter_by(notification_type="help_request").first().link
    assert link.startswith("/student/connections/help/")


@pytest.mark.parametrize("payload", [
    {}, {"subject": ""}, {"subject": "x" * 101}, {"subject": "ok", "message": "y" * 301},
    {"subject": 123}, {"subject": "ok", "message": ["no"]},
])
def test_invalid_input_is_400_not_500(client, network, auth_headers, csrf_headers, payload):
    r = _post(client, auth_headers, csrf_headers, network["me"], payload)
    assert r.status_code == 400
    assert HelpRequest.query.count() == 0


def test_html_is_stripped_from_subject(client, network, auth_headers, csrf_headers):
    r = _post(client, auth_headers, csrf_headers, network["me"], {"subject": "<b>Calculus</b>"})
    assert r.status_code == 200
    assert HelpRequest.query.one().subject == "Calculus"


def test_new_broadcast_expires_the_previous_active_one(client, network, auth_headers, csrf_headers):
    _post(client, auth_headers, csrf_headers, network["me"], {"subject": "Calculus"})
    _post(client, auth_headers, csrf_headers, network["me"], {"subject": "Physics"})
    rows = HelpRequest.query.order_by(HelpRequest.id).all()
    assert [r.status for r in rows] == ["expired", "active"]


def test_database_allows_only_one_active_request_per_user(network):
    from sqlalchemy.exc import IntegrityError
    exp = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    db.session.add(HelpRequest(requester_id=network["me"].id, subject="A", status="active", expires_at=exp))
    db.session.commit()
    db.session.add(HelpRequest(requester_id=network["me"].id, subject="B", status="active", expires_at=exp))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_no_notifications_or_push_when_commit_fails(client, network, auth_headers, csrf_headers, monkeypatch):
    sent = []
    import services.push_notifications as pn
    monkeypatch.setattr(pn.PushNotificationService, "send_multicast", lambda *a, **k: sent.append(1))
    for u in (network["strong"], network["studying"]):
        u.fcm_token = "tok-%d" % u.id
    db.session.commit()

    real_commit = db.session.commit
    def failing_commit():
        db.session.rollback()
        raise RuntimeError("db down")
    monkeypatch.setattr(db.session, "commit", failing_commit)

    r = _post(client, auth_headers, csrf_headers, network["me"], {"subject": "Calculus"})
    monkeypatch.setattr(db.session, "commit", real_commit)

    assert r.status_code == 500
    assert sent == []
    assert Notification.query.count() == 0


def test_no_connections_still_creates_request(client, make_user, auth_headers, csrf_headers):
    loner = make_user(status="approved")
    r = _post(client, auth_headers, csrf_headers, loner, {"subject": "Calculus"})
    assert r.status_code == 200 and r.get_json()["data"]["notified_count"] == 0
