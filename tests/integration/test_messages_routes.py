"""
tests/integration/test_messages_routes.py

routes/student/messages.py has 16 endpoints and previously had zero
tests at any level. This batch covers the core message lifecycle and
permission surface: delete-for-everyone/delete-for-me, clear
conversation, mark-read (single and bulk), unread count,
can-message permission checks, block/unblock messaging, and reporting
a message. Conversation listing/detail (get_conversations,
get_conversation_messages — both large, denormalized queries),
shared-media, and file upload are a separate, larger area left for a
future pass.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# DELETE /messages/<id>/delete-for-everyone
# ============================================================================

class TestDeleteForEveryone:
    def test_sender_can_delete_within_window(
        self, client, make_user, make_message, auth_headers, csrf_headers
    ):
        sender = make_user()
        receiver = make_user()
        msg = make_message(sender, receiver, body="oops")
        headers = {**auth_headers(sender), **csrf_headers(client)}

        resp = client.delete(f"/student/messages/{msg.id}/delete-for-everyone", headers=headers)

        assert resp.status_code == 200
        from models import Message
        row = Message.query.get(msg.id)
        assert row.deleted_by_sender is True
        assert row.deleted_by_receiver is True
        assert row.body == "[Message deleted]"

    def test_receiver_cannot_delete_for_everyone(
        self, client, make_user, make_message, auth_headers, csrf_headers
    ):
        sender = make_user()
        receiver = make_user()
        msg = make_message(sender, receiver)
        headers = {**auth_headers(receiver), **csrf_headers(client)}

        resp = client.delete(f"/student/messages/{msg.id}/delete-for-everyone", headers=headers)

        assert resp.status_code == 400

    def test_delete_window_expired_returns_403(
        self, client, make_user, make_message, auth_headers, csrf_headers
    ):
        sender = make_user()
        receiver = make_user()
        old_time = datetime.datetime.utcnow() - datetime.timedelta(minutes=10)
        msg = make_message(sender, receiver, sent_at=old_time)
        headers = {**auth_headers(sender), **csrf_headers(client)}

        resp = client.delete(f"/student/messages/{msg.id}/delete-for-everyone", headers=headers)

        assert resp.status_code == 403

    def test_nonexistent_message_returns_400(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete("/student/messages/999999/delete-for-everyone", headers=headers)

        assert resp.status_code == 400


# ============================================================================
# DELETE /messages/<id>/delete-for-me
# ============================================================================

class TestDeleteForMe:
    def test_sender_deleting_sets_deleted_by_sender_only(
        self, client, make_user, make_message, auth_headers, csrf_headers
    ):
        sender = make_user()
        receiver = make_user()
        msg = make_message(sender, receiver)
        headers = {**auth_headers(sender), **csrf_headers(client)}

        resp = client.delete(f"/student/messages/{msg.id}/delete-for-me", headers=headers)

        assert resp.status_code == 200
        from models import Message
        row = Message.query.get(msg.id)
        assert row.deleted_by_sender is True
        assert row.deleted_by_receiver is False

    def test_receiver_deleting_sets_deleted_by_receiver_only(
        self, client, make_user, make_message, auth_headers, csrf_headers
    ):
        sender = make_user()
        receiver = make_user()
        msg = make_message(sender, receiver)
        headers = {**auth_headers(receiver), **csrf_headers(client)}

        resp = client.delete(f"/student/messages/{msg.id}/delete-for-me", headers=headers)

        assert resp.status_code == 200
        from models import Message
        row = Message.query.get(msg.id)
        assert row.deleted_by_receiver is True
        assert row.deleted_by_sender is False

    def test_unrelated_user_forbidden(
        self, client, make_user, make_message, auth_headers, csrf_headers
    ):
        sender = make_user()
        receiver = make_user()
        outsider = make_user()
        msg = make_message(sender, receiver)
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.delete(f"/student/messages/{msg.id}/delete-for-me", headers=headers)

        assert resp.status_code == 400


# ============================================================================
# DELETE /messages/clear/<partner_id>
# ============================================================================

class TestClearConversation:
    def test_clears_both_directions(self, client, make_user, make_message, auth_headers, csrf_headers):
        me = make_user()
        partner = make_user()
        m1 = make_message(me, partner)
        m2 = make_message(partner, me)
        headers = {**auth_headers(me), **csrf_headers(client)}

        resp = client.delete(f"/student/messages/clear/{partner.id}", headers=headers)

        assert resp.status_code == 200
        from models import Message
        assert Message.query.get(m1.id).deleted_by_sender is True
        assert Message.query.get(m2.id).deleted_by_receiver is True


# ============================================================================
# mark-read / mark-all-read / unread-count
# ============================================================================

class TestMarkRead:
    def test_receiver_marks_message_read(
        self, client, make_user, make_message, auth_headers, csrf_headers
    ):
        sender = make_user()
        receiver = make_user()
        msg = make_message(sender, receiver, is_read=False)
        headers = {**auth_headers(receiver), **csrf_headers(client)}

        resp = client.post(f"/student/messages/{msg.id}/mark-read", headers=headers)

        assert resp.status_code == 200
        from models import Message
        assert Message.query.get(msg.id).is_read is True

    def test_sender_cannot_mark_own_sent_message_read(
        self, client, make_user, make_message, auth_headers, csrf_headers
    ):
        sender = make_user()
        receiver = make_user()
        msg = make_message(sender, receiver)
        headers = {**auth_headers(sender), **csrf_headers(client)}

        resp = client.post(f"/student/messages/{msg.id}/mark-read", headers=headers)

        assert resp.status_code == 403

    def test_nonexistent_message_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/messages/999999/mark-read", headers=headers)

        assert resp.status_code == 404

    def test_mark_all_read_marks_every_unread_message_from_partner(
        self, client, make_user, make_message, auth_headers, csrf_headers
    ):
        me = make_user()
        partner = make_user()
        m1 = make_message(partner, me, is_read=False)
        m2 = make_message(partner, me, is_read=False)
        headers = {**auth_headers(me), **csrf_headers(client)}

        resp = client.post(f"/student/messages/mark-all-read/{partner.id}", headers=headers)

        assert resp.status_code == 200
        from models import Message
        assert Message.query.get(m1.id).is_read is True
        assert Message.query.get(m2.id).is_read is True

    def test_unread_count_reflects_unread_messages(
        self, client, make_user, make_message, auth_headers
    ):
        me = make_user()
        partner = make_user()
        make_message(partner, me, is_read=False)
        make_message(partner, me, is_read=True)

        resp = client.get("/student/messages/unread-count", headers=auth_headers(me))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["unread_count"] == 1

    def test_unread_count_excludes_deleted_by_receiver(
        self, client, make_user, make_message, auth_headers
    ):
        me = make_user()
        partner = make_user()
        make_message(partner, me, is_read=False, deleted_by_receiver=True)

        resp = client.get("/student/messages/unread-count", headers=auth_headers(me))

        assert resp.get_json()["data"]["unread_count"] == 0


# ============================================================================
# GET /messages/can-message/<user_id>
# ============================================================================

class TestCanMessage:
    def test_cannot_message_self(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(f"/student/messages/can-message/{user.id}", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["can_message"] is False

    def test_nonexistent_target_user(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/messages/can-message/999999", headers=auth_headers(user))

        body = resp.get_json()["data"]
        assert body["can_message"] is False
        assert body["reason"] == "User not found"

    def test_not_connected_users_cannot_message(self, client, make_user, auth_headers):
        me = make_user()
        other = make_user()

        resp = client.get(f"/student/messages/can-message/{other.id}", headers=auth_headers(me))

        assert resp.get_json()["data"]["can_message"] is False

    def test_connected_users_can_message(self, client, db_session, make_user, auth_headers):
        me = make_user()
        other = make_user()
        from models import Connection
        conn = Connection(requester_id=me.id, receiver_id=other.id, status="accepted")
        db_session.add(conn)
        db_session.flush()

        resp = client.get(f"/student/messages/can-message/{other.id}", headers=auth_headers(me))

        assert resp.get_json()["data"]["can_message"] is True

    def test_blocked_users_cannot_message(self, client, db_session, make_user, auth_headers):
        me = make_user()
        other = make_user()
        from models import Connection
        conn = Connection(requester_id=me.id, receiver_id=other.id, status="blocked")
        db_session.add(conn)
        db_session.flush()

        resp = client.get(f"/student/messages/can-message/{other.id}", headers=auth_headers(me))

        body = resp.get_json()["data"]
        assert body["can_message"] is False
        assert body["reason"] == "User is blocked"


# ============================================================================
# POST /messages/block/<id> and /messages/unblock/<id>
# ============================================================================

class TestBlockUnblock:
    def test_block_self_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(f"/student/messages/block/{user.id}", headers=headers)

        assert resp.status_code == 400

    def test_block_then_cannot_message(self, client, make_user, auth_headers, csrf_headers):
        me = make_user()
        other = make_user()
        headers = {**auth_headers(me), **csrf_headers(client)}

        resp = client.post(f"/student/messages/block/{other.id}", headers=headers)
        assert resp.status_code == 200

        check = client.get(f"/student/messages/can-message/{other.id}", headers=auth_headers(me))
        assert check.get_json()["data"]["can_message"] is False

    def test_unblock_restores_messaging(self, client, make_user, auth_headers, csrf_headers):
        me = make_user()
        other = make_user()
        headers = {**auth_headers(me), **csrf_headers(client)}
        client.post(f"/student/messages/block/{other.id}", headers=headers)

        resp = client.post(f"/student/messages/unblock/{other.id}", headers=headers)

        assert resp.status_code == 200
        from models import Connection
        conn = Connection.query.filter_by(requester_id=me.id, receiver_id=other.id).first()
        assert conn.status == "accepted"


# ============================================================================
# POST /messages/report/<id>
# ============================================================================

class TestReportMessage:
    def test_receiver_can_report(self, client, make_user, make_message, auth_headers, csrf_headers):
        sender = make_user()
        receiver = make_user()
        msg = make_message(sender, receiver)
        headers = {**auth_headers(receiver), **csrf_headers(client)}

        resp = client.post(
            f"/student/messages/report/{msg.id}", json={"reason": "spam"}, headers=headers
        )

        assert resp.status_code == 200

    def test_sender_cannot_report_own_message(
        self, client, make_user, make_message, auth_headers, csrf_headers
    ):
        sender = make_user()
        receiver = make_user()
        msg = make_message(sender, receiver)
        headers = {**auth_headers(sender), **csrf_headers(client)}

        resp = client.post(
            f"/student/messages/report/{msg.id}", json={"reason": "spam"}, headers=headers
        )

        assert resp.status_code == 403

    def test_missing_reason_rejected(
        self, client, make_user, make_message, auth_headers, csrf_headers
    ):
        sender = make_user()
        receiver = make_user()
        msg = make_message(sender, receiver)
        headers = {**auth_headers(receiver), **csrf_headers(client)}

        resp = client.post(f"/student/messages/report/{msg.id}", json={}, headers=headers)

        assert resp.status_code == 400

    def test_nonexistent_message_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/messages/report/999999", json={"reason": "spam"}, headers=headers
        )

        assert resp.status_code == 404
