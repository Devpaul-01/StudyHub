"""
services/homework_lifecycle.py

Lifecycle rules for HomeworkSubmission ("one person helping another") -
audit findings F-03, F-05, F-11, F-12.

* One declarative transition table, enforced by the mutating routes, so the
  allowed moves live in one reviewable place instead of being re-derived
  from scattered ``if status ...`` checks.
* ``claim_submission`` performs a status change as a single conditional
  ``UPDATE ... WHERE id=:id AND status IN (...)``. Of N concurrent requests
  racing for the same transition exactly one gets ``True``; the rest get
  ``False`` and must not repeat the side effects (notifications, streak).
  This is plain row-level SQL: it works unchanged under PgBouncer
  transaction pooling and across any number of app instances.
* Cancelling is a soft state change (``cancelled``), never a DELETE, so a
  helper's history and the requester's feedback are not destroyed.

Per the layering rule: no Flask imports. Functions mutate the session but do
NOT commit; the calling route owns the single commit.
"""

from __future__ import annotations

from sqlalchemy import and_, or_

from extensions import db
from models import Connection, HomeworkSubmission

PENDING = "pending"
ACCEPTED = "accepted"      # legacy value, never written by the app
SUBMITTED = "submitted"
REVIEWED = "reviewed"
COMPLETED = "completed"
DECLINED = "declined"      # legacy value, never written by the app
CANCELLED = "cancelled"

# state -> states it may move to. Anything not listed is rejected.
SUBMISSION_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING:   frozenset({SUBMITTED, CANCELLED}),
    ACCEPTED:  frozenset({SUBMITTED, CANCELLED}),
    SUBMITTED: frozenset({SUBMITTED, REVIEWED, COMPLETED, CANCELLED}),
    # feedback given without completing: the helper may revise, or the
    # requester may finalise later (previously a dead end - F-11).
    REVIEWED:  frozenset({SUBMITTED, COMPLETED}),
    COMPLETED: frozenset(),
    DECLINED:  frozenset(),
    CANCELLED: frozenset(),
}

# Rows in these states are "live": they count in lists, badges and stats.
TERMINAL_STATES = frozenset({COMPLETED, DECLINED, CANCELLED})


def can_transition(old: str | None, new: str) -> bool:
    return new in SUBMISSION_TRANSITIONS.get(_value(old), frozenset())


def sources_for(new: str) -> frozenset[str]:
    """Every state from which ``new`` is a legal move."""
    return frozenset(s for s, targets in SUBMISSION_TRANSITIONS.items() if new in targets)


def _value(status) -> str:
    return str(getattr(status, "value", status) or "")


def not_cancelled():
    """SQL filter for 'live' submissions (cancelled rows are history)."""
    return or_(HomeworkSubmission.status.is_(None), HomeworkSubmission.status != CANCELLED)


def claim_submission(submission_id: int, from_statuses, **values) -> bool:
    """Atomically apply ``values`` iff the row is still in one of
    ``from_statuses``. Returns True for the single winner."""
    rowcount = (
        db.session.query(HomeworkSubmission)
        .filter(
            HomeworkSubmission.id == submission_id,
            HomeworkSubmission.status.in_(list(from_statuses)),
        )
        .update(values, synchronize_session=False)
    )
    return rowcount == 1


def cancel_open_offers(assignment_id: int, statuses=(PENDING, ACCEPTED)) -> list[HomeworkSubmission]:
    """Cancel the assignment's open offers (default: ones where the helper
    has not produced work yet). Returns the submissions this call actually
    cancelled so the caller can notify exactly those helpers."""
    candidates = HomeworkSubmission.query.filter(
        HomeworkSubmission.assignment_id == assignment_id,
        HomeworkSubmission.status.in_(list(statuses)),
    ).all()
    cancelled = []
    for sub in candidates:
        if claim_submission(sub.id, statuses, status=CANCELLED):
            cancelled.append(sub)
    for sub in cancelled:
        db.session.refresh(sub)
    return cancelled


def detach_from_assignment(assignment_id: int) -> int:
    """Keep help history when an assignment is deleted: NULL the FK (the
    denormalised title/subject on the row keeps it readable)."""
    return (
        db.session.query(HomeworkSubmission)
        .filter(HomeworkSubmission.assignment_id == assignment_id)
        .update({"assignment_id": None}, synchronize_session=False)
    )


def participants_blocked(user_a: int, user_b: int) -> bool:
    """True if either user has blocked the other. Blocking rewrites the
    Connection row, but these routes only checked connection at offer time."""
    return db.session.query(
        Connection.query.filter(
            Connection.status == "blocked",
            or_(
                and_(Connection.requester_id == user_a, Connection.receiver_id == user_b),
                and_(Connection.requester_id == user_b, Connection.receiver_id == user_a),
            ),
        ).exists()
    ).scalar()
