"""
services/after_commit.py

Tiny registry for side effects that must only happen once the current
database transaction has actually committed: cache invalidation, Redis
counter increments, WebSocket pushes, job enqueues.

Why this exists: running those side effects *inside* the transaction
means a later rollback (an IntegrityError, a failed second step) leaves
the outside world believing something happened that the database never
persisted -- a live "Badge Earned" push and a +1 unread counter for a
notification row that does not exist, or a cache that was invalidated
before the new data was visible (so a concurrent reader re-populates it
with the old value).

Usage::

    from services.after_commit import run_after_commit
    run_after_commit(cache_service.delete, "sh:1:rep:me:42")

Rules:
  * Callables run only after a successful outermost COMMIT, in
    registration order, and must not touch the database (the session
    cannot emit SQL inside the after_commit event) -- capture plain data
    up front.
  * Anything registered is discarded when the outermost transaction ends
    without committing (rollback / close).
  * A failing hook is logged and swallowed; it never breaks the request.
  * If there is no open transaction work to wait for the hook still runs
    at the next commit; callers that never commit simply never fire it.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import event
from sqlalchemy.orm import Session

from extensions import db

logger = logging.getLogger(__name__)

_INFO_KEY = "sh_after_commit_hooks"


def run_after_commit(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """Queue ``fn(*args, **kwargs)`` to run after the current transaction commits."""
    session = db.session()
    # Make sure a transaction is open so a later rollback()/close() ends it
    # and discards these hooks (with no open transaction a rollback is a
    # no-op that would leave them queued for the next, unrelated commit).
    if not session.in_transaction():
        session.begin()
    session.info.setdefault(_INFO_KEY, []).append((fn, args, kwargs))


def pending_hook_count() -> int:
    """Number of hooks currently queued on this session (for tests)."""
    return len(db.session().info.get(_INFO_KEY, []))


@event.listens_for(Session, "after_commit")
def _fire_hooks(session: Session) -> None:
    hooks = session.info.pop(_INFO_KEY, None)
    if not hooks:
        return
    for fn, args, kwargs in hooks:
        try:
            fn(*args, **kwargs)
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "after-commit hook %s failed (non-critical)",
                getattr(fn, "__name__", fn),
                exc_info=True,
            )


@event.listens_for(Session, "after_transaction_end")
def _discard_hooks(session: Session, transaction) -> None:
    # Outermost transaction finished (commit already fired its hooks above;
    # a rollback/close leaves them queued -- drop them).
    if transaction.parent is None:
        session.info.pop(_INFO_KEY, None)
