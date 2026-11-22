"""Integration tests for avatar upload / removal."""
import io

import pytest
from PIL import Image

UPLOAD = "/student/profile/avatar/upload"
REMOVE = "/student/profile/avatar"


def _png(width, height):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (5, 90, 200)).save(buf, "PNG")
    buf.seek(0)
    return buf


@pytest.fixture
def storage(monkeypatch):
    """Replace the Cloudinary client with a recorder."""
    from services.storage import cloudinary_storage

    class Recorder:
        uploads, deletes = [], []
        upload_result = {"success": True, "url": "https://res.cloudinary.test/avatar_1.png"}
        delete_result = {"success": True, "error": None}

    def upload(file, folder, filename, resource_type="auto", timeout=None):
        data = file.read()
        Recorder.uploads.append({"folder": folder, "filename": filename, "size": Image.open(io.BytesIO(data)).size,
                                 "timeout": timeout, "resource_type": resource_type})
        return Recorder.upload_result

    def delete(public_id, resource_type="image"):
        Recorder.deletes.append(public_id)
        return Recorder.delete_result

    Recorder.uploads, Recorder.deletes = [], []
    Recorder.upload_result = {"success": True, "url": "https://res.cloudinary.test/avatar_1.png"}
    Recorder.delete_result = {"success": True, "error": None}
    monkeypatch.setattr(cloudinary_storage, "upload_file", upload)
    monkeypatch.setattr(cloudinary_storage, "delete_file", delete)
    return Recorder


def _upload(client, auth_headers, csrf_headers, user, image, name="me.png"):
    return client.post(UPLOAD, data={"avatar": (image, name)},
                       headers={**auth_headers(user), **csrf_headers(client)},
                       content_type="multipart/form-data")


class TestUploadAvatar:
    def test_saves_the_url_and_sends_a_downscaled_image(
        self, client, db_session, make_user, auth_headers, csrf_headers, storage
    ):
        user = make_user(avatar=None)

        resp = _upload(client, auth_headers, csrf_headers, user, _png(2000, 1000))

        assert resp.status_code == 200
        db_session.refresh(user)
        assert user.avatar == "https://res.cloudinary.test/avatar_1.png"
        sent = storage.uploads[0]
        assert sent["size"] == (512, 256)
        assert sent["folder"] == f"avatars/{user.id}"
        assert sent["filename"] == f"avatar_{user.id}"
        assert sent["timeout"]

    def test_image_with_too_many_pixels_is_rejected_before_storage(
        self, client, db_session, make_user, auth_headers, csrf_headers, storage
    ):
        user = make_user(avatar=None)

        resp = _upload(client, auth_headers, csrf_headers, user, _png(6000, 5000))  # 30 MP

        assert resp.status_code == 400
        assert "too large" in resp.get_json()["message"]
        assert storage.uploads == []

    def test_storage_failure_hides_provider_details_and_keeps_old_avatar(
        self, client, db_session, make_user, auth_headers, csrf_headers, storage
    ):
        """Regression: the provider's raw error text was returned to the client."""
        user = make_user(avatar="https://old.example/a.png")
        storage.upload_result = {"success": False, "url": None, "error": "Invalid api_key abc123"}

        resp = _upload(client, auth_headers, csrf_headers, user, _png(10, 10))

        assert resp.status_code == 502
        assert "abc123" not in resp.get_data(as_text=True)
        db_session.refresh(user)
        assert user.avatar == "https://old.example/a.png"

    def test_rejects_a_disallowed_extension(self, client, make_user, auth_headers, csrf_headers, storage):
        user = make_user()

        resp = _upload(client, auth_headers, csrf_headers, user, _png(10, 10), name="me.svg")

        assert resp.status_code == 400
        assert storage.uploads == []


class TestRemoveAvatar:
    def _delete(self, client, auth_headers, csrf_headers, user):
        return client.delete(REMOVE, headers={**auth_headers(user), **csrf_headers(client)})

    def test_clears_the_column_and_deletes_the_stored_image(
        self, client, db_session, make_user, auth_headers, csrf_headers, storage
    ):
        """Regression: the image used to stay on Cloudinary after removal."""
        user = make_user(avatar="https://res.cloudinary.test/avatar_1.png")

        resp = self._delete(client, auth_headers, csrf_headers, user)

        assert resp.status_code == 200
        db_session.refresh(user)
        assert user.avatar is None
        assert storage.deletes == [f"avatars/{user.id}/avatar_{user.id}"]

    def test_storage_delete_failure_does_not_fail_the_request(
        self, client, db_session, make_user, auth_headers, csrf_headers, storage
    ):
        user = make_user(avatar="https://res.cloudinary.test/avatar_1.png")
        storage.delete_result = {"success": False, "error": "boom"}

        resp = self._delete(client, auth_headers, csrf_headers, user)

        assert resp.status_code == 200
        db_session.refresh(user)
        assert user.avatar is None

    def test_nothing_to_delete_when_there_was_no_avatar(
        self, client, make_user, auth_headers, csrf_headers, storage
    ):
        user = make_user(avatar=None)

        assert self._delete(client, auth_headers, csrf_headers, user).status_code == 200
        assert storage.deletes == []
