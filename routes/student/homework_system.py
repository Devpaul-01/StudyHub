"""
StudyHub - Comprehensive Homework & Assignment System
Combines personal assignments with collaborative homework help

Priority scoring and pagination logic now live in services/homework_service.py
(Document 2 §6's fully-worked example); notification construction now goes
through services/notification_service.py (Document 2 §3.9). This file is
the HTTP layer: request parsing, auth, response envelope, and the
still-route-local help-streak/activity-feed helpers (those stay here since
they're not yet named as a distinct service in the current migration pass —
flagged below for a future homework_service extension if that grows).

Organizational standard (Document 1 §4, applied in Phase 3): module
docstring + section banners (below), pure/no-DB helpers first, then
DB-batch-loading helpers, then routes, roughly outside-in from
cheapest-to-test to most integration-heavy. Batch-loading helpers for
online status use services/online_status_service.py::get_online_status_batch
— every GET route below that lists multiple users' online status now
does exactly one batch call before its loop, never a per-row call inside
it (Document 4 §4's N+1 fix, applied to get_homework_feed,
get_my_help_requests, and get_homework_im_helping_with).
"""

from __future__ import annotations

import datetime as _dt

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import or_, and_, func, desc
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta

from models import (
    User, Assignment, HomeworkSubmission, Connection,
    Notification, LiveStudySession
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)
from services.online_status_service import get_user_online_status, get_online_status_batch
from services import homework_service, notification_service
# Phase 5b (Document 4 §1): WRITE_HEAVY-tier limiting on assignment/help-
# request create/update/delete/offer-help/submit-solution/give-feedback
# routes.
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key, ip_key

homework_bp = Blueprint("student_homework", __name__)


# ============================================================================
# PURE / NO-DB HELPERS
# ============================================================================

def _parse_pagination_params() -> tuple[int, str | None]:
    """Route-layer wrapper: pulls limit/cursor out of request.args, then
    delegates parsing/validation to the service."""
    return homework_service.parse_pagination_params(
        request.args.get("limit"), request.args.get("cursor")
    )


def _slice_by_cursor(items: list, cursor: str | None, limit: int) -> tuple[list, bool, str | None]:
    return homework_service.slice_by_cursor(items, cursor, limit)


def _get_urgency_level(hours_until_due: float) -> str:
    return homework_service.get_urgency_level(hours_until_due)


def _get_smart_suggestions(assignments: list, user) -> list[dict]:
    return homework_service.get_smart_suggestions(assignments)


# ============================================================================
# HELP STREAK / ACTIVITY FEED  (route-local for now — not yet its own service)
# ============================================================================

def _update_help_streak(helper_user_id: int) -> dict | None:
    """
    Update help streak when user provides helpful assistance.
    Called when HomeworkSubmission gets positive feedback.

    AUDIT ENG-8 FIX: no longer commits internally. This was the one
    service-layer helper in this file that broke the codebase's
    established "services mutate + return, the calling route commits
    once" convention (see e.g. services/reputation_service.py's
    award_reputation, which the codebase's own comments already point to
    as the pattern to follow) — its caller, give_feedback, already had
    its own commit a few lines after calling this, so the submission's
    feedback/status fields and this streak update were never actually
    atomic with each other despite looking like they should be. Caller
    now owns the single commit that covers both.
    """
    user = User.query.get(helper_user_id)
    if not user:
        return None

    today = datetime.utcnow().date()
    last_updated = user.help_streak_last_updated.date() if user.help_streak_last_updated else None

    if last_updated == today:
        return {
            'current_streak': user.help_streak_current,
            'longest_streak': user.help_streak_longest,
            'is_new_record': False
        }

    yesterday = today - timedelta(days=1)

    if last_updated == yesterday:
        user.help_streak_current += 1
    elif last_updated is None or (today - last_updated).days > 1:
        user.help_streak_current = 1

    is_new_record = False
    if user.help_streak_current > user.help_streak_longest:
        user.help_streak_longest = user.help_streak_current
        is_new_record = True

    user.help_streak_last_updated = datetime.utcnow()
    user.total_helps_given += 1

    return {
        'current_streak': user.help_streak_current,
        'longest_streak': user.help_streak_longest,
        'is_new_record': is_new_record
    }


def _create_activity(user_id: int, activity_type: str, data: dict):
    """Create activity feed entry. Auto-expires after 24 hours.

    Broad except is intentional here (Document 1 §4 point 5's "scoped
    comment" requirement): this helper is a best-effort side channel
    (activity feed / WebSocket broadcast) called from several mutation
    routes — a DB write succeeding but the activity-feed write or the
    WebSocket broadcast failing (bad ActivityFeed data, ws_manager down,
    etc.) should never roll back or fail the caller's primary operation,
    so every failure mode here is swallowed and logged rather than raised.

    AUDIT BUG-4 FIX: services.websocket_events.ws_manager is never
    initialized anywhere in the app, so ws_manager.broadcast_activity(activity)
    silently no-op'd on every call — this row was always correctly
    persisted (get_activity_feed already reads it back on next poll/
    reload, so that path was never affected), but no connection ever saw
    it pushed live. There is no broadcast_activity equivalent on the
    live message_ws_manager (it only exposes a per-user emit_to_user),
    and no separate "activity broadcast room" exists anywhere else in
    this codebase — get_activity_feed's own query
    (ActivityFeed.user_id.in_(connection_ids), a few functions above)
    is the actual, only definition of who should see this: the actor's
    accepted connections. So the live-push equivalent of a broadcast
    here is a per-connection emit_to_user, matching exactly what the
    DB-backed feed already promises those same connections on their
    next reload — this fix makes the live and polled paths agree
    instead of inventing new fan-out semantics.
    """
    from models import ActivityFeed

    try:
        activity = ActivityFeed(
            user_id=user_id,
            activity_type=activity_type,
            activity_data=data,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=24)
        )
        db.session.add(activity)
        db.session.commit()

        from services.websocket_messages import message_ws_manager

        connections = Connection.query.filter(
            or_(
                Connection.requester_id == user_id,
                Connection.receiver_id == user_id
            ),
            Connection.status == 'accepted'
        ).all()

        payload = {
            'id': activity.id,
            'type': activity.activity_type,
            'user_id': user_id,
            'data': activity.activity_data,
            'created_at': activity.created_at.isoformat(),
        }

        for conn in connections:
            other_id = conn.receiver_id if conn.requester_id == user_id else conn.requester_id
            message_ws_manager.emit_to_user(other_id, 'activity_feed_update', payload)

        return activity
    except Exception as e:
        current_app.logger.error(f"Create activity error: {e}", exc_info=True)
        db.session.rollback()
        return None


# ============================================================================
# PERSONAL ASSIGNMENTS (Private To-Do List)
# ============================================================================

@homework_bp.route("/homework/<int:assignment_id>/helpers", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_assignment_helpers(current_user, assignment_id):
    """Get all helpers for a specific assignment."""
    try:
        assignment = Assignment.query.get_or_404(assignment_id)

        if assignment.user_id != current_user.id:
            return error_response("Unauthorized. You can only view helpers for your own assignments.", 403)

        if not assignment.is_shared_for_help:
            return error_response("This assignment is not shared for help.", 400)

        submissions = HomeworkSubmission.query.filter_by(
            assignment_id=assignment_id,
            requester_id=current_user.id
        ).order_by(
            db.case(
                (HomeworkSubmission.status == 'completed', 1),
                (HomeworkSubmission.status == 'reviewed', 2),
                (HomeworkSubmission.status == 'submitted', 3),
                (HomeworkSubmission.status == 'pending', 4),
                else_=5
            ),
            HomeworkSubmission.created_at.desc()
        ).all()

        helper_ids = [s.helper_id for s in submissions]
        helpers_map = {u.id: u for u in User.query.filter(User.id.in_(helper_ids)).all()} if helper_ids else {}

        helpers_data = []
        for submission in submissions:
            helper_user = helpers_map.get(submission.helper_id)

            if helper_user:
                helpers_data.append({
                    'id': submission.id,
                    'helper': {
                        'id': helper_user.id,
                        'name': helper_user.name,
                        'username': helper_user.username,
                        'avatar': helper_user.avatar
                    },
                    'status': submission.status,
                    'created_at': submission.created_at.isoformat() if submission.created_at else None,
                    'submitted_at': submission.submitted_at.isoformat() if submission.submitted_at else None,
                    'feedback_at': submission.feedback_at.isoformat() if submission.feedback_at else None,
                    'reaction_at': submission.reaction_at.isoformat() if submission.reaction_at else None,
                    'has_solution': bool(submission.solution_text),
                    'has_feedback': bool(submission.feedback_text),
                    'is_marked_helpful': submission.is_marked_helpful,
                    'reaction_type': submission.reaction_type,
                    'response_time_seconds': submission.response_time_seconds,
                    'subject': submission.subject,
                    'difficulty': submission.difficulty
                })

        assignment_info = {
            'id': assignment.id,
            'title': assignment.title,
            'subject': assignment.subject,
            'difficulty': assignment.difficulty,
            'due_date': assignment.due_date.isoformat() if assignment.due_date else None,
            'status': assignment.status,
            'is_shared_for_help': assignment.is_shared_for_help
        }

        return success_response("Helpers loaded successfully", data={
            'assignment': assignment_info,
            'helpers': helpers_data,
            'total_helpers': len(helpers_data)
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error fetching helpers: {str(e)}'
        }), 500


@homework_bp.route("/activity/feed", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_activity_feed(current_user):
    """Get recent homework activities from connections."""
    try:
        from models import ActivityFeed

        connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == 'accepted'
        ).all()

        connection_ids = []
        for conn in connections:
            if conn.requester_id == current_user.id:
                connection_ids.append(conn.receiver_id)
            else:
                connection_ids.append(conn.requester_id)

        cutoff_time = datetime.utcnow() - timedelta(hours=2)

        activities = ActivityFeed.query.filter(
            ActivityFeed.user_id.in_(connection_ids),
            ActivityFeed.created_at >= cutoff_time,
            ActivityFeed.expires_at > datetime.utcnow()
        ).order_by(ActivityFeed.created_at.desc()).limit(50).all()

        actor_ids = {a.user_id for a in activities}
        actors_map = {u.id: u for u in User.query.filter(User.id.in_(actor_ids)).all()} if actor_ids else {}
        # N+1 fix (Document 4 §4): one batch call instead of one
        # get_user_online_status() query per activity row (up to 50/request).
        online_status_map = get_online_status_batch(list(actor_ids))

        now = datetime.utcnow()

        feed_items = []
        for activity in activities:
            user = actors_map.get(activity.user_id)
            if not user:
                continue

            online_status = online_status_map.get(activity.user_id, {
                "is_online": False, "in_study_session": False, "last_active": "Unknown"
            })

            diff = now - activity.created_at
            seconds = diff.total_seconds()

            if seconds < 60:
                time_ago = "just now"
            elif seconds < 3600:
                time_ago = f"{int(seconds / 60)}m ago"
            else:
                time_ago = f"{int(seconds / 3600)}h ago"

            feed_items.append({
                'id': activity.id,
                'type': activity.activity_type,
                'user': {
                    'id': user.id,
                    'name': user.name,
                    'avatar': user.avatar,
                    'is_online': online_status.get('is_online', False)
                },
                'data': activity.activity_data,
                'created_at': activity.created_at.isoformat(),
                'time_ago': time_ago
            })

        return jsonify({
            "status": "success",
            "data": {
                "activities": feed_items,
                "total": len(feed_items)
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get activity feed error: {str(e)}")
        return error_response("Failed to load activity feed")


@homework_bp.route("/homework/my-streak", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_my_streak(current_user):
    """Get current user's help streak information."""
    try:
        today = datetime.utcnow().date()
        last_updated = current_user.help_streak_last_updated.date() if current_user.help_streak_last_updated else None

        streak_at_risk = False
        if last_updated:
            days_since = (today - last_updated).days
            if days_since >= 1:
                streak_at_risk = True

        return jsonify({
            "status": "success",
            "data": {
                "current_streak": current_user.help_streak_current,
                "longest_streak": current_user.help_streak_longest,
                "last_updated": current_user.help_streak_last_updated.isoformat() if current_user.help_streak_last_updated else None,
                "streak_at_risk": streak_at_risk,
                "helped_today": last_updated == today if last_updated else False
            }
        })
    except Exception as e:
        current_app.logger.error(f"Get streak error: {str(e)}")
        return error_response("Failed to load streak")


@homework_bp.route("/homework/champions", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_current_champions(current_user):
    """Get this week's champions."""
    try:
        from models import WeeklyChampion

        today = datetime.utcnow().date()
        week_start = today - timedelta(days=today.weekday())

        champions = WeeklyChampion.query.filter(
            WeeklyChampion.week_start == week_start
        ).all()

        champion_user_ids = {c.user_id for c in champions}
        champion_users_map = (
            {u.id: u for u in User.query.filter(User.id.in_(champion_user_ids)).all()}
            if champion_user_ids else {}
        )

        champions_data = {
            'subject_champions': [],
            'most_helpful': None,
            'fastest_helper': None
        }

        for champion in champions:
            user = champion_users_map.get(champion.user_id)
            if not user:
                continue

            champion_info = {
                'user': {
                    'id': user.id,
                    'name': user.name,
                    'avatar': user.avatar,
                    'username': user.username
                },
                'subject': champion.subject,
                'help_count': champion.help_count,
                'is_you': user.id == current_user.id
            }

            if champion.champion_type == 'subject_champion':
                champions_data['subject_champions'].append(champion_info)
            elif champion.champion_type == 'most_helpful_overall':
                champions_data['most_helpful'] = champion_info
            elif champion.champion_type == 'fastest_helper':
                champions_data['fastest_helper'] = champion_info

        champions_data['subject_champions'].sort(key=lambda x: x['help_count'], reverse=True)

        week_end = week_start + timedelta(days=6)
        helps_this_week = HomeworkSubmission.query.filter(
            HomeworkSubmission.helper_id == current_user.id,
            HomeworkSubmission.status == 'completed',
            HomeworkSubmission.is_marked_helpful == True,
            func.date(HomeworkSubmission.feedback_at) >= week_start,
            func.date(HomeworkSubmission.feedback_at) <= week_end
        ).all()

        subject_counts = {}
        for help_item in helps_this_week:
            subject = help_item.subject or 'General'
            subject_counts[subject] = subject_counts.get(subject, 0) + 1

        your_progress = {
            'total_helps': len(helps_this_week),
            'by_subject': subject_counts
        }

        return jsonify({
            "status": "success",
            "data": {
                "champions": champions_data,
                "week_start": week_start.isoformat(),
                "week_end": (week_start + timedelta(days=6)).isoformat(),
                "your_progress": your_progress
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get champions error: {str(e)}")
        return error_response("Failed to load champions")


@homework_bp.route("/assignments", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_my_assignments(current_user):
    """
    Get all assignments for current user with smart sorting.
    Supports cursor-based (infinite scroll) pagination.

    Query params:
    - status: active, completed, all (default: active)
    - subject: Filter by subject
    - sort: priority, due_date, created_at (default: priority)
    - limit: page size, default 15, max 50
    - cursor: id of the last assignment already loaded by the client
    """
    try:
        query = Assignment.query.filter_by(user_id=current_user.id)

        status_filter = request.args.get("status", "active")
        if status_filter == "active":
            query = query.filter(Assignment.status.in_(["not_started", "in_progress"]))
        elif status_filter != "all":
            query = query.filter_by(status=status_filter)

        subject = request.args.get("subject")
        if subject:
            query = query.filter_by(subject=subject)

        assignments = query.all()

        # H-5: priority is recalculated for sorting/display only — the pure
        # service function never mutates the ORM attribute, so viewing the
        # list never writes to the database (see services/homework_service.py).
        now = datetime.utcnow()
        priority_scores = {
            a.id: homework_service.calculate_priority_score(a, now=now) for a in assignments
        }

        sort_by = request.args.get("sort", "priority")
        if sort_by == "priority":
            assignments.sort(key=lambda x: priority_scores[x.id], reverse=True)
        elif sort_by == "due_date":
            assignments.sort(key=lambda x: x.due_date)
        else:
            assignments.sort(key=lambda x: x.created_at, reverse=True)

        limit, cursor = _parse_pagination_params()
        page, has_more, next_cursor = _slice_by_cursor(assignments, cursor, limit)

        assignments_data = []
        for assignment in page:
            hours_until_due = (assignment.due_date - now).total_seconds() / 3600

            assignments_data.append({
                "id": assignment.id,
                "title": assignment.title,
                "subject": assignment.subject,
                "description": assignment.description,
                "due_date": assignment.due_date.isoformat(),
                "difficulty": assignment.difficulty,
                "resources": assignment.resources or [],
                "status": assignment.status,
                "priority_score": priority_scores[assignment.id],
                "estimated_hours": assignment.estimated_hours,
                "time_spent_minutes": assignment.time_spent_minutes,
                "hours_until_due": round(hours_until_due, 1),
                "is_overdue": hours_until_due < 0,
                "urgency_level": _get_urgency_level(hours_until_due),
                "created_at": assignment.created_at.isoformat(),
                "completed_at": assignment.completed_at.isoformat() if assignment.completed_at else None,
                "is_shared": assignment.is_shared_for_help,
                "help_requests_count": HomeworkSubmission.query.filter_by(
                    assignment_id=assignment.id
                ).count() if assignment.is_shared_for_help else 0
            })

        suggestions = _get_smart_suggestions(assignments, current_user) if not cursor else []

        return jsonify({
            "status": "success",
            "data": {
                "assignments": assignments_data,
                "total": len(assignments),
                "next_cursor": next_cursor,
                "has_more": has_more,
                "suggestions": suggestions,
                "stats": {
                    "not_started": len([a for a in assignments if a.status == "not_started"]),
                    "in_progress": len([a for a in assignments if a.status == "in_progress"]),
                    "completed": len([a for a in assignments if a.status == "completed"]),
                    "overdue": len([a for a in assignments if (a.due_date - now).total_seconds() < 0 and a.status != "completed"]),
                    "shared_for_help": len([a for a in assignments if a.is_shared_for_help])
                }
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get assignments error: {str(e)}")
        return error_response("Failed to load assignments")


@homework_bp.route("/assignments", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def create_assignment(current_user):
    """
    Create new personal assignment.

    Body: {
        "title": "Calculus Problem Set 5",
        "subject": "Calculus",
        "description": "Problems 1-20 from chapter 5",
        "due_date": "2024-12-20T08:00:00",
        "difficulty": "hard",
        "estimated_hours": 3,
        "share_for_help": false
    }
    """
    try:
        data = request.get_json()

        title = data.get("title", "").strip()
        if not title:
            return error_response("Title is required")

        due_date_str = data.get("due_date")
        if not due_date_str:
            return error_response("Due date is required")

        try:
            due_date = datetime.fromisoformat(due_date_str.replace('Z', '+00:00'))
        except ValueError:
            return error_response("Invalid due date format (use ISO 8601)")

        if due_date < datetime.utcnow():
            return error_response("Due date must be in the future")

        resources = data.get('resources', [])
        if resources and not isinstance(resources, list):
            return error_response("Resources must be an array", 400)
        for resource in resources:
            if not isinstance(resource, dict):
                return error_response("Each resource must be an object", 400)
            if not resource.get('url'):
                return error_response("Each resource must have a url", 400)
            if not resource.get('type'):
                return error_response("Each resource must have a type", 400)

        assignment = Assignment(
            user_id=current_user.id,
            title=title,
            subject=data.get("subject", "").strip(),
            description=data.get("description", "").strip(),
            due_date=due_date,
            difficulty=data.get("difficulty", "medium"),
            resources=resources,
            estimated_hours=data.get("estimated_hours"),
            status="not_started",
            is_shared_for_help=data.get("share_for_help", False)
        )

        # Mutation path: persist the computed priority score.
        assignment.priority_score = homework_service.calculate_priority_score(assignment)

        db.session.add(assignment)
        db.session.commit()

        return success_response(
            "Assignment created successfully! 📚",
            data={
                "id": assignment.id,
                "title": assignment.title,
                "priority_score": assignment.priority_score,
                "is_shared": assignment.is_shared_for_help
            }
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create assignment error: {str(e)}")
        return error_response("Failed to create assignment")


@homework_bp.route("/assignments/<int:assignment_id>", methods=["PUT"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def update_assignment(current_user, assignment_id):
    """Update assignment details. Body: any assignment fields to update."""
    try:
        assignment = Assignment.query.get(assignment_id)

        if not assignment:
            return error_response("Assignment not found", 404)

        if assignment.user_id != current_user.id:
            return error_response("Not authorized", 403)

        data = request.get_json()

        if "title" in data:
            assignment.title = data["title"].strip()
        if "subject" in data:
            assignment.subject = data["subject"].strip()
        if "description" in data:
            assignment.description = data["description"].strip()
        if "due_date" in data:
            assignment.due_date = datetime.fromisoformat(data["due_date"].replace('Z', '+00:00'))
        if "difficulty" in data:
            assignment.difficulty = data["difficulty"]
        if "estimated_hours" in data:
            assignment.estimated_hours = data["estimated_hours"]
        if 'resources' in data:
            if not isinstance(data['resources'], list):
                return error_response("Resources must be an array", 400)
            assignment.resources = data['resources']
        if "status" in data:
            old_status = assignment.status
            assignment.status = data["status"]

            if assignment.status == "completed" and old_status != "completed":
                assignment.completed_at = datetime.utcnow()
            elif assignment.status != "completed":
                assignment.completed_at = None

        # Mutation path: persist the recomputed priority score.
        assignment.priority_score = homework_service.calculate_priority_score(assignment)

        db.session.commit()

        return success_response(
            "Assignment updated",
            data={
                "id": assignment.id,
                "status": assignment.status,
                "priority_score": assignment.priority_score
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update assignment error: {str(e)}")
        return error_response("Failed to update assignment")


@homework_bp.route("/assignments/<int:assignment_id>", methods=["DELETE"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def delete_assignment(current_user, assignment_id):
    """Delete assignment (and all associated help requests)."""
    try:
        assignment = Assignment.query.get(assignment_id)

        if not assignment:
            return error_response("Assignment not found", 404)

        if assignment.user_id != current_user.id:
            return error_response("Not authorized", 403)

        HomeworkSubmission.query.filter_by(assignment_id=assignment_id).delete()

        db.session.delete(assignment)
        db.session.commit()

        return success_response("Assignment deleted")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete assignment error: {str(e)}")
        return error_response("Failed to delete assignment")


@homework_bp.route("/assignments/<int:assignment_id>/quick-actions", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def assignment_quick_actions(current_user, assignment_id):
    """
    Quick actions for assignments.
    Body: {"action": "mark_complete" | "start_working" | "share_for_help" | "unshare"}
    """
    try:
        assignment = Assignment.query.get(assignment_id)

        if not assignment:
            return error_response("Assignment not found", 404)

        if assignment.user_id != current_user.id:
            return error_response("Not authorized", 403)

        data = request.get_json()
        action = data.get("action")

        if action == "mark_complete":
            assignment.status = "completed"
            assignment.completed_at = datetime.utcnow()
            message = "Assignment marked as complete! 🎉"

        elif action == "start_working":
            assignment.status = "in_progress"
            message = "Good luck! 💪"

        elif action == "share_for_help":
            if not assignment.is_shared_for_help:
                assignment.is_shared_for_help = True
                message = "Assignment shared! Your connections can now help you 🤝"
            else:
                return error_response("Assignment is already shared")

        elif action == "unshare":
            if assignment.is_shared_for_help:
                assignment.is_shared_for_help = False
                message = "Assignment unshared"
            else:
                return error_response("Assignment is not shared")
        else:
            return error_response("Invalid action")

        # Mutation path: persist the recomputed priority score.
        assignment.priority_score = homework_service.calculate_priority_score(assignment)
        db.session.commit()

        return success_response(message, data={"status": assignment.status})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Quick action error: {str(e)}")
        return error_response("Failed to perform action")


# ============================================================================
# HOMEWORK HELP SYSTEM (Shared with Connections)
# ============================================================================

@homework_bp.route("/homework/feed", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_homework_feed(current_user):
    """
    Get all available homework from connections that need help.
    Supports cursor-based (infinite scroll) pagination.

    Query params:
    - subject: Filter by subject
    - difficulty: Filter by difficulty
    - sort: urgency, recent, difficulty (default: urgency)
    - limit: page size, default 15, max 50
    - cursor: id of the last homework item already loaded by the client
    """
    try:
        connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()

        connection_user_ids = []
        for conn in connections:
            if conn.requester_id == current_user.id:
                connection_user_ids.append(conn.receiver_id)
            else:
                connection_user_ids.append(conn.requester_id)

        if not connection_user_ids:
            return jsonify({
                "status": "success",
                "data": {
                    "homework": [],
                    "total": 0,
                    "next_cursor": None,
                    "has_more": False,
                    "message": "Connect with other students to see their homework requests"
                }
            })

        query = Assignment.query.filter(
            Assignment.user_id.in_(connection_user_ids),
            Assignment.is_shared_for_help == True,
            Assignment.status.in_(["not_started", "in_progress"])
        )

        subject = request.args.get("subject")
        if subject:
            query = query.filter_by(subject=subject)

        difficulty = request.args.get("difficulty")
        if difficulty:
            query = query.filter_by(difficulty=difficulty)

        homework_items = query.all()

        now = datetime.utcnow()
        priority_scores = {
            hw.id: homework_service.calculate_priority_score(hw, now=now) for hw in homework_items
        }

        sort_by = request.args.get("sort", "urgency")
        if sort_by == "urgency":
            homework_items.sort(key=lambda x: priority_scores[x.id], reverse=True)
        elif sort_by == "recent":
            homework_items.sort(key=lambda x: x.created_at, reverse=True)
        elif sort_by == "difficulty":
            difficulty_order = {"easy": 1, "medium": 2, "hard": 3}
            homework_items.sort(key=lambda x: difficulty_order.get(x.difficulty, 2))

        limit, cursor = _parse_pagination_params()
        page, has_more, next_cursor = _slice_by_cursor(homework_items, cursor, limit)

        student_ids = {hw.user_id for hw in page}
        students_map = {u.id: u for u in User.query.filter(User.id.in_(student_ids)).all()} if student_ids else {}
        # N+1 fix (Document 4 §4): one batch call for online status instead of
        # one get_user_online_status() query per row in the loop below.
        online_status_map = get_online_status_batch(list(student_ids))

        homework_ids_on_page = [hw.id for hw in page]
        existing_help_map = {
            eh.assignment_id: eh
            for eh in HomeworkSubmission.query.filter(
                HomeworkSubmission.assignment_id.in_(homework_ids_on_page),
                HomeworkSubmission.helper_id == current_user.id,
            ).all()
        } if homework_ids_on_page else {}

        help_count_rows = (
            db.session.query(
                HomeworkSubmission.assignment_id, func.count(HomeworkSubmission.id)
            )
            .filter(HomeworkSubmission.assignment_id.in_(homework_ids_on_page))
            .group_by(HomeworkSubmission.assignment_id)
            .all()
        ) if homework_ids_on_page else []
        help_count_map = dict(help_count_rows)

        homework_data = []
        for hw in page:
            student = students_map.get(hw.user_id)
            hours_until_due = (hw.due_date - now).total_seconds() / 3600

            existing_help = existing_help_map.get(hw.id)
            active_details = online_status_map.get(student.id) if student else None

            homework_data.append({
                "id": hw.id,
                "title": hw.title,
                "subject": hw.subject,
                "description": hw.description,
                "difficulty": hw.difficulty,
                "due_date": hw.due_date.isoformat(),
                "estimated_hours": hw.estimated_hours,
                "hours_until_due": round(hours_until_due, 1),
                "is_overdue": hours_until_due < 0,
                "urgency_level": _get_urgency_level(hours_until_due),
                "priority_score": priority_scores[hw.id],
                "student": {
                    "id": student.id,
                    "username": student.username,
                    "name": student.name,
                    'active_details': active_details,
                    "avatar": student.avatar,
                    "department": student.student_profile.department if student.student_profile else None
                } if student else None,
                "help_count": help_count_map.get(hw.id, 0),
                "already_helping": existing_help is not None,
                "my_help_status": existing_help.status if existing_help else None,
                'my_submission_id': existing_help.id if existing_help else None,
                "created_at": hw.created_at.isoformat()
            })

        available_subjects = list(set([hw.subject for hw in homework_items if hw.subject]))

        return jsonify({
            "status": "success",
            "data": {
                "homework": homework_data,
                "total": len(homework_items),
                "next_cursor": next_cursor,
                "has_more": has_more,
                "available_subjects": sorted(available_subjects),
                "filters_applied": {
                    "subject": subject,
                    "difficulty": difficulty
                }
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get homework feed error: {str(e)}")
        return error_response("Failed to load homework feed")


@homework_bp.route("/homework/<int:assignment_id>/offer-help", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def offer_homework_help(current_user, assignment_id):
    """
    Offer to help with an assignment. Creates a HomeworkSubmission record.
    Body: {"message": "Hey! I can help you with this"}  (optional)
    """
    try:
        assignment = Assignment.query.get(assignment_id)

        if not assignment:
            return error_response("Assignment not found", 404)

        if assignment.user_id == current_user.id:
            return error_response("Cannot help with your own assignment")

        if not assignment.is_shared_for_help:
            return error_response("This assignment is not shared for help", 403)

        connection = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == assignment.user_id),
                and_(Connection.requester_id == assignment.user_id, Connection.receiver_id == current_user.id)
            ),
            Connection.status == "accepted"
        ).first()

        if not connection:
            return error_response("Must be connected to help", 403)

        existing = HomeworkSubmission.query.filter_by(
            assignment_id=assignment_id,
            helper_id=current_user.id
        ).first()

        if existing:
            return error_response("You're already helping with this assignment", 400)

        data = request.get_json() or {}

        submission = HomeworkSubmission(
            requester_id=assignment.user_id,
            helper_id=current_user.id,
            assignment_id=assignment_id,
            title=assignment.title,
            description=assignment.description,
            subject=assignment.subject,
            difficulty=assignment.difficulty,
            status="pending"
        )

        db.session.add(submission)
        db.session.flush()  # populate submission.id for the notification

        student = User.query.get(assignment.user_id)
        notification_service.notify_homework_help_offer(
            assignment.user_id, current_user.name, assignment.title, submission.id
        )

        db.session.commit()

        return success_response(
            f"You're now helping {student.name if student else 'this student'}!",
            data={
                "submission_id": submission.id,
                "assignment": {
                    "id": assignment.id,
                    "title": assignment.title,
                    "subject": assignment.subject
                }
            }
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Offer help error: {str(e)}")
        return error_response("Failed to offer help")


@homework_bp.route("/homework/my-help-requests", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_my_help_requests(current_user):
    """
    Get all help requests for my assignments (people who offered to help me).
    Query params: status: pending, submitted, reviewed, completed, all
    """
    try:
        query = HomeworkSubmission.query.filter_by(requester_id=current_user.id)

        status_filter = request.args.get("status", "all")
        if status_filter != "all":
            query = query.filter_by(status=status_filter)

        help_requests = query.order_by(HomeworkSubmission.created_at.desc()).all()

        helper_ids = {r.helper_id for r in help_requests}
        assignment_ids = {r.assignment_id for r in help_requests if r.assignment_id}

        # N+1 fix (Document 4 §4): joinedload(User.student_profile) so the
        # `helper.student_profile.department` access in the loop below
        # doesn't trigger a lazy-load query per helper.
        helpers_map = (
            {u.id: u for u in User.query.options(joinedload(User.student_profile))
             .filter(User.id.in_(helper_ids)).all()}
            if helper_ids else {}
        )
        assignments_map = (
            {a.id: a for a in Assignment.query.filter(Assignment.id.in_(assignment_ids)).all()}
            if assignment_ids else {}
        )
        # N+1 fix (Document 4 §4): batch instead of per-row get_user_online_status().
        online_status_map = get_online_status_batch(list(helper_ids))

        requests_data = []
        for req in help_requests:
            helper = helpers_map.get(req.helper_id)
            assignment = assignments_map.get(req.assignment_id) if req.assignment_id else None
            active_details = online_status_map.get(req.helper_id)

            requests_data.append({
                "id": req.id,
                "assignment_id": req.assignment_id,
                "title": req.title,
                "subject": req.subject,
                "difficulty": req.difficulty,
                "status": req.status,
                "helper": {
                    "id": helper.id,
                    "username": helper.username,
                    "name": helper.name,
                    'active_details': active_details,
                    "avatar": helper.avatar,
                    "department": helper.student_profile.department if helper.student_profile else None
                } if helper else None,
                "solution_submitted": req.submitted_at is not None,
                "feedback_received": req.feedback_at is not None,
                "created_at": req.created_at.isoformat(),
                "submitted_at": req.submitted_at.isoformat() if req.submitted_at else None,
                "feedback_at": req.feedback_at.isoformat() if req.feedback_at else None,
                "assignment_due_date": assignment.due_date.isoformat() if assignment else None
            })

        return jsonify({
            "status": "success",
            "data": {
                "help_requests": requests_data,
                "total": len(requests_data),
                "stats": {
                    "pending": len([r for r in help_requests if r.status == "pending"]),
                    "submitted": len([r for r in help_requests if r.status == "submitted"]),
                    "reviewed": len([r for r in help_requests if r.status == "reviewed"]),
                    "completed": len([r for r in help_requests if r.status == "completed"])
                }
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get help requests error: {str(e)}")
        return error_response("Failed to load help requests")


@homework_bp.route("/homework/helping", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_homework_im_helping_with(current_user):
    """
    Get all homework I'm currently helping with (I'm the helper).
    Query params: status: pending, submitted, reviewed, completed, all
    """
    try:
        query = HomeworkSubmission.query.filter_by(helper_id=current_user.id)

        status_filter = request.args.get("status", "all")
        if status_filter != "all":
            query = query.filter_by(status=status_filter)

        helping_with = query.order_by(HomeworkSubmission.created_at.desc()).all()

        student_ids = {h.requester_id for h in helping_with}
        assignment_ids = {h.assignment_id for h in helping_with if h.assignment_id}

        # N+1 fix (Document 4 §4): joinedload(User.student_profile) so the
        # `student.student_profile.department` access in the loop below
        # doesn't trigger a lazy-load query per student.
        students_map = (
            {u.id: u for u in User.query.options(joinedload(User.student_profile))
             .filter(User.id.in_(student_ids)).all()}
            if student_ids else {}
        )
        assignments_map = (
            {a.id: a for a in Assignment.query.filter(Assignment.id.in_(assignment_ids)).all()}
            if assignment_ids else {}
        )
        # N+1 fix (Document 4 §4): batch instead of per-row get_user_online_status().
        online_status_map = get_online_status_batch(list(student_ids))

        helping_data = []
        for hw in helping_with:
            student = students_map.get(hw.requester_id)
            active_details = online_status_map.get(hw.requester_id)
            assignment = assignments_map.get(hw.assignment_id) if hw.assignment_id else None

            helping_data.append({
                "id": hw.id,
                "assignment_id": hw.assignment_id,
                "title": hw.title,
                "subject": hw.subject,
                "difficulty": hw.difficulty,
                "description": hw.description,
                "status": hw.status,
                "student": {
                    "id": student.id,
                    "username": student.username,
                    'active_details': active_details,
                    "name": student.name,
                    "avatar": student.avatar,
                    "department": student.student_profile.department if student and student.student_profile else None,
                } if student else None,
                "solution_submitted": hw.submitted_at is not None,
                "feedback_given": hw.feedback_at is not None,
                "created_at": hw.created_at.isoformat(),
                "submitted_at": hw.submitted_at.isoformat() if hw.submitted_at else None,
                "assignment_due_date": assignment.due_date.isoformat() if assignment else None,
                "hours_until_due": round((assignment.due_date - datetime.utcnow()).total_seconds() / 3600, 1) if assignment else None
            })

        return jsonify({
            "status": "success",
            "data": {
                "helping_with": helping_data,
                "total": len(helping_data),
                "stats": {
                    "pending": len([h for h in helping_with if h.status == "pending"]),
                    "submitted": len([h for h in helping_with if h.status == "submitted"]),
                    "reviewed": len([h for h in helping_with if h.status == "reviewed"]),
                    "completed": len([h for h in helping_with if h.status == "completed"])
                }
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get helping with error: {str(e)}")
        return error_response("Failed to load homework you're helping with")


@homework_bp.route("/homework/submission/<int:submission_id>", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def get_submission_details(current_user, submission_id):
    """Get detailed information about a specific homework submission (requester or helper)."""
    try:
        submission = HomeworkSubmission.query.get(submission_id)

        if not submission:
            return error_response("Submission not found", 404)

        if submission.requester_id != current_user.id and submission.helper_id != current_user.id:
            return error_response("Not authorized", 403)

        requester = User.query.get(submission.requester_id)
        helper = User.query.get(submission.helper_id)
        active_details = get_user_online_status(submission.helper_id)
        assignment = Assignment.query.get(submission.assignment_id) if submission.assignment_id else None

        return jsonify({
            "status": "success",
            "data": {
                "id": submission.id,
                "title": submission.title,
                "subject": submission.subject,
                "description": submission.description,
                "difficulty": submission.difficulty,
                "status": submission.status,
                "requester": {
                    "id": requester.id,
                    "username": requester.username,
                    "name": requester.name,
                    "avatar": requester.avatar,
                    "department": requester.student_profile.department if requester.student_profile else None
                } if requester else None,
                "helper": {
                    "id": helper.id,
                    "username": helper.username,
                    "name": helper.name,
                    "avatar": helper.avatar,
                    'active_details': active_details,
                    "department": helper.student_profile.department if helper.student_profile else None
                } if helper else None,
                "solution": {
                    "text": submission.solution_text,
                    "resources": submission.solution_resources or [],
                    "submitted_at": submission.submitted_at.isoformat() if submission.submitted_at else None
                },
                "feedback": {
                    "text": submission.feedback_text,
                    "resources": submission.feedback_resources or [],
                    "given_at": submission.feedback_at.isoformat() if submission.feedback_at else None
                },
                "assignment": {
                    "id": assignment.id,
                    "due_date": assignment.due_date.isoformat(),
                    "status": assignment.status
                } if assignment else None,
                "created_at": submission.created_at.isoformat(),
                "i_am_requester": submission.requester_id == current_user.id,
                "i_am_helper": submission.helper_id == current_user.id
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get submission details error: {str(e)}")
        return error_response("Failed to load submission details")


@homework_bp.route("/homework/submission/<int:submission_id>/submit-solution", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def submit_solution(current_user, submission_id):
    """
    Helper submits their solution.
    Body: {"solution_text": "...", "resources": [...]}
    """
    try:
        submission = HomeworkSubmission.query.get(submission_id)

        if not submission:
            return error_response("Submission not found", 404)

        if submission.helper_id != current_user.id:
            return error_response("Only the helper can submit solution", 403)

        if submission.status not in ["pending", "submitted"]:
            return error_response("Cannot submit solution for this status", 400)

        if not submission.response_time_seconds:
            time_diff = (datetime.utcnow() - submission.created_at).total_seconds()
            submission.response_time_seconds = int(time_diff)
            _create_activity(
                current_user.id,
                'submitted_solution',
                {
                    'assignment_title': submission.title,
                    'subject': submission.subject,
                    'requester_name': current_user.name,
                    'requester_id': submission.requester_id
                }
            )

        data = request.get_json()

        solution_text = data.get("solution_text", "").strip()
        if not solution_text:
            return error_response("Solution text is required")

        submission.solution_text = solution_text
        submission.solution_resources = data.get("resources", [])
        submission.submitted_at = datetime.utcnow()
        submission.status = "submitted"

        notification_service.notify_homework_solution_submitted(
            submission.requester_id, current_user.name, submission.title, submission.id
        )

        db.session.commit()

        return success_response(
            "Solution submitted! The student will review it soon.",
            data={"submission_id": submission.id, "status": submission.status}
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Submit solution error: {str(e)}")
        return error_response("Failed to submit solution")


@homework_bp.route("/homework/submission/<int:submission_id>/give-feedback", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def give_feedback(current_user, submission_id):
    """Give feedback on homework solution with quick reactions."""
    try:
        data = request.get_json()
        submission = HomeworkSubmission.query.get(submission_id)

        if not submission:
            return error_response("Submission not found", 404)

        if submission.requester_id != current_user.id:
            return error_response("Not authorized", 403)

        if submission.status != "submitted":
            return error_response("Cannot give feedback - solution not submitted yet")

        submission.feedback_text = data.get("feedback_text", "").strip()
        submission.feedback_resources = data.get("feedback_resources", [])
        submission.feedback_at = datetime.utcnow()

        reaction_type = data.get("reaction_type")
        if reaction_type in ['thanks', 'lifesaver', 'mindblown', 'perfect']:
            submission.reaction_type = reaction_type
            submission.reaction_at = datetime.utcnow()
            submission.is_marked_helpful = True

        rating = data.get("rating")
        if rating is not None:
            try:
                rating = int(rating)
            except (TypeError, ValueError):
                rating = None
        if rating and 1 <= rating <= 5:
            submission.feedback_rating = rating
            if rating >= 3:
                submission.is_marked_helpful = True

        if not submission.is_marked_helpful and data.get("is_helpful", True):
            submission.is_marked_helpful = True

        mark_complete = data.get("mark_complete", True)
        if mark_complete:
            submission.status = "completed"

            if submission.is_marked_helpful:
                _update_help_streak(submission.helper_id)
        else:
            submission.status = "reviewed"

        # AUDIT ENG-8 FIX: single commit now covers the submission's
        # feedback/status fields, _update_help_streak's mutations (which
        # no longer commits on its own — see that function's docstring),
        # and the notification below — previously up to three separate
        # commits for what is logically one action, with no real
        # atomicity between the streak update and the feedback it was
        # triggered by.
        helper = User.query.get(submission.helper_id)
        if helper:
            notification_service.notify_homework_feedback_received(
                helper.id, current_user.name, submission.title
            )

        db.session.commit()

        return success_response("Feedback submitted successfully! 🎉")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Give feedback error: {str(e)}")
        return error_response("Failed to submit feedback")


@homework_bp.route("/homework/submission/<int:submission_id>/cancel", methods=["DELETE"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def cancel_help_request(current_user, submission_id):
    """
    Cancel a help request.
    - Requester can cancel at any time.
    - Helper can cancel if solution not yet submitted.
    """
    try:
        submission = HomeworkSubmission.query.get(submission_id)

        if not submission:
            return error_response("Submission not found", 404)

        if submission.requester_id == current_user.id:
            pass
        elif submission.helper_id == current_user.id:
            if submission.status != "pending":
                return error_response("Cannot cancel after submitting solution", 403)
        else:
            return error_response("Not authorized", 403)

        canceller_is_requester = submission.requester_id == current_user.id
        notify_user_id = submission.helper_id if canceller_is_requester else submission.requester_id

        notification_service.notify_homework_help_cancelled(
            notify_user_id, current_user.name, submission.title,
            canceller_is_requester=canceller_is_requester,
        )

        db.session.delete(submission)
        db.session.commit()

        return success_response("Help request cancelled")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Cancel help request error: {str(e)}")
        return error_response("Failed to cancel help request")


# ============================================================================
# ANALYTICS & STATS
# ============================================================================

@homework_bp.route("/homework/stats", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_homework_stats(current_user):
    """Get comprehensive homework statistics for current user."""
    try:
        my_assignments = Assignment.query.filter_by(user_id=current_user.id).all()
        help_received = HomeworkSubmission.query.filter_by(requester_id=current_user.id).all()
        help_given = HomeworkSubmission.query.filter_by(helper_id=current_user.id).all()

        stats = {
            "my_assignments": {
                "total": len(my_assignments),
                "not_started": len([a for a in my_assignments if a.status == "not_started"]),
                "in_progress": len([a for a in my_assignments if a.status == "in_progress"]),
                "completed": len([a for a in my_assignments if a.status == "completed"]),
                "shared_for_help": len([a for a in my_assignments if a.is_shared_for_help]),
                "overdue": len([a for a in my_assignments if (a.due_date - datetime.utcnow()).total_seconds() < 0 and a.status != "completed"])
            },
            "help_received": {
                "total": len(help_received),
                "pending": len([h for h in help_received if h.status == "pending"]),
                "submitted": len([h for h in help_received if h.status == "submitted"]),
                "reviewed": len([h for h in help_received if h.status == "reviewed"]),
                "completed": len([h for h in help_received if h.status == "completed"])
            },
            "help_given": {
                "total": len(help_given),
                "pending": len([h for h in help_given if h.status == "pending"]),
                "submitted": len([h for h in help_given if h.status == "submitted"]),
                "reviewed": len([h for h in help_given if h.status == "reviewed"]),
                "completed": len([h for h in help_given if h.status == "completed"])
            },
            "subjects": {
                "my_subjects": list(set([a.subject for a in my_assignments if a.subject])),
                "helping_with": list(set([h.subject for h in help_given if h.subject]))
            }
        }

        return jsonify({"status": "success", "data": stats})

    except Exception as e:
        current_app.logger.error(f"Get homework stats error: {str(e)}")
        return error_response("Failed to load statistics")


@homework_bp.route("/homework/stats/charts", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_homework_chart_data(current_user):
    """
    Get chart data for the homework stats dashboard.
    Returns: daily_activity, subject_completion, reactions_received, response_time.
    """
    try:
        now = datetime.utcnow()
        seven_days_ago = now - timedelta(days=7)

        day_labels = []
        daily_map = {}
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).date()
            label = day.strftime("%a")
            day_labels.append((day, label))
            daily_map[day] = {"day": label, "helps_given": 0, "assignments_created": 0}

        recent_helps = HomeworkSubmission.query.filter(
            HomeworkSubmission.helper_id == current_user.id,
            HomeworkSubmission.created_at >= seven_days_ago
        ).all()
        for h in recent_helps:
            day = h.created_at.date()
            if day in daily_map:
                daily_map[day]["helps_given"] += 1

        recent_assignments = Assignment.query.filter(
            Assignment.user_id == current_user.id,
            Assignment.created_at >= seven_days_ago
        ).all()
        for a in recent_assignments:
            day = a.created_at.date()
            if day in daily_map:
                daily_map[day]["assignments_created"] += 1

        daily_activity = [daily_map[day] for day, _ in day_labels]

        all_help_given = HomeworkSubmission.query.filter_by(helper_id=current_user.id).all()

        subject_map = {}
        for h in all_help_given:
            subject = h.subject or "General"
            if subject not in subject_map:
                subject_map[subject] = {"total": 0, "completed": 0}
            subject_map[subject]["total"] += 1
            if h.status == "completed":
                subject_map[subject]["completed"] += 1

        subject_completion = sorted([
            {
                "subject": subj,
                "total": counts["total"],
                "completed": counts["completed"],
                "rate": round((counts["completed"] / counts["total"]) * 100) if counts["total"] > 0 else 0
            }
            for subj, counts in subject_map.items()
        ], key=lambda x: x["total"], reverse=True)[:6]

        REACTION_LABELS = {
            "thanks": "Thanks 🙏",
            "lifesaver": "Lifesaver 🔥",
            "mind_blown": "Mind Blown 🧠",
            "perfect": "Perfect ⭐",
        }

        reactions_query = HomeworkSubmission.query.filter(
            HomeworkSubmission.helper_id == current_user.id,
            HomeworkSubmission.reaction_type.isnot(None)
        ).all()

        reaction_counts = {label: 0 for label in REACTION_LABELS.values()}
        for h in reactions_query:
            label = REACTION_LABELS.get(h.reaction_type)
            if label:
                reaction_counts[label] += 1

        reactions_received = [
            {"reaction": reaction, "count": count}
            for reaction, count in reaction_counts.items()
        ]

        timed_submissions = [
            h for h in all_help_given
            if h.response_time_seconds and h.response_time_seconds > 0
        ]

        def fmt_duration(seconds):
            if seconds < 3600:
                return f"{round(seconds / 60)}m"
            elif seconds < 86400:
                return f"{round(seconds / 3600, 1)}h"
            else:
                return f"{round(seconds / 86400, 1)}d"

        response_time = None
        if timed_submissions:
            avg_seconds = sum(h.response_time_seconds for h in timed_submissions) / len(timed_submissions)
            fastest_seconds = min(h.response_time_seconds for h in timed_submissions)
            response_time = {
                "average": fmt_duration(avg_seconds),
                "fastest": fmt_duration(fastest_seconds),
                "total_timed": len(timed_submissions)
            }

        return jsonify({
            "status": "success",
            "data": {
                "daily_activity": daily_activity,
                "subject_completion": subject_completion,
                "reactions_received": reactions_received,
                "response_time": response_time
            }})
    except Exception as e:
        current_app.logger.error(f"Get chart data error: {str(e)}")
        return error_response("Failed to load chart data")
