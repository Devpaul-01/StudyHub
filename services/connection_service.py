"""
services/connection_service.py

Document 2 §3.4. Owns connection-blocking logic, moved out of
routes/student/helpers.py — helpers.py is being repositioned as an
HTTP-layer-only file (Document 2 §2.1), and blocking logic is exactly the
kind of business logic this migration is about relocating.

is_user_blocked / block_connection / unblock_connection are moved here
VERBATIM (no behavior change) from helpers.py. helpers.py keeps a thin
re-export shim for one release cycle so existing callers (e.g.
connections.py's `from routes.student.helpers import block_connection,
unblock_connection`) keep working unchanged — see Document 5's rollout
notes on this specific move ("it's the one place this migration touches
code that was *just* fixed in the prior phase, so it gets extra care").

Per Document 2 §5's transaction-boundary convention: these functions
mutate the session (db.session.add / attribute assignment / db.session.delete)
but do NOT call db.session.commit() or db.session.rollback() — that stays
the calling route's responsibility. This is unchanged from how these
functions already behaved in helpers.py.

Per Document 2 §2's layering rule: no Flask imports, no `request`/`session`/`g`.
"""

import datetime

from sqlalchemy import or_, and_

from models import Connection
from extensions import db


def _find_connection_between(user_a_id, user_b_id, status=None):
    """Find the Connection row between two users, in either direction."""
    query = Connection.query.filter(
        or_(
            and_(Connection.requester_id == user_a_id, Connection.receiver_id == user_b_id),
            and_(Connection.requester_id == user_b_id, Connection.receiver_id == user_a_id),
        )
    )
    if status:
        query = query.filter(Connection.status == status)
    return query.first()


def is_user_blocked(user_a_id, user_b_id):
    """
    Return (blocked_by_a, blocked_by_b): whether user_a has blocked user_b,
    and whether user_b has blocked user_a. At most one of these can be True
    at a time under the current one-row-per-pair model.

    This is the single "is this pair blocked" check — use it everywhere
    instead of re-deriving blocking direction from requester_id/receiver_id.
    """
    connection = _find_connection_between(user_a_id, user_b_id, status="blocked")

    if not connection or not connection.blocked_by_id:
        return False, False

    return connection.blocked_by_id == user_a_id, connection.blocked_by_id == user_b_id


def block_connection(blocker_id, blocked_id):
    """
    Block `blocked_id` on behalf of `blocker_id`.

    Reuses the existing Connection row between the two users if one exists
    (whatever its prior status — pending, accepted, rejected), setting
    status="blocked" and blocked_by_id=blocker_id, WITHOUT touching
    requester_id/receiver_id. Creates a fresh row if no connection existed
    yet. Does not commit — the caller's existing try/except/commit block
    stays in charge of the transaction, same as before this fix.

    Returns the (session-pending) Connection object.
    """
    connection = _find_connection_between(blocker_id, blocked_id)

    now = datetime.datetime.utcnow()

    if connection:
        connection.status = "blocked"
        connection.blocked_by_id = blocker_id
        connection.responded_at = now
    else:
        connection = Connection(
            requester_id=blocker_id,
            receiver_id=blocked_id,
            status="blocked",
            blocked_by_id=blocker_id,
            requested_at=now,
            responded_at=now,
        )
        db.session.add(connection)

    return connection


def unblock_connection(unblocker_id, other_id, restore_to_accepted=False):
    """
    Remove a block between `unblocker_id` and `other_id`. Only the user who
    created the block (Connection.blocked_by_id) may remove it.

    restore_to_accepted:
        False (default) — delete the connection row entirely; the two users
            are no longer connected and would need a fresh connection
            request to reconnect. Matches the original
            connections.py /connections/unblock behaviour.
        True — keep the row and set status back to "accepted" instead of
            deleting it, so messaging/connection status is restored
            immediately with no new request needed. Matches the original
            messages.py /messages/unblock behaviour.

    Does not commit — same convention as block_connection() above.

    Returns (success: bool, error_message: str | None).
    """
    connection = _find_connection_between(unblocker_id, other_id, status="blocked")

    if not connection:
        return False, "User is not blocked"

    if connection.blocked_by_id != unblocker_id:
        return False, "Not authorized"

    if restore_to_accepted:
        connection.status = "accepted"
        connection.blocked_by_id = None
        connection.responded_at = datetime.datetime.utcnow()
    else:
        db.session.delete(connection)

    return True, None


def can_message(sender_id, receiver_id):
    """
    Check whether sender is allowed to DM receiver.

    Moved here (unchanged behavior) from messages.py, per Document 2 §4's
    decision test: pure predicate, no HTTP dependency, and conceptually
    about connection state rather than message content — the same
    reasoning that put is_user_blocked/block_connection/unblock_connection
    here.

    Rules:
    1. Must have an accepted connection, OR
    2. (system-message exception — not currently implemented; see original
       docstring in messages.py, kept verbatim below for context)

    Note: Thread members CANNOT DM — must connect first.
    """
    if sender_id == receiver_id:
        return False

    # Check for accepted connection
    connection = _find_connection_between(sender_id, receiver_id, status="accepted")

    return connection is not None
