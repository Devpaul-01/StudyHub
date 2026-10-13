"""
Tests for services/websocket_messages.py's MessageWebSocketManager.

This module is ~1000 lines, almost entirely Flask-SocketIO event
handlers registered as closures inside register_handlers() (connect,
authenticate, send_message, typing, mark_read, reactions, etc.) —
exercising those properly needs a real running SocketIO server and
client, which this project's test harness doesn't build (the
integration conftest explicitly keeps websocket_messages/
websocket_threads out of the real create_app() wiring — see that
file's own comments). That full handler surface is a materially
bigger, separate effort and is NOT what this file covers.

What this file DOES cover: the manager's standalone, non-handler
methods — create_conversation_key, get_current_user, emit_error,
emit_to_user, disconnect_user, shutdown, sanitize_content, and the
auth_required decorator — which are genuinely unit-testable in
isolation with request/socketio mocked out, and had zero tests before
this. This is a partial-coverage pass on a module flagged as the
largest gap in the codebase, not a claim of full coverage.
"""

from unittest.mock import Mock, patch

import pytest

from services.websocket_messages import MessageWebSocketManager


@pytest.fixture
def manager():
    return MessageWebSocketManager()


# ============================================================================
# create_conversation_key
# ============================================================================

def test_create_conversation_key_is_order_independent(manager):
    assert manager.create_conversation_key(5, 9) == manager.create_conversation_key(9, 5)


def test_create_conversation_key_format(manager):
    assert manager.create_conversation_key(3, 7) == "conv_3_7"


def test_create_conversation_key_same_user_both_sides(manager):
    assert manager.create_conversation_key(4, 4) == "conv_4_4"


# ============================================================================
# get_current_user
# ============================================================================

def test_get_current_user_returns_mapped_user_id(manager):
    manager.socket_to_user["sid-123"] = 42

    with patch("services.websocket_messages.request", Mock(sid="sid-123")):
        assert manager.get_current_user() == 42


def test_get_current_user_returns_none_for_unknown_sid(manager):
    with patch("services.websocket_messages.request", Mock(sid="unknown-sid")):
        assert manager.get_current_user() is None


# ============================================================================
# emit_error
# ============================================================================

def test_emit_error_calls_flask_socketio_emit_with_error_event(manager):
    with patch("services.websocket_messages.emit") as mock_emit:
        manager.emit_error("Something went wrong")

    mock_emit.assert_called_once_with("error", {"message": "Something went wrong"})


# ============================================================================
# emit_to_user
# ============================================================================

def test_emit_to_user_emits_to_the_users_room_not_raw_sids(manager):
    """
    Per the method's own docstring, this is the horizontal-scaling fix:
    emit to the room every socket joins on connect, not raw per-sid
    emits, so it reaches sockets on other instances too once
    message_queue is configured.
    """
    manager.socketio = Mock()

    manager.emit_to_user(77, "new_message", {"text": "hi"})

    manager.socketio.emit.assert_called_once_with(
        "new_message", {"text": "hi"}, room="user_77"
    )


# ============================================================================
# disconnect_user
# ============================================================================

def test_disconnect_user_is_a_noop_when_socketio_not_initialized(manager):
    manager.socketio = None  # default state before init_app() has run

    # Must not raise even though there's no SocketIO instance yet.
    manager.disconnect_user(user_id=1)


def test_disconnect_user_is_a_noop_when_user_has_no_open_sockets(manager):
    manager.socketio = Mock()

    with patch("flask_socketio.disconnect") as mock_disconnect:
        manager.disconnect_user(user_id=999)  # never registered in online_users

    mock_disconnect.assert_not_called()


def test_disconnect_user_force_closes_every_tracked_socket_for_that_user(manager):
    manager.socketio = Mock()
    manager.online_users[5] = ["sid-a", "sid-b"]

    with patch("flask_socketio.disconnect") as mock_disconnect:
        manager.disconnect_user(user_id=5)

    assert mock_disconnect.call_count == 2
    called_sids = {call.kwargs.get("sid") for call in mock_disconnect.call_args_list}
    assert called_sids == {"sid-a", "sid-b"}


def test_disconnect_user_swallows_exception_from_one_socket_and_continues(manager):
    """A stale/already-gone sid raising on disconnect() must not stop the
    other sockets for the same user from also being disconnected."""
    manager.socketio = Mock()
    manager.online_users[5] = ["sid-bad", "sid-good"]

    with patch("flask_socketio.disconnect", side_effect=[RuntimeError("gone"), None]) as mock_disconnect:
        manager.disconnect_user(user_id=5)  # must not raise

    assert mock_disconnect.call_count == 2


# ============================================================================
# shutdown
# ============================================================================

def test_shutdown_cancels_the_presence_touch_timer_when_present(manager):
    fake_timer = Mock()
    manager._presence_touch_timer = fake_timer

    manager.shutdown()

    fake_timer.cancel.assert_called_once()
    assert manager._presence_touch_timer is None


def test_shutdown_is_a_noop_when_no_timer_was_started(manager):
    manager._presence_touch_timer = None

    manager.shutdown()  # must not raise

    assert manager._presence_touch_timer is None


# ============================================================================
# sanitize_content
# ============================================================================

def test_sanitize_content_strips_html_tags():
    manager = MessageWebSocketManager()
    assert manager.sanitize_content("<script>alert(1)</script>hello") == "alert(1)hello"


def test_sanitize_content_strips_leading_trailing_whitespace():
    manager = MessageWebSocketManager()
    assert manager.sanitize_content("  hello world  ") == "hello world"


def test_sanitize_content_returns_falsy_input_unchanged():
    manager = MessageWebSocketManager()
    assert manager.sanitize_content("") == ""
    assert manager.sanitize_content(None) is None


def test_sanitize_content_plain_text_passes_through():
    manager = MessageWebSocketManager()
    assert manager.sanitize_content("just a normal message") == "just a normal message"


# ============================================================================
# auth_required decorator
# ============================================================================

def test_auth_required_rejects_when_no_current_user(manager):
    calls = []

    def inner(current_user_id, data):
        calls.append((current_user_id, data))

    decorated = manager.auth_required(inner)

    with patch.object(manager, "get_current_user", return_value=None), \
         patch.object(manager, "emit_error") as mock_emit_error:
        result = decorated({"some": "data"})

    assert calls == []
    mock_emit_error.assert_called_once()
    assert result is None


def test_auth_required_calls_wrapped_handler_with_user_id_when_authenticated(manager):
    calls = []

    def inner(current_user_id, data):
        calls.append((current_user_id, data))
        return "handler-result"

    decorated = manager.auth_required(inner)

    with patch.object(manager, "get_current_user", return_value=42):
        result = decorated({"some": "data"})

    assert calls == [(42, {"some": "data"})]
    assert result == "handler-result"


def test_auth_required_preserves_wrapped_function_name(manager):
    def handle_send_message(current_user_id, data):
        pass

    decorated = manager.auth_required(handle_send_message)

    assert decorated.__name__ == "handle_send_message"


# ============================================================================
# _start_presence_touch_loop — scheduling behavior, without real threads
# ============================================================================

def test_start_presence_touch_loop_schedules_a_daemon_timer_at_the_configured_interval(manager):
    import services.websocket_messages as ws_messages

    with patch.object(ws_messages.threading, "Timer") as mock_timer_cls:
        mock_timer_instance = Mock()
        mock_timer_cls.return_value = mock_timer_instance

        manager._start_presence_touch_loop()

    mock_timer_cls.assert_called_once()
    interval_arg = mock_timer_cls.call_args[0][0]
    assert interval_arg == ws_messages._PRESENCE_TOUCH_INTERVAL_SECONDS
    assert mock_timer_instance.daemon is True
    mock_timer_instance.start.assert_called_once()


def test_presence_touch_loop_body_touches_every_tracked_socket(manager):
    """
    Capture the function passed to threading.Timer (rather than letting
    a real timer fire) and invoke it directly to exercise the loop body:
    it must call presence_service.touch_connection for every sid across
    every user currently in self.online_users.
    """
    import services.websocket_messages as ws_messages

    manager.online_users = {1: ["sid-a", "sid-b"], 2: ["sid-c"]}
    captured = {}

    def fake_timer(interval, func):
        captured["func"] = func
        return Mock()

    with patch.object(ws_messages.threading, "Timer", side_effect=fake_timer), \
         patch.object(ws_messages.presence_service, "touch_connection") as mock_touch:
        manager._start_presence_touch_loop()
        loop_body = captured["func"]

        # Re-patch Timer so the loop body's own self-rescheduling call
        # (in its `finally`) doesn't recurse into a second real timer.
        with patch.object(ws_messages.threading, "Timer", return_value=Mock()):
            loop_body()

    touched_sids = {call.args[0] for call in mock_touch.call_args_list}
    assert touched_sids == {"sid-a", "sid-b", "sid-c"}


def test_presence_touch_loop_body_reschedules_itself_even_if_touch_raises(manager):
    """The loop's try/finally must still schedule the next tick even when
    presence_service.touch_connection raises for a stale socket."""
    import services.websocket_messages as ws_messages

    manager.online_users = {1: ["sid-a"]}
    captured = {}

    def fake_timer(interval, func):
        captured["func"] = func
        return Mock()

    with patch.object(ws_messages.threading, "Timer", side_effect=fake_timer), \
         patch.object(ws_messages.presence_service, "touch_connection", side_effect=RuntimeError("redis down")):
        manager._start_presence_touch_loop()
        loop_body = captured["func"]

        with patch.object(ws_messages.threading, "Timer", return_value=Mock()) as reschedule_timer:
            loop_body()  # must not raise

    reschedule_timer.assert_called_once()
