"""
services/jobs/maintenance_jobs.py

RQ job bodies for the two new scheduled maintenance jobs
(BACKGROUND_JOBS_IMPLEMENTATION.md §9). Both are enqueued (never run
inline) by scheduler.py's locked scheduler ticks — see that file's
_job_activity_feed_cleanup / _job_stale_conversation_alert wrappers.

cleanup_expired_activity_feed_job:
    ActivityFeed.expires_at promises 24-hour expiry (per that model's
    own docstring), but nothing previously deleted expired rows --
    homework_system.py::get_activity_feed only ever FILTERED them out
    at read time. This job makes that promise true at the storage
    layer. Naturally idempotent (a DELETE-by-expiry-timestamp run
    twice simply matches fewer/zero rows the second time) -- see §7.

alert_stale_ai_conversations_job:
    Read-only. Surfaces AIConversation storage-growth risk (archived
    conversations sitting with an unbounded `messages` JSON blob)
    without unilaterally deciding a retention/deletion policy, mirroring
    reconciliation_service.py's own "alert vs. auto-correct" split for
    capacity-gating counters. Never deletes anything.

Both follow services/jobs/__init__.py's rules: plain-value arguments
(neither takes any -- both scope their own work), fresh queries inside
the function body, one commit per batch (not per row), no reliance on
Flask request/session/g.
"""

import datetime
import logging

from sqlalchemy import func

from extensions import db
from models import ActivityFeed, AIConversation

logger = logging.getLogger(__name__)

# [DEFAULT -- TUNE LATER once real ActivityFeed row-growth data exists,
# per BACKGROUND_JOBS_IMPLEMENTATION.md §9.1]. Large enough to be
# efficient, small enough that one batch's transaction is never a
# problem for a simple indexed-column DELETE.
ACTIVITY_FEED_BATCH_SIZE = 5000

# Safety valve: 200 batches * 5000 = 1,000,000 rows per single job run.
# If more than this is pending, stop and let the NEXT scheduled run
# continue, rather than one job run monopolizing a worker slot
# indefinitely (redundant with, and a graceful complement to,
# MAINTENANCE_JOB_TIMEOUT_SECONDS in services/job_queue.py).
ACTIVITY_FEED_MAX_BATCHES_PER_RUN = 200

# [DEFAULT -- TUNE LATER, per §9.2]. Not derived from any stated
# retention policy in the codebase -- flagged as a number to confirm or
# change, not a discovered requirement.
STALE_CONVERSATION_THRESHOLD_DAYS = 180


def cleanup_expired_activity_feed_job() -> dict:
    """
    Deletes ActivityFeed rows past their expiry, in bounded batches so
    no single DELETE (and no single transaction) scales with total
    table size.

    Selects a bounded batch of expired row IDs, THEN deletes by ID --
    not a single unbounded DELETE ... WHERE expires_at < :cutoff, which
    on a very large table would be one giant transaction (see
    BACKGROUND_JOBS_IMPLEMENTATION.md §8's "no long-running
    transactions" rule). Each batch gets its own commit.

    Returns a summary dict (rows_deleted, batches_run) -- RQ persists a
    job's return value on its own Job record, which doubles as
    observability without any extra logging-only mechanism.

    Idempotent on retry: re-running after a partial failure (or a
    duplicate enqueue) simply finds fewer or zero expired rows on its
    next batch-select and is a correctness no-op.
    """
    total_deleted = 0
    batches_run = 0
    cutoff = datetime.datetime.utcnow()

    while True:
        batch_ids = [
            row.id for row in
            ActivityFeed.query
            .filter(ActivityFeed.expires_at < cutoff)
            .with_entities(ActivityFeed.id)
            .limit(ACTIVITY_FEED_BATCH_SIZE)
            .all()
        ]
        if not batch_ids:
            break

        ActivityFeed.query.filter(
            ActivityFeed.id.in_(batch_ids)
        ).delete(synchronize_session=False)
        db.session.commit()  # one commit per batch, not per row

        total_deleted += len(batch_ids)
        batches_run += 1

        if batches_run >= ACTIVITY_FEED_MAX_BATCHES_PER_RUN:
            logger.warning(
                "[ACTIVITY_FEED_CLEANUP_BATCH_LIMIT] "
                "stopped after %s batches (%s rows) — resuming next scheduled run",
                batches_run, total_deleted,
            )
            break

    logger.info(
        "[ACTIVITY_FEED_CLEANUP_DONE] rows_deleted=%s batches_run=%s",
        total_deleted, batches_run,
    )
    return {"rows_deleted": total_deleted, "batches_run": batches_run}


def alert_stale_ai_conversations_job() -> dict:
    """
    Read-only alert job -- counts archived AIConversation rows past a
    staleness threshold and logs the count. Does NOT delete anything.

    Mirrors reconciliation_service.py's own "capacity-gating counters
    are alert-only, never auto-corrected" split, applied here to a
    genuinely new situation of the same shape: a number worth knowing
    about, where auto-acting on it is a product decision this job does
    not make unilaterally.

    Uses idx_ai_conv_archived_last_msg (models.py) for the filter --
    added alongside this job specifically because neither filtered
    column had an index before this phase.
    """
    threshold = datetime.datetime.utcnow() - datetime.timedelta(
        days=STALE_CONVERSATION_THRESHOLD_DAYS
    )

    stale_count = (
        db.session.query(func.count(AIConversation.id))
        .filter(
            AIConversation.is_archived.is_(True),
            AIConversation.last_message_at < threshold,
        )
        .scalar()
    ) or 0

    # Rough storage-signal proxy: total_messages summed across stale
    # rows, since that's already tracked per-conversation and
    # correlates with the actual JSON blob size without computing real
    # byte sizes in SQL.
    stale_message_total = (
        db.session.query(func.coalesce(func.sum(AIConversation.total_messages), 0))
        .filter(
            AIConversation.is_archived.is_(True),
            AIConversation.last_message_at < threshold,
        )
        .scalar()
    ) or 0

    logger.info(
        "[STALE_AI_CONVERSATION_ALERT] stale_archived_conversations=%s "
        "approx_total_messages=%s threshold_days=%s",
        stale_count, stale_message_total, STALE_CONVERSATION_THRESHOLD_DAYS,
    )
    return {
        "stale_count": stale_count,
        "approx_total_messages": stale_message_total,
    }
