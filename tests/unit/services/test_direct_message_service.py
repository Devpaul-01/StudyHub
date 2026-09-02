"""
Tests for services/direct_message_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.6. delete_message_for_everyone's
"both flags set, not just is_deleted" assertion is the direct regression
test for a documented data-model bug: REST previously set ONLY
is_deleted=True, so a message deleted "for everyone" via REST could
still surface in shared-media listings that filter on
deleted_by_sender/deleted_by_receiver without checking is_deleted.
"""

import datetime

import pytest
from freezegun import freeze_time

from services import direct_message_service as dms


# ============================================================================
# delete_message_for_everyone
# ============================================================================

def test_delete_for_everyone_within_window_sets_both_flags_and_rewrites_body(
    db_session, make_user, make_message
):
    sender, receiver = make_user(), make_user()
    now = datetime.datetime(2026, 1, 1, 12, 0, 0)
    with freeze_time(now):
        msg = make_message(sender, receiver, body="original text", sent_at=now)

    with freeze_time(now + datetime.timedelta(minutes=2)):
        result = dms.delete_message_for_everyone(user_id=sender.id, message_id=msg.id)

    assert result.sender_id == sender.id
    assert result.receiver_id == receiver.id

    from models import Message
    refreshed = Message.query.get(msg.id)
    assert refreshed.deleted_by_sender is True
    assert refreshed.deleted_by_receiver is True  # both, not just is_deleted
    assert refreshed.body == "[Message deleted]"


def test_delete_for_everyone_past_window_raises(db_session, make_user, make_message):
    sender, receiver = make_user(), make_user()
    now = datetime.datetime(2026, 1, 1, 12, 0, 0)
    with freeze_time(now):
        msg = make_message(sender, receiver, sent_at=now)

    with freeze_time(now + datetime.timedelta(minutes=10)):  # past 5 min window
        with pytest.raises(dms.DeleteWindowExpiredError):
            dms.delete_message_for_everyone(user_id=sender.id, message_id=msg.id)


def test_delete_for_everyone_non_sender_raises_permission_denied(db_session, make_user, make_message):
    sender, receiver = make_user(), make_user()
    msg = make_message(sender, receiver, sent_at=datetime.datetime.utcnow())

    with pytest.raises(dms.PermissionDeniedError):
        dms.delete_message_for_everyone(user_id=receiver.id, message_id=msg.id)


def test_delete_for_everyone_nonexistent_message_raises(db_session):
    with pytest.raises(dms.MessageNotFoundError):
        dms.delete_message_for_everyone(user_id=1, message_id=999999)


# ============================================================================
# delete_message_for_me
# ============================================================================

def test_delete_for_me_sender_sets_sender_flag_only(db_session, make_user, make_message):
    sender, receiver = make_user(), make_user()
    msg = make_message(sender, receiver)

    result = dms.delete_message_for_me(user_id=sender.id, message_id=msg.id)

    assert result.was_sender is True
    from models import Message
    refreshed = Message.query.get(msg.id)
    assert refreshed.deleted_by_sender is True
    assert refreshed.deleted_by_receiver is False


def test_delete_for_me_receiver_sets_receiver_flag_only(db_session, make_user, make_message):
    sender, receiver = make_user(), make_user()
    msg = make_message(sender, receiver)

    result = dms.delete_message_for_me(user_id=receiver.id, message_id=msg.id)

    assert result.was_sender is False
    from models import Message
    refreshed = Message.query.get(msg.id)
    assert refreshed.deleted_by_receiver is True
    assert refreshed.deleted_by_sender is False


def test_delete_for_me_unrelated_user_raises(db_session, make_user, make_message):
    sender, receiver, stranger = make_user(), make_user(), make_user()
    msg = make_message(sender, receiver)

    with pytest.raises(dms.PermissionDeniedError):
        dms.delete_message_for_me(user_id=stranger.id, message_id=msg.id)


def test_delete_for_me_nonexistent_message_raises(db_session):
    with pytest.raises(dms.MessageNotFoundError):
        dms.delete_message_for_me(user_id=1, message_id=999999)


# ============================================================================
# mark_messages_read
# ============================================================================

def test_mark_messages_read_only_correctly_addressed_unread(db_session, make_user, make_message):
    sender, receiver, other_receiver = make_user(), make_user(), make_user()
    mine_unread = make_message(sender, receiver, is_read=False)
    mine_already_read = make_message(sender, receiver, is_read=True)
    not_mine = make_message(sender, other_receiver, is_read=False)

    result = dms.mark_messages_read(
        user_id=receiver.id,
        message_ids=[mine_unread.id, mine_already_read.id, not_mine.id],
    )

    assert result.marked_message_ids == [mine_unread.id]
    assert result.marked_count == 1
    assert result.sender_ids_to_notify == {sender.id: [mine_unread.id]}


def test_mark_messages_read_empty_list_no_query(db_session, make_user):
    result = dms.mark_messages_read(user_id=1, message_ids=[])
    assert result.marked_message_ids == []
    assert result.marked_count == 0


def test_mark_messages_read_all_already_read_empty_result(db_session, make_user, make_message):
    sender, receiver = make_user(), make_user()
    msg = make_message(sender, receiver, is_read=True)

    result = dms.mark_messages_read(user_id=receiver.id, message_ids=[msg.id])

    assert result.marked_message_ids == []
    assert result.marked_count == 0


def test_mark_messages_read_groups_by_sender(db_session, make_user, make_message):
    sender_a, sender_b, receiver = make_user(), make_user(), make_user()
    msg_a1 = make_message(sender_a, receiver, is_read=False)
    msg_a2 = make_message(sender_a, receiver, is_read=False)
    msg_b1 = make_message(sender_b, receiver, is_read=False)

    result = dms.mark_messages_read(
        user_id=receiver.id, message_ids=[msg_a1.id, msg_a2.id, msg_b1.id]
    )

    assert set(result.sender_ids_to_notify[sender_a.id]) == {msg_a1.id, msg_a2.id}
    assert result.sender_ids_to_notify[sender_b.id] == [msg_b1.id]
    assert result.marked_count == 3
