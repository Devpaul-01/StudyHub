"""
tests/integration/test_learnora_conversation_routes.py

routes/student/learnora/__init__.py had zero test coverage. This batch
covers everything except the big POST /learnora/api/chat route (which
gets its own dedicated batch given its size and the number of distinct
code paths it has): GET /learnora/ (page render), POST
/learnora/api/conversation/new, GET /learnora/api/conversation/list, GET
/learnora/api/conversations/<id>, DELETE /learnora/api/conversation/<id>,
POST /learnora/api/conversation/<id>/clear, PUT
/learnora/api/conversation/<id>/title, POST /learnora/api/chat/reset-title,
POST /learnora/api/upload/attachment, and GET /learnora/api/stats.

create_conversation() and reset_conversation_title() both call AI
provider functions (generate_conversation_title / provider_manager) --
reset_conversation_title's call is synchronous and in the request path,
so it's mocked directly; create_conversation's title-upgrade call runs
on a background daemon thread it fires and forgets, so this suite
doesn't wait on it or assert its result -- only the synchronous response
(conversation_id, the instant truncated-fallback title) is checked, per
this suite's "AI providers are never actually called" rule (see
test_ai_no_real_calls.py's own docstring).
"""

import io

import pytest

pytestmark = pytest.mark.integration


def _conversation(db_session, user, **overrides):
    from models import AIConversation

    defaults = dict(
        user_id=user.id,
        title="New Conversation",
        messages=[],
        total_messages=0,
        tokens_used=0,
        is_archived=False,
    )
    defaults.update(overrides)
    conv = AIConversation(**defaults)
    db_session.add(conv)
    db_session.flush()
    return conv


# ============================================================================
# GET /learnora/
# ============================================================================

class TestLearnoraPage:
    def test_requires_auth(self, client):
        resp = client.get("/student/learnora/")

        assert resp.status_code == 401

    def test_renders_for_authenticated_user(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/learnora/", headers=auth_headers(user))

        assert resp.status_code == 200


# ============================================================================
# POST /learnora/api/conversation/new
# ============================================================================

class TestCreateConversation:
    def test_requires_auth(self, client):
        resp = client.post("/student/learnora/api/conversation/new")

        assert resp.status_code == 403  # CSRF-before-auth, same ordering as Batch 43

    def test_creates_empty_conversation(self, client, db_session, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/conversation/new", json={}, headers=headers
        )

        assert resp.status_code == 201, resp.get_json()
        data = resp.get_json()["data"]
        assert data["initial_message"] is None
        assert data["title"] == "New Conversation"

        from models import AIConversation
        conv = AIConversation.query.get(data["conversation_id"])
        assert conv.total_messages == 0

    def test_seeds_initial_message_and_truncated_title(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}
        long_msg = "x" * 100

        resp = client.post(
            "/student/learnora/api/conversation/new",
            json={"initial_message": long_msg},
            headers=headers,
        )

        assert resp.status_code == 201
        data = resp.get_json()["data"]
        assert data["initial_message"] == long_msg
        assert data["title"].endswith("...")
        assert len(data["title"]) == 60

        from models import AIConversation
        conv = AIConversation.query.get(data["conversation_id"])
        assert conv.total_messages == 1
        assert conv.messages[0]["content"] == long_msg


# ============================================================================
# GET /learnora/api/conversation/list
# ============================================================================

class TestGetConversations:
    def test_requires_auth(self, client):
        resp = client.get("/student/learnora/api/conversation/list")

        assert resp.status_code == 401

    def test_only_own_non_archived_conversations_returned(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        other = make_user()
        mine = _conversation(db_session, user, title="Mine")
        _conversation(db_session, user, title="Archived", is_archived=True)
        _conversation(db_session, other, title="Not Mine")

        resp = client.get(
            "/student/learnora/api/conversation/list", headers=auth_headers(user)
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["count"] == 1
        assert body["data"][0]["conversation_id"] == mine.id

    def test_limit_capped_at_100(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/learnora/api/conversation/list?limit=5000",
            headers=auth_headers(user),
        )

        assert resp.status_code == 200


# ============================================================================
# GET /learnora/api/conversations/<id>
# ============================================================================

class TestGetConversation:
    def test_requires_auth(self, client, db_session, make_user):
        user = make_user()
        conv = _conversation(db_session, user)

        resp = client.get(f"/student/learnora/api/conversations/{conv.id}")

        assert resp.status_code == 401

    def test_nonexistent_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/learnora/api/conversations/999999", headers=auth_headers(user)
        )

        assert resp.status_code == 404

    def test_other_users_conversation_is_not_found(
        self, client, db_session, make_user, auth_headers
    ):
        owner = make_user()
        conv = _conversation(db_session, owner)
        other = make_user()

        resp = client.get(
            f"/student/learnora/api/conversations/{conv.id}",
            headers=auth_headers(other),
        )

        assert resp.status_code == 404

    def test_pagination_returns_most_recent_page_first(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        messages = [
            {"role": "user", "content": f"msg{i}", "timestamp": "2024-01-01T00:00:00"}
            for i in range(5)
        ]
        conv = _conversation(db_session, user, messages=messages, total_messages=5)

        resp = client.get(
            f"/student/learnora/api/conversations/{conv.id}?per_page=2",
            headers=auth_headers(user),
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert len(data["messages"]) == 2
        # Most recent page first -> last two messages (msg3, msg4).
        assert [m["content"] for m in data["messages"]] == ["msg3", "msg4"]
        assert data["pagination"]["has_more"] is True


# ============================================================================
# DELETE /learnora/api/conversation/<id>
# ============================================================================

class TestDeleteConversation:
    def test_requires_auth(self, client, db_session, make_user):
        user = make_user()
        conv = _conversation(db_session, user)

        resp = client.delete(f"/student/learnora/api/conversation/{conv.id}")

        assert resp.status_code == 403  # CSRF-before-auth

    def test_nonexistent_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete(
            "/student/learnora/api/conversation/999999", headers=headers
        )

        assert resp.status_code == 404

    def test_archives_conversation(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        conv = _conversation(db_session, user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete(
            f"/student/learnora/api/conversation/{conv.id}", headers=headers
        )

        assert resp.status_code == 200
        db_session.refresh(conv)
        assert conv.is_archived is True

    def test_already_archived_returns_409(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        conv = _conversation(db_session, user, is_archived=True)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete(
            f"/student/learnora/api/conversation/{conv.id}", headers=headers
        )

        assert resp.status_code == 409


# ============================================================================
# POST /learnora/api/conversation/<id>/clear
# ============================================================================

class TestClearConversation:
    def test_requires_auth(self, client, db_session, make_user):
        user = make_user()
        conv = _conversation(db_session, user)

        resp = client.post(f"/student/learnora/api/conversation/{conv.id}/clear")

        assert resp.status_code == 403

    def test_nonexistent_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/conversation/999999/clear", headers=headers
        )

        assert resp.status_code == 404

    def test_resets_conversation_fields(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        conv = _conversation(
            db_session, user,
            messages=[{"role": "user", "content": "hi"}],
            total_messages=1,
            tokens_used=42,
            title="Old Title",
        )
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            f"/student/learnora/api/conversation/{conv.id}/clear", headers=headers
        )

        assert resp.status_code == 200
        db_session.refresh(conv)
        assert conv.messages == []
        assert conv.total_messages == 0
        assert conv.tokens_used == 0
        assert conv.title == "New Conversation"


# ============================================================================
# PUT /learnora/api/conversation/<id>/title
# ============================================================================

class TestUpdateConversationTitle:
    def test_requires_auth(self, client, db_session, make_user):
        user = make_user()
        conv = _conversation(db_session, user)

        resp = client.put(
            f"/student/learnora/api/conversation/{conv.id}/title",
            json={"title": "New"},
        )

        assert resp.status_code == 403

    def test_empty_title_rejected(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        conv = _conversation(db_session, user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            f"/student/learnora/api/conversation/{conv.id}/title",
            json={"title": "   "},
            headers=headers,
        )

        assert resp.status_code == 400

    def test_too_long_title_rejected(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        conv = _conversation(db_session, user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            f"/student/learnora/api/conversation/{conv.id}/title",
            json={"title": "x" * 201},
            headers=headers,
        )

        assert resp.status_code == 400

    def test_nonexistent_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            "/student/learnora/api/conversation/999999/title",
            json={"title": "New"},
            headers=headers,
        )

        assert resp.status_code == 404

    def test_successful_update(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        conv = _conversation(db_session, user, title="Old")
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            f"/student/learnora/api/conversation/{conv.id}/title",
            json={"title": "Brand New Title"},
            headers=headers,
        )

        assert resp.status_code == 200
        db_session.refresh(conv)
        assert conv.title == "Brand New Title"


# ============================================================================
# POST /learnora/api/chat/reset-title
# ============================================================================

class TestResetConversationTitle:
    def test_requires_auth(self, client):
        resp = client.post("/student/learnora/api/chat/reset-title")

        assert resp.status_code == 403

    def test_missing_conversation_id_rejected(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat/reset-title", json={}, headers=headers
        )

        assert resp.status_code == 400

    def test_non_integer_conversation_id_rejected(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat/reset-title",
            json={"conversation_id": "not-a-number"},
            headers=headers,
        )

        assert resp.status_code == 400

    def test_nonexistent_conversation_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat/reset-title",
            json={"conversation_id": 999999},
            headers=headers,
        )

        assert resp.status_code == 404

    def test_empty_conversation_rejected(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        conv = _conversation(db_session, user, messages=[])
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat/reset-title",
            json={"conversation_id": conv.id},
            headers=headers,
        )

        assert resp.status_code == 400

    def test_no_user_messages_rejected(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        conv = _conversation(
            db_session, user,
            messages=[{"role": "assistant", "content": "hi there"}],
            total_messages=1,
        )
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat/reset-title",
            json={"conversation_id": conv.id},
            headers=headers,
        )

        assert resp.status_code == 400

    def test_successful_title_reset(
        self, client, db_session, make_user, auth_headers, csrf_headers, monkeypatch
    ):
        import routes.student.learnora as learnora_module

        monkeypatch.setattr(
            learnora_module.provider_manager, "get_working_provider",
            lambda needs_vision=False: "fake_provider",
        )
        monkeypatch.setattr(
            learnora_module, "generate_conversation_title",
            lambda msg, provider: "AI Generated Title",
        )

        user = make_user()
        conv = _conversation(
            db_session, user,
            messages=[{"role": "user", "content": "explain recursion"}],
            total_messages=1,
            title="Old Title",
        )
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat/reset-title",
            json={"conversation_id": conv.id},
            headers=headers,
        )

        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["data"]["title"] == "AI Generated Title"
        db_session.refresh(conv)
        assert conv.title == "AI Generated Title"


# ============================================================================
# POST /learnora/api/upload/attachment
# ============================================================================

class TestUploadAttachment:
    def test_requires_auth(self, client):
        resp = client.post("/student/learnora/api/upload/attachment")

        assert resp.status_code == 403

    def test_no_file_provided_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/upload/attachment", data={}, headers=headers
        )

        assert resp.status_code == 400

    def test_unsupported_extension_rejected(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/upload/attachment",
            data={"file": (io.BytesIO(b"binary junk"), "archive.zip")},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 415

    def test_empty_file_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/upload/attachment",
            data={"file": (io.BytesIO(b""), "notes.txt")},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 400

    def test_oversized_file_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/upload/attachment",
            data={"file": (io.BytesIO(b"x" * (10 * 1024 * 1024 + 1)), "big.txt")},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 413


# ============================================================================
# GET /learnora/api/stats
# ============================================================================

class TestGetStats:
    def test_requires_auth(self, client):
        resp = client.get("/student/learnora/api/stats")

        assert resp.status_code == 401

    def test_no_quota_row_uses_default_limit(self, client, make_user, auth_headers):
        user = make_user(role="student")

        resp = client.get("/student/learnora/api/stats", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["user_quota"]["daily_limit"] == 50
        assert data["user_quota"]["daily_used"] == 0
        assert data["user_quota"]["remaining"] == 50
        assert "provider_stats" not in data

    def test_existing_quota_row_reflected(self, client, db_session, make_user, auth_headers):
        from models import AIUsageQuota

        user = make_user(role="student")
        quota = AIUsageQuota(
            user_id=user.id, daily_messages_limit=20, daily_messages_used=5
        )
        db_session.add(quota)
        db_session.commit()

        resp = client.get("/student/learnora/api/stats", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert data["user_quota"]["daily_limit"] == 20
        assert data["user_quota"]["daily_used"] == 5
        assert data["user_quota"]["remaining"] == 15

    def test_admin_sees_provider_stats(self, client, make_user, auth_headers):
        admin = make_user(role="admin")

        resp = client.get("/student/learnora/api/stats", headers=auth_headers(admin))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "provider_stats" in data

    def test_regular_student_does_not_see_provider_stats(self, client, make_user, auth_headers):
        user = make_user(role="student")

        resp = client.get("/student/learnora/api/stats", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert "provider_stats" not in data
