"""
Regression tests for the reputation-farming findings (audit F-01/F-02/F-03)
and the single-transaction behaviour of the routes that award reputation.
"""

from unittest.mock import patch

import pytest

from extensions import db
from models import CommentHelpfulMark, Notification, Post, ReputationHistory

pytestmark = pytest.mark.integration


def _h(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


def _rows(user, action):
    return ReputationHistory.query.filter_by(user_id=user.id, action=action).count()


# ── F-01: helpful marks ─────────────────────────────────────────────────────

def test_helpful_mark_cycle_pays_exactly_once(
    client, make_user, make_post, make_comment, auth_headers, csrf_headers
):
    author = make_user(status="approved", reputation=0)
    marker = make_user(status="approved")
    comment = make_comment(make_post(author), author)
    h = _h(client, auth_headers, csrf_headers, marker)

    codes = [client.post(f"/student/comments/{comment.id}/mark-helpful", headers=h).status_code
             for _ in range(7)]                    # mark, unmark, mark, ...

    db.session.refresh(author)
    db.session.refresh(comment)
    assert codes == [201, 200, 201, 200, 201, 200, 201]
    assert _rows(author, "comment_marked_helpful") == 1
    assert author.reputation == 3
    assert author.total_helpful == 1 and comment.helpful_count == 1


def test_helpful_unmark_soft_deletes_and_keeps_the_row(
    client, make_user, make_post, make_comment, auth_headers, csrf_headers
):
    author = make_user(status="approved")
    marker = make_user(status="approved")
    comment = make_comment(make_post(author), author)
    h = _h(client, auth_headers, csrf_headers, marker)

    client.post(f"/student/comments/{comment.id}/mark-helpful", headers=h)
    client.post(f"/student/comments/{comment.id}/mark-helpful", headers=h)

    mark = CommentHelpfulMark.query.filter_by(comment_id=comment.id, user_id=marker.id).one()
    assert mark.is_active is False
    db.session.refresh(comment)
    assert comment.helpful_count == 0


def test_different_markers_each_pay_once(
    client, make_user, make_post, make_comment, auth_headers, csrf_headers
):
    author = make_user(status="approved", reputation=0)
    comment = make_comment(make_post(author), author)
    for _ in range(3):
        m = make_user(status="approved")
        client.post(f"/student/comments/{comment.id}/mark-helpful",
                    headers=_h(client, auth_headers, csrf_headers, m))
    db.session.refresh(author)
    assert author.reputation == 9 and author.total_helpful == 3


def test_helpful_mark_reputation_failure_rolls_back_the_mark(
    client, make_user, make_post, make_comment, auth_headers, csrf_headers
):
    """One transaction: if the award blows up, no mark persists either."""
    author = make_user(status="approved", reputation=0)
    marker = make_user(status="approved")
    comment = make_comment(make_post(author), author)
    db.session.commit()      # persist fixtures: the route's rollback must not discard them
    with patch("routes.student.posts.comments.award_reputation", side_effect=RuntimeError("boom")):
        r = client.post(f"/student/comments/{comment.id}/mark-helpful",
                        headers=_h(client, auth_headers, csrf_headers, marker))
    assert r.status_code >= 400
    assert CommentHelpfulMark.query.filter_by(comment_id=comment.id).count() == 0
    db.session.refresh(author)
    assert author.total_helpful == 0 and author.reputation == 0


def test_helpful_mark_schedules_a_badge_check_after_commit(
    client, make_user, make_post, make_comment, auth_headers, csrf_headers
):
    author = make_user(status="approved")
    marker = make_user(status="approved")
    comment = make_comment(make_post(author), author)
    with patch("routes.student.posts.comments.schedule_badge_check") as sched:
        client.post(f"/student/comments/{comment.id}/mark-helpful",
                    headers=_h(client, auth_headers, csrf_headers, marker))
    sched.assert_called_once_with(author.id)


# ── F-02: solutions ─────────────────────────────────────────────────────────

def _solve(client, h, post, comment):
    return client.post(f"/student/posts/{post.id}/mark-solution",
                       json={"comment_id": comment.id}, headers=h)


def test_repeated_mark_solution_pays_once(
    client, make_user, make_post, make_comment, auth_headers, csrf_headers, seeded_badges
):
    asker = make_user(status="approved")
    solver = make_user(status="approved", reputation=0)
    post = make_post(asker, post_type="question")
    comment = make_comment(post, solver)
    h = _h(client, auth_headers, csrf_headers, asker)

    for _ in range(4):
        assert _solve(client, h, post, comment).status_code == 200

    db.session.refresh(solver)
    assert solver.reputation == 15
    assert _rows(solver, "comment_marked_solution") == 1
    solution_notes = Notification.query.filter_by(
        user_id=solver.id, notification_type="solution_accepted").count()
    assert solution_notes == 1


def test_unmark_solution_reverses_the_reward(
    client, make_user, make_post, make_comment, auth_headers, csrf_headers, seeded_badges
):
    asker = make_user(status="approved")
    solver = make_user(status="approved", reputation=0)
    post = make_post(asker, post_type="question")
    comment = make_comment(post, solver)
    h = _h(client, auth_headers, csrf_headers, asker)

    _solve(client, h, post, comment)
    r = client.post(f"/student/posts/{post.id}/unmark-solution",
                    json={"comment_id": comment.id}, headers=h)
    assert r.status_code == 200
    db.session.refresh(solver)
    assert solver.reputation == 0
    assert _rows(solver, "comment_marked_solution_reversed") == 1

    # a second unmark is an idempotent no-op
    client.post(f"/student/posts/{post.id}/unmark-solution",
                json={"comment_id": comment.id}, headers=h)
    db.session.refresh(solver)
    assert solver.reputation == 0


def test_mark_unmark_remark_cycle_nets_a_single_reward(
    client, make_user, make_post, make_comment, auth_headers, csrf_headers, seeded_badges
):
    asker = make_user(status="approved")
    solver = make_user(status="approved", reputation=0)
    post = make_post(asker, post_type="question")
    comment = make_comment(post, solver)
    h = _h(client, auth_headers, csrf_headers, asker)

    for _ in range(3):
        _solve(client, h, post, comment)
        client.post(f"/student/posts/{post.id}/unmark-solution",
                    json={"comment_id": comment.id}, headers=h)
    _solve(client, h, post, comment)

    db.session.refresh(solver)
    assert solver.reputation == 15


def test_switching_solution_moves_the_reward(
    client, make_user, make_post, make_comment, auth_headers, csrf_headers, seeded_badges
):
    asker = make_user(status="approved")
    a = make_user(status="approved", reputation=0)
    b = make_user(status="approved", reputation=0)
    post = make_post(asker, post_type="question")
    ca, cb = make_comment(post, a), make_comment(post, b)
    h = _h(client, auth_headers, csrf_headers, asker)

    _solve(client, h, post, ca)
    _solve(client, h, post, cb)       # switch A -> B
    _solve(client, h, post, ca)       # and back to A

    for u in (a, b):
        db.session.refresh(u)
    assert (a.reputation, b.reputation) == (15, 0)


# ── F-03: like milestones ───────────────────────────────────────────────────

def test_like_toggle_at_milestone_boundary_pays_once(
    client, make_user, make_post, auth_headers, csrf_headers
):
    author = make_user(status="approved", reputation=0)
    liker = make_user(status="approved")
    post = make_post(author, positive_reactions_count=9)
    h = _h(client, auth_headers, csrf_headers, liker)

    for _ in range(7):                 # like(10) unlike(9) like(10) ...
        assert client.post(f"/student/posts/{post.id}/react", headers=h).status_code in (200, 201)

    db.session.refresh(author)
    assert _rows(author, "post_10_likes") == 1
    assert author.reputation == 5


def test_milestone_failure_does_not_lose_the_like_and_self_heals(
    client, make_user, make_post, auth_headers, csrf_headers
):
    author = make_user(status="approved", reputation=0)
    first, second = make_user(status="approved"), make_user(status="approved")
    post = make_post(author, positive_reactions_count=9)

    with patch("routes.student.posts.crud.check_and_award_milestone",
               side_effect=RuntimeError("award failed")):
        r = client.post(f"/student/posts/{post.id}/react",
                        headers=_h(client, auth_headers, csrf_headers, first))
    assert r.status_code == 201                       # the like itself survived
    db.session.refresh(author)
    assert author.reputation == 0

    # Next like re-evaluates (count >= 10 and not yet paid) and pays it.
    client.post(f"/student/posts/{post.id}/react",
                headers=_h(client, auth_headers, csrf_headers, second))
    db.session.refresh(author)
    assert author.reputation == 5


def test_self_like_never_awards(client, make_user, make_post, auth_headers, csrf_headers):
    author = make_user(status="approved", reputation=0)
    post = make_post(author, positive_reactions_count=9)
    client.post(f"/student/posts/{post.id}/react",
                headers=_h(client, auth_headers, csrf_headers, author))
    db.session.refresh(author)
    assert author.reputation == 0


def test_like_counter_uses_relative_updates(client, make_user, make_post, auth_headers, csrf_headers):
    author = make_user(status="approved")
    liker = make_user(status="approved")
    post = make_post(author, positive_reactions_count=4)
    h = _h(client, auth_headers, csrf_headers, liker)
    # simulate another request landing between our read and write
    Post.query.filter_by(id=post.id).update({"positive_reactions_count": 40})
    db.session.commit()
    r = client.post(f"/student/posts/{post.id}/react", headers=h)
    assert r.get_json()["data"]["count"] == 41
