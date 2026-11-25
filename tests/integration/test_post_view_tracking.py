"""POST /student/posts/<id>/view counts each viewer exactly once (AN-05)."""
import threading

import pytest

from extensions import db
from models import Post, PostView

pytestmark = pytest.mark.integration


def _view(client, headers, csrf_headers, post_id):
    return client.post(f"/student/posts/{post_id}/view", headers={**headers, **csrf_headers(client)})


def test_repeat_views_by_same_user_count_once(client, db_session, make_user, make_post, auth_headers, csrf_headers):
    author, viewer = make_user(), make_user()
    post = make_post(author)
    db_session.commit()
    pid, vid = post.id, viewer.id

    first = _view(client, auth_headers(viewer), csrf_headers, pid)
    second = _view(client, auth_headers(viewer), csrf_headers, pid)

    assert first.status_code == 200 and second.status_code == 200
    assert second.get_json()["message"] == "Already viewed"
    db_session.expire_all()
    assert PostView.query.filter_by(user_id=vid, post_id=pid).count() == 1
    assert db.session.get(Post, pid).views_count == 1


def test_different_viewers_each_count(client, db_session, make_user, make_post, auth_headers, csrf_headers):
    author, v1, v2 = make_user(), make_user(), make_user()
    post = make_post(author)
    db_session.commit()
    pid = post.id

    _view(client, auth_headers(v1), csrf_headers, pid)
    _view(client, auth_headers(v2), csrf_headers, pid)

    db_session.expire_all()
    assert db.session.get(Post, pid).views_count == 2


def test_unknown_post_does_not_create_rows(client, db_session, make_user, auth_headers, csrf_headers):
    viewer = make_user()
    db_session.commit()

    resp = _view(client, auth_headers(viewer), csrf_headers, 999999)

    assert resp.status_code >= 400
    assert PostView.query.count() == 0


def test_concurrent_views_by_one_user_create_one_row(app, db_session, make_user, make_post, auth_headers):
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            pytest.skip("needs PostgreSQL")
    author, viewer = make_user(), make_user()
    post = make_post(author)
    db_session.commit()
    pid, vid = post.id, viewer.id
    headers = {**auth_headers(viewer), "X-CSRF-Token": "t"}

    n = 12
    barrier = threading.Barrier(n)
    codes = []

    def worker():
        with app.app_context():
            c = app.test_client()
            c.set_cookie("csrf_token", "t")
            barrier.wait()
            codes.append(c.post(f"/student/posts/{pid}/view", headers=headers).status_code)
            db.session.remove()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert set(codes) == {200}
    db_session.expire_all()
    assert PostView.query.filter_by(user_id=vid, post_id=pid).count() == 1
    assert db.session.get(Post, pid).views_count == 1
