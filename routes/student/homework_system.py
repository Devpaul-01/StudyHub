"""
StudyHub - Comprehensive Homework & Assignment System
Combines personal assignments with collaborative homework help

Features:
- Personal assignment tracking (todo list style)
- Share assignments to get help from connections
- Browse available homework from all connections
- Complete workflow: request → solution → feedback → completion
- Smart prioritization and suggestions
- Cursor-based pagination for "My Work" and "Connections" feeds
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import or_, and_, func, desc
from datetime import datetime, timedelta

from models import (
    User, Assignment, HomeworkSubmission, Connection, 
    Notification, LiveStudySession
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)
from utils import get_user_online_status

homework_bp = Blueprint("student_homework", __name__)

# Default / max page size for cursor-paginated homework endpoints
DEFAULT_PAGE_SIZE = 15
MAX_PAGE_SIZE = 50


def _parse_pagination_params():
    """
    Parse `limit` and `cursor` query params shared by paginated homework endpoints.

    `cursor` is simply the id of the last item the client has already loaded.
    Because the full (already-sorted) list is available server-side for the
    current request, we can find that id's position and resume from there —
    this behaves like keyset pagination without needing a stored offset.
    """
    try:
        limit = int(request.args.get("limit", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE_SIZE
    limit = max(1, min(limit, MAX_PAGE_SIZE))

    cursor = request.args.get("cursor")
    return limit, cursor


def _slice_by_cursor(items, cursor, limit):
    """
    Given a fully sorted list of ORM objects (each with an `.id`), return:
      (page, has_more, next_cursor)

    H-4 (partial fix): finding the cursor's position used to be an O(n)
    linear scan over `items` on every single page request (including page
    1, which doesn't even need it). It's now an O(1) dict lookup built once
    per call.

    This does NOT solve the deeper architectural issue H-4 also flags: for
    `sort=priority`, the "full matching set" still has to be loaded and
    sorted in Python before pagination can happen at all, because priority
    is a time-dependent value (due-date urgency decays continuously) that
    isn't persisted per-request (see the H-5 fix in get_my_assignments/
    get_homework_feed — we deliberately stopped writing it to the database
    on every read). True DB-level keyset pagination for priority order would
    require either persisting a periodically-refreshed snapshot column and
    accepting slightly-stale ordering, or moving priority computation into
    SQL itself — both are larger, behavior-changing architecture decisions
    that are intentionally out of scope for this fix. The `due_date` and
    `created_at` sort modes, by contrast, sort on plain persisted columns
    and would be straightforward to migrate to real SQL-level keyset
    pagination (`WHERE id > :cursor ORDER BY <col>, id LIMIT :limit`) in a
    follow-up — flagged here rather than done silently.
    """
    start_index = 0
    if cursor:
        try:
            cursor_id = int(cursor)
            id_to_index = {item.id: i for i, item in enumerate(items)}
            if cursor_id in id_to_index:
                start_index = id_to_index[cursor_id] + 1
        except (TypeError, ValueError):
            start_index = 0

    page = items[start_index:start_index + limit]
    has_more = (start_index + limit) < len(items)
    next_cursor = str(page[-1].id) if page and has_more else None

    return page, has_more, next_cursor


def _update_help_streak(helper_user_id):
    """
    Update help streak when user provides helpful assistance
    Called when HomeworkSubmission gets positive feedback
    """
    from models import User
    from extensions import db
    
    user = User.query.get(helper_user_id)
    if not user:
        return None
    
    today = datetime.utcnow().date()
    last_updated = user.help_streak_last_updated.date() if user.help_streak_last_updated else None
    
    # Check if already counted today
    if last_updated == today:
        return {
            'current_streak': user.help_streak_current,
            'longest_streak': user.help_streak_longest,
            'is_new_record': False
        }
    
    # Check if yesterday (streak continues)
    yesterday = today - timedelta(days=1)
    
    if last_updated == yesterday:
        # Continue streak
        user.help_streak_current += 1
    elif last_updated is None or (today - last_updated).days > 1:
        # Reset streak (missed a day)
        user.help_streak_current = 1
    
    # Update longest streak
    is_new_record = False
    if user.help_streak_current > user.help_streak_longest:
        user.help_streak_longest = user.help_streak_current
        is_new_record = True
    
    user.help_streak_last_updated = datetime.utcnow()
    user.total_helps_given += 1
    
    db.session.commit()
    
    return {
        'current_streak': user.help_streak_current,
        'longest_streak': user.help_streak_longest,
        'is_new_record': is_new_record
    }


def _create_activity(user_id, activity_type, data):
    """
    Create activity feed entry
    Auto-expires after 24 hours
    """
    from models import ActivityFeed
    from extensions import db
    from services.websocket_events import ws_manager
    
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
        
        # Broadcast via WebSocket
        ws_manager.broadcast_activity(activity)
        
        return activity
    except Exception as e:
        current_app.logger.error(f"Create activity error: {e}")
        db.session.rollback()
        return None

# ============================================================================
# PERSONAL ASSIGNMENTS (Private To-Do List)
# ============================================================================

@homework_bp.route("/homework/<int:assignment_id>/helpers", methods=["GET"])
@token_required
def get_assignment_helpers(current_user,assignment_id):
    """
    Get all helpers for a specific assignment
    
    GET /api/homework/assignments/<assignment_id>/helpers
    
    Returns:
        {
            "success": true,
            "assignment": {
                "id": 123,
                "title": "Math Problem Set",
                "subject": "Math"
            },
            "helpers": [
                {
                    "id": 456,  # submission_id
                    "helper": {
                        "id": 2,
                        "name": "John Doe",
                        "username": "johndoe",
                        "avatar": "/static/images/user2.png"
                    },
                    "status": "completed",
                    "created_at": "2024-02-16T10:30:00",
                    "submitted_at": "2024-02-16T14:30:00",
                    "has_solution": true,
                    "has_feedback": true,
                    "is_marked_helpful": true,
                    "reaction_type": "lifesaver"
                }
            ],
            "total_helpers": 5
        }
    """
    try:
        # Get the assignment
        assignment = Assignment.query.get_or_404(assignment_id)
        
        # Security check: Only the assignment owner can see their helpers
        if assignment.user_id != current_user.id:
            return jsonify({
                'success': False,
                'error': 'Unauthorized. You can only view helpers for your own assignments.'
            }), 403
        
        # Check if assignment is shared for help
        if not assignment.is_shared_for_help:
            return jsonify({
                'success': False,
                'error': 'This assignment is not shared for help.'
            }), 400
        
        # Get all submissions (helpers) for this assignment
        submissions = HomeworkSubmission.query.filter_by(
            assignment_id=assignment_id,
            requester_id=current_user.id
        ).order_by(
            # Order by: completed first, then by creation date
            db.case(
                (HomeworkSubmission.status == 'completed', 1),
                (HomeworkSubmission.status == 'reviewed', 2),
                (HomeworkSubmission.status == 'submitted', 3),
                (HomeworkSubmission.status == 'pending', 4),
                else_=5
            ),
            HomeworkSubmission.created_at.desc()
        ).all()
        
        # Format helpers data for frontend
        helpers_data = []
        for submission in submissions:
            helper_user = User.query.get(submission.helper_id)
            
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
        
        # Assignment info
        assignment_info = {
            'id': assignment.id,
            'title': assignment.title,
            'subject': assignment.subject,
            'difficulty': assignment.difficulty,
            'due_date': assignment.due_date.isoformat() if assignment.due_date else None,
            'status': assignment.status,
            'is_shared_for_help': assignment.is_shared_for_help
        }
        
        return success_response("Helpers loaded successfully",
            data= {
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
@token_required
def get_activity_feed(current_user):
    """Get recent homework activities from connections"""
    try:
        from models import ActivityFeed, Connection, User
        
        # Get user's connections
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
        
        # Get recent activities (last 2 hours)
        cutoff_time = datetime.utcnow() - timedelta(hours=2)
        
        activities = ActivityFeed.query.filter(
            ActivityFeed.user_id.in_(connection_ids),
            ActivityFeed.created_at >= cutoff_time,
            ActivityFeed.expires_at > datetime.utcnow()
        ).order_by(ActivityFeed.created_at.desc()).limit(50).all()
        
        # Format activities
        feed_items = []
        for activity in activities:
            user = User.query.get(activity.user_id)
            if not user:
                continue
            
            online_status = get_user_online_status(activity.user_id)
            
            # Calculate time ago
            now = datetime.utcnow()
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
@token_required
def get_my_streak(current_user):
    """Get current user's help streak information"""
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
@token_required
def get_current_champions(current_user):
    """Get this week's champions"""
    try:
        from models import WeeklyChampion, User
        
        today = datetime.utcnow().date()
        week_start = today - timedelta(days=today.weekday())
        
        champions = WeeklyChampion.query.filter(
            WeeklyChampion.week_start == week_start
        ).all()
        
        champions_data = {
            'subject_champions': [],
            'most_helpful': None,
            'fastest_helper': None
        }
        
        for champion in champions:
            user = User.query.get(champion.user_id)
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
        
        # Sort subject champions by help count
        champions_data['subject_champions'].sort(key=lambda x: x['help_count'], reverse=True)
        
        # Get user's progress this week
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
              (omit / null for the first page)
    """
    try:
        # Base query - only user's own assignments
        query = Assignment.query.filter_by(user_id=current_user.id)
        
        # Filter by status
        status_filter = request.args.get("status", "active")
        if status_filter == "active":
            query = query.filter(Assignment.status.in_(["not_started", "in_progress"]))
        elif status_filter != "all":
            query = query.filter_by(status=status_filter)
        
        # Filter by subject
        subject = request.args.get("subject")
        if subject:
            query = query.filter_by(subject=subject)
        
        # Get the FULL matching set — needed for accurate stats/suggestions
        # and for deterministic sorting (priority score depends on "now").
        assignments = query.all()
        
        # Recalculate priorities for sorting/display purposes only.
        # H-5 fix: this used to call db.session.commit() here too, meaning
        # simply *viewing* your assignment list wrote to the database on
        # every request (a GET with a side effect, and needless write
        # amplification for what should be the cheapest, most cacheable
        # endpoint in this feature). calculate_priority() still updates the
        # in-memory priority_score used below for sorting/serialization —
        # we just never persist it. Any priority_score in the response is
        # accurate as of "now"; it simply isn't written back to the row.
        for assignment in assignments:
            assignment.calculate_priority()
        
        # Sort
        sort_by = request.args.get("sort", "priority")
        if sort_by == "priority":
            assignments.sort(key=lambda x: x.priority_score, reverse=True)
        elif sort_by == "due_date":
            assignments.sort(key=lambda x: x.due_date)
        else:  # created_at
            assignments.sort(key=lambda x: x.created_at, reverse=True)

        # ---- Cursor pagination over the fully-sorted list ----
        limit, cursor = _parse_pagination_params()
        page, has_more, next_cursor = _slice_by_cursor(assignments, cursor, limit)

        # Format response (current page only)
        assignments_data = []
        for assignment in page:
            hours_until_due = (assignment.due_date - datetime.utcnow()).total_seconds() / 3600
            
            assignments_data.append({
                "id": assignment.id,
                "title": assignment.title,
                "subject": assignment.subject,
                "description": assignment.description,
                "due_date": assignment.due_date.isoformat(),
                "difficulty": assignment.difficulty,
                "resources": assignment.resources or [],  # NEW:
                "status": assignment.status,
                "priority_score": assignment.priority_score,
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
        
        # Smart suggestions only need to be computed (and shown) on the
        # first page — they're based on the full active set regardless.
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
                    "overdue": len([a for a in assignments if (a.due_date - datetime.utcnow()).total_seconds() < 0 and a.status != "completed"]),
                    "shared_for_help": len([a for a in assignments if a.is_shared_for_help])
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get assignments error: {str(e)}")
        return error_response("Failed to load assignments")


@homework_bp.route("/assignments", methods=["POST"])
@token_required
def create_assignment(current_user):
    """
    Create new personal assignment
    
    Body: {
        "title": "Calculus Problem Set 5",
        "subject": "Calculus",
        "description": "Problems 1-20 from chapter 5",
        "due_date": "2024-12-20T08:00:00",
        "difficulty": "hard",
        "estimated_hours": 3,
        "share_for_help": false  // Optional: immediately share for help
    }
    """
    try:
        data = request.get_json()
        
        
        # Validate required fields
        title = data.get("title", "").strip()
        if not title:
            return error_response("Title is required")
        
        due_date_str = data.get("due_date")
        if not due_date_str:
            return error_response("Due date is required")
        
        # Parse due date
        try:
            due_date = datetime.fromisoformat(due_date_str.replace('Z', '+00:00'))
        except ValueError:
            return error_response("Invalid due date format (use ISO 8601)")
        
        # Validate due date is in future
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
    
        
        # Create assignment
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
        
        # Calculate initial priority
        assignment.calculate_priority()
        
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
@token_required
def update_assignment(current_user, assignment_id):
    """
    Update assignment details
    
    Body: Can include any assignment fields to update
    """
    try:
        assignment = Assignment.query.get(assignment_id)
        
        if not assignment:
            return error_response("Assignment not found", 404)
        
        if assignment.user_id != current_user.id:
            return error_response("Not authorized", 403)
        
        data = request.get_json()
        
        # Update fields
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
            
            # Mark as completed if status changed to completed
            if assignment.status == "completed" and old_status != "completed":
                assignment.completed_at = datetime.utcnow()
            elif assignment.status != "completed":
                assignment.completed_at = None
        
        # Recalculate priority
        assignment.calculate_priority()
        
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
@token_required
def delete_assignment(current_user, assignment_id):
    """Delete assignment (and all associated help requests)"""
    try:
        assignment = Assignment.query.get(assignment_id)
        
        if not assignment:
            return error_response("Assignment not found", 404)
        
        if assignment.user_id != current_user.id:
            return error_response("Not authorized", 403)
        
        # Also delete any homework submissions linked to this
        HomeworkSubmission.query.filter_by(assignment_id=assignment_id).delete()
        
        db.session.delete(assignment)
        db.session.commit()
        
        return success_response("Assignment deleted")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete assignment error: {str(e)}")
        return error_response("Failed to delete assignment")


@homework_bp.route("/assignments/<int:assignment_id>/quick-actions", methods=["POST"])
@token_required
def assignment_quick_actions(current_user, assignment_id):
    """
    Quick actions for assignments
    
    Body: {
        "action": "mark_complete" | "start_working" | "share_for_help" | "unshare"
    }
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
        
        assignment.calculate_priority()
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
@token_required
def get_homework_feed(current_user):
    """
    Get all available homework from connections that need help.
    Supports cursor-based (infinite scroll) pagination.

    This is the "Browse assignments from connections" endpoint
    
    Query params:
    - subject: Filter by subject
    - difficulty: Filter by difficulty
    - sort: urgency, recent, difficulty (default: urgency)
    - limit: page size, default 15, max 50
    - cursor: id of the last homework item already loaded by the client
              (omit / null for the first page)
    """
    try:
        # Get all accepted connections
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
        
        # Get all assignments that are shared for help from connections
        query = Assignment.query.filter(
            Assignment.user_id.in_(connection_user_ids),
            Assignment.is_shared_for_help == True,
            Assignment.status.in_(["not_started", "in_progress"])
        )
        
        # Apply filters
        subject = request.args.get("subject")
        if subject:
            query = query.filter_by(subject=subject)
            
        difficulty = request.args.get("difficulty")
        if difficulty:
            query = query.filter_by(difficulty=difficulty)
        
        homework_items = query.all()
        
        # Recalculate priorities for sorting/display purposes only.
        # H-5 fix: see get_my_assignments() above — this is a GET request,
        # so it should not write to the database as a side effect. The
        # freshly computed priority_score is still used for sorting/display
        # below; it just isn't persisted.
        for hw in homework_items:
            hw.calculate_priority()
        
        # Sort
        sort_by = request.args.get("sort", "urgency")
        if sort_by == "urgency":
            homework_items.sort(key=lambda x: x.priority_score, reverse=True)
        elif sort_by == "recent":
            homework_items.sort(key=lambda x: x.created_at, reverse=True)
        elif sort_by == "difficulty":
            difficulty_order = {"easy": 1, "medium": 2, "hard": 3}
            homework_items.sort(key=lambda x: difficulty_order.get(x.difficulty, 2))

        # ---- Cursor pagination over the fully-sorted list ----
        limit, cursor = _parse_pagination_params()
        page, has_more, next_cursor = _slice_by_cursor(homework_items, cursor, limit)
        
        # Format response (current page only)
        homework_data = []
        for hw in page:
            student = User.query.get(hw.user_id)
            hours_until_due = (hw.due_date - datetime.utcnow()).total_seconds() / 3600
            
            # Check if current user already helping
            existing_help = HomeworkSubmission.query.filter_by(
                assignment_id=hw.id,
                helper_id=current_user.id
            ).first()
            active_details = get_user_online_status(student.id)
            
            
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
                "priority_score": hw.priority_score,
                "student": {
                    "id": student.id,
                    "username": student.username,
                    "name": student.name,
                    'active_details': active_details,
                  
                    
                    "avatar": student.avatar,
                    "department": student.student_profile.department
                } if student else None,
                "help_count": HomeworkSubmission.query.filter_by(
                    assignment_id=hw.id
                ).count(),
                "already_helping": existing_help is not None,
                "my_help_status": existing_help.status if existing_help else None,
                'my_submission_id': existing_help.id if existing_help else None,
                "created_at": hw.created_at.isoformat()
            })
        
        # Get subjects for filtering (from the full matching set, not just this page)
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
@token_required
def offer_homework_help(current_user, assignment_id):
    """
    Offer to help with an assignment
    
    This creates a HomeworkSubmission record
    
    Body: {
        "message": "Hey! I can help you with this"  // Optional initial message
    }
    """
    try:
        assignment = Assignment.query.get(assignment_id)
        
        if not assignment:
            return error_response("Assignment not found", 404)
        
        if assignment.user_id == current_user.id:
            return error_response("Cannot help with your own assignment")
        
        if not assignment.is_shared_for_help:
            return error_response("This assignment is not shared for help", 403)
        
        # Check if connected
        connection = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == assignment.user_id),
                and_(Connection.requester_id == assignment.user_id, Connection.receiver_id == current_user.id)
            ),
            Connection.status == "accepted"
        ).first()
        
        if not connection:
            return error_response("Must be connected to help", 403)
        
        # Check if already helping
        existing = HomeworkSubmission.query.filter_by(
            assignment_id=assignment_id,
            helper_id=current_user.id
        ).first()
        
        if existing:
            return error_response("You're already helping with this assignment", 400)
        
        data = request.get_json() or {}
        
        # Create homework submission
        submission = HomeworkSubmission(
            requester_id=assignment.user_id,
            helper_id=current_user.id,
            assignment_id=assignment_id,
            title=assignment.title,
            description=assignment.description,
            subject=assignment.subject,
            difficulty=assignment.difficulty,
            status="pending"  # pending → submitted → reviewed → completed
        )
        
        db.session.add(submission)
        
        # Create notification for assignment owner
        student = User.query.get(assignment.user_id)
        notification = Notification(
            user_id=assignment.user_id,
            title="Someone wants to help! 🎓",
            body=f"{current_user.name} offered to help with '{assignment.title}'",
            notification_type="homework_help_offer",
            related_type="homework_submission",
            related_id=submission.id
        )
        db.session.add(notification)
        
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
@token_required
def get_my_help_requests(current_user):
    """
    Get all help requests for my assignments
    (People who offered to help me)
    
    Query params:
    - status: pending, submitted, reviewed, completed, all
    """
    try:
        # Get help requests where I'm the requester
        query = HomeworkSubmission.query.filter_by(requester_id=current_user.id)
        
        status_filter = request.args.get("status", "all")
        if status_filter != "all":
            query = query.filter_by(status=status_filter)
        
        help_requests = query.order_by(HomeworkSubmission.created_at.desc()).all()
        
        requests_data = []
        for req in help_requests:
            helper = User.query.get(req.helper_id)
            assignment = Assignment.query.get(req.assignment_id) if req.assignment_id else None
            active_details = get_user_online_status(req.helper_id)
            
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
                    "department": helper.student_profile.department
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
@token_required
def get_homework_im_helping_with(current_user):
    """
    Get all homework I'm currently helping with
    (Assignments where I'm the helper)
    
    Query params:
    - status: pending, submitted, reviewed, completed, all
    """
    try:
        # Get submissions where I'm the helper
        query = HomeworkSubmission.query.filter_by(helper_id=current_user.id)
        
        status_filter = request.args.get("status", "all")
        if status_filter != "all":
            query = query.filter_by(status=status_filter)
        
        helping_with = query.order_by(HomeworkSubmission.created_at.desc()).all()
        
        helping_data = []
        for hw in helping_with:
            student = User.query.get(hw.requester_id)
            active_details = get_user_online_status(hw.requester_id)
            assignment = Assignment.query.get(hw.assignment_id) if hw.assignment_id else None
            
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
@token_required
def get_submission_details(current_user, submission_id):
    """
    Get detailed information about a specific homework submission
    (Works for both requester and helper)
    """
    try:
        submission = HomeworkSubmission.query.get(submission_id)
        
        if not submission:
            return error_response("Submission not found", 404)
        
        # Only requester or helper can view
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
                    "department": requester.student_profile.department
                } if requester else None,
                "helper": {
                    "id": helper.id,
                    "username": helper.username,
                    "name": helper.name,
                    "avatar": helper.avatar,
                     'active_details': active_details,
                    "department": helper.student_profile.department
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
@token_required
def submit_solution(current_user, submission_id):
    """
    Helper submits their solution
    
    Body: {
        "solution_text": "Here's how to solve it...",
        "resources": [{"id": "uuid", "name": "solution.pdf", "url": "...", "type": "pdf"}]
    }
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
          time_diff = (datetime.utcnow() -submission.created_at).total_seconds()
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
        
        # Update submission
        submission.solution_text = solution_text
        submission.solution_resources = data.get("resources", [])
        submission.submitted_at = datetime.utcnow()
        submission.status = "submitted"
        
        # Create notification for requester
        requester = User.query.get(submission.requester_id)
        notification = Notification(
            user_id=submission.requester_id,
            title="Solution received! 📝",
            body=f"{current_user.name} submitted a solution for '{submission.title}'",
            notification_type="homework_solution_submitted",
            related_type="homework_submission",
            related_id=submission.id
        )
        db.session.add(notification)
        
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
@token_required
def give_feedback(current_user, submission_id):
    """Give feedback on homework solution with quick reactions"""
    try:
        data = request.get_json()
        submission = HomeworkSubmission.query.get(submission_id)
        
        if not submission:
            return error_response("Submission not found", 404)
        
        if submission.requester_id != current_user.id:
            return error_response("Not authorized", 403)
        
        if submission.status != "submitted":
            return error_response("Cannot give feedback - solution not submitted yet")
        
        # Update feedback
        submission.feedback_text = data.get("feedback_text", "").strip()
        submission.feedback_resources = data.get("feedback_resources", [])
        submission.feedback_at = datetime.utcnow()
        
        # Quick reaction
        reaction_type = data.get("reaction_type")  # 'thanks', 'lifesaver', 'mindblown', 'perfect'
        if reaction_type in ['thanks', 'lifesaver', 'mindblown', 'perfect']:
            submission.reaction_type = reaction_type
            submission.reaction_at = datetime.utcnow()
            submission.is_marked_helpful = True
        
        # Rating (optional) — feedback_rating column added by migration 001
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
        
        # Mark as helpful if no specific reaction but positive feedback
        if not submission.is_marked_helpful and data.get("is_helpful", True):
            submission.is_marked_helpful = True
        
        # Update status
        mark_complete = data.get("mark_complete", True)
        if mark_complete:
            submission.status = "completed"
            
            # Update helper's streak if helpful
            if submission.is_marked_helpful:
                
                streak_info = _update_help_streak(submission.helper_id)

            
        else:
            submission.status = "reviewed"
        
        db.session.commit()
        
        # Create notification
        helper = User.query.get(submission.helper_id)
        if helper:
            notification = Notification(
                user_id=helper.id,
                title="Feedback received! 🎉",
                body=f"{current_user.name} reviewed your solution for '{submission.title}'",
                notification_type="homework_feedback_received"
            )
            db.session.add(notification)
            db.session.commit()
        
        return success_response("Feedback submitted successfully! 🎉")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Give feedback error: {str(e)}")
        return error_response("Failed to submit feedback")


@homework_bp.route("/homework/submission/<int:submission_id>/cancel", methods=["DELETE"])
@token_required
def cancel_help_request(current_user, submission_id):
    """
    Cancel a help request
    - Requester can cancel at any time
    - Helper can cancel if solution not yet submitted
    """
    try:
        submission = HomeworkSubmission.query.get(submission_id)
        
        if not submission:
            return error_response("Submission not found", 404)
        
        # Check authorization
        if submission.requester_id == current_user.id:
            # Requester can always cancel
            pass
        elif submission.helper_id == current_user.id:
            # Helper can only cancel if solution not submitted
            if submission.status != "pending":
                return error_response("Cannot cancel after submitting solution", 403)
        else:
            return error_response("Not authorized", 403)
        
        # Send notification to the other party
        if submission.requester_id == current_user.id:
            notify_user_id = submission.helper_id
            message = f"{current_user.name} cancelled the help request for '{submission.title}'"
        else:
            notify_user_id = submission.requester_id
            message = f"{current_user.name} can no longer help with '{submission.title}'"
        
        notification = Notification(
            user_id=notify_user_id,
            title="Help request cancelled",
            body=message,
            notification_type="homework_help_cancelled"
        )
        db.session.add(notification)
        
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
@token_required
def get_homework_stats(current_user):
    """
    Get comprehensive homework statistics for current user
    """
    try:
        # My assignments stats
        my_assignments = Assignment.query.filter_by(user_id=current_user.id).all()
        
        # Help requests stats (where I'm the requester)
        help_received = HomeworkSubmission.query.filter_by(requester_id=current_user.id).all()
        
        # Help given stats (where I'm the helper)
        help_given = HomeworkSubmission.query.filter_by(helper_id=current_user.id).all()
        
        # Calculate stats
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
        
        return jsonify({
            "status": "success",
            "data": stats
        })
        
    except Exception as e:
        current_app.logger.error(f"Get homework stats error: {str(e)}")
        return error_response("Failed to load statistics")


# ================================================
def _get_urgency_level(hours_until_due):
    """Get urgency level based on hours until due"""
    if hours_until_due < 0:
        return "overdue"
    elif hours_until_due < 24:
        return "urgent"
    elif hours_until_due < 48:
        return "soon"
    elif hours_until_due < 168:
        return "this_week"
    else:
        return "upcoming"


def _get_smart_suggestions(assignments, user):
    """
    Generate smart suggestions based on current assignments
    """
    suggestions = []
    now = datetime.utcnow()
    
    # Active assignments only
    active = [a for a in assignments if a.status in ["not_started", "in_progress"]]
    
    if not active:
        return []
    
    # Suggestion 1: Most urgent hard assignment
    urgent_hard = [a for a in active if a.difficulty == "hard" and (a.due_date - now).total_seconds() / 3600 < 48]
    if urgent_hard:
        suggestions.append({
            "type": "urgent_hard",
            "message": f"⚠️ Start '{urgent_hard[0].title}' soon - it's hard and due in {round((urgent_hard[0].due_date - now).total_seconds() / 3600, 1)} hours",
            "assignment_id": urgent_hard[0].id,
            "action": "start_working"
        })
    
    # Suggestion 2: Consider sharing for help
    hard_not_shared = [a for a in active if a.difficulty == "hard" and not a.is_shared_for_help]
    if hard_not_shared:
        suggestions.append({
            "type": "share_for_help",
            "message": f"💡 '{hard_not_shared[0].title}' looks tough - consider sharing it to get help from connections",
            "assignment_id": hard_not_shared[0].id,
            "action": "share_for_help"
        })
    
    # Suggestion 3: Quick win (easy assignment)
    easy_ones = [a for a in active if a.difficulty == "easy" and a.status == "not_started"]
    if easy_ones:
        suggestions.append({
            "type": "quick_win",
            "message": f"✨ Quick win: '{easy_ones[0].title}' is easy - knock it out!",
            "assignment_id": easy_ones[0].id,
            "action": "start_working"
        })
    
    # Suggestion 4: Overdue
    overdue = [a for a in active if (a.due_date - now).total_seconds() < 0]
    if overdue:
        suggestions.append({
            "type": "overdue",
            "message": f"🚨 '{overdue[0].title}' is overdue - prioritize this!",
            "assignment_id": overdue[0].id,
            "action": "start_working"
        })
    
    return suggestions[:2]  # Return   


@homework_bp.route("/homework/stats/charts", methods=["GET"])
@token_required
def get_homework_chart_data(current_user):
    """
    Get chart data for the homework stats dashboard.
    Returns:
      - daily_activity: helps given + assignments created per day for last 7 days
      - subject_completion: completion rate per subject the user is helping with
      - reactions_received: breakdown of reaction types received on user's submissions
      - response_time: average, fastest response time stats for help given
    """
    try:
        now = datetime.utcnow()
        seven_days_ago = now - timedelta(days=7)

        # ── 1. DAILY ACTIVITY (last 7 days) ──────────────────────────────────
        day_labels = []
        daily_map = {}
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).date()
            label = day.strftime("%a")  # Mon, Tue ...
            day_labels.append((day, label))
            daily_map[day] = {"day": label, "helps_given": 0, "assignments_created": 0}

        # Helps given in last 7 days (submissions created by user as helper)
        recent_helps = HomeworkSubmission.query.filter(
            HomeworkSubmission.helper_id == current_user.id,
            HomeworkSubmission.created_at >= seven_days_ago
        ).all()
        for h in recent_helps:
            day = h.created_at.date()
            if day in daily_map:
                daily_map[day]["helps_given"] += 1

        # Assignments created in last 7 days
        recent_assignments = Assignment.query.filter(
            Assignment.user_id == current_user.id,
            Assignment.created_at >= seven_days_ago
        ).all()
        for a in recent_assignments:
            day = a.created_at.date()
            if day in daily_map:
                daily_map[day]["assignments_created"] += 1

        daily_activity = [daily_map[day] for day, _ in day_labels]

        # ── 2. SUBJECT COMPLETION RATE ────────────────────────────────────────
        # Per subject the user has helped with, what % did they complete
        all_help_given = HomeworkSubmission.query.filter_by(
            helper_id=current_user.id
        ).all()

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

        # ── 3. REACTIONS RECEIVED ─────────────────────────────────────────────
        # Reactions left by requesters on the user's submitted solutions
        REACTION_LABELS = {
            "thanks":      "Thanks 🙏",
            "lifesaver":   "Lifesaver 🔥",
            "mind_blown":  "Mind Blown 🧠",
            "perfect":     "Perfect ⭐",
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

        # ── 4. RESPONSE TIME ──────────────────────────────────────────────────
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
