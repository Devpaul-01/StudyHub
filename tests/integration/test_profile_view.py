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


class TestBlockedUsers:
    @pytest.mark.parametrize("blocker_is_owner", [True, False])
    def test_blocked_pair_cannot_view_each_other(
        self, client, db_session, auth_headers, make_connection, owner, viewer, blocker_is_owner
    ):
        blocker, blocked = (owner, viewer) if blocker_is_owner else (viewer, owner)
        conn = make_connection(blocker, blocked, status="blocked")
        conn.blocked_by_id = blocker.id
        db_session.commit()

        resp = _get(client, auth_headers, viewer, owner.username)

        assert resp.status_code == 404
        assert resp.get_json()["message"] == "User not found"

    def test_unblocked_third_party_is_unaffected(
        self, client, db_session, auth_headers, make_user, make_student_profile,
        make_connection, owner, viewer
    ):
        bystander = make_user(username="bystander1")
        make_student_profile(bystander)
        make_connection(owner, viewer, status="blocked")

        resp = _get(client, auth_headers, bystander, owner.username)

        assert resp.status_code == 200


class TestActivityVisibility:
    def _owner_active(self, db_session, owner, **privacy):
        owner.last_active = datetime.datetime.utcnow() - datetime.timedelta(hours=3)
        owner.login_streak = 7
        owner.privacy_settings = privacy
        db_session.commit()

    def test_activity_is_shown_by_default(self, client, db_session, auth_headers, owner, viewer):
        self._owner_active(db_session, owner)

        user = _get(client, auth_headers, viewer, owner.username).get_json()["data"]["user"]

        assert user["last_active"] is not None
        assert user["login_streak"] == 7
        assert user["active_status"] is True

    def test_activity_is_omitted_when_owner_opted_out(self, client, db_session, auth_headers, owner, viewer):
        self._owner_active(db_session, owner, show_active_status=False)

        user = _get(client, auth_headers, viewer, owner.username).get_json()["data"]["user"]

        assert user["last_active"] is None
        assert user["login_streak"] is None
        assert user["active_status"] is False

    def test_owner_still_sees_own_activity_when_opted_out(self, client, db_session, auth_headers, owner):
        self._owner_active(db_session, owner, show_active_status=False)

        user = _get(client, auth_headers, owner, owner.username).get_json()["data"]["user"]

        assert user["last_active"] is not None
        assert user["login_streak"] == 7

    def test_private_view_hides_last_active_when_opted_out(
        self, client, db_session, auth_headers, owner, viewer
    ):
        self._owner_active(db_session, owner, set_profile_private=True, show_active_status=False)

        data = _get(client, auth_headers, viewer, owner.username).get_json()["data"]

        assert data["type"] == "private"
        assert data["last_active"] is None


class TestPublicPayloadShape:
    def test_stats_only_contain_fields_the_page_uses(self, client, auth_headers, owner, viewer):
        data = _get(client, auth_headers, viewer, owner.username).get_json()["data"]

        assert set(data["stats"]) == {
            "total_posts", "total_comments", "total_threads", "total_helpful",
        }
        assert "activity_heatmap" not in data
