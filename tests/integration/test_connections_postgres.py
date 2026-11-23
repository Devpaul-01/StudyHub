"""
Real-PostgreSQL verification of the connections audit fixes (CON-05, CON-08,
CON-16, CON-17).

SQLite cannot exercise LEAST/GREATEST functional indexes, CREATE INDEX
CONCURRENTLY or true parallel transactions, so this module starts a throwaway
PostgreSQL via the optional `pgserver` package (pip install pgserver) and is
skipped automatically when it is not installed.
"""
import importlib.util
import json
import tempfile
import threading

import pytest
import sqlalchemy as sa

pgserver = pytest.importorskip("pgserver")
pytestmark = pytest.mark.integration

MIGRATION = "migrations/versions/c4d9a7e21f58_connections_audit_hardening.py"


@pytest.fixture(scope="module")
def pg_uri():
    server = pgserver.get_server(tempfile.mkdtemp())
    yield server.get_uri().replace("postgresql://", "postgresql+psycopg2://")
    server.cleanup()


def _orm_users(engine, n):
    import models
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        for i in range(1, n + 1):
            s.add(models.User(id=i, username=f"u{i}", email=f"u{i}@x.com", name=f"U{i}", pin="x", status="approved"))
        s.commit()


# --------------------------------------------------------------------------
# CON-05: migration on a LEGACY schema (no indexes/columns, duplicate pairs)
# --------------------------------------------------------------------------
def test_migration_dedups_backfills_and_is_idempotent(pg_uri):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from extensions import db
    import models  # noqa: F401

    engine = sa.create_engine(pg_uri)
    schema = "mig_test"
    with engine.begin() as c:
        c.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        c.execute(sa.text(f"CREATE SCHEMA {schema}"))
    eng = sa.create_engine(pg_uri, connect_args={"options": f"-csearch_path={schema}"})
    db.metadata.create_all(eng)
    _orm_users(eng, 6)

    with eng.begin() as c:
        for stmt in [
            "DROP INDEX IF EXISTS uq_connections_pair", "DROP INDEX IF EXISTS idx_connections_pair_status",
            "DROP INDEX IF EXISTS uq_help_requests_one_active_per_user", "DROP TABLE help_request_volunteers",
            "ALTER TABLE connections DROP COLUMN receiver_seen", "ALTER TABLE connections DROP COLUMN requester_seen",
            "ALTER TABLE connections DROP COLUMN request_message",
        ]:
            c.execute(sa.text(stmt))
        ins = ("INSERT INTO connections (requester_id, receiver_id, status, requested_at, responded_at, is_seen, requester_notes, blocked_by_id) "
               "VALUES (:a,:b,:s,now(),now(),:seen,:n,:bb)")
        for r in [
            dict(a=1, b=2, s="pending", seen=True, n="hello", bb=None),
            dict(a=2, b=1, s="accepted", seen=False, n=None, bb=None),
            dict(a=3, b=4, s="rejected", seen=False, n="pls", bb=None),
            dict(a=4, b=3, s="blocked", seen=False, n=None, bb=4),
            dict(a=5, b=6, s="pending", seen=True, n="Instant connection (compatibility: 80%)", bb=None),
            dict(a=1, b=3, s="pending", seen=False, n="real message", bb=None),
        ]:
            c.execute(sa.text(ins), r)
        c.execute(sa.text(
            "INSERT INTO help_requests (id, requester_id, subject, status, volunteers, created_at, expires_at) VALUES "
            "(1,1,'A','active',CAST(:v AS json),now(),now()+interval '1 hour'),"
            "(2,1,'B','active',CAST(:w AS json),now(),now()+interval '1 hour')"),
            {"v": json.dumps([{"user_id": 2}, {"user_id": 2}, {"user_id": 3}, {"user_id": 999}, {"junk": 1}]),
             "w": json.dumps([{"user_id": 4}])})

    spec = importlib.util.spec_from_file_location("mig", MIGRATION)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    for _ in range(2):   # second run proves idempotency
        with eng.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                with ctx.begin_transaction():
                    mig.upgrade()
            conn.commit()

    with eng.connect() as c:
        rows = c.execute(sa.text(
            "SELECT requester_id, receiver_id, status, receiver_seen, requester_seen, request_message FROM connections ORDER BY id")).fetchall()
        assert len(rows) == 4
        by_pair = {(min(a, b), max(a, b)): st for a, b, st, *_ in rows}
        assert by_pair[(1, 2)] == "accepted" and by_pair[(3, 4)] == "blocked"
        msg = {(a, b): m for a, b, _s, _r, _q, m in rows}
        assert msg[(1, 3)] == "real message" and msg[(5, 6)] is None
        seen = {(a, b): (r, q) for a, b, _s, r, q, _m in rows}
        assert seen[(5, 6)] == (True, True) and seen[(1, 3)] == (False, False)
        assert c.execute(sa.text("SELECT help_request_id, user_id FROM help_request_volunteers ORDER BY 1,2")).fetchall() == [(1, 2), (1, 3), (2, 4)]
        assert c.execute(sa.text("SELECT id, status FROM help_requests ORDER BY id")).fetchall() == [(1, "expired"), (2, "active")]
        idx = {r[0] for r in c.execute(sa.text("SELECT indexname FROM pg_indexes WHERE schemaname=:s"), {"s": schema})}
        assert {"uq_connections_pair", "idx_connections_pair_status", "uq_help_requests_one_active_per_user"} <= idx

    with pytest.raises(sa.exc.IntegrityError):
        with eng.begin() as w:
            w.execute(sa.text("INSERT INTO connections (requester_id, receiver_id, status, requested_at, responded_at) "
                              "VALUES (6,5,'pending',now(),now())"))


# --------------------------------------------------------------------------
# CON-08 / CON-16 / CON-17: real parallel transactions
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def pg_app(pg_uri):
    from flask import Flask
    from extensions import db
    import models

    schema = "conc_test"
    base = sa.create_engine(pg_uri)
    with base.begin() as c:
        c.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        c.execute(sa.text(f"CREATE SCHEMA {schema}"))
    app = Flask("pgtest")
    app.config.update(
        SQLALCHEMY_DATABASE_URI=pg_uri, TESTING=True,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_size": 20, "max_overflow": 20,
                                   "connect_args": {"options": f"-csearch_path={schema}"}},
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        for i in range(1, 41):
            db.session.add(models.User(id=i, username=f"u{i}", email=f"u{i}@x.com", name=f"U{i}", pin="x", status="approved"))
        db.session.commit()
    return app


def _parallel(app, fns):
    from extensions import db
    barrier = threading.Barrier(len(fns))
    out = [None] * len(fns)

    def run(i, fn):
        with app.app_context():
            barrier.wait()
            try:
                out[i] = fn()
            except Exception as exc:   # noqa: BLE001
                db.session.rollback()
                out[i] = exc
            finally:
                db.session.remove()

    threads = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return out


def _new_conn(app, status, a, b):
    from extensions import db
    from models import Connection
    with app.app_context():
        c = Connection(requester_id=a, receiver_id=b, status=status)
        db.session.add(c)
        db.session.commit()
        return c.id


def _state(app, cid):
    from extensions import db
    from models import Connection
    with app.app_context():
        c = db.session.get(Connection, cid)
        return None if c is None else (c.status, c.blocked_by_id)


def _wipe(app):
    from extensions import db
    from models import Connection
    with app.app_context():
        Connection.query.delete()
        db.session.commit()


@pytest.mark.parametrize("round_", range(15))
def test_accept_vs_cancel_has_exactly_one_winner(pg_app, round_):
    from extensions import db
    from services import connection_service as cs
    a, b = 1 + round_, 21 + round_
    cid = _new_conn(pg_app, "pending", a, b)

    def acc():
        n = cs.accept_pending_connection(cid, b); db.session.commit(); return ("accept", n)

    def can():
        n = cs.cancel_pending_connection(cid, a); db.session.commit(); return ("cancel", n)

    res = _parallel(pg_app, [acc, can])
    wins = {k for k, v in res if v == 1}
    state = _state(pg_app, cid)
    assert len(wins) == 1
    if wins == {"accept"}:
        assert state and state[0] == "accepted"      # cancel must NOT have deleted it
    else:
        assert state is None
    _wipe(pg_app)


@pytest.mark.parametrize("round_", range(15))
def test_accept_vs_block_never_resurrects_the_connection(pg_app, round_):
    from extensions import db
    from services import connection_service as cs
    a, b = 1 + round_, 21 + round_
    cid = _new_conn(pg_app, "pending", a, b)

    _parallel(pg_app, [
        lambda: (cs.accept_pending_connection(cid, b), db.session.commit())[0],
        lambda: (cs.block_connection(a, b), db.session.commit())[0],
    ])
    assert _state(pg_app, cid) == ("blocked", a) or _state(pg_app, cid)[0].value == "blocked"
    _wipe(pg_app)


@pytest.mark.parametrize("round_", range(10))
def test_simultaneous_mutual_block_creates_one_row_with_one_owner(pg_app, round_):
    from extensions import db
    from models import Connection
    from services import connection_service as cs
    a, b = 1 + round_, 21 + round_

    res = _parallel(pg_app, [
        lambda: (cs.block_connection(a, b), db.session.commit())[1],
        lambda: (cs.block_connection(b, a), db.session.commit())[1],
    ])
    assert not [r for r in res if isinstance(r, Exception)]
    with pg_app.app_context():
        rows = Connection.query.all()
        assert len(rows) == 1 and rows[0].status == "blocked" and rows[0].blocked_by_id in (a, b)
    _wipe(pg_app)


@pytest.mark.parametrize("round_", range(10))
def test_reversed_pair_insert_race_is_stopped_by_the_unique_index(pg_app, round_):
    from extensions import db
    from models import Connection
    a, b = 1 + round_, 21 + round_

    def ins(x, y):
        def f():
            db.session.add(Connection(requester_id=x, receiver_id=y, status="pending"))
            db.session.commit()
            return "ok"
        return f

    res = _parallel(pg_app, [ins(a, b), ins(b, a)])
    with pg_app.app_context():
        assert Connection.query.count() == 1
    assert sum(isinstance(r, sa.exc.IntegrityError) for r in res) == 1
    _wipe(pg_app)


def test_concurrent_volunteers_are_all_stored_exactly_once(pg_app):
    from extensions import db
    from models import HelpRequest, HelpRequestVolunteer

    with pg_app.app_context():
        hr = HelpRequest(requester_id=1, subject="x", status="active")
        db.session.add(hr); db.session.commit(); hid = hr.id

    def vol(uid):
        def f():
            try:
                with db.session.begin_nested():
                    db.session.add(HelpRequestVolunteer(help_request_id=hid, user_id=uid))
                    db.session.flush()
                db.session.commit()
                return "ok"
            except sa.exc.IntegrityError:
                db.session.rollback()
                return "dup"
        return f

    res = _parallel(pg_app, [vol(u) for u in range(2, 27)] + [vol(30)] * 5)
    with pg_app.app_context():
        assert HelpRequestVolunteer.query.filter_by(help_request_id=hid).count() == 26
    assert res.count("dup") == 4


def test_concurrent_broadcasts_leave_one_active_request(pg_app):
    from extensions import db
    from models import HelpRequest

    def bc():
        HelpRequest.query.filter_by(requester_id=35, status="active").update({"status": "expired"}, synchronize_session=False)
        db.session.add(HelpRequest(requester_id=35, subject="y", status="active"))
        db.session.commit()
        return "ok"

    for _ in range(10):
        _parallel(pg_app, [bc, bc])
        with pg_app.app_context():
            assert HelpRequest.query.filter_by(requester_id=35, status="active").count() == 1
