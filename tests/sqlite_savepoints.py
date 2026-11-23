"""
Make SQLite honour SAVEPOINT / ROLLBACK semantics like PostgreSQL does.

pysqlite's default transaction handling emits BEGIN lazily (only before
INSERT/UPDATE/DELETE), so a SAVEPOINT issued first silently runs in
autocommit mode and RELEASE then COMMITS whatever was inside it -- a later
ROLLBACK cannot undo it. The application relies on savepoints
(begin_nested) for idempotent inserts, so the test engine must behave like
production. This is the workaround from the SQLAlchemy documentation
("Serializable isolation / Savepoints / Transactional DDL" for pysqlite).
"""

import sqlite3

from sqlalchemy import event


def install(engine):
    @event.listens_for(engine, "connect")
    def _no_implicit_begin(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _explicit_begin(conn):
        # The suite's StaticPool shares ONE DBAPI connection between
        # sessions (e.g. a streaming response's own session), so only
        # open a transaction if none is already open.
        if not conn.connection.dbapi_connection.in_transaction:
            conn.exec_driver_sql("BEGIN")

    # With one shared DBAPI connection, a Connection object can believe a
    # transaction is open after a different session already committed it;
    # a ROLLBACK with nothing to roll back is then harmless, not an error.
    _orig_do_rollback = engine.dialect.do_rollback

    def _tolerant_do_rollback(dbapi_connection):
        try:
            _orig_do_rollback(dbapi_connection)
        except sqlite3.OperationalError as exc:
            if "no transaction is active" not in str(exc):
                raise

    engine.dialect.do_rollback = _tolerant_do_rollback
