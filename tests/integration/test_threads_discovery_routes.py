"""
tests/integration/test_threads_discovery_routes.py

routes/student/threads/discovery.py had zero tests at any level. This
batch covers the listing/retrieval half: get_department_stats,
get_popular_threads_by_members, get_my_threads, open_thread, and
get_thread. The heavier recommendation-scoring endpoints
(get_recommended_threads, get_help_suggestions) are left for a
following batch.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET /threads/departments
# ============================================================================

class TestGetDepartmentStats:
    def test_no_threads_returns_empty(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/threads/departments", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["departments"] == []
        assert data["total_departments"] == 0

    def test_counts_open_threads_by_department(
        self, client, make_user, make_thread, auth_headers
    ):
        creator = make_user()
        make_thread(creator, department="CS", is_open=True)
        make_thread(creator, department="CS", is_open=True)
        make_thread(creator, department="Physics", is_open=True)

        resp = client.get("/student/threads/departments", headers=auth_headers(creator))

        data = resp.get_json()["data"]
        dept_map = {d["department"]: d["total_threads"] for d in data["departments"]}
        assert dept_map.get("CS") == 2
        assert dept_map.get("Physics") == 1

    def test_closed_threads_excluded(self, client, make_user, make_thread, auth_headers):
        creator = make_user()
        make_thread(creator, department="CS", is_open=False)

        resp = client.get("/student/threads/departments", headers=auth_headers(creator))

        data = resp.get_json()["data"]
        dept_map = {d["department"]: d["total_threads"] for d in data["departments"]}
        assert "CS" not in dept_map


# ============================================================================
# GET /threads/popular
# ============================================================================

class TestGetPopularThreads:
    def test_empty_when_no_qualifying_threads(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/threads/popular", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["threads"] == []

    def test_other_department_thread_with_enough_members_is_listed(
        self, client, make_user, make_student_profile, make_thread, auth_headers
    ):
        viewer = make_user()
        make_student_profile(viewer, department="CS")
        creator = make_user()
        make_thread(
            creator, department="Physics", member_count=5, max_members=10, is_open=True
        )

        resp = client.get(
            "/student/threads/popular?min_members=3", headers=auth_headers(viewer)
        )

        data = resp.get_json()["data"]
        assert data["total_found"] == 1
        assert data["threads"][0]["department"] == "Physics"

    def test_already_member_thread_excluded(
        self, client, make_user, make_thread, make_thread_member, auth_headers
    ):
        viewer = make_user()
        creator = make_user()
        thread = make_thread(creator, member_count=5, max_members=10, is_open=True)
        make_thread_member(thread, viewer)

        resp = client.get(
            "/student/threads/popular?min_members=3", headers=auth_headers(viewer)
        )

        ids = [t["id"] for t in resp.get_json()["data"]["threads"]]
        assert thread.id not in ids

    def test_full_thread_excluded(self, client, make_user, make_thread, auth_headers):
        viewer = make_user()
        creator = make_user()
        make_thread(creator, member_count=10, max_members=10, is_open=True)

        resp = client.get(
            "/student/threads/popular?min_members=3", headers=auth_headers(viewer)
        )

        assert resp.get_json()["data"]["threads"] == []


# ============================================================================
# GET /threads/my-threads
# ============================================================================

class TestGetMyThreads:
    def test_empty_when_no_memberships(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/threads/my-threads", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.get_json()["data"]["threads"] == []

    def test_lists_thread_with_last_message_preview(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_message(thread, creator, text_content="Hey everyone!")

        resp = client.get("/student/threads/my-threads", headers=auth_headers(creator))

        data = resp.get_json()["data"]
        assert data["total"] == 1
        assert data["threads"][0]["last_message"]["text"] == "Hey everyone!"
        assert data["threads"][0]["is_creator"] is True

    def test_unread_count_excludes_own_messages(
        self, client, make_user, make_thread, make_thread_member, make_thread_message, auth_headers
    ):
        creator = make_user()
        other = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        make_thread_member(thread, other)
        make_thread_message(thread, creator, text_content="From creator")

        resp = client.get("/student/threads/my-threads", headers=auth_headers(creator))

        data = resp.get_json()["data"]["threads"][0]
        assert data["unread_count"] == 0


# ============================================================================
# GET /threads/open
# ============================================================================

class TestOpenThreads:
    def test_lists_open_threads(self, client, make_user, make_thread, auth_headers):
        viewer = make_user()
        creator = make_user()
        make_thread(creator, is_open=True)
        make_thread(creator, is_open=False)

        resp = client.get("/student/threads/open", headers=auth_headers(viewer))

        assert resp.status_code == 200
        assert len(resp.get_json()["data"]) == 1

    def test_full_thread_flagged_is_full(self, client, make_user, make_thread, auth_headers):
        viewer = make_user()
        creator = make_user()
        make_thread(creator, is_open=True, member_count=10, max_members=10)

        resp = client.get("/student/threads/open", headers=auth_headers(viewer))

        data = resp.get_json()["data"]
        assert data[0]["is_full"] is True


# ============================================================================
# GET /threads/<id>
# ============================================================================

class TestGetThread:
    def test_nonexistent_thread_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/threads/999999", headers=auth_headers(user))

        assert resp.status_code == 404

    def test_non_member_sees_basic_info_without_member_list(
        self, client, make_user, make_thread, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()

        resp = client.get(f"/student/threads/{thread.id}", headers=auth_headers(outsider))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["user_status"]["is_member"] is False
        assert "members" not in data["thread"]

    def test_member_sees_member_list(
        self, client, make_user, make_thread, make_thread_member, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")

        resp = client.get(f"/student/threads/{thread.id}", headers=auth_headers(creator))

        data = resp.get_json()["data"]
        assert data["user_status"]["is_member"] is True
        assert data["user_status"]["is_creator"] is True
        assert len(data["thread"]["members"]) == 1

    def test_creator_sees_pending_requests(
        self, client, db_session, make_user, make_thread, make_thread_member, auth_headers
    ):
        from models import ThreadJoinRequest
        import datetime

        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        requester = make_user()
        db_session.add(
            ThreadJoinRequest(
                thread_id=thread.id, requester_id=requester.id, status="pending",
                requested_at=datetime.datetime.utcnow(),
            )
        )
        db_session.flush()

        resp = client.get(f"/student/threads/{thread.id}", headers=auth_headers(creator))

        data = resp.get_json()["data"]["thread"]
        assert len(data["pending_requests"]) == 1
        assert data["pending_requests"][0]["user"]["id"] == requester.id
