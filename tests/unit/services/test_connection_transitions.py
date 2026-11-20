"""Audit CON-08 / CON-10: atomic connection state transitions and idempotent block."""
from models import Connection
from services import connection_service as cs


def test_block_is_idempotent_and_keeps_original_owner(db_session, make_user):
    a, b = make_user(), make_user()
    cs.block_connection(a.id, b.id)
    db_session.commit()

    # The blocked user tries to "take over" the block.
    cs.block_connection(b.id, a.id)
    db_session.commit()

    conn = Connection.query.one()
    assert conn.status == "blocked"
    assert conn.blocked_by_id == a.id  # unchanged

    ok, err = cs.unblock_connection(a.id, b.id)
    assert ok is True and err is None


def test_block_converts_existing_accepted_row_without_swapping_direction(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    make_connection(a, b, status="accepted")

    cs.block_connection(b.id, a.id)
    db_session.commit()

    conn = Connection.query.one()
    assert (conn.requester_id, conn.receiver_id) == (a.id, b.id)
    assert conn.status == "blocked" and conn.blocked_by_id == b.id


def test_accept_is_compare_and_set(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    conn = make_connection(a, b, status="pending")

    assert cs.accept_pending_connection(conn.id, b.id) == 1
    db_session.commit()
    # Second accept (lost race) must be a no-op.
    assert cs.accept_pending_connection(conn.id, b.id) == 0
    # Wrong user can never accept.
    c = make_user()
    other = make_connection(a, c, status="pending")
    assert cs.accept_pending_connection(other.id, b.id) == 0


def test_cancel_cannot_delete_an_already_accepted_connection(db_session, make_user, make_connection):
    """The cancel-vs-accept race: cancel must not destroy an accepted friendship."""
    a, b = make_user(), make_user()
    conn = make_connection(a, b, status="pending")
    cs.accept_pending_connection(conn.id, b.id)
    db_session.commit()

    assert cs.cancel_pending_connection(conn.id, a.id) == 0
    db_session.expire_all()
    assert Connection.query.get(conn.id).status == "accepted"


def test_accept_cannot_resurrect_a_blocked_connection(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    conn = make_connection(a, b, status="pending")
    cs.block_connection(b.id, a.id)
    db_session.commit()

    assert cs.accept_pending_connection(conn.id, b.id) == 0
    db_session.expire_all()
    assert Connection.query.get(conn.id).status == "blocked"


def test_reject_then_reopen_swaps_direction_and_sets_responded_at(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    conn = make_connection(a, b, status="pending")
    assert cs.reject_pending_connection(conn.id, b.id) == 1
    db_session.commit()

    # b (the original receiver) now re-requests a.
    assert cs.reopen_rejected_connection(conn.id, b.id, a.id, "hi again") == 1
    db_session.commit()
    db_session.expire_all()

    row = Connection.query.get(conn.id)
    assert (row.requester_id, row.receiver_id) == (b.id, a.id)
    assert row.status == "pending"
    assert row.responded_at is not None
    assert row.request_message == "hi again"
    assert row.receiver_seen is False


def test_remove_accepted_only_removes_accepted(db_session, make_user, make_connection):
    a, b = make_user(), make_user()
    make_connection(a, b, status="pending")
    assert cs.remove_accepted_connection(a.id, b.id) == 0


def test_clean_user_text_strips_html_and_enforces_length():
    assert cs.clean_user_text("<img src=x onerror=alert(1)>hello", 300) == "hello"
    assert cs.clean_user_text("  a < b  ", 300) == "a < b"
    assert cs.clean_user_text("   ", 300) is None
    assert cs.clean_user_text(None, 300) is None
    for bad in (123, ["x"]):
        try:
            cs.clean_user_text(bad, 300)
            assert False, "expected ValueError"
        except ValueError:
            pass
    try:
        cs.clean_user_text("x" * 301, 300)
        assert False, "expected ValueError"
    except ValueError:
        pass
