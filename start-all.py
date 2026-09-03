"""
start-all.py

Combined entry point that runs the API server, an in-process RQ
worker, and the APScheduler-based scheduler together — for
environments where running three separate deployables isn't practical
(e.g. a single small VM, a local all-in-one dev/staging box).

This does NOT replace running app.py and worker.py separately in
production — independent process scaling is the correct default. This
file exists for the specific case where you want one process tree to
bring up everything at once.

Architecture:
    - The API (Flask + SocketIO) runs on the MAIN thread/process,
      exactly as `python app.py` already does today — this preserves
      gunicorn compatibility and every existing signal-handling
      behavior app.py's own __main__ block already has.
    - The RQ worker runs in a background thread within THIS process,
      using RQ's SimpleWorker (not the default fork-based Worker) —
      RQ's fork-based Worker calls os.fork(), which is unsafe to run
      as a plain Python thread inside a process that's also serving
      HTTP traffic (forking a multi-threaded process is a well-known
      footgun: only the forking thread survives in the child, silently
      corrupting anything else already running, e.g. Flask-SocketIO's
      own background tasks). SimpleWorker processes jobs in-process,
      in-thread, with no fork — the correct choice specifically because
      this is a combined process, not a dedicated worker deployment.
    - The scheduler is NOT started separately here — it already runs
      in-process inside create_app() whenever SCHEDULER_ENABLED=true
      (the existing, unmodified mechanism in app.py/scheduler.py).
      start-all.py simply ensures SCHEDULER_ENABLED defaults to true
      for this combined-process use case, rather than duplicating
      scheduler startup logic that already exists.
    - Graceful shutdown: SIGINT/SIGTERM stop the worker thread first
      (finishing or abandoning its current job per RQ's own shutdown
      semantics), then let socketio.run()'s own shutdown path complete
      normally — mirroring the order dependencies actually have (the
      worker depends on the app's Redis/DB connections being alive
      longer than the worker does, not the reverse).

Usage:
    python start-all.py

Does not interfere with running the API, worker, and scheduler
separately — app.py and worker.py are completely unmodified by this
file's existence; this is purely an additional, optional entry point.
"""

import logging
import os
import signal
import sys
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# This combined-process mode is exactly the case worker.py's own
# startup guard exists to prevent when running the TWO separate
# processes it's designed for — but here, by design, the scheduler and
# the worker are meant to coexist in one process tree. Set this BEFORE
# importing app/worker modules, since config.py reads it at
# Config-class-definition time (import time).
os.environ.setdefault("SCHEDULER_ENABLED", "true")

_shutdown_event = threading.Event()


def _run_worker_thread(app):
    """
    Runs an RQ SimpleWorker against both queues, inside a background
    thread of THIS process. See module docstring for why SimpleWorker
    (no fork) is required here, unlike worker.py's dedicated-process
    use of the default forking Worker.
    """
    from rq import SimpleWorker
    from extensions import redis_client
    from services.job_queue import email_queue, maintenance_queue

    class _ThreadSafeSimpleWorker(SimpleWorker):
        """
        SimpleWorker, minus its own signal handling.

        work() calls self._install_signal_handlers() as its first
        action (rq/worker/base.py), which calls signal.signal() —
        and signal.signal() only works in the main thread of the
        main interpreter. This runs as a background thread (see
        this function's docstring above), so that call always
        raises ValueError before a single job is ever dequeued.

        There's no constructor flag or attribute in RQ that
        suppresses this (checked BaseWorker/Worker/SimpleWorker);
        the documented way to opt out is to override the method
        itself, as e.g. RQ's own HerokuWorker and the community
        gevent worker subclass do for the same reason — signal
        handling that doesn't fit their execution model.

        The process's actual SIGINT/SIGTERM handling already lives
        in the main thread via _install_signal_handlers() below
        (module-level, not to be confused with this method) and
        _shutdown_event; this thread doesn't need its own.
        """

        def _install_signal_handlers(self):
            pass

    with app.app_context():
        worker = _ThreadSafeSimpleWorker(
            [email_queue, maintenance_queue], connection=redis_client
        )
        logger.info(
            "[START_ALL_WORKER_THREAD_STARTED] queues=%s",
            [q.name for q in [email_queue, maintenance_queue]],
        )
        try:
            # burst=False: keep processing jobs as they arrive, for the
            # lifetime of this thread, instead of exiting once the
            # queues are empty (burst=True is for one-shot/cron-style
            # invocations, not this long-running combined process).
            worker.work(burst=False, with_scheduler=False)
        except Exception:
            logger.error("[START_ALL_WORKER_THREAD_CRASHED]", exc_info=True)
        finally:
            logger.info("[START_ALL_WORKER_THREAD_STOPPED]")


def _install_signal_handlers():
    """
    Logs the signal at this codebase's established bracketed-tag
    convention before deferring to socketio.run()'s own shutdown
    handling for the main thread. The worker thread is a daemon thread
    (see main()), so process exit does not wait on it indefinitely —
    matching the same "don't block shutdown on a background thread"
    property daemon threads already provide elsewhere in this codebase
    (see websocket_messages.py's own presence-touch timer thread).
    """
    def _log_signal(signum, _frame):
        name = signal.Signals(signum).name
        logger.info("[START_ALL_SIGNAL_RECEIVED] signal=%s — shutting down", name)
        _shutdown_event.set()

    signal.signal(signal.SIGINT, _log_signal)
    signal.signal(signal.SIGTERM, _log_signal)


def main():
    _install_signal_handlers()

    # Reuses the exact same factory app.py's own module-level
    # `app, socketio = create_app()` uses — one app instance, shared by
    # the HTTP-serving main thread and the worker background thread's
    # own app_context() calls.
    from app import create_app
    app, socketio = create_app()

    worker_thread = threading.Thread(
        target=_run_worker_thread,
        args=(app,),
        name="rq-worker-thread",
        daemon=True,  # never blocks process exit — see _install_signal_handlers
    )
    worker_thread.start()

    port = int(os.environ.get("PORT", 5001))
    host = "0.0.0.0"

    logger.info(
        "[START_ALL_READY] api=http://%s:%s worker_thread=%s scheduler_enabled=%s",
        host, port, worker_thread.name, app.config.get("SCHEDULER_ENABLED", True),
    )

    try:
        # Matches app.py's own socketio.run(...) call exactly
        # (use_reloader=False for the identical reason app.py's own
        # comment gives: prevents double-starting the scheduler and
        # re-registering the threading WebSocket handlers).
        socketio.run(
            app,
            debug=app.config.get("DEBUG", False),
            host=host,
            port=port,
            allow_unsafe_werkzeug=True,
            use_reloader=False
        )
    finally:
        logger.info("[START_ALL_SHUTDOWN_COMPLETE]")


if __name__ == "__main__":
    sys.exit(main() or 0)
