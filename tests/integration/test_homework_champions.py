"""
tests/integration/test_homework_champions.py

Regression tests for audit finding F-15: the weekly champions writer.
"""
import datetime

import pytest

from services import champion_service as cs

pytestmark = pytest.mark.integration

WEEK = datetime.date(2030, 1, 7)          # a Monday
IN_WEEK = datetime.datetime(2030, 1, 9, 12, 0)


def _help(db_session, make_assignment, owner, helper, *, subject="Math", at=IN_WEEK,
          helpful=True, status="completed", response=600):
    from models import HomeworkSubmission

    a = make_assignment(owner, is_shared_for_help=True, subject=subject)
    row = HomeworkSubmission(
        requester_id=owner.id, helper_id=helper.id, assignment_id=a.id, title="T",
        subject=subject, status=status, is_marked_helpful=helpful,
        feedback_at=at, response_time_seconds=response,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _rows():
    from models import WeeklyChampion

    return {
        (r.champion_type, r.subject): r
        for r in WeeklyChampion.query.filter_by(week_start=WEEK).all()
    }


class TestWeekHelpers:
    def test_week_start_is_monday_and_previous_week(self):
        assert cs.week_start_for(datetime.date(2030, 1, 13)) == WEEK        # Sunday -> Monday
        assert cs.previous_week_start(datetime.date(2030, 1, 14)) == WEEK   # Monday -> week before

    def test_non_monday_rejected(self):
        with pytest.raises(ValueError):
            cs.compute_weekly_champions(datetime.date(2030, 1, 8))


class TestCompute:
    def test_picks_overall_subject_and_fastest(self, db_session, make_user, make_assignment):
        owner, ann, bob = make_user(), make_user(), make_user()
        for _ in range(3):
            _help(db_session, make_assignment, owner, ann, subject="Math", response=1200)
        for _ in range(2):
            _help(db_session, make_assignment, owner, bob, subject="Physics", response=120)

        result = cs.compute_weekly_champions(WEEK)

        rows = _rows()
        assert result["created"] == 4
        assert rows[("most_helpful_overall", "")].user_id == ann.id
        assert rows[("most_helpful_overall", "")].help_count == 3
        assert rows[("subject_champion", "Math")].user_id == ann.id
        assert rows[("subject_champion", "Physics")].user_id == bob.id
        assert rows[("fastest_helper", "")].user_id == bob.id
        assert rows[("fastest_helper", "")].avg_response_time_minutes == "2.0"

    def test_only_helpful_completed_helps_in_the_week_count(
        self, db_session, make_user, make_assignment
    ):
        owner, ann = make_user(), make_user()
        _help(db_session, make_assignment, owner, ann)                                   # counts
        _help(db_session, make_assignment, owner, ann, helpful=False)                    # not helpful
        _help(db_session, make_assignment, owner, ann, status="reviewed")                # not completed
        _help(db_session, make_assignment, owner, ann, at=datetime.datetime(2030, 1, 6, 23, 59))   # Sunday before
        _help(db_session, make_assignment, owner, ann, at=datetime.datetime(2030, 1, 14, 0, 0))    # next Monday 00:00

        cs.compute_weekly_champions(WEEK)

        assert _rows()[("most_helpful_overall", "")].help_count == 1

    def test_boundaries_are_inclusive_monday_to_sunday(self, db_session, make_user, make_assignment):
        owner, ann = make_user(), make_user()
        _help(db_session, make_assignment, owner, ann, at=datetime.datetime(2030, 1, 7, 0, 0))
        _help(db_session, make_assignment, owner, ann, at=datetime.datetime(2030, 1, 13, 23, 59, 59))

        cs.compute_weekly_champions(WEEK)

        assert _rows()[("most_helpful_overall", "")].help_count == 2

    def test_no_activity_means_no_rows(self):
        assert cs.compute_weekly_champions(WEEK) == {"created": 0, "updated": 0, "removed": 0, "total": 0}

    def test_fastest_needs_minimum_helps(self, db_session, make_user, make_assignment):
        owner, ann = make_user(), make_user()
        _help(db_session, make_assignment, owner, ann, response=30)       # a single quick help

        cs.compute_weekly_champions(WEEK)

        assert ("fastest_helper", "") not in _rows()

    def test_tie_is_broken_deterministically(self, db_session, make_user, make_assignment):
        owner, ann, bob = make_user(), make_user(), make_user()
        _help(db_session, make_assignment, owner, bob, response=100)
        _help(db_session, make_assignment, owner, ann, response=900)

        cs.compute_weekly_champions(WEEK)
        first = _rows()[("most_helpful_overall", "")].user_id
        cs.compute_weekly_champions(WEEK)

        assert first == bob.id                      # equal helps -> faster wins
        assert _rows()[("most_helpful_overall", "")].user_id == first

    def test_unapproved_users_are_not_champions(self, db_session, make_user, make_assignment):
        owner, ann = make_user(), make_user()
        ann.status = "pending"
        db_session.flush()
        _help(db_session, make_assignment, owner, ann)

        assert cs.compute_weekly_champions(WEEK)["total"] == 0


class TestIdempotency:
    def test_running_twice_gives_identical_rows(self, db_session, make_user, make_assignment):
        from models import WeeklyChampion

        owner, ann = make_user(), make_user()
        for _ in range(2):
            _help(db_session, make_assignment, owner, ann)

        first = cs.compute_weekly_champions(WEEK)
        before = sorted((r.id, r.user_id, r.help_count, r.champion_type, r.subject)
                        for r in WeeklyChampion.query.all())
        second = cs.compute_weekly_champions(WEEK)
        after = sorted((r.id, r.user_id, r.help_count, r.champion_type, r.subject)
                       for r in WeeklyChampion.query.all())

        assert first["created"] > 0
        assert second == {"created": 0, "updated": 0, "removed": 0, "total": first["total"]}
        assert before == after

    def test_rerun_after_late_change_updates_and_removes(self, db_session, make_user, make_assignment):
        owner, ann, bob = make_user(), make_user(), make_user()
        h1 = _help(db_session, make_assignment, owner, ann, subject="Math")
        cs.compute_weekly_champions(WEEK)
        assert _rows()[("subject_champion", "Math")].user_id == ann.id

        h1.is_marked_helpful = False                       # feedback corrected afterwards
        _help(db_session, make_assignment, owner, bob, subject="Math")
        result = cs.compute_weekly_champions(WEEK)

        assert _rows()[("subject_champion", "Math")].user_id == bob.id
        assert result["updated"] >= 1

        db_session.query(type(h1)).delete()
        db_session.flush()
        result = cs.compute_weekly_champions(WEEK)
        assert result["removed"] >= 1 and _rows() == {}

    def test_notifications_only_for_new_rows_and_never_fail_the_job(
        self, db_session, make_user, make_assignment, monkeypatch
    ):
        from services import push_notifications

        calls = []

        def fake(user, ctype, subject=None):
            calls.append((user.id, ctype, subject))
            raise RuntimeError("fcm down")

        monkeypatch.setattr(push_notifications, "notify_became_champion", fake)
        owner, ann = make_user(), make_user()
        _help(db_session, make_assignment, owner, ann)

        cs.compute_weekly_champions(WEEK)       # must not raise even though the push raises
        cs.compute_weekly_champions(WEEK)       # nothing new -> no second push

        assert len(calls) == 2                  # overall + Math subject champion, once each
        assert ("most_helpful_overall" in {c[1] for c in calls})


class TestReaders:
    def test_endpoint_shows_latest_finished_week(
        self, client, db_session, make_user, make_assignment, auth_headers
    ):
        owner, ann = make_user(), make_user()
        _help(db_session, make_assignment, owner, ann, subject="Math")
        cs.compute_weekly_champions(WEEK)

        resp = client.get("/student/homework/champions", headers=auth_headers(owner))

        data = resp.get_json()["data"]
        assert data["week_start"] == WEEK.isoformat()
        assert data["champions"]["most_helpful"]["user"]["id"] == ann.id
        assert data["champions"]["most_helpful"]["subject"] is None      # '' is not leaked
        assert data["champions"]["subject_champions"][0]["subject"] == "Math"
        assert "progress_week_start" in data

    def test_endpoint_empty_before_the_first_run(self, client, make_user, auth_headers):
        resp = client.get("/student/homework/champions", headers=auth_headers(make_user()))

        data = resp.get_json()["data"]
        assert data["champions"] == {"subject_champions": [], "most_helpful": None, "fastest_helper": None}

    def test_scheduler_registers_the_job(self):
        import scheduler

        source = open(scheduler.__file__).read()
        assert 'id="weekly_champions"' in source and "_job_weekly_champions" in source
