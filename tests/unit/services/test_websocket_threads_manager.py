"""
Tests for services/websocket_threads.py's ThreadWebSocketManager /
ThreadTypingManager and module-level helpers.

Same scope decision as tests/unit/services/test_websocket_messages_manager.py
(read that file's docstring for the full rationale): this module is
~2300 lines, almost entirely Flask-SocketIO event handlers registered as
closures inside register_handlers() (join_thread_room, send_thread_message,
thread_typing, add_thread_reaction, edit_thread_message,
delete_thread_message, pin/unpin, mark_thread_read, message_delivered,
request_ai_response, thread_ai_action, etc.) — exercising those properly
needs a real running SocketIO server and client, which this project's test
harness doesn't build (the integration conftest explicitly keeps
websocket_messages/websocket_threads out of the real create_app() wiring).
That full handler surface is a materially bigger, separate effort and is
NOT what this file covers.

What this file DOES cover: the module's standalone, non-handler pieces —
_summarize_payload, _user_room, _is_rate_limited, _is_ai_action_rate_limited
(module-level helpers), ThreadTypingManager's thin wrapper methods
(stop_typing/is_typing), and ThreadWebSocketManager's non-handler methods
(init_socketio, _get_current_user, _emit_error, _is_member,
_is_moderator_or_creator, _build_reactions, broadcast_to_thread,
notify_user, broadcast_ai_message) — which are genuinely unit-testable in
isolation with request/socketio/the ORM query objects mocked out, and had
zero tests before this. This is a partial-coverage pass on a module
flagged as a large gap in the codebase, not a claim of full coverage.

services/websocket_events.py is explicitly out of scope (per product
decision) and is not touched by this batch.
"""

from unittest.mock import Mock, patch

import pytest

from services.websocket_threads import (
    ThreadTypingManager,
    ThreadWebSocketManager,
    _is_ai_action_rate_limited,
    _is_rate_limited,
    _summarize_payload,
    _user_room,
)


@pytest.fixture
def manager():
    return ThreadWebSocketManager()


@pytest.fixture
def typing_mgr():
    return ThreadTypingManager(timeout=3)


# ============================================================================
# _summarize_payload
# ============================================================================

def test_summarize_payload_returns_empty_braces_for_none():
    assert _summarize_payload(None) == "{}"


def test_summarize_payload_returns_empty_braces_for_non_dict():
    assert _summarize_payload("not a dict") == "{}"
    assert _summarize_payload(["a", "list"]) == "{}"


def test_summarize_payload_masks_attachment_url_as_present():
    result = _summarize_payload({"attachment_url": "https://signed.example/x?sig=abc"})
    assert result == "{attachment_url=<present>}"
    assert "signed.example" not in result


def test_summarize_payload_masks_missing_attachment_url_as_none():
    result = _summarize_payload({"attachment_url": None})
    assert result == "{attachment_url=None}"


def test_summarize_payload_masks_attachment_data_the_same_way():
    result = _summarize_payload({"attachment_data": "base64....."})
    assert result == "{attachment_data=<present>}"


def test_summarize_payload_truncates_text_content_to_max_text():
    long_text = "x" * 200
    result = _summarize_payload({"text_content": long_text}, max_text=10)
    assert result == '{text_content="xxxxxxxxxx…"}'


def test_summarize_payload_does_not_append_ellipsis_when_text_fits():
    result = _summarize_payload({"question": "short"}, max_text=80)
    assert result == '{question="short"}'


def test_summarize_payload_replaces_newlines_in_truncated_text():
    result = _summarize_payload({"text_content": "line one\nline two"})
    assert "\n" not in result
    assert "line one line two" in result


def test_summarize_payload_renders_emoji_with_repr():
    result = _summarize_payload({"emoji": "👍"})
    assert result == "{emoji='👍'}"


def test_summarize_payload_renders_scalars_with_repr():
    result = _summarize_payload({"count": 3, "ok": True, "missing": None})
    assert result == "{count=3, ok=True, missing=None}"


def test_summarize_payload_truncates_other_values_to_40_chars():
    # Note the ellipsis lands OUTSIDE the repr's closing quote: the code
    # is `f"{key}={s[:40]!r}{'…' if len(s) > 40 else ''}"` -- repr() runs
    # on the already-truncated string first, then '…' is appended after.
    result = _summarize_payload({"other": "y" * 100})
    assert result == "{other='" + "y" * 40 + "'…}"


def test_summarize_payload_joins_multiple_keys_with_comma_space():
    result = _summarize_payload({"a": 1, "b": 2})
    assert result == "{a=1, b=2}"


# ============================================================================
# _user_room
# ============================================================================

def test_user_room_format():
    assert _user_room(42) == "user_42"


# ============================================================================
# _is_rate_limited
# ============================================================================

def test_is_rate_limited_returns_false_when_limiter_allows():
    with patch(
        "services.websocket_threads._thread_msg_rate_limiter.check_rate_limit",
        return_value=(True, 29),
    ) as mock_check:
        assert _is_rate_limited(7) is False

    mock_check.assert_called_once()
    _, kwargs = mock_check.call_args
    assert kwargs["key"] == "thread_msg_7"


def test_is_rate_limited_returns_true_when_limiter_blocks():
    with patch(
        "services.websocket_threads._thread_msg_rate_limiter.check_rate_limit",
        return_value=(False, 0),
    ):
        assert _is_rate_limited(7) is True


# ============================================================================
# _is_ai_action_rate_limited
# ============================================================================

def test_is_ai_action_rate_limited_returns_false_when_limiter_allows():
    with patch(
        "services.websocket_threads._ai_action_rate_limiter.check_rate_limit",
        return_value=(True, 9),
    ) as mock_check:
        assert _is_ai_action_rate_limited(7) is False

    mock_check.assert_called_once()
    _, kwargs = mock_check.call_args
    assert kwargs["key"] == "thread_ai_action_7"


def test_is_ai_action_rate_limited_returns_true_when_limiter_blocks():
    with patch(
        "services.websocket_threads._ai_action_rate_limiter.check_rate_limit",
        return_value=(False, 0),
    ):
        assert _is_ai_action_rate_limited(7) is True


def test_is_rate_limited_and_ai_action_rate_limited_use_independent_limiters():
    """Regression guard for the module's own documented namespace split —
    msgrate vs aiaction buckets must never collide."""
    with patch(
        "services.websocket_threads._thread_msg_rate_limiter.check_rate_limit",
        return_value=(False, 0),
    ) as mock_msg, patch(
        "services.websocket_threads._ai_action_rate_limiter.check_rate_limit",
        return_value=(True, 5),
    ) as mock_ai:
        assert _is_rate_limited(3) is True
        assert _is_ai_action_rate_limited(3) is False

    assert mock_msg.call_count == 1
    assert mock_ai.call_count == 1


# ============================================================================
# ThreadTypingManager
# ============================================================================

def test_stop_typing_is_an_alias_for_remove_typing(typing_mgr):
    typing_mgr.set_typing(1, 99)
    assert typing_mgr.is_typing(1, 99) is True

    typing_mgr.stop_typing(1, 99)

    assert typing_mgr.is_typing(1, 99) is False


def test_is_typing_true_for_a_currently_typing_user(typing_mgr):
    typing_mgr.set_typing(5, 10)
    assert typing_mgr.is_typing(5, 10) is True


def test_is_typing_false_for_a_user_who_never_typed(typing_mgr):
    assert typing_mgr.is_typing(5, 999) is False


def test_is_typing_is_scoped_per_thread(typing_mgr):
    typing_mgr.set_typing(1, 10)
    assert typing_mgr.is_typing(2, 10) is False


# ============================================================================
# ThreadWebSocketManager.init_socketio
# ============================================================================

def test_init_socketio_stores_app_and_socketio_and_registers_handlers(manager):
    fake_app = Mock()
    fake_socketio = Mock()

    with patch.object(manager, "register_handlers") as mock_register:
        manager.init_socketio(fake_app, fake_socketio)

    assert manager.app is fake_app
    assert manager.socketio is fake_socketio
    mock_register.assert_called_once()


# ============================================================================
# ThreadWebSocketManager._get_current_user
# ============================================================================

def test_get_current_user_returns_mapped_user_id(manager):
    fake_message_ws_manager = Mock()
    fake_message_ws_manager.socket_to_user = {"sid-abc": 55}

    with patch("services.websocket_messages.message_ws_manager", fake_message_ws_manager), \
         patch("services.websocket_threads.request", Mock(sid="sid-abc")):
        assert manager._get_current_user() == 55


def test_get_current_user_returns_none_for_unknown_sid(manager):
    fake_message_ws_manager = Mock()
    fake_message_ws_manager.socket_to_user = {}

    with patch("services.websocket_messages.message_ws_manager", fake_message_ws_manager), \
         patch("services.websocket_threads.request", Mock(sid="unknown-sid")):
        assert manager._get_current_user() is None


# ============================================================================
# ThreadWebSocketManager._emit_error
# ============================================================================

def test_emit_error_calls_flask_socketio_emit_with_thread_error_event(manager):
    with patch("services.websocket_threads.request", Mock(sid="sid-1")), \
         patch("services.websocket_threads.emit") as mock_emit:
        manager._emit_error("bad payload")

    mock_emit.assert_called_once_with("thread_error", {"message": "bad payload"})


# ============================================================================
# ThreadWebSocketManager._is_member
# ============================================================================

def test_is_member_queries_thread_member_by_thread_and_student(manager):
    fake_membership = Mock()
    fake_query = Mock()
    fake_query.filter_by.return_value.first.return_value = fake_membership

    with patch("services.websocket_threads.ThreadMember") as mock_model:
        mock_model.query = fake_query
        result = manager._is_member(thread_id=10, user_id=20)

    fake_query.filter_by.assert_called_once_with(thread_id=10, student_id=20)
    assert result is fake_membership


def test_is_member_returns_none_when_not_a_member(manager):
    fake_query = Mock()
    fake_query.filter_by.return_value.first.return_value = None

    with patch("services.websocket_threads.ThreadMember") as mock_model:
        mock_model.query = fake_query
        assert manager._is_member(thread_id=10, user_id=20) is None


# ============================================================================
# ThreadWebSocketManager._is_moderator_or_creator
# ============================================================================

def test_is_moderator_or_creator_true_for_creator(manager):
    membership = Mock(role="creator")
    assert manager._is_moderator_or_creator(membership) is True


def test_is_moderator_or_creator_true_for_moderator(manager):
    membership = Mock(role="moderator")
    assert manager._is_moderator_or_creator(membership) is True


def test_is_moderator_or_creator_false_for_plain_member(manager):
    membership = Mock(role="member")
    assert manager._is_moderator_or_creator(membership) is False


# ============================================================================
# ThreadWebSocketManager._build_reactions
# ============================================================================

def test_build_reactions_groups_rows_by_emoji_with_counts_and_users(manager):
    rows = [
        Mock(emoji="👍", user_id=1),
        Mock(emoji="👍", user_id=2),
        Mock(emoji="🎉", user_id=3),
    ]
    fake_query = Mock()
    fake_query.filter_by.return_value.all.return_value = rows

    with patch("services.websocket_threads.ThreadMessageReaction") as mock_model:
        mock_model.query = fake_query
        result = manager._build_reactions(message_id=123)

    fake_query.filter_by.assert_called_once_with(message_id=123)
    assert result == {
        "👍": {"emoji": "👍", "count": 2, "users": [1, 2]},
        "🎉": {"emoji": "🎉", "count": 1, "users": [3]},
    }


def test_build_reactions_returns_empty_dict_when_no_reactions(manager):
    fake_query = Mock()
    fake_query.filter_by.return_value.all.return_value = []

    with patch("services.websocket_threads.ThreadMessageReaction") as mock_model:
        mock_model.query = fake_query
        assert manager._build_reactions(message_id=123) == {}


# ============================================================================
# ThreadWebSocketManager.broadcast_to_thread
# ============================================================================

def test_broadcast_to_thread_emits_to_the_thread_room(manager):
    manager.socketio = Mock()

    manager.broadcast_to_thread(9, "new_thread_message", {"text": "hi"})

    manager.socketio.emit.assert_called_once_with(
        "new_thread_message", {"text": "hi"}, room="thread_9"
    )


def test_broadcast_to_thread_is_a_noop_when_socketio_not_initialized(manager):
    manager.socketio = None

    manager.broadcast_to_thread(9, "new_thread_message", {"text": "hi"})  # must not raise


# ============================================================================
# ThreadWebSocketManager.notify_user
# ============================================================================

def test_notify_user_emits_to_the_users_personal_room(manager):
    manager.socketio = Mock()

    manager.notify_user(77, "message_status_updated", {"status": "read"})

    manager.socketio.emit.assert_called_once_with(
        "message_status_updated", {"status": "read"}, room="user_77"
    )


def test_notify_user_is_a_noop_when_socketio_not_initialized(manager):
    manager.socketio = None

    manager.notify_user(77, "message_status_updated", {"status": "read"})  # must not raise


# ============================================================================
# ThreadWebSocketManager.broadcast_ai_message
# ============================================================================

def test_broadcast_ai_message_uses_real_user_when_found(manager):
    manager.socketio = Mock()
    # NOTE: `name` is a reserved Mock() constructor kwarg (sets the
    # mock's own repr name, not a `.name` attribute) -- set it after
    # construction instead, same as the synthetic-user-fallback test
    # below relies on the real `type('DummyUser', ...)` object's plain
    # attribute access.
    bot_user = Mock(id=900, username="learnora", avatar=None)
    bot_user.name = "Learnora"
    msg = Mock(id=1, sender_id=900, sent_at=__import__("datetime").datetime(2026, 1, 1))

    with patch("services.websocket_threads.User") as mock_user_model:
        mock_user_model.query.get.return_value = bot_user
        manager.broadcast_ai_message(thread_id=5, msg=msg, text="Hello there")

    mock_user_model.query.get.assert_called_once_with(900)
    manager.socketio.emit.assert_called_once()
    event, payload = manager.socketio.emit.call_args.args
    assert event == "new_thread_message"
    assert payload["sender"]["name"] == "Learnora"
    assert payload["text_content"] == "Hello there"
    assert payload["is_ai_response"] is True
    assert manager.socketio.emit.call_args.kwargs == {"room": "thread_5"}


def test_broadcast_ai_message_falls_back_to_synthetic_user_when_bot_row_missing(manager):
    """
    FIX documented in the module itself: LEARNORA_BOT_USER_ID may not
    correspond to a real row in the users table. User.query.get(...)
    returning None must not leak None-driven defaults into the emitted
    payload -- a synthetic user object is used instead.
    """
    manager.socketio = Mock()
    msg = Mock(id=2, sender_id=12345, sent_at=__import__("datetime").datetime(2026, 1, 1))

    with patch("services.websocket_threads.User") as mock_user_model:
        mock_user_model.query.get.return_value = None
        manager.broadcast_ai_message(thread_id=5, msg=msg, text="fallback reply")

    event, payload = manager.socketio.emit.call_args.args
    assert payload["sender"]["name"] == "Learnora"
    assert payload["sender"]["username"] == "learnora"
    assert payload["sender"]["avatar"] is None
    assert payload["sender_id"] == 12345


def test_broadcast_ai_message_appends_z_suffix_to_sent_at(manager):
    manager.socketio = Mock()
    import datetime as dt
    bot_user = Mock(id=900, username="learnora", avatar=None)
    bot_user.name = "Learnora"
    msg = Mock(id=3, sender_id=900, sent_at=dt.datetime(2026, 5, 4, 3, 2, 1))

    with patch("services.websocket_threads.User") as mock_user_model:
        mock_user_model.query.get.return_value = bot_user
        manager.broadcast_ai_message(thread_id=5, msg=msg, text="x")

    _, payload = manager.socketio.emit.call_args.args
    assert payload["sent_at"] == "2026-05-04T03:02:01Z"
