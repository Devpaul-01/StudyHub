"""
StudyHub - Threads: Chat/messaging REST fallback (messages, edit/delete/pin, search, attachment upload)

Split from threads.py per Document 1 (Architecture Refactor) §2.2 as part
of Phase 2 (God-file splitting). This is a pure move — function bodies,
decorators, routes, and logic are unchanged from the original threads.py.
See routes/student/threads/__init__.py for the sub-blueprint aggregation
that re-exposes all routes under the same paths as before.
"""

from flask import Blueprint, request, jsonify, current_app, render_template
from sqlalchemy import or_, and_, func, desc, case
import datetime
import mimetypes
import secrets
import json as _json

from services.storage import cloudinary_storage, supabase_storage, FilenameService
import bleach

from models import (
    User, StudentProfile, Thread, ThreadMember, ThreadJoinRequest,
    ThreadMessage, ThreadMessageReaction, ThreadMessageAttachment,
    Post, Connection,
    Mention, OnboardingDetails,
    ThreadMeetingNote,
)
from extensions import db
from errors import ValidationError
from routes.student.helpers import (
    token_required, success_response, error_response
)

from services.ai_provider_service import call_ai_response
from services.thread_authorization import is_moderator_or_creator, require_moderator_or_creator
from services import notification_service
# Phase 5b (Document 4 §1): WRITE_HEAVY for send/edit/delete/upload,
# BURST_OK for the message-list GET (polled frequently by chat UIs).
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key, ip_key

import sys
import os
import re as _re
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

threads_messaging_bp = Blueprint("threads_messaging", __name__)
def detect_mentions_in_thread(text_content, sender_id, thread_id, message_id):
    """Detect @username mentions in thread messages."""
    if not text_content:
        return []

    import re
    mention_pattern = r'@([a-zA-Z0-9_]{3,20})'
    matches = re.finditer(mention_pattern, text_content)
    mentioned_users = []
    sender = User.query.get(sender_id)

    for match in matches:
        username = match.group(1).lower()
        mentioned_user = User.query.filter_by(username=username).first()

        if mentioned_user and mentioned_user.id != sender_id:
            is_member = ThreadMember.query.filter_by(
                thread_id=thread_id,
                student_id=mentioned_user.id
            ).first()

            if is_member:
                existing = Mention.query.filter_by(
                    mentioned_in_type="thread_message",
                    mentioned_in_id=message_id,
                    mentioned_user_id=mentioned_user.id,
                    mentioned_by_user_id=sender_id
                ).first()

                if not existing:
                    mention = Mention(
                        mentioned_in_type="thread_message",
                        mentioned_in_id=message_id,
                        mentioned_user_id=mentioned_user.id,
                        mentioned_by_user_id=sender_id
                    )
                    db.session.add(mention)

                    # AUDIT ENG-3 FIX: migrated off a direct Notification(...)
                    # construction to notification_service.notify() — see
                    # post_service.py::detect_and_create_mentions for the
                    # identical fix and full reasoning. Same fields, same
                    # values, unchanged behavior otherwise.
                    notification_service.notify(
                        user_id=mentioned_user.id,
                        title=f"{sender.name} mentioned you in a thread",
                        body="",
                        notification_type="mention",
                        related_type="thread",
                        related_id=thread_id,
                    )
                    mentioned_users.append(mentioned_user.id)

    return mentioned_users


# Document 1 §2.2 / Document 2 §3.8: _is_mod_or_creator_static moved to
# services/thread_authorization.py as is_moderator_or_creator() — that
# module is now the single implementation shared by this REST blueprint
# AND services/websocket_threads.py, closing the REST/WebSocket
# duplication named in Document 1 §2.2's table.


# ============================================================================
# THREAD CREATION
# ============================================================================

@threads_messaging_bp.route("/threads/<int:thread_id>/messages", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def get_thread_messages(current_user, thread_id):
    """
    Fetch thread messages with cursor-based pagination.

    Query params:
      before_id   int   — return messages with id < before_id (load older)
      after_id    int   — return messages with id > after_id  (load newer / poll)
      limit       int   — max results, default 30, max 50
    """
    try:
        membership = ThreadMember.query.filter_by(
            thread_id=thread_id, student_id=current_user.id
        ).first()
        if not membership:
            return error_response("You are not a member of this thread", 403)

        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)

        before_id = request.args.get("before_id", type=int)
        after_id  = request.args.get("after_id",  type=int)
        limit     = min(request.args.get("limit", 30, type=int), 50)

        query = ThreadMessage.query.filter_by(thread_id=thread_id, is_deleted=False)
        if before_id:
            query = query.filter(ThreadMessage.id < before_id)
        elif after_id:
            query = query.filter(ThreadMessage.id > after_id)

        raw_messages = query.order_by(ThreadMessage.id.desc()).limit(limit + 1).all()

        has_more = len(raw_messages) > limit
        if has_more:
            raw_messages = raw_messages[:limit]
        raw_messages.reverse()

        sender_ids     = {m.sender_id for m in raw_messages}
        senders        = {u.id: u for u in User.query.filter(User.id.in_(sender_ids)).all()}
        reply_ids      = {m.reply_to_id for m in raw_messages if m.reply_to_id}
        parents        = {p.id: p for p in ThreadMessage.query.filter(
            ThreadMessage.id.in_(reply_ids)
        ).all()} if reply_ids else {}
        parent_sender_ids = {p.sender_id for p in parents.values()}
        parent_senders    = {u.id: u for u in User.query.filter(
            User.id.in_(parent_sender_ids)
        ).all()} if parent_sender_ids else {}

        msg_ids  = [m.id for m in raw_messages]
        all_rxns = ThreadMessageReaction.query.filter(
            ThreadMessageReaction.message_id.in_(msg_ids)
        ).all() if msg_ids else []
        rxn_map: dict = {}
        for r in all_rxns:
            rxn_map.setdefault(r.message_id, {})
            rxn_map[r.message_id].setdefault(r.emoji, {"emoji": r.emoji, "count": 0, "users": []})
            rxn_map[r.message_id][r.emoji]["count"] += 1
            rxn_map[r.message_id][r.emoji]["users"].append(r.user_id)

        # Issue 1: Batch-load attachments to avoid N+1 queries
        all_att = ThreadMessageAttachment.query.filter(
            ThreadMessageAttachment.message_id.in_(msg_ids)
        ).order_by(ThreadMessageAttachment.sort_order).all() if msg_ids else []
        att_map: dict = {}
        for a in all_att:
            att_map.setdefault(a.message_id, []).append(a.to_dict())

        def serialize_message(msg):
            sender        = senders.get(msg.sender_id)
            reply_preview = None
            if msg.reply_to_id and msg.reply_to_id in parents:
                parent = parents[msg.reply_to_id]
                ps     = parent_senders.get(parent.sender_id)
                reply_preview = {
                    "id":        parent.id,
                    "text":      parent.text_content[:120],
                    "sender":    ps.name if ps else "Unknown",
                    "sender_id": parent.sender_id
                }
            return {
                "id":              msg.id,
                "sender_id":       msg.sender_id,
                "sender": {
                    "id":       sender.id,
                    "name":     sender.name,
                    "username": sender.username,
                    "avatar":   sender.avatar
                } if sender else None,
                "text_content":    msg.text_content,
                "is_edited":       msg.is_edited,
                "is_pinned":       msg.is_pinned,
                "is_ai_response":  msg.is_ai_response,
                "reply_to":        reply_preview,
                "reply_to_id":     msg.reply_to_id,
                # Issue 1: attachments array (new) with legacy fallback
                "attachments": (lambda al: al if al else ([{
                    "attachment_url":  msg.attachment_url,
                    "attachment_name": msg.attachment_name,
                    "attachment_type": msg.attachment_type,
                    "attachment_size": msg.attachment_size,
                    "sort_order":      0,
                }] if msg.attachment_url else []))(att_map.get(msg.id, [])),
                "attachment_url":  msg.attachment_url,
                "attachment_name": msg.attachment_name,
                "attachment_type": msg.attachment_type,
                "attachment_size": msg.attachment_size,
                "reactions":       rxn_map.get(msg.id, {}),
                "status":          getattr(msg, "status", "sent"),  # FIX: fallback for pre-migration rows
                "sent_at":         msg.sent_at.isoformat() + "Z",
                "edited_at":       msg.edited_at.isoformat() + "Z" if msg.edited_at else None,
            }

        messages_data = [serialize_message(m) for m in raw_messages]

        pinned = ThreadMessage.query.filter_by(
            thread_id=thread_id, is_pinned=True, is_deleted=False
        ).order_by(ThreadMessage.id.desc()).limit(5).all()
        pinned_data = [serialize_message(p) for p in pinned]

        # Document 4 §4 (C-2): only touch last_read_at when this page
        # actually contains something new to mark as read — on the common
        # "no new messages" polling case (after_id with nothing newer, or
        # re-fetching a page with nothing unread) this skips the UPDATE
        # and, more importantly, the commit entirely.
        cutoff = membership.last_read_at or datetime.datetime(2000, 1, 1)
        unread_in_page = any(
            m.sent_at > cutoff and m.sender_id != current_user.id
            for m in raw_messages
        )
        if unread_in_page:
            ThreadMember.query.filter_by(
                thread_id=thread_id, student_id=current_user.id
            ).update(
                {ThreadMember.last_read_at: datetime.datetime.utcnow()},
                synchronize_session=False
            )
            db.session.commit()

        return jsonify({
            "status": "success",
            "data": {
                "messages":        messages_data,
                "has_more":        has_more,
                "oldest_id":       raw_messages[0].id if raw_messages else None,
                "pinned_messages": pinned_data
            }
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Get thread messages error: {e}")
        return error_response("Failed to load messages")


ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain", "text/csv",
    "video/mp4", "video/quicktime"
}
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


@threads_messaging_bp.route("/threads/<int:thread_id>/messages/upload", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def upload_thread_attachment(current_user, thread_id):
    """
    Upload an attachment for a thread message.
    FIX: now uses Cloudinary instead of Supabase.

    Returns:
      attachment_url, attachment_name, attachment_type, attachment_size
    """
    try:
        membership = ThreadMember.query.filter_by(
            thread_id=thread_id, student_id=current_user.id
        ).first()
        if not membership:
            return error_response("You are not a member of this thread", 403)

        if "file" not in request.files:
            return error_response("No file provided")

        file = request.files["file"]
        if not file.filename:
            return error_response("Empty filename")

        mime_type = mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
        if mime_type not in ALLOWED_MIME_TYPES:
            return error_response(f"File type not allowed: {mime_type}")

        file.seek(0, 2)
        file_size = file.tell()
        file.seek(0)
        if file_size > MAX_FILE_SIZE_BYTES:
            return error_response(f"File too large (max {MAX_FILE_SIZE_BYTES // 1024 // 1024} MB)")

        if not cloudinary_storage:
            return error_response("Storage not configured", 503)

        file_category = FilenameService.get_file_category(file.filename)
        now           = datetime.datetime.utcnow()
        token         = secrets.token_hex(8)
        ext           = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "bin"
        filename      = f"thread_msg_{current_user.id}_{token}.{ext}"
        folder        = f"threads/{file_category}s/{now.year}/{now.month:02d}"

        # Determine Cloudinary resource_type
        if mime_type.startswith("image/"):
            resource_type = "image"
        elif mime_type.startswith("video/"):
            resource_type = "video"
        else:
            resource_type = "raw"

        # Document 3 §3: the mimetypes.guess_type() check above is
        # extension-based and can be spoofed. Images get re-encoded from
        # decoded pixel data (structurally rules out SVG/polyglot content);
        # non-image, non-video document types get their real magic-number
        # signature checked against the claimed mime, where one exists.
        from services.upload_validation_service import (
            validate_and_normalize_image, validate_document_mime,
        )
        upload_target = file
        if resource_type == "image":
            try:
                upload_target = validate_and_normalize_image(file)
            except ValidationError as e:
                return error_response(str(e))
        elif resource_type == "raw":
            try:
                validate_document_mime(file, {mime_type})
            except ValidationError as e:
                return error_response(str(e))

        result = cloudinary_storage.upload_file(
            file=upload_target, folder=folder, filename=filename, resource_type=resource_type
        )

        if not result["success"]:
            current_app.logger.error(f"Thread attachment upload failed: {result['error']}")
            return error_response("Failed to upload attachment")

        return jsonify({
            "status": "success",
            "data": {
                "attachment_url":  result["url"],
                "attachment_name": file.filename,
                "attachment_type": file_category,
                "attachment_size": file_size
            }
        }), 201

    except Exception as e:
        current_app.logger.error(f"Thread attachment upload error: {e}")
        return error_response("Failed to upload attachment")


@threads_messaging_bp.route("/threads/<int:thread_id>/messages/search", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def search_thread_messages(current_user, thread_id):
    """Search messages within a thread by keyword."""
    try:
        membership = ThreadMember.query.filter_by(
            thread_id=thread_id, student_id=current_user.id
        ).first()
        if not membership:
            return error_response("You are not a member of this thread", 403)

        q     = (request.args.get("q") or "").strip()
        limit = min(request.args.get("limit", 20, type=int), 50)

        if len(q) < 2:
            return error_response("Search term must be at least 2 characters")

        matches = (
            ThreadMessage.query
            .filter(
                ThreadMessage.thread_id == thread_id,
                ThreadMessage.is_deleted == False,
                ThreadMessage.text_content.ilike(f"%{q}%")
            )
            .order_by(ThreadMessage.id.desc())
            .limit(limit)
            .all()
        )

        results = []
        for msg in matches:
            sender = User.query.get(msg.sender_id)
            results.append({
                "id":           msg.id,
                "text_content": msg.text_content,
                "sender": {
                    "id":     sender.id,
                    "name":   sender.name,
                    "avatar": sender.avatar
                } if sender else None,
                "sent_at":   msg.sent_at.isoformat() + "Z",
                "is_pinned": msg.is_pinned
            })

        return jsonify({
            "status": "success",
            "data": {"results": results, "total": len(results), "query": q}
        })

    except Exception as e:
        current_app.logger.error(f"Search thread messages error: {e}")
        return error_response("Search failed")


@threads_messaging_bp.route("/threads/<int:thread_id>/messages/pinned", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def get_pinned_messages(current_user, thread_id):
    """Return all pinned messages for a thread (members only)."""
    try:
        membership = ThreadMember.query.filter_by(
            thread_id=thread_id, student_id=current_user.id
        ).first()
        if not membership:
            return error_response("You are not a member of this thread", 403)

        pinned = ThreadMessage.query.filter_by(
            thread_id=thread_id, is_pinned=True, is_deleted=False
        ).order_by(ThreadMessage.id.desc()).all()

        results = []
        for msg in pinned:
            sender    = User.query.get(msg.sender_id)
            pinned_by = User.query.get(msg.pinned_by_id) if msg.pinned_by_id else None
            results.append({
                "id":           msg.id,
                "text_content": msg.text_content,
                "sender": {
                    "id": sender.id, "name": sender.name
                } if sender else None,
                "pinned_by": {
                    "id": pinned_by.id, "name": pinned_by.name
                } if pinned_by else None,
                "sent_at":        msg.sent_at.isoformat() + "Z",
                "attachment_url": msg.attachment_url
            })

        return jsonify({"status": "success", "data": {"pinned_messages": results}})

    except Exception as e:
        current_app.logger.error(f"Get pinned messages error: {e}")
        return error_response("Failed to load pinned messages")


@threads_messaging_bp.route("/threads/<int:thread_id>/messages", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def send_thread_message(current_user, thread_id):
    """
    Send message in thread (REST fallback — primary path is WebSocket).

    HORIZONTAL SCALING BATCH 2 (01-DESIGN-horizontal-scaling.md §13):
    now delegates to services.thread_message_service.create_thread_message
    — the exact same validation, persistence, attachment handling,
    presence-based initial status, and mention detection the WebSocket
    handler uses, instead of the much thinner logic this route previously
    had of its own. This closes two real gaps found when comparing the
    two implementations line by line:

      1. This route never broadcast anything — a member connected via
         WebSocket right now would never see a REST-sent message arrive
         live, only on their next poll/reload. Now broadcasts
         "new_thread_message" to the thread room exactly like the WS
         handler does, via the shared thread_ws_manager instance
         (deferred import + try/except, matching the exact pattern
         already used elsewhere in this codebase for REST-route ->
         WebSocket pushes — see crud.py::update_thread /
         upload_thread_avatar, membership.py::remove_member, etc.).
      2. This route had none of: attachment support, reply_to_id,
         presence-based initial status (previously hardcoded 'sent'
         regardless of whether anyone was actually online/viewing).
         Support for these is now accepted from the request body,
         optional, so existing callers sending only {"text_content": ...}
         keep working identically.

    Deliberately NOT changed: the HTTP response shape. Per the refactor's
    explicit "don't change payload schemas unless necessary" instruction,
    this still returns exactly {"message_id": ..., "sent_at": ...} on
    success — not the full WebSocket message payload — since expanding it
    could break any existing frontend code parsing this specific REST
    response. The full payload IS what gets broadcast to other members
    (so a WS-connected member sees the identical shape whether the
    message came from REST or WebSocket), just not what's returned to the
    sender's own HTTP request.

    Deliberately NOT ported: the WebSocket handler's per-socket rate
    limiter (RedisFixedWindowLimiter) and client_temp_id handling — see
    thread_message_service.create_thread_message's own docstring for why
    (this route already has its own WRITE_HEAVY Flask-Limiter tier via
    the decorator above; client_temp_id has no REST equivalent to
    reconcile against). The Learnora AI-trigger dispatch is also
    deliberately NOT wired up here — see the note further below.
    """
    from services import thread_message_service as tms

    try:
        data = request.get_json() or {}
        text_content = (data.get("text_content") or "").strip()
        reply_to_id = data.get("reply_to_id")

        # Accept the same attachments[] shape the WS handler accepts,
        # with the identical legacy single-field fallback, so a frontend
        # that already builds this payload for the WS path can send the
        # exact same body to this REST fallback with zero translation.
        attachments_data = data.get("attachments") or []
        if not attachments_data and data.get("attachment_url"):
            attachments_data = [{
                "attachment_url":  data.get("attachment_url"),
                "attachment_name": data.get("attachment_name"),
                "attachment_type": data.get("attachment_type"),
                "attachment_size": data.get("attachment_size"),
            }]

        try:
            result = tms.create_thread_message(
                user_id=current_user.id,
                thread_id=thread_id,
                text_content=text_content,
                reply_to_id=reply_to_id,
                attachments_data=attachments_data,
            )
        except tms.NotAMemberError as e:
            return error_response(str(e), 403)
        except tms.ThreadNotFoundError as e:
            return error_response(str(e), 404)
        except tms.ThreadClosedError as e:
            return error_response(str(e), 403)
        except tms.ValidationFailedError as e:
            return error_response(str(e))

        # ── Broadcast to the thread room, matching the WS handler's own
        # broadcast exactly (same event name, same payload shape) — see
        # docstring above for why the HTTP response itself stays smaller. ──
        try:
            from services.websocket_threads import thread_ws_manager
            thread_ws_manager.broadcast_to_thread(
                thread_id, "new_thread_message", result.payload
            )
            for mid in result.other_member_ids:
                preview_text = result.text_content[:80] if result.text_content else (
                    "📎 Attachment" if result.attachments_data else ""
                )
                thread_ws_manager.notify_user(mid, "thread_list_update", {
                    "thread_id": thread_id,
                    "last_message": {
                        "text":      preview_text,
                        "sender":    current_user.name,
                        "sender_id": current_user.id,
                        "sent_at":   result.message.sent_at.isoformat() + "Z",
                        "status":    result.message.status,
                    },
                    "last_activity": result.message.sent_at.isoformat() + "Z",
                })
        except Exception as ws_err:
            current_app.logger.warning(
                f"[SEND_THREAD_MESSAGE_WS_FAILED] thread_id={thread_id} "
                f"message_id={result.message.id} error={ws_err!r}"
            )

        # Learnora AI-trigger dispatch is deliberately NOT wired up on
        # this REST fallback path. The WebSocket handler's trigger is a
        # fire-and-forget background thread kicked off from within a live
        # socket-connected request — appropriate there since the AI's
        # reply arrives back over the same live connection moments later.
        # A REST caller has no open connection to receive that reply on;
        # they'd need to separately poll or reconnect via WebSocket to
        # ever see it, which makes triggering it from here silently
        # start background work whose result the REST caller can't
        # observe through the API they just called. Flagged as a
        # deliberate scope boundary, not an oversight — surface if you
        # want REST-triggered AI replies delivered via a different
        # mechanism (e.g. included synchronously in this response).

        return success_response(
            "Message sent",
            data={"message_id": result.message.id, "sent_at": result.message.sent_at.isoformat()}
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Send message error: {str(e)}")
        return error_response("Failed to send message")


@threads_messaging_bp.route("/threads/<int:thread_id>/messages/<int:message_id>", methods=["PATCH"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def edit_thread_message(current_user, thread_id, message_id):
    """
    Edit your own message.

    HORIZONTAL SCALING BATCH 2 (01-DESIGN-horizontal-scaling.md §13): now
    delegates to services.thread_message_service.edit_thread_message,
    closing two real gaps found comparing this route to the WebSocket
    handler line by line:

      1. This route never broadcast "thread_message_edited" — a
         WS-connected member watching this thread would never see a
         REST-made edit reflected live.
      2. This route enforced NO 15-minute edit window and had no
         moderator/creator bypass concept at all (WS allows a moderator
         or creator to edit past the window; REST previously let ANY
         sender edit at any time with no window whatsoever — actually a
         permissiveness gap in the opposite direction from most of the
         other REST/WS divergences found in this file). Now enforces the
         identical window/bypass logic as WebSocket.

    KEPT AS A DELIBERATE REST-SPECIFIC ADDITION, not ported into the
    shared service: mention re-detection on edit (delete old Mention
    rows for this message, re-parse the new text for @mentions). The
    WebSocket edit handler does NOT do this — confirmed by reading it —
    so porting this into thread_message_service.edit_thread_message
    would silently change WebSocket's existing behavior too, which
    wasn't asked for and isn't obviously correct either way (re-parsing
    mentions on every edit could re-notify someone already mentioned in
    the original text). Kept here, layered on top of the shared call, so
    this route's pre-existing behavior for its own callers is preserved
    exactly while still gaining the window-enforcement/broadcast fix
    above. Flagged explicitly rather than silently resolved either
    direction — worth a product decision if you want these to match.
    """
    from services import thread_message_service as tms

    try:
        # Preserve this route's existing behavior: message must belong to
        # the thread_id in the URL. thread_message_service.edit_thread_message
        # looks up by message_id + sender_id only (matching the WS
        # handler, which has no thread_id in its payload to cross-check
        # against) — so this check stays here, before delegating, exactly
        # where the REST route already had it.
        message = ThreadMessage.query.get(message_id)
        if not message:
            return error_response("Message not found", 404)
        if message.thread_id != thread_id:
            return error_response("Message does not belong to this thread", 400)

        data = request.get_json() or {}
        new_text = (data.get("text_content") or "").strip()

        try:
            result = tms.edit_thread_message(
                user_id=current_user.id, message_id=message_id, new_text=new_text,
            )
        except tms.ValidationFailedError as e:
            return error_response(str(e))
        except tms.MessageNotFoundError as e:
            # thread_message_service's MessageNotFoundError here means
            # "found by id but sender_id didn't match" (see its filter),
            # which is this route's pre-existing 403 case, not a 404 —
            # the existence check above already ruled out a true 404.
            return error_response("You can only edit your own messages", 403)
        except tms.PermissionDeniedError as e:
            return error_response(str(e), 403)
        except tms.EditWindowExpiredError as e:
            return error_response(str(e), 403)

        # ── REST-specific: mention re-detection (see docstring above) ──
        Mention.query.filter_by(
            mentioned_in_type="thread_message", mentioned_in_id=message_id
        ).delete()
        detect_mentions_in_thread(result.message.text_content, current_user.id, thread_id, message_id)
        db.session.commit()

        # ── Broadcast, matching the WS handler's own event/payload exactly ──
        try:
            from services.websocket_threads import thread_ws_manager
            thread_ws_manager.broadcast_to_thread(thread_id, "thread_message_edited", {
                "message_id":   message_id,
                "text_content": result.message.text_content,
                "edited_at":    result.message.edited_at.isoformat() + "Z",
            })
        except Exception as ws_err:
            current_app.logger.warning(
                f"[EDIT_THREAD_MESSAGE_WS_FAILED] thread_id={thread_id} "
                f"message_id={message_id} error={ws_err!r}"
            )

        return success_response("Message updated", data={"edited_at": result.message.edited_at.isoformat()})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Edit message error: {str(e)}")
        return error_response("Failed to edit message")


@threads_messaging_bp.route("/threads/<int:thread_id>/messages/<int:message_id>", methods=["DELETE"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def delete_thread_message(current_user, thread_id, message_id):
    """
    Delete your own message (soft delete).

    HORIZONTAL SCALING BATCH 2 (01-DESIGN-horizontal-scaling.md §13): now
    delegates to services.thread_message_service.delete_thread_message,
    fixing a genuine permission BUG this route had — not just a missing
    broadcast. The old check here was:

        message.sender_id != current_user.id and thread.creator_id != current_user.id

    i.e. only the sender or the thread CREATOR could delete via REST. The
    WebSocket handler has always allowed sender OR moderator-OR-creator
    (is_moderator_or_creator). A thread moderator (promoted, not the
    original creator) could therefore delete a message via WebSocket but
    got a 403 calling this exact same logical action through the REST
    fallback — a real behavioral divergence for the identical operation,
    not a cosmetic gap. Both callers now share the identical permission
    model.

    Also now broadcasts "thread_message_deleted" (previously silent —
    WS-connected members never saw a REST-made deletion reflected live).
    """
    from services import thread_message_service as tms

    try:
        try:
            result = tms.delete_thread_message(user_id=current_user.id, message_id=message_id)
        except tms.MessageNotFoundError as e:
            return error_response(str(e), 404)
        except tms.PermissionDeniedError as e:
            return error_response("You cannot delete this message", 403)

        if result.thread_id != thread_id:
            # The message existed and the caller was allowed to delete
            # it, but it belongs to a DIFFERENT thread than the URL says.
            # thread_message_service already committed the delete by this
            # point (matching the WS handler, which has no thread_id in
            # its payload to cross-check against and so has no equivalent
            # guard at all) — this mismatch almost certainly indicates a
            # client-side bug (wrong thread_id in the URL) rather than
            # anything to roll back. Logged, not treated as a hard error,
            # since the delete itself was legitimate for the message's
            # actual thread.
            current_app.logger.warning(
                f"[DELETE_THREAD_MESSAGE_THREAD_MISMATCH] url_thread_id={thread_id} "
                f"actual_thread_id={result.thread_id} message_id={message_id} "
                f"user_id={current_user.id}"
            )

        try:
            from services.websocket_threads import thread_ws_manager
            thread_ws_manager.broadcast_to_thread(result.thread_id, "thread_message_deleted", {
                "message_id": message_id,
                "deleted_by": current_user.id,
            })
        except Exception as ws_err:
            current_app.logger.warning(
                f"[DELETE_THREAD_MESSAGE_WS_FAILED] thread_id={result.thread_id} "
                f"message_id={message_id} error={ws_err!r}"
            )

        return success_response("Message deleted")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete message error: {str(e)}")
        return error_response("Failed to delete message")


# ============================================================================
# MY THREADS
# FIX: includes last_message preview and avatar in response
# ============================================================================

