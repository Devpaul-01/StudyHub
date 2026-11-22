"""
services/atomic_counters.py

Race-free integer counter updates for denormalised columns
(``comments.helpful_count``, ``users.total_helpful``, ``posts.positive_reactions_count``...).

The ORM idiom ``row.count += 1`` reads the value into Python and writes back
an ABSOLUTE number (``UPDATE ... SET count = 8``). Two concurrent requests
that both read 7 both write 8 and one increment is lost. The helper below
emits a relative expression (``SET count = coalesce(count, 0) + 1``) that
the database applies atomically against the current row value, with an
optional floor so decrements never go negative.

Does NOT commit. Any already-loaded instance is expired so its next
attribute access re-reads the true value.
"""

from __future__ import annotations

from sqlalchemy import case, func
from sqlalchemy.orm.util import identity_key

from extensions import db


def adjust_counter(model, row_id, field: str, delta: int, *, floor: int | None = 0) -> None:
    """Atomically add ``delta`` to ``model.<field>`` for the row ``row_id``."""
    column = getattr(model, field)
    new_value = func.coalesce(column, 0) + delta
    if floor is not None and delta < 0:
        new_value = case((new_value < floor, floor), else_=new_value)

    db.session.query(model).filter(model.id == row_id).update(
        {column: new_value}, synchronize_session=False
    )

    instance = db.session.identity_map.get(identity_key(model, row_id))
    if instance is not None:
        db.session.expire(instance, [field])
