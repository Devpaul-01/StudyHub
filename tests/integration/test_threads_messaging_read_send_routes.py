"""
tests/integration/test_threads_messaging_read_send_routes.py

routes/student/threads/messaging.py had zero tests at any level. This
batch covers the read/send surface: get_thread_messages (GET
/messages), send_thread_message (POST /messages, which delegates to
services.thread_message_service.create_thread_message), search_thread_messages,
and get_pinned_messages. Edit/delete/upload are left for a following
batch.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET /threads/<id>/messages
# ============================================================================

class TestGetThreadMessages:
    def test_non_member_forbidden(self, client, make_user, make_thread, auth_headers):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()

        resp = client.get(
            f"/student/threads/{thread.id}/messages", headers=auth_headers(outsider)
        )

        assert resp.status_code == 403

    def test_nonexistent_thread_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/threads/999999/messages", headers=auth_headers(user)
        )

        assert resp.status_code == 404

    def test_lists_messages_oldest_to_newest(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_message(thread, creator, text_content="first")
        make_thread_message(thread, creator, text_content="second")

        resp = client.get(
            f"/student/threads/{thread.id}/messages", headers=auth_headers(creator)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        texts = [m["text_content"] for m in data["messages"]]
        assert texts == ["first", "second"]
        assert data["has_more"] is False

    def test_pinned_messages_included(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_message(thread, creator, text_content="pin me", is_pinned=True)

        resp = client.get(
            f"/student/threads/{thread.id}/messages", headers=auth_headers(creator)
        )

        data = resp.get_json()["data"]
        assert len(data["pinned_messages"]) == 1
        assert data["pinned_messages"][0]["text_content"] == "pin me"

    def test_deleted_messages_excluded(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_message(thread, creator, text_content="gone", is_deleted=True)

        resp = client.get(
            f"/student/threads/{thread.id}/messages", headers=auth_headers(creator)
        )

        data = resp.get_json()["data"]
        assert data["messages"] == []

    def test_limit_and_has_more(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        for i in range(3):
            make_thread_message(thread, creator, text_content=f"msg {i}")

        resp = client.get(
            f"/student/threads/{thread.id}/messages?limit=2", headers=auth_headers(creator)
        )

        data = resp.get_json()["data"]
        assert len(data["messages"]) == 2
        assert data["has_more"] is True


# ============================================================================
# POST /threads/<id>/messages
# ============================================================================

class TestSendThreadMessage:
    def test_non_member_forbidden(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()

        resp = client.post(
            f"/student/threads/{thread.id}/messages",
            json={"text_content": "hi"},
            headers={**auth_headers(outsider), **csrf_headers(client)},
        )

        assert resp.status_code == 403

    def test_closed_thread_forbidden(
        self, client, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator, is_open=False)
        make_thread_member(thread, creator, role="creator")

        resp = client.post(
            f"/student/threads/{thread.id}/messages",
            json={"text_content": "hi"},
            headers={**auth_headers(creator), **csrf_headers(client)},
        )

        assert resp.status_code == 403

    def test_empty_message_rejected(
        self, client, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")

        resp = client.post(
            f"/student/threads/{thread.id}/messages",
            json={"text_content": "   "},
            headers={**auth_headers(creator), **csrf_headers(client)},
        )

        assert resp.status_code == 400

    def test_successful_send_creates_message(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        from models import ThreadMessage

        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")

        resp = client.post(
            f"/student/threads/{thread.id}/messages",
            json={"text_content": "Hello everyone"},
            headers={**auth_headers(creator), **csrf_headers(client)},
        )

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["status"] == "success"
        msg_id = body["data"]["message_id"]
        msg = db_session.get(ThreadMessage, msg_id)
        assert msg.text_content == "Hello everyone"
        assert msg.sender_id == creator.id

    def test_invalid_reply_to_id_silently_dropped(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        from models import ThreadMessage

        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")

        resp = client.post(
            f"/student/threads/{thread.id}/messages",
            json={"text_content": "reply to nothing", "reply_to_id": 999999},
            headers={**auth_headers(creator), **csrf_headers(client)},
        )

        assert resp.status_code == 201
        msg = db_session.get(ThreadMessage, resp.get_json()["data"]["message_id"])
        assert msg.reply_to_id is None


# ============================================================================
# GET /threads/<id>/messages/search
# ============================================================================

class TestSearchThreadMessages:
    def test_non_member_forbidden(self, client, make_user, make_thread, auth_headers):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()

        resp = client.get(
            f"/student/threads/{thread.id}/messages/search?q=hi", headers=auth_headers(outsider)
        )

        assert resp.status_code == 403

    def test_short_query_rejected(
        self, client, make_user, make_thread, make_thread_member, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")

        resp = client.get(
            f"/student/threads/{thread.id}/messages/search?q=a", headers=auth_headers(creator)
        )

        assert resp.status_code == 400

    def test_matching_messages_returned(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_message(thread, creator, text_content="let's discuss calculus homework")
        make_thread_message(thread, creator, text_content="unrelated message")

        resp = client.get(
            f"/student/threads/{thread.id}/messages/search?q=calculus",
            headers=auth_headers(creator),
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert "calculus" in data["results"][0]["text_content"]


# ============================================================================
# GET /threads/<id>/messages/pinned
# ============================================================================

class TestGetPinnedMessages:
    def test_non_member_forbidden(self, client, make_user, make_thread, auth_headers):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()

        resp = client.get(
            f"/student/threads/{thread.id}/messages/pinned", headers=auth_headers(outsider)
        )

        assert resp.status_code == 403

    def test_only_pinned_non_deleted_returned(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_message(thread, creator, text_content="pinned one", is_pinned=True)
        make_thread_message(thread, creator, text_content="not pinned", is_pinned=False)
        make_thread_message(
            thread, creator, text_content="pinned but deleted", is_pinned=True, is_deleted=True
        )

        resp = client.get(
            f"/student/threads/{thread.id}/messages/pinned", headers=auth_headers(creator)
        )

        data = resp.get_json()["data"]["pinned_messages"]
        assert len(data) == 1
        assert data[0]["text_content"] == "pinned one"
