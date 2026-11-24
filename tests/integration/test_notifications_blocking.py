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


def test_blocked_user_cannot_notify_thread_creator_with_a_join_request(
    client, db_session, make_user, make_thread, make_thread_member, make_connection, auth_headers, csrf_headers
):
    creator = make_user(status="approved")
    abuser = make_user(status="approved")
    make_connection(creator, abuser, status="blocked", blocked_by_id=creator.id)
    thread = make_thread(creator)
    make_thread_member(thread, creator, role="creator")
    headers = {**auth_headers(abuser), **csrf_headers(client)}

    resp = client.post(f"/student/threads/{thread.id}/join", json={}, headers=headers)

    assert resp.status_code in (200, 201), resp.get_json()
    assert _types(creator) == []


def test_thread_join_request_still_notifies_when_not_blocked(
    client, db_session, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
):
    creator = make_user(status="approved")
    requester = make_user(status="approved")
    thread = make_thread(creator)
    make_thread_member(thread, creator, role="creator")
    headers = {**auth_headers(requester), **csrf_headers(client)}

    resp = client.post(f"/student/threads/{thread.id}/join", json={}, headers=headers)

    assert resp.status_code in (200, 201), resp.get_json()
    assert _types(creator) == ["thread_join_request"]
