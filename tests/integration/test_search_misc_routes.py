"""
tests/integration/test_search_misc_routes.py

routes/student/search.py has 17 endpoints. Most (unified/users/posts/
threads/global/suggestions) are thin wrappers that delegate straight to
services/search_service.py, whose query-building and ranking logic is
already covered by tests/unit/services/test_search_service.py. This
batch instead covers the standalone logic that lives directly in the
route file and had zero tests: the search-history mini feature
(GET/POST/DELETE /search/history), popular tags, and the department/
class-level filter-dropdown endpoints.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET/POST/DELETE /search/history
# ============================================================================

class TestSearchHistory:
    def test_history_starts_empty(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/search/history", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["recent_searches"] == []

    def test_saving_a_query_adds_it_to_history(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/search/history", json={"query": "linear algebra"}, headers=headers)
        assert resp.status_code == 200

        get_resp = client.get("/student/search/history", headers=auth_headers(user))
        assert get_resp.get_json()["data"]["recent_searches"] == ["linear algebra"]

    def test_saving_duplicate_query_moves_it_to_front_without_duplicating(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        client.post("/student/search/history", json={"query": "alpha"}, headers=headers)
        client.post("/student/search/history", json={"query": "beta"}, headers=headers)
        client.post("/student/search/history", json={"query": "alpha"}, headers=headers)

        get_resp = client.get("/student/search/history", headers=auth_headers(user))
        recents = get_resp.get_json()["data"]["recent_searches"]
        assert recents.count("alpha") == 1
        assert recents[0] == "alpha"

    def test_empty_query_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/search/history", json={"query": ""}, headers=headers)

        assert resp.status_code == 400

    def test_single_character_query_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/search/history", json={"query": "a"}, headers=headers)

        assert resp.status_code == 400

    def test_clear_history_empties_it(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}
        client.post("/student/search/history", json={"query": "something"}, headers=headers)

        resp = client.delete("/student/search/history", headers=headers)
        assert resp.status_code == 200

        get_resp = client.get("/student/search/history", headers=auth_headers(user))
        assert get_resp.get_json()["data"]["recent_searches"] == []

    def test_history_is_scoped_per_user(self, client, make_user, auth_headers, csrf_headers):
        me = make_user()
        other = make_user()
        other_headers = {**auth_headers(other), **csrf_headers(client)}
        client.post("/student/search/history", json={"query": "other users query"}, headers=other_headers)

        resp = client.get("/student/search/history", headers=auth_headers(me))

        assert resp.get_json()["data"]["recent_searches"] == []

    def test_recent_searches_actually_returns_the_most_recent_queries(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        for i in range(15):
            client.post("/student/search/history", json={"query": f"query {i}"}, headers=headers)

        resp = client.get("/student/search/history", headers=auth_headers(user))
        recents = resp.get_json()["data"]["recent_searches"]

        # The most recently saved query must be present in "recent searches".
        assert "query 14" in recents


# ============================================================================
# GET /search/tags/popular
# ============================================================================

class TestPopularTags:
    def test_returns_tags_sorted_by_frequency(self, client, make_user, make_post, auth_headers):
        user = make_user()
        make_post(user, title="Post one", tags=["python", "calculus"])
        make_post(user, title="Post two", tags=["python"])
        make_post(user, title="Post three", tags=["chemistry"])

        resp = client.get("/student/search/tags/popular", headers=auth_headers(user))

        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["popular_tags"][0] == "python"
        detailed = {d["tag"]: d["count"] for d in body["detailed"]}
        assert detailed["python"] == 2
        assert detailed["chemistry"] == 1

    def test_respects_limit_param(self, client, make_user, make_post, auth_headers):
        user = make_user()
        make_post(user, tags=["a", "b", "c"])

        resp = client.get("/student/search/tags/popular?limit=1", headers=auth_headers(user))

        assert len(resp.get_json()["data"]["popular_tags"]) == 1

    def test_empty_when_no_tagged_posts(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/search/tags/popular", headers=auth_headers(user))

        assert resp.get_json()["data"]["popular_tags"] == []


# ============================================================================
# GET /search/filters/departments and /search/filters/class-levels
# ============================================================================

class TestFilterDropdowns:
    def test_departments_grouped_with_counts(
        self, client, make_user, make_student_profile, auth_headers
    ):
        u1 = make_user()
        u2 = make_user()
        u3 = make_user()
        make_student_profile(u1, department="Computer Science")
        make_student_profile(u2, department="Computer Science")
        make_student_profile(u3, department="Physics")

        resp = client.get("/student/search/filters/departments", headers=auth_headers(u1))

        assert resp.status_code == 200
        depts = {d["name"]: d["student_count"] for d in resp.get_json()["data"]["departments"]}
        assert depts["Computer Science"] == 2
        assert depts["Physics"] == 1

    def test_class_levels_grouped_with_counts(
        self, client, make_user, make_student_profile, auth_headers
    ):
        u1 = make_user()
        u2 = make_user()
        make_student_profile(u1, class_name="SS2")
        make_student_profile(u2, class_name="SS3")

        resp = client.get("/student/search/filters/class-levels", headers=auth_headers(u1))

        assert resp.status_code == 200
        levels = {d["name"] for d in resp.get_json()["data"]["class_levels"]}
        assert levels == {"SS2", "SS3"}
