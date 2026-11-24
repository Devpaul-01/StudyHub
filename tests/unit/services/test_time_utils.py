"""Tests for services/time_utils.py and its use for activity day boundaries."""
import datetime

from services import time_utils
from services import post_service
from models import UserActivity


def test_utc_today_matches_utc_clock_not_local_clock(monkeypatch):
    """Just after UTC midnight the local date may still be 'yesterday'."""
    fixed = datetime.datetime(2026, 3, 1, 0, 5, 0)

    class FrozenDatetime(datetime.datetime):
        @classmethod
        def utcnow(cls):
            return fixed

    monkeypatch.setattr(time_utils.datetime, "datetime", FrozenDatetime)
    assert time_utils.utc_today() == datetime.date(2026, 3, 1)


def test_update_user_activity_uses_utc_day(db_session, make_user, monkeypatch):
    user = make_user()
    db_session.commit()
    monkeypatch.setattr(post_service, "utc_today", lambda: datetime.date(2026, 3, 1))

    post_service.update_user_activity(user.id, "post")
    db_session.commit()

    row = UserActivity.query.filter_by(user_id=user.id).one()
    assert row.activity_date == datetime.date(2026, 3, 1)
    assert row.posts_created == 1
