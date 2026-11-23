"""
services/champion_service.py

Weekly Champions writer (audit F-15, product decision Q-7: build it).

The reader (GET /homework/champions, leaderboard badge) existed but nothing
ever wrote a WeeklyChampion row, so the panel was permanently empty.

``compute_weekly_champions(week_start)`` computes the champions for ONE
finished week (Monday..Sunday, UTC) from completed + helpful submissions and
upserts them. It is idempotent: running it twice (or re-running after late
feedback) converges on exactly the same rows, enforced by the unique index
``uq_champion_week_type_subject`` on (week_start, champion_type, subject).

Champion types
  most_helpful_overall  most helpful completions in the week (any subject)
  subject_champion      most helpful completions per subject
  fastest_helper        lowest average response time (>= MIN_HELPS_FOR_FASTEST helps)

Ties are broken deterministically (more helps, then faster, then lower id),
so a re-run never flips a winner.

"Helpful" is the F-06 definition (``is_marked_helpful``), which is why that
fix had to land first - otherwise every completion would count.

Per the layering rule: no Flask imports. Commits once (it is a job entry
point, like take_snapshot), then sends best-effort notifications for newly
created rows only.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import func

from extensions import db
from models import HomeworkSubmission, User, WeeklyChampion

logger = logging.getLogger(__name__)

MOST_HELPFUL = "most_helpful_overall"
SUBJECT_CHAMPION = "subject_champion"
FASTEST_HELPER = "fastest_helper"

MIN_HELPS_FOR_FASTEST = 2


def week_start_for(day: datetime.date) -> datetime.date:
    """Monday of the week containing ``day`` (UTC calendar)."""
    return day - datetime.timedelta(days=day.weekday())


def previous_week_start(today: datetime.date | None = None) -> datetime.date:
    today = today or datetime.datetime.utcnow().date()
    return week_start_for(today) - datetime.timedelta(days=7)


def _week_bounds(week_start: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    start = datetime.datetime.combine(week_start, datetime.time.min)
    return start, start + datetime.timedelta(days=7)


def _winners(week_start: datetime.date) -> dict[tuple[str, str], dict]:
    """{(champion_type, subject_key): {user_id, help_count, avg_seconds}}"""
    start, end = _week_bounds(week_start)
    base = [
        HomeworkSubmission.status == "completed",
        HomeworkSubmission.is_marked_helpful.is_(True),
        HomeworkSubmission.feedback_at >= start,
        HomeworkSubmission.feedback_at < end,
        User.status == "approved",
    ]
    avg_rt = func.avg(
        db.case((HomeworkSubmission.response_time_seconds > 0, HomeworkSubmission.response_time_seconds))
    )

    def _pick(rows, *, key, reverse_count=True):
        # rows: (helper_id, count, avg_seconds[, subject])
        best = {}
        for row in rows:
            helper_id, count, avg = int(row[0]), int(row[1]), (float(row[2]) if row[2] is not None else None)
            k = key(row)
            cand = {"user_id": helper_id, "help_count": count, "avg_seconds": avg}
            cur = best.get(k)
            if cur is None or _rank(cand) < _rank(cur):
                best[k] = cand
        return best

    def _rank(c):
        # lower tuple wins: more helps, then faster (None = slowest), then lower id
        return (-c["help_count"], c["avg_seconds"] if c["avg_seconds"] is not None else float("inf"), c["user_id"])

    overall_rows = (
        db.session.query(HomeworkSubmission.helper_id, func.count(HomeworkSubmission.id), avg_rt)
        .join(User, User.id == HomeworkSubmission.helper_id)
        .filter(*base)
        .group_by(HomeworkSubmission.helper_id)
        .all()
    )
    subject_rows = (
        db.session.query(
            HomeworkSubmission.helper_id, func.count(HomeworkSubmission.id), avg_rt,
            HomeworkSubmission.subject,
        )
        .join(User, User.id == HomeworkSubmission.helper_id)
        .filter(*base, HomeworkSubmission.subject.isnot(None), HomeworkSubmission.subject != "")
        .group_by(HomeworkSubmission.helper_id, HomeworkSubmission.subject)
        .all()
    )

    out: dict[tuple[str, str], dict] = {}

    overall = _pick(overall_rows, key=lambda r: "")
    if "" in overall:
        out[(MOST_HELPFUL, "")] = overall[""]

    for subject, cand in _pick(subject_rows, key=lambda r: r[3]).items():
        out[(SUBJECT_CHAMPION, subject)] = cand

    fastest_pool = [r for r in overall_rows if r[2] is not None and int(r[1]) >= MIN_HELPS_FOR_FASTEST]
    fastest = None
    for row in fastest_pool:
        cand = {"user_id": int(row[0]), "help_count": int(row[1]), "avg_seconds": float(row[2])}
        if fastest is None or (cand["avg_seconds"], -cand["help_count"], cand["user_id"]) < (
            fastest["avg_seconds"], -fastest["help_count"], fastest["user_id"]
        ):
            fastest = cand
    if fastest:
        out[(FASTEST_HELPER, "")] = fastest

    return out


def compute_weekly_champions(week_start: datetime.date) -> dict:
    """Compute and upsert champions for the week starting ``week_start``
    (must be a Monday). Returns {"created", "updated", "removed", "total"}."""
    if week_start.weekday() != 0:
        raise ValueError("week_start must be a Monday")

    week_end = week_start + datetime.timedelta(days=6)
    winners = _winners(week_start)

    existing = {
        (row.champion_type, row.subject or ""): row
        for row in WeeklyChampion.query.filter(WeeklyChampion.week_start == week_start).all()
    }

    created, updated, removed = [], 0, 0
    now = datetime.datetime.utcnow()

    for key, win in winners.items():
        champion_type, subject = key
        avg_minutes = (
            str(round(win["avg_seconds"] / 60, 1)) if win["avg_seconds"] is not None else None
        )
        row = existing.pop(key, None)
        if row is None:
            row = WeeklyChampion(
                user_id=win["user_id"], subject=subject, help_count=win["help_count"],
                week_start=week_start, week_end=week_end,
                avg_response_time_minutes=avg_minutes,
                champion_type=champion_type, created_at=now,
            )
            db.session.add(row)
            created.append(row)
        elif (row.user_id, row.help_count, row.avg_response_time_minutes) != (
            win["user_id"], win["help_count"], avg_minutes
        ):
            row.user_id = win["user_id"]
            row.help_count = win["help_count"]
            row.avg_response_time_minutes = avg_minutes
            updated += 1

    # rows that no longer have a winner (e.g. feedback was later withdrawn)
    for stale in existing.values():
        db.session.delete(stale)
        removed += 1

    db.session.commit()

    for row in created:
        _notify_champion(row)

    result = {"created": len(created), "updated": updated, "removed": removed, "total": len(winners)}
    logger.info("weekly champions for %s: %s", week_start, result)
    return result


def _notify_champion(row: WeeklyChampion) -> None:
    """Best-effort push; a failure must never undo or fail the computation."""
    try:
        from services.push_notifications import notify_became_champion

        user = User.query.get(row.user_id)
        if user:
            notify_became_champion(user, row.champion_type, row.subject or None)
    except Exception:
        logger.warning("champion notification failed for user %s", row.user_id, exc_info=True)
