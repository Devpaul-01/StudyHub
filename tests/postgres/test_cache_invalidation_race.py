import threading
from unittest.mock import patch

import fakeredis
from sqlalchemy import text


def test_reader_caching_mid_transaction_is_not_served_after_commit(pg_app, pg_db, make_pg_user, monkeypatch):
    """A reader that runs between award_reputation() and the commit caches
    the pre-commit state. Invalidation happens after commit and moves the
    user to a new cache version, so that entry must never be served."""
    from services import cache_service, leaderboard_service as ls, reputation_service as rs

    fake = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(cache_service, "redis_client", fake)

    uid = make_pg_user(reputation=100)
    award_done, reader_done = threading.Event(), threading.Event()
    seen = {}

    def writer():
        with pg_app.app_context():
            with patch("services.reputation_service.notification_service.notify_level_up"):
                rs.award_reputation(uid, "comment_marked_solution")   # +15, uncommitted
            award_done.set()
            reader_done.wait(10)
            pg_db.session.commit()

    def reader():
        award_done.wait(10)
        with pg_app.app_context():
            seen["mid"] = ls.get_my_rank(uid, "all_time")["score"]["all_time"]
        reader_done.set()

    tw, tr = threading.Thread(target=writer), threading.Thread(target=reader)
    tw.start(); tr.start(); tw.join(30); tr.join(30)

    with pg_app.app_context():
        truth = pg_db.session.execute(text("select reputation from users where id=:i"), {"i": uid}).scalar()
        after = ls.get_my_rank(uid, "all_time")["score"]["all_time"]
    assert seen["mid"] == 100          # reader legitimately saw committed state
    assert truth == 115
    assert after == 115                # and the stale cache entry is not served
