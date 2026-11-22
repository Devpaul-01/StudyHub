"""Tests for services/atomic_counters.py."""

from sqlalchemy import event

from extensions import db
from models import User
from services.atomic_counters import adjust_counter


def test_increment_and_decrement(db_session, make_user):
    user = make_user(total_helpful=5)
    adjust_counter(User, user.id, "total_helpful", 1)
    assert user.total_helpful == 6          # loaded instance is refreshed
    adjust_counter(User, user.id, "total_helpful", -2)
    assert user.total_helpful == 4


def test_decrement_is_floored_at_zero(db_session, make_user):
    user = make_user(total_helpful=1)
    adjust_counter(User, user.id, "total_helpful", -5)
    assert user.total_helpful == 0


def test_null_counter_is_treated_as_zero(db_session, make_user):
    user = make_user()
    user.total_helpful = None
    db_session.commit()
    adjust_counter(User, user.id, "total_helpful", 1)
    assert user.total_helpful == 1


def test_emits_relative_sql_not_an_absolute_value(db_session, make_user):
    user = make_user(total_helpful=5)
    seen = []

    def grab(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE USERS"):
            seen.append(" ".join(statement.split()))

    event.listen(db.engine, "before_cursor_execute", grab)
    try:
        adjust_counter(User, user.id, "total_helpful", 1)
    finally:
        event.remove(db.engine, "before_cursor_execute", grab)

    assert seen and "coalesce(users.total_helpful, ?) + ?" in seen[0]


def test_stale_in_memory_value_does_not_cause_lost_update(db_session, make_user):
    """Simulates two requests that both loaded total_helpful == 5."""
    user = make_user(total_helpful=5)
    stale_read_a = user.total_helpful
    stale_read_b = user.total_helpful
    adjust_counter(User, user.id, "total_helpful", 1)   # request A
    db_session.commit()
    adjust_counter(User, user.id, "total_helpful", 1)   # request B (same stale read)
    db_session.commit()
    assert stale_read_a == stale_read_b == 5
    assert user.total_helpful == 7                       # both increments landed
