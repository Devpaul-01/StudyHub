"""Dialect-aware ``INSERT`` construct for atomic upserts.

Production runs PostgreSQL; the test-suite default is SQLite. Both support
``ON CONFLICT`` with the same SQLAlchemy API, but through different
dialect-specific ``insert`` constructs.
"""
from sqlalchemy.dialects import postgresql, sqlite

from extensions import db


def dialect_insert():
    """Return the dialect's ``insert`` callable for the current session, or
    None when the dialect has no ``ON CONFLICT`` support."""
    name = db.session.get_bind().dialect.name
    if name == "postgresql":
        return postgresql.insert
    if name == "sqlite":
        return sqlite.insert
    return None
