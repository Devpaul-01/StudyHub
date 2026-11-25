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


# --- live push happens only for committed notifications (NOTIF-16) -------------

def _emitted_ids(manager):
    return [call.args[2]["id"] for call in manager.emit_to_user.call_args_list]


def test_no_push_before_commit_one_push_after(db_session, make_user):
    user = make_user()
    with patch("services.websocket_messages.message_ws_manager") as manager:
        note = notification_service.notify(user.id, "t", "b", "x")
        manager.emit_to_user.assert_not_called()
        db_session.commit()
    assert _emitted_ids(manager) == [note.id]


def test_rollback_drops_the_push_and_the_row(db_session, make_user):
    """A rolled-back request must not leave the client holding a card for a row
    that never existed (this was reproduced as V3 in the audit)."""
    user = make_user()
    db_session.commit()
    with patch("services.websocket_messages.message_ws_manager") as manager:
        note = notification_service.notify(user.id, "t", "b", "x")
        note_id = note.id
        db_session.rollback()
        db_session.commit()
    manager.emit_to_user.assert_not_called()
    assert db_session.get(Notification, note_id) is None


def test_rolled_back_savepoint_drops_only_its_own_pushes(db_session, make_user):
    keep, drop = make_user(), make_user()
    db_session.commit()
    with patch("services.websocket_messages.message_ws_manager") as manager:
        kept = notification_service.notify(keep.id, "t", "b", "x")
        savepoint = db_session.begin_nested()
        notification_service.notify(drop.id, "t", "b", "x")
        savepoint.rollback()
        db_session.commit()
    assert _emitted_ids(manager) == [kept.id]
    assert [n.user_id for n in Notification.query.all()] == [keep.id]


def test_released_savepoint_is_pushed_when_the_outer_transaction_commits(db_session, make_user):
    user = make_user()
    db_session.commit()
    with patch("services.websocket_messages.message_ws_manager") as manager:
        with db_session.begin_nested():
            note = notification_service.notify(user.id, "t", "b", "x")
        manager.emit_to_user.assert_not_called()
        db_session.commit()
    assert _emitted_ids(manager) == [note.id]


def test_savepoint_released_then_outer_rollback_pushes_nothing(db_session, make_user):
    user = make_user()
    db_session.commit()
    with patch("services.websocket_messages.message_ws_manager") as manager:
        with db_session.begin_nested():
            notification_service.notify(user.id, "t", "b", "x")
        db_session.rollback()
        db_session.commit()
    manager.emit_to_user.assert_not_called()


def test_push_failure_after_commit_never_raises(db_session, make_user):
    user = make_user()
    with patch("services.websocket_messages.message_ws_manager") as manager:
        manager.emit_to_user.side_effect = RuntimeError("socket layer down")
        notification_service.notify(user.id, "t", "b", "x")
        db_session.commit()  # must not raise
    assert len(_stored(user.id)) == 1
