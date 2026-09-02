"""
Tests for services/jobs/email_jobs.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.8. The entire point of this job,
per its own docstring, is that a mail.send() failure PROPAGATES rather
than being caught/logged/swallowed -- that's what makes RQ's own retry
mechanism and FailedJobRegistry actually engage. A regression that added
a try/except here would silently defeat retries.
"""

from unittest.mock import patch

import pytest

from services.jobs import email_jobs


def test_send_email_job_success_no_exception(app):
    with patch("services.jobs.email_jobs.mail.send") as mock_send:
        result = email_jobs.send_email_job(
            to_email="student@example.com",
            subject="Test Subject",
            html_content="<p>Hello</p>",
        )
    assert result is None
    mock_send.assert_called_once()


def test_send_email_job_failure_propagates(app):
    """This is the entire point of the job -- a regression that swallows
    this exception silently defeats RQ's retry/FailedJobRegistry."""
    with patch("services.jobs.email_jobs.mail.send", side_effect=ConnectionError("SMTP down")):
        with pytest.raises(ConnectionError):
            email_jobs.send_email_job(
                to_email="student@example.com",
                subject="Test Subject",
                html_content="<p>Hello</p>",
            )


def test_send_email_job_uses_configured_sender(app):
    with patch("services.jobs.email_jobs.mail.send") as mock_send:
        email_jobs.send_email_job(
            to_email="student@example.com", subject="S", html_content="<p>x</p>"
        )
    sent_message = mock_send.call_args[0][0]
    assert sent_message.sender == app.config.get("MAIL_DEFAULT_SENDER")
    assert sent_message.recipients == ["student@example.com"]
