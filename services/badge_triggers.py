"""
services/badge_triggers.py

Schedules badge evaluation for the events that change badge inputs: post
created, login (streak), connection accepted, thread created / member
joined, helpful marks, reputation level changes.

Why a job and why after commit: evaluating all badges is a few dozen
queries, which should not lengthen the request that triggered it, and it
must only run once the triggering change is durable (otherwise the check
could read the old counters). So the enqueue is registered with
services.after_commit and runs only after a successful COMMIT, and a
failure to enqueue (Redis down) is logged and swallowed -- the next
trigger, or the manual POST /badges/check-all, catches the user up.
"""

from __future__ import annotations

import logging

from services.after_commit import run_after_commit

logger = logging.getLogger(__name__)


def _enqueue(user_id: int) -> None:
    try:
        from services.job_queue import badge_queue
        from services.jobs.badge_jobs import check_badges_job
        from services.jobs.job_specs import BADGE_CHECK_RETRY

        badge_queue.enqueue(check_badges_job, user_id=user_id, retry=BADGE_CHECK_RETRY)
    except Exception as exc:
        logger.warning("could not enqueue badge check for user_id=%s: %s", user_id, exc)


def schedule_badge_check(user_id: int | None) -> None:
    """Queue a badge evaluation for ``user_id`` to run after the current commit."""
    if not user_id:
        return
    run_after_commit(_enqueue, user_id)
