"""Concurrent profile writes (needs a real database with row locks).

SQLite shares one connection and ignores SELECT ... FOR UPDATE, so these
tests only run against PostgreSQL:

    TEST_DATABASE_URL=postgresql://... pytest tests/integration/test_profile_concurrency.py
"""
import os
import threading

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="set TEST_DATABASE_URL to a PostgreSQL database to run concurrency tests",
)

THREADS = 8


def _run_concurrently(app, make_request):
    """Call make_request(client, index) from THREADS threads released together."""
    barrier = threading.Barrier(THREADS)
    results = [None] * THREADS

    def worker(index):
        with app.app_context():
            client = app.test_client()
            barrier.wait()
            results[index] = make_request(client, index)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert None not in results, f"a request never finished (deadlock?): {results}"
    return results


def _headers(client, user, auth_headers, csrf_headers):
    return {**auth_headers(user), **csrf_headers(client)}


def test_concurrent_skill_additions_are_all_kept(app, client, db_session, make_user, auth_headers, csrf_headers):
    """Regression: simultaneous adds read the same list and the last write won."""
    from extensions import db
    from models import User
    user = make_user(skills=[])
    db.session.commit()

    results = _run_concurrently(app, lambda client, i: client.post(
        "/student/profile/skills", json={"skill": f"Skill{i}"},
        headers=_headers(client, user, auth_headers, csrf_headers)).status_code)

    assert results == [201] * THREADS, results
    db.session.expire_all()
    assert sorted(User.query.get(user.id).skills) == sorted(f"Skill{i}" for i in range(THREADS))


def test_skill_limit_cannot_be_exceeded_by_racing_requests(app, client, db_session, make_user, auth_headers, csrf_headers):
    from extensions import db
    from models import User
    user = make_user(skills=[f"Existing{i}" for i in range(7)])    # room for exactly 3 more
    db.session.commit()

    results = _run_concurrently(app, lambda client, i: client.post(
        "/student/profile/skills", json={"skill": f"New{i}"},
        headers=_headers(client, user, auth_headers, csrf_headers)).status_code)

    assert sorted(results).count(201) == 3, results
    db.session.expire_all()
    assert len(User.query.get(user.id).skills) == 10


def test_pin_limit_cannot_be_exceeded_by_racing_requests(app, client, db_session, make_user, make_post, auth_headers, csrf_headers):
    """Regression: count-then-set let simultaneous pins exceed the cap of 5."""
    from extensions import db
    from models import Post
    user = make_user()
    posts = [make_post(user) for _ in range(THREADS)]
    db.session.commit()

    _run_concurrently(app, lambda client, i: client.post(
        f"/student/profile/pin-post/{posts[i].id}",
        headers=_headers(client, user, auth_headers, csrf_headers)).status_code)

    db.session.expire_all()
    assert Post.query.filter_by(student_id=user.id, is_pinned=True).count() == 5


def test_concurrent_partial_privacy_updates_do_not_overwrite_each_other(app, client, db_session, make_user, auth_headers, csrf_headers):
    from extensions import db
    from models import User
    user = make_user(privacy_settings={})
    db.session.commit()
    keys = ["set_profile_private", "show_active_status", "show_in_activity_feed"]
    values = {"set_profile_private": True, "show_active_status": False, "show_in_activity_feed": False}

    def request(client, i):
        key = keys[i % 3]
        return client.post("/student/profile/visibility-settings", json={key: values[key]},
                           headers=_headers(client, user, auth_headers, csrf_headers)).status_code

    results = _run_concurrently(app, request)

    assert set(results) == {200}, results
    db.session.expire_all()
    assert User.query.get(user.id).privacy_settings == values


def test_concurrent_post_creation_counts_every_post(app, client, db_session, make_user, make_student_profile, auth_headers, csrf_headers):
    """Regression: total_posts was a Python read-modify-write and lost increments."""
    from extensions import db
    from models import User
    user = make_user(total_posts=0)
    make_student_profile(user)
    db.session.commit()

    results = _run_concurrently(app, lambda client, i: client.post(
        "/student/posts/create",
        json={"title": f"Concurrent post {i}", "text_content": "Body text for the concurrent post.",
              "post_type": "discussion", "tags": ["race"]},
        headers=_headers(client, user, auth_headers, csrf_headers)).status_code)

    created = sum(1 for r in results if r in (200, 201))
    db.session.expire_all()
    assert created >= 2
    assert User.query.get(user.id).total_posts == created
