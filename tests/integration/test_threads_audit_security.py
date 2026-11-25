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
