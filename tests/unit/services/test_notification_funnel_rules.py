"""
Rules enforced by the notify() funnel (audit NOTIF-01/05/12/13/16).

Kept separate from test_notification_service.py, which covers the WebSocket
routing regression from the original BUG-4 fix.
"""
from unittest.mock import patch

import pytest

from models import Notification
from services import notification_service


def _stored(user_id):
    return Notification.query.filter_by(user_id=user_id).all()


# --- links (NOTIF-01) --------------------------------------------------------

@pytest.mark.parametrize("link", [
    "javascript:alert(1)", "//evil.example/x", "https://evil.example", "data:text/html,x",
    "relative/path", "/has space", "/back\\slash", "/new\nline", "x" * 300, "",
])
def test_unsafe_links_are_not_stored(db_session, make_user, link):
    user = make_user()
    with patch("services.websocket_messages.message_ws_manager"):
        notification_service.notify(user.id, "t", "b", "x", link=link)
    db_session.commit()
    assert _stored(user.id)[0].link is None


@pytest.mark.parametrize("link", ["/student/post/1", "/a/b?c=d#e"])
def test_same_origin_links_are_kept(db_session, make_user, link):
    user = make_user()
    with patch("services.websocket_messages.message_ws_manager"):
        notification_service.notify(user.id, "t", "b", "x", link=link)
    db_session.commit()
    assert _stored(user.id)[0].link == link


# --- user setting (NOTIF-05) ---------------------------------------------------

def test_disabled_user_gets_no_row_and_no_push(db_session, make_user):
    user = make_user()
    user.notification_settings = {"enable_notification": False}
    db_session.add(user)
    db_session.flush()

    with patch("services.websocket_messages.message_ws_manager") as manager:
        result = notification_service.notify(user.id, "t", "b", "x")
    db_session.commit()

    assert result is None
    assert _stored(user.id) == []
    manager.emit_to_user.assert_not_called()


@pytest.mark.parametrize("settings", [None, {}, {"enable_notification": True}, {"enable_notification_sound": False}])
def test_enabled_by_default(db_session, make_user, settings):
    user = make_user()
    user.notification_settings = settings
    db_session.add(user)
    db_session.flush()

    with patch("services.websocket_messages.message_ws_manager"):
        assert notification_service.notify(user.id, "t", "b", "x") is not None
    db_session.commit()
    assert len(_stored(user.id)) == 1


# --- blocks (NOTIF-12) ---------------------------------------------------------

def _block(make_connection, blocker, blocked, requester=None):
    requester = requester or blocked
    receiver = blocker if requester is blocked else blocked
    return make_connection(requester, receiver, status="blocked", blocked_by_id=blocker.id)


@pytest.mark.parametrize("blocked_is_requester", [True, False])
def test_blocked_actor_cannot_notify_the_blocker(db_session, make_user, make_connection, blocked_is_requester):
    blocker, abuser = make_user(), make_user()
    _block(make_connection, blocker, abuser, requester=abuser if blocked_is_requester else blocker)

    with patch("services.websocket_messages.message_ws_manager") as manager:
        result = notification_service.notify(blocker.id, "t", "b", "like", actor_id=abuser.id)
    db_session.commit()

    assert result is None
    assert _stored(blocker.id) == []
    manager.emit_to_user.assert_not_called()


def test_blocker_can_still_notify_the_person_they_blocked(db_session, make_user, make_connection):
    """Only the person who blocked is protected, not the other way round."""
    blocker, abuser = make_user(), make_user()
    _block(make_connection, blocker, abuser)

    with patch("services.websocket_messages.message_ws_manager"):
        assert notification_service.notify(abuser.id, "t", "b", "x", actor_id=blocker.id) is not None


def test_unblocked_connection_does_not_suppress(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    make_connection(a, b, status="accepted")
    with patch("services.websocket_messages.message_ws_manager"):
        assert notification_service.notify(a.id, "t", "b", "x", actor_id=b.id) is not None


def test_connection_templates_respect_blocks(db_session, make_user, make_connection):
    blocker, abuser = make_user(), make_user()
    _block(make_connection, blocker, abuser)
    with patch("services.websocket_messages.message_ws_manager"):
        assert notification_service.notify_connection_request(blocker.id, abuser.name, abuser.id) is None
    assert _stored(blocker.id) == []
