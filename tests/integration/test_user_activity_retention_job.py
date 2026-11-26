"""prune_old_user_activity_job: bounded retention for user_activity (AN-20)."""
import datetime

import pytest

from models import UserActivity
from services.jobs import maintenance_jobs
from services.jobs.maintenance_jobs import USER_ACTIVITY_RETENTION_DAYS, prune_old_user_activity_job
from services.time_utils import utc_today

pytestmark = pytest.mark.integration


def _row(db_session, user, age_days):
    db_session.add(UserActivity(user_id=user.id, activity_date=utc_today() - datetime.timedelta(days=age_days),
                                posts_created=1, activity_score=5))


def test_prunes_only_rows_past_retention(db_session, make_user):
    user = make_user()
    _row(db_session, user, USER_ACTIVITY_RETENTION_DAYS + 1)
    _row(db_session, user, USER_ACTIVITY_RETENTION_DAYS + 30)
    _row(db_session, user, USER_ACTIVITY_RETENTION_DAYS)  # boundary row is kept
    _row(db_session, user, 0)
    db_session.commit()

    result = prune_old_user_activity_job()

    assert result["rows_deleted"] == 2
    assert UserActivity.query.count() == 2


def test_runs_in_batches_and_is_idempotent(db_session, make_user, monkeypatch):
    monkeypatch.setattr(maintenance_jobs, "USER_ACTIVITY_PRUNE_BATCH_SIZE", 2)
    user = make_user()
    for i in range(5):
        _row(db_session, user, USER_ACTIVITY_RETENTION_DAYS + 1 + i)
    db_session.commit()

    first = prune_old_user_activity_job()
    second = prune_old_user_activity_job()

    assert first == {"rows_deleted": 5, "batches_run": 3}
    assert second == {"rows_deleted": 0, "batches_run": 0}
