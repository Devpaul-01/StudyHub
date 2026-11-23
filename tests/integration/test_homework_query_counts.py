"""
tests/integration/test_homework_query_counts.py

Audit F-16: stats, chart and list endpoints must cost a bounded number of SQL
statements regardless of how much history the user has (no row loading, no
per-item count queries), and the SQL aggregates must match the real rows.
"""

import datetime

import pytest
from sqlalchemy import event

pytestmark = pytest.mark.integration


class _QueryCounter:
    def __init__(self, engine):
        self.engine = engine
        self.count = 0

    def _on(self, conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            self.count += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._on)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._on)


def _seed(db_session, make_assignment, owner, helper, n):
    from models import HomeworkSubmission

    now = datetime.datetime.utcnow()
    for i in range(n):
        a = make_assignment(
            owner,
            title=f"A{i}",
            subject=f"S{i % 3}",
            status=["not_started", "in_progress", "completed"][i % 3],
            is_shared_for_help=(i % 2 == 0),
            due_date=now + datetime.timedelta(days=(-1 if i % 4 == 0 else 3)),
        )
        db_session.add(HomeworkSubmission(
            requester_id=owner.id, helper_id=helper.id, assignment_id=a.id,
            title=a.title, subject=a.subject, difficulty="medium",
            status=["pending", "submitted", "reviewed", "completed"][i % 4],
            response_time_seconds=600 * (i + 1) if i % 4 == 3 else None,
            reaction_type="thanks" if i % 4 == 3 else None,
        ))
    db_session.commit()


def _count_for(client, db_session, url, headers):
    from extensions import db

    with _QueryCounter(db.engine) as counter:
        resp = client.get(url, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    return counter.count, resp.get_json()["data"]


@pytest.mark.parametrize("url", [
    "/student/homework/stats",
    "/student/homework/stats/charts",
])
def test_stats_endpoints_query_count_is_independent_of_history(
    url, client, db_session, make_user, make_assignment, auth_headers
):
    owner, helper = make_user(), make_user()
    _seed(db_session, make_assignment, owner, helper, 4)
    small, _ = _count_for(client, db_session, url, auth_headers(helper))

    _seed(db_session, make_assignment, owner, helper, 40)
    large, _ = _count_for(client, db_session, url, auth_headers(helper))

    assert large == small


def test_my_assignments_has_no_per_item_count_queries(
    client, db_session, make_user, make_assignment, auth_headers
):
    owner, helper = make_user(), make_user()
    _seed(db_session, make_assignment, owner, helper, 3)
    small, _ = _count_for(client, db_session, "/student/assignments?limit=50", auth_headers(owner))

    _seed(db_session, make_assignment, owner, helper, 30)
    large, _ = _count_for(client, db_session, "/student/assignments?limit=50", auth_headers(owner))

    assert large == small


def test_stats_values_match_rows(client, db_session, make_user, make_assignment, auth_headers):
    owner, helper = make_user(), make_user()
    _seed(db_session, make_assignment, owner, helper, 12)

    _, mine = _count_for(client, db_session, "/student/homework/stats", auth_headers(owner))
    ma = mine["my_assignments"]
    assert ma["total"] == 12
    assert (ma["not_started"], ma["in_progress"], ma["completed"]) == (4, 4, 4)
    assert ma["shared_for_help"] == 6
    # due in the past when i % 4 == 0 -> i in {0, 4, 8}; i=8 is completed (8 % 3 == 2), so 2 overdue
    assert ma["overdue"] == 2
    assert mine["help_received"] == {"total": 12, "pending": 3, "submitted": 3, "reviewed": 3, "completed": 3}
    assert mine["subjects"]["my_subjects"] and set(mine["subjects"]["my_subjects"]) == {"S0", "S1", "S2"}

    _, theirs = _count_for(client, db_session, "/student/homework/stats", auth_headers(helper))
    assert theirs["help_given"]["total"] == 12
    assert set(theirs["subjects"]["helping_with"]) == {"S0", "S1", "S2"}


def test_chart_values_match_rows(client, db_session, make_user, make_assignment, auth_headers):
    owner, helper = make_user(), make_user()
    _seed(db_session, make_assignment, owner, helper, 8)

    _, data = _count_for(client, db_session, "/student/homework/stats/charts", auth_headers(helper))

    assert sum(d["helps_given"] for d in data["daily_activity"]) == 8
    assert data["daily_activity"][-1]["helps_given"] == 8  # all created "today"
    assert sum(r["count"] for r in data["reactions_received"]) == 2
    assert sum(s["total"] for s in data["subject_completion"]) == 8
    assert data["response_time"]["total_timed"] == 2
