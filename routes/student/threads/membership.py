"""
StudyHub - Threads: Membership (leave/remove, join-request workflow, invites, role management)

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

threads_membership_bp = Blueprint("threads_membership", __name__)
@threads_membership_bp.route("/threads/<int:thread_id>/leave", methods=["POST"])
@token_required
def leave_thread(current_user, thread_id):
    """Leave a thread you're a member of."""
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)
        if thread.creator_id == current_user.id:
            return error_response("Creator cannot leave thread. Transfer ownership or delete thread.", 403)

        membership = ThreadMember.query.filter_by(thread_id=thread_id, student_id=current_user.id).first()
        if not membership:
            return error_response("You are not a member of this thread", 404)

        db.session.delete(membership)
        Thread.query.filter_by(id=thread_id).update(
            {Thread.member_count: case(
                (Thread.member_count > 1, Thread.member_count - 1),
                else_=1
             ),
             Thread.last_activity: datetime.datetime.utcnow()},
            synchronize_session=False
        )

        db.session.add(Notification(
            user_id=thread.creator_id,
            title=f"{current_user.name} left your thread",
            body=f'Thread: "{thread.title}"',
            notification_type="thread_member_left",
            related_type="thread",
            related_id=thread_id
        ))
        db.session.commit()
        return success_response("You left the thread")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Leave thread error: {str(e)}")
        return error_response("Failed to leave thread")


@threads_membership_bp.route("/threads/<int:thread_id>/remove/<int:user_id>", methods=["DELETE"])
@token_required
def remove_member(current_user, thread_id, user_id):
    """Remove a member from thread (creator/moderator only)."""
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)

        current_membership = require_moderator_or_creator(thread_id, current_user.id)

        if user_id == thread.creator_id:
            return error_response("Cannot remove thread creator", 403)

        member = ThreadMember.query.filter_by(thread_id=thread_id, student_id=user_id).first()
        if not member:
            return error_response("User is not a member", 404)

        db.session.delete(member)
        Thread.query.filter_by(id=thread_id).update(
            {Thread.member_count: case(
                (Thread.member_count > 1, Thread.member_count - 1),
                else_=1
             ),
             Thread.last_activity: datetime.datetime.utcnow()},
            synchronize_session=False
        )

        db.session.add(Notification(
            user_id=user_id,
            title="You were removed from a thread",
            body=f'Thread: "{thread.title}"',
            notification_type="thread_removed",
            related_type="thread",
            related_id=thread_id
        ))
        db.session.commit()

        # FIX: notify the removed user and all thread members in real-time
        try:
            from services.websocket_threads import thread_ws_manager
            thread_ws_manager.broadcast_to_thread(thread_id, "thread_member_removed", {
                "thread_id": thread_id,
                "user_id":   user_id,
            })
        except Exception:
            pass

        return success_response("Member removed from thread")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Remove member error: {str(e)}")
        return error_response("Failed to remove member")


# ============================================================================
# NEW: GET /threads/<thread_id>/members
# FIX: was completely missing — frontend called it via THREAD_API.MEMBERS(id)
# ============================================================================

@threads_membership_bp.route("/threads/<int:thread_id>/members", methods=["GET"])
@token_required
def get_thread_members(current_user, thread_id):
    """
    GET /threads/<thread_id>/members
    Returns full member list with role, online status, and joined_at.
    Members only.
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

        members      = ThreadMember.query.filter_by(thread_id=thread_id).all()
        members_data = []

        for m in members:
            user = User.query.get(m.student_id)
            if not user:
                continue
            online = bool(
                user.last_active and
                (datetime.datetime.utcnow() - user.last_active).total_seconds() < 300
            )
            members_data.append({
                "user_id":       user.id,
                "id":            user.id,           # alias for frontend compatibility
                "username":      user.username,
                "name":          user.name,
                "avatar":        user.avatar,
                "role":          m.role,
                "online":        online,
                "joined_at":     m.joined_at.isoformat(),
                "messages_sent": m.messages_sent,
                "last_read_at":  m.last_read_at.isoformat() if m.last_read_at else None,
            })

        return jsonify({
            "status": "success",
            "data": {"members": members_data, "total": len(members_data)}
        })

    except Exception as e:
        current_app.logger.error(f"Get thread members error: {e}")
        return error_response("Failed to load members")


# ============================================================================
# THREAD MANAGEMENT
# ============================================================================

@threads_membership_bp.route("/threads/pending-requests", methods=["GET"])
@token_required
def get_pending_requests(current_user):
    """Get all pending join requests for threads you created."""
    try:
        created_threads = Thread.query.filter_by(creator_id=current_user.id).all()
        thread_ids      = [t.id for t in created_threads]

        requests = ThreadJoinRequest.query.filter(
            ThreadJoinRequest.thread_id.in_(thread_ids),
            ThreadJoinRequest.status == "pending"
        ).all()

        requests_data = []
        for req in requests:
            thread    = Thread.query.get(req.thread_id)
            requester = User.query.get(req.requester_id)
            if thread and requester:
                requests_data.append({
                    "request_id": req.id,
                    "thread": {
                        "id":           thread.id,
                        "title":        thread.title,
                        "member_count": thread.member_count,
                        "max_members":  thread.max_members
                    },
                    "requester": {
                        "id":       requester.id,
                        "username": requester.username,
                        "name":     requester.name,
                        "avatar":   requester.avatar
                    },
                    "message":      req.message,
                    "requested_at": req.requested_at.isoformat()
                })

        return jsonify({
            "status": "success",
            "data": {"pending_requests": requests_data, "total": len(requests_data)}
        })

    except Exception as e:
        current_app.logger.error(f"Get pending requests error: {str(e)}")
        return error_response("Failed to load pending requests")


@threads_membership_bp.route("/threads/my-requests", methods=["GET"])
@token_required
def get_my_join_requests(current_user):
    """Get all join requests YOU sent that are still pending."""
    try:
        requests = ThreadJoinRequest.query.filter_by(
            requester_id=current_user.id, status="pending"
        ).all()

        requests_data = []
        for req in requests:
            thread = Thread.query.get(req.thread_id)
            if thread:
                requests_data.append({
                    "request_id": req.id,
                    "thread": {
                        "id":           thread.id,
                        "title":        thread.title,
                        "member_count": thread.member_count,
                        "max_members":  thread.max_members,
                        "is_full":      thread.member_count >= thread.max_members
                    },
                    "requested_at": req.requested_at.isoformat()
                })

        return jsonify({
            "status": "success",
            "data": {"my_requests": requests_data, "total": len(requests_data)}
        })

    except Exception as e:
        current_app.logger.error(f"Get my requests error: {str(e)}")
        return error_response("Failed to load your requests")


# ============================================================================
# MEMBER ROLE MANAGEMENT
# ============================================================================

@threads_membership_bp.route("/threads/<int:thread_id>/members/<int:user_id>/role", methods=["PATCH"])
@token_required
def update_member_role(current_user, thread_id, user_id):
    """Update member's role (creator only). Roles: member, moderator."""
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)
        if thread.creator_id != current_user.id:
            return error_response("Only creator can change member roles", 403)

        member = ThreadMember.query.filter_by(thread_id=thread_id, student_id=user_id).first()
        if not member:
            return error_response("User is not a member", 404)
        if member.role == "creator":
            return error_response("Cannot change creator role", 403)

        data     = request.get_json()
        new_role = data.get("role", "").strip().lower()
        if new_role not in ["member", "moderator"]:
            return error_response("Role must be 'member' or 'moderator'")
        if member.role == new_role:
            return success_response("No change needed")

        member.role = new_role
        db.session.commit()

        user = User.query.get(user_id)
        if user:
            db.session.add(Notification(
                user_id=user_id,
                title=f"You are now a {new_role} in a thread",
                body=f'Thread: "{thread.title}"',
                notification_type="thread_role_updated",
                related_type="thread",
                related_id=thread_id
            ))
            db.session.commit()

        return success_response(
            f"Member role updated to {new_role}",
            data={"user_id": user_id, "new_role": new_role}
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update member role error: {str(e)}")
        return error_response("Failed to update role")


# ============================================================================
# THREAD STATISTICS
# ============================================================================

@threads_membership_bp.route("/threads/requests/<int:request_id>/cancel", methods=["DELETE"])
@token_required
def cancel_join_request(current_user, request_id):
    """Cancel your own pending join request. FIX: dead code block removed."""
    try:
        request_obj = ThreadJoinRequest.query.get(request_id)
        if not request_obj:
            return error_response("Request not found", 404)
        if request_obj.requester_id != current_user.id:
            return error_response("You can only cancel your own requests", 403)
        if request_obj.status != "pending":
            return error_response("Request is no longer pending", 400)

        db.session.delete(request_obj)
        db.session.commit()
        return success_response("Join request cancelled")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Cancel request error: {str(e)}")
        return error_response("Failed to cancel request")


@threads_membership_bp.route("/threads/<int:resource_id>/join", methods=["POST"])
@token_required
def request_join_thread(current_user, resource_id):
    """
    Request to join a thread.
    FIX: request body parsed only once at the top (previously re-read stream).
    """
    try:
        # Parse body exactly once
        data      = request.get_json(silent=True) or {}
        type_     = data.get("type")
        message   = data.get("message", "").strip()

        thread_id = resource_id
        if type_ == "post":
            thread = Thread.query.filter_by(post_id=resource_id).first()
            if not thread:
                return error_response("Thread not found for this post", 404)
            thread_id = thread.id

        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)

        if not thread.is_open:
            return error_response("This thread is closed", 403)
        if thread.member_count >= thread.max_members:
            return error_response("This thread is full", 403)

        existing_member = ThreadMember.query.filter_by(
            thread_id=thread_id, student_id=current_user.id
        ).first()
        if existing_member:
            return error_response("You are already a member of this thread", 409)

        existing_request = ThreadJoinRequest.query.filter_by(
            thread_id=thread_id, requester_id=current_user.id
        ).first()

        if existing_request:
            if existing_request.status == "pending":
                return error_response("You already have a pending request", 409)

            elif existing_request.status == "rejected":
                # FIX: reviewed_at may be NULL (migration, direct DB edit, or old data)
                if existing_request.reviewed_at:
                    cooldown_period      = datetime.timedelta(hours=24)
                    time_since_rejection = datetime.datetime.utcnow() - existing_request.reviewed_at
                    if time_since_rejection < cooldown_period:
                        remaining = int((cooldown_period - time_since_rejection).total_seconds() / 3600)
                        return error_response(
                            f"Please wait {remaining} more hour{'s' if remaining != 1 else ''} before requesting again",
                            429
                        )
                # If reviewed_at is None, allow re-request without enforcing cooldown

                existing_request.status       = "pending"
                existing_request.requested_at = datetime.datetime.utcnow()
                existing_request.reviewed_at  = None
                existing_request.reviewed_by  = None
                existing_request.message      = message or existing_request.message

                db.session.add(Notification(
                    user_id=thread.creator_id,
                    title=f"{current_user.name} wants to join your thread again",
                    body=f'Thread: "{thread.title}"',
                    notification_type="thread_join_request",
                    related_type="thread",
                    related_id=thread_id
                ))
                db.session.commit()
                return success_response("Re-request submitted", data={"request_id": existing_request.id}), 201

            elif existing_request.status == "approved":
                return error_response("Your request was already approved", 409)

        join_request = ThreadJoinRequest(
            thread_id=thread_id,
            requester_id=current_user.id,
            message=message if message else None,
            status="pending"
        )
        db.session.add(join_request)
        db.session.add(Notification(
            user_id=thread.creator_id,
            title=f"{current_user.name} wants to join your thread",
            body=f'Thread: "{thread.title}"',
            notification_type="thread_join_request",
            related_type="thread",
            related_id=thread_id
        ))
        db.session.commit()
        return success_response("Join request sent", data={"request_id": join_request.id}), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Join thread error: {str(e)}")
        return error_response("Failed to send join request")


# ============================================================================
# APPROVE / REJECT JOIN REQUESTS
# FIX: URL now uses request_id (matches THREAD_API.APPROVE_REQUEST constant).
#      Old route was /approve/<user_id>; frontend sends request row id, not user id.
# ============================================================================

@threads_membership_bp.route("/threads/<int:thread_id>/requests/<int:request_id>/approve", methods=["POST"])
@token_required
def approve_join_request(current_user, thread_id, request_id):
    """
    Approve a join request by request ID.
    FIX: route changed from /approve/<user_id> to /requests/<request_id>/approve.
    FIX: atomic SQL increment for member_count.
    """
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)

        require_moderator_or_creator(thread_id, current_user.id)

        thread = Thread.query.with_for_update().get(thread_id)
        if thread.member_count >= thread.max_members:
            return error_response("Thread is full", 403)

        join_request = ThreadJoinRequest.query.filter_by(
            id=request_id, thread_id=thread_id, status="pending"
        ).first()
        if not join_request:
            return error_response("Join request not found", 404)

        user_id                  = join_request.requester_id
        join_request.status      = "approved"
        join_request.reviewed_at = datetime.datetime.utcnow()
        join_request.reviewed_by = current_user.id

        db.session.add(ThreadMember(
            thread_id=thread_id, student_id=user_id, role="member"
        ))

        Thread.query.filter_by(id=thread_id).update(
            {
                Thread.member_count: Thread.member_count + 1,
                Thread.last_activity: datetime.datetime.utcnow()
            },
            synchronize_session=False
        )

        db.session.add(Notification(
            user_id=user_id,
            title="Join request approved!",
            body=f'You can now participate in "{thread.title}"',
            notification_type="thread_join_approved",
            related_type="thread",
            related_id=thread_id
        ))
        db.session.commit()

        try:
            from services.websocket_threads import thread_ws_manager
            requester = User.query.get(user_id)

            # Notify existing thread room members (those who have the thread open)
            thread_ws_manager.broadcast_to_thread(thread_id, "thread_member_joined", {
                "thread_id": thread_id,
                "user": {
                    "id":       requester.id,
                    "name":     requester.name,
                    "username": requester.username,
                    "avatar":   requester.avatar,
                } if requester else None
            })

            # Issue 6: Notify the NEW member via personal room.
            # They are not in the thread room yet (haven't called join_thread_room).
            thread_ws_manager.notify_user(user_id, "thread_joined", {
                "thread_id": thread_id,
                "thread": {
                    "id":           thread.id,
                    "title":        thread.title,
                    "avatar":       thread.avatar,
                    "description":  thread.description,
                    "department":   thread.department,
                    "tags":         thread.tags or [],
                    "member_count": thread.member_count,
                    "max_members":  thread.max_members,
                    "is_open":      thread.is_open,
                    "last_activity": thread.last_activity.isoformat(),
                    "your_role":    "member",
                    "unread_count": 0,
                }
            })
        except Exception as ws_err:
            current_app.logger.warning(
                f"[APPROVE_JOIN_WS_FAILED] thread_id={thread_id} error={ws_err!r}"
            )

        requester = User.query.get(user_id)
        return success_response(
            "Join request approved",
            data={"new_member": {
                "id":       requester.id,
                "username": requester.username,
                "name":     requester.name
            } if requester else None}
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Approve join error: {e}")
        return error_response("Failed to approve request")


@threads_membership_bp.route("/threads/<int:thread_id>/requests/<int:request_id>/reject", methods=["POST"])
@token_required
def reject_join_request(current_user, thread_id, request_id):
    """
    Reject a join request by request ID.
    FIX: route changed from /reject/<user_id> to /requests/<request_id>/reject.
    """
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)

        require_moderator_or_creator(thread_id, current_user.id)

        join_request = ThreadJoinRequest.query.filter_by(
            id=request_id, thread_id=thread_id, status="pending"
        ).first()
        if not join_request:
            return error_response("Join request not found", 404)

        join_request.status      = "rejected"
        join_request.reviewed_at = datetime.datetime.utcnow()
        join_request.reviewed_by = current_user.id
        db.session.commit()
        return success_response("Join request rejected")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Reject join error: {e}")
        return error_response("Failed to reject request")


# ============================================================================
# MANUAL INVITES
# ============================================================================

@threads_membership_bp.route("/threads/<int:thread_id>/invite/<int:user_id>", methods=["POST"])
@token_required
def invite_to_thread(current_user, thread_id, user_id):
    """
    Manually invite a user to a thread (creator / moderator only).
    Bypasses approval — the invited user just has to accept.
    """
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)

        require_moderator_or_creator(thread_id, current_user.id)

        if thread.member_count >= thread.max_members:
            return error_response("Thread is full", 403)

        invited_user = User.query.get(user_id)
        if not invited_user:
            return error_response("User not found", 404)

        if ThreadMember.query.filter_by(thread_id=thread_id, student_id=user_id).first():
            return error_response("User is already a member", 409)

        data           = request.get_json(silent=True) or {}
        invite_message = data.get("message", "").strip()
        msg_text       = f"[INVITE] {invite_message}" if invite_message else "[INVITED]"

        existing = ThreadJoinRequest.query.filter_by(
            thread_id=thread_id, requester_id=user_id
        ).first()

        if existing:
            if existing.status == "invited":
                return error_response("User already has a pending invite", 409)
            existing.status       = "invited"
            existing.message      = msg_text
            existing.reviewed_by  = current_user.id
            existing.reviewed_at  = datetime.datetime.utcnow()
            existing.requested_at = datetime.datetime.utcnow()
        else:
            db.session.add(ThreadJoinRequest(
                thread_id    = thread_id,
                requester_id = user_id,
                message      = msg_text,
                status       = "invited",
                reviewed_by  = current_user.id,
                reviewed_at  = datetime.datetime.utcnow()
            ))

        db.session.add(Notification(
            user_id=user_id,
            title=f"{current_user.name} invited you to a thread",
            body=f'Thread: "{thread.title}"',
            notification_type="thread_invite",
            related_type="thread",
            related_id=thread_id
        ))
        db.session.commit()

        return success_response(
            "Invitation sent",
            data={
                "invited_user": {
                    "id":       invited_user.id,
                    "username": invited_user.username,
                    "name":     invited_user.name
                }
            }
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Invite to thread error: {e}")
        return error_response("Failed to send invitation")


@threads_membership_bp.route("/threads/invites", methods=["GET"])
@token_required
def get_my_invites(current_user):
    """Get all thread invites for the current user."""
    try:
        invites = ThreadJoinRequest.query.filter_by(
            requester_id=current_user.id, status="invited"
        ).all()

        invites_data = []
        for invite in invites:
            thread = Thread.query.get(invite.thread_id)
            if thread:
                inviter = User.query.get(invite.reviewed_by) if invite.reviewed_by else None
                invites_data.append({
                    "invite_id": invite.id,
                    "thread": {
                        "id":           thread.id,
                        "title":        thread.title,
                        "description":  thread.description,
                        "member_count": thread.member_count,
                        "max_members":  thread.max_members,
                        "tags":         thread.tags,
                        "department":   thread.department,
                        "avatar":       thread.avatar
                    },
                    "invited_by": {
                        "id":       inviter.id,
                        "username": inviter.username,
                        "name":     inviter.name,
                        "avatar":   inviter.avatar
                    } if inviter else None,
                    "message":    invite.message,
                    "invited_at": invite.requested_at.isoformat()
                })

        return jsonify({
            "status": "success",
            "data": {"invites": invites_data, "total": len(invites_data)}
        })

    except Exception as e:
        current_app.logger.error(f"Get invites error: {e}")
        return error_response("Failed to load invites")


@threads_membership_bp.route("/threads/invites/<int:invite_id>/accept", methods=["POST"])
@token_required
def accept_thread_invite(current_user, invite_id):
    """
    Accept a thread invitation.
    FIX: atomic SQL increment instead of Python += 1.
    """
    try:
        invite = ThreadJoinRequest.query.get(invite_id)
        if not invite:
            return error_response("Invite not found", 404)
        if invite.requester_id != current_user.id:
            return error_response("This invite is not for you", 403)
        if invite.status != "invited":
            return error_response("Invite is no longer valid", 400)

        thread = Thread.query.get(invite.thread_id)
        if not thread:
            return error_response("Thread not found", 404)
        if thread.member_count >= thread.max_members:
            return error_response("Thread is now full", 403)

        invite.status     = "approved"
        invite.reviewed_at = datetime.datetime.utcnow()

        db.session.add(ThreadMember(
            thread_id=thread.id, student_id=current_user.id, role="member"
        ))

        # Atomic SQL increment
        Thread.query.filter_by(id=thread.id).update(
            {
                Thread.member_count: Thread.member_count + 1,
                Thread.last_activity: datetime.datetime.utcnow()
            },
            synchronize_session=False
        )

        db.session.add(Notification(
            user_id=thread.creator_id,
            title=f"{current_user.name} accepted your invitation",
            body=f'Thread: "{thread.title}"',
            notification_type="thread_invite_accepted",
            related_type="thread",
            related_id=thread.id
        ))
        db.session.commit()

        # Issue 6: Notify existing members and the accepting user via personal room
        try:
            from services.websocket_threads import thread_ws_manager

            # Notify existing members that someone joined
            thread_ws_manager.broadcast_to_thread(thread.id, "thread_member_joined", {
                "thread_id": thread.id,
                "user": {
                    "id":       current_user.id,
                    "name":     current_user.name,
                    "username": current_user.username,
                    "avatar":   current_user.avatar,
                }
            })

            # Notify the accepting user — adds thread to their list immediately
            thread_ws_manager.notify_user(current_user.id, "thread_joined", {
                "thread_id": thread.id,
                "thread": {
                    "id":           thread.id,
                    "title":        thread.title,
                    "avatar":       thread.avatar,
                    "description":  thread.description,
                    "department":   thread.department,
                    "tags":         thread.tags or [],
                    "member_count": thread.member_count,
                    "max_members":  thread.max_members,
                    "is_open":      thread.is_open,
                    "last_activity": thread.last_activity.isoformat(),
                    "your_role":    "member",
                    "unread_count": 0,
                }
            })
        except Exception as ws_err:
            current_app.logger.warning(
                f"[ACCEPT_INVITE_WS_FAILED] thread_id={thread.id} error={ws_err!r}"
            )

        return success_response(
            "Invitation accepted! You're now a member.",
            data={
                "thread_id": thread.id,
                # Issue 2: return thread object so frontend can update state
                # without calling handleLoadThreadList()
                "thread": {
                    "id":           thread.id,
                    "title":        thread.title,
                    "avatar":       thread.avatar,
                    "description":  thread.description,
                    "department":   thread.department,
                    "tags":         thread.tags or [],
                    "member_count": thread.member_count,
                    "max_members":  thread.max_members,
                    "is_open":      thread.is_open,
                    "last_activity": thread.last_activity.isoformat(),
                    "your_role":    "member",
                    "unread_count": 0,
                }
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Accept invite error: {str(e)}")
        return error_response("Failed to accept invitation")


@threads_membership_bp.route("/threads/invites/<int:invite_id>/decline", methods=["POST"])
@token_required
def decline_thread_invite(current_user, invite_id):
    """Decline a thread invitation."""
    try:
        invite = ThreadJoinRequest.query.get(invite_id)
        if not invite:
            return error_response("Invite not found", 404)
        if invite.requester_id != current_user.id:
            return error_response("This invite is not for you", 403)
        if invite.status != "invited":
            return error_response("Invite is no longer valid", 400)

        invite.status      = "rejected"
        invite.reviewed_at = datetime.datetime.utcnow()
        db.session.commit()
        return success_response("Invitation declined")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Decline invite error: {str(e)}")
        return error_response("Failed to decline invitation")


@threads_membership_bp.route("/threads/<int:thread_id>/members/add", methods=["POST"])
@token_required
def add_members_to_thread(current_user, thread_id):
    """
    Directly add one or more users to a thread as full members.

    Only the creator or a moderator can call this endpoint.
    Added users must be accepted connections of the current user.
    Already-members are silently skipped (idempotent).
    Capacity is checked before adding; the batch is rejected if it would
    exceed max_members.

    Body JSON:
        { "user_ids": [<int>, ...] }   -- 1–10 user IDs

    Returns:
        added   – list of users successfully added
        skipped – user IDs that were already members or not found
    """
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)

        if not thread.is_open:
            return error_response("Thread is closed — reopen it before adding members", 403)

        require_moderator_or_creator(thread_id, current_user.id)

        data     = request.get_json(silent=True) or {}
        user_ids = data.get("user_ids", [])

        if not user_ids or not isinstance(user_ids, list):
            return error_response("user_ids must be a non-empty array")
        if len(user_ids) > 10:
            return error_response("Cannot add more than 10 members at once")

        # ── Verify every requested ID is an accepted connection ─────────
        from sqlalchemy import or_, and_
        accepted_connection_ids = {
            (c.receiver_id if c.requester_id == current_user.id else c.requester_id)
            for c in Connection.query.filter(
                or_(
                    and_(
                        Connection.requester_id == current_user.id,
                        Connection.receiver_id.in_(user_ids)
                    ),
                    and_(
                        Connection.receiver_id == current_user.id,
                        Connection.requester_id.in_(user_ids)
                    )
                ),
                Connection.status == "accepted"
            ).all()
        }

        # ── Who is already a member? ─────────────────────────────────────
        existing_member_ids = {
            m.student_id
            for m in ThreadMember.query.filter(
                ThreadMember.thread_id   == thread_id,
                ThreadMember.student_id.in_(user_ids)
            ).all()
        }

        to_add  = []
        skipped = []
        for uid in user_ids:
            if uid == current_user.id:
                skipped.append(uid)
                continue
            if uid in existing_member_ids:
                skipped.append(uid)
                continue
            if uid not in accepted_connection_ids:
                # Not a connection — cannot add
                skipped.append(uid)
                continue
            user = User.query.get(uid)
            if not user or user.status != "approved":
                skipped.append(uid)
                continue
            to_add.append(user)

        if not to_add:
            return error_response(
                "No eligible users to add — they may already be members, "
                "not your connections, or have inactive accounts"
            )

        # ── Capacity check ───────────────────────────────────────────────
        slots_available = thread.max_members - thread.member_count
        if len(to_add) > slots_available:
            return error_response(
                f"Not enough space — {slots_available} slot(s) available, "
                f"but you are trying to add {len(to_add)}"
            )

        # ── Add members ──────────────────────────────────────────────────
        added = []
        for user in to_add:
            # Cancel any existing pending/rejected join-request or invite row
            # so the new direct-add row doesn't violate the unique constraint.
            ThreadJoinRequest.query.filter_by(
                thread_id=thread_id, requester_id=user.id
            ).delete()

            db.session.add(ThreadMember(
                thread_id=thread_id, student_id=user.id, role="member"
            ))
            db.session.add(Notification(
                user_id=user.id,
                title=f"{current_user.name} added you to a thread",
                body=f'You are now a member of "{thread.title}"',
                notification_type="thread_member_added",
                related_type="thread",
                related_id=thread_id
            ))
            added.append({"id": user.id, "username": user.username, "name": user.name, "avatar": user.avatar})

        Thread.query.filter_by(id=thread_id).update(
            {
                Thread.member_count:  Thread.member_count + len(to_add),
                Thread.last_activity: datetime.datetime.utcnow()
            },
            synchronize_session=False
        )

        db.session.commit()

        # ── Real-time notifications ──────────────────────────────────────
        try:
            from services.websocket_threads import thread_ws_manager

            # Tell everyone already in the thread room that new people joined
            for user_data in added:
                thread_ws_manager.broadcast_to_thread(thread_id, "thread_member_joined", {
                    "thread_id": thread_id,
                    "user": user_data,
                })

            # Reload the thread row so member_count is fresh
            thread = Thread.query.get(thread_id)
            thread_snapshot = {
                "id":           thread.id,
                "title":        thread.title,
                "avatar":       thread.avatar,
                "description":  thread.description,
                "department":   thread.department,
                "tags":         thread.tags or [],
                "member_count": thread.member_count,
                "max_members":  thread.max_members,
                "is_open":      thread.is_open,
                "last_activity": thread.last_activity.isoformat(),
                "your_role":    "member",
                "unread_count": 0,
            }

            # Push thread_joined to each new member's personal room so the
            # thread appears in their list immediately without a reload.
            for user_data in added:
                thread_ws_manager.notify_user(user_data["id"], "thread_joined", {
                    "thread_id": thread_id,
                    "thread":    thread_snapshot,
                })

        except Exception as ws_err:
            current_app.logger.warning(
                f"[ADD_MEMBERS_WS_FAILED] thread_id={thread_id} error={ws_err!r}"
            )

        return jsonify({
            "status":  "success",
            "message": f"Added {len(added)} member(s) to the thread",
            "data": {
                "added":       added,
                "skipped":     skipped,
                "member_count": thread.member_count,
            }
        }), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Add members to thread error: {e!r}", exc_info=True)
        return error_response("Failed to add members")

