"""
StudyHub - Threads: AI meeting notes

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

threads_ai_bp = Blueprint("threads_ai", __name__)
@threads_ai_bp.route("/threads/<int:thread_id>/meeting-notes", methods=["POST"])
@token_required
def generate_meeting_notes(current_user, thread_id):
    membership = ThreadMember.query.filter_by(thread_id=thread_id, student_id=current_user.id).first()
    if not membership:
        return error_response("Not a member", 403)

    thread = Thread.query.get(thread_id)
    if not thread:
        return error_response("Thread not found", 404)

    data = request.get_json(silent=True) or {}
    message_range = min(max(int(data.get("message_range", 50)), 10), 500)

    messages = (ThreadMessage.query
                .filter_by(thread_id=thread_id, is_deleted=False)
                .order_by(ThreadMessage.sent_at.desc())
                .limit(message_range)
                .all())
    messages.reverse()

    if len(messages) < 3:
        return error_response("Not enough messages to summarize (minimum 3)")

    lines = []
    for m in messages:
        sender = User.query.get(m.sender_id)
        name = "Learnora" if m.is_ai_response else (sender.name if sender else "Unknown")
        lines.append(f"[{name}]: {m.text_content}")
    conversation = "\n".join(lines)

    system = """You are a meeting notes assistant. Return ONLY a JSON object with these keys:
{"topics_discussed":[],"decisions_made":[],"action_items":[],"open_questions":[],"summary":""}
No markdown, no explanation."""

    user_prompt = f'Thread: "{thread.title}"\nLast {message_range} messages:\n\n{conversation}'

    try:
        # Document 1 §2.4: call_ai_response() handles provider selection,
        # retry, and rotation internally — replaces the single-attempt
        # _call_provider_sync() call this used to make directly, so a
        # transient provider failure no longer fails meeting-notes
        # generation outright.
        ai_response, diagnostics = call_ai_response(
            [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
            needs_vision=False,
            call_type="meeting_notes",
        )
        if not ai_response:
            current_app.logger.error(f"Meeting notes AI error: {diagnostics}")
            return error_response("AI service unavailable", 503)

        # Document 1 §5 fix: the original .lstrip("```json").lstrip("```")
        # strips arbitrary leading/trailing characters from the given sets
        # (character-strip, not substring-strip) — wrong semantics for
        # removing a code fence. Replaced with a regex that removes the
        # fence as a literal substring.
        clean = _re.sub(r"^```(?:json)?\s*|\s*```$", "", ai_response.strip())
        notes = _json.loads(clean)
    except Exception as e:
        current_app.logger.error(f"Meeting notes AI error: {e!r}")
        return error_response("Failed to generate meeting notes")

    note = ThreadMeetingNote(
        thread_id=thread_id,
        created_by=current_user.id,
        message_range=message_range,
        message_count=len(messages),
        notes_json=notes
    )
    db.session.add(note)
    db.session.commit()

    return jsonify({
        "status": "success",
        "data": {
            "notes": notes,
            "message_count": len(messages),
            "note_id": note.id,
            "generated_at": note.created_at.isoformat()
        }
    })


@threads_ai_bp.route("/threads/<int:thread_id>/meeting-notes", methods=["GET"])
@token_required
def get_meeting_notes(current_user, thread_id):
    membership = ThreadMember.query.filter_by(thread_id=thread_id, student_id=current_user.id).first()
    if not membership:
        return error_response("Not a member", 403)

    limit = min(int(request.args.get("limit", 5)), 20)
    notes = (ThreadMeetingNote.query
             .filter_by(thread_id=thread_id)
             .order_by(ThreadMeetingNote.created_at.desc())
             .limit(limit)
             .all())

    return jsonify({
        "status": "success",
        "data": {
            "notes": [
                {
                    "id": n.id,
                    "notes_json": n.notes_json,
                    "message_count": n.message_count,
                    "message_range": n.message_range,
                    "created_at": n.created_at.isoformat()
                }
                for n in notes
            ]
        }
    })

