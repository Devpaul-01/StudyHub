"""
The events that change badge inputs must schedule a badge evaluation (audit
F-05). Before this wiring 16 of the 18 badges could never be awarded
automatically.
"""

from unittest.mock import patch

import pytest

from extensions import db
from models import User

pytestmark = pytest.mark.integration


def _h(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


def _scheduled(mock):
    return sorted(c.args[0] for c in mock.call_args_list)


def test_post_creation_schedules_a_badge_check_and_counts_atomically(
    client, make_user, auth_headers, csrf_headers
):
    user = make_user(status="approved", total_posts=0)
    with patch("routes.student.posts.crud.schedule_badge_check") as sched:
        r = client.post(
            "/student/posts/create",
            json={"title": "Hello world title", "text_content": "body text here",
                  "post_type": "discussion"},
            headers=_h(client, auth_headers, csrf_headers, user),
        )
    assert r.status_code == 201
    sched.assert_called_once_with(user.id)
    db.session.refresh(user)
    assert user.total_posts == 1


def test_login_schedules_a_badge_check(db_session, make_user):
    from services import auth_service
    user = make_user(status="approved")
    with patch("services.auth_service.schedule_badge_check") as sched:
        auth_service.record_login_and_commit(user)
    sched.assert_called_once_with(user.id)


def test_accepting_a_connection_schedules_checks_for_both_users(
    client, make_user, make_connection, auth_headers, csrf_headers
):
    requester, receiver = make_user(), make_user()
    conn = make_connection(requester, receiver, status="pending", responded_at=None)
    with patch("routes.student.connections.crud.schedule_badge_check") as sched:
        r = client.post(f"/student/connections/accept/{conn.id}",
                        headers=_h(client, auth_headers, csrf_headers, receiver))
    assert r.status_code == 200
    assert _scheduled(sched) == sorted([requester.id, receiver.id])


def test_creating_a_thread_schedules_a_badge_check(
    client, make_user, auth_headers, csrf_headers
):
    user = make_user(status="approved")
    with patch("routes.student.threads.crud.schedule_badge_check") as sched:
        r = client.post("/student/threads/create",
                        json={"title": "Calc study group", "max_members": 5},
                        headers=_h(client, auth_headers, csrf_headers, user))
    assert r.status_code in (200, 201)
    sched.assert_called_once_with(user.id)


def test_deleting_a_post_decrements_total_posts_with_a_floor(
    client, make_user, make_post, auth_headers, csrf_headers
):
    user = make_user(status="approved", total_posts=0)
    post = make_post(user)
    r = client.delete(f"/student/posts/{post.id}",
                      headers=_h(client, auth_headers, csrf_headers, user))
    assert r.status_code == 200
    db.session.refresh(user)
    assert user.total_posts == 0          # never negative
