"""
StudyHub - Typed Application Exceptions
Phase 0 foundation piece of the backend refactor.

Services and routes raise these instead of manually constructing
`error_response(...)` JSON dicts inline. app.py registers exactly one
Flask error handler (`@app.errorhandler(AppError)`) that turns any of
these into the existing `{"status": "error", "message": ...}` response
shape — so the wire format every existing frontend caller depends on is
completely unchanged; only how the backend *produces* that shape changes.

Deliberately dependency-light: no Flask imports here. That's what makes
this module safe for services/*.py to import without violating the
services -> routes layering rule (services must never import Flask's
request/session/g, or anything under routes/).
"""


class AppError(Exception):
    """
    Base class for every typed application error.

    status_code: HTTP status the centralized handler will use.
    default_message: shown if the raiser didn't pass an explicit message.
    details: optional structured extra info (e.g. per-field validation
             errors) — surfaced under an "errors" key in the response,
             matching helpers.py::error_response's existing `errors=`
             parameter shape so nothing about the response contract changes.
    """
    status_code = 400
    default_message = "Something went wrong."

    def __init__(self, message: str | None = None, *, status_code: int | None = None, details=None):
        self.message = message or self.default_message
        if status_code is not None:
            self.status_code = status_code
        self.details = details
        super().__init__(self.message)

    def __str__(self):
        return self.message


class ValidationError(AppError):
    """Bad input from the caller — malformed/missing/out-of-range fields."""
    status_code = 400
    default_message = "Invalid request."


class NotFoundError(AppError):
    """The requested resource does not exist (or isn't visible to this caller)."""
    status_code = 404
    default_message = "Not found."


class AuthorizationError(AppError):
    """Caller is authenticated but not allowed to perform this action."""
    status_code = 403
    default_message = "You are not authorized to perform this action."


class ConflictError(AppError):
    """The action conflicts with existing state (duplicate request, already-exists, etc.)."""
    status_code = 409
    default_message = "This action conflicts with the current state."


class RateLimitedError(AppError):
    """Caller has exceeded an allowed rate — distinct from Flask-Limiter's own
    429 responses (which fire before a view function even runs); this is for
    business-logic-level throttling (e.g. check_spam()) that still wants a
    typed, catchable error rather than an ad hoc tuple return."""
    status_code = 429
    default_message = "You're doing that too often. Please slow down."


class ExternalServiceError(AppError):
    """A third-party dependency (Cloudinary, an AI provider, email delivery)
    failed. Distinct from a 500 caused by a genuine bug in our own code —
    kept as its own class so logs/monitoring can tell the two apart."""
    status_code = 502
    default_message = "A required external service is temporarily unavailable."
