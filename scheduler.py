"""
StudyHub – Background Scheduler
════════════════════════════════════════════════════════════════════════════════

Uses APScheduler's BackgroundScheduler.

⚠️  THREADING COMPATIBILITY NOTE:
     This app runs socketio with async_mode='threading' (see
     services/websocket_messages.py::init_app) — BackgroundScheduler's
     ThreadPoolExecutor runs on ordinary OS threads under this mode, which
     is safe and intentional. (Corrected from a prior "eventlet" docstring
     that no longer matched the actual async_mode in use — Document 1 §5's
     file-by-file note flagged this exact staleness.)

     use_reloader=False is already set in socketio.run(), so the scheduler
     will never double-start within one process. atexit handles clean
     shutdown.

HORIZONTAL SCALING (see 01-DESIGN-horizontal-scaling.md §7):
     The above ("never double-start") only ever protected against
     duplicate registration WITHIN one process. It said nothing about N
     separate processes each independently running this exact same
     module — every one of them would call init_scheduler(), each pass
     its own `if scheduler.running` guard (a fresh, empty scheduler in
     each process), and register the identical three cron jobs on
     identical schedules. On any given tick, every scheduler-enabled
     instance would fire simultaneously — guaranteed duplicate execution,
     not a hypothetical race.

     Fixed with a Redis distributed lock (services/distributed_lock.py)
     wrapped around each job's BODY, not around registration —
     registration stays exactly as it was (every instance still
     registers all three jobs, on the same schedule, unchanged); what
     changed is that only the instance that wins the lock on a given
     tick actually executes the job function. See _run_locked below and
     the design doc §7.1-§7.4 for the full reasoning, including why the
     lock fails CLOSED (skip this cycle) rather than this app's usual
     Redis-down fail-open default — a scheduler job failing to run once
     is far cheaper than two processes both writing duplicate
     leaderboard-snapshot rows.

     Cron schedules, timezones, job_defaults, and the "Keep -w 1" operational
     constraint that USED to be required to avoid this exact problem (see
     app.py's Production Entry Point comment) are otherwise UNCHANGED by
     this refactor except that the "-w 1" constraint is now obsolete —
     SCHEDULER_ENABLED=true is now safe on every instance simultaneously.

Install dependency:
    pip install apscheduler

Jobs registered here:
    • weekly_leaderboard_snapshot  – every Sunday 00:05 UTC
    • monthly_leaderboard_snapshot – 1st of every month 00:10 UTC
    • counter_reconciliation       – every Sunday 00:20 UTC
    • activity_feed_cleanup        – daily 03:00 UTC
    • stale_ai_conversation_alert  – every Sunday 00:25 UTC

BACKGROUND JOBS PHASE (BACKGROUND_JOBS_IMPLEMENTATION.md §5):
    The two newest jobs above (activity_feed_cleanup,
    stale_ai_conversation_alert) differ structurally from the three
    original jobs in one specific way: their locked scheduler tick
    ENQUEUES the work onto services/job_queue.py's maintenance_queue
    (run later by worker.py) rather than executing it inline, the way
    the three original jobs still do. This split follows a concrete
    rule, not an arbitrary choice: the three original jobs each already
    have their own idempotency guard independent of the distributed
    lock (take_snapshot()'s one-per-day DB check; reconcile's natural
    recompute-and-compare) AND bounded runtime regardless of table size
    — so running them inline, inside the lock, adds nothing. The two
    new jobs' cost scales with table size (activity_feed_cleanup) or
    have no independent idempotency guard of their own — so per §5's
    rule, the scheduler tick's job is only to acquire the lock and
    enqueue; the RQ job itself is the idempotent, retryable unit. See
    services/jobs/maintenance_jobs.py for the actual job bodies.

SNAPSHOT LOGIC CONSOLIDATION (Document 1 §6.3):
    The actual snapshot computation used to be duplicated here (as
    _take_snapshot) and, separately, in leaderboard.py's manual
    POST /leaderboard/snapshot admin endpoint — with two slightly
    different implementations. services/leaderboard_service.take_snapshot()
    is now the SOLE implementation (this file's version was kept as the
    "template" since it already had the one-per-day idempotency guard and
    the cleaner two-query department-rank computation). Both this
    scheduler and leaderboard.py's admin route now call the same function.

    HORIZONTAL SCALING NOTE: that DB-level "one-per-day" guard alone is
    NOT sufficient at more than one process — it has a real TOCTOU race
    (no unique constraint backs it; see design doc §5 for the exact gap
    read directly out of leaderboard_service.py). The distributed lock
    below is what actually closes it; the DB guard remains valuable
    defense-in-depth for the case where the lock itself was contended.

COUNTER RECONCILIATION (Document 4 §3.3 point 2, Phase 5a):
    services/reconciliation_service.py::reconcile_denormalized_counts() is
    a safety-net job, not a primary consistency mechanism, so it runs at
    the same low weekly frequency as the snapshot jobs rather than more
    often. Scheduled 15 minutes after the monthly snapshot slot (00:20 vs
    00:05/00:10) purely so the three jobs don't contend for DB load in the
    same window on the Sundays when weekly snapshot + reconciliation both
    fire — there's no other ordering dependency between them.
"""

import atexit
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from services.distributed_lock import DistributedLock

logger = logging.getLogger(__name__)

# Module-level scheduler instance (single shared instance across the app)
scheduler = BackgroundScheduler(
    job_defaults={
        "coalesce":           True,   # merge missed runs into one
        "max_instances":      1,      # never run the same job twice concurrently WITHIN one process
        "misfire_grace_time": 3600,   # fire up to 1h late if server was down
    },
    timezone="UTC",
)

# Lock TTL per job — generous relative to realistic runtime (both job
# bodies are, at worst, a handful of aggregate queries plus a bulk
# insert/update over "approved users" or "posts+threads", not an
# unbounded operation), so a slow-but-still-running job is never
# mistaken for a crashed one and preempted mid-run. If Redis is down when
# a tick fires, acquire() fails closed (see distributed_lock.py) and the
# job is skipped for that tick — the next scheduled tick tries again.
_JOB_LOCK_TTL_SECONDS = 600  # 10 minutes


_CONSECUTIVE_SKIP_ALERT_THRESHOLD = 5  # ~5 ticks of the SAME job with no execution


def _run_locked(job_id: str, work_fn) -> None:
    """
    Shared wrapper: attempt the distributed lock for job_id, run work_fn()
    only if acquired, always release in a finally. Centralizes the
    lock-around-a-job pattern so all three job wrappers below stay
    one-line calls into this, rather than three near-duplicate
    try/finally blocks that could drift out of sync with each other.

    Logs exactly what the design doc's §23 observability requirement asks
    for: lock acquired, lock skipped (and by implication which instance
    is the current owner, via distributed_lock.py's own logging), and
    does NOT log anything for the common no-op "nothing to do" case beyond
    what work_fn() itself already logs — no added heartbeat-style noise.

    Also tracks CONSECUTIVE skips of the same job_id (SENTRY_IMPLEMENTATION_PLAN.md
    §5): a single skip is normal (another instance legitimately won the
    lock this tick), but if Redis is down for an extended period every
    tick skips silently forever with no signal beyond a warning log line
    each time. After _CONSECUTIVE_SKIP_ALERT_THRESHOLD consecutive skips,
    this escalates to logger.error + a Sentry message, then keeps
    counting without re-alerting until the job successfully runs again
    and resets the counter (avoids alert spam from a Redis-down window
    that lasts hours). Uses cache_service.get/set directly (fail-open,
    matching every other Redis consumer in this codebase) — a Redis
    hiccup on THIS bookkeeping must never itself break the scheduler skip
    path.
    """
    from services import cache_service  # local import, matches this module's existing style

    lock_key = f"sh:1:sched:lock:{job_id}"
    skip_counter_key = f"sh:1:sched:skipcount:{job_id}"

    with DistributedLock(lock_key, ttl_seconds=_JOB_LOCK_TTL_SECONDS) as lock:
        if not lock.acquired:
            logger.info(
                "[SCHED_JOB_SKIPPED_LOCK_HELD] job_id=%s — another instance "
                "owns this tick's execution", job_id,
            )
            # Fail-open read/write, matching cache_service.py's own
            # convention — a Redis hiccup on THIS bookkeeping must never
            # itself break the scheduler skip path.
            skip_count = (cache_service.get(skip_counter_key) or 0) + 1
            cache_service.set(skip_counter_key, skip_count, ttl_seconds=86400)
            if skip_count >= _CONSECUTIVE_SKIP_ALERT_THRESHOLD:
                logger.error(
                    "[SCHED_JOB_REPEATEDLY_SKIPPED] job_id=%s skip_count=%s — "
                    "this instance has failed to acquire the lock %s times in a "
                    "row; if no other instance is genuinely running this job, "
                    "Redis or the lock itself may be stuck",
                    job_id, skip_count, skip_count,
                )
                try:
                    import sentry_sdk
                    sentry_sdk.capture_message(
                        f"Scheduler job '{job_id}' skipped {skip_count} consecutive ticks",
                        level="error",
                    )
                except Exception:
                    pass
            return
        # Lock acquired — reset the skip counter and run.
        cache_service.set(skip_counter_key, 0, ttl_seconds=86400)
        work_fn()


# ─────────────────────────────────────────────────────────────────────────────
# JOB WRAPPERS  (APScheduler calls these — they receive the app via closure)
# ─────────────────────────────────────────────────────────────────────────────

def _job_weekly(app):
    """
    APScheduler calls this directly on EVERY scheduler-enabled instance at
    the scheduled tick (registration is unchanged — see init_scheduler).
    _run_locked is what ensures only the instance that wins the Redis
    lock for this tick actually executes take_snapshot(); every other
    instance's call returns immediately after logging the skip.
    """
    def _work():
        logger.info("[Scheduler] ▶ Running weekly leaderboard snapshot job")
        with app.app_context():
            from services.leaderboard_service import take_snapshot
            result = take_snapshot("weekly")
            logger.info(
                "[Scheduler] ✅ weekly snapshot done — created=%s skipped=%s total_ranked=%s",
                result["created"], result["skipped"], result["total_ranked"],
            )

    _run_locked("weekly_leaderboard_snapshot", _work)


def _job_monthly(app):
    """See _job_weekly's docstring — identical locking rationale."""
    def _work():
        logger.info("[Scheduler] ▶ Running monthly leaderboard snapshot job")
        with app.app_context():
            from services.leaderboard_service import take_snapshot
            result = take_snapshot("monthly")
            logger.info(
                "[Scheduler] ✅ monthly snapshot done — created=%s skipped=%s total_ranked=%s",
                result["created"], result["skipped"], result["total_ranked"],
            )

    _run_locked("monthly_leaderboard_snapshot", _work)


def _job_reconcile_counters(app):
    """
    See _job_weekly's docstring for the general locking rationale. This
    job's underlying work is naturally idempotent on its own (recompute +
    UPDATE-if-different — see reconciliation_service.py), so the lock
    here isn't closing a correctness gap the way it is for the snapshot
    jobs; it avoids two instances redundantly running the same full-table
    scan concurrently. See design doc §7.2.
    """
    def _work():
        logger.info("[Scheduler] ▶ Running denormalized counter reconciliation job")
        with app.app_context():
            from services.reconciliation_service import reconcile_denormalized_counts
            report = reconcile_denormalized_counts()
            logger.info(
                "[Scheduler] ✅ reconciliation done — checked=%s drifts=%s corrected=%s alert_only=%s",
                report.counters_checked, len(report.drifts_found),
                report.corrected_count, report.alerted_only_count,
            )

    _run_locked("counter_reconciliation", _work)


# ─────────────────────────────────────────────────────────────────────────────
# NEW JOB WRAPPERS — background-jobs phase (enqueue, don't execute inline)
#
# See this file's module docstring's "BACKGROUND JOBS PHASE" note above
# for why these two wrappers enqueue onto maintenance_queue instead of
# calling their job function directly inside _work(), unlike the three
# wrappers above them.
# ─────────────────────────────────────────────────────────────────────────────

def _job_activity_feed_cleanup(app):
    """
    Enqueues (does not execute inline) — see this module's docstring
    for why this job, unlike the three above it, is enqueued rather
    than run directly inside the locked scheduler tick.
    """
    def _work():
        logger.info("[Scheduler] ▶ Enqueueing activity feed cleanup job")
        with app.app_context():
            from services.job_queue import maintenance_queue
            from services.jobs.maintenance_jobs import cleanup_expired_activity_feed_job
            from services.jobs.job_specs import ACTIVITY_FEED_CLEANUP_RETRY
            job = maintenance_queue.enqueue(
                cleanup_expired_activity_feed_job,
                retry=ACTIVITY_FEED_CLEANUP_RETRY,
            )
            logger.info(
                "[SCHED_JOB_ENQUEUED] job_id=%s queue=%s", job.id, maintenance_queue.name
            )

    _run_locked("activity_feed_cleanup", _work)


def _job_stale_conversation_alert(app):
    """See _job_activity_feed_cleanup's docstring — identical shape."""
    def _work():
        logger.info("[Scheduler] ▶ Enqueueing stale AI conversation alert job")
        with app.app_context():
            from services.job_queue import maintenance_queue
            from services.jobs.maintenance_jobs import alert_stale_ai_conversations_job
            from services.jobs.job_specs import STALE_CONVERSATION_ALERT_RETRY
            job = maintenance_queue.enqueue(
                alert_stale_ai_conversations_job,
                retry=STALE_CONVERSATION_ALERT_RETRY,
            )
            logger.info(
                "[SCHED_JOB_ENQUEUED] job_id=%s queue=%s", job.id, maintenance_queue.name
            )

    _run_locked("stale_ai_conversation_alert", _work)


# ─────────────────────────────────────────────────────────────────────────────
# EVENT LISTENER  (logs job outcomes to your existing logger)
# ─────────────────────────────────────────────────────────────────────────────

def _job_listener(event):
    if event.exception:
        logger.error(
            "[Scheduler] Job '%s' raised an exception: %s",
            event.job_id, event.exception,
        )
        try:
            import sentry_sdk
            sentry_sdk.set_tag("scheduler_job_id", event.job_id)
            sentry_sdk.capture_exception(event.exception)
        except Exception:
            pass
    else:
        logger.info(
            "[Scheduler] Job '%s' completed successfully",
            event.job_id,
        )


# ─────────────────────────────────────────────────────────────────────────────
# INIT  (called from create_app())
# ─────────────────────────────────────────────────────────────────────────────

def init_scheduler(app) -> None:
    """
    Wire APScheduler into the Flask app.

    Call this ONCE PER PROCESS at the end of create_app(), after all
    extensions and blueprints are registered. Safe to call in production
    and dev alike because use_reloader=False prevents double-invocation
    WITHIN a single process.

    HORIZONTAL SCALING: this function is expected to run on every
    scheduler-enabled instance — that is now safe (see module docstring
    §7). Registration itself is unchanged and intentionally NOT
    deduplicated across processes; every instance registers the same
    three jobs on the same schedule. What makes concurrent multi-instance
    registration safe is the Redis lock inside each job body
    (_run_locked), not anything in this function.

    Schedule (UTC):
        Weekly snapshot   – every Sunday at 00:05
        Monthly snapshot  – 1st of every month at 00:10
        Counter reconciliation – every Sunday at 00:20
    """
    if scheduler.running:
        # This guard is PROCESS-LOCAL ONLY — it does not, and is not meant
        # to, prevent a DIFFERENT process from also calling init_scheduler.
        # See module docstring for the mechanism that actually handles that.
        logger.warning("[Scheduler] Already running — init_scheduler called twice in this process, skipping")
        return

    # Register event listener
    scheduler.add_listener(_job_listener, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)

    # ── Weekly job: every Sunday at 00:05 UTC ─────────────────────────────────
    scheduler.add_job(
        func=_job_weekly,
        args=[app],
        trigger=CronTrigger(day_of_week="sun", hour=0, minute=5, timezone="UTC"),
        id="weekly_leaderboard_snapshot",
        name="Weekly Leaderboard Snapshot",
        replace_existing=True,
    )

    # ── Monthly job: 1st of month at 00:10 UTC ────────────────────────────────
    scheduler.add_job(
        func=_job_monthly,
        args=[app],
        trigger=CronTrigger(day=1, hour=0, minute=10, timezone="UTC"),
        id="monthly_leaderboard_snapshot",
        name="Monthly Leaderboard Snapshot",
        replace_existing=True,
    )

    # ── Counter reconciliation: every Sunday at 00:20 UTC ─────────────────────
    # Safety-net job (Document 4 §3.3 point 2) — runs weekly, offset 15
    # minutes after the other Sunday job so they don't contend for DB load
    # in the same window.
    scheduler.add_job(
        func=_job_reconcile_counters,
        args=[app],
        trigger=CronTrigger(day_of_week="sun", hour=0, minute=20, timezone="UTC"),
        id="counter_reconciliation",
        name="Denormalized Counter Reconciliation",
        replace_existing=True,
    )

    # ── Activity feed cleanup: daily at 03:00 UTC ─────────────────────────────
    # Background-jobs phase addition. Enqueues onto maintenance_queue
    # rather than running inline — see this module's docstring.
    scheduler.add_job(
        func=_job_activity_feed_cleanup,
        args=[app],
        trigger=CronTrigger(hour=3, minute=0, timezone="UTC"),
        id="activity_feed_cleanup",
        name="Activity Feed Cleanup",
        replace_existing=True,
    )

    # ── Stale AI conversation alert: every Sunday at 00:25 UTC ────────────────
    # Background-jobs phase addition. Placed 5 minutes after
    # counter_reconciliation's 00:20 slot, for the same "don't contend
    # for DB load in the same window" reasoning already used to space
    # the original three jobs apart. Enqueues, does not run inline.
    scheduler.add_job(
        func=_job_stale_conversation_alert,
        args=[app],
        trigger=CronTrigger(day_of_week="sun", hour=0, minute=25, timezone="UTC"),
        id="stale_ai_conversation_alert",
        name="Stale AI Conversation Alert",
        replace_existing=True,
    )

    scheduler.start()

    # Graceful shutdown when the Python process exits (Gunicorn SIGTERM, etc.)
    atexit.register(lambda: _shutdown_scheduler())

    # Log next run times for confirmation
    for job in scheduler.get_jobs():
        logger.info(
            "[Scheduler] ✅ Registered '%s' — next run: %s",
            job.name,
            job.next_run_time,
        )

    app.logger.info("[Scheduler] APScheduler started with %d job(s)", len(scheduler.get_jobs()))


def _shutdown_scheduler() -> None:
    """Gracefully stop the scheduler on process exit."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Shut down cleanly")
