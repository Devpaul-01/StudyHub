"""
StudyHub - Threads: Thread CRUD (create, details, update, delete, close/reopen, avatar, settings)

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

threads_crud_bp = Blueprint("threads_crud", __name__)
@threads_crud_bp.route("/", methods=["GET"])
@token_required
def threads_page(current_user):
    return render_template('threads/threads.html')


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

@threads_crud_bp.route("/threads/create", methods=["POST"])
@token_required
def create_thread(current_user):
    """Create thread from a post."""
    try:
        post = None
        data = request.get_json()
        post_id = data.get("post_id")

        if post_id:
            post = Post.query.get(post_id)
            if not post:
                return error_response("Post not found", 404)
            if not post.thread_enabled:
                return error_response("This post does not allow thread creation", 403)

        tags  = data.get("tags", [])
        title = data.get("title", "").strip()
        if not title:
            return error_response("Thread title is required")
        if len(title) < 5:
            return error_response("Title too short (minimum 5 characters)")

        description = data.get("description", "").strip()
        try:
            max_members = int(data.get("max_members", 10))
        except (ValueError, TypeError):
            max_members = 10

        requires_approval = data.get("requires_approval", True)
        resource          = data.get("resource")
        member_ids        = data.get("member_ids", [])

        if max_members < 2:
            return error_response("Thread must allow at least 2 members")
        if max_members > 50:
            return error_response("Thread cannot exceed 50 members")

        valid_member_ids = []
        if member_ids:
            if not isinstance(member_ids, list):
                return error_response("member_ids must be an array")
            for uid in member_ids:
                user = User.query.get(uid)
                if user and user.status == 'approved' and user.id != current_user.id:
                    valid_member_ids.append(uid)
            if 1 + len(valid_member_ids) > max_members:
                return error_response(
                    f"Cannot add {len(valid_member_ids)} members. Max capacity is {max_members} (including creator)"
                )

        profile    = StudentProfile.query.filter_by(user_id=current_user.id).first()
        new_thread = Thread(
            creator_id=current_user.id,
            title=title,
            tags=tags,
            description=description,
            avatar=resource if resource else None,
            max_members=max_members,
            requires_approval=requires_approval,
            department=profile.department if profile else None,
            member_count=1 + len(valid_member_ids)
        )

        db.session.add(new_thread)
        db.session.flush()

        db.session.add(ThreadMember(
            thread_id=new_thread.id,
            student_id=current_user.id,
            role="creator"
        ))

        added_members = []
        for member_id in valid_member_ids:
            db.session.add(ThreadMember(
                thread_id=new_thread.id,
                student_id=member_id,
                role="member"
            ))
            member_user = User.query.get(member_id)
            if member_user:
                db.session.add(Notification(
                    user_id=member_id,
                    title=f"{current_user.name} added you to a thread",
                    body=f'Thread: "{new_thread.title}"',
                    notification_type="thread_member_added",
                    related_type="thread",
                    related_id=new_thread.id
                ))
                added_members.append({"id": member_user.id, "username": member_user.username, "name": member_user.name})

        db.session.commit()

        return success_response(
            "Thread created successfully!",
            data={
                "thread": {
                    "id": new_thread.id, "title": new_thread.title,
                    "max_members": new_thread.max_members,
                    "member_count": new_thread.member_count,
                    "created_at": new_thread.created_at.isoformat()
                },
                "added_members": added_members
            }
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create thread error: {str(e)}")
        return error_response("Failed to create thread")


@threads_crud_bp.route("/threads/create-standalone", methods=["POST"])
@token_required
def create_standalone_thread(current_user):
    """Create thread WITHOUT a post (standalone study group)."""
    try:
        data = request.get_json()

        title = data.get("title", "").strip()
        if not title:
            return error_response("Thread title is required")
        if len(title) < 5:
            return error_response("Title too short (minimum 5 characters)")

        description       = data.get("description", "").strip()
        max_members       = data.get("max_members", 10)
        requires_approval = data.get("requires_approval", True)
        tags              = data.get("tags", [])
        member_ids        = data.get("member_ids", [])

        if max_members < 2 or max_members > 50:
            return error_response("Max members must be between 2 and 50")

        week_ago       = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        recent_threads = Thread.query.filter(
            Thread.creator_id == current_user.id,
            Thread.created_at >= week_ago
        ).count()
        if recent_threads >= 3:
            return error_response("You can only create 3 threads per week", 429)

        valid_member_ids = []
        if member_ids:
            if not isinstance(member_ids, list):
                return error_response("member_ids must be an array")
            for uid in member_ids:
                user = User.query.get(uid)
                if user and user.status == 'approved' and user.id != current_user.id:
                    valid_member_ids.append(uid)
            if 1 + len(valid_member_ids) > max_members:
                return error_response(
                    f"Cannot add {len(valid_member_ids)} members. Max capacity is {max_members} (including creator)"
                )

        profile    = StudentProfile.query.filter_by(user_id=current_user.id).first()
        new_thread = Thread(
            post_id=None,
            creator_id=current_user.id,
            title=title,
            description=description,
            max_members=max_members,
            requires_approval=requires_approval,
            department=profile.department if profile else None,
            tags=tags[:5] if tags else [],
            member_count=1 + len(valid_member_ids)
        )

        db.session.add(new_thread)
        db.session.flush()

        db.session.add(ThreadMember(
            thread_id=new_thread.id,
            student_id=current_user.id,
            role="creator"
        ))

        added_members = []
        for member_id in valid_member_ids:
            db.session.add(ThreadMember(
                thread_id=new_thread.id,
                student_id=member_id,
                role="member"
            ))
            member_user = User.query.get(member_id)
            if member_user:
                db.session.add(Notification(
                    user_id=member_id,
                    title=f"{current_user.name} added you to a thread",
                    body=f'Thread: "{new_thread.title}"',
                    notification_type="thread_member_added",
                    related_type="thread",
                    related_id=new_thread.id
                ))
                added_members.append({"id": member_user.id, "username": member_user.username, "name": member_user.name})

        db.session.commit()

        return success_response(
            "Standalone thread created!",
            data={
                "thread": {
                    "id": new_thread.id, "title": new_thread.title,
                    "is_standalone": True,
                    "member_count": new_thread.member_count,
                    "created_at": new_thread.created_at.isoformat()
                },
                "added_members": added_members
            }
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create standalone thread error: {str(e)}")
        return error_response("Failed to create thread")


# ============================================================================
# THREAD DETAILS (legacy POST endpoint — still used by frontend)
# ============================================================================

@threads_crud_bp.route("/threads/<int:resource_id>/details", methods=["POST"])
@token_required
def thread_details(current_user, resource_id):
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")

        data       = request.get_json() or {}
        type_param = data.get("type")
        thread_id  = resource_id

        if type_param == "post":
            thread = Thread.query.filter_by(post_id=resource_id).first()
            if not thread:
                return error_response("Thread not found for this post")
            thread_id = thread.id

        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found")

        members_data  = []
        thread_members = ThreadMember.query.filter_by(thread_id=thread.id).all()
        for member in thread_members:
            author = User.query.get(member.student_id)
            if not author:
                continue
            connection = Connection.query.filter(
                or_(
                    and_(Connection.requester_id == user.id, Connection.receiver_id == author.id),
                    and_(Connection.receiver_id == user.id, Connection.requester_id == author.id)
                )
            ).first()
            onboarding = OnboardingDetails.query.filter_by(user_id=author.id).first()
            class_level = onboarding.class_level if onboarding else None
            department  = onboarding.department  if onboarding else None

            members_data.append({
                "id":                author.id,
                "name":              author.name,
                "username":          author.username,
                "avatar":            author.avatar,
                "connection_status": connection.status if connection else None,
                "reputation":        author.reputation,
                "reputation_level":  author.reputation_level,
                "department":        department,
                "class_level":       class_level,
            })

        creator    = User.query.get(thread.creator_id) if thread.creator_id else None
        thread_data = {
            "id":               thread.id,
            "title":            thread.title,
            "description":      thread.description,
            "department":       thread.department,
            "tags":             thread.tags or [],
            "member_count":     thread.member_count,
            "max_members":      thread.max_members,
            "requires_approval":thread.requires_approval,
            "created_at":       thread.created_at.isoformat(),
            "last_activity":    thread.last_activity.isoformat(),
            "total_users":      len(members_data),
            "members_data":     members_data,
            "creator": {
                "id": creator.id, "username": creator.username,
                "name": creator.name, "avatar": creator.avatar,
                "reputation_level": creator.reputation_level
            } if creator else None,
        }

        return jsonify({"status": "success", "data": {"thread": thread_data}})

    except Exception as e:
        return error_response(str(e))


# ============================================================================
# DISCOVERY: DEPARTMENTS
# ============================================================================

@threads_crud_bp.route("/threads/<int:thread_id>/close", methods=["POST"])
@token_required
def close_thread(current_user, thread_id):
    """
    Close thread: stops NEW join requests only.
    Does NOT block existing members from messaging.
    """
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)
        if thread.creator_id != current_user.id:
            return error_response("Only creator can close thread", 403)
        if not thread.is_open:
            return error_response("Thread is already closed", 409)

        thread.is_open = False
        db.session.commit()
        return success_response("Thread closed - no more join requests will be accepted")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Close thread error: {str(e)}")
        return error_response("Failed to close thread")


@threads_crud_bp.route("/threads/<int:thread_id>/reopen", methods=["POST"])
@token_required
def reopen_thread(current_user, thread_id):
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)
        if thread.creator_id != current_user.id:
            return error_response("Only creator can reopen thread", 403)
        if thread.is_open:
            return error_response("Thread is already open", 409)

        thread.is_open = True
        db.session.commit()
        return success_response("Thread reopened - now accepting join requests")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Reopen thread error: {str(e)}")
        return error_response("Failed to reopen thread")


@threads_crud_bp.route("/threads/<int:thread_id>", methods=["PATCH"])
@token_required
def update_thread(current_user, thread_id):
    """Update thread details (creator only)."""
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)
        if thread.creator_id != current_user.id:
            return error_response("Only creator can update thread", 403)

        data    = request.get_json()
        changes = []

        if "title" in data:
            new_title = data["title"].strip()
            if len(new_title) >= 5:
                thread.title = new_title
                changes.append("title")

        if "description" in data:
            thread.description = data["description"].strip()
            changes.append("description")

        if "max_members" in data:
            new_max = data["max_members"]
            if new_max >= thread.member_count and new_max <= 50:
                thread.max_members = new_max
                changes.append("max_members")

        if "tags" in data:
            thread.tags = data["tags"][:5]
            changes.append("tags")

        if changes:
            db.session.commit()

            # Issue 6: Broadcast metadata change to all member personal rooms
            try:
                from services.websocket_threads import thread_ws_manager
                update_payload = {
                    "thread_id":          thread_id,
                    "changes":            changes,
                    "title":              thread.title       if "title"       in changes else None,
                    "description":        thread.description if "description" in changes else None,
                    "tags":               thread.tags        if "tags"        in changes else None,
                    "max_members":        thread.max_members if "max_members" in changes else None,
                    "requires_approval":  None,
                    "avatar":             None,
                }
                memberships = ThreadMember.query.filter_by(thread_id=thread_id).all()
                for m in memberships:
                    thread_ws_manager.notify_user(m.student_id, "thread_updated", update_payload)
                thread_ws_manager.broadcast_to_thread(thread_id, "thread_updated", update_payload)
            except Exception as ws_err:
                current_app.logger.warning(
                    f"[UPDATE_THREAD_WS_FAILED] thread_id={thread_id} error={ws_err!r}"
                )

            return success_response("Thread updated", data={"changes": changes})
        return success_response("No changes made")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update thread error: {str(e)}")
        return error_response("Failed to update thread")


@threads_crud_bp.route("/threads/<int:thread_id>", methods=["DELETE"])
@token_required
def delete_thread(current_user, thread_id):
    """Delete thread (creator only). Cascade deletes all members, messages, requests."""
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)
        if thread.creator_id != current_user.id:
            return error_response("Only creator can delete thread", 403)

        members = ThreadMember.query.filter_by(thread_id=thread_id).all()
        for member in members:
            if member.student_id != current_user.id:
                db.session.add(Notification(
                    user_id=member.student_id,
                    title="Thread deleted",
                    body=f'The thread "{thread.title}" has been deleted',
                    notification_type="thread_deleted",
                    related_type="thread",
                    related_id=thread_id
                ))

        # FIX: broadcast BEFORE delete so WS manager can still find the room
        try:
            from services.websocket_threads import thread_ws_manager
            thread_ws_manager.broadcast_to_thread(thread_id, "thread_deleted", {
                "thread_id": thread_id,
                "title":     thread.title,
            })
        except Exception:
            pass

        db.session.delete(thread)
        db.session.commit()
        return success_response("Thread deleted successfully")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete thread error: {str(e)}")
        return error_response("Failed to delete thread")


# ============================================================================
# THREAD AVATAR UPLOAD
# NEW: Was missing. Frontend can call POST /threads/<id>/avatar to update avatar.
# ============================================================================

@threads_crud_bp.route("/threads/<int:thread_id>/avatar", methods=["POST"])
@token_required
def upload_thread_avatar(current_user, thread_id):
    """Upload/replace thread avatar (creator only). Uses Cloudinary."""
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)
        if thread.creator_id != current_user.id:
            return error_response("Only creator can update thread avatar", 403)

        if "file" not in request.files:
            return error_response("No file provided")

        file = request.files["file"]
        if not file.filename:
            return error_response("Empty filename")

        allowed_mime = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        mime         = mimetypes.guess_type(file.filename)[0] or ""
        if mime not in allowed_mime:
            return error_response("Only image files allowed for avatar")

        file.seek(0, 2)
        if file.tell() > 5 * 1024 * 1024:
            return error_response("Avatar must be under 5 MB")
        file.seek(0)

        if not cloudinary_storage:
            return error_response("Storage not configured", 503)

        folder, filename = FilenameService.get_avatar_path(
            f"thread_{thread_id}", file.filename
        )
        folder = "threads/avatars"

        result = cloudinary_storage.upload_file(
            file=file, folder=folder, filename=filename, resource_type="image"
        )
        if not result["success"]:
            return error_response("Avatar upload failed")

        thread.avatar = result["url"]
        db.session.commit()

        # Issue 6: Broadcast avatar change to all member personal rooms
        try:
            from services.websocket_threads import thread_ws_manager
            avatar_payload = {
                "thread_id":         thread_id,
                "changes":           ["avatar"],
                "avatar":            thread.avatar,
                "title":             None,
                "description":       None,
                "tags":              None,
                "max_members":       None,
                "requires_approval": None,
            }
            memberships = ThreadMember.query.filter_by(thread_id=thread_id).all()
            for m in memberships:
                thread_ws_manager.notify_user(m.student_id, "thread_updated", avatar_payload)
            thread_ws_manager.broadcast_to_thread(thread_id, "thread_updated", avatar_payload)
        except Exception as ws_err:
            current_app.logger.warning(
                f"[AVATAR_UPLOAD_WS_FAILED] thread_id={thread_id} error={ws_err!r}"
            )

        return jsonify({
            "status": "success",
            "data": {"avatar_url": result["url"]}
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Thread avatar upload error: {e}")
        return error_response("Failed to upload avatar")


# ============================================================================
# THREAD CHAT / MESSAGES
# ============================================================================

@threads_crud_bp.route("/threads/<int:thread_id>/stats", methods=["GET"])
@token_required
def get_thread_stats(current_user, thread_id):
    """Get thread statistics (members only)."""
    try:
        membership = ThreadMember.query.filter_by(
            thread_id=thread_id, student_id=current_user.id
        ).first()
        if not membership:
            return error_response("You must be a member to view stats", 403)

        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)

        members      = ThreadMember.query.filter_by(thread_id=thread_id).all()
        members_stats = []
        for member in members:
            user = User.query.get(member.student_id)
            if user:
                members_stats.append({
                    "user": {
                        "id":       user.id,
                        "username": user.username,
                        "name":     user.name,
                        "avatar":   user.avatar
                    },
                    "role":          member.role,
                    "messages_sent": member.messages_sent,
                    "joined_at":     member.joined_at.isoformat()
                })

        members_stats.sort(key=lambda x: x["messages_sent"], reverse=True)
        thread_age          = (datetime.datetime.utcnow() - thread.created_at).days
        avg_messages_per_day = thread.message_count / max(thread_age, 1)

        return jsonify({
            "status": "success",
            "data": {
                "thread": {
                    "id":         thread.id,
                    "title":      thread.title,
                    "created_at": thread.created_at.isoformat(),
                    "age_days":   thread_age
                },
                "stats": {
                    "total_members":       thread.member_count,
                    "total_messages":      thread.message_count,
                    "avg_messages_per_day":round(avg_messages_per_day, 2),
                    "last_activity":       thread.last_activity.isoformat()
                },
                "members":     members_stats,
                "most_active": members_stats[0] if members_stats else None
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get thread stats error: {str(e)}")
        return error_response("Failed to load stats")


# ============================================================================
# THREAD SETTINGS
# ============================================================================

@threads_crud_bp.route("/threads/<int:thread_id>/settings", methods=["GET"])
@token_required
def get_thread_settings(current_user, thread_id):
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)
        if thread.creator_id != current_user.id:
            return error_response("Only creator can view settings", 403)

        return jsonify({
            "status": "success",
            "data": {
                "settings": {
                    "is_open":           thread.is_open,
                    "max_members":       thread.max_members,
                    "requires_approval": thread.requires_approval,
                    "current_members":   thread.member_count
                }
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get thread settings error: {str(e)}")
        return error_response("Failed to load settings")


@threads_crud_bp.route("/threads/<int:thread_id>/settings", methods=["PATCH"])
@token_required
def update_thread_settings(current_user, thread_id):
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)
        if thread.creator_id != current_user.id:
            return error_response("Only creator can update settings", 403)

        data    = request.get_json()
        changes = []

        if "requires_approval" in data:
            thread.requires_approval = bool(data["requires_approval"])
            changes.append("requires_approval")

        if "max_members" in data:
            new_max = data["max_members"]
            if new_max >= thread.member_count and new_max <= 50:
                thread.max_members = new_max
                changes.append("max_members")
            else:
                return error_response("Invalid max_members value")

        if changes:
            db.session.commit()
            return success_response("Settings updated", data={"changes": changes})
        return success_response("No changes made")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update settings error: {str(e)}")
        return error_response("Failed to update settings")


# ============================================================================
# OPEN THREADS
# ============================================================================

