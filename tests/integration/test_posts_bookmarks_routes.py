"""
tests/integration/test_posts_bookmarks_routes.py

routes/student/posts/bookmarks.py has 4 endpoints and previously had
zero tests at any level. This batch covers all of them: the two
different toggle implementations (toggle_bookmarks, which organizes
via BookmarkFolder, and bulk_bookmark/bookmark_post, which use the
plain Bookmark.folder string column), and the listing endpoint.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# POST /posts/bookmark/toggle
# ============================================================================

class TestToggleBookmarks:
    def test_bookmarking_creates_a_bookmark_and_folder(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        user = make_user()
        post = make_post(author)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/posts/bookmark/toggle", json={"post_ids": [post.id]}, headers=headers
        )

        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["results"][0]["bookmarked"] is True
        assert body["folder"]["name"] == "Saved"

        from models import Bookmark, Post
        assert Bookmark.query.filter_by(post_id=post.id, student_id=user.id).first() is not None
        assert Post.query.get(post.id).bookmark_count == 1

    def test_toggling_twice_unbookmarks(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        user = make_user()
        post = make_post(author)
        headers = {**auth_headers(user), **csrf_headers(client)}
        client.post("/student/posts/bookmark/toggle", json={"post_ids": [post.id]}, headers=headers)

        resp = client.post(
            "/student/posts/bookmark/toggle", json={"post_ids": [post.id]}, headers=headers
        )

        assert resp.get_json()["data"]["results"][0]["bookmarked"] is False
        from models import Bookmark, Post
        assert Bookmark.query.filter_by(post_id=post.id, student_id=user.id).first() is None
        assert Post.query.get(post.id).bookmark_count == 0

    def test_empty_post_ids_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/posts/bookmark/toggle", json={"post_ids": []}, headers=headers)

        assert resp.status_code == 400

    def test_nonexistent_post_reported_as_failure_within_results(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/posts/bookmark/toggle", json={"post_ids": [999999]}, headers=headers
        )

        assert resp.status_code == 200
        assert resp.get_json()["data"]["results"][0]["success"] is False

    def test_custom_folder_name_is_reflected_in_the_listing_filter(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        user = make_user()
        post = make_post(author)
        headers = {**auth_headers(user), **csrf_headers(client)}

        client.post(
            "/student/posts/bookmark/toggle",
            json={"post_ids": [post.id], "folder_name": "Exam Prep"},
            headers=headers,
        )

        resp = client.get("/student/posts/bookmarked?folder=Exam Prep", headers=auth_headers(user))

        assert resp.get_json()["data"]["total"] == 1


# ============================================================================
# POST /posts/bulk/bookmark
# ============================================================================

class TestBulkBookmark:
    def test_bulk_bookmarks_multiple_posts(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        user = make_user()
        p1 = make_post(author, title="One")
        p2 = make_post(author, title="Two")
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/posts/bulk/bookmark", json={"post_ids": [p1.id, p2.id]}, headers=headers
        )

        assert resp.status_code == 200
        details = resp.get_json()["data"]["bookmark_details"]
        assert all(d["bookmarked"] for d in details)

    def test_too_many_post_ids_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/posts/bulk/bookmark", json={"post_ids": list(range(1, 52))}, headers=headers
        )

        assert resp.status_code == 400

    def test_empty_post_ids_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/posts/bulk/bookmark", json={"post_ids": []}, headers=headers)

        assert resp.status_code == 400

    def test_bulk_bookmark_respects_custom_folder_string(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        user = make_user()
        post = make_post(author)
        headers = {**auth_headers(user), **csrf_headers(client)}

        client.post(
            "/student/posts/bulk/bookmark",
            json={"post_ids": [post.id], "folder": "Exam Prep"},
            headers=headers,
        )

        resp = client.get("/student/posts/bookmarked?folder=Exam Prep", headers=auth_headers(user))

        assert resp.get_json()["data"]["total"] == 1


# ============================================================================
# POST /posts/<id>/bookmark
# ============================================================================

class TestBookmarkSinglePost:
    def test_bookmarking_increments_count(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        user = make_user()
        post = make_post(author)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(f"/student/posts/{post.id}/bookmark", headers=headers)

        assert resp.status_code == 201
        assert resp.get_json()["data"]["bookmark_count"] == 1

    def test_bookmarking_twice_unbookmarks(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        user = make_user()
        post = make_post(author)
        headers = {**auth_headers(user), **csrf_headers(client)}
        client.post(f"/student/posts/{post.id}/bookmark", headers=headers)

        resp = client.post(f"/student/posts/{post.id}/bookmark", headers=headers)

        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["bookmarked"] is False
        assert body["bookmark_count"] == 0

    def test_nonexistent_post_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/posts/999999/bookmark", headers=headers)

        assert resp.status_code == 404


# ============================================================================
# GET /posts/bookmarked
# ============================================================================

class TestGetBookmarkedPosts:
    def test_empty_when_nothing_bookmarked(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/posts/bookmarked", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["bookmarks"] == []

    def test_lists_bookmarked_posts_with_folder_breakdown(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        user = make_user()
        post = make_post(author, title="Save me")
        headers = {**auth_headers(user), **csrf_headers(client)}
        client.post(f"/student/posts/{post.id}/bookmark", headers=headers)

        resp = client.get("/student/posts/bookmarked", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["bookmarks"][0]["post"]["title"] == "Save me"
        assert data["folders"] == [{"name": "Saved", "count": 1}]

    def test_bookmarks_are_scoped_per_user(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        me = make_user()
        other = make_user()
        post = make_post(author)
        other_headers = {**auth_headers(other), **csrf_headers(client)}
        client.post(f"/student/posts/{post.id}/bookmark", headers=other_headers)

        resp = client.get("/student/posts/bookmarked", headers=auth_headers(me))

        assert resp.get_json()["data"]["bookmarks"] == []
