"""
tests/integration/test_posts_discovery_routes.py

routes/student/posts/discovery.py has 4 endpoints and previously had
zero tests at any level: comment_resources, post_resources,
get_posts_by_tag, popular_tags.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET /comments/<id>/resources
# ============================================================================

class TestCommentResources:
    def test_returns_comment_resources(
        self, client, make_user, make_post, make_comment, auth_headers
    ):
        author = make_user()
        post = make_post(author)
        comment = make_comment(post, author, resources=["http://example.com/notes.pdf"])

        resp = client.get(f"/student/comments/{comment.id}/resources", headers=auth_headers(author))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["id"] == comment.id
        assert data["resources"] == ["http://example.com/notes.pdf"]

    def test_nonexistent_comment_returns_error(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/comments/999999/resources", headers=auth_headers(user))

        assert resp.get_json()["status"] == "error"


# ============================================================================
# GET /posts/<id>/resources
# ============================================================================

class TestPostResources:
    def test_returns_post_resources(self, client, make_user, make_post, auth_headers):
        author = make_user()
        post = make_post(author, resources=["http://example.com/slides.pdf"])

        resp = client.get(f"/student/posts/{post.id}/resources", headers=auth_headers(author))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["id"] == post.id
        assert data["resources"] == ["http://example.com/slides.pdf"]

    def test_nonexistent_post_returns_error(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/posts/999999/resources", headers=auth_headers(user))

        assert resp.get_json()["status"] == "error"


# ============================================================================
# GET /posts/tags/<tag>
# ============================================================================

class TestGetPostsByTag:
    def test_nonmatching_tag_returns_empty(self, client, make_user, make_post, auth_headers):
        author = make_user()
        make_post(author, tags=["python"])

        resp = client.get("/student/posts/tags/ruby", headers=auth_headers(author))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["posts"] == []

    def test_pagination_fields_present(self, client, make_user, make_post, auth_headers):
        author = make_user()
        make_post(author, tags=["python"])

        resp = client.get("/student/posts/tags/python", headers=auth_headers(author))

        assert resp.status_code == 200
        pagination = resp.get_json()["data"]["pagination"]
        assert pagination["page"] == 1
        assert pagination["per_page"] == 20
        assert resp.get_json()["data"]["tag"] == "python"


# ============================================================================
# GET /posts/tags (popular_tags)
# ============================================================================

class TestPopularTags:
    def test_empty_when_no_posts(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/posts/tags", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"] == {}

    def test_counts_tags_across_posts(self, client, make_user, make_post, auth_headers):
        author = make_user()
        make_post(author, tags=["python", "flask"])
        make_post(author, tags=["python"])

        resp = client.get("/student/posts/tags", headers=auth_headers(author))

        data = resp.get_json()["data"]
        assert data["python"] == 2
        assert data["flask"] == 1

    def test_tags_are_lowercased_and_deduped(self, client, make_user, make_post, auth_headers):
        author = make_user()
        make_post(author, tags=["Python", "PYTHON"])

        resp = client.get("/student/posts/tags", headers=auth_headers(author))

        data = resp.get_json()["data"]
        assert data == {"python": 2}
