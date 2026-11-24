"""
@mentions in thread messages (audit NOTIF-02 and NOTIF-06).

Send and edit share one implementation: only current thread members are ever
notified, text from the thread is never copied into a notification, and an edit
notifies only the people it newly mentions.
"""
import pytest

from models import Mention, Notification, ThreadMessage


@pytest.fixture
def room(client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers):
    alice = make_user(status="approved", username="alice_a")
    bob = make_user(status="approved", username="bobby_b")
    carol = make_user(status="approved", username="carol_c")
    outsider = make_user(status="approved", username="outsider_o")
    thread = make_thread(alice, title="Calculus study group")
    for user, role in ((alice, "creator"), (bob, "member"), (carol, "member")):
        make_thread_member(thread, user, role=role)
    headers = {**auth_headers(alice), **csrf_headers(client)}

    class Room:
        pass

    r = Room()
    r.alice, r.bob, r.carol, r.outsider, r.thread, r.headers, r.client = alice, bob, carol, outsider, thread, headers, client

    def send(text):
        resp = client.post(f"/student/threads/{thread.id}/messages", json={"text_content": text}, headers=headers)
        assert resp.status_code == 201, resp.get_json()
        return ThreadMessage.query.filter_by(thread_id=thread.id).order_by(ThreadMessage.id.desc()).first().id

    def edit(message_id, text):
        resp = client.patch(f"/student/threads/{thread.id}/messages/{message_id}", json={"text_content": text}, headers=headers)
        assert resp.status_code == 200, resp.get_json()

    r.send, r.edit = send, edit
    r.notes = lambda user: Notification.query.filter_by(user_id=user.id).order_by(Notification.id).all()
    return r


def test_member_mention_is_notified_through_the_funnel(room):
    room.send("hey @bobby_b can you look at this?")
    (note,) = room.notes(room.bob)
    assert note.notification_type == "thread_mention"
    assert note.related_type == "thread" and note.related_id == room.thread.id
    assert "alice" in note.title.lower() or note.title.endswith("mentioned you in a thread")


def test_non_member_is_never_notified_and_no_text_leaks(room):
    """NOTIF-02: a private message must not reach someone outside the thread."""
    room.send("PRIVATE exam answers: 1=B 2=D @outsider_o")
    assert room.notes(room.outsider) == []
    assert Mention.query.filter_by(mentioned_user_id=room.outsider.id).count() == 0


def test_notification_body_does_not_quote_the_message(room):
    room.send("the secret password is hunter2 @bobby_b")
    (note,) = room.notes(room.bob)
    assert "hunter2" not in (note.body or "")
    assert "Calculus study group" in (note.body or "")


def test_editing_does_not_renotify_existing_mentions(room):
    """NOTIF-06: send + 3 edits used to produce 4 notifications."""
    message_id = room.send("hey @bobby_b look")
    for i in range(3):
        room.edit(message_id, f"hey @bobby_b look again v{i}")
    assert len(room.notes(room.bob)) == 1


def test_edit_notifies_only_newly_mentioned_members(room):
    message_id = room.send("hey @bobby_b")
    room.edit(message_id, "hey @bobby_b and @carol_c")
    assert len(room.notes(room.bob)) == 1
    assert len(room.notes(room.carol)) == 1


def test_edit_that_removes_a_mention_drops_its_row(room):
    message_id = room.send("hey @bobby_b")
    room.edit(message_id, "never mind")
    assert Mention.query.filter_by(mentioned_in_id=message_id, mentioned_in_type="thread_message").count() == 0


def test_removed_then_readded_mention_is_notified_again(room):
    """A deliberate new mention (after removal) is a new event."""
    message_id = room.send("hey @bobby_b")
    room.edit(message_id, "never mind")
    room.edit(message_id, "hey @bobby_b again")
    assert len(room.notes(room.bob)) == 2


def test_self_mention_is_ignored(room):
    room.send("note to self @alice_a")
    assert room.notes(room.alice) == []


def test_blocked_member_is_not_notified(room, make_connection):
    make_connection(room.bob, room.alice, status="blocked", blocked_by_id=room.bob.id)
    room.send("hey @bobby_b")
    assert room.notes(room.bob) == []
