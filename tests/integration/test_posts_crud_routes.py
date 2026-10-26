"""
tests/integration/test_posts_crud_routes.py

routes/student/posts/crud.py has 19 endpoints and previously had zero
tests at any level. This batch covers the core post CRUD and lifecycle
surface: create, get, edit, delete, mark/unmark-solved, follow/unfollow,
and the like toggle. Feed/listing endpoints (get_feed, get_posts_by_type,
get_posts_by_status, get_my_posts), post_options_menu, metrics, reporting,
and file-upload (upload_post_resource) are a separate, larger area left
for a future pass.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# POST /posts (create_post)
# ============================================================================

class TestCreatePost:
    def test_happy_path_creates_post(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/posts/create",
            json={
                "title": "A valid question title",
                "text_content": "Body text",
                "post_type": "question",
                "tags": ["calculus", "homework"],
            },
            headers=headers,
        )

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["data"]["post"]["title"] == "A valid question title"
        assert body["data"]["post"]["post_type"] == "question"

        from models import Post
        row = Post.query.get(body["data"]["post"]["id"])
        assert row.student_id == user.id

    def test_missing_title_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/posts/create", json={"text_content": "Body"}, headers=headers
        )

        assert resp.status_code == 400
        assert "Title is required" in resp.get_json()["message"]

    def test_title_too_short_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/posts/create", json={"title": "Hi"}, headers=headers
        )

        assert resp.status_code == 400
        assert "too short" in resp.get_json()["message"].lower()

    def test_invalid_post_type_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/posts/create",
            json={"title": "A valid title here", "post_type": "not-a-type"},
            headers=headers,
        )

        assert resp.status_code == 400
        assert "Invalid post type" in resp.get_json()["message"]

    def test_tags_are_truncated_to_five(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/posts/create",
            json={"title": "A valid title here", "tags": ["a", "b", "c", "d", "e", "f", "g"]},
            headers=headers,
        )

        assert resp.status_code == 201
        from models import Post
        row = Post.query.get(resp.get_json()["data"]["post"]["id"])
        assert len(row.tags) == 5

    def test_creating_with_thread_enabled_creates_a_thread(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/posts/create",
            json={"title": "A valid title here", "thread_enabled": True},
            headers=headers,
        )

        assert resp.status_code == 201
        post_id = resp.get_json()["data"]["post"]["id"]

        from models import Thread
        thread = Thread.query.filter_by(post_id=post_id).first()
        assert thread is not None
        assert thread.creator_id == user.id


# ============================================================================
# GET /posts/<id>
# ============================================================================

class TestGetPost:
    def test_returns_post_with_author_and_permissions(
        self, client, make_user, make_post, auth_headers
    ):
        author = make_user()
        post = make_post(author, title="My post")
        other = make_user()

        resp = client.get(f"/student/posts/{post.id}", headers=auth_headers(other))

        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["post"]["title"] == "My post"
        assert body["author"]["id"] == author.id
        assert body["user_interaction"]["is_author"] is False
        assert body["permissions"]["can_edit"] is False

    def test_author_sees_can_edit_true(self, client, make_user, make_post, auth_headers):
        author = make_user()
        post = make_post(author)

        resp = client.get(f"/student/posts/{post.id}", headers=auth_headers(author))

        body = resp.get_json()["data"]
        assert body["user_interaction"]["is_author"] is True
        assert body["permissions"]["can_edit"] is True

    def test_increments_views_count(self, client, make_user, make_post, auth_headers):
        author = make_user()
        post = make_post(author)
        assert post.views_count == 0

        client.get(f"/student/posts/{post.id}", headers=auth_headers(author))

        from models import Post
        assert Post.query.get(post.id).views_count == 1

    def test_nonexistent_post_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/posts/999999", headers=auth_headers(user))

        assert resp.status_code == 404


# ============================================================================
# PATCH /posts/<id>/edit
# ============================================================================

class TestEditPost:
    def test_owner_updates_title(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        post = make_post(author, title="Old title")
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.patch(
            f"/student/posts/{post.id}/edit",
            json={"title": "New valid title"},
            headers=headers,
        )

        assert resp.status_code == 200
        assert "title" in resp.get_json()["data"]["changes"]

        from models import Post
        assert Post.query.get(post.id).title == "New valid title"

    def test_non_owner_cannot_edit(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        other = make_user()
        post = make_post(author)
        headers = {**auth_headers(other), **csrf_headers(client)}

        resp = client.patch(
            f"/student/posts/{post.id}/edit", json={"title": "Hijacked title"}, headers=headers
        )

        assert resp.status_code == 403

    def test_nonexistent_post_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.patch(
            "/student/posts/999999/edit", json={"title": "New valid title"}, headers=headers
        )

        assert resp.status_code == 404

    def test_title_too_short_rejected(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        post = make_post(author)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.patch(
            f"/student/posts/{post.id}/edit", json={"title": "Hi"}, headers=headers
        )

        assert resp.status_code == 400

    def test_no_changes_returns_success_without_edited_at(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author, title="Same title")
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.patch(
            f"/student/posts/{post.id}/edit", json={"title": "Same title"}, headers=headers
        )

        assert resp.status_code == 200
        assert resp.get_json()["message"] == "No changes made"


# ============================================================================
# DELETE /posts/<id>
# ============================================================================

class TestDeletePost:
    def test_owner_deletes_post(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        post = make_post(author)
        post_id = post.id
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.delete(f"/student/posts/{post_id}", headers=headers)

        assert resp.status_code == 200
        from models import Post
        assert Post.query.get(post_id) is None

    def test_non_owner_cannot_delete(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        other = make_user()
        post = make_post(author)
        headers = {**auth_headers(other), **csrf_headers(client)}

        resp = client.delete(f"/student/posts/{post.id}", headers=headers)

        assert resp.status_code == 403

    def test_nonexistent_post_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete("/student/posts/999999", headers=headers)

        assert resp.status_code == 404

    def test_deleting_decrements_total_posts(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        author.total_posts = 3
        post = make_post(author)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.delete(f"/student/posts/{post.id}", headers=headers)

        assert resp.status_code == 200
        from models import User
        assert User.query.get(author.id).total_posts == 2


# ============================================================================
# mark-solved / unmark-solved
# ============================================================================

class TestMarkSolved:
    def test_owner_marks_question_solved(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        post = make_post(author, post_type="question")
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(f"/student/posts/{post.id}/mark-solved", headers=headers)

        assert resp.status_code == 200
        from models import Post
        row = Post.query.get(post.id)
        assert row.is_solved is True
        assert row.solved_at is not None

    def test_non_owner_cannot_mark_solved(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        other = make_user()
        post = make_post(author, post_type="question")
        headers = {**auth_headers(other), **csrf_headers(client)}

        resp = client.post(f"/student/posts/{post.id}/mark-solved", headers=headers)

        assert resp.status_code == 403

    def test_discussion_post_type_cannot_be_marked_solved(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author, post_type="discussion")
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(f"/student/posts/{post.id}/mark-solved", headers=headers)

        assert resp.status_code == 400

    def test_unmark_solved_clears_flag_and_solution_comments(
        self, client, db_session, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author, post_type="question", is_solved=True)
        comment = make_comment(post, author, is_solution=True)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(f"/student/posts/{post.id}/unmark-solved", headers=headers)

        assert resp.status_code == 200
        from models import Post, Comment
        assert Post.query.get(post.id).is_solved is False
        assert Comment.query.get(comment.id).is_solution is False


# ============================================================================
# follow / unfollow
# ============================================================================

class TestFollowUnfollowPost:
    def test_follow_creates_a_postfollow_row(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        follower = make_user()
        post = make_post(author)
        headers = {**auth_headers(follower), **csrf_headers(client)}

        resp = client.post(f"/student/posts/{post.id}/follow", headers=headers)

        assert resp.status_code == 201
        from models import PostFollow
        assert PostFollow.query.filter_by(post_id=post.id, student_id=follower.id).first() is not None

    def test_following_twice_is_rejected(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        follower = make_user()
        post = make_post(author)
        headers = {**auth_headers(follower), **csrf_headers(client)}

        client.post(f"/student/posts/{post.id}/follow", headers=headers)
        resp = client.post(f"/student/posts/{post.id}/follow", headers=headers)

        assert resp.status_code == 409

    def test_unfollow_removes_the_row(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        follower = make_user()
        post = make_post(author)
        headers = {**auth_headers(follower), **csrf_headers(client)}
        client.post(f"/student/posts/{post.id}/follow", headers=headers)

        resp = client.delete(f"/student/posts/{post.id}/unfollow", headers=headers)

        assert resp.status_code == 200
        from models import PostFollow
        assert PostFollow.query.filter_by(post_id=post.id, student_id=follower.id).first() is None

    def test_unfollow_when_not_following_returns_404(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        user = make_user()
        post = make_post(author)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete(f"/student/posts/{post.id}/unfollow", headers=headers)

        assert resp.status_code == 404


# ============================================================================
# react (like toggle)
# ============================================================================

class TestReactToPost:
    def test_liking_a_post_increments_count(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        liker = make_user()
        post = make_post(author)
        headers = {**auth_headers(liker), **csrf_headers(client)}

        resp = client.post(f"/student/posts/{post.id}/react", headers=headers)

        assert resp.status_code == 201
        body = resp.get_json()["data"]
        assert body["liked"] is True
        assert body["count"] == 1

    def test_liking_twice_unlikes(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        liker = make_user()
        post = make_post(author)
        headers = {**auth_headers(liker), **csrf_headers(client)}
        client.post(f"/student/posts/{post.id}/react", headers=headers)

        resp = client.post(f"/student/posts/{post.id}/react", headers=headers)

        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["liked"] is False
        assert body["count"] == 0

        from models import PostReaction
        assert PostReaction.query.filter_by(post_id=post.id, student_id=liker.id).first() is None

    def test_react_to_nonexistent_post_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/posts/999999/react", headers=headers)

        assert resp.status_code == 404
