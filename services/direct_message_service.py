"""
services/direct_message_service.py

Single source of truth for direct-message delete/mark-read logic, shared
between the WebSocket handler (services/websocket_messages.py) and the
REST fallback (routes/student/messages.py). Same motivation and pattern as
services/thread_message_service.py — see that module's docstring for the
full rationale; this one exists because reading messages.py against
websocket_messages.py line-by-line turned up real, not cosmetic, drift:

  - delete_message_for_everyone (REST) had NO 5-minute edit window — WS
    enforces one. REST let the sender delete-for-everyone at any time.
  - delete_message_for_everyone (REST) set ONLY message.is_deleted = True.
    WS sets deleted_by_sender=True, deleted_by_receiver=True, AND rewrites
    body to '[Message deleted]'. This is a genuine DATA-MODEL
    inconsistency, not just a missing broadcast: get_shared_media,
    get_shared_media_count, and get_conversations' unread-count query all
    filter on deleted_by_sender/deleted_by_receiver WITHOUT also checking
    is_deleted — so a message deleted "for everyone" via REST could still
    surface in shared-media listings or unread counts, because those
    queries never look at the flag REST actually set. A message deleted
    via REST and one deleted via WS ended up in different, incompatible
    states in the same table.
  - Neither delete_message_for_everyone nor delete_message (for-me) nor
    mark_message_read nor mark_all_read broadcast anything — a
    WS-connected recipient never saw a REST-driven delete or read-receipt
    reflected live.

Same design as thread_message_service.py: typed exceptions, no
transport-layer opinion, no flask_socketio import, no import of
websocket_messages.py (avoids circular import once that file calls in).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from extensions import db
from models import Message

DELETE_FOR_EVERYONE_WINDOW_SECONDS = 300  # 5 minutes, matching the WS handler


class DirectMessageError(Exception):
    code = "error"

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class MessageNotFoundError(DirectMessageError):
    code = "message_not_found"


class PermissionDeniedError(DirectMessageError):
    code = "permission_denied"


class DeleteWindowExpiredError(DirectMessageError):
    code = "delete_window_expired"


@dataclass
class DeleteForEveryoneResult:
    message_id: int
    sender_id: int
    receiver_id: int


@dataclass
class DeleteForMeResult:
    message_id: int
    deleted_by: int
    was_sender: bool


@dataclass
class MarkReadResult:
    marked_message_ids: list[int]
    sender_ids_to_notify: dict[int, list[int]]  # {sender_id: [message_id, ...]}
    marked_count: int


def delete_message_for_everyone(*, user_id: int, message_id: int) -> DeleteForEveryoneResult:
    """
    Mirrors MessageWebSocketManager's handle_delete_for_everyone exactly:
    sender-only, within DELETE_FOR_EVERYONE_WINDOW_SECONDS (5 min) of
    sent_at. Sets deleted_by_sender/deleted_by_receiver AND rewrites body
    — NOT is_deleted (see module docstring for why this matters: matching
    WS's data model, not REST's old, incompatible one, is the actual fix
    here, not just adding a broadcast on top of REST's old behavior).

    Raises MessageNotFoundError, PermissionDeniedError, or
    DeleteWindowExpiredError.
    """
    message = Message.query.get(message_id)
    if not message:
        raise MessageNotFoundError("Message not found")
    if message.sender_id != user_id:
        raise PermissionDeniedError("Unauthorized")

    seconds_old = (datetime.datetime.utcnow() - message.sent_at).total_seconds()
    if seconds_old > DELETE_FOR_EVERYONE_WINDOW_SECONDS:
        raise DeleteWindowExpiredError(
            f"Can only delete messages within {DELETE_FOR_EVERYONE_WINDOW_SECONDS // 60} minutes"
        )

    message.deleted_by_sender = True
    message.deleted_by_receiver = True
    message.body = "[Message deleted]"
    db.session.commit()

    return DeleteForEveryoneResult(
        message_id=message_id,
        sender_id=message.sender_id,
        receiver_id=message.receiver_id,
    )


def delete_message_for_me(*, user_id: int, message_id: int) -> DeleteForMeResult:
    """
    Mirrors MessageWebSocketManager's handle_delete_for_me exactly:
    sender-or-receiver, sets the matching deleted_by_* flag only. No time
    window — matches both REST's and WS's existing behavior (neither ever
    enforced one for this action; only delete-for-everyone has a window).

    Raises MessageNotFoundError or PermissionDeniedError.
    """
    message = Message.query.get(message_id)
    if not message:
        raise MessageNotFoundError("Message not found")

    if message.sender_id == user_id:
        message.deleted_by_sender = True
        was_sender = True
    elif message.receiver_id == user_id:
        message.deleted_by_receiver = True
        was_sender = False
    else:
        raise PermissionDeniedError("Unauthorized")

    db.session.commit()

    return DeleteForMeResult(message_id=message_id, deleted_by=user_id, was_sender=was_sender)


def mark_messages_read(*, user_id: int, message_ids: list[int]) -> MarkReadResult:
    """
    Mirrors MessageWebSocketManager's handle_mark_read: bulk-marks the
    given message_ids as read (only those actually addressed to user_id
    and not already read — a stricter, correctness-preserving filter the
    original WS handler already had via its query, kept identical here),
    then groups the marked messages by original sender so the caller can
    notify each sender's room once with just their own message_ids.

    Deliberately accepts a LIST of message_ids (matching the WS handler's
    payload shape) rather than a single message_id, so this one function
    serves both REST callers: mark_message_read (single-id, wraps it in a
    one-item list) and mark_all_read (every currently-unread id from a
    given partner, resolved by the caller before calling this).

    Returns marked_count for the REST callers' existing
    counter_cache_service.decrement_unread_message_count(...) call sites,
    which stay in each REST route (not moved here — see
    thread_message_service.py's precedent of keeping transport/
    infra-specific side effects, like Redis counters, out of the shared
    core, though here it's arguably not transport-specific so much as
    "already correct and no need to relocate a working call site").

    Raises nothing — an empty or all-already-read message_ids list simply
    yields an empty result, matching both original implementations'
    early-return-on-empty behavior.
    """
    if not message_ids:
        return MarkReadResult(marked_message_ids=[], sender_ids_to_notify={}, marked_count=0)

    to_mark = Message.query.filter(
        Message.id.in_(message_ids),
        Message.receiver_id == user_id,
        Message.is_read == False,
    ).all()

    if not to_mark:
        return MarkReadResult(marked_message_ids=[], sender_ids_to_notify={}, marked_count=0)

    now = datetime.datetime.utcnow()
    sender_map: dict[int, list[int]] = {}
    marked_ids: list[int] = []

    for msg in to_mark:
        msg.is_read = True
        msg.read_at = now
        marked_ids.append(msg.id)
        sender_map.setdefault(msg.sender_id, []).append(msg.id)

    db.session.commit()

    return MarkReadResult(
        marked_message_ids=marked_ids,
        sender_ids_to_notify=sender_map,
        marked_count=len(marked_ids),
    )
