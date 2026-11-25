"""
Concurrency + failure-isolation tests for daily activity bookkeeping (AN-02).

The race tests need a real multi-connection database, so they only run on
PostgreSQL (set DATABASE_NEW_URL before the app is created); on the default
in-memory SQLite they are skipped. The failure-isolation test runs anywhere.
"""
import threading

import pytest

from extensions import db
from models import UserActivity
from services import daily_activity_service
from services.post_service import update_user_activity

pytestmark = pytest.mark.integration


def _is_postgres(app):
    with app.app_context():
        return db.engine.dialect.name == "postgresql"


def _run_concurrently(app, n, fn):
    barrier = threading.Barrier(n)
    results, errors = [], []

    def worker():
        with app.app_context():
            try:
                barrier.wait()
                results.append(fn())
                db.session.commit()
            except Exception as exc:  # noqa: BLE001 - recorded for assertions
                db.session.rollback()
                errors.append(exc)
            finally:
                db.session.remove()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results, errors


def test_concurrent_first_writes_of_the_day_all_succeed_and_count(app, db_session, make_user):
    if not _is_postgres(app):
        pytest.skip("needs PostgreSQL")
    user = make_user()
    db_session.commit()
    uid = user.id

    n = 10
    _, errors = _run_concurrently(app, n, lambda: update_user_activity(uid, "post"))

    assert errors == []
    db_session.expire_all()
    rows = UserActivity.query.filter_by(user_id=uid).all()
    assert len(rows) == 1
    assert rows[0].posts_created == n
    assert rows[0].activity_score == 5 * n


def test_concurrent_increments_on_existing_row_are_not_lost(app, db_session, make_user):
    if not _is_postgres(app):
        pytest.skip("needs PostgreSQL")
    user = make_user()
    db_session.commit()
    uid = user.id
    update_user_activity(uid, "comment")
    db_session.commit()

    n = 10
    _, errors = _run_concurrently(app, n, lambda: update_user_activity(uid, "comment"))

    assert errors == []
    db_session.expire_all()
    row = UserActivity.query.filter_by(user_id=uid).one()
    assert row.comments_created == n + 1
    assert row.activity_score == 2 * (n + 1)


def test_bookkeeping_failure_does_not_break_the_surrounding_transaction(app, db_session, make_user, monkeypatch):
    user = make_user()
    db_session.commit()
    uid = user.id

    def boom(*args, **kwargs):
        raise RuntimeError("simulated activity failure")

    monkeypatch.setattr(daily_activity_service, "_increment_row", boom)

    user.total_posts = 7  # the "business" change that must still commit
    assert update_user_activity(uid, "post") is None
    db_session.commit()

    db_session.expire_all()
    assert db.session.get(type(user), uid).total_posts == 7
    assert UserActivity.query.filter_by(user_id=uid).count() == 0


def test_login_activity_and_post_activity_share_one_row(db_session, make_user):
    from services import auth_service

    user = make_user()
    db_session.commit()
    auth_service.record_activity(user.id, "login")
    update_user_activity(user.id, "post")
    db_session.commit()

    row = UserActivity.query.filter_by(user_id=user.id).one()
    assert row.posts_created == 1
    assert row.activity_score == 1 + 5
