"""Dialect-portable helpers for filtering JSON array columns."""
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB

from extensions import db


def json_array_contains(column, value):
    """Return a filter clause: JSON-array ``column`` contains ``value``.

    PostgreSQL uses the JSONB ``@>`` operator (index friendly). Other
    dialects (SQLite in local dev/tests) fall back to ``json_each`` because
    ``JSON.contains()`` silently matches nothing there.
    """
    if db.session.get_bind().dialect.name == "postgresql":
        return cast(column, JSONB).contains([value])
    elems = func.json_each(column).table_valued("value")
    return select(1).select_from(elems).where(elems.c.value == value).exists()
