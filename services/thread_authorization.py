"""
services/thread_authorization.py

Document 2 §3.8 / Document 1 §2.2. The single "is this membership a
moderator or creator" check, replacing threads.py's local
_is_mod_or_creator_static AND the five separate inline re-derivations of
the same check that were scattered across threads.py's route bodies
(each doing `if not membership or membership.role not in ("creator",
"moderator")` independently, with its own hand-written error_response call
afterward).

require_moderator_or_creator is the pattern every route used to repeat
manually, collapsed into one call — a small, concrete example of the
"centralize authorization logic instead of scattering inline permission
checks" instruction, scoped narrowly to thread moderation here (the
broader authorization system is Document 3's subject).

Per Document 2 §8: raises AuthorizationError (from errors.py) rather than
returning an HTTP response directly — routes let this propagate to the
centralized @app.errorhandler(AppError) handler already registered in
app.py, instead of each route hand-building an error_response(...) call.

Per Document 2 §2's layering rule: no Flask imports, no `request`/`session`/`g`.
"""

from models import ThreadMember
from errors import AuthorizationError


def is_moderator_or_creator(membership) -> bool:
    """
    Return True if the given ThreadMember row has a privileged role
    (creator or moderator). Pure predicate — no DB access, no raising.

    Replaces threads.py's local _is_mod_or_creator_static, and the
    equivalent (differently-named) helper that used to live in
    websocket_threads.py — this is now the ONE implementation both the
    REST blueprint and the WebSocket manager import, closing the
    REST/WebSocket duplication named in Document 1 §2.2's table.
    """
    return bool(membership and membership.role in ("creator", "moderator"))


def require_moderator_or_creator(thread_id: int, user_id: int) -> ThreadMember:
    """
    Look up the caller's membership in a thread and raise
    AuthorizationError if they aren't a member, or aren't a moderator/creator.

    Returns the ThreadMember row on success, so callers that also need the
    membership object (e.g. to check other fields) don't have to query
    twice.

    This collapses the repeated pattern:
        membership = ThreadMember.query.filter_by(thread_id=thread_id, student_id=current_user.id).first()
        if not membership or membership.role not in ("creator", "moderator"):
            return error_response("Only creator/moderator can ...", 403)
    into a single call:
        membership = require_moderator_or_creator(thread_id, current_user.id)
    with the route letting AuthorizationError propagate to the centralized
    handler instead of hand-building the error response.
    """
    membership = ThreadMember.query.filter_by(
        thread_id=thread_id, student_id=user_id
    ).first()

    if not is_moderator_or_creator(membership):
        raise AuthorizationError("Only the creator or a moderator can perform this action")

    return membership
