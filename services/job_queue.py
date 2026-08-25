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

email_queue = Queue(
    "sh:1:rq:email",
    connection=redis_client,
    default_timeout=EMAIL_JOB_TIMEOUT_SECONDS,
)

maintenance_queue = Queue(
    "sh:1:rq:maintenance",
    connection=redis_client,
    default_timeout=MAINTENANCE_JOB_TIMEOUT_SECONDS,
)
