"""
services/jobs/maintenance_jobs.py

RQ job bodies for the two new scheduled maintenance jobs — see
BACKGROUND_JOBS.md §3.2/§3.3 (this repo's shipped doc; the working/
planning doc this comment used to cite, BACKGROUND_JOBS_IMPLEMENTATION.md,
isn't part of this repo). Both are enqueued (never run
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
from models import (
    ActivityFeed, AIConversation, EmailVerificationToken, Notification,
    PasswordResetToken, RefreshToken, StudentProfile, User, UserActivity,
)

logger = logging.getLogger(__name__)

# [DEFAULT -- TUNE LATER once real ActivityFeed row-growth data exists,
# per BACKGROUND_JOBS.md §3.2]. Large enough to be
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
    BACKGROUND_JOBS.md §3.2's "no long-running transactions" rule).
    Each batch gets its own commit.

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


# Retention for authentication leftovers (audit AUD-06 / AUD-20).
# A sign-up whose email was never verified is deleted after this many days, which
# also releases the address for its real owner. Spent or expired one-time tokens and
# refresh tokens are kept this long for debugging, then deleted.
UNVERIFIED_ACCOUNT_RETENTION_DAYS = 7
AUTH_TOKEN_RETENTION_DAYS = 30
AUTH_CLEANUP_BATCH_SIZE = 200
AUTH_CLEANUP_MAX_BATCHES = 50


def purge_stale_auth_data_job() -> dict:
    """
    Delete abandoned sign-ups and expired authentication tokens.

    * Accounts that never verified their email (``email_verified`` false, status
      ``pending_verification``, not linked to Google) and are older than
      UNVERIFIED_ACCOUNT_RETENTION_DAYS. Such an account can own nothing but the
      rows created at registration (profile, welcome notification, activity row,
      tokens), which are removed with it. Each account is deleted inside its own
      savepoint, so one unexpected row cannot block the rest.
    * Password-reset, email-verification and refresh tokens that expired more than
      AUTH_TOKEN_RETENTION_DAYS ago.

    Bounded batches, one commit per batch, idempotent on retry (see the activity
    feed job above for the pattern).
    """
    _SKIPPED_ACCOUNT_IDS.clear()
    now = datetime.datetime.utcnow()
    account_cutoff = now - datetime.timedelta(days=UNVERIFIED_ACCOUNT_RETENTION_DAYS)
    token_cutoff = now - datetime.timedelta(days=AUTH_TOKEN_RETENTION_DAYS)

    accounts_deleted = 0
    accounts_skipped = 0
    batches_run = 0

    while batches_run < AUTH_CLEANUP_MAX_BATCHES:
        stale_ids = [
            row.id for row in
            User.query
            .filter(
                User.email_verified.is_(False),
                User.status == "pending_verification",
                User.google_id.is_(None),
                User.joined_at < account_cutoff,
            )
            .with_entities(User.id)
            .order_by(User.id)
            .limit(AUTH_CLEANUP_BATCH_SIZE)
            .all()
        ]
        stale_ids = [i for i in stale_ids if i not in _SKIPPED_ACCOUNT_IDS]
        if not stale_ids:
            break

        for user_id in stale_ids:
            try:
                with db.session.begin_nested():
                    for model in (PasswordResetToken, EmailVerificationToken, RefreshToken,
                                  Notification, UserActivity, StudentProfile):
                        model.query.filter(model.user_id == user_id).delete(synchronize_session=False)
                    User.query.filter(User.id == user_id).delete(synchronize_session=False)
                accounts_deleted += 1
            except Exception:
                accounts_skipped += 1
                _SKIPPED_ACCOUNT_IDS.add(user_id)
                logger.warning("[AUTH_CLEANUP_SKIPPED] user_id=%s", user_id, exc_info=True)
        db.session.commit()
        batches_run += 1

    tokens_deleted = 0
    for model in (PasswordResetToken, EmailVerificationToken, RefreshToken):
        while True:
            ids = [
                row.id for row in
                model.query.filter(model.expires_at < token_cutoff)
                .with_entities(model.id).limit(AUTH_CLEANUP_BATCH_SIZE * 5).all()
            ]
            if not ids:
                break
            if model is RefreshToken:
                # Successors reference their predecessor through replaced_by_id; clear the
                # links first so the delete cannot trip the self-referencing foreign key.
                RefreshToken.query.filter(RefreshToken.replaced_by_id.in_(ids)).update(
                    {"replaced_by_id": None}, synchronize_session=False)
            model.query.filter(model.id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()
            tokens_deleted += len(ids)

    logger.info(
        "[AUTH_CLEANUP_DONE] accounts_deleted=%s accounts_skipped=%s tokens_deleted=%s",
        accounts_deleted, accounts_skipped, tokens_deleted,
    )
    return {
        "accounts_deleted": accounts_deleted,
        "accounts_skipped": accounts_skipped,
        "tokens_deleted": tokens_deleted,
    }


# Accounts this process could not delete (an unexpected dependent row). Remembered so
# the batch loop above does not pick the same ones again within one run.
_SKIPPED_ACCOUNT_IDS: set = set()


def alert_stale_ai_conversations_job() -> dict:
    """
    Read-only alert job -- counts archived AIConversation rows past a
    staleness threshold and logs the count. Does NOT delete anything.

    Mirrors reconciliation_service.py's own "capacity-gating counters
    are alert-only, never auto-corrected" split, applied here to a
    genuinely new situation of the same shape: a number worth knowing
    about, where auto-acting on it is a product decision this job does
    not make unilaterally.

    Uses idx_ai_conv_archived_last_msg (models.py, AIConversation.__table_args__)
    for the filter -- added alongside this job specifically because
    neither filtered column had an index before this phase. (Audit fix:
    this index was referenced here and in BACKGROUND_JOBS.md §3.3 before
    it actually existed in models.py -- the AIConversation model only had
    idx_ai_conv_user_archived_lastmsg, a user_id-led composite index that
    cannot serve this query via the leftmost-prefix rule since this job
    has no user_id predicate at all. The missing index has now been
    added.)
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
