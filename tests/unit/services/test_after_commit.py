"""Tests for services/after_commit.py and the deferred side effects in notify()."""

from unittest.mock import MagicMock, patch

from extensions import db
from services import notification_service
from services.after_commit import pending_hook_count, run_after_commit


def test_hook_runs_only_after_commit(db_session):
    hook = MagicMock()
    run_after_commit(hook, "a", k=1)
    hook.assert_not_called()
    assert pending_hook_count() == 1

    db.session.commit()

    hook.assert_called_once_with("a", k=1)
    assert pending_hook_count() == 0


def test_hooks_run_in_registration_order(db_session):
    calls = []
    run_after_commit(calls.append, 1)
    run_after_commit(calls.append, 2)
    db.session.commit()
    assert calls == [1, 2]


def test_hook_discarded_on_rollback(db_session):
    hook = MagicMock()
    run_after_commit(hook)
    db.session.rollback()
    db.session.commit()
    hook.assert_not_called()
    assert pending_hook_count() == 0


def test_failing_hook_does_not_break_commit_or_later_hooks(db_session):
    later = MagicMock()

    def boom():
        raise RuntimeError("nope")

    run_after_commit(boom)
    run_after_commit(later)
    db.session.commit()  # must not raise
    later.assert_called_once()


def test_hook_fires_once_only(db_session):
    hook = MagicMock()
    run_after_commit(hook)
    db.session.commit()
    db.session.commit()
    hook.assert_called_once()


def test_notify_defers_counter_and_websocket_until_commit(db_session, make_user):
    user = make_user()
    with patch.object(notification_service.counter_cache_service,
                      "increment_unread_notification_count") as incr, \
         patch.object(notification_service, "_broadcast") as broadcast:
        n = notification_service.notify(user.id, "t", "b", "system")
        assert n.id is not None
        incr.assert_not_called()
        broadcast.assert_not_called()

        db.session.commit()

        incr.assert_called_once_with(user.id)
        broadcast.assert_called_once()
        payload = broadcast.call_args.args[1]
        assert payload["id"] == n.id and payload["title"] == "t"


def test_notify_rolled_back_produces_no_side_effects(db_session, make_user):
    user = make_user()
    with patch.object(notification_service.counter_cache_service,
                      "increment_unread_notification_count") as incr, \
         patch.object(notification_service, "_broadcast") as broadcast:
        notification_service.notify(user.id, "t", "b", "system")
        db.session.rollback()
        db.session.commit()
        incr.assert_not_called()
        broadcast.assert_not_called()
