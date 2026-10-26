"""
tests/integration/test_threads_crud_settings_routes.py

The remaining untested endpoints in routes/student/threads/crud.py:
upload_thread_avatar, get_thread_stats, get_thread_settings, and
update_thread_settings. The core CRUD endpoints in this same file were
covered in a previous batch.

upload_thread_avatar's happy path makes a real network call to
Cloudinary (cloudinary.uploader.upload) once it passes validation, so
only the guard clauses before that call (auth, missing file, bad mime
type, oversized file) are exercised here -- consistent with this
suite's established rule of never calling real external services.
"""

import io

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# POST /threads/<id>/avatar
# ============================================================================

class TestUploadThreadAvatar:
    def test_nonexistent_thread_returns_404(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/threads/999999/avatar",
            data={"file": (io.BytesIO(b"fake"), "avatar.png")},
            headers=headers, content_type="multipart/form-data",
        )

        assert resp.status_code == 404

    def test_non_creator_forbidden(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.post(
            f"/student/threads/{thread.id}/avatar",
            data={"file": (io.BytesIO(b"fake"), "avatar.png")},
            headers=headers, content_type="multipart/form-data",
        )

        assert resp.status_code == 403

    def test_no_file_rejected(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(
            f"/student/threads/{thread.id}/avatar",
            data={}, headers=headers, content_type="multipart/form-data",
        )

        assert resp.status_code == 400

    def test_non_image_mime_rejected(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.post(
            f"/student/threads/{thread.id}/avatar",
            data={"file": (io.BytesIO(b"not an image"), "notes.txt")},
            headers=headers, content_type="multipart/form-data",
        )

        assert resp.status_code == 400

    def test_oversized_file_rejected(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        headers = {**auth_headers(creator), **csrf_headers(client)}
        oversized = io.BytesIO(b"x" * (5 * 1024 * 1024 + 1))

        resp = client.post(
            f"/student/threads/{thread.id}/avatar",
            data={"file": (oversized, "avatar.png")},
            headers=headers, content_type="multipart/form-data",
        )

        assert resp.status_code == 400


# ============================================================================
# GET /threads/<id>/stats
# ============================================================================

class TestGetThreadStats:
    def test_non_member_forbidden(self, client, make_user, make_thread, auth_headers):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()

        resp = client.get(
            f"/student/threads/{thread.id}/stats", headers=auth_headers(outsider)
        )

        assert resp.status_code == 403

    def test_member_sees_stats(
        self, client, make_user, make_thread, make_thread_member, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        make_thread_member(thread, creator, role="creator")

        resp = client.get(
            f"/student/threads/{thread.id}/stats", headers=auth_headers(creator)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["thread"]["id"] == thread.id
        assert data["stats"]["total_members"] == thread.member_count
        assert len(data["members"]) == 1
        assert data["most_active"]["user"]["id"] == creator.id


# ============================================================================
# GET /threads/<id>/settings and PATCH /threads/<id>/settings
# ============================================================================

class TestThreadSettings:
    def test_non_creator_cannot_view_settings(
        self, client, make_user, make_thread, auth_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()

        resp = client.get(
            f"/student/threads/{thread.id}/settings", headers=auth_headers(outsider)
        )

        assert resp.status_code == 403

    def test_creator_views_settings(self, client, make_user, make_thread, auth_headers):
        creator = make_user()
        thread = make_thread(creator, max_members=15, requires_approval=True)

        resp = client.get(
            f"/student/threads/{thread.id}/settings", headers=auth_headers(creator)
        )

        assert resp.status_code == 200
        settings = resp.get_json()["data"]["settings"]
        assert settings["max_members"] == 15
        assert settings["requires_approval"] is True

    def test_non_creator_cannot_update_settings(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator)
        outsider = make_user()
        headers = {**auth_headers(outsider), **csrf_headers(client)}

        resp = client.patch(
            f"/student/threads/{thread.id}/settings",
            json={"requires_approval": False}, headers=headers,
        )

        assert resp.status_code == 403

    def test_creator_updates_requires_approval(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator, requires_approval=True)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.patch(
            f"/student/threads/{thread.id}/settings",
            json={"requires_approval": False}, headers=headers,
        )

        assert resp.status_code == 200
        assert "requires_approval" in resp.get_json()["data"]["changes"]
        from models import Thread
        assert Thread.query.get(thread.id).requires_approval is False

    def test_invalid_max_members_rejected(
        self, client, make_user, make_thread, auth_headers, csrf_headers
    ):
        creator = make_user()
        thread = make_thread(creator, member_count=5)
        headers = {**auth_headers(creator), **csrf_headers(client)}

        resp = client.patch(
            f"/student/threads/{thread.id}/settings",
            json={"max_members": 2}, headers=headers,
        )

        assert resp.status_code == 400
