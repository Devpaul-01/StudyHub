"""Integration tests for GET /student/profile/<username> (viewing a profile)."""
import datetime

import pytest

URL = "/student/profile/{}"


@pytest.fixture
def owner(db_session, make_user, make_student_profile):
    user = make_user(username="owner1")
    make_student_profile(user)
    return user


@pytest.fixture
def viewer(db_session, make_user, make_student_profile):
    user = make_user(username="viewer1")
    make_student_profile(user)
    return user


def _set_privacy(db_session, user, **settings):
    user.privacy_settings = settings
    db_session.commit()


def _get(client, auth_headers, viewer, username):
    return client.get(URL.format(username), headers=auth_headers(viewer))


class TestProfileVisibility:
    def test_public_profile_returns_full_payload(self, client, auth_headers, owner, viewer):
        resp = _get(client, auth_headers, viewer, owner.username)

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["type"] == "public"
        assert data["is_own_profile"] is False
        assert "stats" in data

    def test_unknown_user_is_not_found(self, client, auth_headers, viewer):
        resp = _get(client, auth_headers, viewer, "nobody-here")
        assert resp.status_code == 404

    def test_private_profile_hides_details_from_strangers(
        self, client, db_session, auth_headers, owner, viewer
    ):
        _set_privacy(db_session, owner, set_profile_private=True)

        resp = _get(client, auth_headers, viewer, owner.username)

        data = resp.get_json()["data"]
        assert data["type"] == "private"
        assert "stats" not in data
        assert "bio" not in data["user"]

    def test_private_profile_still_lists_pinned_posts(
        self, client, db_session, auth_headers, make_post, owner, viewer
    ):
        post = make_post(owner, title="Pinned one")
        post.is_pinned = True
        db_session.commit()
        _set_privacy(db_session, owner, set_profile_private=True)

        data = _get(client, auth_headers, viewer, owner.username).get_json()["data"]

        assert [p["title"] for p in data["user"]["pinned_posts"]] == ["Pinned one"]

    def test_private_profile_is_full_for_accepted_connections(
        self, client, db_session, auth_headers, make_connection, owner, viewer
    ):
        _set_privacy(db_session, owner, set_profile_private=True)
        make_connection(viewer, owner, status="accepted")

        data = _get(client, auth_headers, viewer, owner.username).get_json()["data"]

        assert data["type"] == "public"
        assert data["connection_status"] == "connected"

    def test_private_profile_stays_private_for_pending_requests(
        self, client, db_session, auth_headers, make_connection, owner, viewer
    ):
        _set_privacy(db_session, owner, set_profile_private=True)
        make_connection(viewer, owner, status="pending")

        data = _get(client, auth_headers, viewer, owner.username).get_json()["data"]

        assert data["type"] == "private"
        assert data["connection_status"] == "pending_sent"

    def test_owner_sees_their_own_private_profile_in_full(
        self, client, db_session, auth_headers, owner
    ):
        _set_privacy(db_session, owner, set_profile_private=True)

        data = _get(client, auth_headers, owner, owner.username).get_json()["data"]

        assert data["type"] == "public"
        assert data["is_own_profile"] is True
