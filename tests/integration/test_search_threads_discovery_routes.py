"""
tests/integration/test_search_threads_discovery_routes.py

The remaining untested endpoints in routes/student/search.py:
search_threads, open_threads, global_search, search_suggestions, and
personalized_discovery. The user/post search endpoints in this same
file were covered in a previous batch.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET /search/threads
# ============================================================================

class TestSearchThreads:
    def test_matches_by_title(self, client, make_user, make_thread, auth_headers):
        user = make_user()
        thread = make_thread(user, title="Unique Calculus Study Group")

        resp = client.get(
            "/student/search/threads?q=Calculus+Study", headers=auth_headers(user)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        ids = [t["id"] for t in data.get("threads", data.get("results", []))]
        assert thread.id in ids


# ============================================================================
# GET /search/threads/open
# ============================================================================

class TestOpenThreads:
    def test_lists_threads_with_space(self, client, make_user, make_thread, auth_headers):
        user = make_user()
        open_thread = make_thread(user, is_open=True, member_count=2, max_members=10)
        full_thread = make_thread(user, is_open=True, member_count=10, max_members=10)
        closed_thread = make_thread(user, is_open=False, member_count=2, max_members=10)

        resp = client.get("/student/search/threads/open", headers=auth_headers(user))

        assert resp.status_code == 200
        ids = [t["id"] for t in resp.get_json()["data"]["open_threads"]]
        assert open_thread.id in ids
        assert full_thread.id not in ids
        assert closed_thread.id not in ids

    def test_marks_is_member_correctly(
        self, client, make_user, make_thread, make_thread_member, auth_headers
    ):
        user = make_user()
        thread = make_thread(user, is_open=True, member_count=1, max_members=10)
        make_thread_member(thread, user, role="creator")

        resp = client.get("/student/search/threads/open", headers=auth_headers(user))

        entry = next(
            t for t in resp.get_json()["data"]["open_threads"] if t["id"] == thread.id
        )
        assert entry["is_member"] is True

    def test_department_filter(
        self, client, make_user, make_thread, auth_headers
    ):
        user = make_user()
        cs_thread = make_thread(
            user, is_open=True, member_count=1, max_members=10, department="CS"
        )
        physics_thread = make_thread(
            user, is_open=True, member_count=1, max_members=10, department="Physics"
        )

        resp = client.get(
            "/student/search/threads/open?department=CS", headers=auth_headers(user)
        )

        ids = [t["id"] for t in resp.get_json()["data"]["open_threads"]]
        assert cs_thread.id in ids
        assert physics_thread.id not in ids


# ============================================================================
# GET /search/global
# ============================================================================

class TestGlobalSearch:
    def test_missing_query_rejected(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/search/global", headers=auth_headers(user))

        assert resp.status_code == 400

    def test_short_query_rejected(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/search/global?q=a", headers=auth_headers(user))

        assert resp.status_code == 400

    def test_returns_counts_and_results(
        self, client, make_user, make_post, auth_headers
    ):
        user = make_user()
        make_post(user, title="Unique Searchable Post Title")

        resp = client.get(
            "/student/search/global?q=Searchable", headers=auth_headers(user)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "results" in data
        assert "counts" in data
        assert data["query"] == "Searchable"


# ============================================================================
# GET /search/suggestions
# ============================================================================

class TestSearchSuggestions:
    def test_short_query_returns_empty(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/search/suggestions?q=a", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["suggestions"] == []

    def test_user_suggestions_prefix_match(self, client, make_user, auth_headers):
        user = make_user()
        match = make_user(username="suggestme123")

        resp = client.get(
            "/student/search/suggestions?q=suggestme&type=users",
            headers=auth_headers(user),
        )

        suggestions = resp.get_json()["data"]["suggestions"]
        usernames = [s["text"] for s in suggestions]
        assert "suggestme123" in usernames

    def test_type_filter_excludes_other_types(
        self, client, make_user, make_post, auth_headers
    ):
        user = make_user()
        make_post(user, title="Suggestable Post Title")
        make_user(username="suggestableuser")

        resp = client.get(
            "/student/search/suggestions?q=suggestable&type=posts",
            headers=auth_headers(user),
        )

        suggestions = resp.get_json()["data"]["suggestions"]
        types = {s["type"] for s in suggestions}
        assert types <= {"post"}


# ============================================================================
# GET /search/discovery/for-you
# ============================================================================

class TestPersonalizedDiscovery:
    def test_no_profile_returns_empty_recommendations(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/search/discovery/for-you", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["based_on"]["department"] is None

    def test_recommends_same_department_posts(
        self, client, make_user, make_student_profile, make_post, auth_headers
    ):
        import datetime

        user = make_user()
        make_student_profile(user, department="CS")
        recent_post = make_post(
            user, department="CS", posted_at=datetime.datetime.utcnow()
        )

        resp = client.get("/student/search/discovery/for-you", headers=auth_headers(user))

        ids = [p["id"] for p in resp.get_json()["data"]["recommended_posts"]]
        assert recent_post.id in ids

    def test_recommends_connections_excluded_users(
        self, client, make_user, make_student_profile, make_connection, auth_headers
    ):
        user = make_user()
        make_student_profile(user, department="CS")
        friend = make_user(reputation=10)
        make_student_profile(friend, department="CS")
        make_connection(user, friend, status="accepted")
        stranger = make_user(reputation=10)
        make_student_profile(stranger, department="CS")

        resp = client.get("/student/search/discovery/for-you", headers=auth_headers(user))

        ids = [u["id"] for u in resp.get_json()["data"]["recommended_users"]]
        assert friend.id not in ids
        assert stranger.id in ids
