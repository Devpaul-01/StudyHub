"""
StudyHub - one-off clean-up of reputation that was paid out more than once.

Before one-time awards were made idempotent, milestone (post_10/50/100_likes)
and solution (comment_marked_solution) awards could be repeated for the same
post or comment. This module finds the surplus ledger rows (everything after
the first per user/action/related object) and can remove them while taking the
same points back off users.reputation, so the ledger and the total stay in
step.

Helpful-mark awards are intentionally not handled: legacy rows do not record
which user marked, so surplus payouts cannot be told apart from legitimate
ones from different markers.

Nothing here runs automatically. It is driven by scripts/dedupe_reputation_awards.py,
which is a dry run unless --apply is given.
"""
from collections import defaultdict

from sqlalchemy import func, select

from extensions import db
from models import ReputationHistory, User

ONE_TIME_ACTIONS = (
    "post_10_likes",
    "post_50_likes",
    "post_100_likes",
    "comment_marked_solution",
)


def find_surplus_awards():
    """Return the ledger rows that duplicate an earlier award, oldest kept."""
    groups = (
        select(
            ReputationHistory.user_id, ReputationHistory.action,
            ReputationHistory.related_type, ReputationHistory.related_id,
            func.min(ReputationHistory.id).label("keep_id"),
        )
        .where(
            ReputationHistory.action.in_(ONE_TIME_ACTIONS),
            ReputationHistory.related_id.is_not(None),
        )
        .group_by(
            ReputationHistory.user_id, ReputationHistory.action,
            ReputationHistory.related_type, ReputationHistory.related_id,
        )
        .having(func.count(ReputationHistory.id) > 1)
        .subquery()
    )
    return db.session.execute(
        select(ReputationHistory)
        .join(
            groups,
            (ReputationHistory.user_id == groups.c.user_id)
            & (ReputationHistory.action == groups.c.action)
            & (ReputationHistory.related_type == groups.c.related_type)
            & (ReputationHistory.related_id == groups.c.related_id),
        )
        .where(ReputationHistory.id != groups.c.keep_id)
        .order_by(ReputationHistory.id)
    ).scalars().all()


def summarize(rows):
    """{user_id: points to take back} for a list of surplus rows."""
    per_user = defaultdict(int)
    for row in rows:
        per_user[row.user_id] += row.points_change
    return dict(per_user)


def apply_cleanup(rows):
    """Delete the surplus rows and reduce each user's total (floor 0).

    Does not commit; the caller owns the transaction.
    """
    per_user = summarize(rows)
    for user_id, points in per_user.items():
        user = db.session.execute(
            select(User).where(User.id == user_id).with_for_update()
        ).scalar_one_or_none()
        if user:
            user.reputation = max(0, (user.reputation or 0) - points)
            user.update_reputation_level()
    for row in rows:
        db.session.delete(row)
    return per_user
