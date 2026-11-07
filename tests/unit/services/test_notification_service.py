"""
Tests for services/notification_service.py.

Regression coverage for the AUDIT BUG-4 fix: _broadcast() used to route
through services.websocket_events.ws_manager, a manager whose
init_app/register_handlers is never called anywhere in the app. That left
ws_manager.online_users permanently empty, so the old
`if hasattr(ws_manager, "emit_to_user")` branch was always taken (the
method exists on the class regardless of init state) and always iterated
zero socket ids -- every notification this module creates was correctly
persisted to the DB but never actually pushed live. The same bug class
was already fixed at three other call sites (routes/student/connections/
crud.py, routes/student/homework_system.py,
routes/student/study_sessions.py's _emit helper, since removed along
with the rest of the study-sessions feature); this file was the one
remaining call site still using the broken manager.

services/websocket_events.py (the module `ws_manager` lived in) has since
been deleted entirely as unused legacy code, so the original regression
this guarded against -- a future call reintroducing
services.websocket_events.ws_manager -- is no longer something a mock
patch can even target: there's no module left for `_broadcast()` to
import from, so the only test below that asserted that path was never
touched (by mock-patching it) has been removed, since `patch()` requires
the target module to exist. What remains is coverage that _broadcast()
correctly routes through services.websocket_messages.message_ws_manager.
"""

from unittest.mock import patch, MagicMock

from services import notification_service
from models import Notification


def test_notify_calls_message_ws_manager_emit_to_user(db_session, make_user):
    """The core regression guard: notify() must broadcast through
    services.websocket_messages.message_ws_manager (the genuinely
    initialized manager)."""
    user = make_user()

    with patch("services.websocket_messages.message_ws_manager") as mock_manager:
        notification_service.notify(
            user_id=user.id,
            title="Test Title",
            body="Test body",
            notification_type="test_type",
        )

    mock_manager.emit_to_user.assert_called_once()
    args, kwargs = mock_manager.emit_to_user.call_args
    assert args[0] == user.id
    assert args[1] == "new_notification"
    payload = args[2]
    assert payload["title"] == "Test Title"
    assert payload["body"] == "Test body"
    assert payload["type"] == "test_type"


def test_notify_persists_notification_even_if_broadcast_raises(db_session, make_user):
    """Broadcast is best-effort: notify() must still create and return the
    Notification row even if the WebSocket push itself blows up."""
    user = make_user()

    with patch("services.websocket_messages.message_ws_manager") as mock_manager:
        mock_manager.emit_to_user.side_effect = RuntimeError("socket layer down")
        notification = notification_service.notify(
            user_id=user.id,
            title="T",
            body="B",
            notification_type="test_type",
        )

    assert notification is not None
    assert notification.id is not None
    row = Notification.query.get(notification.id)
    assert row is not None
    assert row.title == "T"


def test_notify_badge_earned_broadcasts_via_message_ws_manager(db_session, make_user):
    """Spot-check one of the named notify_*() templates end-to-end to
    confirm the fix applies through the whole call chain, not just the
    generic notify() entry point directly under test above."""
    from models import Badge

    user = make_user()
    badge = Badge(
        name="Test Badge",
        description="desc",
        icon="🏅",
        criteria={"posts_count": 1},
    )
    from extensions import db
    db.session.add(badge)
    db.session.flush()

    with patch("services.websocket_messages.message_ws_manager") as mock_manager:
        notification_service.notify_badge_earned(user.id, badge)

    mock_manager.emit_to_user.assert_called_once()
    args, _ = mock_manager.emit_to_user.call_args
    payload = args[2]
    assert payload["type"] == "badge_earned"
