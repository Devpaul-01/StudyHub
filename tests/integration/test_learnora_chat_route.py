"""
tests/integration/test_learnora_chat_route.py

Covers POST /learnora/api/chat (routes/student/learnora/__init__.py's
chat()), the one route Batch 44 deliberately left out given its size
(~490 lines) and number of distinct code paths: conversation
resolution/validation, the 500-message hard cap, daily quota
enforcement, the seeded-message reuse path, the attachment count cap,
and -- for the "a provider is available" scenarios -- the SSE streaming
response itself.

This test env has no AI provider API keys configured, so
provider_manager.get_working_provider() genuinely returns None for real
(no mocking needed) for the "no provider available" 503 path, matching
test_ai_no_real_calls.py's own documented behavior. For the scenarios
that need a *successful* streaming response, routes.student.learnora's
module-level StudyAssistant binding and provider_manager.get_working_provider
are monkeypatched directly -- StudyAssistant is imported with
`from services.ai_provider_service import StudyAssistant` at module level
in routes/student/learnora/__init__.py, so (same lesson as Batch 43's
threads/ai.py) the mock target is the learnora module's own local name,
not the ai_provider_service module. No real HTTP call is ever made.
"""

import json

import pytest

pytestmark = pytest.mark.integration


def _load_conversation_raw(conv_id):
    """Re-fetch an AIConversation's row via a brand-new raw connection,
    bypassing the ORM session/identity-map entirely.

    chat()'s streaming generate() persists the assistant reply through
    its own nested transaction (db.session.begin_nested() + an explicit
    db.session.commit() inside it), on top of the main request already
    having expunge()'d the conversation instance beforehand. By the time
    the test client finishes consuming the streamed response body, the
    conversation instance this test created (and chat()'s own, the same
    object within this shared scoped session) ends up detached in a way
    plain db_session.refresh()/Model.query.get() can't cleanly recover
    from in this environment (confirmed: both raise
    sqlalchemy.orm.exc.DetachedInstanceError on an attribute access right
    after the request, even after an explicit db.session.remove()).
    Sidestepping the ORM session for this specific read avoids chasing
    that scoping interaction further -- it isn't what these tests are
    about.
    """
    from extensions import db

    with db.engine.connect() as conn:
        row = conn.execute(
            db.text(
                "SELECT total_messages, tokens_used, is_last_message_complete, "
                "messages FROM ai_conversations WHERE id = :id"
            ),
            {"id": conv_id},
        ).first()
    return {
        "total_messages": row.total_messages,
        "tokens_used": row.tokens_used,
        "is_last_message_complete": bool(row.is_last_message_complete),
        "messages": json.loads(row.messages) if isinstance(row.messages, str) else row.messages,
    }


def _load_quota_raw(user_id):
    from extensions import db

    with db.engine.connect() as conn:
        row = conn.execute(
            db.text(
                "SELECT daily_messages_used, last_reset_date FROM ai_usage_quotas "
                "WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        ).first()
    return {"daily_messages_used": row.daily_messages_used, "last_reset_date": row.last_reset_date}


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


class _FakeAssistant:
    """Stand-in for services.ai_provider_service.StudyAssistant that never
    makes a real network call -- stream_response yields pre-canned
    SSE-formatted chunks matching the real shape chat()'s generate()
    parses (data: {...}\\n\\n with 'content' / 'complete' keys).

    build_messages_calls (reset per-test by the fake_provider_and_assistant
    fixture) records each build_messages() call's user_input, so a test
    can assert what question chat() actually sent the assistant without
    needing to re-read persisted conversation state afterward."""

    build_messages_calls = []

    def __init__(self, provider, conversation_messages_copy):
        self.provider = provider
        self.conversation_messages_copy = conversation_messages_copy
        self.model = "fake-model"
        self._provider_exhausted = False

    def select_model(self, has_images):
        self.model = "fake-vision-model" if has_images else "fake-model"

    def build_messages(self, user_input, extracted_data, mode, post_content=None, continuation_text=None):
        _FakeAssistant.build_messages_calls.append(user_input)
        return [{"role": "user", "content": user_input}]

    def stream_response(self, messages, has_images=False):
        yield f"data: {json.dumps({'content': 'Hello '})}\n\n"
        yield f"data: {json.dumps({'content': 'world'})}\n\n"
        yield f"data: {json.dumps({'complete': True})}\n\n"


def _parse_sse_events(raw_text):
    events = []
    for line in raw_text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


@pytest.fixture
def fake_provider_and_assistant(monkeypatch):
    """Patches routes.student.learnora's own StudyAssistant and
    provider_manager.get_working_provider so chat() takes the "a working
    provider exists" branch and streams a canned, non-network reply."""
    import routes.student.learnora as learnora_module

    _FakeAssistant.build_messages_calls = []
    monkeypatch.setattr(learnora_module, "StudyAssistant", _FakeAssistant)
    monkeypatch.setattr(
        learnora_module.provider_manager, "get_working_provider",
        lambda needs_vision=False: {"name": "fake_provider"},
    )
    # Defensive: these mutate the module-level singleton's shared state
    # (see ai_provider_service.py's own docstring) -- neutered here so a
    # test that happens to exercise an error branch can't leak cooldown/
    # rotation state into a later test via the real provider_manager.
    monkeypatch.setattr(learnora_module.provider_manager, "mark_provider_failed", lambda *a, **kw: None)
    monkeypatch.setattr(learnora_module.provider_manager, "rotate", lambda: None)
    return learnora_module


# ============================================================================
# Validation / guard-clause paths (none of these need a working provider)
# ============================================================================

class TestChatValidation:
    def test_requires_auth(self, client):
        # Unlike most other POST routes in this suite, /learnora/api/chat
        # is listed in CSRF_EXEMPT_PATHS (routes/student/__init__.py), so
        # there's no CSRF-before-auth ordering to account for here -- a
        # request with no auth at all goes straight to @token_required's
        # own 401.
        resp = client.post("/student/learnora/api/chat")

        assert resp.status_code == 401

    def test_missing_conversation_id_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"message": "hi"},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 400
        assert "conversation_id" in resp.get_json()["message"]

    def test_non_numeric_conversation_id_rejected(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"conversation_id": "not-a-number", "message": "hi"},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 400

    def test_nonexistent_conversation_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"conversation_id": "999999", "message": "hi"},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 404

    def test_other_users_conversation_is_not_found(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        owner = make_user()
        conv = _conversation(db_session, owner)
        other = make_user()
        headers = {**auth_headers(other), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"conversation_id": str(conv.id), "message": "hi"},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 404

    def test_archived_conversation_rejected(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        conv = _conversation(db_session, user, is_archived=True)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"conversation_id": str(conv.id), "message": "hi"},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 410

    def test_message_cap_of_500_enforced(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        conv = _conversation(db_session, user, total_messages=500)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"conversation_id": str(conv.id), "message": "hi"},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 429
        assert "500 messages" in resp.get_json()["message"]

    def test_daily_quota_exhausted_rejected(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        from models import AIUsageQuota

        user = make_user()
        conv = _conversation(db_session, user)
        quota = AIUsageQuota(
            user_id=user.id, daily_messages_limit=5, daily_messages_used=5,
        )
        db_session.add(quota)
        db_session.commit()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"conversation_id": str(conv.id), "message": "hi"},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 429
        assert "Daily limit" in resp.get_json()["message"]

    def test_empty_message_with_no_seeded_turn_rejected(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        conv = _conversation(db_session, user)  # no messages at all
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"conversation_id": str(conv.id), "message": ""},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 400
        assert "cannot be empty" in resp.get_json()["message"]

    def test_too_many_attachments_rejected(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        import io

        user = make_user()
        conv = _conversation(db_session, user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        data = {"conversation_id": str(conv.id), "message": "hi"}
        for i in range(6):
            data[f"file{i}"] = (io.BytesIO(b"hello"), f"note{i}.txt")

        resp = client.post(
            "/student/learnora/api/chat",
            data=data,
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 400
        assert "Too many attachments" in resp.get_json()["message"]

    def test_no_provider_available_returns_503(
        self, client, db_session, make_user, auth_headers, csrf_headers
    ):
        # Real behavior in this test environment: no AI provider API keys
        # are configured, so provider_manager.get_working_provider()
        # genuinely returns None -- same documented behavior as
        # test_ai_no_real_calls.py, no mocking needed here.
        user = make_user()
        conv = _conversation(db_session, user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"conversation_id": str(conv.id), "message": "hi"},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 503
        assert "stats" in resp.get_json()


# ============================================================================
# Successful streaming path (mocked StudyAssistant + provider)
# ============================================================================

class TestChatStreaming:
    def test_successful_reply_streams_and_persists(
        self, client, db_session, make_user, auth_headers, csrf_headers,
        fake_provider_and_assistant,
    ):
        # Pre-seeded with one prior exchange so this isn't the
        # conversation's first message -- chat() only fires its
        # background title-generation thread on the first message, and
        # that real daemon thread's own `with _app.app_context()` races
        # against this test's synchronous SSE consumption under this
        # environment's eventlet-patched threading, corrupting Flask's
        # app-context stack for the test process. Avoiding the first-
        # message path sidesteps that entirely; it isn't what this test
        # is about.
        user = make_user()
        conv = _conversation(
            db_session, user,
            messages=[
                {"role": "user", "content": "prior question", "timestamp": "2024-01-01T00:00:00", "attachments": [], "is_continue": False},
                {"role": "assistant", "content": "prior answer", "timestamp": "2024-01-01T00:00:01", "is_complete": True},
            ],
            total_messages=2,
        )
        conv_id = conv.id  # captured before the request detaches `conv`
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"conversation_id": str(conv_id), "message": "What is a base case?"},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"

        events = _parse_sse_events(resp.get_data(as_text=True))
        assert events[0]["type"] == "start"
        assert events[0]["provider"] == "fake_provider"
        assert any(e.get("content") == "Hello " for e in events)
        done = next(e for e in events if e.get("type") == "done")
        assert done["complete"] is True

        # See _load_conversation_raw's docstring: the ORM session/identity
        # map is left in a state plain query.get()/refresh() can't cleanly
        # recover from here, so this reads the persisted row directly.
        conv_row = _load_conversation_raw(conv_id)
        assert conv_row["total_messages"] == 4  # 2 prior + new user turn + assistant turn
        assert conv_row["messages"][2]["role"] == "user"
        assert conv_row["messages"][2]["content"] == "What is a base case?"
        assert conv_row["messages"][3]["role"] == "assistant"
        assert conv_row["messages"][3]["content"] == "Hello world"
        assert conv_row["is_last_message_complete"] is True

        quota_row = _load_quota_raw(user.id)
        assert quota_row["daily_messages_used"] == 1

    def test_seeded_message_is_reused_without_duplication(
        self, client, db_session, make_user, auth_headers, csrf_headers,
        fake_provider_and_assistant,
    ):
        # Mirrors POST /conversation/new with an initial_message, followed
        # by POST /api/chat with an empty "message" to get the AI's first
        # reply -- the route should reuse the already-stored user turn
        # rather than requiring it to be retyped, and must NOT duplicate
        # it in conversation.messages.
        user = make_user()
        seeded_msg = {
            "role": "user",
            "content": "Explain recursion",
            "timestamp": "2024-01-01T00:00:00",
            "attachments": [],
            "is_continue": False,
        }
        conv = _conversation(
            db_session, user, messages=[seeded_msg], total_messages=1,
        )
        conv_id = conv.id  # captured before the request detaches `conv`
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"conversation_id": str(conv_id), "message": ""},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.get_data(as_text=True))
        done = next(e for e in events if e.get("type") == "done")
        assert done["complete"] is True

        # The seeded message was reused as the question sent to the
        # assistant -- not re-typed, and not an empty string -- confirming
        # chat() correctly fell back to the last stored user turn instead
        # of rejecting the empty "message" field outright.
        assert _FakeAssistant.build_messages_calls == ["Explain recursion"]

        # The seeded turn itself must still appear exactly once in the
        # stored conversation (not duplicated by this request).
        conv_row = _load_conversation_raw(conv_id)
        user_turns = [m for m in conv_row["messages"] if m.get("role") == "user"]
        assert len(user_turns) == 1
        assert user_turns[0]["content"] == "Explain recursion"

    def test_quota_resets_on_a_new_day(
        self, client, db_session, make_user, auth_headers, csrf_headers,
        fake_provider_and_assistant,
    ):
        import datetime
        from models import AIUsageQuota

        user = make_user()
        # Pre-seeded with a prior exchange, same reasoning as
        # test_successful_reply_streams_and_persists above -- avoids
        # chat()'s first-message background title-generation thread.
        conv = _conversation(
            db_session, user,
            messages=[
                {"role": "user", "content": "prior question", "timestamp": "2024-01-01T00:00:00", "attachments": [], "is_continue": False},
                {"role": "assistant", "content": "prior answer", "timestamp": "2024-01-01T00:00:01", "is_complete": True},
            ],
            total_messages=2,
        )
        conv_id = conv.id  # captured before the request detaches `conv`
        quota = AIUsageQuota(
            user_id=user.id, daily_messages_limit=5, daily_messages_used=5,
            last_reset_date=datetime.date.today() - datetime.timedelta(days=1),
        )
        db_session.add(quota)
        db_session.commit()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/learnora/api/chat",
            data={"conversation_id": str(conv_id), "message": "hi"},
            headers=headers,
            content_type="multipart/form-data",
        )

        # Yesterday's usage doesn't carry over -- the route resets it to 0
        # before checking the limit, so today's first message succeeds.
        assert resp.status_code == 200
        quota_row = _load_quota_raw(user.id)
        assert quota_row["daily_messages_used"] == 1
        last_reset = quota_row["last_reset_date"]
        if isinstance(last_reset, str):
            last_reset = datetime.date.fromisoformat(last_reset)
        assert last_reset == datetime.date.today()
