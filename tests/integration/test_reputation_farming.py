"""Regression tests: leaderboard points must not be farmable by toggling.

Each test drives the real routes with two cooperating accounts and asserts
that repeating a state transition does not pay out again.
"""
from models import ReputationHistory, User


def _awards(db_session, user_id, action):
    db_session.expire_all()
    return db_session.query(ReputationHistory).filter_by(user_id=user_id, action=action).count()


def test_like_unlike_cycles_at_the_milestone_pay_once(
    client, db_session, make_user, make_post, auth_headers, csrf_headers, fakeredis_client
):
    author, alt = make_user(), make_user()
    post = make_post(author, positive_reactions_count=9)
    headers = lambda: {**auth_headers(alt), **csrf_headers(client)}

    for _ in range(6):
        assert client.post(f"/student/posts/{post.id}/react", json={}, headers=headers()).status_code == 201  # like -> 10
        assert client.post(f"/student/posts/{post.id}/react", json={}, headers=headers()).status_code == 200  # unlike -> 9

    assert _awards(db_session, author.id, "post_10_likes") == 1
    assert db_session.get(User, author.id).reputation == 5


def test_helpful_mark_unmark_cycles_pay_once_per_marker(
    client, db_session, make_user, make_post, make_comment, auth_headers, csrf_headers, fakeredis_client
):
    author, alt = make_user(), make_user()
    comment = make_comment(make_post(make_user()), author)
    headers = lambda: {**auth_headers(alt), **csrf_headers(client)}

    for _ in range(10):                          # 5 marks, 5 unmarks
        client.post(f"/student/comments/{comment.id}/mark-helpful", json={}, headers=headers())

    assert _awards(db_session, author.id, "comment_marked_helpful") == 1
    assert db_session.get(User, author.id).reputation == 3


def test_two_different_markers_each_pay(
    client, db_session, make_user, make_post, make_comment, auth_headers, csrf_headers, fakeredis_client
):
    author, marker_a, marker_b = make_user(), make_user(), make_user()
    comment = make_comment(make_post(make_user()), author)
    for marker in (marker_a, marker_b):
        client.post(f"/student/comments/{comment.id}/mark-helpful", json={},
                    headers={**auth_headers(marker), **csrf_headers(client)})

    assert _awards(db_session, author.id, "comment_marked_helpful") == 2


def test_repeating_mark_solution_pays_once(
    client, db_session, make_user, make_post, make_comment, auth_headers, csrf_headers, fakeredis_client
):
    poster, helper = make_user(), make_user()
    post = make_post(poster, post_type="question")
    comment = make_comment(post, helper)
    headers = lambda: {**auth_headers(poster), **csrf_headers(client)}

    for _ in range(5):
        assert client.post(f"/student/posts/{post.id}/mark-solution",
                           json={"comment_id": comment.id}, headers=headers()).status_code == 200

    assert _awards(db_session, helper.id, "comment_marked_solution") == 1
    assert db_session.get(User, helper.id).reputation == 15
