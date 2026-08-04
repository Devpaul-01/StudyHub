"""
services/authorization_service.py

Document 3 §6.4: centralizes the "is the caller the owner of this
resource" check that used to be repeated near-identically across
posts.py, threads.py, homework_system.py, and study_sessions.py —
dozens of `if resource.student_id != current_user.id: return
error_response(..., 403)` blocks, each hand-written slightly
differently.

Combined with services/thread_authorization.py::require_moderator_or_creator
and the connection-blocking checks in services/connection_service.py,
this covers the three genuinely-repeated categories of inline permission
check named in Document 3 §6.4: resource ownership, thread role
membership, and connection/block status.

Per Document 2 §2's layering rule: no Flask imports, no request/session/g.
"""

from __future__ import annotations

from errors import AuthorizationError


def require_owner(resource, owner_field: str, user_id: int, *, message: str = "Not authorized") -> None:
    """
    Raise AuthorizationError unless `resource`'s `owner_field` attribute
    equals `user_id`.

    Usage (replacing the repeated inline pattern):
        authorization_service.require_owner(post, "student_id", current_user.id)

    instead of:
        if post.student_id != current_user.id:
            return error_response("You can only edit your own posts", 403)

    Deliberately does not fetch the resource itself — callers already have
    it in hand (they needed it for the 404-check first), so this stays a
    pure, zero-query predicate.
    """
    if getattr(resource, owner_field) != user_id:
        raise AuthorizationError(message)


def is_owner(resource, owner_field: str, user_id: int) -> bool:
    """
    Pure boolean variant for call sites that want to branch on ownership
    rather than hard-fail (e.g. "show an edit button only if...").
    """
    return getattr(resource, owner_field) == user_id
