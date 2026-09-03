"""
worker.py

StudyHub RQ worker entry point. Run as its own process, separate from
the gunicorn-served app.py — see
BACKGROUND_JOBS_IMPLEMENTATION.md §11 for exactly how many of these to
run and how they relate to API instance count (answer: independently —
they don't need to match).

Usage:
    python worker.py

Each worker process handles ONE job at a time (RQ's simple worker is
single-threaded per process, deliberately — see §11 for why this is
the right default here and how to scale via process count instead of
in-process concurrency).

CRITICAL — required environment for this process:
  - SCHEDULER_ENABLED must be set to "false". A worker process has no
    business also running APScheduler — see main()'s own comment below
    for the full reasoning.
  - Every MAIL_* setting config.py reads (MAIL_SERVER, MAIL_USERNAME,
    MAIL_PASSWORD, MAIL_DEFAULT_SENDER, ...) must be present in this
    process's environment, exactly as they already are for the API
    process — email jobs run here, on the worker, not on the API
    instance that enqueued them.
  - REDIS_URL, DATABASE_NEW_URL, SECRET_KEY — same requirement as any
    other process running this codebase, already enforced by
    config.py's own ValueError-on-import checks.
"""

import logging
import os
import signal
import sys

from rq import Worker

from app import create_app
from extensions import redis_client
from services.job_queue import email_queue, maintenance_queue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _install_signal_handlers(worker: Worker) -> None:
    """
    RQ's Worker.work() already installs its own SIGINT/SIGTERM handlers
    internally (warm shutdown on first signal, cold/immediate on a
    second) — this function exists only to log the signal at this
    codebase's own established log-tag convention before RQ's handler
    takes over, matching the observability style already used
    throughout scheduler.py/websocket_messages.py (a visible log line
    at the moment of a lifecycle transition, not a silent one).
    """
    def _log_signal(signum, _frame):
        name = signal.Signals(signum).name
        logger.info("[WORKER_SIGNAL_RECEIVED] signal=%s — beginning graceful shutdown", name)

    signal.signal(signal.SIGINT, _log_signal)
    signal.signal(signal.SIGTERM, _log_signal)


def main() -> int:
    # Reuses the SAME create_app() factory app.py uses — this worker
    # process needs a Flask app context for the exact same reason RQ
    # job bodies in email_jobs.py need current_app.config (MAIL_*
    # settings) and db.session (in maintenance_jobs.py).
    if os.environ.get("SCHEDULER_ENABLED", "true").lower() == "true":
        # A worker process must never also run the APScheduler-based
        # scheduler (scheduler.py) — doing so would silently create an
        # additional scheduler-lock-race participant beyond whatever API
        # instances already have it enabled, multiplying lock contention
        # for zero benefit. Fail loudly rather than silently letting this
        # slip through — BACKGROUND_JOBS_IMPLEMENTATION.md §24 flags this
        # as the single highest operational risk in this phase.
        logger.error(
            "[WORKER_STARTUP_ABORTED] SCHEDULER_ENABLED must be 'false' for "
            "the worker process — refusing to start with the scheduler "
            "enabled to avoid an unintended extra scheduler-lock-race "
            "participant. Set SCHEDULER_ENABLED=false in this process's "
            "environment."
        )
        return 1

    app, _socketio = create_app()

    with app.app_context():
        queues = [email_queue, maintenance_queue]
        worker = Worker(queues, connection=redis_client)
        _install_signal_handlers(worker)

        logger.info(
            "[WORKER_STARTED] queues=%s pid=%s",
            [q.name for q in queues], os.getpid(),
        )
        try:
            worker.work(with_scheduler=True)
        except Exception:
            logger.error("[WORKER_CRASHED]", exc_info=True)
            raise
        finally:
            logger.info("[WORKER_STOPPED] pid=%s", os.getpid())

    return 0


if __name__ == "__main__":
    sys.exit(main())
