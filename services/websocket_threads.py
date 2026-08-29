"""
Thread WebSocket Manager — PRODUCTION
Handles real-time group chat for the Thread (study group) system.

Features:
- Room-based architecture  → one SocketIO room per thread ("thread_{id}")
- JWT auth shared with MessageWebSocketManager (no duplicate auth state)
- Send / broadcast messages with full metadata
- Typing indicators per thread (multi-user aware)
- Emoji reactions  (add / toggle / remove)
- Reply-to-message (quoted context)
- Pin / unpin messages (creator + moderator)
- Edit and delete messages (ownership + role checks)
- Mark thread as read
- @mention detection + Notification creation
- Learnora (AI) trigger on @learnora mention — runs async so send never blocks

Fixes merged vs original:
  - join_thread_room: also joins user-specific room f"user_{user_id}"
    (enables per-user delivered/read status pushes)
  - _build_message_payload: includes `status` field ('sent'|'delivered'|'read')
  - send_thread_message: per-user sliding-window rate limiter added
  - mark_thread_read: upserts ThreadMessageReadReceipt rows and emits
    message_status_updated to each original sender's personal room
  - message_delivered handler: new — upgrades status sent→delivered,
    pushes message_status_updated to sender's personal room
  - _call_learnora_for_thread: uses app.config["LEARNORA_BOT_USER_ID"]
    with a hard guard so missing config never crashes the handler

Architecture:
  Shares the SocketIO instance created by MessageWebSocketManager.
  Auth state (socket_to_user) is read from message_ws_manager.
  Both managers register handlers on the same socketio object.

HORIZONTAL SCALING (see 01-DESIGN-horizontal-scaling.md §6):
  - user_active_thread (below) moved to Redis via services.presence_service
    — it was being read in send_thread_message to decide a NEW message's
    initial delivery status ('read' vs 'delivered' vs 'sent'), which needs
    to be correct regardless of which instance the relevant member's
    socket is connected to.
  - _thread_msg_rate_limiter now uses RedisFixedWindowLimiter instead of
    the in-memory RateLimiter, since a per-user thread-message cap is a
    security control that must hold even if a user's reconnects land on a
    different instance each time (see websocket_rate_limiter.py).
  - message_ws_manager.online_users reads (deciding a message's initial
    status) switched to presence_service.is_user_online for the same
    reason.
  - AUDIT §14.3 FIX: _ai_action_buckets has been converted to real,
    Redis-backed rate limiting (_ai_action_rate_limiter, same
    RedisFixedWindowLimiter class as _thread_msg_rate_limiter above) —
    it was previously declared but never actually read or written
    anywhere, meaning thread_ai_action had NO rate limit at all prior to
    this fix. _auto_reply_buckets remains the one still-deferred item —
    see its own declaration below for why it wasn't folded into this
    same fix (it's a genuine, working, process-local sliding-window
    limiter that would need its own migration, not dead code with no
    behavior to build from scratch).
  - Broadcasting (broadcast_to_thread, notify_user) needed ZERO changes —
    both already just call self.socketio.emit(..., room=X), which becomes
    cross-instance-correct once message_queue is set in
    websocket_messages.py::init_app (this file shares that socketio
    instance via init_socketio).

Usage in app factory:
  from websocket_messages import message_ws_manager, init_message_websocket
  from websocket_threads import thread_ws_manager

  socketio = init_message_websocket(app)
  thread_ws_manager.init_socketio(app, socketio)
"""

import threading
from concurrent.futures import ThreadPoolExecutor
import datetime
import time
import os

from flask_socketio import emit, join_room, leave_room
from flask import request, current_app

# HORIZONTAL SCALING BATCH 3: `from sqlalchemy import func` (used only by
# the now-removed _parse_mentions class method's func.lower(...) call) was
# removed as a dead import alongside it — see the comment above
# _build_reactions for what replaced _parse_mentions.

# H-10 fix: reuse the shared, lock-protected rate limiter/typing tracker
# instead of the ad hoc, unlocked module-level dicts that used to live in
# this file (_send_buckets/_is_rate_limited and a bespoke ThreadTypingManager).
# Both are genuinely mutated from multiple threads under async_mode='threading',
# so an actual lock (which this shared implementation has) matters here.
#
# RedisFixedWindowLimiter (horizontal scaling): distributed replacement for
# the per-user thread-message rate limit specifically — see module
# docstring. TypingStatusManager is untouched; typing dedup stays local by
# design (design doc §2/§6.1). RateLimiter (the plain in-memory class) is
# NOT imported here — a Batch 2 comment previously claimed it was kept
# "for websocket_events.py compat," but that was inaccurate:
# websocket_events.py imports RateLimiter directly from
# services.websocket_rate_limiter itself, not from this file, so this file
# never needed to re-export it. Corrected in this batch.
from services.websocket_rate_limiter import TypingStatusManager, RedisFixedWindowLimiter

# Horizontal scaling: distributed presence (design doc §6.3).
from services import presence_service

# HORIZONTAL SCALING BATCH 3: shared thread-message create/edit/delete
# logic, previously duplicated between this file and the REST fallback
# (routes/student/threads/messaging.py) — see Batch 2 (design doc §13)
# for why the module exists, and this batch's own module-docstring note
# above for why THIS file now also calls into it rather than keeping its
# own separate copy of the same validation/persist/broadcast logic.
# Aliased _tms (not `thread_message_service`) purely to keep the many
# `_tms.SomeError` references below short and visually distinct from this
# file's own `_`-prefixed local helpers (_is_member, _sanitize, etc.).
from services import thread_message_service as _tms

from extensions import db
from models import (
    User, Thread, ThreadMember, ThreadMessage,
    ThreadMessageReaction, ThreadMessageReadReceipt,
)
# HORIZONTAL SCALING BATCH 3: ThreadMessageAttachment, Mention, and
# Notification were removed from the import above — each was used only
# by the now-removed _build_message_payload / _parse_mentions class
# methods (see the comment above _build_reactions) or by the inline
# mention/notification-creation logic inside the old
# handle_send_thread_message body, all of which now live in
# services/thread_message_service.py instead. Confirmed zero remaining
# references to any of the three in this file before removing them.

import logging
logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTS
# ============================================================================

MAX_MESSAGE_LENGTH   = 5_000
MAX_PINS_PER_THREAD  = 5
EDIT_WINDOW_SECONDS  = 900

AI_PERSONALITIES = {
    "learnora":  {"trigger": "@learnora",  "display_name": "Learnora",  "key": "learnora",
                  "system_prompt": "You are Learnora, a helpful AI study assistant. Be concise (2-4 sentences). Be helpful, not lecture-heavy."},
    "teacherai": {"trigger": "@teacherai", "display_name": "TeacherAI", "key": "teacherai",
                  "system_prompt": "You are TeacherAI, a patient educator. Explain concept → example → check understanding. Depth over brevity."},
    "coderai":   {"trigger": "@coderai",   "display_name": "CoderAI",   "key": "coderai",
                  "system_prompt": "You are CoderAI, a senior engineer. Always respond with working code in code blocks. Use modern idiomatic patterns."},
    "productai": {"trigger": "@productai", "display_name": "ProductAI", "key": "productai",
                  "system_prompt": "You are ProductAI, a product manager. Structure answers: Problem → Solution → Trade-offs → Recommendation."},
    "funnyai":   {"trigger": "@funnyai",   "display_name": "FunnyAI",   "key": "funnyai",
                  "system_prompt": "You are FunnyAI. Explain concepts using humor and pop culture. Educational but entertaining. Include at least one joke or analogy."},
}

_TRIGGER_MAP = {p["trigger"]: p for p in AI_PERSONALITIES.values()}
LEARNORA_TRIGGERS = list(_TRIGGER_MAP.keys())

# AUDIT §14.3 FIX (implemented per explicit approval — originally
# classified CAN WAIT UNTIL AFTER TESTING/deferred, promoted into scope):
# _ai_action_buckets removed. It was declared but never actually read or
# written anywhere in this file — the thread_ai_action handler applied NO
# rate limit at all prior to this fix, unlike every other AI-dispatch path
# in this module (send_thread_message's manual @mention trigger and
# reply-to-AI auto-continue both have real limits). Replaced with a
# genuine Redis-backed fixed-window limiter (_ai_action_rate_limiter,
# below, alongside _thread_msg_rate_limiter) rather than porting
# _auto_reply_buckets' in-memory sliding-window logic — the module
# docstring's own "FLAGGED but intentionally NOT changed this batch" note
# groups these two together, but they are not actually the same shape of
# fix: _auto_reply_buckets is a real, working, process-local sliding-window
# limiter that needed migrating to Redis for cross-instance correctness;
# _ai_action_buckets was inert dead code with no behavior to migrate. This
# builds a new control from scratch rather than reproducing the
# process-local variant's exact algorithm.
_auto_reply_buckets: dict = {}

# Background-jobs phase hardening (BACKGROUND_JOBS_IMPLEMENTATION.md
# §18): bounds concurrent Learnora background calls across ALL threads/
# actions combined, replacing the previous unbounded
# threading.Thread(...).start() calls. AI dispatch stays threaded, NOT
# moved to RQ, per explicit product decision (no frontend polling
# change) -- this is a hardening pass on the existing pattern, not a
# migration. Each in-flight call holds one DB connection from the pool
# for its duration (a few seconds, per an AI provider round trip), so
# this ceiling is chosen well under typical pool sizes to leave
# headroom for normal HTTP request traffic sharing the same pool.
# [DEFAULT -- TUNE LATER once real concurrent-AI-call volume is
# observed.]
_learnora_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="learnora-bg")

# Per-user rate limit  (env-overridable)
_RATE_LIMIT_MAX    = int(os.environ.get("THREAD_MSG_RATE_MAX",    "30"))
_RATE_LIMIT_WINDOW = int(os.environ.get("THREAD_MSG_RATE_WINDOW", "60"))  # seconds

# H-10 fix: thread-safe sliding-window limiter (was a plain, unlocked dict
# mutated from multiple SocketIO worker threads — a real data race under
# async_mode='threading'). One shared, namespaced key per user below keeps
# this isolated from any other feature that also uses the same RateLimiter.
#
# Horizontal scaling: swapped from the in-memory RateLimiter to
# RedisFixedWindowLimiter (see module docstring / websocket_rate_limiter.py).
# A per-user thread-message cap is a security control — an in-memory bucket
# meant a user who reconnects to a different instance got a fresh, unlinked
# limit each time, which defeats the point of the limit at any instance
# count above 1. The RateLimiter import above is kept (still used by the
# untouched websocket_events.py) but no longer used in THIS file.
_thread_msg_rate_limiter = RedisFixedWindowLimiter(key_prefix="sh:1:ws:msgrate")

# AUDIT §14.3 FIX: per-user rate limit for thread_ai_action (env-overridable,
# same convention as _RATE_LIMIT_MAX/_RATE_LIMIT_WINDOW above). Deliberately
# a SEPARATE limit from _RATE_LIMIT_MAX (thread messages) and the
# auto-reply-to-AI limit below — this gates the five explicit AI action
# buttons (summarize/translate/explain/to_code/fact_check), a materially
# different action from sending a chat message, so it gets its own budget
# rather than sharing/competing with the message-send limit.
#
# Default chosen conservatively: 10 actions per 5-minute window per user.
# Each action is a real AI-provider round trip (same cost profile as
# send_thread_message's @mention trigger), so this is deliberately tighter
# than the 30-per-60s message-send limit while still generous enough for
# normal use across all five action types. [DEFAULT -- TUNE LATER once
# real usage volume is observed, same tuning posture as
# _learnora_executor's max_workers above.]
_AI_ACTION_RATE_LIMIT_MAX    = int(os.environ.get("THREAD_AI_ACTION_RATE_MAX",    "10"))
_AI_ACTION_RATE_LIMIT_WINDOW = int(os.environ.get("THREAD_AI_ACTION_RATE_WINDOW", "300"))  # seconds

# Distinct Redis key namespace (sh:1:ws:aiaction, not sh:1:ws:msgrate) so
# this limiter's buckets never collide with _thread_msg_rate_limiter's —
# same class, same fixed-window algorithm, independent budget.
_ai_action_rate_limiter = RedisFixedWindowLimiter(key_prefix="sh:1:ws:aiaction")


# ============================================================================
# LOGGING HELPERS
# ============================================================================

def _summarize_payload(data: dict, max_text: int = 80) -> str:
    """
    Return a safe, compact one-line summary of an incoming WebSocket payload.

    Rules:
    - Truncates text_content / question to max_text chars
    - Replaces attachment_url with <present> / None — never logs signed URLs
    - Never serialises full reaction sets or sender objects
    - Safe to call on None / non-dict values
    """
    if not data or not isinstance(data, dict):
        return "{}"
    parts = []
    for key, val in data.items():
        if key in ("attachment_url", "attachment_data"):
            parts.append(f"{key}={'<present>' if val else 'None'}")
        elif key in ("text_content", "question") and isinstance(val, str):
            preview = val[:max_text].replace("\n", " ")
            suffix  = "…" if len(val) > max_text else ""
            parts.append(f'{key}="{preview}{suffix}"')
        elif key == "emoji":
            parts.append(f"emoji={val!r}")
        elif isinstance(val, (int, float, bool, type(None))):
            parts.append(f"{key}={val!r}")
        else:
            # Strings + anything else — truncate to 40 chars
            s = str(val)
            parts.append(f"{key}={s[:40]!r}{'…' if len(s) > 40 else ''}")
    return "{" + ", ".join(parts) + "}"


# ============================================================================
# MODULE-LEVEL HELPERS
# ============================================================================

def _user_room(user_id: int) -> str:
    """Personal SocketIO room for targeted per-user events (status ticks)."""
    return f"user_{user_id}"


def _is_rate_limited(user_id: int) -> bool:
    """
    Fixed-window rate limiter.
    Returns True (blocked) if the user has sent >= _RATE_LIMIT_MAX messages
    in the current _RATE_LIMIT_WINDOW-second window.

    HORIZONTAL SCALING: backed by RedisFixedWindowLimiter (see module
    docstring) — correct regardless of which application instance handles
    a given user's messages, not just safe across threads within one
    process. check_rate_limit's signature/return shape (allowed, remaining)
    is identical to the old in-memory RateLimiter's, so this call itself
    needed no change beyond the class swap above. Fails OPEN on Redis
    error (see RedisFixedWindowLimiter's docstring), matching this app's
    established rate-limiting precedent (rate_limit_service.py).
    """
    allowed, _remaining = _thread_msg_rate_limiter.check_rate_limit(
        key=f"thread_msg_{user_id}",
        limit=_RATE_LIMIT_MAX,
        window=_RATE_LIMIT_WINDOW,
    )
    return not allowed


def _is_ai_action_rate_limited(user_id: int) -> bool:
    """
    AUDIT §14.3 FIX: fixed-window rate limiter for thread_ai_action.
    Returns True (blocked) if the user has triggered >= _AI_ACTION_RATE_LIMIT_MAX
    AI actions (summarize/translate/explain/to_code/fact_check, combined) in
    the current _AI_ACTION_RATE_LIMIT_WINDOW-second window.

    Same RedisFixedWindowLimiter backing as _is_rate_limited above —
    correct regardless of which application instance handles a given
    user's action clicks. Fails OPEN on Redis error (see
    RedisFixedWindowLimiter's docstring), matching this app's established
    rate-limiting precedent (rate_limit_service.py, _is_rate_limited above).
    """
    allowed, _remaining = _ai_action_rate_limiter.check_rate_limit(
        key=f"thread_ai_action_{user_id}",
        limit=_AI_ACTION_RATE_LIMIT_MAX,
        window=_AI_ACTION_RATE_LIMIT_WINDOW,
    )
    return not allowed


# ============================================================================
# THREAD TYPING MANAGER
# ============================================================================

class ThreadTypingManager(TypingStatusManager):
    """
    Tracks per-thread typing state for all active users, keyed by thread_id
    (used as the shared TypingStatusManager's "conversation_key").

    H-10 fix: this used to be a standalone class mutating a plain
    `{ thread_id: { user_id: last_typed_at } }` dict with no locking, even
    though SocketIO event handlers can run concurrently on different
    threads under async_mode='threading'. It's now a thin, backward-compatible
    subclass of the shared, lock-protected TypingStatusManager — every
    existing call site (`set_typing(thread_id, user_id)`, `stop_typing(...)`,
    `cleanup_expired()`) keeps working unchanged.
    """

    def __init__(self, timeout: int = 3):
        super().__init__(timeout=timeout)

    def stop_typing(self, thread_id: int, user_id: int) -> None:
        """Alias for remove_typing — keeps the pre-existing call sites working."""
        self.remove_typing(thread_id, user_id)

    def is_typing(self, thread_id: int, user_id: int) -> bool:
        """Encapsulated membership check, replacing direct access to the
        (now private/locked) internal typing_status dict."""
        return user_id in self.get_typing_users(thread_id)


# ============================================================================
# THREAD WEBSOCKET MANAGER
# ============================================================================

class ThreadWebSocketManager:
    """
    Production WebSocket manager for the Thread group chat system.
    Registers all handlers on the shared SocketIO instance.
    """

    def __init__(self):
        self.socketio   = None
        self.app        = None
        self.typing_mgr = ThreadTypingManager(timeout=3)
        # ARCH-02: tracks which thread each connected user is actively
        # viewing. { user_id: thread_id }
        #
        # HORIZONTAL SCALING: this dict is DEPRECATED as of the horizontal
        # scaling refactor and is no longer read or written by any handler
        # below — services.presence_service.set_active_thread /
        # get_active_thread / clear_active_thread (Redis-backed) is now
        # the single source of truth, since this needs to be correct
        # regardless of which instance a given thread member's socket is
        # connected to (send_thread_message reads this to decide a new
        # message's initial delivery status for EVERY other member, not
        # just ones connected to the same instance as the sender). Left
        # declared (rather than removed) only in case any external
        # debugging/monitoring code reaches into it directly; nothing in
        # this file does anymore.
        self.user_active_thread: dict[int, int] = {}

    # ------------------------------------------------------------------ #
    # INIT                                                                 #
    # ------------------------------------------------------------------ #

    def init_socketio(self, app, socketio) -> None:
        """
        Attach to an existing SocketIO instance (created by message_ws_manager).
        Call this AFTER init_message_websocket().
        """
        self.socketio = socketio
        self.app      = app
        self.register_handlers()
        logger.info(
            "[THREAD_WS_INIT] Thread WebSocket handlers registered — "
            f"rate_limit={_RATE_LIMIT_MAX}msg/{_RATE_LIMIT_WINDOW}s "
            f"edit_window={EDIT_WINDOW_SECONDS}s "
            f"max_pins={MAX_PINS_PER_THREAD} "
            f"max_msg_len={MAX_MESSAGE_LENGTH}"
        )

    # ------------------------------------------------------------------ #
    # HELPERS                                                              #
    # ------------------------------------------------------------------ #

    def _get_current_user(self) -> int | None:
        """
        Read user_id from the shared socket->user map owned by
        MessageWebSocketManager.  Avoids duplicating auth state.
        """
        from services.websocket_messages import message_ws_manager
        return message_ws_manager.socket_to_user.get(request.sid)

    def _emit_error(self, message: str) -> None:
        """Emit a thread_error event and log it at DEBUG so error origins are traceable."""
        current_app.logger.debug(
            f"[THREAD_ERROR_EMITTED] sid={request.sid} message={message!r}"
        )
        emit("thread_error", {"message": message})

    def _is_member(self, thread_id: int, user_id: int) -> "ThreadMember | None":
        return ThreadMember.query.filter_by(
            thread_id=thread_id,
            student_id=user_id
        ).first()

    def _is_moderator_or_creator(self, membership: "ThreadMember") -> bool:
        return membership.role in ("creator", "moderator")

    # HORIZONTAL SCALING BATCH 3: _sanitize, _parse_mentions, and
    # _build_message_payload — previously defined here — were removed as
    # dead code once handle_send_thread_message (this class's only caller
    # of all three, confirmed by inspection before removal) was refactored
    # to call services.thread_message_service.create_thread_message
    # instead, which performs the identical sanitize/parse-mentions/
    # build-payload logic internally. See that module for the current
    # implementations — _sanitize -> thread_message_service._sanitize,
    # _parse_mentions -> thread_message_service._parse_mentions,
    # _build_message_payload -> thread_message_service._build_message_payload
    # (that module's docstring explains why it keeps its own copy rather
    # than this file importing it). _build_reactions below is UNCHANGED
    # and still lives here — it has two other callers in this file
    # (add_thread_reaction / remove_thread_reaction) that were never part
    # of this batch's scope.

    def _build_reactions(self, message_id: int) -> dict:
        """Return grouped reaction counts for a message."""
        rows    = ThreadMessageReaction.query.filter_by(message_id=message_id).all()
        grouped: dict[str, dict] = {}
        for r in rows:
            if r.emoji not in grouped:
                grouped[r.emoji] = {"emoji": r.emoji, "count": 0, "users": []}
            grouped[r.emoji]["count"] += 1
            grouped[r.emoji]["users"].append(r.user_id)
        return grouped

    def broadcast_to_thread(self, thread_id: int, event: str, data: dict) -> None:
        """
        Emit an event to every connected member of a thread room.
        Logs every outbound broadcast so emission gaps are immediately visible.
        """
        if self.socketio:
            current_app.logger.debug(
                f"[THREAD_EMIT] event={event!r} "
                f"room=thread_{thread_id} "
                f"payload_keys={list(data.keys())}"
            )
            self.socketio.emit(event, data, room=f"thread_{thread_id}")

    def notify_user(self, user_id: int, event: str, data: dict) -> None:
        """
        Emit a targeted event to a single user's personal room.
        Logs every targeted push so missed status ticks are diagnosable.
        """
        if self.socketio:
            current_app.logger.debug(
                f"[USER_EMIT] event={event!r} "
                f"room=user_{user_id} "
                f"payload_keys={list(data.keys())}"
            )
            self.socketio.emit(event, data, room=_user_room(user_id))

    def broadcast_ai_message(self, thread_id: int, msg: "ThreadMessage", text: str) -> None:
        """
        Called from the Learnora background thread after saving the AI reply.
        Emits to the thread room so all connected members see it instantly.

        FIX: LEARNORA_BOT_USER_ID may not correspond to a real row in the
        users table (e.g. bot user not yet seeded in this environment). If
        User.query.get(...) returns None, fall back to a synthetic user
        object instead of leaking None-driven defaults silently — and log
        it, since a persistently missing bot user is worth noticing.
        """
        bot_user = User.query.get(msg.sender_id)

        if not bot_user:
            bot_user = type('DummyUser', (), {
                'id':       msg.sender_id,
                'name':     'Learnora',
                'username': 'learnora',
                'avatar':   None
            })()
            current_app.logger.debug(
                f"[LEARNORA_SYNTHETIC_USER] msg_id={msg.id} "
                f"user_id={msg.sender_id} using synthetic user object"
            )

        payload = {
            "id":             msg.id,
            "thread_id":      thread_id,
            "text_content":   text,
            "sender_id":      msg.sender_id,
            "sender": {
                "id":       msg.sender_id,
                "name":     bot_user.name,
                "username": bot_user.username,
                "avatar":   bot_user.avatar
            },
            "is_ai_response":  True,
            "is_edited":       False,
            "is_pinned":       False,
            "reply_to":        None,
            "reactions":       {},
            "status":          "sent",
            "sent_at":         msg.sent_at.isoformat() + "Z",
        }
        current_app.logger.info(
            f"[LEARNORA_BROADCAST] "
            f"thread_id={thread_id} msg_id={msg.id} "
            f"response_length={len(text)} "
            f"room=thread_{thread_id}"
        )
        self.broadcast_to_thread(thread_id, "new_thread_message", payload)

    # ------------------------------------------------------------------ #
    # REGISTER ALL HANDLERS                                               #
    # ------------------------------------------------------------------ #

    def register_handlers(self) -> None:

        sio = self.socketio

        # ================================================================
        # JOIN / LEAVE ROOM
        # ================================================================

        @sio.on("join_thread_room")
        def handle_join_thread_room(data):
            """
            Client calls this when opening a thread chat view.
            FIX: also joins user-specific room f"user_{user_id}" so that
                 message_status_updated events reach the sender's socket.

            Payload: { "thread_id": <int> }
            Emits back: "thread_room_joined"
            """
            sid     = request.sid
            user_id = self._get_current_user()

            if not user_id:
                current_app.logger.warning(
                    f"[THREAD_JOIN_AUTH_FAILED] "
                    f"sid={sid} payload={_summarize_payload(data)} "
                    f"reason=unauthenticated_socket"
                )
                self._emit_error("Authentication required")
                return

            thread_id = data.get("thread_id")

            current_app.logger.info(
                f"[THREAD_JOIN_ATTEMPT] "
                f"user_id={user_id} thread_id={thread_id} sid={sid}"
            )

            if not thread_id:
                current_app.logger.warning(
                    f"[THREAD_JOIN_INVALID] "
                    f"user_id={user_id} sid={sid} reason=missing_thread_id"
                )
                self._emit_error("thread_id required")
                return

            membership = self._is_member(thread_id, user_id)
            if not membership:
                current_app.logger.warning(
                    f"[THREAD_JOIN_DENIED] "
                    f"user_id={user_id} thread_id={thread_id} sid={sid} "
                    f"reason=not_a_member"
                )
                self._emit_error("You are not a member of this thread")
                return

            # Join shared thread room
            join_room(f"thread_{thread_id}")

            # Track active thread for presence-based status.
            # HORIZONTAL SCALING: writes to Redis via presence_service
            # instead of the local self.user_active_thread dict (now
            # deprecated — see __init__) so this is visible to
            # send_thread_message's status decision regardless of which
            # instance handles the sender's message.
            presence_service.set_active_thread(user_id, thread_id)

            # FIX: join personal room for targeted status push-events
            join_room(_user_room(user_id))

            # NOTE: last_read_at is intentionally NOT updated here.
            # Updating it to utcnow() would move the cutoff past all existing
            # messages, causing mark_thread_read to find nothing to mark.
            # mark_thread_read is the sole owner of last_read_at updates.

            current_app.logger.info(
                f"[THREAD_JOINED] "
                f"user_id={user_id} thread_id={thread_id} sid={sid} "
                f"role={membership.role} "
                f"rooms=[thread_{thread_id}, user_{user_id}]"
            )

            emit("thread_room_joined", {
                "thread_id": thread_id,
                "your_role": membership.role
            })

        # ----------------------------------------------------------------
        
        @sio.on("leave_thread_room")
        def handle_leave_thread_room(data):
            sid = request.sid
            user_id = self._get_current_user()

            if not user_id:
                return

            thread_id = data.get("thread_id")
            if not thread_id:
                return

            # Clear typing indicator
            was_typing = self.typing_mgr.is_typing(thread_id, user_id)
            self.typing_mgr.stop_typing(thread_id, user_id)

            # Clean up active thread tracking.
            # HORIZONTAL SCALING: clears via presence_service (Redis),
            # preserving the original's exact semantics — only clear if
            # the CURRENTLY-tracked active thread for this user is the one
            # being left (expected_thread_id=thread_id), so leaving a
            # background/stale thread room never clobbers a different,
            # newer active-thread value the user has since set elsewhere.
            presence_service.clear_active_thread(user_id, expected_thread_id=thread_id)

            leave_room(f"thread_{thread_id}")

            current_app.logger.info(
                f"[THREAD_LEFT] "
                f"user_id={user_id} thread_id={thread_id} sid={sid} "
                f"typing_indicator_cleared={was_typing}"
            )

        # ================================================================
        # SEND MESSAGE
        # ================================================================

        @sio.on("send_thread_message")
        def handle_send_thread_message(data):
            """
            Send a new message to a thread.

            HORIZONTAL SCALING BATCH 3: now delegates validation,
            persistence, presence-based initial status, attachment
            handling, and payload construction to
            services.thread_message_service.create_thread_message — the
            exact same shared module the REST fallback
            (routes/student/threads/messaging.py::send_thread_message)
            already calls (see Batch 2 / design doc §13). This closes the
            remaining WS-handler/service duplication Batch 2 explicitly
            flagged as left unaddressed.

            Kept HERE, deliberately NOT moved into the shared service
            (per Batch 2's own documented scope boundary — see
            thread_message_service.create_thread_message's docstring):
              - The per-socket rate limiter (RedisFixedWindowLimiter) and
                its thread_error-emit rejection path — a REST caller has
                no persistent connection to rate-limit against the same
                way; it already gets its own WRITE_HEAVY Flask-Limiter
                tier instead.
              - client_temp_id extraction/echo — a WS-only optimistic-UI
                reconciliation concept with no REST equivalent.
              - The `thread_list_update` personal-room fan-out, and the
                Learnora AI-trigger dispatch (both manual @mention and
                reply-to-AI auto-continue) — the fire-and-forget
                background thread's reply arrives back over THIS live
                socket connection; a REST caller has no connection to
                receive it on (see Batch 2's design-doc note on why this
                was deliberately not wired into REST).

            Payload / Emits: UNCHANGED — see the payload/emits list this
            docstring used to carry; nothing about the wire contract
            changed, only how the body is implemented internally.
            """
            sid     = request.sid
            user_id = self._get_current_user()

            if not user_id:
                current_app.logger.warning(
                    f"[MESSAGE_AUTH_FAILED] "
                    f"sid={sid} event=send_thread_message reason=unauthenticated"
                )
                self._emit_error("Authentication required")
                return

            # Extract fields early so they're available in the except block
            thread_id      = data.get("thread_id")
            client_temp_id = data.get("client_temp_id")
            text_content_raw = data.get("text_content", "").strip()
            has_attachment = bool(data.get("attachments") or data.get("attachment_url"))

            current_app.logger.info(
                f"[MESSAGE_RECEIVED] "
                f"user_id={user_id} thread_id={thread_id} "
                f"client_temp_id={client_temp_id!r} "
                f"text_length={len(text_content_raw)} "
                f"has_attachment={has_attachment} "
                f"sid={sid}"
            )

            try:
                reply_to_id = data.get("reply_to_id")

                # Issue 1: Extract attachments array with legacy single-field fallback.
                # Same extraction thread_message_service.create_thread_message
                # itself performs internally is NOT duplicated here beyond this
                # point — this handler only needs it pre-service-call for the
                # [MESSAGE_ATTACHMENT] log line below and the has_attachment
                # flag already computed above; the service does its own
                # MAX_ATTACHMENTS capping and per-item validation.
                attachments_data = data.get("attachments", [])
                if not attachments_data and data.get("attachment_url"):
                    attachments_data = [{
                        "attachment_url":  data.get("attachment_url"),
                        "attachment_name": data.get("attachment_name"),
                        "attachment_type": data.get("attachment_type"),
                        "attachment_size": data.get("attachment_size"),
                    }]

                # ── Rate limit — WS-only, checked BEFORE calling the shared
                # service so a rate-limited request never touches the DB at
                # all (matching the original behavior exactly: this check
                # ran before persistence before this refactor too). See
                # docstring above for why this stays a WS-handler concern. ──
                if _is_rate_limited(user_id):
                    current_app.logger.warning(
                        f"[MESSAGE_RATE_LIMITED] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"limit={_RATE_LIMIT_MAX}msg/{_RATE_LIMIT_WINDOW}s "
                        f"client_temp_id={client_temp_id!r} sid={sid} "
                        f"— duplicate flood or client retry storm possible"
                    )
                    emit("thread_error", {
                        "message":        f"Slow down — max {_RATE_LIMIT_MAX} messages per minute",
                        "client_temp_id": client_temp_id
                    })
                    return

                if has_attachment:
                    current_app.logger.info(
                        f"[MESSAGE_ATTACHMENT] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"attachment_count={len(attachments_data)} "
                        f"client_temp_id={client_temp_id!r}"
                    )

                t_persist_start = time.monotonic()
                current_app.logger.debug(
                    f"[MESSAGE_PERSIST_START] "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"client_temp_id={client_temp_id!r} "
                    f"reply_to_id={reply_to_id} "
                    f"has_attachment={has_attachment}"
                )

                # ── Delegate validation, presence-based initial status,
                # persistence, attachment rows, mention detection, and
                # payload construction to the shared service. Each typed
                # exception below corresponds to exactly one validation
                # branch this handler used to inline itself (thread not
                # found, thread closed, not a member, message too
                # long/empty) — the exception's own .message is emitted
                # via self._emit_error, which is the same string the
                # inline branch used to pass to self._emit_error directly,
                # so client-facing error text is unchanged. ──
                try:
                    result = _tms.create_thread_message(
                        user_id=user_id,
                        thread_id=thread_id,
                        text_content=text_content_raw,
                        reply_to_id=reply_to_id,
                        attachments_data=attachments_data,
                    )
                except _tms.NotAMemberError as e:
                    current_app.logger.warning(
                        f"[MESSAGE_DENIED] user_id={user_id} thread_id={thread_id} "
                        f"reason=not_a_member client_temp_id={client_temp_id!r}"
                    )
                    self._emit_error(str(e))
                    return
                except _tms.ThreadNotFoundError as e:
                    current_app.logger.warning(
                        f"[MESSAGE_DENIED] user_id={user_id} thread_id={thread_id} "
                        f"reason=thread_not_found client_temp_id={client_temp_id!r}"
                    )
                    self._emit_error(str(e))
                    return
                except _tms.ThreadClosedError as e:
                    current_app.logger.warning(
                        f"[MESSAGE_DENIED] user_id={user_id} thread_id={thread_id} "
                        f"reason=thread_closed client_temp_id={client_temp_id!r}"
                    )
                    self._emit_error(str(e))
                    return
                except _tms.ValidationFailedError as e:
                    current_app.logger.warning(
                        f"[MESSAGE_VALIDATION_FAILED] user_id={user_id} thread_id={thread_id} "
                        f"reason={e.message!r} client_temp_id={client_temp_id!r}"
                    )
                    self._emit_error(str(e))
                    return

                msg               = result.message
                text_content      = result.text_content     # sanitized, post-service
                other_ids         = result.other_member_ids
                mentioned_ids     = result.mentioned_user_ids
                attachments_data  = result.attachments_data  # post-cap, post-service

                t_persist_ms = (time.monotonic() - t_persist_start) * 1000
                current_app.logger.info(
                    f"[MESSAGE_PERSISTED] "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"msg_id={msg.id} client_temp_id={client_temp_id!r} "
                    f"has_attachment={has_attachment} reply_to_id={msg.reply_to_id} "
                    f"mention_count={len(mentioned_ids)} "
                    f"persist_ms={t_persist_ms:.1f}"
                )
                if mentioned_ids:
                    current_app.logger.info(
                        f"[MESSAGE_MENTIONS] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"msg_id={msg.id} mentioned_user_ids={mentioned_ids} "
                        f"notification_count={len(mentioned_ids)}"
                    )

                # ── Build and broadcast — payload comes straight from the
                # service's result; only client_temp_id (WS-only, see
                # docstring) is layered on afterward. ──
                payload = dict(result.payload)
                payload["client_temp_id"] = client_temp_id

                current_app.logger.info(
                    f"[MESSAGE_BROADCAST] "
                    f"event=new_thread_message "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"msg_id={msg.id} client_temp_id={client_temp_id!r} "
                    f"room=thread_{thread_id} status={msg.status}"
                )
                self.broadcast_to_thread(thread_id, "new_thread_message", payload)

                # Confirmation to this socket only — used by frontend to swap
                # the optimistic (temp_id) message for the server-confirmed one.
                current_app.logger.debug(
                    f"[MESSAGE_SENT_ACK] "
                    f"event=thread_message_sent "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"msg_id={msg.id} client_temp_id={client_temp_id!r} sid={sid}"
                )
                emit("thread_message_sent", {
                    "id":             msg.id,
                    "client_temp_id": client_temp_id,
                    "sent_at":        msg.sent_at.isoformat() + "Z",
                    "status":         msg.status,
                })

                # ── ISSUE-6: Push lightweight metadata to all member personal rooms ──
                # This keeps every member's thread list fresh without auto-joining
                # all thread rooms (which would break the 3-state status system).
                # Uses result.text_content / result.attachments_data (the
                # service's sanitized/capped versions), not the raw
                # pre-service locals — see design note above the service call.
                if text_content:
                    preview_text = text_content[:80]
                elif attachments_data:
                    type_map = {"image": "📷 Image", "video": "🎬 Video", "document": "📎 File"}
                    first_type = (attachments_data[0].get("attachment_type") or "document")
                    preview_text = type_map.get(first_type, "📎 Attachment")
                else:
                    preview_text = ""

                sender = User.query.get(user_id)
                metadata_payload = {
                    "thread_id":    thread_id,
                    "last_message": {
                        "text":      preview_text,
                        "sender":    sender.name if sender else "Unknown",
                        "sender_id": user_id,
                        "sent_at":   msg.sent_at.isoformat() + "Z",
                        "status":    msg.status,
                    },
                    "last_activity": msg.sent_at.isoformat() + "Z",
                }

                for mid in other_ids:
                    self.notify_user(mid, "thread_list_update", metadata_payload)

                current_app.logger.debug(
                    f"[THREAD_LIST_UPDATE_PUSHED] thread_id={thread_id} "
                    f"recipient_count={len(other_ids)}"
                )

                # ── Learnora trigger — WS-only, see docstring above for why ──
                # FIX: result.matched_ai_trigger comes from thread_message_service
                # and is NOT guaranteed to carry the full AI_PERSONALITIES shape
                # (e.g. may be missing "system_prompt") — that mismatch was
                # crashing _call_learnora_for_thread with a KeyError on
                # personality["system_prompt"]. Re-resolve the canonical dict
                # from AI_PERSONALITIES by key here instead of trusting the
                # service's return shape to stay in lockstep with this file's
                # constant. Falls back to plain "learnora" if the service's
                # key doesn't match anything known (defensive — should not
                # normally happen given _TRIGGER_MAP is this file's own source
                # of truth for trigger detection).
                raw_matched = result.matched_ai_trigger
                matched_personality = None
                if raw_matched:
                    trigger_key = (
                        raw_matched.get("key")
                        if isinstance(raw_matched, dict)
                        else getattr(raw_matched, "key", None)
                    )
                    matched_personality = AI_PERSONALITIES.get(
                        trigger_key, AI_PERSONALITIES["learnora"]
                    )

                if matched_personality:
                    self.broadcast_to_thread(thread_id, "learnora_thinking", {
                        "thread_id": thread_id,
                        "personality": matched_personality["display_name"]
                    })
                    app_ref = current_app._get_current_object()
                    _learnora_executor.submit(
                        _call_learnora_for_thread,
                        app_ref, thread_id, text_content, user_id, matched_personality,
                    )

                # Auto-reply: if the user replied to an AI message without a manual trigger,
                # let the AI continue the conversation (rate-limited 3 per 5 min per user/thread)
                if msg.reply_to_id and not matched_personality:
                    parent_msg = ThreadMessage.query.filter_by(
                        id=msg.reply_to_id, is_ai_response=True, is_deleted=False
                    ).first()

                    if parent_msg:
                        key = (user_id, thread_id)
                        now = time.monotonic()
                        bucket = [t for t in _auto_reply_buckets.get(key, []) if now - t < 300]

                        if len(bucket) < 3:
                            bucket.append(now)
                            _auto_reply_buckets[key] = bucket

                            personality = AI_PERSONALITIES["learnora"]
                            self.broadcast_to_thread(thread_id, "learnora_thinking", {
                                "thread_id": thread_id,
                                "personality": personality["display_name"]
                            })
                            _learnora_executor.submit(
                                _call_learnora_for_thread,
                                current_app._get_current_object(), thread_id, text_content,
                                user_id, personality, msg.id,
                            )

            except Exception as e:
                current_app.logger.error(
                    f"[MESSAGE_SEND_ERROR] "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"client_temp_id={client_temp_id!r} sid={sid} "
                    f"error={e!r}",
                    exc_info=True
                )
                db.session.rollback()
                emit("thread_message_error", {
                    "message":        "Failed to send message",
                    "client_temp_id": data.get("client_temp_id")
                })

        # ================================================================
        # TYPING INDICATORS
        # ================================================================

        @sio.on("thread_typing")
        def handle_thread_typing(data):
            """
            Notify other thread members that this user is typing.
            Fires frequently — logging is intentionally minimal (DEBUG only)
            to avoid log noise. Errors are still captured.

            Payload: { "thread_id": <int> }
            Emits:   "thread_typing_started" to room (except sender)
            """
            user_id = self._get_current_user()
            if not user_id:
                return

            try:
                thread_id = data.get("thread_id")
                if not thread_id:
                    return

                if not self._is_member(thread_id, user_id):
                    return

                self.typing_mgr.cleanup_expired()
                self.typing_mgr.set_typing(thread_id, user_id)

                user = User.query.get(user_id)
                self.socketio.emit(
                    "thread_typing_started",
                    {"thread_id": thread_id, "user_id": user_id,
                     "user_name": user.name if user else "Someone"},
                    room=f"thread_{thread_id}",
                    include_self=False
                )

            except Exception as e:
                current_app.logger.error(
                    f"[THREAD_TYPING_ERROR] "
                    f"user_id={user_id} thread_id={data.get('thread_id')} "
                    f"error={e!r}"
                )

        # ----------------------------------------------------------------

        @sio.on("thread_typing_stop")
        def handle_thread_typing_stop(data):
            """
            Payload: { "thread_id": <int> }
            Emits:   "thread_typing_stopped" to room (except sender)
            """
            user_id = self._get_current_user()
            if not user_id:
                return

            try:
                thread_id = data.get("thread_id")
                if not thread_id:
                    return

                self.typing_mgr.stop_typing(thread_id, user_id)

                self.socketio.emit(
                    "thread_typing_stopped",
                    {"thread_id": thread_id, "user_id": user_id},
                    room=f"thread_{thread_id}",
                    include_self=False
                )

            except Exception as e:
                current_app.logger.error(
                    f"[THREAD_TYPING_STOP_ERROR] "
                    f"user_id={user_id} thread_id={data.get('thread_id')} "
                    f"error={e!r}"
                )

        # ================================================================
        # REACTIONS
        # ================================================================

        @sio.on("add_thread_reaction")
        def handle_add_thread_reaction(data):
            """
            Add or change an emoji reaction. Sending the same emoji toggles it off.
            Payload: { "message_id": <int>, "emoji": "🔥" }
            Emits:   "thread_reactions_updated" to room
            """
            sid     = request.sid
            user_id = self._get_current_user()
            if not user_id:
                self._emit_error("Authentication required")
                return

            try:
                message_id = data.get("message_id")
                emoji      = data.get("emoji", "").strip()

                current_app.logger.info(
                    f"[REACTION_RECEIVED] "
                    f"user_id={user_id} message_id={message_id} "
                    f"emoji={emoji!r} sid={sid}"
                )

                if not message_id or not emoji:
                    current_app.logger.warning(
                        f"[REACTION_VALIDATION_FAILED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"emoji={emoji!r} reason=missing_fields"
                    )
                    self._emit_error("message_id and emoji required")
                    return

                msg = ThreadMessage.query.filter_by(
                    id=message_id, is_deleted=False
                ).first()
                if not msg:
                    current_app.logger.warning(
                        f"[REACTION_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=message_not_found"
                    )
                    self._emit_error("Message not found")
                    return

                if not self._is_member(msg.thread_id, user_id):
                    current_app.logger.warning(
                        f"[REACTION_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"thread_id={msg.thread_id} reason=not_a_member"
                    )
                    self._emit_error("You are not a member of this thread")
                    return

                existing = ThreadMessageReaction.query.filter_by(
                    message_id=message_id,
                    user_id=user_id
                ).first()

                if existing:
                    if existing.emoji == emoji:
                        db.session.delete(existing)
                        action = "toggled_off"
                    else:
                        prev_emoji = existing.emoji
                        existing.emoji = emoji
                        action = f"changed_from_{prev_emoji}_to_{emoji}"
                else:
                    db.session.add(ThreadMessageReaction(
                        message_id=message_id,
                        user_id=user_id,
                        emoji=emoji
                    ))
                    action = "added"

                db.session.commit()

                current_app.logger.info(
                    f"[REACTION_UPDATED] "
                    f"user_id={user_id} message_id={message_id} "
                    f"thread_id={msg.thread_id} emoji={emoji!r} action={action}"
                )

                reactions = self._build_reactions(message_id)

                current_app.logger.debug(
                    f"[REACTION_BROADCAST] "
                    f"event=thread_reactions_updated "
                    f"message_id={message_id} thread_id={msg.thread_id} "
                    f"distinct_emojis={len(reactions)}"
                )
                self.broadcast_to_thread(msg.thread_id, "thread_reactions_updated", {
                    "message_id": message_id,
                    "reactions":  reactions
                })

            except Exception as e:
                current_app.logger.error(
                    f"[REACTION_ERROR] "
                    f"user_id={user_id} message_id={data.get('message_id')} "
                    f"emoji={data.get('emoji')!r} error={e!r}",
                    exc_info=True
                )
                db.session.rollback()

        # ----------------------------------------------------------------

        @sio.on("remove_thread_reaction")
        def handle_remove_thread_reaction(data):
            """
            Explicitly remove own reaction.
            Payload: { "message_id": <int> }
            Emits:   "thread_reactions_updated" to room
            """
            sid     = request.sid
            user_id = self._get_current_user()
            if not user_id:
                return

            try:
                message_id = data.get("message_id")

                current_app.logger.info(
                    f"[REACTION_REMOVE_RECEIVED] "
                    f"user_id={user_id} message_id={message_id} sid={sid}"
                )

                if not message_id:
                    return

                msg = ThreadMessage.query.get(message_id)
                if not msg:
                    current_app.logger.warning(
                        f"[REACTION_REMOVE_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=message_not_found"
                    )
                    return

                reaction = ThreadMessageReaction.query.filter_by(
                    message_id=message_id,
                    user_id=user_id
                ).first()

                if reaction:
                    removed_emoji = reaction.emoji
                    db.session.delete(reaction)
                    db.session.commit()
                    current_app.logger.info(
                        f"[REACTION_REMOVED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"thread_id={msg.thread_id} emoji={removed_emoji!r}"
                    )
                else:
                    current_app.logger.debug(
                        f"[REACTION_REMOVE_NOOP] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=no_reaction_exists_for_user"
                    )

                reactions = self._build_reactions(message_id)
                self.broadcast_to_thread(msg.thread_id, "thread_reactions_updated", {
                    "message_id": message_id,
                    "reactions":  reactions
                })

            except Exception as e:
                current_app.logger.error(
                    f"[REACTION_REMOVE_ERROR] "
                    f"user_id={user_id} message_id={data.get('message_id')} "
                    f"error={e!r}",
                    exc_info=True
                )
                db.session.rollback()

        # ================================================================
        # EDIT MESSAGE
        # ================================================================

        @sio.on("edit_thread_message")
        def handle_edit_thread_message(data):
            """
            Edit own message within the edit window (15 min).
            Payload: { "message_id": <int>, "text_content": "new text" }
            Emits:   "thread_message_edited" to room

            HORIZONTAL SCALING BATCH 3: delegates ownership/AI-message/
            edit-window enforcement and the actual persist to
            services.thread_message_service.edit_thread_message — the
            same shared function the REST fallback
            (messaging.py::edit_thread_message) already calls (Batch 2).
            The `message_id required` presence check below is kept
            inline (not delegated) because the shared service has no
            equivalent — it looks up by message_id + sender_id and simply
            finds no row for message_id=None, which would surface as a
            less specific "not found" error instead of this handler's
            original, more precise "message_id and text_content
            required" — preserved here so the client-facing error text
            for this specific case is unchanged.
            """
            sid     = request.sid
            user_id = self._get_current_user()
            if not user_id:
                self._emit_error("Authentication required")
                return

            try:
                message_id = data.get("message_id")
                new_text   = (data.get("text_content") or "").strip()

                current_app.logger.info(
                    f"[MESSAGE_EDIT_RECEIVED] "
                    f"user_id={user_id} message_id={message_id} "
                    f"new_text_length={len(new_text)} sid={sid}"
                )

                if not message_id or not new_text:
                    current_app.logger.warning(
                        f"[MESSAGE_EDIT_VALIDATION_FAILED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=missing_fields"
                    )
                    self._emit_error("message_id and text_content required")
                    return

                try:
                    result = _tms.edit_thread_message(
                        user_id=user_id, message_id=message_id, new_text=new_text,
                    )
                except _tms.ValidationFailedError as e:
                    # Only reachable here if new_text somehow passed the
                    # `not new_text` check above but still fails the
                    # service's own length check — i.e. MAX_MESSAGE_LENGTH
                    # too long, the original behavior for this branch.
                    current_app.logger.warning(
                        f"[MESSAGE_EDIT_VALIDATION_FAILED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason={e.message!r}"
                    )
                    self._emit_error(str(e))
                    return
                except _tms.MessageNotFoundError as e:
                    current_app.logger.warning(
                        f"[MESSAGE_EDIT_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=not_found_or_not_owner"
                    )
                    self._emit_error("Message not found or you don't own it")
                    return
                except _tms.PermissionDeniedError as e:
                    current_app.logger.warning(
                        f"[MESSAGE_EDIT_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=ai_message_not_editable"
                    )
                    self._emit_error(str(e))
                    return
                except _tms.EditWindowExpiredError as e:
                    current_app.logger.warning(
                        f"[MESSAGE_EDIT_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=edit_window_expired"
                    )
                    self._emit_error(str(e))
                    return

                msg = result.message

                current_app.logger.info(
                    f"[MESSAGE_EDITED] "
                    f"user_id={user_id} message_id={message_id} "
                    f"thread_id={msg.thread_id} "
                    f"new_length={len(msg.text_content)}"
                )

                self.broadcast_to_thread(msg.thread_id, "thread_message_edited", {
                    "message_id":   message_id,
                    "text_content": msg.text_content,
                    "edited_at":    msg.edited_at.isoformat() + "Z"
                })

            except Exception as e:
                current_app.logger.error(
                    f"[MESSAGE_EDIT_ERROR] "
                    f"user_id={user_id} message_id={data.get('message_id')} "
                    f"sid={sid} error={e!r}",
                    exc_info=True
                )
                db.session.rollback()

        # ================================================================
        # DELETE MESSAGE
        # ================================================================

        @sio.on("delete_thread_message")
        def handle_delete_thread_message(data):
            """
            Soft-delete a message.
            Sender can delete own; creator/moderator can delete anyone's.
            Payload: { "message_id": <int> }
            Emits:   "thread_message_deleted" to room

            HORIZONTAL SCALING BATCH 3: delegates the not-found/
            membership/permission checks and the actual soft-delete to
            services.thread_message_service.delete_thread_message — the
            same shared function messaging.py::delete_thread_message
            already calls (Batch 2), which is also where the REST/WS
            permission-model bug (moderator delete allowed via WS, wrongly
            rejected via REST) was fixed. The `message_id required`
            presence check is kept inline for the same reason as the edit
            handler above — the service has no equivalent for a caller
            that didn't supply a message_id at all.
            """
            sid     = request.sid
            user_id = self._get_current_user()
            if not user_id:
                self._emit_error("Authentication required")
                return

            try:
                message_id = data.get("message_id")

                current_app.logger.info(
                    f"[MESSAGE_DELETE_RECEIVED] "
                    f"user_id={user_id} message_id={message_id} sid={sid}"
                )

                if not message_id:
                    current_app.logger.warning(
                        f"[MESSAGE_DELETE_VALIDATION_FAILED] "
                        f"user_id={user_id} sid={sid} reason=missing_message_id"
                    )
                    self._emit_error("message_id required")
                    return

                try:
                    result = _tms.delete_thread_message(user_id=user_id, message_id=message_id)
                except _tms.MessageNotFoundError as e:
                    current_app.logger.warning(
                        f"[MESSAGE_DELETE_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=not_found_or_already_deleted"
                    )
                    self._emit_error(str(e))
                    return
                except _tms.PermissionDeniedError as e:
                    # The service raises this SAME exception class for both
                    # "not a member at all" and "member but insufficient
                    # role" — distinguished here only by message text, so
                    # this handler's original two distinct log tags are
                    # preserved exactly (see thread_message_service
                    # .delete_thread_message's docstring for the exact two
                    # raise sites and their message strings).
                    if e.message == "Not a thread member":
                        current_app.logger.warning(
                            f"[MESSAGE_DELETE_DENIED] "
                            f"user_id={user_id} message_id={message_id} "
                            f"reason=not_a_member"
                        )
                    else:
                        current_app.logger.warning(
                            f"[MESSAGE_DELETE_DENIED] "
                            f"user_id={user_id} message_id={message_id} "
                            f"reason=insufficient_permissions"
                        )
                    self._emit_error(str(e))
                    return

                current_app.logger.info(
                    f"[MESSAGE_DELETED] "
                    f"user_id={user_id} message_id={message_id} "
                    f"thread_id={result.thread_id} "
                    f"original_sender_id={result.original_sender_id} "
                    f"delete_type={'own' if result.original_sender_id == user_id else 'moderation'}"
                )

                self.broadcast_to_thread(result.thread_id, "thread_message_deleted", {
                    "message_id": message_id,
                    "deleted_by": user_id
                })

            except Exception as e:
                current_app.logger.error(
                    f"[MESSAGE_DELETE_ERROR] "
                    f"user_id={user_id} message_id={data.get('message_id')} "
                    f"sid={sid} error={e!r}",
                    exc_info=True
                )
                db.session.rollback()

        # ================================================================
        # PIN / UNPIN MESSAGE
        # ================================================================

        @sio.on("pin_thread_message")
        def handle_pin_thread_message(data):
            """
            Pin a message (creator / moderator only). Max 5 per thread.
            Payload: { "message_id": <int> }
            Emits:   "thread_message_pinned" to room
            """
            sid     = request.sid
            user_id = self._get_current_user()
            if not user_id:
                self._emit_error("Authentication required")
                return

            try:
                message_id = data.get("message_id")

                current_app.logger.info(
                    f"[MESSAGE_PIN_RECEIVED] "
                    f"user_id={user_id} message_id={message_id} sid={sid}"
                )

                if not message_id:
                    self._emit_error("message_id required")
                    return

                msg = ThreadMessage.query.filter_by(
                    id=message_id, is_deleted=False
                ).first()
                if not msg:
                    current_app.logger.warning(
                        f"[MESSAGE_PIN_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=message_not_found"
                    )
                    self._emit_error("Message not found")
                    return

                membership = self._is_member(msg.thread_id, user_id)
                if not membership or not self._is_moderator_or_creator(membership):
                    current_app.logger.warning(
                        f"[MESSAGE_PIN_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"thread_id={msg.thread_id} "
                        f"role={membership.role if membership else 'non-member'} "
                        f"reason=insufficient_permissions"
                    )
                    self._emit_error("Only creator or moderator can pin messages")
                    return

                if msg.is_pinned:
                    current_app.logger.warning(
                        f"[MESSAGE_PIN_NOOP] "
                        f"user_id={user_id} message_id={message_id} "
                        f"thread_id={msg.thread_id} reason=already_pinned"
                    )
                    self._emit_error("Message is already pinned")
                    return

                pinned_count = ThreadMessage.query.filter_by(
                    thread_id=msg.thread_id, is_pinned=True, is_deleted=False
                ).count()
                if pinned_count >= MAX_PINS_PER_THREAD:
                    current_app.logger.warning(
                        f"[MESSAGE_PIN_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"thread_id={msg.thread_id} "
                        f"pinned_count={pinned_count} max={MAX_PINS_PER_THREAD} "
                        f"reason=pin_limit_reached"
                    )
                    self._emit_error(f"Max {MAX_PINS_PER_THREAD} pinned messages per thread")
                    return

                msg.is_pinned    = True
                msg.pinned_by_id = user_id
                db.session.commit()

                current_app.logger.info(
                    f"[MESSAGE_PINNED] "
                    f"user_id={user_id} message_id={message_id} "
                    f"thread_id={msg.thread_id} "
                    f"pins_used={pinned_count + 1}/{MAX_PINS_PER_THREAD}"
                )

                sender = User.query.get(msg.sender_id)
                self.broadcast_to_thread(msg.thread_id, "thread_message_pinned", {
                    "message_id": message_id,
                    "is_pinned":  True,
                    "pinned_by":  user_id,
                    "text":       msg.text_content[:120],
                    "sender":     sender.name if sender else "Unknown"
                })

            except Exception as e:
                current_app.logger.error(
                    f"[MESSAGE_PIN_ERROR] "
                    f"user_id={user_id} message_id={data.get('message_id')} "
                    f"sid={sid} error={e!r}",
                    exc_info=True
                )
                db.session.rollback()

        # ----------------------------------------------------------------

        @sio.on("unpin_thread_message")
        def handle_unpin_thread_message(data):
            """
            Unpin a message (creator / moderator only).
            Payload: { "message_id": <int> }
            Emits:   "thread_message_unpinned" to room
            """
            sid     = request.sid
            user_id = self._get_current_user()
            if not user_id:
                self._emit_error("Authentication required")
                return

            try:
                message_id = data.get("message_id")

                current_app.logger.info(
                    f"[MESSAGE_UNPIN_RECEIVED] "
                    f"user_id={user_id} message_id={message_id} sid={sid}"
                )

                if not message_id:
                    self._emit_error("message_id required")
                    return

                msg = ThreadMessage.query.filter_by(
                    id=message_id, is_deleted=False, is_pinned=True
                ).first()
                if not msg:
                    current_app.logger.warning(
                        f"[MESSAGE_UNPIN_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=not_found_or_not_pinned"
                    )
                    self._emit_error("Pinned message not found")
                    return

                membership = self._is_member(msg.thread_id, user_id)
                if not membership or not self._is_moderator_or_creator(membership):
                    current_app.logger.warning(
                        f"[MESSAGE_UNPIN_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"thread_id={msg.thread_id} "
                        f"role={membership.role if membership else 'non-member'} "
                        f"reason=insufficient_permissions"
                    )
                    self._emit_error("Only creator or moderator can unpin messages")
                    return

                msg.is_pinned    = False
                msg.pinned_by_id = None
                db.session.commit()

                current_app.logger.info(
                    f"[MESSAGE_UNPINNED] "
                    f"user_id={user_id} message_id={message_id} "
                    f"thread_id={msg.thread_id}"
                )

                self.broadcast_to_thread(msg.thread_id, "thread_message_unpinned", {
                    "message_id": message_id,
                    "is_pinned":  False
                })

            except Exception as e:
                current_app.logger.error(
                    f"[MESSAGE_UNPIN_ERROR] "
                    f"user_id={user_id} message_id={data.get('message_id')} "
                    f"sid={sid} error={e!r}",
                    exc_info=True
                )
                db.session.rollback()

        # ================================================================
        # MARK THREAD AS READ
        # ================================================================

        @sio.on("mark_thread_read")
        def handle_mark_thread_read(data):
            """
            Update the member's last_read_at so unread counts reset.
            Client should call this whenever the thread chat is visible.

            FIX: upserts ThreadMessageReadReceipt rows and emits
                 message_status_updated per sender, enabling blue ticks.

            Payload: { "thread_id": <int> }
            """
            sid     = request.sid
            user_id = self._get_current_user()
            if not user_id:
                return

            try:
                thread_id = data.get("thread_id")

                current_app.logger.info(
                    f"[READ_RECEIPT_RECEIVED] "
                    f"user_id={user_id} thread_id={thread_id} sid={sid}"
                )

                if not thread_id:
                    return

                membership = self._is_member(thread_id, user_id)
                if not membership:
                    current_app.logger.warning(
                        f"[READ_RECEIPT_DENIED] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"reason=not_a_member"
                    )
                    return

                now          = datetime.datetime.utcnow()
                t_read_start = time.monotonic()

                # Fetch ALL non-deleted messages in the thread not sent by this user.
                # No cutoff filter — opening the thread means every message is visible,
                # so all of them should get a read receipt. The existing_receipt_ids
                # dedup check below is what prevents re-inserting duplicates.
                unread_messages = ThreadMessage.query.filter(
                    ThreadMessage.thread_id == thread_id,
                    ThreadMessage.sender_id != user_id,
                    ThreadMessage.is_deleted == False,
                    ThreadMessage.status != 'read'
                ).all()

                current_app.logger.debug(
                    f"[READ_RECEIPT_PROCESSING] "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"total_messages_to_check={len(unread_messages)}"
                )

                sender_msg_map: dict[int, list[int]] = {}

                if unread_messages:
                    msg_ids = [m.id for m in unread_messages]

                    # FIX: single batch SELECT for existing receipts
                    existing_receipt_ids = {
                        r.message_id for r in ThreadMessageReadReceipt.query.filter(
                            ThreadMessageReadReceipt.message_id.in_(msg_ids),
                            ThreadMessageReadReceipt.user_id == user_id
                        ).all()
                    }

                    # Bulk insert missing receipts
                    new_receipts = [
                        ThreadMessageReadReceipt(message_id=mid, user_id=user_id, read_at=now)
                        for mid in msg_ids
                        if mid not in existing_receipt_ids
                    ]
                    if new_receipts:
                        db.session.add_all(new_receipts)
                        current_app.logger.debug(
                            f"[READ_RECEIPT_INSERTING] "
                            f"user_id={user_id} thread_id={thread_id} "
                            f"new_receipts={len(new_receipts)} "
                            f"already_existed={len(existing_receipt_ids)} "
                            f"— duplicate receipt guard active"
                        )

                    # FIX: single bulk UPDATE instead of one per message
                    ThreadMessage.query.filter(
                        ThreadMessage.id.in_(msg_ids),
                        ThreadMessage.status != 'read'
                    ).update({ThreadMessage.status: 'read'}, synchronize_session=False)

                    for msg in unread_messages:
                        sender_msg_map.setdefault(msg.sender_id, []).append(msg.id)

                ThreadMember.query.filter_by(
                    thread_id=thread_id, student_id=user_id
                ).update(
                    {ThreadMember.last_read_at: now},
                    synchronize_session=False
                )
                db.session.commit()

                t_read_ms = (time.monotonic() - t_read_start) * 1000
                current_app.logger.info(
                    f"[READ_RECEIPT_PERSISTED] "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"messages_marked_read={len(unread_messages)} "
                    f"senders_to_notify={len(sender_msg_map)} "
                    f"duration_ms={t_read_ms:.1f}"
                )

                # Push read status to each original sender's personal room.
                # Race risk: if mark_thread_read fires concurrently with
                # message_delivered for the same message_id, both may push
                # status updates. The client should treat 'read' as terminal
                # and ignore subsequent 'delivered' for the same message_id.
                for sender_id, notif_msg_ids in sender_msg_map.items():
                    current_app.logger.debug(
                        f"[READ_STATUS_PUSH] "
                        f"reader_user_id={user_id} thread_id={thread_id} "
                        f"notifying_sender_id={sender_id} "
                        f"msg_count={len(notif_msg_ids)} "
                        f"room=user_{sender_id}"
                    )
                    self.socketio.emit(
                        "message_status_updated",
                        {
                            "thread_id":   thread_id,
                            "message_ids": notif_msg_ids,
                            "status":      "read",
                            "by_user_id":  user_id,
                        },
                        room=_user_room(sender_id)
                    )

            except Exception as e:
                current_app.logger.error(
                    f"[READ_RECEIPT_ERROR] "
                    f"user_id={user_id} thread_id={data.get('thread_id')} "
                    f"sid={sid} error={e!r}",
                    exc_info=True
                )
                db.session.rollback()

        # ================================================================
        # MESSAGE DELIVERED  (NEW)
        # ================================================================

        @sio.on("message_delivered")
        def handle_message_delivered(data):
            """
            NEW: Client emits this when it first renders a message sent by
            another user. Upgrades message.status sent->delivered and pushes
            message_status_updated to the sender's personal room.

            Payload: { "message_id": <int> }
            """
            sid     = request.sid
            user_id = self._get_current_user()
            if not user_id:
                return

            try:
                message_id = data.get("message_id")

                current_app.logger.debug(
                    f"[MESSAGE_DELIVERED_RECEIVED] "
                    f"user_id={user_id} message_id={message_id} sid={sid}"
                )

                if not message_id:
                    return

                msg = ThreadMessage.query.filter_by(
                    id=message_id, is_deleted=False
                ).first()

                if not msg:
                    current_app.logger.debug(
                        f"[MESSAGE_DELIVERED_SKIPPED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=message_not_found"
                    )
                    return

                # Guard: sender cannot mark their own message as delivered
                if msg.sender_id == user_id:
                    current_app.logger.debug(
                        f"[MESSAGE_DELIVERED_SKIPPED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=self_delivery_blocked"
                    )
                    return

                if not self._is_member(msg.thread_id, user_id):
                    current_app.logger.warning(
                        f"[MESSAGE_DELIVERED_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"thread_id={msg.thread_id} reason=not_a_member"
                    )
                    return

                current_status = getattr(msg, "status", "sent")

                # Only upgrade; never downgrade read -> delivered.
                # Concurrent mark_thread_read + message_delivered on the same
                # message_id can cause a benign double push — client must
                # treat 'read' as terminal and discard later 'delivered'.
                if current_status == "sent":
                    msg.status = "delivered"
                    db.session.commit()

                    current_app.logger.info(
                        f"[MESSAGE_DELIVERED_UPGRADED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"thread_id={msg.thread_id} "
                        f"sender_id={msg.sender_id} "
                        f"status=sent→delivered"
                    )

                    current_app.logger.debug(
                        f"[DELIVERED_STATUS_PUSH] "
                        f"notifying sender_id={msg.sender_id} "
                        f"room=user_{msg.sender_id} "
                        f"message_id={message_id} thread_id={msg.thread_id}"
                    )
                    self.socketio.emit(
                        "message_status_updated",
                        {
                            "thread_id":   msg.thread_id,
                            "message_ids": [msg.id],
                            "status":      "delivered",
                            "by_user_id":  user_id,
                        },
                        room=_user_room(msg.sender_id)
                    )
                else:
                    current_app.logger.debug(
                        f"[MESSAGE_DELIVERED_SKIPPED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"current_status={current_status} "
                        f"reason=no_upgrade_needed"
                    )

            except Exception as e:
                current_app.logger.error(
                    f"[MESSAGE_DELIVERED_ERROR] "
                    f"user_id={user_id} message_id={data.get('message_id')} "
                    f"sid={sid} error={e!r}",
                    exc_info=True
                )
                db.session.rollback()

        # ================================================================
        # EXPLICIT AI REQUEST
        # ================================================================

        @sio.on("request_ai_response")
        def handle_request_ai_response(data):
            """
            Explicitly trigger Learnora without mentioning @learnora in chat.
            Useful for a dedicated "Ask AI" button in the UI.

            Payload:
              thread_id   int   (required)
              question    str   (required)
              mode        str   (optional: "summarize" | "quiz" | default)

            Emits: "learnora_thinking" immediately, then "new_thread_message"
            """
            sid     = request.sid
            user_id = self._get_current_user()
            if not user_id:
                self._emit_error("Authentication required")
                return

            try:
                thread_id = data.get("thread_id")
                question  = (data.get("question") or "").strip()
                mode      = data.get("mode", "")

                current_app.logger.info(
                    f"[AI_REQUEST_RECEIVED] "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"mode={mode!r} question_length={len(question)} sid={sid}"
                )

                if not thread_id or not question:
                    current_app.logger.warning(
                        f"[AI_REQUEST_VALIDATION_FAILED] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"reason=missing_fields"
                    )
                    self._emit_error("thread_id and question required")
                    return

                if not self._is_member(thread_id, user_id):
                    current_app.logger.warning(
                        f"[AI_REQUEST_DENIED] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"reason=not_a_member"
                    )
                    self._emit_error("You are not a member of this thread")
                    return

                prefix = ""
                if mode == "summarize":
                    prefix = "@learnora summarize "
                elif mode == "quiz":
                    prefix = "@learnora quiz me on "

                trigger_text = f"{prefix}{question}"

                self.broadcast_to_thread(thread_id, "learnora_thinking", {
                    "thread_id": thread_id
                })

                app_ref = current_app._get_current_object()
                future = _learnora_executor.submit(
                    _call_learnora_for_thread,
                    app_ref, thread_id, trigger_text, user_id,
                )

                current_app.logger.info(
                    f"[AI_REQUEST_DISPATCHED] "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"mode={mode!r} executor=learnora-bg"
                )

            except Exception as e:
                current_app.logger.error(
                    f"[AI_REQUEST_ERROR] "
                    f"user_id={user_id} thread_id={data.get('thread_id')} "
                    f"sid={sid} error={e!r}",
                    exc_info=True
                )

        @sio.on("thread_ai_action")
        def handle_thread_ai_action(data):
            user_id = self._get_current_user()
            if not user_id:
                self._emit_error("Authentication required")
                return

            thread_id = data.get("thread_id")
            message_id = data.get("message_id")
            action = data.get("action", "")

            # AUDIT §14.3 FIX: rate limit checked immediately after auth,
            # before any DB query — mirrors send_thread_message's placement
            # discipline (a rate-limited request never touches the
            # database). This handler previously had NO rate limit at all,
            # unlike every other AI-dispatch path in this module.
            if _is_ai_action_rate_limited(user_id):
                current_app.logger.warning(
                    f"[AI_ACTION_RATE_LIMITED] "
                    f"user_id={user_id} thread_id={thread_id} action={action!r} "
                    f"limit={_AI_ACTION_RATE_LIMIT_MAX}/{_AI_ACTION_RATE_LIMIT_WINDOW}s"
                )
                self._emit_error(
                    f"Slow down — max {_AI_ACTION_RATE_LIMIT_MAX} AI actions "
                    f"per {_AI_ACTION_RATE_LIMIT_WINDOW // 60} minutes"
                )
                return

            if action not in ("summarize", "translate", "explain", "to_code", "fact_check"):
                self._emit_error("Invalid action")
                return

            if not self._is_member(thread_id, user_id):
                self._emit_error("Not a member")
                return

            target_msg = ThreadMessage.query.filter_by(id=message_id, is_deleted=False).first()
            if not target_msg:
                self._emit_error("Message not found")
                return

            self.broadcast_to_thread(thread_id, "learnora_thinking", {
                "thread_id": thread_id,
                "personality": "Learnora"
            })

            _learnora_executor.submit(
                _call_learnora_action,
                current_app._get_current_object(), thread_id, message_id, action,
                data.get("target_lang"), user_id,
            )


# ============================================================================
# LEARNORA BACKGROUND FUNCTION
# ============================================================================

def _call_learnora_for_thread(app, thread_id: int, trigger_text: str, triggering_user_id: int,
                               personality=None, reply_to_message_id=None) -> None:
    """
    Runs in a daemon thread. Never blocks the WebSocket send path.

    FIX: uses app.config.get("LEARNORA_BOT_USER_ID", 0) with a hard guard —
         if 0 or not configured, exits immediately without crashing.

    Steps:
      1. Fetch recent thread messages for context
      2. Build system + history + user prompt
      3. Call provider (non-streaming, 30s timeout)
      4. Save AI reply as ThreadMessage (sender = bot user)
      5. Broadcast to thread room via thread_ws_manager
    """
    with app.app_context():
        t_total_start = time.monotonic()
        try:
            bot_user_id = app.config.get("LEARNORA_BOT_USER_ID", 0)

            # Hard guard — skip entirely if bot not configured
            if not bot_user_id:
                logger.warning(
                    f"[LEARNORA_SKIP] thread_id={thread_id} "
                    f"reason=LEARNORA_BOT_USER_ID_not_configured"
                )
                return

            if triggering_user_id == bot_user_id:
                logger.debug(
                    f"[LEARNORA_SKIP] thread_id={thread_id} "
                    f"reason=triggering_user_is_bot user_id={triggering_user_id}"
                )
                return

            logger.info(
                f"[LEARNORA_START] "
                f"thread_id={thread_id} triggered_by={triggering_user_id} "
                f"trigger_preview={trigger_text[:60]!r}"
            )

            thread = Thread.query.get(thread_id)
            if not thread:
                logger.warning(
                    f"[LEARNORA_ABORT] thread_id={thread_id} reason=thread_not_found"
                )
                return

            # ── Build conversation context ────────────────────────────
            recent = (
                ThreadMessage.query
                .filter_by(thread_id=thread_id, is_deleted=False)
                .order_by(ThreadMessage.sent_at.desc())
                .limit(12)
                .all()
            )
            recent.reverse()

            history = []
            for m in recent:
                sender = User.query.get(m.sender_id)
                role   = "assistant" if m.is_ai_response else "user"
                name   = "Learnora" if m.is_ai_response else (sender.name if sender else "Student")
                history.append({
                    "role":    role,
                    "content": f"[{name}]: {m.text_content}"
                })

            logger.debug(
                f"[LEARNORA_CONTEXT_BUILT] "
                f"thread_id={thread_id} context_messages={len(history)} "
                f"thread_title={thread.title!r}"
            )

            # ── System prompt ─────────────────────────────────────────
            # FIX: use .get() with a fallback rather than personality["system_prompt"]
            # directly — a caller passing a personality dict/object that doesn't
            # carry system_prompt (see the resolve-by-key fix at the trigger
            # detection call site above) previously crashed this whole
            # background thread with a KeyError instead of degrading gracefully.
            personality = personality or AI_PERSONALITIES["learnora"]
            base_system = (
                personality.get("system_prompt")
                if isinstance(personality, dict)
                else getattr(personality, "system_prompt", None)
            ) or AI_PERSONALITIES["learnora"]["system_prompt"]

            system = f'{base_system} Thread: "{thread.title}".'
            if thread.department:
                system += f" Department: {thread.department}."
            if thread.tags:
                system += f" Topics: {', '.join(thread.tags)}."

            messages = [
                {"role": "system", "content": system},
                *history,
                {"role": "user", "content": trigger_text}
            ]

            # ── Get provider and call AI ──────────────────────────────
            from routes.student.learnora import provider_manager, _call_provider_sync
            provider = provider_manager.get_working_provider(needs_vision=False)
            if not provider:
                logger.warning(
                    f"[LEARNORA_NO_PROVIDER] thread_id={thread_id} "
                    f"reason=all_providers_unavailable"
                )
                return

            provider_name = getattr(provider, "name", repr(provider))
            logger.info(
                f"[LEARNORA_PROVIDER_CALLING] "
                f"thread_id={thread_id} provider={provider_name} "
                f"context_messages={len(history)}"
            )

            t_ai_start = time.monotonic()
            ai_text    = _call_provider_sync(messages, provider)
            t_ai_ms    = (time.monotonic() - t_ai_start) * 1000

            if not ai_text:
                logger.warning(
                    f"[LEARNORA_EMPTY_RESPONSE] "
                    f"thread_id={thread_id} provider={provider_name} "
                    f"provider_latency_ms={t_ai_ms:.0f}"
                )
                return

            logger.info(
                f"[LEARNORA_RESPONSE_RECEIVED] "
                f"thread_id={thread_id} provider={provider_name} "
                f"response_length={len(ai_text)} "
                f"provider_latency_ms={t_ai_ms:.0f}"
            )

            # ── Persist as ThreadMessage ──────────────────────────────
            bot_msg = ThreadMessage(
                thread_id      = thread_id,
                sender_id      = bot_user_id,
                text_content   = ai_text,
                is_ai_response = True,
                ai_personality = personality.get("key"),          # NEW
                reply_to_id    = reply_to_message_id,              # NEW (None if not a reply)
                status         = "sent",
                sent_at        = datetime.datetime.utcnow()
            )
            db.session.add(bot_msg)

            Thread.query.filter_by(id=thread_id).update(
                {
                    Thread.message_count: Thread.message_count + 1,
                    Thread.last_activity: datetime.datetime.utcnow()
                },
                synchronize_session=False
            )

            try:
                db.session.commit()
                logger.info(
                    f"[LEARNORA_MESSAGE_PERSISTED] "
                    f"thread_id={thread_id} msg_id={bot_msg.id} "
                    f"response_length={len(ai_text)}"
                )
            except Exception as commit_err:
                logger.error(
                    f"[LEARNORA_COMMIT_ERROR] "
                    f"thread_id={thread_id} error={commit_err!r} "
                    f"— AI message NOT saved, NOT broadcast"
                )
                db.session.rollback()
                return

            # ── Broadcast to thread room ──────────────────────────────
            thread_ws_manager.broadcast_ai_message(thread_id, bot_msg, ai_text)

            t_total_ms = (time.monotonic() - t_total_start) * 1000
            logger.info(
                f"[LEARNORA_COMPLETE] "
                f"thread_id={thread_id} msg_id={bot_msg.id} "
                f"total_ms={t_total_ms:.0f} provider_ms={t_ai_ms:.0f}"
            )

        except Exception as e:
            logger.error(
                f"[LEARNORA_ERROR] "
                f"thread_id={thread_id} triggered_by={triggering_user_id} "
                f"error={e!r}",
                exc_info=True
            )
            db.session.rollback()


def _call_learnora_action(app, thread_id, message_id, action, target_lang, triggering_user_id):
    ACTION_PROMPTS = {
        "summarize":  "Summarize in 2-3 concise bullet points. Be factual.",
        "translate":  f"Translate to {target_lang or 'Spanish'}. Provide only the translation.",
        "explain":    "Explain in simple terms for a student. Under 4 sentences.",
        "to_code":    "Convert to working code. Use appropriate language. Wrap in code block.",
        "fact_check": "Fact-check. Respond: 1.Verdict 2.Confidence 3.Analysis 4.Caveats",
    }

    with app.app_context():
        try:
            bot_user_id = app.config.get("LEARNORA_BOT_USER_ID", 0)
            if not bot_user_id:
                return

            target_msg = ThreadMessage.query.get(message_id)
            thread = Thread.query.get(thread_id)
            if not target_msg or not thread:
                return

            system = f'You are Learnora in thread "{thread.title}". Perform the requested action concisely.'
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": f"{ACTION_PROMPTS[action]}\n\n---\n\n{target_msg.text_content}"}
            ]

            from routes.student.learnora import provider_manager, _call_provider_sync
            provider = provider_manager.get_working_provider(needs_vision=False)
            if not provider:
                return

            ai_text = _call_provider_sync(messages, provider)
            if not ai_text:
                return

            bot_msg = ThreadMessage(
                thread_id=thread_id,
                sender_id=bot_user_id,
                text_content=ai_text,
                reply_to_id=message_id,
                is_ai_response=True,
                ai_personality="learnora",
                status="sent",
                sent_at=datetime.datetime.utcnow()
            )
            db.session.add(bot_msg)

            Thread.query.filter_by(id=thread_id).update(
                {
                    Thread.message_count: Thread.message_count + 1,
                    Thread.last_activity: datetime.datetime.utcnow()
                },
                synchronize_session=False
            )

            db.session.commit()
            thread_ws_manager.broadcast_ai_message(thread_id, bot_msg, ai_text)

        except Exception as e:
            logger.error(
                f"[LEARNORA_ACTION_ERROR] "
                f"thread_id={thread_id} message_id={message_id} action={action} "
                f"triggered_by={triggering_user_id} error={e!r}",
                exc_info=True
            )
            db.session.rollback()


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

thread_ws_manager = ThreadWebSocketManager()


def init_thread_websocket(app, socketio) -> None:
    """
    Entry point called from the app factory, after init_message_websocket().

    Example:
        socketio = init_message_websocket(app)
        init_thread_websocket(app, socketio)
    """
    thread_ws_manager.init_socketio(app, socketio)
