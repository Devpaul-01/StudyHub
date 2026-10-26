"""
tests/integration/test_search_users_posts_routes.py

routes/student/search.py had only filters/class-levels, filters/departments,
history, and tags/popular covered (test_search_misc_routes.py). This batch
covers unified_search, search_users, top_contributors, search_posts,
unanswered_posts, and trending_posts. The thread-search, global-search,
suggestions, and discovery endpoints are left for a following batch.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET /search/unified
# ============================================================================

class TestUnifiedSearch:
    def test_missing_query_rejected(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/search/unified", headers=auth_headers(user))

        assert resp.status_code == 400

    def test_short_query_rejected(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/search/unified?q=a", headers=auth_headers(user))

        assert resp.status_code == 400

    def test_invalid_type_rejected(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/search/unified?q=hello&type=bogus", headers=auth_headers(user)
        )

        assert resp.status_code == 400

    def test_users_type_delegates_to_search_users(
        self, client, make_user, auth_headers
    ):
        viewer = make_user()
        match = make_user(username="findme_search", name="Findme Search")

        resp = client.get(
            "/student/search/unified?q=findme&type=users", headers=auth_headers(viewer)
        )

        assert resp.status_code == 200
        assert resp.get_json()["search_type"] == "users"

    def test_all_type_limit_is_capped(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(
            "/student/search/unified?q=hello&type=all&limit=999", headers=auth_headers(user)
        )

        assert resp.status_code == 200
        assert resp.get_json()["search_type"] == "all"


# ============================================================================
# GET /search/users
# ============================================================================

class TestSearchUsers:
    def test_matches_by_username(self, client, make_user, auth_headers):
        viewer = make_user()
        match = make_user(username="uniqueuser123", name="Someone")

        resp = client.get(
            "/student/search/users?q=uniqueuser123", headers=auth_headers(viewer)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        ids = [u["id"] for u in data.get("users", data.get("results", []))]
        assert match.id in ids

    def test_connected_only_filter_excludes_non_connections(
        self, client, make_user, make_connection, auth_headers
    ):
        viewer = make_user()
        friend = make_user(username="myfriend999")
        stranger = make_user(username="stranger999")
        make_connection(viewer, friend, status="accepted")

        resp = client.get(
            "/student/search/users?q=999&connected=true", headers=auth_headers(viewer)
        )

        data = resp.get_json()["data"]
        ids = [u["id"] for u in data.get("users", data.get("results", []))]
        assert friend.id in ids
        assert stranger.id not in ids


# ============================================================================
# GET /search/users/top-contributors
# ============================================================================

class TestTopContributors:
    def test_ranked_by_reputation(self, client, make_user, auth_headers):
        viewer = make_user(status="approved")
        high = make_user(reputation=500, status="approved")
        low = make_user(reputation=10, status="approved")

        resp = client.get(
            "/student/search/users/top-contributors", headers=auth_headers(viewer)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]["top_contributors"]
        ids_in_order = [u["id"] for u in data]
        assert ids_in_order.index(high.id) < ids_in_order.index(low.id)

    def test_department_filter(
        self, client, make_user, make_student_profile, auth_headers
    ):
        viewer = make_user(status="approved")
        cs_user = make_user(reputation=100, status="approved")
        make_student_profile(cs_user, department="CS")
        other_user = make_user(reputation=100, status="approved")
        make_student_profile(other_user, department="Physics")

        resp = client.get(
            "/student/search/users/top-contributors?department=CS",
            headers=auth_headers(viewer),
        )

        ids = [u["id"] for u in resp.get_json()["data"]["top_contributors"]]
        assert cs_user.id in ids
        assert other_user.id not in ids


# ============================================================================
# GET /search/posts
# ============================================================================

class TestSearchPosts:
    def test_matches_by_title(self, client, make_user, make_post, auth_headers):
        user = make_user()
        post = make_post(user, title="Unique Calculus Question")

        resp = client.get(
            "/student/search/posts?q=Calculus+Question", headers=auth_headers(user)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        ids = [p["id"] for p in data.get("posts", data.get("results", []))]
        assert post.id in ids


# ============================================================================
# GET /search/posts/unanswered
# ============================================================================

class TestUnansweredPosts:
    def test_excludes_posts_with_comments(
        self, client, make_user, make_post, make_comment, auth_headers
    ):
        user = make_user()
        no_comments = make_post(
            user, post_type="question", is_solved=False, comments_count=0
        )
        has_comments = make_post(
            user, post_type="question", is_solved=False, comments_count=1
        )

        resp = client.get("/student/search/posts/unanswered", headers=auth_headers(user))

        assert resp.status_code == 200
        ids = [p["id"] for p in resp.get_json()["data"]["unanswered_posts"]]
        assert no_comments.id in ids
        assert has_comments.id not in ids

    def test_excludes_solved_posts(self, client, make_user, make_post, auth_headers):
        user = make_user()
        solved = make_post(
            user, post_type="question", is_solved=True, comments_count=0
        )

        resp = client.get("/student/search/posts/unanswered", headers=auth_headers(user))

        ids = [p["id"] for p in resp.get_json()["data"]["unanswered_posts"]]
        assert solved.id not in ids

    def test_only_question_like_types_included(
        self, client, make_user, make_post, auth_headers
    ):
        user = make_user()
        resource_post = make_post(
            user, post_type="resource", is_solved=False, comments_count=0
        )

        resp = client.get("/student/search/posts/unanswered", headers=auth_headers(user))

        ids = [p["id"] for p in resp.get_json()["data"]["unanswered_posts"]]
        assert resource_post.id not in ids

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG FOUND BY THIS TEST: unanswered_posts()'s tag filter "
            "computes `tags_list = [t.strip().lower() for t in "
            "tags.split(\",\")]` from the raw `?tags=` query string but "
            "then never uses it -- the actual filter is "
            "`query.filter(Post.tags.in_(tags))` with the RAW, unsplit "
            "query string `tags` (e.g. \"calculus\"), not `tags_list`. "
            "Post.tags is a JSON list column, and SQLAlchemy's .in_() "
            "iterates its argument to build the IN(...) list -- iterating "
            "a plain string yields its individual CHARACTERS "
            "('c','a','l','c','u','l','u','s'), so the generated query is "
            "effectively `Post.tags IN ('c','a','l',...)`, comparing each "
            "post's whole tags array against single letters. This can "
            "never match a real tags array, so passing ?tags=<anything> "
            "to this endpoint always filters out every post, even ones "
            "that are an exact tag match. strict=True so this flips to "
            "XPASS once the filter uses `Post.tags.op('@>')([...])`-style "
            "containment (or any check against `tags_list`, not the raw "
            "string) instead of `.in_(tags)`."
        ),
    )
    def test_tags_filter_matches_a_post_with_that_tag(
        self, client, make_user, make_post, auth_headers
    ):
        user = make_user()
        tagged = make_post(
            user, post_type="question", is_solved=False, comments_count=0,
            tags=["calculus"],
        )

        resp = client.get(
            "/student/search/posts/unanswered?tags=calculus", headers=auth_headers(user)
        )

        ids = [p["id"] for p in resp.get_json()["data"]["unanswered_posts"]]
        assert tagged.id in ids


# ============================================================================
# GET /search/posts/trending
# ============================================================================

class TestTrendingPosts:
    def test_ranked_by_engagement_score(self, client, make_user, make_post, auth_headers):
        user = make_user()
        hot = make_post(
            user, positive_reactions_count=50, comments_count=20, views_count=1000
        )
        cold = make_post(
            user, positive_reactions_count=0, comments_count=0, views_count=1
        )

        resp = client.get("/student/search/posts/trending", headers=auth_headers(user))

        assert resp.status_code == 200
        ids_in_order = [p["id"] for p in resp.get_json()["data"]["trending_posts"]]
        assert ids_in_order.index(hot.id) < ids_in_order.index(cold.id)

    def test_defaults_to_viewer_department_when_set(
        self, client, make_user, make_student_profile, make_post, auth_headers
    ):
        user = make_user()
        make_student_profile(user, department="CS")
        cs_post = make_post(user, department="CS")
        other_user = make_user()
        other_post = make_post(other_user, department="Physics")

        resp = client.get("/student/search/posts/trending", headers=auth_headers(user))

        ids = [p["id"] for p in resp.get_json()["data"]["trending_posts"]]
        assert cs_post.id in ids
        assert other_post.id not in ids
