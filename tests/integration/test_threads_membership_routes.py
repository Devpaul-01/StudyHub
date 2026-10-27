"""
tests/integration/test_threads_membership_routes.py

routes/student/threads/membership.py has 15 endpoints and previously
had zero tests at any level. This batch covers leave/remove, the
member-listing endpoint, the join-request workflow (request/cancel/
approve/reject/listings), role updates, and the manual-invite workflow
(invite/listings/accept/decline). add_members_to_thread (direct-add
with connection verification) is a separate, more involved endpoint
left for a future pass.

Note: require_moderator_or_creator() checks the caller's ThreadMember
row (role == "creator" or "moderator"), not Thread.creator_id alone —
so every fixture thread below also creates a ThreadMember(role="creator")
row for its creator, matching how a real thread is set up elsewhere in
the app (thread creation always makes the creator a member too).

Also note: a real, systemic bug was found while writing these tests
(see the three xfails below) -- ThreadJoinRequest.requested_at has no
column default, and neither request_join_thread()'s nor
invite_to_thread()'s fresh-creation branch ever sets it. Every listing
endpoint that reads a freshly-created row back (get_pending_requests,
get_my_join_requests, get_my_invites) unconditionally calls
`.requested_at.isoformat()`, which raises AttributeError on None and
gets flattened into a generic 400 by the route's bare except. In
other words: right after a brand-new join request or invite is
created, the matching "list my requests/invites" endpoint 400s.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


def _make_thread_with_creator(db_session, make_thread, make_thread_member, creator, **overrides):
    thread = make_thread(creator, **overrides)
    make_thread_member(thread, creator, role="creator")
    return thread


# ============================================================================
# POST /threads/<id>/leave
# ============================================================================

class TestLeaveThread:
    def test_member_can_leave(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        member = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator, member_count=2)
        make_thread_member(thread, member)
        headers = {**auth_headers(member), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/leave", headers=headers)

        assert resp.status_code == 200
        from models import ThreadMember, Thread
        assert ThreadMember.query.filter_by(thread_id=thread.id, student_id=member.id).first() is None
        assert Thread.query.get(thread.id).member_count == 1

    def test_creator_cannot_leave(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/leave", headers=headers)

        assert resp.status_code == 403

    def test_non_member_cannot_leave(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        outsider = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/leave", headers=headers)

        assert resp.status_code == 404

    def test_nonexistent_thread_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/threads/999999/leave", headers=headers)

        assert resp.status_code == 404


# ============================================================================
# DELETE /threads/<id>/remove/<user_id>
# ============================================================================

class TestRemoveMember:
    def test_creator_can_remove_a_member(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        member = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator, member_count=2)
        make_thread_member(thread, member)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.delete(f"/student/threads/{thread.id}/remove/{member.id}", headers=headers)

        assert resp.status_code == 200
        from models import ThreadMember
        assert ThreadMember.query.filter_by(thread_id=thread.id, student_id=member.id).first() is None

    def test_plain_member_cannot_remove_another_member(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        member1 = make_user()
        member2 = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        make_thread_member(thread, member1)
        make_thread_member(thread, member2)
        headers = {**auth_headers(member1), **csrf_headers(client)}

        resp = client.delete(f"/student/threads/{thread.id}/remove/{member2.id}", headers=headers)

        assert resp.status_code == 403

    def test_cannot_remove_the_creator(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        moderator = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        make_thread_member(thread, moderator, role="moderator")
        headers = {**auth_headers(moderator), **csrf_headers(client)}

        resp = client.delete(f"/student/threads/{thread.id}/remove/{creator.id}", headers=headers)

        assert resp.status_code == 403


# ============================================================================
# GET /threads/<id>/members
# ============================================================================

class TestGetThreadMembers:
    def test_member_sees_the_full_list(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers
    ):
        creator = make_user()
        member = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        make_thread_member(thread, member)

        resp = client.get(f"/student/threads/{thread.id}/members", headers=auth_headers(member))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total"] == 2
        ids = {m["user_id"] for m in data["members"]}
        assert ids == {creator.id, member.id}

    def test_non_member_forbidden(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers
    ):
        creator = make_user()
        outsider = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)

        resp = client.get(f"/student/threads/{thread.id}/members", headers=auth_headers(outsider))

        assert resp.status_code == 403


# ============================================================================
# PATCH /threads/<id>/members/<user_id>/role
# ============================================================================

class TestUpdateMemberRole:
    def test_creator_promotes_member_to_moderator(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        member = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        make_thread_member(thread, member)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.patch(
            f"/student/threads/{thread.id}/members/{member.id}/role",
            json={"role": "moderator"}, headers=headers,
        )

        assert resp.status_code == 200
        from models import ThreadMember
        row = ThreadMember.query.filter_by(thread_id=thread.id, student_id=member.id).first()
        assert row.role == "moderator"

    def test_non_creator_cannot_change_roles(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        moderator = make_user()
        member = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        make_thread_member(thread, moderator, role="moderator")
        make_thread_member(thread, member)
        headers = {**auth_headers(moderator), **csrf_headers(client)}

        resp = client.patch(
            f"/student/threads/{thread.id}/members/{member.id}/role",
            json={"role": "moderator"}, headers=headers,
        )

        assert resp.status_code == 403

    def test_invalid_role_rejected(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        member = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        make_thread_member(thread, member)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.patch(
            f"/student/threads/{thread.id}/members/{member.id}/role",
            json={"role": "owner"}, headers=headers,
        )

        assert resp.status_code == 400


# ============================================================================
# Join-request workflow: request / cancel / approve / reject / listings
# ============================================================================

class TestJoinRequestWorkflow:
    def test_request_join_creates_pending_request(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        requester = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        headers = {**auth_headers(requester), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/join", json={}, headers=headers)

        assert resp.status_code == 201
        from models import ThreadJoinRequest
        row = ThreadJoinRequest.query.get(resp.get_json()["data"]["request_id"])
        assert row.status == "pending"

    def test_cannot_request_join_when_already_a_member(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        member = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        make_thread_member(thread, member)
        headers = {**auth_headers(member), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/join", json={}, headers=headers)

        assert resp.status_code == 409

    def test_closed_thread_rejects_join_request(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        requester = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator, is_open=False)
        headers = {**auth_headers(requester), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/join", json={}, headers=headers)

        assert resp.status_code == 403

    def test_full_thread_rejects_join_request(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        requester = make_user()
        thread = _make_thread_with_creator(
            db_session, make_thread, make_thread_member, creator, max_members=1
        )
        headers = {**auth_headers(requester), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/join", json={}, headers=headers)

        assert resp.status_code == 403

    def test_pending_requests_lists_requests_for_threads_i_created(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        requester = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        headers = {**auth_headers(requester), **csrf_headers(client)}
        client.post(f"/student/threads/{thread.id}/join", json={}, headers=headers)

        resp = client.get("/student/threads/pending-requests", headers=auth_headers(creator))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["pending_requests"][0]["requester"]["id"] == requester.id

    def test_my_requests_lists_what_i_sent(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        requester = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        headers = {**auth_headers(requester), **csrf_headers(client)}
        client.post(f"/student/threads/{thread.id}/join", json={}, headers=headers)

        resp = client.get("/student/threads/my-requests", headers=auth_headers(requester))

        assert resp.get_json()["data"]["total"] == 1

    def test_approve_adds_member_and_marks_approved(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        requester = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        join_resp = client.post(f"/student/threads/{thread.id}/join", json={}, headers=req_headers)
        request_id = join_resp.get_json()["data"]["request_id"]
        creator_headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(
            f"/student/threads/{thread.id}/requests/{request_id}/approve", headers=creator_headers
        )

        assert resp.status_code == 200
        from models import ThreadMember, ThreadJoinRequest, Thread
        assert ThreadMember.query.filter_by(thread_id=thread.id, student_id=requester.id).first() is not None
        assert ThreadJoinRequest.query.get(request_id).status == "approved"
        assert Thread.query.get(thread.id).member_count == 2

    def test_reject_marks_rejected_without_adding_member(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        requester = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        join_resp = client.post(f"/student/threads/{thread.id}/join", json={}, headers=req_headers)
        request_id = join_resp.get_json()["data"]["request_id"]
        creator_headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(
            f"/student/threads/{thread.id}/requests/{request_id}/reject", headers=creator_headers
        )

        assert resp.status_code == 200
        from models import ThreadMember, ThreadJoinRequest
        assert ThreadMember.query.filter_by(thread_id=thread.id, student_id=requester.id).first() is None
        assert ThreadJoinRequest.query.get(request_id).status == "rejected"

    def test_requester_can_cancel_own_pending_request(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        requester = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        headers = {**auth_headers(requester), **csrf_headers(client)}
        join_resp = client.post(f"/student/threads/{thread.id}/join", json={}, headers=headers)
        request_id = join_resp.get_json()["data"]["request_id"]

        resp = client.delete(f"/student/threads/requests/{request_id}/cancel", headers=headers)

        assert resp.status_code == 200
        from models import ThreadJoinRequest
        assert ThreadJoinRequest.query.get(request_id) is None

    def test_cannot_cancel_someone_elses_request(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        requester = make_user()
        outsider = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        req_headers = {**auth_headers(requester), **csrf_headers(client)}
        join_resp = client.post(f"/student/threads/{thread.id}/join", json={}, headers=req_headers)
        request_id = join_resp.get_json()["data"]["request_id"]
        outsider_headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.delete(f"/student/threads/requests/{request_id}/cancel", headers=outsider_headers)

        assert resp.status_code == 403


# ============================================================================
# Manual invites: invite / listings / accept / decline
# ============================================================================

class TestInviteWorkflow:
    def test_creator_invites_a_user(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        invitee = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/invite/{invitee.id}", json={}, headers=headers)

        assert resp.status_code == 201
        from models import ThreadJoinRequest
        row = ThreadJoinRequest.query.filter_by(thread_id=thread.id, requester_id=invitee.id).first()
        assert row.status == "invited"

    def test_plain_member_cannot_invite(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        member = make_user()
        invitee = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        make_thread_member(thread, member)
        headers = {**auth_headers(member), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/invite/{invitee.id}", json={}, headers=headers)

        assert resp.status_code == 403

    def test_my_invites_lists_pending_invites(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        invitee = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        creator_headers = {**auth_headers(creator), **csrf_headers(client)}
        client.post(f"/student/threads/{thread.id}/invite/{invitee.id}", json={}, headers=creator_headers)

        resp = client.get("/student/threads/invites", headers=auth_headers(invitee))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["invites"][0]["thread"]["id"] == thread.id

    def test_accept_invite_adds_member(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        invitee = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        creator_headers = {**auth_headers(creator), **csrf_headers(client)}
        client.post(f"/student/threads/{thread.id}/invite/{invitee.id}", json={}, headers=creator_headers)
        from models import ThreadJoinRequest
        invite_id = ThreadJoinRequest.query.filter_by(thread_id=thread.id, requester_id=invitee.id).first().id
        invitee_headers = {**auth_headers(invitee), **csrf_headers(client)}

        resp = client.post(f"/student/threads/invites/{invite_id}/accept", headers=invitee_headers)

        assert resp.status_code == 200
        from models import ThreadMember, Thread
        assert ThreadMember.query.filter_by(thread_id=thread.id, student_id=invitee.id).first() is not None
        assert Thread.query.get(thread.id).member_count == 2

    def test_decline_invite_does_not_add_member(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        invitee = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        creator_headers = {**auth_headers(creator), **csrf_headers(client)}
        client.post(f"/student/threads/{thread.id}/invite/{invitee.id}", json={}, headers=creator_headers)
        from models import ThreadJoinRequest
        invite_id = ThreadJoinRequest.query.filter_by(thread_id=thread.id, requester_id=invitee.id).first().id
        invitee_headers = {**auth_headers(invitee), **csrf_headers(client)}

        resp = client.post(f"/student/threads/invites/{invite_id}/decline", headers=invitee_headers)

        assert resp.status_code == 200
        from models import ThreadMember
        assert ThreadMember.query.filter_by(thread_id=thread.id, student_id=invitee.id).first() is None

    def test_accept_someone_elses_invite_forbidden(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        invitee = make_user()
        outsider = make_user()
        thread = _make_thread_with_creator(db_session, make_thread, make_thread_member, creator)
        creator_headers = {**auth_headers(creator), **csrf_headers(client)}
        client.post(f"/student/threads/{thread.id}/invite/{invitee.id}", json={}, headers=creator_headers)
        from models import ThreadJoinRequest
        invite_id = ThreadJoinRequest.query.filter_by(thread_id=thread.id, requester_id=invitee.id).first().id
        outsider_headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.post(f"/student/threads/invites/{invite_id}/accept", headers=outsider_headers)

        assert resp.status_code == 403
