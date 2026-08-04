"""
services/thread_authorization.py

Document 2 §3.8 / Document 1 §2.2: the single implementation of
"is this membership a moderator or creator" — replaces the two
independently-duplicated copies that used to live in threads.py
(`_is_mod_or_creator_static`) and services/websocket_threads.py
(`_is_moderator_or_creator`).

Per Document 2 §2's layering rule: no Flask imports, no request/session/g.
Raises errors.AuthorizationError (imported from the dependency-free
errors.py — safe for services to import per errors.py's own docstring).
"""

from __future__ import annotations

from models import ThreadMember
from errors import AuthorizationError


def is_moderator_or_creator(membership: "ThreadMember | None") -> bool:
    """
    Pure predicate: does this membership row carry moderator/creator
    privileges? Returns False for None (not a member at all) rather than
    raising, so callers that just want a boolean (e.g. websocket handlers
    building a UI-permission flag) don't need a try/except for the common
    "not even a member" case.
    """
    if not membership:
        return False
    return membership.role in ("creator", "moderator")


def require_moderator_or_creator(thread_id: int, user_id: int) -> ThreadMember:
    """
    Look up the membership row for (thread_id, user_id) and raise
    AuthorizationError unless it exists and carries moderator/creator
    privileges. Returns the membership row on success, so callers that
    need it anyway (e.g. to read `.role`) don't have to query twice.

    This collapses the pattern every route used to repeat manually:
        membership = ThreadMember.query.filter_by(...).first()
        if not membership or not is_mod_or_creator(membership):
            return error_response(..., 403)
    into one call, per Document 2 §3.8.
    """
    membership = ThreadMember.query.filter_by(
        thread_id=thread_id, student_id=user_id
    ).first()

    if not membership or not is_moderator_or_creator(membership):
        raise AuthorizationError("Only the thread creator or a moderator can perform this action")

    return membership
