# StudyHub — Architecture Notes

## Single-process deployment constraint (H-11)

This application currently **must** run as a single Python process
(`gunicorn -w 1`, one dyno/instance, no horizontal scaling) because several
independent subsystems keep their runtime state purely in that process's
memory, with no shared backing store across processes/machines:

| Subsystem | State kept in-process | File(s) |
|---|---|---|
| Real-time messaging / presence | `online_users`, `socket_to_user` | `services/websocket_messages.py` |
| Group-chat / typing / rate limiting | `user_active_thread`, typing state, send-rate buckets | `services/websocket_threads.py`, `services/websocket_rate_limiter.py` |
| AI provider routing | `failed_providers`, per-provider-type blacklist | `routes/student/learnora.py::MultiProviderManager` |
| Scheduled jobs | APScheduler's `BackgroundScheduler` job registry | `scheduler.py` |

Concretely, this means:

- A client connected to worker/instance A's Socket.IO server will **not**
  receive a broadcast triggered by an event handled on worker/instance B,
  because `SocketIO(...)` is configured with no `message_queue=` (no
  Redis/RabbitMQ pub-sub backing every room across processes).
- If Cerebras (for example) starts failing, worker A's
  `MultiProviderManager` will correctly blacklist it after a few failures —
  but worker B has no idea that happened and will keep sending it traffic
  until it independently hits the same threshold.
- `scheduler.py`'s own module docstring already calls this out explicitly
  for APScheduler specifically (`"Keep -w 1 if using APScheduler..."`) —
  the same constraint silently applies to the two subsystems above it in
  the table, which weren't previously documented anywhere.

**This is a conscious, currently-accepted constraint, not an oversight** —
recorded here so it's a known trade-off rather than a surprise the first
time someone tries to add a second worker or a second instance behind a
load balancer.

### If/when horizontal scaling becomes a requirement

The migration path, roughly in order of how much of the above table it
unblocks per unit of effort:

1. **Redis-backed Socket.IO** — pass `message_queue="redis://..."` to the
   `SocketIO(...)` constructor in `services/websocket_messages.py`. This
   alone lets `online_users`/room broadcasts work correctly across multiple
   processes (Socket.IO handles the pub/sub fan-out itself once pointed at
   Redis) — it does **not**, by itself, fix `socket_to_user`/
   `user_active_thread`, which are plain Python dicts local to each worker
   and would still need to move to Redis (or be looked up per-request
   instead of cached in memory) separately.
2. **Move `MultiProviderManager`'s failure/blacklist state into Redis** —
   swap the in-memory dicts for Redis keys with TTLs matching
   `PROVIDER_FAILURE_WINDOW`/`PROVIDER_BLACKLIST_DURATION`, so every worker
   shares one view of "is this provider currently blacklisted."
3. **Move scheduled jobs off in-process APScheduler** — either run
   `scheduler.py`'s jobs from a single, dedicated worker
   (`SCHEDULER_ENABLED=false` everywhere else, which the code already
   supports today) as a lighter-weight interim step, or migrate to a
   distributed scheduler (Celery Beat, or a managed cron hitting an
   internal endpoint) if the interim step isn't sufficient.

None of the above is implemented as part of this pass — see the
accompanying implementation summary for why (it's a genuine architecture
change, not a bug fix, and the audit itself frames it as optional: *"if
genuine horizontal scaling is a goal..."*). This document exists purely to
make the current, real constraint explicit and discoverable.
