"""
tests/integration/test_file_upload_validation.py

Covers INTEGRATION_TEST_IMPLEMENTATION_PLAN.md §3.9 — the ROUTE boundary
around services/upload_validation_service.py's already-pure,
already-unit-testable image validation: does a spoofed file get rejected
end-to-end with a 400 and, critically, WITHOUT ever reaching Cloudinary
or creating a DB row? The validation logic itself (Pillow verify/load/
re-encode) is not re-tested here per that plan's own §3.9 reasoning.
"""

import io

import pytest

pytestmark = pytest.mark.integration


class TestAvatarUploadRejectsSpoofedImage:
    def test_non_image_bytes_with_jpg_extension_rejected_before_cloudinary(
        self, client, make_user, auth_headers, csrf_headers, monkeypatch
    ):
        from services.storage import cloudinary_storage

        cloudinary_called = {"n": 0}

        def _tracking_upload(*a, **kw):
            cloudinary_called["n"] += 1
            return {"success": True, "url": "https://fake/should-not-be-called.jpg"}

        monkeypatch.setattr(cloudinary_storage, "upload_file", _tracking_upload)

        user = make_user(status="approved", avatar=None)
        headers = {**auth_headers(user), **csrf_headers(client)}

        # Plain text bytes, not a real JPEG, disguised with a .jpg filename
        # and multipart field — this is exactly the extension-spoofing
        # scenario validate_and_normalize_image() exists to reject.
        fake_file = (io.BytesIO(b"this is definitely not a jpeg"), "fake.jpg")

        resp = client.post(
            "/student/profile/avatar/upload",
            data={"avatar": fake_file},
            headers=headers,
            content_type="multipart/form-data",
        )

        assert resp.status_code == 400, resp.get_json()
        assert cloudinary_called["n"] == 0

        from extensions import db
        db.session.refresh(user)
        assert user.avatar is None

