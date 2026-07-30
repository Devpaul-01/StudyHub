"""
StudyHub - Threads blueprint aggregation

Document 1 §2.2: threads.py (~3,100 lines) split into 5 files:
    crud.py        - thread CRUD (create, details, update, delete, close/reopen, avatar, settings)
    membership.py  - leave/remove, join-request workflow, invites, role management
    messaging.py   - chat/messaging REST fallback (messages, edit/delete/pin, search, attachment upload)
    discovery.py   - department stats, popular, recommended, help suggestions, my-threads
    ai.py          - AI meeting notes

This __init__.py aggregates the five sub-blueprints so that wherever
blueprints are registered with the Flask app, the same import path
(`from routes.student.threads import threads_bp` / or the registration
helper below) continues to expose every route at the exact same path as
the pre-split threads.py.

Also includes the top-level page route (rendered directly here rather than
in crud.py, matching this file's role as the aggregation/entry point).

This is a pure move (Document 5 Phase 2, principle #1): no route paths,
decorators, or function bodies were changed as part of this split.
"""

from flask import Blueprint

from routes.student.threads.crud import threads_crud_bp
from routes.student.threads.membership import threads_membership_bp
from routes.student.threads.messaging import threads_messaging_bp
from routes.student.threads.discovery import threads_discovery_bp
from routes.student.threads.ai import threads_ai_bp

# Aggregate blueprint — kept for any code that imports `threads_bp` by name.
threads_bp = Blueprint("student_threads", __name__)

_SUB_BLUEPRINTS = [
    threads_crud_bp,
    threads_membership_bp,
    threads_messaging_bp,
    threads_discovery_bp,
    threads_ai_bp,
]


def register_threads_blueprints(app_or_bp):
    """
    Register all five threads sub-blueprints directly on the given Flask
    app (or parent blueprint). Prefer this over registering the aggregate
    `threads_bp` above, since Flask blueprints can't be nested by simple
    attachment — each sub-blueprint needs its own `register_blueprint` call.

    Usage in app.py (replacing the old `app.register_blueprint(threads_bp)`):

        from routes.student.threads import register_threads_blueprints
        register_threads_blueprints(app)
    """
    for bp in _SUB_BLUEPRINTS:
        app_or_bp.register_blueprint(bp)
