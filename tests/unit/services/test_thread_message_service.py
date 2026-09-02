"""
Tests for services/thread_message_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.6. edit_thread_message's
ownership-vs-window-bypass distinction and delete_thread_message's
moderator-can-delete-others'-messages behavior are both direct
regression tests for documented historical REST/WS behavioral drift --
treated with care since a test that only checks one side of either
distinction could pass while the real property is broken.
"""

import datetime
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from services import thread_message_service as tms


# ============================================================================
# create_thread_message
# ============================================================================

def test_create_message_non_member_raises(db_session, make_user, make_thread):
    creator = make_user()
    stranger = make_user()
    thread = make_thread(creator)

    with pytest.raises(tms.NotAMemberError):
        tms.create_thread_message(user_id=stranger.id, thread_id=thread.id, text_content="hi")


def test_create_message_thread_not_found_raises(db_session, make_user):
    """Membership is checked BEFORE thread existence (confirmed directly
    in the function body), so testing "thread not found" as a distinct
    case from "not a member" requires a ThreadMember row that references
    a thread_id with no backing Thread row -- constructed directly here
    since SQLite (as configured for this suite) doesn't enforce the FK."""
    user = make_user()
    from models import ThreadMember
    db_session.add(ThreadMember(thread_id=999999, student_id=user.id, role="member"))
    db_session.flush()

    with pytest.raises(tms.ThreadNotFoundError):
        tms.create_thread_message(user_id=user.id, thread_id=999999, text_content="hi")


def test_create_message_closed_thread_raises(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    thread = make_thread(creator, is_open=False)
    make_thread_member(thread, creator)

    with patch("services.thread_message_service.presence_service.get_active_threads_batch", return_value={}), \
         patch("services.thread_message_service.presence_service.get_online_user_ids", return_value=set()):
        with pytest.raises(tms.ThreadClosedError):
            tms.create_thread_message(user_id=creator.id, thread_id=thread.id, text_content="hi")


def test_create_message_no_text_no_attachment_raises(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, creator)

    with patch("services.thread_message_service.presence_service.get_active_threads_batch", return_value={}), \
         patch("services.thread_message_service.presence_service.get_online_user_ids", return_value=set()):
        with pytest.raises(tms.ValidationFailedError):
            tms.create_thread_message(user_id=creator.id, thread_id=thread.id, text_content="   ")


def test_create_message_too_long_raises(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, creator)

    with patch("services.thread_message_service.presence_service.get_active_threads_batch", return_value={}), \
         patch("services.thread_message_service.presence_service.get_online_user_ids", return_value=set()):
        with pytest.raises(tms.ValidationFailedError):
            tms.create_thread_message(
                user_id=creator.id, thread_id=thread.id, text_content="x" * (tms.MAX_MESSAGE_LENGTH + 1)
            )


def test_create_message_invalid_reply_to_silently_cleared(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, creator)

    with patch("services.thread_message_service.presence_service.get_active_threads_batch", return_value={}), \
         patch("services.thread_message_service.presence_service.get_online_user_ids", return_value=set()):
        result = tms.create_thread_message(
            user_id=creator.id, thread_id=thread.id, text_content="hello",
            reply_to_id=999999,  # doesn't exist
        )

    assert result.message.reply_to_id is None  # cleared, not an error


def test_create_message_attachments_capped(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, creator)
    attachments = [
        {"attachment_url": f"https://example.com/{i}.jpg"} for i in range(tms.MAX_ATTACHMENTS + 3)
    ]

    with patch("services.thread_message_service.presence_service.get_active_threads_batch", return_value={}), \
         patch("services.thread_message_service.presence_service.get_online_user_ids", return_value=set()):
        result = tms.create_thread_message(
            user_id=creator.id, thread_id=thread.id, text_content="hi", attachments_data=attachments
        )

    assert len(result.attachments_data) == tms.MAX_ATTACHMENTS


def test_create_message_status_read_when_active_viewer(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    other = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, creator)
    make_thread_member(thread, other)

    with patch("services.thread_message_service.presence_service.get_active_threads_batch", return_value={other.id: thread.id}), \
         patch("services.thread_message_service.presence_service.get_online_user_ids", return_value={other.id}):
        result = tms.create_thread_message(user_id=creator.id, thread_id=thread.id, text_content="hi")

    assert result.message.status == "read"


def test_create_message_status_delivered_when_online_not_viewing(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    other = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, creator)
    make_thread_member(thread, other)

    with patch("services.thread_message_service.presence_service.get_active_threads_batch", return_value={}), \
         patch("services.thread_message_service.presence_service.get_online_user_ids", return_value={other.id}):
        result = tms.create_thread_message(user_id=creator.id, thread_id=thread.id, text_content="hi")

    assert result.message.status == "delivered"


def test_create_message_status_sent_when_all_offline(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    other = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, creator)
    make_thread_member(thread, other)

    with patch("services.thread_message_service.presence_service.get_active_threads_batch", return_value={}), \
         patch("services.thread_message_service.presence_service.get_online_user_ids", return_value=set()):
        result = tms.create_thread_message(user_id=creator.id, thread_id=thread.id, text_content="hi")

    assert result.message.status == "sent"


def test_create_message_active_viewer_priority_over_online_non_viewer(
    db_session, make_user, make_thread, make_thread_member
):
    """One member is an active viewer, another is merely online-not-
    viewing -- 'read' must win even though an online-non-viewer also
    exists."""
    creator = make_user()
    viewer = make_user()
    online_only = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, creator)
    make_thread_member(thread, viewer)
    make_thread_member(thread, online_only)

    with patch(
        "services.thread_message_service.presence_service.get_active_threads_batch",
        return_value={viewer.id: thread.id},
    ), patch(
        "services.thread_message_service.presence_service.get_online_user_ids",
        return_value={viewer.id, online_only.id},
    ):
        result = tms.create_thread_message(user_id=creator.id, thread_id=thread.id, text_content="hi")

    assert result.message.status == "read"


def test_create_message_html_sanitized(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, creator)

    with patch("services.thread_message_service.presence_service.get_active_threads_batch", return_value={}), \
         patch("services.thread_message_service.presence_service.get_online_user_ids", return_value=set()):
        result = tms.create_thread_message(
            user_id=creator.id, thread_id=thread.id, text_content="<script>alert(1)</script>hello"
        )

    assert "<script>" not in result.message.text_content
    assert "hello" in result.message.text_content


# ============================================================================
# edit_thread_message
# ============================================================================

def test_edit_message_sender_within_window_succeeds(db_session, make_user, make_thread, make_thread_member, make_thread_message):
    sender = make_user()
    thread = make_thread(sender)
    make_thread_member(thread, sender, role="member")
    msg = make_thread_message(thread, sender, sent_at=datetime.datetime.utcnow())

    result = tms.edit_thread_message(user_id=sender.id, message_id=msg.id, new_text="updated text")

    assert result.message.text_content == "updated text"
    assert result.message.is_edited is True


def test_edit_message_sender_past_window_plain_member_raises(
    db_session, make_user, make_thread, make_thread_member, make_thread_message
):
    sender = make_user()
    thread = make_thread(sender)
    make_thread_member(thread, sender, role="member")
    old_time = datetime.datetime.utcnow() - datetime.timedelta(seconds=tms.EDIT_WINDOW_SECONDS + 100)
    msg = make_thread_message(thread, sender, sent_at=old_time)

    with pytest.raises(tms.EditWindowExpiredError):
        tms.edit_thread_message(user_id=sender.id, message_id=msg.id, new_text="too late")


def test_edit_message_sender_past_window_but_moderator_bypasses(
    db_session, make_user, make_thread, make_thread_member, make_thread_message
):
    """Moderators/creators are exempt from the 15-minute window -- but
    only when editing THEIR OWN message (see the next test for the
    ownership side of this distinction)."""
    mod_user = make_user()
    creator = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, mod_user, role="moderator")
    old_time = datetime.datetime.utcnow() - datetime.timedelta(seconds=tms.EDIT_WINDOW_SECONDS + 100)
    msg = make_thread_message(thread, mod_user, sent_at=old_time)  # moderator's OWN old message

    result = tms.edit_thread_message(user_id=mod_user.id, message_id=msg.id, new_text="still editable")
    assert result.message.text_content == "still editable"


def test_edit_message_moderator_cannot_edit_someone_elses_message(
    db_session, make_user, make_thread, make_thread_member, make_thread_message
):
    """The window-bypass is NOT an ownership bypass -- a moderator editing
    someone else's message is rejected regardless of the window, since
    the query filter requires sender_id == user_id unconditionally."""
    mod_user = make_user()
    other_sender = make_user()
    thread = make_thread(mod_user)
    make_thread_member(thread, mod_user, role="moderator")
    make_thread_member(thread, other_sender, role="member")
    msg = make_thread_message(thread, other_sender, sent_at=datetime.datetime.utcnow())  # NOT the moderator's message

    with pytest.raises(tms.MessageNotFoundError):
        tms.edit_thread_message(user_id=mod_user.id, message_id=msg.id, new_text="trying to edit")


def test_edit_message_ai_response_never_editable(
    db_session, make_user, make_thread, make_thread_member, make_thread_message
):
    user = make_user()
    thread = make_thread(user)
    make_thread_member(thread, user)
    msg = make_thread_message(thread, user, is_ai_response=True, sent_at=datetime.datetime.utcnow())

    with pytest.raises(tms.PermissionDeniedError):
        tms.edit_thread_message(user_id=user.id, message_id=msg.id, new_text="edit attempt")


def test_edit_message_empty_text_raises(db_session, make_user, make_thread, make_thread_member, make_thread_message):
    user = make_user()
    thread = make_thread(user)
    make_thread_member(thread, user)
    msg = make_thread_message(thread, user, sent_at=datetime.datetime.utcnow())

    with pytest.raises(tms.ValidationFailedError):
        tms.edit_thread_message(user_id=user.id, message_id=msg.id, new_text="   ")


# ============================================================================
# delete_thread_message
# ============================================================================

def test_delete_message_sender_deletes_own(db_session, make_user, make_thread, make_thread_member, make_thread_message):
    sender = make_user()
    thread = make_thread(sender, message_count=1)
    make_thread_member(thread, sender)
    msg = make_thread_message(thread, sender)

    result = tms.delete_thread_message(user_id=sender.id, message_id=msg.id)

    assert result.deleted_by == sender.id
    from models import ThreadMessage, Thread
    refreshed = ThreadMessage.query.get(msg.id)
    assert refreshed.is_deleted is True
    assert refreshed.text_content == "[deleted]"
    assert Thread.query.get(thread.id).message_count == 0


def test_delete_message_moderator_deletes_someone_elses_regression_fix(
    db_session, make_user, make_thread, make_thread_member, make_thread_message
):
    """Direct regression test for the documented historical bug: REST
    used to reject this (creator-or-sender only); WS always allowed it.
    A moderator (not creator, not sender) deleting someone else's
    message must succeed."""
    creator = make_user()
    mod_user = make_user()
    other_sender = make_user()
    thread = make_thread(creator, message_count=1)
    make_thread_member(thread, creator, role="creator")
    make_thread_member(thread, mod_user, role="moderator")
    make_thread_member(thread, other_sender, role="member")
    msg = make_thread_message(thread, other_sender)

    result = tms.delete_thread_message(user_id=mod_user.id, message_id=msg.id)

    assert result.deleted_by == mod_user.id
    assert result.original_sender_id == other_sender.id


def test_delete_message_creator_deletes_someone_elses(
    db_session, make_user, make_thread, make_thread_member, make_thread_message
):
    creator = make_user()
    other_sender = make_user()
    thread = make_thread(creator, message_count=1)
    make_thread_member(thread, creator, role="creator")
    make_thread_member(thread, other_sender, role="member")
    msg = make_thread_message(thread, other_sender)

    result = tms.delete_thread_message(user_id=creator.id, message_id=msg.id)
    assert result.deleted_by == creator.id


def test_delete_message_plain_member_cannot_delete_others(
    db_session, make_user, make_thread, make_thread_member, make_thread_message
):
    creator = make_user()
    plain_member = make_user()
    other_sender = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, plain_member, role="member")
    make_thread_member(thread, other_sender, role="member")
    msg = make_thread_message(thread, other_sender)

    with pytest.raises(tms.PermissionDeniedError):
        tms.delete_thread_message(user_id=plain_member.id, message_id=msg.id)


def test_delete_message_non_member_raises_distinct_message(
    db_session, make_user, make_thread, make_thread_member, make_thread_message
):
    """websocket_threads.py is documented to branch on the exact string
    'Not a thread member' -- this exact wording matters, not just the
    exception type."""
    creator = make_user()
    stranger = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, creator)
    msg = make_thread_message(thread, creator)

    with pytest.raises(tms.PermissionDeniedError) as exc_info:
        tms.delete_thread_message(user_id=stranger.id, message_id=msg.id)
    assert exc_info.value.message == "Not a thread member"


def test_delete_message_count_never_goes_negative(db_session, make_user, make_thread, make_thread_member, make_thread_message):
    sender = make_user()
    thread = make_thread(sender, message_count=0)  # already at zero
    make_thread_member(thread, sender)
    msg = make_thread_message(thread, sender)

    tms.delete_thread_message(user_id=sender.id, message_id=msg.id)

    from models import Thread
    assert Thread.query.get(thread.id).message_count == 0  # not -1


def test_delete_message_nonexistent_raises(db_session, make_user):
    user = make_user()
    with pytest.raises(tms.MessageNotFoundError):
        tms.delete_thread_message(user_id=user.id, message_id=999999)
