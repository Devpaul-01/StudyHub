import threading
from unittest.mock import patch

from sqlalchemy import text


def test_concurrent_awards_to_one_user_are_not_lost(pg_app, pg_db, make_pg_user):
    """Two transactions award the same user at the same time. Before the row
    lock, both read 100 and the later UPDATE overwrote the earlier one."""
    from services import reputation_service as rs

    uid = make_pg_user(reputation=100)
    both_have_awarded = threading.Barrier(2, timeout=10)
    errors = []

    def worker(action):
        try:
            with pg_app.app_context():
                with patch("services.reputation_service.notification_service.notify_level_up"):
                    rs.award_reputation(uid, action)
                # Neither transaction has committed yet. With the lock the
                # second one would block here, so only wait briefly.
                try:
                    both_have_awarded.wait(timeout=1)
                except threading.BrokenBarrierError:
                    pass
                pg_db.session.commit()
        except Exception as exc:  # pragma: no cover - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(a,))
               for a in ("comment_marked_solution", "comment_marked_helpful")]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert not errors, errors

    with pg_app.app_context():
        rep = pg_db.session.execute(text("select reputation from users where id=:i"), {"i": uid}).scalar()
        ledger = pg_db.session.execute(
            text("select coalesce(sum(points_change),0) from reputation_history where user_id=:i"),
            {"i": uid}).scalar()
    assert ledger == 18
    assert rep == 100 + ledger
