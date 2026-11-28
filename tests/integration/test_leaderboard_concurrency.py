"""Leaderboard behaviour that needs a real database (row locks, concurrent
transactions). SQLite shares one connection and ignores SELECT ... FOR UPDATE,
so these only run against PostgreSQL:

    TEST_DATABASE_URL=postgresql://... pytest tests/integration/test_leaderboard_concurrency.py
"""
import os
import threading
from unittest.mock import patch

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="set TEST_DATABASE_URL to a PostgreSQL database to run concurrency tests",
)


def test_concurrent_awards_to_one_user_are_not_lost(app, db_session, make_user):
    """Two transactions award the same user at the same time. Before the row
    lock both read 100 and the later UPDATE overwrote the earlier one."""
    from extensions import db
    from services import reputation_service as rs

    user = make_user(reputation=100)
    db.session.commit()
    uid = user.id
    both_have_awarded = threading.Barrier(2, timeout=10)
    errors = []

    def worker(action):
        try:
            with app.app_context():
                with patch("services.reputation_service.notification_service.notify_level_up"):
                    rs.award_reputation(uid, action)
                # With the lock the second transaction blocks inside award_reputation,
                # so only wait briefly for the other one.
                try:
                    both_have_awarded.wait(timeout=1)
                except threading.BrokenBarrierError:
                    pass
                db.session.commit()
        except Exception as exc:  # pragma: no cover - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(a,))
               for a in ("comment_marked_solution", "comment_marked_helpful")]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert not errors, errors

    with app.app_context():
        rep = db.session.execute(text("select reputation from users where id=:i"), {"i": uid}).scalar()
        ledger = db.session.execute(
            text("select coalesce(sum(points_change),0) from reputation_history where user_id=:i"),
            {"i": uid}).scalar()
    assert ledger == 18
    assert rep == 100 + ledger


def test_reader_caching_mid_transaction_is_not_served_after_commit(app, db_session, make_user, fakeredis_client):
    """A reader that runs between award_reputation() and the commit caches the
    pre-commit state. Invalidation runs after commit and moves the user to a new
    cache version, so that entry must never be served."""
    from extensions import db
    from services import leaderboard_service as ls, reputation_service as rs

    user = make_user(reputation=100, status="approved")
    db.session.commit()
    uid = user.id
    award_done, reader_done = threading.Event(), threading.Event()
    seen = {}

    def writer():
        with app.app_context():
            with patch("services.reputation_service.notification_service.notify_level_up"):
                rs.award_reputation(uid, "comment_marked_solution")   # +15, uncommitted
            award_done.set()
            reader_done.wait(10)
            db.session.commit()

    def reader():
        award_done.wait(10)
        with app.app_context():
            seen["mid"] = ls.get_my_rank(uid, "all_time")["score"]["all_time"]
        reader_done.set()

    tw, tr = threading.Thread(target=writer), threading.Thread(target=reader)
    tw.start(); tr.start(); tw.join(30); tr.join(30)

    with app.app_context():
        truth = db.session.execute(text("select reputation from users where id=:i"), {"i": uid}).scalar()
        after = ls.get_my_rank(uid, "all_time")["score"]["all_time"]
    assert seen["mid"] == 100          # the reader legitimately saw committed state
    assert truth == 115
    assert after == 115                # and its stale cache entry is not served


def test_two_concurrent_snapshot_runs_produce_one_row_per_user(app, db_session, make_user, fakeredis_client):
    """Both callers pass the 'already ran today?' check before either has
    committed. The unique index plus ON CONFLICT DO NOTHING must still leave
    exactly one row per user."""
    from extensions import db
    from services import leaderboard_service as ls

    for _ in range(5):
        make_user(reputation=10, status="approved")
    db.session.commit()

    pass_guard = threading.Barrier(2, timeout=5)
    tls = threading.local()

    @event.listens_for(Session, "do_orm_execute")
    def hold_after_guard(state):
        # Pause each caller right after its first SELECT (the guard query) so both
        # have read "nothing ran today" before either inserts.
        if getattr(tls, "armed", False) and state.is_select:
            tls.armed = False
            try:
                pass_guard.wait()
            except threading.BrokenBarrierError:
                pass

    results, errors = [], []

    def run():
        try:
            with app.app_context():
                tls.armed = True
                results.append(ls.take_snapshot("weekly"))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    try:
        threads = [threading.Thread(target=run) for _ in range(2)]
        [t.start() for t in threads]
        [t.join(timeout=30) for t in threads]
    finally:
        event.remove(Session, "do_orm_execute", hold_after_guard)

    assert not errors, errors
    with app.app_context():
        total = db.session.execute(text("select count(*) from leaderboard_snapshots")).scalar()
        users = db.session.execute(text("select count(*) from users where status='approved'")).scalar()
        dupes = db.session.execute(text(
            "select count(*) from (select 1 from leaderboard_snapshots "
            "group by user_id, snapshot_type, snapshot_date having count(*)>1) x")).scalar()
    assert dupes == 0
    assert total == users
    assert sum(r["created"] for r in results) == users
