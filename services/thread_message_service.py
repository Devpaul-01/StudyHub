"""
services/thread_message_service.py

Single source of truth for thread-message CREATE / EDIT / DELETE logic —
validation, persistence, and broadcast — shared between the WebSocket
handler (services/websocket_threads.py) and the REST fallback
(routes/student/threads/messaging.py).

WHY THIS FILE EXISTS (Batch 2 of the horizontal-scaling refactor; see
01-DESIGN-horizontal-scaling.md §13):

Reading messaging.py's REST send/edit/delete against
websocket_threads.py's WS equivalents turned up real, accumulated
behavioral drift, not just "the REST version is a simpler subset":

  - REST send_thread_message never broadcast to the thread room at all —
    a WS-connected member never saw a REST-sent message arrive live.
  - REST send_thread_message had no attachment support, no reply_to_id,
    no rate limit, no presence-based initial status (always hardcoded
    'sent'), no Learnora trigger detection.
  - REST edit_thread_message didn't broadcast thread_message_edited,
    didn't enforce the 15-minute edit window, didn't allow the
    moderator-bypass WS allows.
  - REST delete_thread_message's permission check was STRICTER than WS's
    (WS allows moderator-or-creator-or-sender; REST only allowed
    creator-or-sender) — a real behavioral divergence, not just a
    missing broadcast.

Duplicating the WS handler's ~150 lines of persist+broadcast logic a
second time inside messaging.py would only recreate this exact drift risk
on day one — the next time either file is touched, the two would start
disagreeing again. Instead, this module is the ONE place that logic lives;
both callers invoke it and differ only in what's genuinely specific to
their transport:

  - WS-only: emit()-based rate-limit rejection to the calling socket,
    client_temp_id echo/ack, Learnora background-thread dispatch staying
    in the WS handler (the "trigger a fire-and-forget background AI
    reply" behavior is arguably a chat-livevness feature that doesn't
    obviously belong on a synchronous REST fallback endpoint — flagged as
    a deliberate scope decision below, not an oversight).
  - REST-only: HTTP status codes / error_response() shape instead of
    thread_error emits.

Everything else — membership checks, thread-open checks, reply_to_id
validation, attachment handling, presence-based initial status,
sanitization, the DB writes themselves, the moderator-or-creator
permission model, and the broadcast/notify calls — lives here exactly
once.

DESIGN: this module raises typed exceptions (ThreadMessageError and its
subclasses) rather than returning Flask responses or emitting Socket.IO
events itself, so it has no transport-layer opinion. Each caller catches
these and translates to its own protocol (HTTP error_response vs.
thread_error emit). This mirrors the existing errors.py /
AppError pattern already used elsewhere in this codebase (see
app.py::handle_app_error) — same shape, scoped to this domain.

This module does NOT import flask_socketio or anything WS-specific, and
does NOT import routes.student.helpers (REST-specific). It imports only
current_app (safe in both an HTTP request context and a Flask-SocketIO
event context — confirmed by the pre-existing WS handler already relying
on this for its Learnora background-thread dispatch), db, models, and
bleach — all already transport-agnostic in this codebase.
"""

from __future__ import annotations

import datetime
import re as _re
from dataclasses import dataclass, field

import bleach
from flask import current_app
from sqlalchemy import func as _sa_func

from extensions import db
from models import (
    Thread, ThreadMember, ThreadMessage, ThreadMessageAttachment,
    Mention, Notification, User,
)
from services import presence_service

MAX_MESSAGE_LENGTH = 5_000
MAX_PINS_PER_THREAD = 5
EDIT_WINDOW_SECONDS = 900
MAX_ATTACHMENTS = 5


# ============================================================================
# TYPED ERRORS — transport-agnostic; each caller translates to its own shape
# ============================================================================

class ThreadMessageError(Exception):
    """Base class. `code` is a short machine-readable tag callers can
    branch on if they want a specific HTTP status per case; `message` is
    the human-readable string both callers already show as-is today."""
    code = "error"

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class NotAMemberError(ThreadMessageError):
    code = "not_a_member"


class ThreadNotFoundError(ThreadMessageError):
    code = "thread_not_found"


class ThreadClosedError(ThreadMessageError):
    code = "thread_closed"


class ValidationFailedError(ThreadMessageError):
    code = "validation_failed"


class MessageNotFoundError(ThreadMessageError):
    code = "message_not_found"


class PermissionDeniedError(ThreadMessageError):
    code = "permission_denied"


class EditWindowExpiredError(ThreadMessageError):
    code = "edit_window_expired"


# ============================================================================
# RESULT SHAPES
# ============================================================================

@dataclass
class CreateMessageResult:
    message: ThreadMessage
    payload: dict                    # exact shape _build_message_payload produces
    other_member_ids: list[int]      # for thread_list_update fan-out
    mentioned_user_ids: list[int]
    matched_ai_trigger: dict | None  # personality dict if @mention-triggered, else None
    text_content: str                # sanitized, for the caller's own Learnora dispatch
    attachments_data: list[dict] = field(default_factory=list)


@dataclass
class EditMessageResult:
    message: ThreadMessage
    thread_id: int


@dataclass
class DeleteMessageResult:
    message_id: int
    thread_id: int
    deleted_by: int
    original_sender_id: int  # who actually sent the message, for the
                              # caller's own "own vs moderation" delete-type
                              # logging distinction (deleted_by == this
                              # means a self-delete, otherwise a
                              # moderator/creator deleting someone else's)


# ============================================================================
# SHARED HELPERS (moved here verbatim from websocket_threads.py so both
# callers see identical behavior — not reimplemented)
# ============================================================================

def _sanitize(text: str) -> str:
    """Strip all HTML — plain text only in thread messages. Identical to
    ThreadWebSocketManager._sanitize; centralized here so both callers use
    the exact same sanitization, not two copies that could drift."""
    if not text:
        return text
    return bleach.clean(text, tags=[], strip=True).strip()


def _is_member(thread_id: int, user_id: int) -> "ThreadMember | None":
    return ThreadMember.query.filter_by(
        thread_id=thread_id, student_id=user_id
    ).first()


def _is_moderator_or_creator(membership: "ThreadMember") -> bool:
    return membership.role in ("creator", "moderator")


def _parse_mentions(text: str) -> list[int]:
    """
    Identical logic to ThreadWebSocketManager._parse_mentions, minus the
    verbose per-token MENTION_DEBUG logging that function has (that
    logging was clearly added for interactively debugging the regex/
    lookup during development — kept in the WS file since removing
    logging isn't part of this batch's scope, but not worth replicating
    here; this module logs only the final result, same as the WS
    function's own final [MENTION_RESULT] line).
    """
    if not text:
        return []

    pattern = r"@([\w\u00C0-\u00FF]+)"
    raw_matches = _re.findall(pattern, text)
    mentioned_names = set(raw_matches)

    found_ids = []
    for username in mentioned_names:
        if username == "learnora":
            continue
        user = User.query.filter(
            _sa_func.lower(User.username) == username.lower()
        ).first()
        if user:
            found_ids.append(user.id)
        if len(found_ids) >= 20:
            current_app.logger.warning(
                f"[MENTION_CAPPED] text={text[:100]!r} found={len(found_ids)}"
            )
            break

    if found_ids:
        current_app.logger.info(
            f"[MENTION_RESULT] text={text[:100]!r} found_mentions={found_ids}"
        )
    return found_ids


def _build_message_payload(msg: "ThreadMessage", sender: "User") -> dict:
    """
    Verbatim copy of ThreadWebSocketManager._build_message_payload's
    logic. Duplicated (not imported from websocket_threads.py) because
    this module must not import websocket_threads — that file imports
    flask_socketio and constructs Socket.IO-specific state
    (ThreadWebSocketManager instances); importing it from here would pull
    WS-only dependencies into what's meant to be a transport-agnostic
    module, and would risk a circular import once websocket_threads.py
    itself calls into this module below.

    Both copies must be kept in sync if the payload shape ever changes —
    flagged here explicitly rather than silently duplicated. Given this
    is the exact "duplication causes drift" problem this whole module
    exists to solve, the intent is that a future payload-shape change
    updates THIS copy and websocket_threads.py::_build_message_payload is
    then changed to call this one instead of keeping its own — not
    tackled in this batch since it means changing
    ThreadWebSocketManager's broadcast call sites too, and this batch's
    goal is closing the REST/WS behavioral gap, not restructuring the WS
    file itself.
    """
    reply_preview = None
    if msg.reply_to_id:
        parent = ThreadMessage.query.get(msg.reply_to_id)
        if parent:
            parent_sender = User.query.get(parent.sender_id)
            reply_preview = {
                "id":        parent.id,
                "text":      parent.text_content[:120],
                "sender":    parent_sender.name if parent_sender else "Unknown",
                "sender_id": parent.sender_id
            }

    reactions = _build_reactions(msg.id)

    attachments_list = []
    try:
        atts = msg.attachments.order_by(ThreadMessageAttachment.sort_order).all()
        attachments_list = [a.to_dict() for a in atts]
    except Exception:
        pass

    if not attachments_list and msg.attachment_url:
        attachments_list = [{
            "attachment_url":  msg.attachment_url,
            "attachment_name": msg.attachment_name,
            "attachment_type": msg.attachment_type,
            "attachment_size": msg.attachment_size,
            "sort_order":      0,
        }]

    return {
        "id":              msg.id,
        "thread_id":       msg.thread_id,
        "text_content":    msg.text_content,
        "sender_id":       msg.sender_id,
        "sender": {
            "id":       sender.id,
            "name":     sender.name,
            "username": sender.username,
            "avatar":   sender.avatar
        },
        "is_ai_response":  msg.is_ai_response,
        "is_edited":       msg.is_edited,
        "is_pinned":       msg.is_pinned,
        "reply_to":        reply_preview,
        "reply_to_id":     msg.reply_to_id,
        "attachments":     attachments_list,
        "attachment_url":  attachments_list[0]["attachment_url"]  if attachments_list else None,
        "attachment_name": attachments_list[0]["attachment_name"] if attachments_list else None,
        "attachment_type": attachments_list[0]["attachment_type"] if attachments_list else None,
        "attachment_size": attachments_list[0]["attachment_size"] if attachments_list else None,
        "reactions":       reactions,
        "status":          getattr(msg, "status", "sent"),
        "sent_at":         msg.sent_at.isoformat() + "Z",
        "edited_at":       msg.edited_at.isoformat() + "Z" if msg.edited_at else None,
    }


def _build_reactions(message_id: int) -> dict:
    from models import ThreadMessageReaction
    rows = ThreadMessageReaction.query.filter_by(message_id=message_id).all()
    grouped: dict[str, dict] = {}
    for r in rows:
        if r.emoji not in grouped:
            grouped[r.emoji] = {"emoji": r.emoji, "count": 0, "users": []}
        grouped[r.emoji]["count"] += 1
        grouped[r.emoji]["users"].append(r.user_id)
    return grouped


# AI trigger detection — identical trigger map to websocket_threads.py.
# Duplicated for the same reason _build_message_payload is (no import of
# websocket_threads.py from here) — see that function's docstring.
_AI_PERSONALITIES_FOR_TRIGGER = {
    "@learnora":  {"trigger": "@learnora",  "display_name": "Learnora",  "key": "learnora"},
    "@teacherai": {"trigger": "@teacherai", "display_name": "TeacherAI", "key": "teacherai"},
    "@coderai":   {"trigger": "@coderai",   "display_name": "CoderAI",   "key": "coderai"},
    "@productai": {"trigger": "@productai", "display_name": "ProductAI", "key": "productai"},
    "@funnyai":   {"trigger": "@funnyai",   "display_name": "FunnyAI",   "key": "funnyai"},
}


def detect_ai_trigger(text_content: str) -> dict | None:
    """Returns the matched personality dict if the text contains an AI
    trigger phrase, else None. Callers that want to dispatch Learnora
    (currently only the WS handler — see module docstring's "WS-only"
    list) check this on the returned CreateMessageResult.matched_ai_trigger."""
    lower = text_content.lower()
    return next(
        (p for t, p in _AI_PERSONALITIES_FOR_TRIGGER.items() if t in lower),
        None,
    )


# ============================================================================
# CREATE
# ============================================================================

def create_thread_message(
    *,
    user_id: int,
    thread_id: int,
    text_content: str,
    reply_to_id: int | None = None,
    attachments_data: list[dict] | None = None,
) -> CreateMessageResult:
    """
    Validate, persist, and prepare a new thread message. Does NOT emit or
    broadcast anything — that stays the caller's job (WS broadcasts via
    self.broadcast_to_thread/notify_user; REST will do the identical
    thing via the same ThreadWebSocketManager instance — see messaging.py
    for how it reaches it).

    Mirrors ThreadWebSocketManager.handle_send_thread_message's logic
    exactly for: membership check, thread-open check, reply_to_id
    validation (silently dropped if invalid — matching the WS behavior's
    own comment on this), MAX_MESSAGE_LENGTH, MAX_ATTACHMENTS capping,
    sanitization, presence-based initial status, the ThreadMessage +
    ThreadMessageAttachment writes, the atomic counter updates, and
    mention detection/notification creation.

    Deliberately excludes: rate limiting (transport-specific — the WS
    handler's rate limit is a request-frequency control tied to a
    persistent socket connection; a REST caller has no equivalent
    connection to rate-limit against beyond the existing WRITE_HEAVY
    Flask-Limiter tier messaging.py's blueprint already applies at the
    route-decorator level — so REST already has its own, differently-
    shaped protection and doesn't need this module to also apply the
    socket-specific one). client_temp_id handling (WS-only concept, no
    REST equivalent — REST already gets a real ID back synchronously in
    the HTTP response, so there's nothing to reconcile).

    Raises NotAMemberError, ThreadNotFoundError, ThreadClosedError, or
    ValidationFailedError. Never returns partial state — any raise happens
    before any db.session.add.
    """
    membership = _is_member(thread_id, user_id)
    if not membership:
        raise NotAMemberError("You are not a member of this thread")

    thread = Thread.query.get(thread_id)
    if not thread:
        raise ThreadNotFoundError("Thread not found")
    if not thread.is_open:
        raise ThreadClosedError("This thread is closed")

    text_content = (text_content or "").strip()
    attachments_data = list(attachments_data or [])

    if len(attachments_data) > MAX_ATTACHMENTS:
        attachments_data = attachments_data[:MAX_ATTACHMENTS]
        current_app.logger.warning(
            f"[MESSAGE_ATTACHMENTS_CAPPED] user_id={user_id} thread_id={thread_id} "
            f"capped_at={MAX_ATTACHMENTS}"
        )

    has_attachment = bool(attachments_data)

    if not text_content and not has_attachment:
        raise ValidationFailedError("Message must have text or an attachment")

    if len(text_content) > MAX_MESSAGE_LENGTH:
        raise ValidationFailedError(
            f"Message too long (max {MAX_MESSAGE_LENGTH} characters)"
        )

    if reply_to_id:
        parent = ThreadMessage.query.filter_by(
            id=reply_to_id, thread_id=thread_id, is_deleted=False
        ).first()
        if not parent:
            current_app.logger.debug(
                f"[MESSAGE_REPLY_REF_INVALID] user_id={user_id} thread_id={thread_id} "
                f"reply_to_id={reply_to_id} — silently cleared"
            )
            reply_to_id = None

    text_content = _sanitize(text_content) if text_content else ""

    # ── Presence-based initial status (identical to the WS handler's
    # logic — see websocket_threads.py's own comment on why this uses the
    # batch presence_service calls, not N individual lookups) ───────────
    members_except_sender = ThreadMember.query.filter(
        ThreadMember.thread_id == thread_id,
        ThreadMember.student_id != user_id
    ).all()
    other_ids = [m.student_id for m in members_except_sender]

    active_thread_by_id = presence_service.get_active_threads_batch(other_ids)
    online_ids = presence_service.get_online_user_ids(other_ids)

    active_viewers = [mid for mid in other_ids if active_thread_by_id.get(mid) == thread_id]
    online_non_viewers = [
        mid for mid in other_ids
        if mid in online_ids and active_thread_by_id.get(mid) != thread_id
    ]

    if active_viewers:
        initial_status = "read"
    elif online_non_viewers:
        initial_status = "delivered"
    else:
        initial_status = "sent"

    legacy_first = attachments_data[0] if attachments_data else {}

    msg = ThreadMessage(
        thread_id       = thread_id,
        sender_id       = user_id,
        text_content    = text_content,
        reply_to_id     = reply_to_id,
        attachment_url  = legacy_first.get("attachment_url"),
        attachment_name = legacy_first.get("attachment_name"),
        attachment_type = legacy_first.get("attachment_type"),
        attachment_size = legacy_first.get("attachment_size"),
        is_ai_response  = False,
        status          = initial_status,
        sent_at         = datetime.datetime.utcnow(),
    )
    db.session.add(msg)
    db.session.flush()

    for att_idx, att in enumerate(attachments_data):
        att_url = att.get("attachment_url", "")
        if not att_url:
            continue
        db.session.add(ThreadMessageAttachment(
            message_id      = msg.id,
            attachment_url  = att_url,
            attachment_name = att.get("attachment_name"),
            attachment_type = att.get("attachment_type"),
            attachment_size = att.get("attachment_size"),
            sort_order      = att_idx,
        ))

    ThreadMember.query.filter_by(
        thread_id=thread_id, student_id=user_id
    ).update(
        {ThreadMember.messages_sent: ThreadMember.messages_sent + 1},
        synchronize_session=False,
    )
    Thread.query.filter_by(id=thread_id).update(
        {
            Thread.message_count: Thread.message_count + 1,
            Thread.last_activity: datetime.datetime.utcnow(),
        },
        synchronize_session=False,
    )

    mentioned_ids = _parse_mentions(text_content)
    for mid in mentioned_ids:
        if mid == user_id:
            continue
        db.session.add(Mention(
            mentioned_in_type    = "thread_message",
            mentioned_in_id      = msg.id,
            mentioned_user_id    = mid,
            mentioned_by_user_id = user_id,
        ))
        db.session.add(Notification(
            user_id           = mid,
            title             = "You were mentioned in a thread",
            body              = f"{text_content[:80]}...",
            notification_type = "thread_mention",
            related_type      = "thread",
            related_id        = thread_id,
        ))

    db.session.commit()

    sender = User.query.get(user_id)
    payload = _build_message_payload(msg, sender)

    return CreateMessageResult(
        message=msg,
        payload=payload,
        other_member_ids=other_ids,
        mentioned_user_ids=mentioned_ids,
        matched_ai_trigger=detect_ai_trigger(text_content),
        text_content=text_content,
        attachments_data=attachments_data,
    )


# ============================================================================
# EDIT
# ============================================================================

def edit_thread_message(*, user_id: int, message_id: int, new_text: str) -> EditMessageResult:
    """
    Mirrors ThreadWebSocketManager.handle_edit_thread_message exactly:
    sender-only ownership (no moderator bypass for editing — matching the
    WS handler's own membership.role check, which only exempts
    moderator/creator from the 15-MINUTE WINDOW, not from ownership
    itself: `msg.sender_id == user_id` is still required by the query
    filter below regardless of role), AI messages are never editable,
    and moderators/creators are exempt from EDIT_WINDOW_SECONDS (everyone
    else is bound by it).

    This closes the gap flagged in the design doc §13: REST previously
    had none of this — no window enforcement, no broadcast, no
    moderator-bypass logic at all (it didn't check role in any way).

    Raises ValidationFailedError, MessageNotFoundError, or
    EditWindowExpiredError.
    """
    new_text = (new_text or "").strip()
    if not new_text:
        raise ValidationFailedError("text_content required")
    if len(new_text) > MAX_MESSAGE_LENGTH:
        raise ValidationFailedError(f"Message too long (max {MAX_MESSAGE_LENGTH} chars)")

    msg = ThreadMessage.query.filter_by(
        id=message_id, sender_id=user_id, is_deleted=False
    ).first()
    if not msg:
        raise MessageNotFoundError("Message not found or you don't own it")

    if msg.is_ai_response:
        raise PermissionDeniedError("AI messages cannot be edited")

    membership = _is_member(msg.thread_id, user_id)
    if membership and not _is_moderator_or_creator(membership):
        seconds_old = (datetime.datetime.utcnow() - msg.sent_at).total_seconds()
        if seconds_old > EDIT_WINDOW_SECONDS:
            raise EditWindowExpiredError("Edit window expired (15 minutes)")

    msg.text_content = _sanitize(new_text)
    msg.is_edited    = True
    msg.edited_at    = datetime.datetime.utcnow()
    db.session.commit()

    return EditMessageResult(message=msg, thread_id=msg.thread_id)


# ============================================================================
# DELETE
# ============================================================================

def delete_thread_message(*, user_id: int, message_id: int) -> DeleteMessageResult:
    """
    Mirrors ThreadWebSocketManager.handle_delete_thread_message exactly:
    sender OR moderator-or-creator may delete (soft delete, text replaced
    with "[deleted]", message_count decremented via the same CASE-guarded
    SQL update the WS handler uses so it never goes negative).

    This closes the REAL divergence flagged in the design doc §13: REST's
    old permission check (`message.sender_id != current_user.id and
    thread.creator_id != current_user.id`) was STRICTER than WS's — a
    thread moderator (not creator) could delete via WebSocket but got a
    403 via the REST fallback for the identical action. That's not a
    missing-broadcast gap, it's a genuine behavioral bug this module
    fixes by giving both callers the exact same permission model.

    Raises MessageNotFoundError or PermissionDeniedError.
    """
    msg = ThreadMessage.query.filter_by(id=message_id, is_deleted=False).first()
    if not msg:
        raise MessageNotFoundError("Message not found")

    membership = _is_member(msg.thread_id, user_id)
    if not membership:
        raise PermissionDeniedError("Not a thread member")

    is_own_message = msg.sender_id == user_id
    is_privileged  = _is_moderator_or_creator(membership)
    original_sender_id = msg.sender_id

    if not is_own_message and not is_privileged:
        raise PermissionDeniedError("You cannot delete this message")

    msg.is_deleted   = True
    msg.text_content = "[deleted]"

    from sqlalchemy import case
    Thread.query.filter_by(id=msg.thread_id).update(
        {Thread.message_count: case(
            (Thread.message_count > 0, Thread.message_count - 1),
            else_=0
        )},
        synchronize_session=False,
    )
    db.session.commit()

    return DeleteMessageResult(
        message_id=message_id,
        thread_id=msg.thread_id,
        deleted_by=user_id,
        original_sender_id=original_sender_id,
    )
