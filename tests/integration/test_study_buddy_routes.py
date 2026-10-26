"""
tests/integration/test_study_buddy_routes.py

routes/student/study_buddy.py has 16 endpoints and previously had zero
tests at any level. This batch covers the core request/partnership
lifecycle and preference management: preferences CRUD, send/cancel a
study-buddy request, sent/received request listings, session logging
and history, match detail, and ending a partnership. The matching
algorithm itself (get_suggestions, get_match_details ranking) and the
cached discovery/stats endpoints (success-stories, stats) are a
separate, larger area left for a future pass.

Note: this file has no "accept request" endpoint -- a StudyBuddyMatch
row is apparently created by code outside this file's 16 routes, so
match-dependent tests (session logging, match details, ending a
partnership) construct the StudyBuddyMatch row directly.

Also note: a real bug was found while writing these tests --
send_request() can never actually succeed (see the xfail on
test_send_request_creates_a_pending_request below). Tests for the
other request-lifecycle endpoints (cancel, sent/received listings)
therefore construct the StudyBuddyRequest row directly rather than
going through the broken POST endpoint, so they can still exercise
their own logic in isolation.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# Preferences: POST / GET / PATCH /study-buddy/preferences
# ============================================================================

class TestPreferences:
    def test_set_preferences_happy_path(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/study-buddy/preferences",
            json={"needs_help": ["calculus"], "good_at": ["chemistry"], "goals": "Pass finals"},
            headers=headers,
        )

        assert resp.status_code == 201
        prefs = resp.get_json()["data"]["preferences"]
        assert prefs["needs_help"] == ["calculus"]
        assert prefs["goals"] == "Pass finals"

    def test_set_preferences_requires_at_least_one_course(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/study-buddy/preferences", json={"needs_help": [], "good_at": []}, headers=headers
        )

        assert resp.status_code == 400

    def test_set_preferences_rejects_non_list_fields(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/study-buddy/preferences",
            json={"needs_help": "not-a-list", "good_at": []},
            headers=headers,
        )

        assert resp.status_code == 400

    def test_get_preferences_empty_before_set(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/study-buddy/preferences", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"] == {}

    def test_get_preferences_after_set(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}
        client.post(
            "/student/study-buddy/preferences", json={"needs_help": ["physics"]}, headers=headers
        )

        resp = client.get("/student/study-buddy/preferences", headers=auth_headers(user))

        assert resp.get_json()["data"]["needs_help"] == ["physics"]

    def test_patch_preferences_updates_only_given_fields(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}
        client.post(
            "/student/study-buddy/preferences",
            json={"needs_help": ["calculus"], "goals": "Original goal"},
            headers=headers,
        )

        resp = client.patch(
            "/student/study-buddy/preferences", json={"goals": "Updated goal"}, headers=headers
        )

        assert resp.status_code == 200
        prefs = resp.get_json()["data"]["preferences"]
        assert prefs["goals"] == "Updated goal"
        assert prefs["needs_help"] == ["calculus"]


# ============================================================================
# POST /study-buddy/request/<user_id>  and  DELETE /study-buddy/cancel/<id>
# ============================================================================

def _make_request(db_session, requester, receiver, **overrides):
    from models import StudyBuddyRequest

    defaults = dict(
        requester_id=requester.id, receiver_id=receiver.id,
        message="Let's study together!", subjects=[], availability={},
        status="pending",
    )
    defaults.update(overrides)
    row = StudyBuddyRequest(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


class TestSendAndCancelRequest:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG FOUND BY THIS TEST: send_request() always builds the new "
            "StudyBuddyRequest with `availability=prefs.get(\"available_days\", [])` "
            "-- a LIST -- but StudyBuddyRequest.availability is declared as "
            "`MutableDict.as_mutable(db.JSON)`, which rejects non-dict "
            "values at assignment time with "
            "\"Attribute 'availability' does not accept objects of type "
            "list\". There is no code path that makes `prefs.get(\"available_days\", [])` "
            "return a dict (set_preferences/update_preferences always store "
            "it as a list too), so POST /study-buddy/request/<id> can never "
            "succeed -- every call hits the bare except and returns a "
            "generic 400 'Failed to send request', even for a perfectly "
            "valid request. strict=True so this flips to XPASS the moment "
            "the field is fixed (either store availability as a dict, or "
            "change the model column to a list-accepting JSON type)."
        ),
    )
    def test_send_request_creates_a_pending_request(
        self, client, make_user, auth_headers, csrf_headers
    ):
        sender = make_user()
        target = make_user()
        headers = {**auth_headers(sender), **csrf_headers(client)}

        resp = client.post(
            f"/student/study-buddy/request/{target.id}",
            json={"message": "Let's go", "subjects": ["math"]},
            headers=headers,
        )

        assert resp.status_code == 201
        from models import StudyBuddyRequest
        row = StudyBuddyRequest.query.get(resp.get_json()["data"]["request_id"])
        assert row.requester_id == sender.id
        assert row.receiver_id == target.id
        assert row.status == "pending"

    def test_cannot_send_request_to_self(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(f"/student/study-buddy/request/{user.id}", json={}, headers=headers)

        assert resp.status_code == 400

    def test_duplicate_pending_request_rejected(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        sender = make_user()
        target = make_user()
        _make_request(db_session, sender, target)
        headers = {**auth_headers(sender), **csrf_headers(client)}

        # The existing-pending-request check runs before the buggy
        # availability assignment, so this 409 path is reachable even
        # though a fresh, successful send_request() is not (see the
        # xfail above).
        resp = client.post(f"/student/study-buddy/request/{target.id}", json={}, headers=headers)

        assert resp.status_code == 409

    def test_request_to_nonexistent_user_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/study-buddy/request/999999", json={}, headers=headers)

        assert resp.status_code == 404

    def test_sender_can_cancel_a_pending_request(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        sender = make_user()
        target = make_user()
        row = _make_request(db_session, sender, target)
        headers = {**auth_headers(sender), **csrf_headers(client)}

        resp = client.delete(f"/student/study-buddy/cancel/{row.id}", headers=headers)

        assert resp.status_code == 200
        from models import StudyBuddyRequest
        assert StudyBuddyRequest.query.get(row.id) is None

    def test_receiver_cannot_cancel_a_request_sent_to_them(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        sender = make_user()
        target = make_user()
        row = _make_request(db_session, sender, target)
        target_headers = {**auth_headers(target), **csrf_headers(client)}

        resp = client.delete(f"/student/study-buddy/cancel/{row.id}", headers=target_headers)

        assert resp.status_code == 403

    def test_cancel_nonexistent_request_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete("/student/study-buddy/cancel/999999", headers=headers)

        assert resp.status_code == 404


# ============================================================================
# GET /study-buddy/requests/sent and /requests/received
# ============================================================================

class TestRequestListings:
    def test_sent_requests_lists_what_the_user_sent(
        self, client, db_session, make_user, auth_headers
    ):
        sender = make_user()
        target = make_user()
        _make_request(db_session, sender, target)

        resp = client.get("/student/study-buddy/requests/sent", headers=auth_headers(sender))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["sent_requests"][0]["user"]["id"] == target.id

    def test_sent_requests_empty_for_a_fresh_user(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/study-buddy/requests/sent", headers=auth_headers(user))

        assert resp.get_json()["data"] == {"sent_requests": [], "total": 0}

    def test_received_requests_lists_pending_incoming(
        self, client, db_session, make_user, auth_headers
    ):
        sender = make_user()
        target = make_user()
        _make_request(db_session, sender, target)

        resp = client.get("/student/study-buddy/requests/received", headers=auth_headers(target))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["received_requests"][0]["user"]["id"] == sender.id


# ============================================================================
# Match-dependent endpoints (StudyBuddyMatch constructed directly)
# ============================================================================

class TestMatchLifecycle:
    def _make_match(self, db_session, user1, user2, **overrides):
        from models import StudyBuddyMatch

        defaults = dict(
            user1_id=user1.id, user2_id=user2.id,
            subjects=["math"], sessions_count=0, is_active=True,
            matched_at=datetime.datetime.utcnow(),
        )
        defaults.update(overrides)
        match = StudyBuddyMatch(**defaults)
        db_session.add(match)
        db_session.flush()
        return match

    def test_get_match_details_for_a_participant(
        self, client, db_session, make_user, auth_headers
    ):
        u1 = make_user()
        u2 = make_user()
        match = self._make_match(db_session, u1, u2)

        resp = client.get(f"/student/study-buddy/match/{match.id}", headers=auth_headers(u1))

        assert resp.status_code == 200
        body = resp.get_json()["data"]["match"]
        assert body["partner"]["id"] == u2.id

    def test_get_match_details_non_participant_forbidden(
        self, client, db_session, make_user, auth_headers
    ):
        u1 = make_user()
        u2 = make_user()
        outsider = make_user()
        match = self._make_match(db_session, u1, u2)

        resp = client.get(f"/student/study-buddy/match/{match.id}", headers=auth_headers(outsider))

        assert resp.status_code == 403

    def test_get_match_details_nonexistent_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/study-buddy/match/999999", headers=auth_headers(user))

        assert resp.status_code == 404

    def test_log_session_increments_sessions_count(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        u1 = make_user()
        u2 = make_user()
        match = self._make_match(db_session, u1, u2)
        headers = {**auth_headers(u1), **csrf_headers(client)}

        resp = client.post(
            "/student/study-buddy/session", json={"match_id": match.id}, headers=headers
        )

        assert resp.status_code == 201
        from models import StudyBuddyMatch
        assert StudyBuddyMatch.query.get(match.id).sessions_count == 1

    def test_log_session_missing_match_id_rejected(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/study-buddy/session", json={}, headers=headers)

        assert resp.status_code == 400

    def test_log_session_non_participant_forbidden(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        u1 = make_user()
        u2 = make_user()
        outsider = make_user()
        match = self._make_match(db_session, u1, u2)
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.post(
            "/student/study-buddy/session", json={"match_id": match.id}, headers=headers
        )

        assert resp.status_code == 403

    def test_session_history_reflects_logged_sessions(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        u1 = make_user()
        u2 = make_user()
        match = self._make_match(db_session, u1, u2, sessions_count=3)

        resp = client.get("/student/study-buddy/sessions", headers=auth_headers(u1))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total_sessions"] == 3
        assert data["sessions"][0]["match_id"] == match.id

    def test_end_partnership_deactivates_match(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        u1 = make_user()
        u2 = make_user()
        match = self._make_match(db_session, u1, u2)
        headers = {**auth_headers(u1), **csrf_headers(client)}

        resp = client.delete(f"/student/study-buddy/remove/{match.id}", headers=headers)

        assert resp.status_code == 200
        from models import StudyBuddyMatch
        row = StudyBuddyMatch.query.get(match.id)
        assert row.is_active is False
        assert row.ended_at is not None

    def test_end_partnership_non_participant_forbidden(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        u1 = make_user()
        u2 = make_user()
        outsider = make_user()
        match = self._make_match(db_session, u1, u2)
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.delete(f"/student/study-buddy/remove/{match.id}", headers=headers)

        assert resp.status_code == 400

    def test_buddy_connections_lists_active_and_ended_matches(
        self, client, db_session, make_user, auth_headers
    ):
        u1 = make_user()
        u2 = make_user()
        self._make_match(db_session, u1, u2)

        resp = client.get("/student/study-buddy/requests/connected", headers=auth_headers(u1))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert len(data) == 1
        assert data[0]["user"]["user_id"] == u2.id
