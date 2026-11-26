"""Regression tests for the threads production-readiness audit (security batch)."""
import pytest

pytestmark = pytest.mark.integration
B = "/student/threads"


def _hdr(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


def _thread(make_thread, make_thread_member, creator, **kw):
    t = make_thread(creator, **kw)
    make_thread_member(t, creator, role="creator")
    return t


class TestCrossThreadAI:
    """TH-01: AI actions must never read a message from another thread."""

    def _setup(self, app, make_user, make_thread, make_thread_member,
               make_thread_message, monkeypatch):
        from services import websocket_threads as wt
        import services.ai_provider_service as aps
        attacker, victim, bot = make_user(), make_user(), make_user()
        a = _thread(make_thread, make_thread_member, attacker)
        b = _thread(make_thread, make_thread_member, victim)
        secret = make_thread_message(b, victim, text_content="PRIVATE: B secret")
        app.config["LEARNORA_BOT_USER_ID"] = bot.id
        seen = {}
        monkeypatch.setattr(aps.provider_manager, "get_working_provider",
                            lambda **k: object())

        def fake(messages, provider):
            seen["prompt"] = messages[-1]["content"]
            return "ok"

        monkeypatch.setattr(aps, "_call_provider_sync", fake)
        monkeypatch.setattr(wt.thread_ws_manager, "broadcast_ai_message",
                            lambda *a, **k: None)
        return wt, a, b, secret, attacker, seen

    def test_action_on_foreign_message_is_ignored(
        self, app, db_session, make_user, make_thread, make_thread_member,
        make_thread_message, monkeypatch,
    ):
        wt, a, _b, secret, attacker, seen = self._setup(
            app, make_user, make_thread, make_thread_member,
            make_thread_message, monkeypatch)
        from models import ThreadMessage
        wt._call_learnora_action(app, a.id, secret.id, "explain", None, attacker.id)
        assert "prompt" not in seen
        assert ThreadMessage.query.filter_by(
            thread_id=a.id, is_ai_response=True).count() == 0

    def test_action_on_own_thread_message_still_works(
        self, app, db_session, make_user, make_thread, make_thread_member,
        make_thread_message, monkeypatch,
    ):
        wt, a, _b, _secret, attacker, seen = self._setup(
            app, make_user, make_thread, make_thread_member,
            make_thread_message, monkeypatch)
        own = make_thread_message(a, attacker, text_content="my own text")
        wt._call_learnora_action(app, a.id, own.id, "explain", None, attacker.id)
        assert "my own text" in seen["prompt"]

    def test_reply_preview_never_crosses_threads(
        self, client, db_session, make_user, make_thread, make_thread_member,
        make_thread_message, auth_headers,
    ):
        from models import db
        u, other = make_user(), make_user()
        a = _thread(make_thread, make_thread_member, u)
        b = _thread(make_thread, make_thread_member, other)
        foreign = make_thread_message(b, other, text_content="B-private")
        msg = make_thread_message(a, u, text_content="reply in A")
        msg.reply_to_id = foreign.id
        db.session.commit()
        resp = client.get(f"{B}/{a.id}/messages", headers=auth_headers(u))
        assert resp.status_code == 200
        assert "B-private" not in str(resp.get_json())


class TestEditAuthorization:
    """TH-10: edit needs membership; edit must not resurrect a deleted message."""

    def test_ex_member_cannot_edit_old_message(
        self, client, db_session, make_user, make_thread, make_thread_member,
        make_thread_message, auth_headers, csrf_headers,
    ):
        import datetime
        creator, member = make_user(), make_user()
        t = _thread(make_thread, make_thread_member, creator, member_count=2)
        make_thread_member(t, member)
        old = datetime.datetime.utcnow() - datetime.timedelta(days=3)
        msg = make_thread_message(t, member, text_content="orig", sent_at=old)
        h = _hdr(client, auth_headers, csrf_headers, member)
        assert client.post(f"{B}/{t.id}/leave", headers=h).status_code == 200
        resp = client.patch(f"{B}/{t.id}/messages/{msg.id}",
                            json={"text_content": "rewritten"}, headers=h)
        assert resp.status_code == 403
        from models import ThreadMessage
        assert ThreadMessage.query.get(msg.id).text_content == "orig"

    def test_member_can_edit_recent_message(
        self, client, db_session, make_user, make_thread, make_thread_member,
        make_thread_message, auth_headers, csrf_headers,
    ):
        creator, member = make_user(), make_user()
        t = _thread(make_thread, make_thread_member, creator, member_count=2)
        make_thread_member(t, member)
        msg = make_thread_message(t, member, text_content="orig")
        resp = client.patch(f"{B}/{t.id}/messages/{msg.id}",
                            json={"text_content": "fixed"},
                            headers=_hdr(client, auth_headers, csrf_headers, member))
        assert resp.status_code == 200

    def test_edit_of_deleted_message_is_rejected(
        self, db_session, make_user, make_thread, make_thread_member,
        make_thread_message,
    ):
        from services import thread_message_service as tms
        from models import ThreadMessage, db
        u = make_user()
        t = _thread(make_thread, make_thread_member, u)
        msg = make_thread_message(t, u, text_content="orig")
        tms.delete_thread_message(user_id=u.id, message_id=msg.id)
        with pytest.raises(tms.MessageNotFoundError):
            tms.edit_thread_message(user_id=u.id, message_id=msg.id, new_text="zombie")
        assert ThreadMessage.query.get(msg.id).text_content == "[deleted]"


class TestMentionScoping:
    """TH-06 / TH-22: mentions are member-only, leak-free and not re-sent."""

    def test_non_member_mention_creates_no_notification(
        self, db_session, make_user, make_thread, make_thread_member,
    ):
        from services import thread_message_service as tms
        from models import Notification, Mention
        u, outsider = make_user(), make_user()
        t = _thread(make_thread, make_thread_member, u)
        tms.create_thread_message(
            user_id=u.id, thread_id=t.id,
            text_content=f"secret answers @{outsider.username}")
        assert Notification.query.filter_by(user_id=outsider.id).count() == 0
        assert Mention.query.filter_by(mentioned_user_id=outsider.id).count() == 0

    def test_member_mention_notifies_without_message_text(
        self, db_session, make_user, make_thread, make_thread_member,
    ):
        from services import thread_message_service as tms
        from models import Notification
        u, m = make_user(), make_user()
        t = _thread(make_thread, make_thread_member, u, member_count=2)
        make_thread_member(t, m)
        tms.create_thread_message(
            user_id=u.id, thread_id=t.id,
            text_content=f"private exam text @{m.username}")
        notes = Notification.query.filter_by(user_id=m.id).all()
        assert len(notes) == 1
        assert notes[0].notification_type == "thread_mention"
        assert "private exam text" not in (notes[0].body or "")

    def test_editing_does_not_renotify_existing_mention(
        self, db_session, make_user, make_thread, make_thread_member,
    ):
        from services import thread_message_service as tms
        from models import Notification
        u, m = make_user(), make_user()
        t = _thread(make_thread, make_thread_member, u, member_count=2)
        make_thread_member(t, m)
        res = tms.create_thread_message(
            user_id=u.id, thread_id=t.id, text_content=f"hi @{m.username}")
        for i in range(3):
            tms.edit_thread_message(
                user_id=u.id, message_id=res.message.id,
                new_text=f"hi again {i} @{m.username}")
        assert Notification.query.filter_by(user_id=m.id).count() == 1

    def test_edit_that_adds_a_mention_notifies_only_the_new_one(
        self, db_session, make_user, make_thread, make_thread_member,
    ):
        from services import thread_message_service as tms
        from models import Notification
        u, m1, m2 = make_user(), make_user(), make_user()
        t = _thread(make_thread, make_thread_member, u, member_count=3)
        make_thread_member(t, m1)
        make_thread_member(t, m2)
        res = tms.create_thread_message(
            user_id=u.id, thread_id=t.id, text_content=f"hi @{m1.username}")
        tms.edit_thread_message(
            user_id=u.id, message_id=res.message.id,
            new_text=f"hi @{m1.username} and @{m2.username}")
        assert Notification.query.filter_by(user_id=m1.id).count() == 1
        assert Notification.query.filter_by(user_id=m2.id).count() == 1


class TestThreadDetailsVisibility:
    """TH-11: the roster is for members only; errors do not leak internals."""

    def test_non_member_gets_no_roster(
        self, client, db_session, make_user, make_thread, make_thread_member,
        auth_headers, csrf_headers,
    ):
        creator, member, outsider = make_user(), make_user(), make_user()
        t = _thread(make_thread, make_thread_member, creator, member_count=2)
        make_thread_member(t, member)
        resp = client.post(f"{B}/{t.id}/details", json={},
                           headers=_hdr(client, auth_headers, csrf_headers, outsider))
        assert resp.status_code == 200
        thread = resp.get_json()["data"]["thread"]
        assert thread["members_data"] == []
        assert thread["total_users"] == 2
        assert thread["is_member"] is False

    def test_member_still_gets_roster(
        self, client, db_session, make_user, make_thread, make_thread_member,
        auth_headers, csrf_headers,
    ):
        creator, member = make_user(), make_user()
        t = _thread(make_thread, make_thread_member, creator, member_count=2)
        make_thread_member(t, member)
        resp = client.post(f"{B}/{t.id}/details", json={},
                           headers=_hdr(client, auth_headers, csrf_headers, member))
        thread = resp.get_json()["data"]["thread"]
        assert {m["id"] for m in thread["members_data"]} == {creator.id, member.id}
        assert thread["is_member"] is True


class _FakeSocketIO:
    """Captures emits and room closes made through ThreadWebSocketManager."""

    def __init__(self):
        self.emits, self.closed = [], []
        outer = self

        class _Server:
            def close_room(self, room, namespace=None):
                outer.closed.append(room)

        self.server = _Server()

    def emit(self, event, data, room=None, **kw):
        self.emits.append((event, room, data))


class TestRoomEviction:
    """TH-02: removed/left members and deleted threads lose the live room."""

    def _manager(self, monkeypatch):
        from services.websocket_threads import thread_ws_manager as mgr
        fake = _FakeSocketIO()
        monkeypatch.setattr(mgr, "socketio", fake)
        return fake

    def test_remove_member_closes_room_and_resyncs_the_rest(
        self, client, db_session, make_user, make_thread, make_thread_member,
        auth_headers, csrf_headers, monkeypatch,
    ):
        fake = self._manager(monkeypatch)
        creator, victim, other = make_user(), make_user(), make_user()
        t = _thread(make_thread, make_thread_member, creator, member_count=3)
        make_thread_member(t, victim)
        make_thread_member(t, other)
        resp = client.delete(f"{B}/{t.id}/remove/{victim.id}",
                             headers=_hdr(client, auth_headers, csrf_headers, creator))
        assert resp.status_code == 200
        assert f"thread_{t.id}" in fake.closed
        events = {(e, r) for e, r, _ in fake.emits}
        assert ("thread_access_revoked", f"user_{victim.id}") in events
        assert ("thread_room_resync", f"user_{other.id}") in events
        assert ("thread_room_resync", f"user_{victim.id}") not in events

    def test_leave_closes_room(
        self, client, db_session, make_user, make_thread, make_thread_member,
        auth_headers, csrf_headers, monkeypatch,
    ):
        fake = self._manager(monkeypatch)
        creator, member = make_user(), make_user()
        t = _thread(make_thread, make_thread_member, creator, member_count=2)
        make_thread_member(t, member)
        resp = client.post(f"{B}/{t.id}/leave",
                           headers=_hdr(client, auth_headers, csrf_headers, member))
        assert resp.status_code == 200
        assert f"thread_{t.id}" in fake.closed
        assert any(e == "thread_member_left" for e, _r, _d in fake.emits)

    def test_delete_thread_closes_room(
        self, client, db_session, make_user, make_thread, make_thread_member,
        auth_headers, csrf_headers, monkeypatch,
    ):
        fake = self._manager(monkeypatch)
        creator = make_user()
        t = _thread(make_thread, make_thread_member, creator)
        resp = client.delete(f"{B}/{t.id}",
                             headers=_hdr(client, auth_headers, csrf_headers, creator))
        assert resp.status_code == 200
        assert f"thread_{t.id}" in fake.closed
