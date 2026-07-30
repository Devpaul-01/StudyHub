"""
StudyHub - Threads: Discovery/recommendation (department stats, popular, recommended, help suggestions, my-threads)

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

threads_discovery_bp = Blueprint("threads_discovery", __name__)
@threads_discovery_bp.route("/threads/departments", methods=["GET"])
@token_required
def get_department_stats(current_user):
    """Get thread statistics by department. FIX: case() now imported."""
    try:
        department_stats = db.session.query(
            Thread.department,
            func.count(Thread.id).label('total_threads'),
            func.sum(
                case(
                    (Thread.member_count < Thread.max_members, 1),
                    else_=0
                )
            ).label('available_threads'),
            func.sum(Thread.member_count).label('total_members'),
            func.avg(Thread.member_count).label('avg_members')
        ).filter(
            Thread.is_open == True,
            Thread.department.isnot(None)
        ).group_by(Thread.department).order_by(desc('total_threads')).all()

        departments_data = []
        for dept, total, available, total_members, avg_members in department_stats:
            departments_data.append({
                'department':             dept,
                'total_threads':          total,
                'available_threads':      available or 0,
                'total_members':          total_members or 0,
                'avg_members_per_thread': round(avg_members, 1) if avg_members else 0
            })

        profile   = StudentProfile.query.filter_by(user_id=current_user.id).first()
        user_dept = profile.department if profile else None

        return jsonify({
            'status': 'success',
            'data': {
                'departments':       departments_data,
                'your_department':   user_dept,
                'total_departments': len(departments_data)
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get department stats error: {str(e)}")
        return error_response("Failed to load department statistics")


# ============================================================================
# DISCOVERY: POPULAR THREADS
# ============================================================================

@threads_discovery_bp.route("/threads/popular", methods=["GET"])
@token_required
def get_popular_threads_by_members(current_user):
    """Get most popular threads by member count (excluding user's department)."""
    try:
        limit       = min(int(request.args.get('limit', 20)), 50)
        min_members = int(request.args.get('min_members', 3))

        profile   = StudentProfile.query.filter_by(user_id=current_user.id).first()
        user_dept = profile.department if profile else None

        member_thread_ids = [
            m.thread_id for m in ThreadMember.query.filter_by(student_id=current_user.id).all()
        ]

        query = Thread.query.filter(
            Thread.is_open == True,
            Thread.member_count >= min_members,
            Thread.member_count < Thread.max_members,
            Thread.department != user_dept if user_dept else True,
            ~Thread.id.in_(member_thread_ids) if member_thread_ids else True
        ).order_by(
            Thread.member_count.desc(),
            Thread.message_count.desc(),
            Thread.last_activity.desc()
        ).limit(limit * 2)

        threads      = query.all()
        threads_data = []

        for thread in threads:
            creator = User.query.get(thread.creator_id)
            has_pending = ThreadJoinRequest.query.filter_by(
                thread_id=thread.id,
                requester_id=current_user.id,
                status='pending'
            ).first() is not None

            thread_age_days  = (datetime.datetime.utcnow() - thread.created_at).days or 1
            msgs_per_member  = (thread.message_count / thread.member_count) if thread.member_count > 0 else 0
            messages_per_day = thread.message_count / thread_age_days

            threads_data.append({
                'id': thread.id, 'title': thread.title,
                'description': thread.description,
                'department': thread.department,
                'tags': thread.tags or [],
                'member_count': thread.member_count,
                'max_members': thread.max_members,
                'message_count': thread.message_count,
                'requires_approval': thread.requires_approval,
                'is_standalone': thread.post_id is None,
                'avatar': thread.avatar,
                'created_at': thread.created_at.isoformat(),
                'last_activity': thread.last_activity.isoformat(),
                'creator': {
                    'id': creator.id, 'username': creator.username,
                    'name': creator.name, 'avatar': creator.avatar,
                    'reputation_level': creator.reputation_level
                } if creator else None,
                'popularity_metrics': {
                    'member_percentage':    round((thread.member_count / thread.max_members) * 100, 1),
                    'messages_per_member':  round(msgs_per_member, 1),
                    'messages_per_day':     round(messages_per_day, 1),
                    'age_days':             thread_age_days,
                    'is_trending':          messages_per_day > 5 and thread_age_days < 30
                },
                'cross_department':   True,
                'has_pending_request': has_pending
            })

        return jsonify({
            'status': 'success',
            'data': {
                'threads':             threads_data[:limit],
                'excluded_department': user_dept,
                'total_found':         len(threads_data),
                'discovery_mode':      'cross_department'
            },
            'message': 'Discover popular threads from other departments'
        })

    except Exception as e:
        current_app.logger.error(f"Get popular threads error: {str(e)}")
        return error_response("Failed to load popular threads")


# ============================================================================
# DISCOVERY: RECOMMENDED THREADS
# ============================================================================

@threads_discovery_bp.route("/threads/recommended", methods=["GET"])
@token_required
def get_recommended_threads(current_user):
    """Get personalized thread recommendations. FIX: SQL pre-filter limits in-memory set."""
    try:
        limit = min(int(request.args.get('limit', 10)), 30)

        user       = User.query.get(current_user.id)
        profile    = StudentProfile.query.filter_by(user_id=current_user.id).first()
        onboarding = OnboardingDetails.query.filter_by(user_id=current_user.id).first()

        user_dept          = profile.department if profile else None
        user_subjects      = set(onboarding.subjects or [])      if onboarding else set()
        user_help_subjects = set(onboarding.help_subjects or []) if onboarding else set()

        connections  = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id  == current_user.id
            ),
            Connection.status == 'accepted'
        ).all()
        friend_ids = [
            c.receiver_id if c.requester_id == current_user.id else c.requester_id
            for c in connections
        ]

        member_thread_ids = [
            m.thread_id for m in ThreadMember.query.filter_by(student_id=current_user.id).all()
        ]

        # FIX: SQL pre-filter to cap in-memory set at 200 recently-active threads
        thirty_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=30)
        threads = Thread.query.filter(
            Thread.is_open == True,
            Thread.member_count < Thread.max_members,
            Thread.last_activity >= thirty_days_ago,
            ~Thread.id.in_(member_thread_ids) if member_thread_ids else True
        ).limit(200).all()

        # FIX: preload all friend User objects in a single query to avoid N+1
        friend_user_map = {}
        if friend_ids:
            friend_user_map = {
                u.id: u
                for u in User.query.filter(User.id.in_(friend_ids)).all()
            }

        recommendations = []
        for thread in threads:
            score   = 0
            reasons = []

            if thread.department == user_dept:
                score += 35
                reasons.append("Your department")

            thread_tags         = set(thread.tags or [])
            all_user_subjects   = user_subjects | user_help_subjects
            subject_overlap     = thread_tags & all_user_subjects
            if subject_overlap:
                score += min(len(subject_overlap) * 10, 30)
                reasons.append(f"Matches: {', '.join(list(subject_overlap)[:2])}")

            thread_members     = ThreadMember.query.filter_by(thread_id=thread.id).all()
            thread_member_ids  = [m.student_id for m in thread_members]
            friends_in_thread  = set(friend_ids) & set(thread_member_ids)
            if friends_in_thread:
                score += min(len(friends_in_thread) * 10, 20)
                friend_names = [
                    friend_user_map[fid].name
                    for fid in list(friends_in_thread)[:2]
                    if fid in friend_user_map
                ]
                reasons.append(f"{', '.join(friend_names)} already in")

            hours_since = (datetime.datetime.utcnow() - thread.last_activity).total_seconds() / 3600
            if hours_since < 24:
                score += 10 - (hours_since / 24 * 10)
                if hours_since < 2:
                    reasons.append("Very active now")

            if thread.member_count < thread.max_members * 0.7:
                score += 5
                reasons.append("Good space available")

            if score > 20:
                creator = User.query.get(thread.creator_id)
                has_pending = ThreadJoinRequest.query.filter_by(
                    thread_id=thread.id, requester_id=current_user.id, status='pending'
                ).first() is not None

                recommendations.append({
                    'score': score,
                    'thread': {
                        'id': thread.id, 'title': thread.title,
                        'description': thread.description,
                        'department': thread.department,
                        'tags': thread.tags or [],
                        'member_count': thread.member_count,
                        'max_members': thread.max_members,
                        'message_count': thread.message_count,
                        'requires_approval': thread.requires_approval,
                        'avatar': thread.avatar,
                        'created_at': thread.created_at.isoformat(),
                        'last_activity': thread.last_activity.isoformat(),
                        'creator': {
                            'id': creator.id, 'username': creator.username,
                            'name': creator.name, 'avatar': creator.avatar,
                            'reputation_level': creator.reputation_level
                        } if creator else None,
                        'recommendation_score': round(score, 1),
                        'reasons': reasons,
                        'has_pending_request': has_pending
                    }
                })

        recommendations.sort(key=lambda x: x['score'], reverse=True)
        top = recommendations[:limit]

        return jsonify({
            'status': 'success',
            'data': {
                'recommendations': [r['thread'] for r in top],
                'total_found':     len(recommendations),
                'showing':         len(top),
                'personalization': {
                    'has_onboarding': onboarding is not None,
                    'has_friends':    len(friend_ids) > 0,
                    'department':     user_dept
                }
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get recommendation error: {e}", exc_info=True)
        return error_response("Failed to load recommendations")


# ============================================================================
# DISCOVERY: HELP SUGGESTIONS
# ============================================================================

@threads_discovery_bp.route("/threads/help/suggestions", methods=["GET"])
@token_required
def get_help_suggestions(current_user):
    """Find users the current user can help based on onboarding details."""
    try:
        limit = min(int(request.args.get('limit', 10)), 50)

        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")

        user_onboarding = OnboardingDetails.query.filter_by(user_id=user.id).first()
        if not user_onboarding:
            return error_response(
                "Complete your onboarding to get help suggestions",
                data={'redirect': '/student/onboard'}
            )

        user_strong_subjects = set(user_onboarding.strong_subjects or [])
        if not user_strong_subjects:
            return success_response("No strong subjects set", data={'suggestions': []})

        user_profile   = user.student_profile
        user_dept      = user_profile.department if user_profile else None
        user_schedule  = user_onboarding.study_schedule or {}

        existing_connections = [
            c.receiver_id if c.requester_id == current_user.id else c.requester_id
            for c in Connection.query.filter(
                or_(
                    Connection.requester_id == current_user.id,
                    Connection.receiver_id  == current_user.id
                ),
                Connection.status == 'accepted'
            ).all()
        ]

        potential_users = db.session.query(User, OnboardingDetails, StudentProfile).join(
            OnboardingDetails, OnboardingDetails.user_id == User.id
        ).outerjoin(
            StudentProfile, StudentProfile.user_id == User.id
        ).filter(
            User.id != current_user.id,
            User.status == 'approved',
            ~User.id.in_(existing_connections) if existing_connections else True
        ).all()

        suggestions = []
        for candidate_user, candidate_onboarding, candidate_profile in potential_users:
            if not candidate_onboarding:
                continue
            candidate_help_subjects = set(candidate_onboarding.help_subjects or [])
            if not candidate_help_subjects:
                continue

            matching_subjects = user_strong_subjects & candidate_help_subjects
            if not matching_subjects:
                continue

            score        = 0
            match_reasons = []

            subject_score = min(len(matching_subjects) * 10, 40)
            score += subject_score
            match_reasons.append(f"Can help with: {', '.join(list(matching_subjects)[:3])}")

            if candidate_profile and candidate_profile.department == user_dept:
                score += 30
                match_reasons.append(f"Same department: {user_dept}")

            candidate_schedule = candidate_onboarding.study_schedule or {}
            schedule_overlap   = 0
            for day, times in user_schedule.items():
                candidate_times = candidate_schedule.get(day, [])
                if candidate_times and times:
                    schedule_overlap += len(set(times) & set(candidate_times))
            if schedule_overlap > 0:
                score += min(schedule_overlap * 5, 20)
                match_reasons.append("Compatible study times")

            if candidate_profile and user_profile:
                if candidate_profile.class_name == user_profile.class_name:
                    score += 10
                    match_reasons.append(f"Same level: {user_profile.class_name}")

            pending_request = Connection.query.filter(
                or_(
                    and_(Connection.requester_id == current_user.id, Connection.receiver_id == candidate_user.id),
                    and_(Connection.requester_id == candidate_user.id, Connection.receiver_id == current_user.id)
                ),
                Connection.status == 'pending'
            ).first()

            suggestions.append({
                'score': score,
                'user': {
                    'id': candidate_user.id, 'username': candidate_user.username,
                    'name': candidate_user.name, 'avatar': candidate_user.avatar,
                    'reputation': candidate_user.reputation,
                    'reputation_level': candidate_user.reputation_level,
                    'bio': candidate_user.bio,
                    'department': candidate_profile.department if candidate_profile else None,
                    'class_level': candidate_profile.class_name if candidate_profile else None
                },
                'match_details': {
                    'can_help_with':    list(matching_subjects),
                    'total_subjects':   len(matching_subjects),
                    'match_score':      round(score, 1),
                    'reasons':          match_reasons,
                    'same_department':  candidate_profile and candidate_profile.department == user_dept,
                    'has_pending_request': pending_request is not None
                },
                'their_needs': {
                    'help_subjects':     candidate_onboarding.help_subjects or [],
                    'study_preferences': candidate_onboarding.study_preferences or [],
                    'session_length':    candidate_onboarding.session_length
                }
            })

        suggestions.sort(key=lambda x: x['score'], reverse=True)
        top = suggestions[:limit]

        return jsonify({
            'status': 'success',
            'data': {
                'suggestions':    top,
                'your_strengths': list(user_strong_subjects),
                'total_found':    len(suggestions),
                'showing':        len(top)
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get help suggestions error: {str(e)}")
        return error_response("Failed to load help suggestions")


# ============================================================================
# THREAD VIEWING — MEMBER MANAGEMENT
# ============================================================================

@threads_discovery_bp.route("/threads/my-threads", methods=["GET"])
@token_required
def get_my_threads(current_user):
    """Get all threads user is a member of. Includes last_message preview."""
    try:
        memberships  = ThreadMember.query.filter_by(student_id=current_user.id).all()
        threads_data = []

        for membership in memberships:
            thread = Thread.query.get(membership.thread_id)
            if not thread:
                continue

            # Unread count
            # FIX: NULL last_read_at means member never opened thread → all messages unread
            cutoff = membership.last_read_at or datetime.datetime(2000, 1, 1)
            unread_count = ThreadMessage.query.filter(
                ThreadMessage.thread_id == thread.id,
                ThreadMessage.sent_at   >  cutoff,
                ThreadMessage.sender_id != current_user.id,
                ThreadMessage.is_deleted == False
            ).count()

            # Last message preview
            last_msg = ThreadMessage.query.filter_by(
                thread_id=thread.id, is_deleted=False
            ).order_by(ThreadMessage.sent_at.desc()).first()

            last_message_preview = None
            if last_msg:
                if last_msg.attachment_url and not last_msg.text_content:
                    type_map     = {'image': '📷 Image', 'video': '🎬 Video', 'document': '📎 File'}
                    preview_text = type_map.get(last_msg.attachment_type, '📎 Attachment')
                elif last_msg.is_ai_response:
                    preview_text = f'🤖 {last_msg.text_content[:60]}'
                else:
                    preview_text = last_msg.text_content[:80] if last_msg.text_content else ''

                sender = User.query.get(last_msg.sender_id)

                # ── Message status (only meaningful when current user is sender) ──
                # "seen"      → at least one other member has read past this message
                # "delivered" → in DB but nobody else has read it yet
                msg_status = None
                if last_msg.sender_id == current_user.id:
                    other_members = ThreadMember.query.filter(
                        ThreadMember.thread_id  == thread.id,
                        ThreadMember.student_id != current_user.id
                    ).all()
                    anyone_seen = any(
                        m.last_read_at and m.last_read_at >= last_msg.sent_at
                        for m in other_members
                    )
                    msg_status = "seen" if anyone_seen else "delivered"

                last_message_preview = {
                    "text":      preview_text,
                    "sender":    sender.name if sender else "Unknown",
                    "sender_id": last_msg.sender_id,
                    "sent_at":   last_msg.sent_at.isoformat(),
                    "status":    last_msg.status   # "seen" | "delivered" | None (not sender)
                }

            threads_data.append({
                "id":            thread.id,
                "title":         thread.title,
                "avatar":        thread.avatar,
                "department":    thread.department,
                "tags":          thread.tags or [],
                "member_count":  thread.member_count,
                "max_members":   thread.max_members,
                "message_count": thread.message_count,
                "is_open":       thread.is_open,
                "is_creator":    thread.creator_id == current_user.id,
                "last_activity": thread.last_activity.isoformat(),
                "last_message":  last_message_preview,
                "unread_count":  unread_count,
                "your_role":     membership.role
            })

        threads_data.sort(key=lambda x: x["last_activity"], reverse=True)

        return jsonify({
            "status": "success",
            "data": {"threads": threads_data, "total": len(threads_data)}
        })

    except Exception as e:
        current_app.logger.error(f"Get my threads error: {str(e)}")
        return error_response("Failed to load your threads")


# ============================================================================
# PENDING REQUESTS (for creator's dashboard)
# ============================================================================

@threads_discovery_bp.route("/threads/open", methods=["GET"])
@token_required
def open_thread(current_user):
    """List all open threads, ordered by department match then activity."""
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")

        profile   = StudentProfile.query.filter_by(user_id=user.id).first()
        user_dept = profile.department if profile else None

        threads = (
            Thread.query
            .filter(Thread.is_open == True)
            .order_by(
                (Thread.department == user_dept).desc() if user_dept else Thread.last_activity.desc(),
                Thread.last_activity.desc(),
                Thread.created_at.desc()
            )
            .all()
        )

        threads_data = []
        for thread in threads:
            threads_data.append({
                "id":                thread.id,
                "title":             thread.title,
                "description":       thread.description,
                "department":        thread.department,
                "tags":              thread.tags,
                "member_count":      thread.member_count,
                "max_members":       thread.max_members,
                "requires_approval": thread.requires_approval,
                "avatar":            thread.avatar,
                "last_activity":     thread.last_activity.isoformat(),
                "is_full":           thread.member_count >= thread.max_members
            })

        return jsonify({"status": "success", "data": threads_data})

    except Exception as e:
        current_app.logger.error(f"Open threads error: {e}")
        return error_response("Failed to load open threads")


# ============================================================================
# GET SINGLE THREAD
# ============================================================================

@threads_discovery_bp.route("/threads/<int:thread_id>", methods=["GET"])
@token_required
def get_thread(current_user, thread_id):
    """
    Get full thread detail.
    - All users: basic thread info + user's membership status.
    - Members: full member list.
    - Creator / moderator: pending join requests included.
    """
    try:
        thread = Thread.query.get(thread_id)
        if not thread:
            return error_response("Thread not found", 404)

        membership = ThreadMember.query.filter_by(
            thread_id=thread_id, student_id=current_user.id
        ).first()

        is_member  = bool(membership)
        is_creator = thread.creator_id == current_user.id

        pending_request = ThreadJoinRequest.query.filter_by(
            thread_id=thread_id, requester_id=current_user.id, status="pending"
        ).first()

        creator = User.query.get(thread.creator_id)

        post = None
        if thread.post_id:
            post_obj = Post.query.get(thread.post_id)
            if post_obj:
                post = {"id": post_obj.id, "title": post_obj.title, "post_type": post_obj.post_type}

        thread_data = {
            "id":               thread.id,
            "title":            thread.title,
            "description":      thread.description,
            "department":       thread.department,
            "tags":             thread.tags,
            "avatar":           thread.avatar,
            "is_open":          thread.is_open,
            "member_count":     thread.member_count,
            "max_members":      thread.max_members,
            "is_full":          thread.member_count >= thread.max_members,
            "requires_approval":thread.requires_approval,
            "created_at":       thread.created_at.isoformat(),
            "last_activity":    thread.last_activity.isoformat(),
            # creator_id exposed at the top level so the frontend can compare
            # directly without drilling into the nested creator object.
            "creator_id":       thread.creator_id,
            "creator": {
                "id":       creator.id,
                "username": creator.username,
                "name":     creator.name,
                "avatar":   creator.avatar
            } if creator else None,
            "post":        post,
            "is_standalone": thread.post_id is None
        }

        user_status = {
            "is_member":          is_member,
            "is_creator":         is_creator,
            "your_role":          membership.role if membership else None,
            "has_pending_request":bool(pending_request),
            "can_join": (
                not is_member and
                thread.is_open and
                thread.member_count < thread.max_members
            )
        }

        if is_member:
            members      = ThreadMember.query.filter_by(thread_id=thread_id).all()
            members_data = []
            for m in members:
                u = User.query.get(m.student_id)
                if u:
                    members_data.append({
                        "id":            u.id,
                        "username":      u.username,
                        "name":          u.name,
                        "avatar":        u.avatar,
                        "role":          m.role,
                        "joined_at":     m.joined_at.isoformat(),
                        "messages_sent": m.messages_sent
                    })
            thread_data["members"]       = members_data
            thread_data["message_count"] = thread.message_count

        if is_member and membership and is_moderator_or_creator(membership):
            pending_reqs  = ThreadJoinRequest.query.filter_by(
                thread_id=thread_id, status="pending"
            ).all()
            requests_data = []
            for req in pending_reqs:
                requester = User.query.get(req.requester_id)
                if requester:
                    requests_data.append({
                        "request_id":   req.id,
                        "user": {
                            "id":       requester.id,
                            "username": requester.username,
                            "name":     requester.name,
                            "avatar":   requester.avatar
                        },
                        "message":      req.message,
                        "requested_at": req.requested_at.isoformat()
                    })
            thread_data["pending_requests"] = requests_data

        return jsonify({
            "status": "success",
            "data": {"thread": thread_data, "user_status": user_status}
        })

    except Exception as e:
        current_app.logger.error(f"Get thread error: {e}")
        return error_response("Failed to load thread")


# ============================================================================
# JOIN REQUESTS
# ============================================================================

