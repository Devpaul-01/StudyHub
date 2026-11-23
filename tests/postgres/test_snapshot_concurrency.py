import threading

from sqlalchemy import event, text
from sqlalchemy.orm import Session


def test_two_concurrent_snapshot_runs_produce_one_row_per_user(pg_app, pg_db, make_pg_user, monkeypatch):
    """Both callers pass the 'already ran today?' check before either has
    committed. The unique index plus ON CONFLICT DO NOTHING must still leave
    exactly one row per user (previously: two)."""
    import fakeredis
    from services import cache_service, leaderboard_service as ls

    monkeypatch.setattr(
        cache_service, "redis_client",
        fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True),
    )
    for _ in range(5):
        make_pg_user(reputation=10)

    pass_guard = threading.Barrier(2, timeout=5)
    tls = threading.local()

    @event.listens_for(Session, "do_orm_execute")
    def hold_after_guard(state):
        # Pause each caller right after its first statement (the guard query)
        # so both have read "nothing ran today" before either inserts.
        if getattr(tls, "armed", False) and state.is_select:
            tls.armed = False
            try:
                pass_guard.wait()
            except threading.BrokenBarrierError:
                pass

    results, errors = [], []

    def run():
        try:
            with pg_app.app_context():
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
    with pg_app.app_context():
        total = pg_db.session.execute(text("select count(*) from leaderboard_snapshots")).scalar()
        users = pg_db.session.execute(text("select count(*) from users where status='approved'")).scalar()
        dupes = pg_db.session.execute(text(
            "select count(*) from (select 1 from leaderboard_snapshots group by user_id, snapshot_type, snapshot_date having count(*)>1) x")).scalar()
    assert dupes == 0
    assert total == users
    assert sum(r["created"] for r in results) == users
