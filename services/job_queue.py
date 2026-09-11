"""
services/job_queue.py

RQ (Redis Queue) wrapper — the durable-job equivalent of
extensions.py::redis_client / distributed_lock.py's Redis usage
pattern. This is the ONLY file that constructs rq.Queue instances;
every job-enqueuing call site imports the named queues from here,
never constructs its own Queue(...).

Two named queues, matching the two job categories this phase
introduces (see BACKGROUND_JOBS_IMPLEMENTATION.md §6):
  - email_queue:       password reset / verification / waitlist /
                        referral-milestone email sends
  - maintenance_queue:  scheduled cleanup/alert jobs enqueued by
                        scheduler.py

Reuses extensions.redis_client's connection rather than opening a
second Redis connection pool, matching this codebase's established
"one Redis client, reused everywhere" convention (see
websocket_messages.py's own comment on reusing extensions.redis_client's
URL for its message_queue, for the identical reasoning applied to
Flask-SocketIO's own Redis needs).

Per services/__init__.py's layering rule: no Flask imports, no
request/session/g.
"""

from rq import Queue

from extensions import redis_client

# Job timeout ceilings — RQ's own per-job "kill if it runs longer than
# this" guard, distinct from retry count (see services/jobs/job_specs.py's
# per-job retry configuration). Chosen per queue based on the slowest
# job that queue is expected to run — see
# BACKGROUND_JOBS_IMPLEMENTATION.md §6.2/§6.3 for why each number was
# picked.
EMAIL_JOB_TIMEOUT_SECONDS = 30          # SMTP send, generous vs. realistic send time
MAINTENANCE_JOB_TIMEOUT_SECONDS = 600   # batched DB scan/delete, see §9 batch sizing

# FIX: extensions.redis_client is None whenever Redis is unreachable at
# import time (see extensions.py::_create_redis_client's documented
# fail-open path — this is genuinely None in local dev with no Redis
# running, and in this test suite, which points REDIS_URL at a port
# nothing listens on). rq.Queue(connection=None) does NOT treat that as
# "use the default connection" the way some other Redis-adjacent
# wrappers do — it raises `TypeError: Queue() missing 1 required
# positional argument: 'connection'` at construction time, which then
# blows up on module import (`from services.job_queue import
# email_queue`) for every caller, in every environment, any time Redis
# happens to be down — including this fail-open test environment where
# every OTHER Redis-touching module (cache_service, rate_limit_service,
# etc.) degrades gracefully instead of crashing at import.
#
# A real redis.Redis client object (rather than None) always satisfies
# rq.Queue's constructor, whether or not the server behind it is
# actually reachable — RQ doesn't ping at construction time, only when a
# job is actually enqueued/dequeued. So constructing a client here from
# the same REDIS_URL, without a connection check, is enough to make
# import always succeed; any actual .enqueue() call while Redis is down
# still fails at call time, which is the existing (correct, already
# handled elsewhere) fail-open expectation, not a new failure mode.
import os
import redis as _redis_module


def _redis_connection_for_queues():
    if redis_client is not None:
        return redis_client
    # Fall back to constructing our own client from REDIS_URL rather than
    # reusing extensions.redis_client's already-failed instance — no
    # connection attempt happens here, only object construction, so this
    # never raises even if the URL is unreachable.
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return _redis_module.from_url(redis_url)


_queue_connection = _redis_connection_for_queues()

email_queue = Queue(
    "sh:1:rq:email",
    connection=_queue_connection,
    default_timeout=EMAIL_JOB_TIMEOUT_SECONDS,
)

maintenance_queue = Queue(
    "sh:1:rq:maintenance",
    connection=_queue_connection,
    default_timeout=MAINTENANCE_JOB_TIMEOUT_SECONDS,
)
