"""
Authenticated onboarding suggestions / connect routes (audit AUD-01, AUD-16).
They replace the old unauthenticated /connections/*-by-email routes.
"""
import pytest

from models import Connection, Notification

pytestmark = pytest.mark.integration

@pytest.fixture
def hdr(client, request):
    """Bearer token + double-submit CSRF headers for ``user`` (mutating routes need both)."""
    build_auth = request.getfixturevalue("auth_headers")
    build_csrf = request.getfixturevalue("csrf_headers")
    return lambda user: {**build_auth(user), **build_csrf(client)}


SUGGEST = "/student/connections/onboarding/suggestions"
CONNECT = "/student/connections/onboarding/connect/{}"
CONNECT_ALL = "/student/connections/onboarding/connect-all"


def _registering(make_user, email="new@example.com", status="pending_verification"):
    return make_user(email=email, username=None, pin="PENDING_VERIFICATION",
                     status=status, email_verified=True)


def _row(a, b):
    return Connection.query.filter(
        ((Connection.requester_id == a.id) & (Connection.receiver_id == b.id))
        | ((Connection.requester_id == b.id) & (Connection.receiver_id == a.id))
    ).first()


class TestAuthentication:
    def test_all_routes_require_a_session(self, client, make_user, csrf_headers):
        other = make_user(email="o@example.com")
        # A self-made matching csrf cookie/header pair is NOT authentication.
        csrf = csrf_headers(client)
        assert client.get(SUGGEST).status_code == 401
        assert client.post(CONNECT.format(other.id), headers=csrf).status_code == 401
        assert client.post(CONNECT_ALL, json={"ids": [other.id]}, headers=csrf).status_code == 401

    def test_old_email_in_url_routes_are_gone(self, client, make_user, csrf_headers):
        victim = make_user(email="victim@example.com")
        other = make_user(email="other@example.com")
        csrf = csrf_headers(client)
        assert client.get(f"/student/connections/suggestions-by-email/{victim.email}").status_code == 404
        assert client.post(f"/student/connections/onboard-connect/{victim.email}/{other.id}", headers=csrf).status_code in (404, 405)
        assert client.post(f"/student/connections/onboard-connect-all/{victim.email}", json={"ids": [other.id]}, headers=csrf).status_code in (404, 405)
        assert _row(victim, other) is None

    def test_unverified_pending_user_is_rejected(self, client, make_user, hdr):
        user = make_user(email="u@example.com", username=None, pin="PENDING_VERIFICATION",
                         status="pending_verification", email_verified=False)
        assert client.get(SUGGEST, headers=hdr(user)).status_code == 403


class TestConnect:
    def test_registering_user_sends_a_pending_request(self, client, make_user, hdr):
        me, target = _registering(make_user), make_user(email="t@example.com")
        resp = client.post(CONNECT.format(target.id), headers=hdr(me))
        assert resp.status_code == 201
        row = _row(me, target)
        assert row.status == "pending" and row.requester_id == me.id
        note = Notification.query.filter_by(user_id=target.id, notification_type="connection_request").one()
        assert note.related_id == me.id

    def test_existing_block_is_never_overwritten(self, client, db_session, make_user, make_connection, hdr):
        me, blocker = _registering(make_user), make_user(email="b@example.com")
        row = make_connection(blocker, me, status="blocked")
        resp = client.post(CONNECT.format(blocker.id), headers=hdr(me))
        assert resp.status_code == 403
        db_session.refresh(row)
        assert row.status == "blocked"

    def test_rejected_pair_is_left_alone(self, client, db_session, make_user, make_connection, hdr):
        me, other = _registering(make_user), make_user(email="r@example.com")
        row = make_connection(me, other, status="rejected")
        assert client.post(CONNECT.format(other.id), headers=hdr(me)).status_code == 403
        db_session.refresh(row)
        assert row.status == "rejected"

    def test_repeat_request_is_idempotent(self, client, make_user, hdr):
        me, target = _registering(make_user), make_user(email="t2@example.com")
        client.post(CONNECT.format(target.id), headers=hdr(me))
        resp = client.post(CONNECT.format(target.id), headers=hdr(me))
        assert resp.status_code == 200
        assert Connection.query.count() == 1

    def test_incoming_pending_request_is_accepted(self, client, db_session, make_user, make_connection, hdr):
        me, other = _registering(make_user), make_user(email="inc@example.com")
        row = make_connection(other, me, status="pending", responded_at=None)
        resp = client.post(CONNECT.format(other.id), headers=hdr(me))
        assert resp.status_code == 201
        db_session.refresh(row)
        assert row.status == "accepted"

    def test_cannot_connect_with_self_or_unknown_or_unapproved(self, client, make_user, hdr):
        me = _registering(make_user)
        pending = make_user(email="p@example.com", status="pending_verification")
        assert client.post(CONNECT.format(me.id), headers=hdr(me)).status_code == 400
        assert client.post(CONNECT.format(99999), headers=hdr(me)).status_code == 404
        assert client.post(CONNECT.format(pending.id), headers=hdr(me)).status_code == 404


class TestConnectAll:
    def test_sends_requests_and_reports_skips(self, client, make_user, make_connection, hdr):
        me = _registering(make_user)
        ok1, ok2 = make_user(email="a@example.com"), make_user(email="b2@example.com")
        blocker = make_user(email="blk@example.com")
        make_connection(blocker, me, status="blocked")
        resp = client.post(CONNECT_ALL, headers=hdr(me),
                           json={"ids": [ok1.id, ok2.id, blocker.id, me.id, 99999]})
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert sorted(data["sent"]) == sorted([ok1.id, ok2.id])
        assert {s["id"]: s["reason"] for s in data["skipped"]} == {blocker.id: "unavailable", 99999: "not_found"}
        assert data["requested_count"] == 2

    @pytest.mark.parametrize("payload", [
        {"ids": "1,2"}, {"ids": [1, "2"]}, {"ids": [True]}, {"ids": [0]}, {"ids": [-3]}, {"ids": [1.5]}, {},
    ])
    def test_rejects_malformed_ids(self, client, make_user, hdr, payload):
        me = _registering(make_user)
        assert client.post(CONNECT_ALL, headers=hdr(me), json=payload).status_code == 400

    def test_rejects_non_object_body(self, client, make_user, hdr):
        me = _registering(make_user)
        assert client.post(CONNECT_ALL, headers=hdr(me), json=[1, 2]).status_code == 400

    def test_caps_the_number_of_ids(self, client, make_user, hdr):
        me = _registering(make_user)
        resp = client.post(CONNECT_ALL, headers=hdr(me), json={"ids": list(range(1, 27))})
        assert resp.status_code == 400
        assert Connection.query.count() == 0

    def test_duplicate_ids_create_one_request(self, client, make_user, hdr):
        me, t = _registering(make_user), make_user(email="d@example.com")
        resp = client.post(CONNECT_ALL, headers=hdr(me), json={"ids": [t.id, t.id, t.id]})
        assert resp.status_code == 200
        assert Connection.query.count() == 1


class TestSuggestions:
    def test_scores_shared_subjects_and_skips_existing_connections(
        self, client, make_user, make_student_profile, make_onboarding_details,
        make_connection, hdr,
    ):
        me = _registering(make_user)
        make_onboarding_details(me, department="CS", subjects=["Math", "Physics"])
        match = make_user(email="m@example.com")
        make_student_profile(match, department="CS")
        make_onboarding_details(match, department="CS", subjects=["math"])
        connected = make_user(email="c@example.com")
        make_student_profile(connected, department="CS")
        make_connection(me, connected, status="accepted")
        make_user(email="pend@example.com", status="pending_verification")

        data = client.get(SUGGEST, headers=hdr(me)).get_json()["data"]["matches"]
        ids = [m["user"]["id"] for m in data]
        assert match.id in ids and connected.id not in ids
        top = next(m for m in data if m["user"]["id"] == match.id)
        assert top["match_score"] >= 50 and "Same department" in top["reasons"]

    def test_fallback_has_no_invented_score(self, client, make_user, hdr):
        me = _registering(make_user)
        make_user(email="x@example.com")
        data = client.get(SUGGEST, headers=hdr(me)).get_json()["data"]["matches"]
        assert data and all(m["match_score"] is None for m in data)

    def test_pool_is_bounded(self, client, make_user, hdr, monkeypatch):
        from services import onboarding_service
        monkeypatch.setattr(onboarding_service, "CANDIDATE_POOL_SIZE", 3)
        me = _registering(make_user)
        for i in range(8):
            make_user(email=f"bulk{i}@example.com")
        data = client.get(SUGGEST, headers=hdr(me)).get_json()["data"]["matches"]
        assert len(data) <= 3
