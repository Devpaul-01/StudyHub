"""
A user who has blocked someone must not receive notifications caused by them
(audit NOTIF-12). Exercises the real routes end to end.
"""
from models import Notification


def _types(user):
    return sorted(n.notification_type for n in Notification.query.filter_by(user_id=user.id).all())


def test_blocked_user_cannot_notify_blocker_by_comment_like_or_mention(
    client, db_session, make_user, make_post, make_comment, make_connection, auth_headers, csrf_headers
):
    blocker = make_user(status="approved", username="blocker_b")
    abuser = make_user(status="approved", username="abuser_a")
    make_connection(blocker, abuser, status="blocked", blocked_by_id=blocker.id)
    post = make_post(blocker, title="My question")
    comment = make_comment(post, blocker)
    headers = {**auth_headers(abuser), **csrf_headers(client)}

    r1 = client.post("/student/comments/create", json={"post_id": post.id, "text_content": "hi @blocker_b"}, headers=headers)
    r2 = client.post(f"/student/comments/{comment.id}/like", headers=headers)

    assert r1.status_code == 201 and r2.status_code == 200  # the actions themselves still work
    assert _types(blocker) == []


def test_unblocked_user_still_notifies(
    client, db_session, make_user, make_post, make_comment, auth_headers, csrf_headers
):
    author = make_user(status="approved")
    other = make_user(status="approved")
    post = make_post(author, title="My question")
    comment = make_comment(post, author)
    headers = {**auth_headers(other), **csrf_headers(client)}

    client.post("/student/comments/create", json={"post_id": post.id, "text_content": "nice"}, headers=headers)
    client.post(f"/student/comments/{comment.id}/like", headers=headers)

    assert _types(author) == ["comment", "like"]
