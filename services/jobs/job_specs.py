"""
services/jobs/job_specs.py

Central place for this phase's RQ Retry() policies — one named
constant per job, imported by every enqueue call site instead of each
call site inventing its own retry numbers inline. Mirrors
rate_limit_service.py's own RateLimitTier pattern (named policies,
picked by name at the call site) for the identical reason: one place
to see/change every job's retry policy, not scattered inline literals.

See BACKGROUND_JOBS_IMPLEMENTATION.md §12 for why each policy's max
attempts / backoff intervals were chosen per job.
"""

from rq import Retry

# send_email_job: 3 attempts total (1 original + 2 retries), backing off
# 10s then 60s. Balances "genuinely transient SMTP hiccups get a fair
# chance" against "don't let a permanently-broken mail config retry
# forever."
EMAIL_RETRY = Retry(max=3, interval=[10, 60])

# cleanup_expired_activity_feed_job: 3 attempts, longer backoff (30s,
# 300s) appropriate for a DB-connection-blip failure mode, which
# benefits from more time passing before retry than an SMTP hiccup does.
ACTIVITY_FEED_CLEANUP_RETRY = Retry(max=3, interval=[30, 300])

# alert_stale_ai_conversations_job: 2 attempts, fixed 60s backoff —
# lowest-stakes job in this phase (read-only, weekly, alert-only); 2
# attempts is enough to ride out a momentary DB blip without
# over-engineering retry policy for a job whose worst-case failure is
# "one log line is late."
STALE_CONVERSATION_ALERT_RETRY = Retry(max=2, interval=[60])
