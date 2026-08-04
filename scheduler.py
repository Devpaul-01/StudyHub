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
     will never double-start. atexit handles clean shutdown.

Install dependency:
    pip install apscheduler

Jobs registered here:
    • weekly_leaderboard_snapshot  – every Sunday 00:05 UTC
    • monthly_leaderboard_snapshot – 1st of every month 00:10 UTC
    • counter_reconciliation       – every Sunday 00:20 UTC

SNAPSHOT LOGIC CONSOLIDATION (Document 1 §6.3):
    The actual snapshot computation used to be duplicated here (as
    _take_snapshot) and, separately, in leaderboard.py's manual
    POST /leaderboard/snapshot admin endpoint — with two slightly
    different implementations. services/leaderboard_service.take_snapshot()
    is now the SOLE implementation (this file's version was kept as the
    "template" since it already had the one-per-day idempotency guard and
    the cleaner two-query department-rank computation). Both this
    scheduler and leaderboard.py's admin route now call the same function.

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

logger = logging.getLogger(__name__)

# Module-level scheduler instance (single shared instance across the app)
scheduler = BackgroundScheduler(
    job_defaults={
        "coalesce":           True,   # merge missed runs into one
        "max_instances":      1,      # never run the same job twice concurrently
        "misfire_grace_time": 3600,   # fire up to 1h late if server was down
    },
    timezone="UTC",
)


# ─────────────────────────────────────────────────────────────────────────────
# JOB WRAPPERS  (APScheduler calls these — they receive the app via closure)
# ─────────────────────────────────────────────────────────────────────────────

def _job_weekly(app):
    logger.info("[Scheduler] ▶ Running weekly leaderboard snapshot job")
    with app.app_context():
        from services.leaderboard_service import take_snapshot
        result = take_snapshot("weekly")
        logger.info(
            "[Scheduler] ✅ weekly snapshot done — created=%s skipped=%s total_ranked=%s",
            result["created"], result["skipped"], result["total_ranked"],
        )


def _job_monthly(app):
    logger.info("[Scheduler] ▶ Running monthly leaderboard snapshot job")
    with app.app_context():
        from services.leaderboard_service import take_snapshot
        result = take_snapshot("monthly")
        logger.info(
            "[Scheduler] ✅ monthly snapshot done — created=%s skipped=%s total_ranked=%s",
            result["created"], result["skipped"], result["total_ranked"],
        )


def _job_reconcile_counters(app):
    logger.info("[Scheduler] ▶ Running denormalized counter reconciliation job")
    with app.app_context():
        from services.reconciliation_service import reconcile_denormalized_counts
        report = reconcile_denormalized_counts()
        logger.info(
            "[Scheduler] ✅ reconciliation done — checked=%s drifts=%s corrected=%s alert_only=%s",
            report.counters_checked, len(report.drifts_found),
            report.corrected_count, report.alerted_only_count,
        )


# ─────────────────────────────────────────────────────────────────────────────
# EVENT LISTENER  (logs job outcomes to your existing logger)
# ─────────────────────────────────────────────────────────────────────────────

def _job_listener(event):
    if event.exception:
        logger.error(
            "[Scheduler] Job '%s' raised an exception: %s",
            event.job_id, event.exception,
        )
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

    Call this ONCE at the end of create_app(), after all extensions and
    blueprints are registered. Safe to call in production and dev alike
    because use_reloader=False prevents double-invocation.

    Schedule (UTC):
        Weekly snapshot   – every Sunday at 00:05
        Monthly snapshot  – 1st of every month at 00:10
        Counter reconciliation – every Sunday at 00:20
    """
    if scheduler.running:
        logger.warning("[Scheduler] Already running — init_scheduler called twice, skipping")
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
