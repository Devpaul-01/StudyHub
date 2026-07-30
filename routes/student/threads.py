"""
StudyHub - Complete Thread System
Private collaboration groups for studying together
Includes: creation, invites, join requests, chat, member management

Fixes applied vs original:
  - case() imported from sqlalchemy (fixes get_department_stats crash)
  - upload_thread_attachment now uses Cloudinary instead of Supabase
  - GET /threads/<id>/members endpoint added (was missing, frontend called it)
  - get_my_threads includes last_message preview + avatar field
  - POST /threads/<id>/avatar endpoint added
  - approve/reject routes now use request_id URL pattern (matches frontend constants)
  - accept_thread_invite uses atomic SQL increment (fixes race condition)
  - request_join_thread parses request body only once (fixes stream re-read)
  - cancel_join_request dead code removed
  - get_recommended_threads adds SQL pre-filter for scale
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
    ThreadMessage, ThreadMessageReaction, ThreadMessageAttachment,  # Issue 1
    Post, Notification, Connection,
    Mention, OnboardingDetails,
    ThreadMeetingNote,   # ADD
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)

# Document 1 §2.4: shared multi-provider AI access, moved out of learnora.py
# into services/ai_provider_service.py. call_ai_response() replaces the
# single-attempt _call_provider_sync() call this file used to make directly
# for meeting-notes generation, adding retry/rotation for free.
from services.ai_provider_service import call_ai_response

# Document 1 §2.2 / Document 2 §3.8: the single moderator/creator check,
# replacing the local _is_mod_or_creator_static and the five separate
# inline re-derivations of the same check that used to be scattered
# through this file's route bodies.
from services.thread_authorization import is_moderator_or_creator, require_moderator_or_creator

import sys
import os
import re as _re
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

threads_bp = Blueprint("student_threads", __name__)


# ============================================================================
# PAGE ROUTE
# ============================================================================

@threads_bp.route("/", methods=["GET"])
@token_required
def threads_page(current_user):
    return render_template('threads/threads.html')


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

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

@threads_bp.route("/threads/create", methods=["POST"])
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


@threads_bp.route("/threads/create-standalone", methods=["POST"])
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

@threads_bp.route("/threads/<int:resource_id>/details", methods=["POST"])
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

@threads_bp.route("/threads/departments", methods=["GET"])
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

@threads_bp.route("/threads/popular", methods=["GET"])
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

@threads_bp.route("/threads/recommended", methods=["GET"])
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

@threads_bp.route("/threads/help/suggestions", methods=["GET"])
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

@threads_bp.route("/threads/<int:thread_id>/leave", methods=["POST"])
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


@threads_bp.route("/threads/<int:thread_id>/remove/<int:user_id>", methods=["DELETE"])
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

@threads_bp.route("/threads/<int:thread_id>/members", methods=["GET"])
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

@threads_bp.route("/threads/<int:thread_id>/close", methods=["POST"])
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


@threads_bp.route("/threads/<int:thread_id>/reopen", methods=["POST"])
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


@threads_bp.route("/threads/<int:thread_id>", methods=["PATCH"])
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


@threads_bp.route("/threads/<int:thread_id>", methods=["DELETE"])
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

@threads_bp.route("/threads/<int:thread_id>/avatar", methods=["POST"])
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

@threads_bp.route("/threads/<int:thread_id>/messages", methods=["GET"])
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


@threads_bp.route("/threads/<int:thread_id>/messages/upload", methods=["POST"])
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


@threads_bp.route("/threads/<int:thread_id>/messages/search", methods=["GET"])
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


@threads_bp.route("/threads/<int:thread_id>/messages/pinned", methods=["GET"])
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


@threads_bp.route("/threads/<int:thread_id>/messages", methods=["POST"])
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


@threads_bp.route("/threads/<int:thread_id>/messages/<int:message_id>", methods=["PATCH"])
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


@threads_bp.route("/threads/<int:thread_id>/messages/<int:message_id>", methods=["DELETE"])
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

@threads_bp.route("/threads/my-threads", methods=["GET"])
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

@threads_bp.route("/threads/pending-requests", methods=["GET"])
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


@threads_bp.route("/threads/my-requests", methods=["GET"])
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

@threads_bp.route("/threads/<int:thread_id>/members/<int:user_id>/role", methods=["PATCH"])
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

@threads_bp.route("/threads/<int:thread_id>/stats", methods=["GET"])
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

@threads_bp.route("/threads/<int:thread_id>/settings", methods=["GET"])
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


@threads_bp.route("/threads/<int:thread_id>/settings", methods=["PATCH"])
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

@threads_bp.route("/threads/open", methods=["GET"])
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

@threads_bp.route("/threads/<int:thread_id>", methods=["GET"])
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

@threads_bp.route("/threads/requests/<int:request_id>/cancel", methods=["DELETE"])
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


@threads_bp.route("/threads/<int:resource_id>/join", methods=["POST"])
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

@threads_bp.route("/threads/<int:thread_id>/requests/<int:request_id>/approve", methods=["POST"])
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


@threads_bp.route("/threads/<int:thread_id>/requests/<int:request_id>/reject", methods=["POST"])
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

@threads_bp.route("/threads/<int:thread_id>/invite/<int:user_id>", methods=["POST"])
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


@threads_bp.route("/threads/invites", methods=["GET"])
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


@threads_bp.route("/threads/invites/<int:invite_id>/accept", methods=["POST"])
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


@threads_bp.route("/threads/invites/<int:invite_id>/decline", methods=["POST"])
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


@threads_bp.route("/threads/<int:thread_id>/meeting-notes", methods=["POST"])
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


@threads_bp.route("/threads/<int:thread_id>/meeting-notes", methods=["GET"])
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
@threads_bp.route("/threads/<int:thread_id>/members/add", methods=["POST"])
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
