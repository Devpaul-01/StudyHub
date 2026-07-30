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
    Post, Notification, Connection,
    Mention, OnboardingDetails,
    ThreadMeetingNote,
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)

from services.ai_provider_service import call_ai_response
from services.thread_authorization import is_moderator_or_creator, require_moderator_or_creator

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

                    notification = Notification(
                        user_id=mentioned_user.id,
                        title=f"{sender.name} mentioned you in a thread",
                        body="",
                        notification_type="mention",
                        related_type="thread",
                        related_id=thread_id
                    )
                    db.session.add(notification)
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

        result = cloudinary_storage.upload_file(
            file=file, folder=folder, filename=filename, resource_type=resource_type
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
@token_required
def send_thread_message(current_user, thread_id):
    """Send message in thread (REST fallback — primary path is WebSocket)."""
    try:
        membership = ThreadMember.query.filter_by(
            thread_id=thread_id, student_id=current_user.id
        ).first()
        if not membership:
            return error_response("You must be a member to send messages", 403)

        data         = request.get_json()
        text_content = data.get("text_content", "").strip()
        if not text_content:
            return error_response("Message text is required")
        if len(text_content) > 5000:
            return error_response("Message too long (max 5000 characters)")

        new_message = ThreadMessage(
            thread_id=thread_id,
            sender_id=current_user.id,
            text_content=text_content,
            status='sent'
        )
        db.session.add(new_message)
        db.session.flush()

        detect_mentions_in_thread(text_content, current_user.id, thread_id, new_message.id)

        Thread.query.filter_by(id=thread_id).update(
            {Thread.message_count: Thread.message_count + 1,
             Thread.last_activity: datetime.datetime.utcnow()},
            synchronize_session=False
        )
        ThreadMember.query.filter_by(
            thread_id=thread_id, student_id=current_user.id
        ).update(
            {ThreadMember.messages_sent: ThreadMember.messages_sent + 1},
            synchronize_session=False
        )
        db.session.commit()

        return success_response(
            "Message sent",
            data={"message_id": new_message.id, "sent_at": new_message.sent_at.isoformat()}
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Send message error: {str(e)}")
        return error_response("Failed to send message")


@threads_messaging_bp.route("/threads/<int:thread_id>/messages/<int:message_id>", methods=["PATCH"])
@token_required
def edit_thread_message(current_user, thread_id, message_id):
    """Edit your own message."""
    try:
        message = ThreadMessage.query.get(message_id)
        if not message:
            return error_response("Message not found", 404)
        if message.sender_id != current_user.id:
            return error_response("You can only edit your own messages", 403)
        if message.thread_id != thread_id:
            return error_response("Message does not belong to this thread", 400)

        data     = request.get_json()
        new_text = data.get("text_content", "").strip()
        if not new_text:
            return error_response("Message text is required")

        message.text_content = new_text
        message.is_edited    = True
        message.edited_at    = datetime.datetime.utcnow()

        Mention.query.filter_by(
            mentioned_in_type="thread_message", mentioned_in_id=message_id
        ).delete()
        detect_mentions_in_thread(new_text, current_user.id, thread_id, message_id)

        db.session.commit()
        return success_response("Message updated", data={"edited_at": message.edited_at.isoformat()})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Edit message error: {str(e)}")
        return error_response("Failed to edit message")


@threads_messaging_bp.route("/threads/<int:thread_id>/messages/<int:message_id>", methods=["DELETE"])
@token_required
def delete_thread_message(current_user, thread_id, message_id):
    """Delete your own message (soft delete)."""
    try:
        message = ThreadMessage.query.get(message_id)
        if not message:
            return error_response("Message not found", 404)

        thread = Thread.query.get(thread_id)
        if message.sender_id != current_user.id and thread.creator_id != current_user.id:
            return error_response("You can only delete your own messages", 403)

        message.is_deleted   = True
        message.text_content = "[deleted]"
        Thread.query.filter_by(id=thread_id).update(
            {Thread.message_count: case(
                (Thread.message_count > 0, Thread.message_count - 1),
                else_=0
             )},
            synchronize_session=False
        )
        db.session.commit()
        return success_response("Message deleted")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete message error: {str(e)}")
        return error_response("Failed to delete message")


# ============================================================================
# MY THREADS
# FIX: includes last_message preview and avatar in response
# ============================================================================

