"""
tests/integration/test_profile_routes.py

routes/student/profile.py has 24 endpoints; only GET /profile/me/data
had any integration coverage before this (test_auth_lifecycle.py). This
file covers the core profile-management CRUD surface: academic info,
visibility settings, profile update, skills, learning goals, pin/unpin
posts, and study schedule. The AI-suggestion endpoints (help/suggestions,
can-help/suggestions, homepage) are deliberately out of scope here --
they lean on heavier recommendation logic worth their own focused pass.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# GET / PUT /profile/academic-info
# ============================================================================

class TestAcademicInfo:
    def test_get_returns_defaults_when_no_onboarding_row_exists(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/profile/academic-info", headers=auth_headers(user))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["subjects"] == []
        assert data["learning_style"] is None

    def test_get_returns_onboarding_data_when_present(
        self, client, make_user, make_onboarding_details, auth_headers
    ):
        user = make_user()
        make_onboarding_details(user, subjects=["Calculus", "Physics"], learning_style="Visual")

        resp = client.get("/student/profile/academic-info", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert data["subjects"] == ["Calculus", "Physics"]
        assert data["learning_style"] == "Visual"

    def test_put_updates_subjects_and_returns_changes_list(
        self, client, db_session, make_user, make_onboarding_details, auth_headers, csrf_headers
    ):
        user = make_user()
        make_onboarding_details(user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            "/student/profile/academic-info",
            json={"subjects": ["Chemistry", "Biology"]},
            headers=headers,
        )

        assert resp.status_code == 200
        assert "subjects" in resp.get_json()["data"]["changes"]

        from models import OnboardingDetails
        refreshed = OnboardingDetails.query.filter_by(user_id=user.id).first()
        assert refreshed.subjects == ["Chemistry", "Biology"]

    def test_put_truncates_subjects_to_15(
        self, client, make_user, make_onboarding_details, auth_headers, csrf_headers
    ):
        user = make_user()
        make_onboarding_details(user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            "/student/profile/academic-info",
            json={"subjects": [f"Subject{i}" for i in range(20)]},
            headers=headers,
        )

        from models import OnboardingDetails
        refreshed = OnboardingDetails.query.filter_by(user_id=user.id).first()
        assert len(refreshed.subjects) == 15

    def test_put_without_onboarding_row_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put(
            "/student/profile/academic-info", json={"subjects": ["X"]}, headers=headers
        )

        assert resp.status_code == 400
        assert "not found" in resp.get_json()["message"].lower()

    def test_put_no_data_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.put("/student/profile/academic-info", json=None, headers=headers)

        assert resp.status_code == 400


# ============================================================================
# GET / POST /profile/visibility-settings
# ============================================================================

class TestVisibilitySettings:
    def test_get_returns_defaults_when_never_set(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/profile/visibility-settings", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert data["set_profile_private"] is False
        assert data["show_active_status"] is True

    def test_post_then_get_round_trips_settings(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        post_resp = client.post(
            "/student/profile/visibility-settings",
            json={
                "set_profile_private": True,
                "show_active_status": False,
                "show_in_activity_feed": False,
            },
            headers=headers,
        )
        assert post_resp.status_code == 200

        get_resp = client.get("/student/profile/visibility-settings", headers=auth_headers(user))
        data = get_resp.get_json()["data"]
        assert data == {
            "set_profile_private": True,
            "show_active_status": False,
            "show_in_activity_feed": False,
        }

    def test_partial_update_keeps_other_settings(
        self, client, make_user, auth_headers, csrf_headers
    ):
        """Regression: omitted keys used to be reset to their defaults."""
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}
        client.post(
            "/student/profile/visibility-settings",
            json={"set_profile_private": True, "show_active_status": False,
                  "show_in_activity_feed": False},
            headers=headers,
        )

        resp = client.post(
            "/student/profile/visibility-settings",
            json={"show_in_activity_feed": True},
            headers=headers,
        )
        assert resp.status_code == 200

        data = client.get("/student/profile/visibility-settings",
                          headers=auth_headers(user)).get_json()["data"]
        assert data["set_profile_private"] is True
        assert data["show_active_status"] is False
        assert data["show_in_activity_feed"] is True

    @pytest.mark.parametrize("bad_value", ["false", "true", 1, 0, None, [], {}])
    def test_rejects_non_boolean_values(
        self, client, make_user, auth_headers, csrf_headers, bad_value
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/profile/visibility-settings",
                           json={"set_profile_private": bad_value}, headers=headers)

        assert resp.status_code == 400
        data = client.get("/student/profile/visibility-settings",
                          headers=auth_headers(user)).get_json()["data"]
        assert data["set_profile_private"] is False

    def test_rejects_unknown_empty_and_non_object_bodies(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}
        url = "/student/profile/visibility-settings"

        assert client.post(url, json={"nope": True}, headers=headers).status_code == 400
        assert client.post(url, json={}, headers=headers).status_code == 400
        assert client.post(url, json=[True], headers=headers).status_code == 400
        assert client.post(url, data="not json", headers=headers).status_code == 400

    def test_legacy_keys_are_ignored_not_rejected(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/profile/visibility-settings",
            json={"set_profile_private": True, "set_dark_mode": True,
                  "send_weekly_notification": False},
            headers=headers,
        )

        assert resp.status_code == 200
        data = client.get("/student/profile/visibility-settings",
                          headers=auth_headers(user)).get_json()["data"]
        assert "set_dark_mode" not in data
        assert "send_weekly_notification" not in data
        assert data["set_profile_private"] is True


# ============================================================================
# PATCH /profile/update
# ============================================================================

class TestUpdateProfile:
    def test_updates_name_and_bio(
        self, client, db_session, make_user, make_student_profile, auth_headers, csrf_headers
    ):
        user = make_user(name="Old Name")
        make_student_profile(user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.patch(
            "/student/profile/update",
            json={"name": "New Name", "bio": "A new bio"},
            headers=headers,
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert "name" in body["data"]["changes"]
        assert "bio" in body["data"]["changes"]

        from models import User
        refreshed = User.query.get(user.id)
        assert refreshed.name == "New Name"
        assert refreshed.bio == "A new bio"

    def test_name_too_short_returns_error(
        self, client, make_user, make_student_profile, auth_headers, csrf_headers
    ):
        user = make_user()
        make_student_profile(user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.patch("/student/profile/update", json={"name": "ab"}, headers=headers)

        assert resp.status_code == 400
        assert "at least 3 characters" in resp.get_json()["message"]

    def test_bio_too_long_returns_error(
        self, client, make_user, make_student_profile, auth_headers, csrf_headers
    ):
        user = make_user()
        make_student_profile(user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.patch(
            "/student/profile/update", json={"bio": "x" * 501}, headers=headers
        )

        assert resp.status_code == 400

    def test_no_changes_returns_success_without_changes(
        self, client, make_user, make_student_profile, auth_headers, csrf_headers
    ):
        user = make_user(name="Same Name")
        make_student_profile(user)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.patch("/student/profile/update", json={"name": "Same Name"}, headers=headers)

        assert resp.status_code == 200
        assert "No changes" in resp.get_json()["message"]

    def test_missing_profile_returns_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()  # no StudentProfile row created
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.patch("/student/profile/update", json={"bio": "hi"}, headers=headers)

        assert resp.status_code == 404


# ============================================================================
# POST /profile/skills, DELETE /profile/skills/<skill_name>
# ============================================================================

class TestSkills:
    def test_add_skill_happy_path(self, client, db_session, make_user, auth_headers, csrf_headers):
        user = make_user(skills=[])
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/profile/skills", json={"skill": "Python"}, headers=headers)

        assert resp.status_code == 201
        assert "Python" in resp.get_json()["data"]["skills"]

    def test_add_skill_empty_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/profile/skills", json={"skill": "  "}, headers=headers)

        assert resp.status_code == 400

    def test_add_duplicate_skill_case_insensitive_returns_error(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user(skills=["python"])
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/profile/skills", json={"skill": "Python"}, headers=headers)

        assert resp.status_code == 400
        assert "already added" in resp.get_json()["message"].lower()

    def test_add_skill_at_max_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user(skills=[f"skill{i}" for i in range(10)])
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post("/student/profile/skills", json={"skill": "One More"}, headers=headers)

        assert resp.status_code == 400
        assert "Maximum 10" in resp.get_json()["message"]

    def test_remove_skill_case_insensitive(self, client, db_session, make_user, auth_headers, csrf_headers):
        user = make_user(skills=["Python", "SQL"])
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete("/student/profile/skills/python", headers=headers)

        assert resp.status_code == 200
        assert resp.get_json()["data"]["skills"] == ["SQL"]

    def test_remove_nonexistent_skill_is_a_no_op_success(self, client, make_user, auth_headers, csrf_headers):
        user = make_user(skills=["SQL"])
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete("/student/profile/skills/does-not-exist", headers=headers)

        assert resp.status_code == 200
        assert resp.get_json()["data"]["skills"] == ["SQL"]


# ============================================================================
# POST /profile/learning-goals, DELETE /profile/learning-goals/<index>
# ============================================================================

class TestLearningGoals:
    def test_add_goal_happy_path(self, client, make_user, auth_headers, csrf_headers):
        user = make_user(learning_goals=[])
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/profile/learning-goals", json={"goal": "Learn ML"}, headers=headers
        )

        assert resp.status_code == 201
        assert "Learn ML" in resp.get_json()["data"]["learning_goals"]

    def test_add_duplicate_goal_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user(learning_goals=["Learn ML"])
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/profile/learning-goals", json={"goal": "Learn ML"}, headers=headers
        )

        assert resp.status_code == 400

    def test_add_goal_at_max_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user(learning_goals=[f"Goal {i}" for i in range(5)])
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/profile/learning-goals", json={"goal": "One More"}, headers=headers
        )

        assert resp.status_code == 400
        assert "Maximum 5" in resp.get_json()["message"]

    def test_remove_goal_by_valid_index(self, client, make_user, auth_headers, csrf_headers):
        user = make_user(learning_goals=["Goal A", "Goal B"])
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete("/student/profile/learning-goals/0", headers=headers)

        assert resp.status_code == 200
        assert resp.get_json()["data"]["learning_goals"] == ["Goal B"]

    def test_remove_goal_invalid_index_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user(learning_goals=["Goal A"])
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.delete("/student/profile/learning-goals/99", headers=headers)

        assert resp.status_code == 400


# ============================================================================
# POST /profile/pin-post/<id>, POST /profile/unpin-post/<id>
# ============================================================================

class TestPinUnpinPost:
    def test_pin_own_post(self, client, db_session, make_user, make_post, auth_headers, csrf_headers):
        user = make_user()
        post = make_post(user, is_pinned=False)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(f"/student/profile/pin-post/{post.id}", headers=headers)

        assert resp.status_code == 200
        from models import Post
        assert Post.query.get(post.id).is_pinned is True

    def test_pin_someone_elses_post_returns_error(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        owner = make_user()
        other = make_user()
        post = make_post(owner)
        headers = {**auth_headers(other), **csrf_headers(client)}

        resp = client.post(f"/student/profile/pin-post/{post.id}", headers=headers)

        assert resp.status_code == 400
        assert "not yours" in resp.get_json()["message"].lower()

    def test_pinning_already_pinned_post_is_idempotent_success(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        user = make_user()
        post = make_post(user, is_pinned=True)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(f"/student/profile/pin-post/{post.id}", headers=headers)

        assert resp.status_code == 200
        assert "already" in resp.get_json()["message"].lower()

    def test_pin_sixth_post_rejected_at_five_pin_limit(
        self, client, make_user, make_post, auth_headers, csrf_headers
    ):
        user = make_user()
        for _ in range(5):
            make_post(user, is_pinned=True)
        sixth = make_post(user, is_pinned=False)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(f"/student/profile/pin-post/{sixth.id}", headers=headers)

        assert resp.status_code == 400
        assert "can't pin more than 5" in resp.get_json()["message"].lower()

    def test_unpin_own_post(self, client, db_session, make_user, make_post, auth_headers, csrf_headers):
        user = make_user()
        post = make_post(user, is_pinned=True)
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(f"/student/profile/unpin-post/{post.id}", headers=headers)

        assert resp.status_code == 200
        from models import Post
        assert Post.query.get(post.id).is_pinned is False


# ============================================================================
# GET / POST /profile/study-schedule
# ============================================================================

class TestStudySchedule:
    def test_get_returns_all_seven_days_with_defaults(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/profile/study-schedule", headers=auth_headers(user))

        schedule = resp.get_json()["data"]["study_schedule"]
        assert set(schedule.keys()) == {
            "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
        }
        assert schedule["monday"] == []

    def test_post_updates_schedule_and_get_reflects_it(
        self, client, make_user, auth_headers, csrf_headers
    ):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        post_resp = client.post(
            "/student/profile/study-schedule",
            json={"monday": ["morning", "evening"]},
            headers=headers,
        )
        assert post_resp.status_code == 200

        get_resp = client.get("/student/profile/study-schedule", headers=auth_headers(user))
        assert get_resp.get_json()["data"]["study_schedule"]["monday"] == ["morning", "evening"]

    def test_post_invalid_day_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/profile/study-schedule", json={"notaday": ["morning"]}, headers=headers
        )

        assert resp.status_code == 400

    def test_post_invalid_time_slot_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/profile/study-schedule", json={"monday": ["midnight"]}, headers=headers
        )

        assert resp.status_code == 400

    def test_post_non_object_body_returns_error(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.post(
            "/student/profile/study-schedule", json=["not", "an", "object"], headers=headers
        )

        assert resp.status_code == 400


# ============================================================================
# GET /skills/popular (public-ish, still token_required per blueprint norms)
# ============================================================================

class TestPopularSkills:
    def test_returns_skills_sorted_by_frequency(self, client, make_user):
        make_user(skills=["Python", "SQL"])
        make_user(skills=["Python"])
        make_user(skills=["SQL"])
        make_user(skills=["Python"])

        resp = client.get("/student/skills/popular")

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["skills"][0] == "Python"
        assert data["detailed"][0] == {"name": "Python", "count": 3}
        assert data["detailed"][1] == {"name": "SQL", "count": 2}

    def test_counts_are_case_insensitive_but_keep_first_seen_casing(self, client, make_user):
        make_user(skills=["python"])
        make_user(skills=["Python"])

        resp = client.get("/student/skills/popular")

        detailed = resp.get_json()["data"]["detailed"]
        assert len(detailed) == 1
        assert detailed[0]["count"] == 2

    def test_empty_when_no_users_have_skills(self, client, make_user):
        make_user(skills=[])

        resp = client.get("/student/skills/popular")

        assert resp.status_code == 200
        assert resp.get_json()["data"]["skills"] == []


# ============================================================================
# GET /profile/my-stats
# ============================================================================

class TestMyStats:
    def test_marked_helpful_reflects_the_users_counter(
        self, client, db_session, make_user, auth_headers
    ):
        """Regression: this value was hard-coded to 0."""
        user = make_user()
        user.total_helpful = 4
        db_session.commit()

        resp = client.get("/student/profile/my-stats", headers=auth_headers(user))

        assert resp.status_code == 200
        engagement = resp.get_json()["data"]["engagement"]
        assert engagement["helpful_received"] == 4


# ============================================================================
# /profile/me page route
# ============================================================================

class TestOwnProfilePage:
    def test_get_serves_the_app_shell(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/profile/me", headers=auth_headers(user))

        assert resp.status_code == 200
        assert resp.content_type.startswith("text/html")

    def test_post_is_no_longer_a_json_api(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        headers = {**auth_headers(user), **csrf_headers(client)}

        assert client.post("/student/profile/me", headers=headers).status_code == 405


class TestPageRoutesWithoutSession:
    """The shells carry no data; the SPA redirects to login on the first 401."""

    @pytest.mark.parametrize("path", ["/student/profile/me", "/student/profile/view/someone"])
    def test_shell_is_html_not_json_401(self, client, path):
        resp = client.get(path)

        assert resp.status_code == 200
        assert resp.content_type.startswith("text/html")

    def test_data_api_still_requires_a_session(self, client):
        assert client.get("/student/profile/someone").status_code == 401
        assert client.get("/student/profile/me/data").status_code == 401


class TestReservedUsernames:
    @pytest.mark.parametrize("name", ["homepage", "update", "avatar", "skills", "view"])
    def test_check_username_rejects_names_that_shadow_profile_routes(self, client, csrf_headers, name):
        resp = client.post("/student/check-username", json={"username": name}, headers=csrf_headers(client))

        assert resp.status_code == 400
        assert resp.get_json()["message"] == "Username taken"


# ============================================================================
# PATCH /profile/update — validation and consistency
# ============================================================================

class TestUpdateProfileValidation:
    @pytest.fixture
    def user(self, make_user, make_student_profile):
        user = make_user(name="Original Name")
        make_student_profile(user)
        return user

    def _patch(self, client, auth_headers, csrf_headers, user, payload):
        headers = {**auth_headers(user), **csrf_headers(client)}
        return client.patch("/student/profile/update", json=payload, headers=headers)

    @pytest.mark.parametrize("name", [
        '"><img src=x onerror=alert(1)>', "<script>alert(1)</script>", "a<b", "x>y",
    ])
    def test_name_with_markup_is_rejected(self, client, auth_headers, csrf_headers, user, name):
        resp = self._patch(client, auth_headers, csrf_headers, user, {"name": name})

        assert resp.status_code == 400
        assert "<" in name or ">" in name
        assert user.name == "Original Name"

    @pytest.mark.parametrize("bad", [12345, None, ["x"], {"a": 1}])
    def test_non_string_name_gets_a_validation_error_not_a_server_error(
        self, client, auth_headers, csrf_headers, user, bad
    ):
        resp = self._patch(client, auth_headers, csrf_headers, user, {"name": bad})

        assert resp.status_code == 400
        assert resp.get_json()["message"] == "Name must be text"

    def test_name_longer_than_the_column_is_rejected(self, client, auth_headers, csrf_headers, user):
        resp = self._patch(client, auth_headers, csrf_headers, user, {"name": "x" * 81})

        assert resp.status_code == 400

    @pytest.mark.parametrize("bio", ["<script>alert(1)</script>", 'hi <img src=x onerror=1>'])
    def test_bio_with_tags_is_rejected(self, client, auth_headers, csrf_headers, user, bio):
        resp = self._patch(client, auth_headers, csrf_headers, user, {"bio": bio})

        assert resp.status_code == 400

    def test_bio_may_contain_plain_symbols_and_can_be_cleared(
        self, client, db_session, auth_headers, csrf_headers, user
    ):
        assert self._patch(client, auth_headers, csrf_headers, user, {"bio": "I <3 maths"}).status_code == 200
        assert user.bio == "I <3 maths"

        assert self._patch(client, auth_headers, csrf_headers, user, {"bio": ""}).status_code == 200
        assert user.bio == ""

    def test_class_level_must_be_a_known_level(self, client, auth_headers, csrf_headers, user):
        bad = self._patch(client, auth_headers, csrf_headers, user, {"class_level": "Year 3"})
        good = self._patch(client, auth_headers, csrf_headers, user, {"class_level": "300 level"})

        assert bad.status_code == 400
        assert good.status_code == 200
        assert good.get_json()["data"]["user"]["class_level"] == "300 Level"

    def test_department_is_length_limited_plain_text(self, client, auth_headers, csrf_headers, user):
        assert self._patch(client, auth_headers, csrf_headers, user, {"department": "d" * 51}).status_code == 400
        assert self._patch(client, auth_headers, csrf_headers, user, {"department": "<b>CS</b>"}).status_code == 400

    def test_department_and_class_are_mirrored_to_onboarding(
        self, client, auth_headers, csrf_headers, make_onboarding_details, user
    ):
        """Regression: /academic-info kept returning the old department."""
        make_onboarding_details(user, department="Physics", class_level="100 Level")

        resp = self._patch(client, auth_headers, csrf_headers, user,
                           {"department": "Biology", "class_level": "200 Level"})
        assert resp.status_code == 200

        academic = client.get("/student/profile/academic-info",
                              headers=auth_headers(user)).get_json()["data"]
        mine = client.get("/student/profile/me/data",
                          headers=auth_headers(user)).get_json()["data"]
        assert academic["department"] == mine["department"] == "Biology"
        assert academic["class_level"] == mine["class_level"] == "200 Level"

    def test_non_object_body_is_rejected(self, client, auth_headers, csrf_headers, user):
        headers = {**auth_headers(user), **csrf_headers(client)}

        resp = client.patch("/student/profile/update", json=["name"], headers=headers)

        assert resp.status_code == 400
