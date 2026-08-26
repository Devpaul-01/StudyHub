"""
services/error_tracking.py

Sentry initialization. Follows the same two-step construct-then-init_app
pattern as services/rate_limit_service.py: nothing here does real work at
import time, so this module is safe to import unconditionally from
app.py regardless of whether SENTRY_DSN is set.

FAIL-OPEN, matching every other piece of infrastructure in this codebase
(see cache_service.py's own module docstring for the identical
philosophy applied to Redis): if SENTRY_DSN is unset, or the sentry_sdk
import fails, or Sentry itself is unreachable at runtime, the application
must continue functioning identically. Sentry is an observability layer,
never a request-path dependency.
"""

import logging
import os

logger = logging.getLogger(__name__)

_initialized = False


def init_app(app) -> bool:
    """
    Call once from create_app(), after app.config is populated (same
    ordering rule as rate_limit_service.init_app — config values are read
    here, not at import time).

    Returns True if Sentry was actually initialized, False if skipped
    (no DSN configured) or failed (import error, invalid DSN, etc.) —
    callers that want to log the outcome can use this; nothing in this
    app currently needs to branch on it.
    """
    global _initialized

    dsn = app.config.get("SENTRY_DSN") or os.environ.get("SENTRY_DSN")
    if not dsn:
        logger.info("[SENTRY] SENTRY_DSN not set — error tracking disabled")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        # LoggingIntegration is the primary mechanism — see
        # SENTRY_IMPLEMENTATION_PLAN.md §1 for why this single integration
        # covers all 109+ existing exc_info=True call sites with zero
        # per-route changes. event_level=ERROR deliberately excludes every
        # existing warning/debug fail-open log line (cache_service.py,
        # presence_service.py, distributed_lock.py, etc.) from becoming a
        # Sentry event — those become breadcrumbs instead (level=INFO).
        logging_integration = LoggingIntegration(
            level=logging.INFO,
            event_level=logging.ERROR,
        )

        sentry_sdk.init(
            dsn=dsn,
            environment=app.config.get("FLASK_ENV", "production"),
            release=os.environ.get("SENTRY_RELEASE"),  # e.g. git SHA, set by your deploy step — optional, no-op if unset
            integrations=[
                FlaskIntegration(),
                logging_integration,
            ],
            # Errors only — this app has no APM/tracing need identified in
            # the audit, and turning on performance tracing (traces_sample_rate)
            # would add real per-request overhead for a benefit not asked
            # for. Leave at 0 unless you specifically want it.
            traces_sample_rate=0.0,
            # Sensitive-data scrubbing: send_default_pii=False is the
            # default and is kept explicit here. This app authenticates
            # via JWT cookies (access_token/refresh_token/csrf_token —
            # see helpers.py::set_auth_cookies) and stores password
            # hashes in User.pin / StudentProfile.pin. Do not flip this to
            # True without adding a before_send scrubber for those cookie
            # names and the `pin` field name specifically.
            send_default_pii=False,
            before_send=_scrub_event,
        )

        _initialized = True
        logger.info(
            "[SENTRY] Initialized — environment=%s",
            app.config.get("FLASK_ENV", "production"),
        )
        return True

    except Exception:
        # Fail-open: Sentry failing to initialize must never prevent the
        # app from starting. Logged, not raised.
        logger.warning("[SENTRY] Initialization failed — continuing without error tracking", exc_info=True)
        return False


def _scrub_event(event, hint):
    """
    before_send hook. Strips the three auth cookies and the password-hash
    field name this app actually uses, by name, before an event ever
    leaves the process.

    Deliberately name-based and narrow rather than a generic PII regex —
    this app's exact sensitive fields are known (see helpers.py::
    set_auth_cookies for the three cookie names; models.py::User.pin /
    StudentProfile.pin for the password hash field). A broad scrubber
    risks either missing something this narrow one catches by name, or
    stripping legitimate debugging data this one leaves alone.
    """
    _COOKIE_NAMES = {"access_token", "refresh_token", "csrf_token"}
    _FIELD_NAMES = {"pin", "password", "confirm_password"}

    request_data = event.get("request")
    if request_data and isinstance(request_data.get("cookies"), dict):
        for name in _COOKIE_NAMES:
            if name in request_data["cookies"]:
                request_data["cookies"][name] = "[Filtered]"

    # request body (POST JSON) — several routes accept password/pin
    # fields directly (auth.py's login/register/set-password/
    # complete-registration). Strip by key name if the SDK captured a
    # parsed body.
    data_field = request_data.get("data") if request_data else None
    if isinstance(data_field, dict):
        for name in _FIELD_NAMES:
            if name in data_field:
                data_field[name] = "[Filtered]"

    # Also strip the Authorization / Cookie HTTP headers themselves, if
    # captured — cookies dict above covers the parsed cookie jar, but
    # some SDK versions/configurations also attach a raw headers block.
    # Defensive, name-based, same philosophy as above: better to strip a
    # header that turns out to already be clean than to leave a raw
    # Authorization: Bearer <jwt> line in an event.
    headers = request_data.get("headers") if request_data else None
    if isinstance(headers, dict):
        for header_name in list(headers.keys()):
            if header_name.lower() in ("authorization", "cookie"):
                headers[header_name] = "[Filtered]"

    return event


def is_initialized() -> bool:
    return _initialized
