"""
tests/integration/test_threads_crud_routes.py

routes/student/threads/crud.py had zero tests at any level. This batch
covers the core CRUD half: create_thread, create_standalone_thread,
thread_details, close_thread, reopen_thread, update_thread, and
delete_thread. The avatar-upload, stats, and settings endpoints in the
same file are left for a following batch.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# POST /threads/create
# ============================================================================

class TestCreateThread:
    def test_missing_title_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/threads/create", json={}, headers=headers)

        assert resp.status_code == 400

    def test_short_title_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/threads/create", json={"title": "Hi"}, headers=headers
        )

        assert resp.status_code == 400

    def test_nonexistent_post_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/threads/create",
            json={"title": "Study group thread", "post_id": 999999}, headers=headers,
        )

        assert resp.status_code == 404

    def test_post_without_thread_enabled_rejected(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        author = make_user()
        post = make_post(author, thread_enabled=False)
        headers = {**auth_headers(author), **csrf_headers(client)}

        resp = client.post(
            "/student/threads/create",
            json={"title": "Study group thread", "post_id": post.id}, headers=headers,
        )

        assert resp.status_code == 403

    def test_creates_thread_and_makes_creator_a_member(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/threads/create",
            json={"title": "Calc study group", "max_members": 5}, headers=headers,
        )

        assert resp.status_code == 201
        data = resp.get_json()["data"]
        assert data["thread"]["member_count"] == 1

        from models import ThreadMember
        membership = ThreadMember.query.filter_by(
            thread_id=data["thread"]["id"], student_id=user.id
        ).first()
        assert membership is not None
        assert membership.role == "creator"

    def test_max_members_too_low_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/threads/create",
            json={"title": "Too small", "max_members": 1}, headers=headers,
        )

        assert resp.status_code == 400

    def test_valid_member_ids_are_added(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        friend = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/threads/create",
            json={"title": "Group with friends", "member_ids": [friend.id]},
            headers=headers,
        )

        assert resp.status_code == 201
        data = resp.get_json()["data"]
        assert data["thread"]["member_count"] == 2
        assert len(data["added_members"]) == 1


# ============================================================================
# POST /threads/create-standalone
# ============================================================================

class TestCreateStandaloneThread:
    def test_missing_title_rejected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/threads/create-standalone", json={}, headers=headers)

        assert resp.status_code == 400

    def test_creates_standalone_thread(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/threads/create-standalone",
            json={"title": "Standalone study group"}, headers=headers,
        )

        assert resp.status_code == 201
        assert resp.get_json()["data"]["thread"]["is_standalone"] is True

    def test_weekly_thread_creation_limit_enforced(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}
        for i in range(3):
            client.post(
                "/student/threads/create-standalone",
                json={"title": f"Group number {i}"}, headers=headers,
            )

        resp = client.post(
            "/student/threads/create-standalone",
            json={"title": "One too many"}, headers=headers,
        )

        assert resp.status_code == 429


# ============================================================================
# POST /threads/<id>/details
# ============================================================================

class TestThreadDetails:
    def test_nonexistent_thread_returns_error(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/threads/999999/details", json={}, headers=headers)

        assert resp.get_json()["status"] == "error"

    def test_returns_thread_with_members(
        self, client, make_user, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/details", json={}, headers=headers)

        assert resp.status_code == 200
        data = resp.get_json()["data"]["thread"]
        assert data["id"] == thread.id
        assert data["total_users"] == 1
        assert data["creator"]["id"] == creator.id

    def test_lookup_by_post_type(
        self, client, make_user, make_post, make_thread, make_thread_member, auth_headers, csrf_headers
    ):
        creator = make_user()
        post = make_post(creator)
        thread = make_thread(creator, post_id=post.id)
        make_thread_member(thread, creator, role="creator")
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(
            f"/student/threads/{post.id}/details", json={"type": "post"}, headers=headers,
        )

        assert resp.status_code == 200
        assert resp.get_json()["data"]["thread"]["id"] == thread.id


# ============================================================================
# POST /threads/<id>/close and /threads/<id>/reopen
# ============================================================================

class TestCloseReopenThread:
    def test_non_creator_cannot_close(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/close", headers=headers)

        assert resp.status_code == 403

    def test_creator_closes_thread(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/close", headers=headers)

        assert resp.status_code == 200
        from models import Thread
        assert Thread.query.get(thread.id).is_open is False

    def test_closing_already_closed_thread_rejected(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator, is_open=False)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/close", headers=headers)

        assert resp.status_code == 409

    def test_creator_reopens_thread(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator, is_open=False)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(f"/student/threads/{thread.id}/reopen", headers=headers)

        assert resp.status_code == 200
        from models import Thread
        assert Thread.query.get(thread.id).is_open is True


# ============================================================================
# PATCH /threads/<id>
# ============================================================================

class TestUpdateThread:
    def test_non_creator_cannot_update(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.patch(
            f"/student/threads/{thread.id}", json={"title": "New title here"}, headers=headers
        )

        assert resp.status_code == 403

    def test_creator_updates_title(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.patch(
            f"/student/threads/{thread.id}",
            json={"title": "Brand new title"}, headers=headers,
        )

        assert resp.status_code == 200
        assert "title" in resp.get_json()["data"]["changes"]
        from models import Thread
        assert Thread.query.get(thread.id).title == "Brand new title"

    def test_no_changes_returns_success_without_changes(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.patch(f"/student/threads/{thread.id}", json={}, headers=headers)

        assert resp.status_code == 200
        body = resp.get_json()
        assert not (body.get("data") or {}).get("changes")


# ============================================================================
# DELETE /threads/<id>
# ============================================================================

class TestDeleteThread:
    def test_non_creator_cannot_delete(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.delete(f"/student/threads/{thread.id}", headers=headers)

        assert resp.status_code == 403

    def test_creator_deletes_thread(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.delete(f"/student/threads/{thread.id}", headers=headers)

        assert resp.status_code == 200
        from models import Thread
        assert Thread.query.get(thread.id) is None

    def test_nonexistent_thread_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete("/student/threads/999999", headers=headers)

        assert resp.status_code == 404
