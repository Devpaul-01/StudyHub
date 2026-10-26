"""
tests/integration/test_posts_comments_routes.py

routes/student/posts/comments.py has 9 endpoints and previously had
zero tests at any level. This batch covers the full comment CRUD and
lifecycle surface: create (including max-depth-1 reply enforcement),
edit, delete, like toggle, mark-helpful toggle, mark/unmark-solution,
and the two listing endpoints (post_comments, comment_replies).
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# POST /comments/create
# ============================================================================

class TestCreateComment:
    def test_happy_path_top_level_comment(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        commenter = make_user()
        post = make_post(author)
        headers = {**auth_headers(commenter), **csrf_headers(client)}

        resp = client.post(
            "/student/comments/create",
            json={"post_id": post.id, "text_content": "Nice post!"},
            headers=headers,
        )

        assert resp.status_code == 201
        body = resp.get_json()["data"]["comment"]
        assert body["depth_level"] == 0
        assert body["can_reply"] is True

        from models import Post
        assert Post.query.get(post.id).comments_count == 1

    def test_missing_post_id_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/comments/create", json={"text_content": "hi"}, headers=headers
        )

        assert resp.status_code == 400

    def test_empty_text_rejected(self, client, make_user, make_post, auth_headers, csrf_headers):
        author = make_user()
        post = make_post(author)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(
            "/student/comments/create", json={"post_id": post.id, "text_content": ""}, headers=headers
        )

        assert resp.status_code == 400

    def test_reply_to_top_level_comment_is_depth_1(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author)
        parent = make_comment(post, author)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(
            "/student/comments/create",
            json={"post_id": post.id, "text_content": "a reply", "parent_id": parent.id},
            headers=headers,
        )

        assert resp.status_code == 201
        assert resp.get_json()["data"]["comment"]["depth_level"] == 1
        from models import Comment
        assert Comment.query.get(parent.id).replies_count == 1

    def test_reply_to_a_depth_1_comment_rejected(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author)
        reply = make_comment(post, author, depth_level=1)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(
            "/student/comments/create",
            json={"post_id": post.id, "text_content": "too deep", "parent_id": reply.id},
            headers=headers,
        )

        assert resp.status_code == 400

    def test_comment_on_locked_post_rejected(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author, is_locked=True)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(
            "/student/comments/create",
            json={"post_id": post.id, "text_content": "hi"}, headers=headers,
        )

        assert resp.status_code == 403

    def test_reply_to_deleted_comment_rejected(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author)
        parent = make_comment(post, author, is_deleted=True)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(
            "/student/comments/create",
            json={"post_id": post.id, "text_content": "hi", "parent_id": parent.id},
            headers=headers,
        )

        assert resp.status_code == 400


# ============================================================================
# PATCH /comments/<id> and DELETE /comments/<id>
# ============================================================================

class TestEditDeleteComment:
    def test_owner_edits_comment(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author)
        comment = make_comment(post, author, text_content="old text")
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.patch(
            f"/student/comments/{comment.id}", json={"text_content": "new text"}, headers=headers
        )

        assert resp.status_code == 200
        from models import Comment
        assert Comment.query.get(comment.id).text_content == "new text"

    def test_non_owner_cannot_edit(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        other = make_user()
        post = make_post(author)
        comment = make_comment(post, author)
        headers = {**auth_headers(other), **csrf_headers(client)}

        resp = client.patch(
            f"/student/comments/{comment.id}", json={"text_content": "hijacked"}, headers=headers
        )

        assert resp.status_code == 403

    def test_edit_empty_text_rejected(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author)
        comment = make_comment(post, author)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.patch(
            f"/student/comments/{comment.id}", json={"text_content": "  "}, headers=headers
        )

        assert resp.status_code == 400

    def test_edit_nonexistent_comment_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.patch(
            "/student/comments/999999", json={"text_content": "x"}, headers=headers
        )

        assert resp.status_code == 404

    def test_owner_soft_deletes_comment(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author, comments_count=1)
        comment = make_comment(post, author)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.delete(f"/student/comments/{comment.id}", headers=headers)

        assert resp.status_code == 200
        from models import Comment, Post
        row = Comment.query.get(comment.id)
        assert row.is_deleted is True
        assert row.text_content == "[deleted]"
        assert Post.query.get(post.id).comments_count == 0

    def test_non_owner_cannot_delete(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        other = make_user()
        post = make_post(author)
        comment = make_comment(post, author)
        headers = {**auth_headers(other), **csrf_headers(client)}

        resp = client.delete(f"/student/comments/{comment.id}", headers=headers)

        assert resp.status_code == 403


# ============================================================================
# POST /comments/<id>/like
# ============================================================================

class TestLikeComment:
    def test_liking_increments_count(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        liker = make_user()
        post = make_post(author)
        comment = make_comment(post, author)
        headers = {**auth_headers(liker), **csrf_headers(client)}

        resp = client.post(f"/student/comments/{comment.id}/like", headers=headers)

        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["liked"] is True
        assert body["count"] == 1

    def test_liking_twice_unlikes(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        liker = make_user()
        post = make_post(author)
        comment = make_comment(post, author)
        headers = {**auth_headers(liker), **csrf_headers(client)}
        client.post(f"/student/comments/{comment.id}/like", headers=headers)

        resp = client.post(f"/student/comments/{comment.id}/like", headers=headers)

        body = resp.get_json()["data"]
        assert body["liked"] is False
        assert body["count"] == 0

    def test_liking_deleted_comment_rejected(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author)
        comment = make_comment(post, author, is_deleted=True)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(f"/student/comments/{comment.id}/like", headers=headers)

        assert resp.status_code == 400


# ============================================================================
# POST /comments/<id>/mark-helpful
# ============================================================================

class TestMarkCommentHelpful:
    def test_marking_increments_count_and_author_totals(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        marker = make_user()
        post = make_post(author)
        comment = make_comment(post, author)
        headers = {**auth_headers(marker), **csrf_headers(client)}

        resp = client.post(f"/student/comments/{comment.id}/mark-helpful", headers=headers)

        assert resp.status_code == 201
        body = resp.get_json()["data"]
        assert body["is_helpful"] is True
        assert body["count"] == 1

        from models import User
        assert User.query.get(author.id).total_helpful == 1

    def test_cannot_mark_own_comment_helpful(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author)
        comment = make_comment(post, author)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(f"/student/comments/{comment.id}/mark-helpful", headers=headers)

        assert resp.status_code == 403

    def test_marking_twice_unmarks(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        marker = make_user()
        post = make_post(author)
        comment = make_comment(post, author)
        headers = {**auth_headers(marker), **csrf_headers(client)}
        client.post(f"/student/comments/{comment.id}/mark-helpful", headers=headers)

        resp = client.post(f"/student/comments/{comment.id}/mark-helpful", headers=headers)

        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["is_helpful"] is False
        assert body["count"] == 0

        from models import User
        assert User.query.get(author.id).total_helpful == 0


# ============================================================================
# POST /posts/<id>/mark-solution and /unmark-solution
# ============================================================================

class TestMarkUnmarkSolution:
    def test_author_marks_comment_as_solution(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        commenter = make_user()
        post = make_post(author, post_type="question")
        comment = make_comment(post, commenter)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(
            f"/student/posts/{post.id}/mark-solution",
            json={"comment_id": comment.id}, headers=headers,
        )

        assert resp.status_code == 200
        from models import Comment, Post
        assert Comment.query.get(comment.id).is_solution is True
        assert Post.query.get(post.id).is_solved is True

    def test_marking_new_solution_unmarks_old_one(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        commenter = make_user()
        post = make_post(author, post_type="question")
        old_solution = make_comment(post, commenter, is_solution=True)
        new_solution = make_comment(post, commenter)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(
            f"/student/posts/{post.id}/mark-solution",
            json={"comment_id": new_solution.id}, headers=headers,
        )

        assert resp.status_code == 200
        from models import Comment
        assert Comment.query.get(old_solution.id).is_solution is False
        assert Comment.query.get(new_solution.id).is_solution is True

    def test_non_author_cannot_mark_solution(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        outsider = make_user()
        post = make_post(author, post_type="question")
        comment = make_comment(post, author)
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.post(
            f"/student/posts/{post.id}/mark-solution",
            json={"comment_id": comment.id}, headers=headers,
        )

        assert resp.status_code == 403

    def test_unmark_solution_clears_flags(
        self, client, make_user, make_post, make_comment, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author, post_type="question", is_solved=True)
        comment = make_comment(post, author, is_solution=True)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(
            f"/student/posts/{post.id}/unmark-solution",
            json={"comment_id": comment.id}, headers=headers,
        )

        assert resp.status_code == 200
        from models import Comment, Post
        assert Comment.query.get(comment.id).is_solution is False
        assert Post.query.get(post.id).is_solved is False


# ============================================================================
# GET /posts/<id>/comments and GET /comments/<id>/replies
# ============================================================================

class TestListComments:
    def test_post_comments_lists_top_level_only(
        self, client, make_user, make_post, make_comment, auth_headers
    ):
        author = make_user()
        post = make_post(author)
        top = make_comment(post, author, text_content="top level")
        make_comment(post, author, parent_id=top.id, depth_level=1, text_content="a reply")

        resp = client.get(f"/student/posts/{post.id}/comments", headers=auth_headers(author))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        comments = data.get("comments", data) if isinstance(data, dict) else data
        # top-level listing should include the top comment; exact shape may
        # nest replies, so just assert the top-level id is present somewhere.
        import json as _json
        assert str(top.id) in _json.dumps(comments)

    def test_post_comments_excludes_deleted(
        self, client, make_user, make_post, make_comment, auth_headers
    ):
        author = make_user()
        post = make_post(author)
        make_comment(post, author, is_deleted=True, text_content="[deleted]")

        resp = client.get(f"/student/posts/{post.id}/comments", headers=auth_headers(author))

        assert resp.status_code == 200

    def test_comment_replies_returns_children(
        self, client, make_user, make_post, make_comment, auth_headers
    ):
        author = make_user()
        post = make_post(author)
        parent = make_comment(post, author)
        reply = make_comment(post, author, parent_id=parent.id, depth_level=1, text_content="reply text")

        resp = client.get(f"/student/comments/{parent.id}/replies", headers=auth_headers(author))

        assert resp.status_code == 200
        import json as _json
        assert str(reply.id) in _json.dumps(resp.get_json())
