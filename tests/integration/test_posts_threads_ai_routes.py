"""
tests/integration/test_posts_threads_ai_routes.py

routes/student/posts/ai.py and routes/student/threads/ai.py. The AI-call
half of posts/ai.py (ask_learnora_about_post) already has dedicated
coverage in test_ai_no_real_calls.py (no-provider 503 + canned-response
200, both via the established canned_ai_response fixture / direct
monkeypatch of services.ai_provider_service.call_ai_response, which this
suite never lets reach a real HTTP call). This batch covers what that
file doesn't: apply_refinement (no AI call at all -- pure DB update), and
both threads/ai.py routes (generate_meeting_notes, get_meeting_notes),
again mocking call_ai_response directly rather than using the
canned_ai_response fixture where the route needs a specific JSON-shaped
reply instead of a plain string.
"""

import json

import pytest

pytestmark = pytest.mark.integration


def _fake_meeting_notes_response(notes_dict):
    """Build a call_ai_response-shaped fake that returns `notes_dict` as
    a JSON string, the shape generate_meeting_notes() expects back."""
    def _fake(messages, needs_vision=False, max_retries=2, **kw):
        return json.dumps(notes_dict), {
            "attempts": 1, "provider": "fake_provider", "errors": []
        }
    return _fake


_VALID_NOTES = {
    "topics_discussed": ["Chapter 4"],
    "decisions_made": ["Meet again Friday"],
    "action_items": ["Read chapter 5"],
    "open_questions": [],
    "summary": "Short recap.",
}


# ============================================================================
# PATCH /posts/<id>/apply-refinement
# ============================================================================

class TestApplyRefinement:
    def test_requires_auth(self, client, make_user, make_post):
        author = make_user()
        post = make_post(author)

        # No CSRF token either, and the enforce_csrf before_request hook
        # runs before @token_required gets a chance to reject for missing
        # auth -- so this is a 403 (CSRF), not a 401 (auth). See
        # TestGenerateMeetingNotes.test_requires_auth below for the same
        # ordering on a different route.
        resp = client.patch(
            f"/student/posts/{post.id}/apply-refinement",
            json={"title": "New", "content": "New body"},
        )

        assert resp.status_code == 403

    def test_nonexistent_post_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.patch(
            "/student/posts/999999/apply-refinement",
            json={"title": "New", "content": "New body"},
            headers=headers,
        )

        assert resp.status_code == 404

    def test_only_author_can_apply_refinement(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author)
        other = make_user()
        headers = {**auth_headers(other), **csrf_headers(client)}

        resp = client.patch(
            f"/student/posts/{post.id}/apply-refinement",
            json={"title": "New", "content": "New body"},
            headers=headers,
        )

        assert resp.status_code == 403

    def test_successful_refinement_updates_post(
        self, client, db_session, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author, title="Old Title", text_content="Old body")
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.patch(
            f"/student/posts/{post.id}/apply-refinement",
            json={"title": "Refined Title", "content": "Refined body"},
            headers=headers,
        )

        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["data"]["title"] == "Refined Title"
        assert body["data"]["content"] == "Refined body"
        assert body["data"]["original"]["title"] == "Old Title"

        db_session.refresh(post)
        assert post.title == "Refined Title"
        assert post.text_content == "Refined body"
        assert post.edited_at is not None


# ============================================================================
# POST /threads/<id>/meeting-notes
# ============================================================================

class TestGenerateMeetingNotes:
    def test_requires_auth(self, client, make_user, make_thread):
        creator = make_user()
        thread = make_thread(creator)

        # Same CSRF-before-auth ordering as apply_refinement above: no
        # CSRF token present means the enforce_csrf hook rejects with 403
        # before @token_required's own 401 is ever reached.
        resp = client.post(f"/student/threads/{thread.id}/meeting-notes")

        assert resp.status_code == 403

    def test_non_member_forbidden(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/meeting-notes", headers=headers)

        assert resp.status_code == 403

    def test_nonexistent_thread_after_membership_check(
        self, client, make_user, auth_headers, csrf_headers
    ):
        # No membership row exists for a nonexistent thread either, so
        # this always hits the 403 branch first, same shape as the
        # already-documented get_thread_messages() bug in messaging.py.
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/threads/999999/meeting-notes", headers=headers)

        assert resp.status_code == 403

    def test_too_few_messages_rejected(
        self, client, make_user, make_thread, make_thread_member,
        make_thread_message, auth_headers, csrf_headers,
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_message(thread, creator, text_content="Hi")
        make_thread_message(thread, creator, text_content="Hey")
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/meeting-notes", headers=headers)

        assert resp.get_json()["status"] == "error"

    def test_ai_failure_returns_503(
        self, client, make_user, make_thread, make_thread_member,
        make_thread_message, auth_headers, csrf_headers, monkeypatch,
    ):
        from routes.student.threads import ai as threads_ai

        def _fake_call_ai_response(messages, needs_vision=False, **kw):
            return None, {"attempts": 2, "provider": None, "errors": ["no working provider available"]}

        monkeypatch.setattr(threads_ai, "call_ai_response", _fake_call_ai_response)

        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        for i in range(3):
            make_thread_message(thread, creator, text_content=f"Message {i}")
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/meeting-notes", headers=headers)

        assert resp.status_code == 503

    def test_successful_generation_persists_and_returns_notes(
        self, client, db_session, make_user, make_thread, make_thread_member,
        make_thread_message, auth_headers, csrf_headers, monkeypatch,
    ):
        from routes.student.threads import ai as threads_ai

        monkeypatch.setattr(
            threads_ai, "call_ai_response",
            _fake_meeting_notes_response(_VALID_NOTES),
        )

        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        for i in range(3):
            make_thread_message(thread, creator, text_content=f"Message {i}")
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(
            f"/student/threads/{thread.id}/meeting-notes",
            json={"message_range": 20},
            headers=headers,
        )

        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["data"]["notes"] == _VALID_NOTES
        assert body["data"]["message_count"] == 3
        assert "note_id" in body["data"]

        from models import ThreadMeetingNote
        row = ThreadMeetingNote.query.get(body["data"]["note_id"])
        assert row is not None
        assert row.notes_json == _VALID_NOTES

    def test_response_wrapped_in_code_fence_is_unwrapped(
        self, client, make_user, make_thread, make_thread_member,
        make_thread_message, auth_headers, csrf_headers, monkeypatch,
    ):
        from routes.student.threads import ai as threads_ai

        def _fake_call_ai_response(messages, needs_vision=False, **kw):
            fenced = "```json\n" + json.dumps(_VALID_NOTES) + "\n```"
            return fenced, {"attempts": 1, "provider": "fake_provider", "errors": []}

        monkeypatch.setattr(threads_ai, "call_ai_response", _fake_call_ai_response)

        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        for i in range(3):
            make_thread_message(thread, creator, text_content=f"Message {i}")
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/meeting-notes", headers=headers)

        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["data"]["notes"] == _VALID_NOTES

    def test_malformed_json_response_returns_error(
        self, client, make_user, make_thread, make_thread_member,
        make_thread_message, auth_headers, csrf_headers, monkeypatch,
    ):
        from routes.student.threads import ai as threads_ai

        def _fake_call_ai_response(messages, needs_vision=False, **kw):
            return "not valid json at all", {
                "attempts": 1, "provider": "fake_provider", "errors": []
            }

        monkeypatch.setattr(threads_ai, "call_ai_response", _fake_call_ai_response)

        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        for i in range(3):
            make_thread_message(thread, creator, text_content=f"Message {i}")
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/meeting-notes", headers=headers)

        assert resp.get_json()["status"] == "error"

    def test_non_numeric_message_range_is_handled_gracefully(
        self, client, make_user, make_thread, make_thread_member,
        make_thread_message, auth_headers, csrf_headers,
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        for i in range(3):
            make_thread_message(thread, creator, text_content=f"Message {i}")
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(
            f"/student/threads/{thread.id}/meeting-notes",
            json={"message_range": "not-a-number"},
            headers=headers,
        )

        assert resp.status_code in (400, 422)


# ============================================================================
# GET /threads/<id>/meeting-notes
# ============================================================================

class TestGetMeetingNotes:
    def test_requires_auth(self, client, make_user, make_thread):
        creator = make_user()
        thread = make_thread(creator)

        resp = client.get(f"/student/threads/{thread.id}/meeting-notes")

        assert resp.status_code == 401

    def test_non_member_forbidden(self, client, make_user, make_thread, auth_headers):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()

        resp = client.get(
            f"/student/threads/{thread.id}/meeting-notes",
            headers=auth_headers(outsider),
        )

        assert resp.status_code == 403

    def test_returns_notes_most_recent_first(
        self, client, db_session, make_user, make_thread, make_thread_member,
        auth_headers,
    ):
        from models import ThreadMeetingNote
        import datetime

        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")

        older = ThreadMeetingNote(
            thread_id=thread.id, created_by=creator.id, message_range=50,
            message_count=10, notes_json={"summary": "older"},
            created_at=datetime.datetime.utcnow() - datetime.timedelta(hours=2),
        )
        newer = ThreadMeetingNote(
            thread_id=thread.id, created_by=creator.id, message_range=50,
            message_count=10, notes_json={"summary": "newer"},
            created_at=datetime.datetime.utcnow(),
        )
        db_session.add_all([older, newer])
        db_session.commit()

        resp = client.get(
            f"/student/threads/{thread.id}/meeting-notes",
            headers=auth_headers(creator),
        )

        assert resp.status_code == 200
        notes = resp.get_json()["data"]["notes"]
        assert len(notes) == 2
        assert notes[0]["notes_json"]["summary"] == "newer"

    def test_limit_param_respected(
        self, client, db_session, make_user, make_thread, make_thread_member,
        auth_headers,
    ):
        from models import ThreadMeetingNote

        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")

        for i in range(3):
            db_session.add(ThreadMeetingNote(
                thread_id=thread.id, created_by=creator.id, message_range=50,
                message_count=10, notes_json={"summary": f"note{i}"},
            ))
        db_session.commit()

        resp = client.get(
            f"/student/threads/{thread.id}/meeting-notes?limit=2",
            headers=auth_headers(creator),
        )

        assert len(resp.get_json()["data"]["notes"]) == 2
