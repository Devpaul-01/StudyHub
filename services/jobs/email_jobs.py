"""
services/jobs/email_jobs.py

RQ job functions for email sending. Replaces two previously-separate
patterns: utils.py's direct in-request mail.send() (validate_user,
register's non-Google path) and utils.py::send_email_now's untracked
daemon-thread pattern (waitlist welcome, referral milestone) -- both
collapse into this one durable, retryable job.

CRITICAL: this function is NOT idempotent on retry, and that is
intentional -- see BACKGROUND_JOBS_IMPLEMENTATION.md §13.2. True
delivery-exactly-once would require a durable send-ledger for a
codebase whose actual email surface is four low-frequency transactional
types, each already protected downstream by its own single-use
mechanism (PasswordResetToken.used, User.email_verified idempotent
checks). At-least-once is the accepted, reasoned tradeoff.

Runs inside a WORKER process (worker.py), which has its own Flask app
context -- NOT the same app context as whatever HTTP request enqueued
the job. Arguments are plain strings, never ORM objects, per
services/jobs/__init__.py's rules.
"""

import logging

from flask import current_app
from flask_mail import Message

from extensions import mail

logger = logging.getLogger(__name__)


def send_email_job(*, to_email: str, subject: str, html_content: str) -> None:
    """
    The single, unified email-send job body.

    Raises on failure (does NOT catch/log-and-swallow) so RQ's own
    retry mechanism (configured at enqueue time via
    services/jobs/job_specs.py::EMAIL_RETRY) actually engages. This is
    the one behavioral difference from the old send_email_now, which
    caught everything and returned False -- that pattern made failures
    invisible; letting the exception propagate here is what makes RQ's
    FailedJobRegistry actually populate on genuine failures.
    """
    msg = Message(
        subject=subject,
        recipients=[to_email],
        html=html_content,
        sender=current_app.config.get("MAIL_DEFAULT_SENDER"),
    )
    mail.send(msg)
    logger.info("[EMAIL_JOB_SENT] to=%s subject=%s", to_email, subject)
