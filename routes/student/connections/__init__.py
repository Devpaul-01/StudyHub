"""
StudyHub - Connections blueprint aggregation

Document 1 §2.1: connections.py (~4,900 lines) split into 5 files:
    crud.py           - connection request lifecycle + help requests
    blocking.py       - block / unblock endpoints
    discovery.py      - mutual connections, suggestions, search, availability
    compatibility.py  - AI compatibility scoring + connection overview
    health.py         - connection health/detail, notes, online-connections listing

This __init__.py aggregates the five sub-blueprints into a single
`connections_bp` so that routes/student/__init__.py (or wherever blueprints
are registered with the Flask app) can keep doing
`from routes.student.connections import connections_bp` unchanged —
every route path below is identical to the pre-split connections.py.

This is a pure move (Document 5 Phase 2, principle #1): no route paths,
decorators, or function bodies were changed as part of this split.
"""

from flask import Blueprint

from routes.student.connections.crud import connections_crud_bp
from routes.student.connections.blocking import connections_blocking_bp
from routes.student.connections.discovery import connections_discovery_bp
from routes.student.connections.compatibility import connections_compatibility_bp
from routes.student.connections.health import connections_health_bp

# Aggregate blueprint — registered with the Flask app exactly like the
# original monolithic connections_bp was.
connections_bp = Blueprint("connections", __name__)

_SUB_BLUEPRINTS = [
    connections_crud_bp,
    connections_blocking_bp,
    connections_discovery_bp,
    connections_compatibility_bp,
    connections_health_bp,
]


def register_connections_blueprints(app_or_bp):
    """
    Register all five connections sub-blueprints directly on the given
    Flask app (or parent blueprint). Prefer this over registering the
    aggregate `connections_bp` above, since Flask blueprints can't be
    nested by simple attachment — each sub-blueprint needs its own
    `register_blueprint` call.

    Usage in app.py (replacing the old `app.register_blueprint(connections_bp)`):

        from routes.student.connections import register_connections_blueprints
        register_connections_blueprints(app)
    """
    for bp in _SUB_BLUEPRINTS:
        app_or_bp.register_blueprint(bp)
