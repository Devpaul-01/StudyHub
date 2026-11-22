"""
services/jobs/badge_jobs.py

Background badge evaluation. Arguments are plain values (never ORM
objects), per services/jobs/__init__.py's rules; the job runs inside the
worker's own Flask app context.
"""

import logging

from extensions import db
from services import badge_service, cache_service
from services.activity_service import record_live_activity

logger = logging.getLogger(__name__)


def check_badges_job(*, user_id: int) -> int:
    """
    Evaluate every active badge for ``user_id`` and award what they have
    earned. Idempotent and safe to run more than once or concurrently:
    already-earned badges are skipped and a concurrent duplicate award is
    absorbed by the UNIQUE(user_id, badge_id) constraint.

    Returns the number of newly awarded badges. Raises on failure so RQ's
    retry policy (job_specs.BADGE_CHECK_RETRY) engages.
    """
    try:
        awarded = badge_service.check_all_badges_for_user(user_id)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("[BADGE_CHECK_FAILED] user_id=%s", user_id)
        raise

    # The progress cache reflects the counters that triggered this check,
    # so refresh it even when nothing new was awarded.
    cache_service.delete(f"sh:1:badge:progress:{user_id}")

    for badge in awarded:
        record_live_activity(
            user_id,
            "badge_earned",
            {"badge_id": badge.id, "badge_name": badge.name},
            scope="global",
        )

    logger.info("[BADGE_CHECK_DONE] user_id=%s awarded=%s", user_id, [b.name for b in awarded])
    return len(awarded)
