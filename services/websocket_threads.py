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

Usage in app factory:
  from websocket_messages import message_ws_manager, init_message_websocket
  from websocket_threads import thread_ws_manager

  socketio = init_message_websocket(app)
  thread_ws_manager.init_socketio(app, socketio)
"""

import bleach
import threading
import datetime
import time
import os

from flask_socketio import emit, join_room, leave_room
from flask import request, current_app
from sqlalchemy import func

# H-10 fix: reuse the shared, lock-protected rate limiter/typing tracker
# instead of the ad hoc, unlocked module-level dicts that used to live in
# this file (_send_buckets/_is_rate_limited and a bespoke ThreadTypingManager).
# Both are genuinely mutated from multiple threads under async_mode='threading',
# so an actual lock (which this shared implementation has) matters here.
from services.websocket_rate_limiter import RateLimiter, TypingStatusManager

from extensions import db
from models import (
    User, Thread, ThreadMember, ThreadMessage,
    ThreadMessageReaction, ThreadMessageReadReceipt,
    ThreadMessageAttachment,                          # Issue 1: new
    Mention, Notification
)

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

# Rate-limit buckets
_ai_action_buckets: dict = {}
_auto_reply_buckets: dict = {}

# Per-user rate limit  (env-overridable)
_RATE_LIMIT_MAX    = int(os.environ.get("THREAD_MSG_RATE_MAX",    "30"))
_RATE_LIMIT_WINDOW = int(os.environ.get("THREAD_MSG_RATE_WINDOW", "60"))  # seconds

# H-10 fix: thread-safe sliding-window limiter (was a plain, unlocked dict
# mutated from multiple SocketIO worker threads — a real data race under
# async_mode='threading'). One shared, namespaced key per user below keeps
# this isolated from any other feature that also uses the same RateLimiter.
_thread_msg_rate_limiter = RateLimiter()


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
    Sliding-window rate limiter.
    Returns True (blocked) if the user has sent >= _RATE_LIMIT_MAX messages
    in the last _RATE_LIMIT_WINDOW seconds.

    Backed by the shared, lock-protected RateLimiter (see H-10) instead of
    a bare module-level dict — safe to call concurrently from multiple
    SocketIO worker threads.
    """
    allowed, _remaining = _thread_msg_rate_limiter.check_rate_limit(
        key=f"thread_msg_{user_id}",
        limit=_RATE_LIMIT_MAX,
        window=_RATE_LIMIT_WINDOW,
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
        # ARCH-02: tracks which thread each connected user is actively viewing
        # { user_id: thread_id }
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

    def _sanitize(self, text: str) -> str:
        """Strip all HTML — plain text only in thread messages."""
        if not text:
            return text
        return bleach.clean(text, tags=[], strip=True).strip()

    def _is_member(self, thread_id: int, user_id: int) -> "ThreadMember | None":
        return ThreadMember.query.filter_by(
            thread_id=thread_id,
            student_id=user_id
        ).first()

    def _is_moderator_or_creator(self, membership: "ThreadMember") -> bool:
        return membership.role in ("creator", "moderator")

    def _parse_mentions(self, text: str) -> list[int]:
        import re

        current_app.logger.debug(f"[MENTION_DEBUG] Input text: {text!r}")

        if not text:
            current_app.logger.debug(f"[MENTION_DEBUG] Empty text, returning []")
            return []

        pattern = r"@([\w\u00C0-\u00FF]+)"
        raw_matches = re.findall(pattern, text)
        current_app.logger.debug(f"[MENTION_DEBUG] Raw regex matches: {raw_matches}")

        mentioned_names = set(raw_matches)
        current_app.logger.debug(f"[MENTION_DEBUG] Unique matches after dedup: {mentioned_names}")

        found_ids = []
        for username in mentioned_names:
            current_app.logger.debug(f"[MENTION_DEBUG] Processing username: {username!r}")

            if username == "learnora":
                current_app.logger.debug(f"[MENTION_DEBUG] Skipping learnora trigger")
                continue

            username_lower = username
            current_app.logger.debug(f"[MENTION_DEBUG] Looking up user with username (case-insensitive): {username_lower!r}")

            user = User.query.filter(func.lower(User.username) == username_lower.lower()).first()

            if user:
                current_app.logger.debug(f"[MENTION_DEBUG] Found user: id={user.id}, username={user.username!r}, name={user.name!r}")
                found_ids.append(user.id)
            else:
                current_app.logger.debug(f"[MENTION_DEBUG] No user found for username: {username!r}")

            if len(found_ids) >= 20:
                current_app.logger.warning(f"[MENTION_DEBUG] Mentions capped at 20 for message, found={len(found_ids)}")
                break

        current_app.logger.info(f"[MENTION_RESULT] text={text[:100]!r}, found_mentions={found_ids}, unique_usernames={mentioned_names}")
        return found_ids

    def _build_message_payload(self, msg: "ThreadMessage", sender: "User") -> dict:
        """
        Serialize a ThreadMessage for WebSocket delivery.
        Issue 1: includes attachments[] array with legacy fallback.
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

        reactions = self._build_reactions(msg.id)

        # Issue 1: Build attachments list from ThreadMessageAttachment table,
        # falling back to legacy single-attachment columns for old messages.
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
            # Issue 1: new attachments array
            "attachments":     attachments_list,
            # Legacy single-attachment fields (kept for backward compat)
            "attachment_url":  attachments_list[0]["attachment_url"]  if attachments_list else None,
            "attachment_name": attachments_list[0]["attachment_name"] if attachments_list else None,
            "attachment_type": attachments_list[0]["attachment_type"] if attachments_list else None,
            "attachment_size": attachments_list[0]["attachment_size"] if attachments_list else None,
            "reactions":       reactions,
            "status":          getattr(msg, "status", "sent"),
            "sent_at":         msg.sent_at.isoformat() + "Z",
            "edited_at":       msg.edited_at.isoformat() + "Z" if msg.edited_at else None,
        }

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
        """
        bot_user = User.query.get(msg.sender_id)
        payload = {
            "id":             msg.id,
            "thread_id":      thread_id,
            "text_content":   text,
            "sender_id":      msg.sender_id,
            "sender": {
                "id":       msg.sender_id,
                "name":     bot_user.name if bot_user else "Learnora",
                "username": bot_user.username if bot_user else "learnora",
                "avatar":   bot_user.avatar if bot_user else None
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

            # Track active thread for presence-based status
            self.user_active_thread[user_id] = thread_id

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

            # Clean up active thread tracking
            if self.user_active_thread.get(user_id) == thread_id:
                del self.user_active_thread[user_id]

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

            FIX: per-user sliding-window rate limiter applied before persist.
            FIX: `status` included in both confirmation and broadcast payloads.

            Payload:
              thread_id        int  (required)
              text_content     str  (required unless attachment present)
              reply_to_id      int  (optional)
              attachment_url   str  (optional)
              attachment_name  str  (optional)
              attachment_type  str  (optional)
              attachment_size  int  (optional)
              client_temp_id   str  (optional — frontend dedup key)

            Emits:
              -> "new_thread_message"   to thread room (all members)
              -> "thread_message_sent"  to sender only (confirmation with real ID)
              -> "learnora_thinking"    to thread room if @learnora detected
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
            text_content   = data.get("text_content", "").strip()
            has_attachment = bool(data.get("attachments") or data.get("attachment_url"))

            current_app.logger.info(
                f"[MESSAGE_RECEIVED] "
                f"user_id={user_id} thread_id={thread_id} "
                f"client_temp_id={client_temp_id!r} "
                f"text_length={len(text_content)} "
                f"has_attachment={has_attachment} "
                f"sid={sid}"
            )

            try:
                reply_to_id     = data.get("reply_to_id")
                attachment_url  = data.get("attachment_url")
                attachment_name = data.get("attachment_name")
                attachment_type = data.get("attachment_type")
                attachment_size = data.get("attachment_size")

                # Issue 1: Extract attachments array with legacy single-field fallback
                attachments_data = data.get("attachments", [])
                if not attachments_data and data.get("attachment_url"):
                    attachments_data = [{
                        "attachment_url":  data.get("attachment_url"),
                        "attachment_name": data.get("attachment_name"),
                        "attachment_type": data.get("attachment_type"),
                        "attachment_size": data.get("attachment_size"),
                    }]

                # Validate attachment count
                MAX_ATTACHMENTS = 5
                if len(attachments_data) > MAX_ATTACHMENTS:
                    attachments_data = attachments_data[:MAX_ATTACHMENTS]
                    current_app.logger.warning(
                        f"[MESSAGE_ATTACHMENTS_CAPPED] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"capped_at={MAX_ATTACHMENTS}"
                    )

                # ── Validation ──────────────────────────────────────────
                if not thread_id:
                    current_app.logger.warning(
                        f"[MESSAGE_VALIDATION_FAILED] "
                        f"user_id={user_id} reason=missing_thread_id "
                        f"client_temp_id={client_temp_id!r}"
                    )
                    self._emit_error("thread_id required")
                    return

                if not text_content and not attachment_url:
                    current_app.logger.warning(
                        f"[MESSAGE_VALIDATION_FAILED] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"reason=empty_message client_temp_id={client_temp_id!r}"
                    )
                    self._emit_error("Message must have text or an attachment")
                    return

                if len(text_content) > MAX_MESSAGE_LENGTH:
                    current_app.logger.warning(
                        f"[MESSAGE_VALIDATION_FAILED] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"reason=text_too_long length={len(text_content)} "
                        f"max={MAX_MESSAGE_LENGTH} client_temp_id={client_temp_id!r}"
                    )
                    self._emit_error(f"Message too long (max {MAX_MESSAGE_LENGTH} characters)")
                    return

                # ── Rate limit ──────────────────────────────────────────
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

                # ── Membership ──────────────────────────────────────────
                membership = self._is_member(thread_id, user_id)
                if not membership:
                    current_app.logger.warning(
                        f"[MESSAGE_DENIED] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"reason=not_a_member client_temp_id={client_temp_id!r}"
                    )
                    self._emit_error("You are not a member of this thread")
                    return

                thread = Thread.query.get(thread_id)
                if not thread:
                    current_app.logger.warning(
                        f"[MESSAGE_DENIED] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"reason=thread_not_found client_temp_id={client_temp_id!r}"
                    )
                    self._emit_error("Thread not found")
                    return

                if not thread.is_open:
                    current_app.logger.warning(
                        f"[MESSAGE_DENIED] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"reason=thread_closed client_temp_id={client_temp_id!r}"
                    )
                    self._emit_error("This thread is closed")
                    return

                # ── Validate reply_to_id ────────────────────────────────
                if reply_to_id:
                    parent = ThreadMessage.query.filter_by(
                        id=reply_to_id,
                        thread_id=thread_id,
                        is_deleted=False
                    ).first()
                    if not parent:
                        current_app.logger.debug(
                            f"[MESSAGE_REPLY_REF_INVALID] "
                            f"user_id={user_id} thread_id={thread_id} "
                            f"reply_to_id={reply_to_id} — silently cleared"
                        )
                        reply_to_id = None  # silently drop invalid ref

                # ── Attachment metadata log ─────────────────────────────
                if has_attachment:
                    current_app.logger.info(
                        f"[MESSAGE_ATTACHMENT] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"attachment_name={attachment_name!r} "
                        f"attachment_type={attachment_type!r} "
                        f"attachment_size={attachment_size} "
                        f"client_temp_id={client_temp_id!r}"
                    )

                # ── Sanitize ────────────────────────────────────────────
                text_content = self._sanitize(text_content) if text_content else ""

                # ── Persist ─────────────────────────────────────────────
                t_persist_start = time.monotonic()

                current_app.logger.debug(
                    f"[MESSAGE_PERSIST_START] "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"client_temp_id={client_temp_id!r} "
                    f"reply_to_id={reply_to_id} "
                    f"has_attachment={has_attachment}"
                )

                # ── Determine initial delivery status based on member presence ──
                from services.websocket_messages import message_ws_manager

                members_except_sender = ThreadMember.query.filter(
                    ThreadMember.thread_id == thread_id,
                    ThreadMember.student_id != user_id
                ).all()
                other_ids = [m.student_id for m in members_except_sender]

                active_viewers = [
                    mid for mid in other_ids
                    if self.user_active_thread.get(mid) == thread_id
                ]
                online_non_viewers = [
                    mid for mid in other_ids
                    if mid in message_ws_manager.online_users
                    and self.user_active_thread.get(mid) != thread_id
                ]

                if active_viewers:
                    initial_status = 'read'
                elif online_non_viewers:
                    initial_status = 'delivered'
                else:
                    initial_status = 'sent'

                msg = ThreadMessage(
                    thread_id       = thread_id,
                    sender_id       = user_id,
                    text_content    = text_content,
                    reply_to_id     = reply_to_id,
                    attachment_url  = attachment_url,
                    attachment_name = attachment_name,
                    attachment_type = attachment_type,
                    attachment_size = attachment_size,
                    is_ai_response  = False,
                    status          = initial_status,
                    sent_at         = datetime.datetime.utcnow()
                )
                db.session.add(msg)
                db.session.flush()

                # Issue 1: Create ThreadMessageAttachment rows for multi-file support.
                # The legacy single-attachment columns on ThreadMessage are left NULL
                # for new messages; legacy rows keep their columns for backward compat.
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

                # After flush, msg.id is assigned — log it alongside client_temp_id
                # so duplicate detection can correlate both IDs going forward.
                current_app.logger.debug(
                    f"[MESSAGE_FLUSHED] "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"msg_id={msg.id} client_temp_id={client_temp_id!r} "
                    f"— real ID now known; client should map temp→real on ack"
                )

                # ── Atomic counter updates ──────────────────────────────
                ThreadMember.query.filter_by(
                    thread_id=thread_id, student_id=user_id
                ).update(
                    {ThreadMember.messages_sent: ThreadMember.messages_sent + 1},
                    synchronize_session=False
                )
                Thread.query.filter_by(id=thread_id).update(
                    {
                        Thread.message_count: Thread.message_count + 1,
                        Thread.last_activity: datetime.datetime.utcnow()
                    },
                    synchronize_session=False
                )

                # ── Mentions ────────────────────────────────────────────
                mentioned_ids = self._parse_mentions(text_content)
                for mid in mentioned_ids:
                    if mid == user_id:
                        continue
                    db.session.add(Mention(
                        mentioned_in_type    = "thread_message",
                        mentioned_in_id      = msg.id,
                        mentioned_user_id    = mid,
                        mentioned_by_user_id = user_id
                    ))
                    db.session.add(Notification(
                        user_id           = mid,
                        title             = "You were mentioned in a thread",
                        body              = f"{text_content[:80]}...",
                        notification_type = "thread_mention",
                        related_type      = "thread",
                        related_id        = thread_id
                    ))

                if mentioned_ids:
                    current_app.logger.info(
                        f"[MESSAGE_MENTIONS] "
                        f"user_id={user_id} thread_id={thread_id} "
                        f"msg_id={msg.id} mentioned_user_ids={mentioned_ids} "
                        f"notification_count={len(mentioned_ids)}"
                    )

                db.session.commit()

                t_persist_ms = (time.monotonic() - t_persist_start) * 1000
                current_app.logger.info(
                    f"[MESSAGE_PERSISTED] "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"msg_id={msg.id} client_temp_id={client_temp_id!r} "
                    f"has_attachment={has_attachment} reply_to_id={reply_to_id} "
                    f"mention_count={len(mentioned_ids)} "
                    f"persist_ms={t_persist_ms:.1f}"
                )

                # ── Build and broadcast ──────────────────────────────────
                sender  = User.query.get(user_id)
                payload = self._build_message_payload(msg, sender)
                payload["client_temp_id"] = client_temp_id

                # Broadcast to ALL room members (including sender's other tabs).
                # NOTE: if a duplicate new_thread_message arrives for the same
                # msg_id or client_temp_id, the client dedup layer should
                # discard it — check client-side dedup on reconnect.
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
                if text_content:
                    preview_text = text_content[:80]
                elif attachments_data:
                    type_map = {"image": "📷 Image", "video": "🎬 Video", "document": "📎 File"}
                    first_type = (attachments_data[0].get("attachment_type") or "document")
                    preview_text = type_map.get(first_type, "📎 Attachment")
                elif attachment_url:
                    preview_text = "📎 Attachment"
                else:
                    preview_text = ""

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

                # ── Learnora trigger ────────────────────────────────────
                lower = text_content.lower()
                matched_personality = next((p for t, p in _TRIGGER_MAP.items() if t in lower), None)

                if matched_personality:
                    self.broadcast_to_thread(thread_id, "learnora_thinking", {
                        "thread_id": thread_id,
                        "personality": matched_personality["display_name"]
                    })
                    app_ref = current_app._get_current_object()
                    threading.Thread(
                        target=_call_learnora_for_thread,
                        args=(app_ref, thread_id, text_content, user_id, matched_personality),
                        daemon=True
                    ).start()

                # Auto-reply: if the user replied to an AI message without a manual trigger,
                # let the AI continue the conversation (rate-limited 3 per 5 min per user/thread)
                if reply_to_id and not matched_personality:
                    parent_msg = ThreadMessage.query.filter_by(
                        id=reply_to_id, is_ai_response=True, is_deleted=False
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
                            threading.Thread(
                                target=_call_learnora_for_thread,
                                args=(current_app._get_current_object(), thread_id, text_content,
                                      user_id, personality, msg.id),
                                daemon=True
                            ).start()

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

                if len(new_text) > MAX_MESSAGE_LENGTH:
                    current_app.logger.warning(
                        f"[MESSAGE_EDIT_VALIDATION_FAILED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=text_too_long length={len(new_text)}"
                    )
                    self._emit_error(f"Message too long (max {MAX_MESSAGE_LENGTH} chars)")
                    return

                msg = ThreadMessage.query.filter_by(
                    id=message_id,
                    sender_id=user_id,
                    is_deleted=False
                ).first()

                if not msg:
                    current_app.logger.warning(
                        f"[MESSAGE_EDIT_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=not_found_or_not_owner"
                    )
                    self._emit_error("Message not found or you don't own it")
                    return

                if msg.is_ai_response:
                    current_app.logger.warning(
                        f"[MESSAGE_EDIT_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=ai_message_not_editable"
                    )
                    self._emit_error("AI messages cannot be edited")
                    return

                membership = self._is_member(msg.thread_id, user_id)
                if membership and not self._is_moderator_or_creator(membership):
                    seconds_old = (datetime.datetime.utcnow() - msg.sent_at).total_seconds()
                    if seconds_old > EDIT_WINDOW_SECONDS:
                        current_app.logger.warning(
                            f"[MESSAGE_EDIT_DENIED] "
                            f"user_id={user_id} message_id={message_id} "
                            f"thread_id={msg.thread_id} "
                            f"reason=edit_window_expired "
                            f"age_seconds={seconds_old:.0f} "
                            f"limit={EDIT_WINDOW_SECONDS}s role={membership.role}"
                        )
                        self._emit_error("Edit window expired (15 minutes)")
                        return

                old_preview      = msg.text_content[:60]
                msg.text_content = self._sanitize(new_text)
                msg.is_edited    = True
                msg.edited_at    = datetime.datetime.utcnow()
                db.session.commit()

                current_app.logger.info(
                    f"[MESSAGE_EDITED] "
                    f"user_id={user_id} message_id={message_id} "
                    f"thread_id={msg.thread_id} "
                    f"old_preview={old_preview!r} "
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

                msg = ThreadMessage.query.filter_by(
                    id=message_id, is_deleted=False
                ).first()
                if not msg:
                    current_app.logger.warning(
                        f"[MESSAGE_DELETE_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"reason=not_found_or_already_deleted"
                    )
                    self._emit_error("Message not found")
                    return

                membership = self._is_member(msg.thread_id, user_id)
                if not membership:
                    current_app.logger.warning(
                        f"[MESSAGE_DELETE_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"thread_id={msg.thread_id} reason=not_a_member"
                    )
                    self._emit_error("Not a thread member")
                    return

                is_own_message = msg.sender_id == user_id
                is_privileged  = self._is_moderator_or_creator(membership)

                if not is_own_message and not is_privileged:
                    current_app.logger.warning(
                        f"[MESSAGE_DELETE_DENIED] "
                        f"user_id={user_id} message_id={message_id} "
                        f"thread_id={msg.thread_id} "
                        f"original_sender_id={msg.sender_id} "
                        f"role={membership.role} reason=insufficient_permissions"
                    )
                    self._emit_error("You cannot delete this message")
                    return

                delete_type      = "own" if is_own_message else "moderation"
                msg.is_deleted   = True
                msg.text_content = "[deleted]"

                from sqlalchemy import case
                Thread.query.filter_by(id=msg.thread_id).update(
                    {Thread.message_count: case(
                        (Thread.message_count > 0, Thread.message_count - 1),
                        else_=0
                    )},
                    synchronize_session=False
                )

                db.session.commit()

                current_app.logger.info(
                    f"[MESSAGE_DELETED] "
                    f"user_id={user_id} message_id={message_id} "
                    f"thread_id={msg.thread_id} "
                    f"original_sender_id={msg.sender_id} "
                    f"delete_type={delete_type}"
                )

                self.broadcast_to_thread(msg.thread_id, "thread_message_deleted", {
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
                t = threading.Thread(
                    target=_call_learnora_for_thread,
                    args=(app_ref, thread_id, trigger_text, user_id),
                    daemon=True
                )
                t.start()

                current_app.logger.info(
                    f"[AI_REQUEST_DISPATCHED] "
                    f"user_id={user_id} thread_id={thread_id} "
                    f"mode={mode!r} daemon_thread={t.name}"
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

            threading.Thread(
                target=_call_learnora_action,
                args=(current_app._get_current_object(), thread_id, message_id, action,
                      data.get("target_lang"), user_id),
                daemon=True
            ).start()


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
            personality = personality or AI_PERSONALITIES["learnora"]
            base_system = personality["system_prompt"]

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
            from learnora import provider_manager, _call_provider_sync
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

        from learnora import provider_manager, _call_provider_sync
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

        try:
            db.session.commit()
            thread_ws_manager.broadcast_ai_message(thread_id, bot_msg, ai_text)
        except Exception:
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
