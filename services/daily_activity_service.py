"""
Daily activity bookkeeping (the ``user_activity`` table).

One row per (user, UTC day) feeds the analytics heatmap, streaks and
weekly/overview numbers. Several request paths bump it concurrently
(post, comment, login, ...), so the write has to be atomic:

* The row is created/incremented with a single
  ``INSERT ... ON CONFLICT (user_id, activity_date) DO UPDATE SET
  col = col + n`` statement. The old read-modify-write in Python lost
  updates under concurrency, and the check-then-insert raced on the
  unique constraint, which turned into a failed *business* commit
  (e.g. a post or comment that was otherwise valid).
* The statement runs inside a SAVEPOINT and any failure is swallowed
  (and logged): activity bookkeeping must never be able to fail the user
  action it is attached to.

The caller owns the transaction: nothing here commits.
"""
from __future__ import annotations

import datetime
import logging

from sqlalchemy import func

from extensions import db
from models import UserActivity
from services.db_upsert import dialect_insert
from services.time_utils import utc_today

logger = logging.getLogger(__name__)

# activity_type -> counter column bumped in addition to activity_score.
COUNTER_FIELDS = {
    "post": "posts_created",
    "comment": "comments_created",
    "message": "messages_sent",
    "helpful": "helpful_count",
}

_ALL_COUNTERS = (
    "posts_created",
    "comments_created",
    "threads_joined",
    "messages_sent",
    "helpful_count",
)


def _increment_row(user_id: int, today: datetime.date, counter_field: str | None, score: int) -> None:
    table = UserActivity.__table__
    insert_values = {
        "user_id": user_id,
        "activity_date": today,
        "activity_score": score,
        **{name: 0 for name in _ALL_COUNTERS},
    }
    update_values = {
        "activity_score": func.coalesce(table.c.activity_score, 0) + score,
    }
    if counter_field:
        insert_values[counter_field] = 1
        update_values[counter_field] = func.coalesce(table.c[counter_field], 0) + 1

    insert = dialect_insert()
    if insert is None:
        # Unknown dialect: fall back to get-or-create + increment.
        row = UserActivity.query.filter_by(user_id=user_id, activity_date=today).first()
        if row is None:
            db.session.add(UserActivity(**insert_values))
        else:
            row.activity_score = (row.activity_score or 0) + score
            if counter_field:
                setattr(row, counter_field, (getattr(row, counter_field) or 0) + 1)
        return

    stmt = insert(table).values(**insert_values).on_conflict_do_update(
        index_elements=["user_id", "activity_date"],
        set_=update_values,
    )
    db.session.execute(stmt)


def record_daily_activity(
    user_id: int,
    activity_type: str,
    score: int = 1,
    today: datetime.date | None = None,
) -> UserActivity | None:
    """
    Atomically add one unit of `activity_type` (and `score` points) to the
    user's activity row for `today` (UTC). Creates the row if needed.

    Returns the refreshed UserActivity row, or None if bookkeeping failed
    (the failure is logged and the surrounding transaction is untouched).
    Does not commit.
    """
    today = today or utc_today()
    counter_field = COUNTER_FIELDS.get(activity_type)
    try:
        with db.session.begin_nested():
            _increment_row(user_id, today, counter_field, score)
        return (
            UserActivity.query.filter_by(user_id=user_id, activity_date=today)
            .populate_existing()
            .first()
        )
    except Exception:
        logger.exception("record_daily_activity failed (%s, user %s)", activity_type, user_id)
        return None
