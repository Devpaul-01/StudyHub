"""
tests/integration/test_homework_gamification.py

Regression tests for audit findings F-06 (helpful semantics, helps counter),
F-18 (reaction keys) and the streak half of F-23 (effective streak).
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


def _submitted(db_session, make_assignment, owner, helper, **kw):
    from models import HomeworkSubmission

    a = make_assignment(owner, is_shared_for_help=True)
    sub = HomeworkSubmission(
        requester_id=owner.id, helper_id=helper.id, assignment_id=a.id,
        title="T", subject="Math", status="submitted", solution_text="s", **kw,
    )
    db_session.add(sub)
    db_session.flush()
    return sub


def _feedback(client, owner, sub, body, auth_headers, csrf_headers):
    return client.post(
        f"/student/homework/submission/{sub.id}/give-feedback", json=body,
        headers={**auth_headers(owner), **csrf_headers(client)},
    )


def _helper_state(helper):
    from models import User

    u = User.query.get(helper.id)
    return u.total_helps_given, u.help_streak_current


class TestHelpfulSemantics:
    def test_resolve_is_helpful_rules(self):
        from services.homework_service import resolve_is_helpful as r

        base = dict(previously_helpful=False, reaction_type=None, rating=None, explicit=None)
        assert r(**base) is False                              # nothing assumed
        assert r(**{**base, "explicit": True}) is True
        assert r(**{**base, "reaction_type": "thanks"}) is True
        assert r(**{**base, "rating": 3}) is True
        assert r(**{**base, "rating": 2}) is False
        assert r(**{**base, "rating": 2, "reaction_type": "thanks"}) is False   # low rating wins
        assert r(**{**base, "explicit": False, "reaction_type": "thanks"}) is False
        assert r(**{**base, "previously_helpful": True}) is True
        assert r(**{**base, "previously_helpful": True, "explicit": False}) is False

    @pytest.mark.parametrize("body", [{"rating": 1}, {"rating": 2}, {"is_helpful": False}, {}])
    def test_no_positive_signal_completes_without_credit(
        self, body, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        from models import HomeworkSubmission

        owner, helper = make_user(), make_user()
        sub = _submitted(db_session, make_assignment, owner, helper)

        resp = _feedback(client, owner, sub, body, auth_headers, csrf_headers)

        assert resp.status_code == 200
        fresh = HomeworkSubmission.query.get(sub.id)
        assert str(getattr(fresh.status, "value", fresh.status)) == "completed"
        assert fresh.is_marked_helpful is False
        assert _helper_state(helper) == (0, 0)

    @pytest.mark.parametrize("body", [{"rating": 3}, {"rating": 5}, {"reaction_type": "perfect"},
                                      {"is_helpful": True}])
    def test_positive_signal_gives_credit(
        self, body, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        sub = _submitted(db_session, make_assignment, owner, helper)

        _feedback(client, owner, sub, body, auth_headers, csrf_headers)

        assert _helper_state(helper) == (1, 1)

    def test_two_helps_same_day_count_twice_but_streak_once(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        for _ in range(2):
            sub = _submitted(db_session, make_assignment, owner, helper)
            _feedback(client, owner, sub, {"is_helpful": True}, auth_headers, csrf_headers)

        assert _helper_state(helper) == (2, 1)

    def test_consecutive_days_extend_the_streak(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        helper.help_streak_current = 1
        helper.help_streak_longest = 1
        helper.total_helps_given = 1
        helper.help_streak_last_updated = datetime.datetime.utcnow() - datetime.timedelta(days=1)
        db_session.flush()
        sub = _submitted(db_session, make_assignment, owner, helper)

        _feedback(client, owner, sub, {"is_helpful": True}, auth_headers, csrf_headers)

        assert _helper_state(helper) == (2, 2)

    def test_gap_resets_streak(
        self, client, db_session, make_user, make_assignment, auth_headers, csrf_headers
    ):
        owner, helper = make_user(), make_user()
        helper.help_streak_current = 5
        helper.help_streak_longest = 5
        helper.help_streak_last_updated = datetime.datetime.utcnow() - datetime.timedelta(days=4)
        db_session.flush()
        sub = _submitted(db_session, make_assignment, owner, helper)

        _feedback(client, owner, sub, {"is_helpful": True}, auth_headers, csrf_headers)

        assert _helper_state(helper)[1] == 1


class TestReactionKeys:
    def test_every_accepted_reaction_is_counted_in_charts(
        self, client, db_session, make_user, make_assignment, auth_headers
    ):
        from models import HomeworkSubmission
        from services.homework_validation import REACTION_LABELS

        owner, helper = make_user(), make_user()
        for key in REACTION_LABELS:
            a = make_assignment(owner, is_shared_for_help=True)
            db_session.add(HomeworkSubmission(
                requester_id=owner.id, helper_id=helper.id, assignment_id=a.id, title="T",
                status="completed", reaction_type=key,
            ))
        db_session.flush()

        resp = client.get("/student/homework/stats/charts", headers=auth_headers(helper))

        counts = {r["reaction"]: r["count"] for r in resp.get_json()["data"]["reactions_received"]}
        assert counts == {label: 1 for label in REACTION_LABELS.values()}

    def test_reaction_keys_match_validator_and_constraint(self):
        from services.homework_validation import REACTION_LABELS, REACTION_TYPES
        from models import HomeworkSubmission

        assert set(REACTION_TYPES) == set(REACTION_LABELS)
        check = next(
            c for c in HomeworkSubmission.__table__.constraints
            if getattr(c, "name", None) == "ck_hw_submissions_reaction"
        )
        assert all(f"'{k}'" in str(check.sqltext) for k in REACTION_TYPES)


class TestEffectiveStreak:
    def test_pure_decay_rules(self):
        from services.homework_service import effective_help_streak as e

        now = datetime.datetime(2030, 1, 10, 12)
        assert e(7, datetime.datetime(2030, 1, 10, 1), now=now) == 7   # today
        assert e(7, datetime.datetime(2030, 1, 9, 23), now=now) == 7   # yesterday: at risk, alive
        assert e(7, datetime.datetime(2030, 1, 8, 23), now=now) == 0   # missed a day
        assert e(7, None, now=now) == 0
        assert e(None, datetime.datetime(2030, 1, 10), now=now) == 0

    def test_my_streak_endpoint_shows_zero_after_inactivity(
        self, client, db_session, make_user, auth_headers
    ):
        user = make_user()
        user.help_streak_current = 7
        user.help_streak_longest = 7
        user.help_streak_last_updated = datetime.datetime.utcnow() - datetime.timedelta(days=10)
        db_session.flush()

        resp = client.get("/student/homework/my-streak", headers=auth_headers(user))

        data = resp.get_json()["data"]
        assert data["current_streak"] == 0
        assert data["longest_streak"] == 7


class TestListPayloads:
    def test_stats_cover_all_assignments_not_just_the_filtered_list(
        self, client, make_user, make_assignment, auth_headers
    ):
        user = make_user()
        past = datetime.datetime.utcnow() - datetime.timedelta(days=1)
        make_assignment(user, status="completed")
        make_assignment(user, status="completed")
        make_assignment(user, status="in_progress")
        make_assignment(user, status="not_started", due_date=past, is_shared_for_help=True)

        resp = client.get("/student/assignments", headers=auth_headers(user))  # default: active

        data = resp.get_json()["data"]
        assert data["total"] == 2                       # filtered list size
        assert data["stats"]["completed"] == 2          # was always 0 before F-23
        assert data["stats"]["in_progress"] == 1
        assert data["stats"]["not_started"] == 1
        assert data["stats"]["overdue"] == 1
        assert data["stats"]["shared_for_help"] == 1

    def test_help_requests_count_is_grouped_and_ignores_cancelled(
        self, client, db_session, make_user, make_assignment, auth_headers
    ):
        from models import HomeworkSubmission

        owner = make_user()
        a = make_assignment(owner, is_shared_for_help=True)
        b = make_assignment(owner, is_shared_for_help=True)
        for helper, status in ((make_user(), "pending"), (make_user(), "submitted"), (make_user(), "cancelled")):
            db_session.add(HomeworkSubmission(requester_id=owner.id, helper_id=helper.id,
                                              assignment_id=a.id, title="T", status=status))
        db_session.flush()

        resp = client.get("/student/assignments", headers=auth_headers(owner))

        counts = {x["id"]: x["help_requests_count"] for x in resp.get_json()["data"]["assignments"]}
        assert counts == {a.id: 2, b.id: 0}

    def test_feed_available_subjects_not_collapsed_by_subject_filter(
        self, client, make_user, make_assignment, make_connection, auth_headers
    ):
        me, friend = make_user(), make_user()
        make_connection(me, friend)
        make_assignment(friend, subject="Math", is_shared_for_help=True)
        make_assignment(friend, subject="Physics", is_shared_for_help=True)

        resp = client.get("/student/homework/feed?subject=Math", headers=auth_headers(me))

        data = resp.get_json()["data"]
        assert [h["subject"] for h in data["homework"]] == ["Math"]
        assert data["available_subjects"] == ["Math", "Physics"]
