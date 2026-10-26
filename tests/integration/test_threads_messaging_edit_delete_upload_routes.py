"""
tests/integration/test_threads_messaging_edit_delete_upload_routes.py

The remaining untested endpoints in routes/student/threads/messaging.py:
edit_thread_message (PATCH), delete_thread_message (DELETE), and
upload_thread_attachment (POST .../upload). The read/send half of this
file was covered in a previous batch.

upload_thread_attachment's happy path makes a real Cloudinary network
call once validation passes, so only the guard clauses before that call
(membership, missing file, bad mime type, oversized file) are exercised
here -- consistent with this suite's established rule of never calling
real external services.
"""

import io

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# PATCH /threads/<id>/messages/<message_id>
# ============================================================================

class TestEditThreadMessage:
    def test_nonexistent_message_returns_404(
        self, client, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")

        resp = client.patch(
            f"/student/threads/{thread.id}/messages/999999",
            json={"text_content": "edited"},
            headers={**auth_headers(creator), **csrf_headers(client)},
        )

        assert resp.status_code == 404

    def test_message_from_different_thread_rejected(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread_a = make_thread(creator)
        thread_b = make_thread(creator)
        make_thread_member(thread_a, creator, role="creator")
        make_thread_member(thread_b, creator, role="creator")
        msg = make_thread_message(thread_b, creator, text_content="hi")

        resp = client.patch(
            f"/student/threads/{thread_a.id}/messages/{msg.id}",
            json={"text_content": "edited"},
            headers={**auth_headers(creator), **csrf_headers(client)},
        )

        assert resp.status_code == 400

    def test_non_owner_cannot_edit(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers, csrf_headers
    ):
        creator = make_user()
        other = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_member(thread, other)
        msg = make_thread_message(thread, creator, text_content="hi")

        resp = client.patch(
            f"/student/threads/{thread.id}/messages/{msg.id}",
            json={"text_content": "edited by other"},
            headers={**auth_headers(other), **csrf_headers(client)},
        )

        assert resp.status_code == 403

    def test_owner_can_edit_within_window(
        self, client, db_session, make_user, make_thread, make_thread_member, make_thread_message, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        msg = make_thread_message(thread, creator, text_content="original")

        resp = client.patch(
            f"/student/threads/{thread.id}/messages/{msg.id}",
            json={"text_content": "updated text"},
            headers={**auth_headers(creator), **csrf_headers(client)},
        )

        assert resp.status_code == 200
        db_session.refresh(msg)
        assert msg.text_content == "updated text"
        assert msg.is_edited is True

    def test_edit_window_expired_for_non_moderator(
        self, client, db_session, make_user, make_thread, make_thread_member, make_thread_message, auth_headers, csrf_headers
    ):
        import datetime

        creator = make_user()
        member = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_member(thread, member, role="member")
        old_time = datetime.datetime.utcnow() - datetime.timedelta(minutes=20)
        msg = make_thread_message(thread, member, text_content="old", sent_at=old_time)

        resp = client.patch(
            f"/student/threads/{thread.id}/messages/{msg.id}",
            json={"text_content": "too late"},
            headers={**auth_headers(member), **csrf_headers(client)},
        )

        assert resp.status_code == 403

    def test_empty_text_rejected(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        msg = make_thread_message(thread, creator, text_content="original")

        resp = client.patch(
            f"/student/threads/{thread.id}/messages/{msg.id}",
            json={"text_content": "   "},
            headers={**auth_headers(creator), **csrf_headers(client)},
        )

        assert resp.status_code == 400


# ============================================================================
# DELETE /threads/<id>/messages/<message_id>
# ============================================================================

class TestDeleteThreadMessage:
    def test_nonexistent_message_returns_404(
        self, client, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")

        resp = client.delete(
            f"/student/threads/{thread.id}/messages/999999",
            headers={**auth_headers(creator), **csrf_headers(client)},
        )

        assert resp.status_code == 404

    def test_non_member_cannot_delete(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers, csrf_headers
    ):
        creator = make_user()
        outsider = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        msg = make_thread_message(thread, creator, text_content="hi")

        resp = client.delete(
            f"/student/threads/{thread.id}/messages/{msg.id}",
            headers={**auth_headers(outsider), **csrf_headers(client)},
        )

        assert resp.status_code == 403

    def test_other_members_message_cannot_be_deleted_by_plain_member(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers, csrf_headers
    ):
        creator = make_user()
        member = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_member(thread, member, role="member")
        msg = make_thread_message(thread, creator, text_content="creator's message")

        resp = client.delete(
            f"/student/threads/{thread.id}/messages/{msg.id}",
            headers={**auth_headers(member), **csrf_headers(client)},
        )

        assert resp.status_code == 403

    def test_moderator_can_delete_others_message(
        self, client, db_session, make_user, make_thread, make_thread_member, make_thread_message, auth_headers, csrf_headers
    ):
        creator = make_user()
        moderator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_member(thread, moderator, role="moderator")
        msg = make_thread_message(thread, creator, text_content="creator's message")

        resp = client.delete(
            f"/student/threads/{thread.id}/messages/{msg.id}",
            headers={**auth_headers(moderator), **csrf_headers(client)},
        )

        assert resp.status_code == 200
        db_session.refresh(msg)
        assert msg.is_deleted is True
        assert msg.text_content == "[deleted]"

    def test_sender_can_delete_own_message(
        self, client, db_session, make_user, make_thread, make_thread_member, make_thread_message, auth_headers, csrf_headers
    ):
        creator = make_user()
        member = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_member(thread, member, role="member")
        msg = make_thread_message(thread, member, text_content="my own message")

        resp = client.delete(
            f"/student/threads/{thread.id}/messages/{msg.id}",
            headers={**auth_headers(member), **csrf_headers(client)},
        )

        assert resp.status_code == 200
        db_session.refresh(msg)
        assert msg.is_deleted is True


# ============================================================================
# POST /threads/<id>/messages/upload
# ============================================================================

class TestUploadThreadAttachment:
    def test_non_member_forbidden(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.post(
            f"/student/threads/{thread.id}/messages/upload",
            data={"file": (io.BytesIO(b"fake"), "doc.pdf")},
            headers=headers, content_type="multipart/form-data",
        )

        assert resp.status_code == 403

    def test_no_file_rejected(
        self, client, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(
            f"/student/threads/{thread.id}/messages/upload",
            data={}, headers=headers, content_type="multipart/form-data",
        )

        assert resp.status_code == 400

    def test_disallowed_mime_type_rejected(
        self, client, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(
            f"/student/threads/{thread.id}/messages/upload",
            data={"file": (io.BytesIO(b"#!/bin/sh\necho hi"), "script.sh")},
            headers=headers, content_type="multipart/form-data",
        )

        assert resp.status_code == 400

    def test_oversized_file_rejected(
        self, client, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        headers = {**auth_headers(creator), **csrf_headers(client)}
        oversized = io.BytesIO(b"x" * (25 * 1024 * 1024 + 1))

        resp = client.post(
            f"/student/threads/{thread.id}/messages/upload",
            data={"file": (oversized, "big.pdf")},
            headers=headers, content_type="multipart/form-data",
        )

        assert resp.status_code == 400
