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
from sqlalchemy import or_, and_, func, desc, case
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta

from models import (
    User, Assignment, HomeworkSubmission, Connection,
    Notification
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)
from services.online_status_service import get_user_online_status, get_online_status_batch
from sqlalchemy.exc import IntegrityError
from services import homework_lifecycle as lifecycle
from services import homework_service, idempotency_service as idempotency, notification_service
from services.homework_validation import (
    HomeworkValidationError,
    validate_assignment_create,
    validate_assignment_update,
    validate_feedback,
    validate_solution_submit,
    REACTION_LABELS,
    safe_resources_for_output,
)
from services.activity_service import (
    record_live_activity,
    ACTIVITY_FEED_WINDOW_HOURS,
    ACTIVITY_PULSE_WINDOW_MINUTES,
)
# Phase 5b (Document 4 §1): WRITE_HEAVY-tier limiting on assignment/help-
# request create/update/delete/offer-help/submit-solution/give-feedback
# routes.
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key

homework_bp = Blueprint("student_homework", __name__)


def _server_error(log_label: str, public_message: str):
    """Log the active exception WITH its traceback and return a 500.

    Unexpected faults (DB down, constraint errors, bugs) must not look like
    client mistakes (HTTP 400) and must never echo exception text to the
    caller (audit F-22). Call from inside an ``except`` block.
    """
    current_app.logger.exception(log_label)
    return error_response(public_message, 500)


def _notify_after_commit(send, *args, **kwargs) -> None:
    """Send a notification AFTER the primary change has been committed.

    notify() bumps the Redis unread counter and pushes over WebSocket
    immediately, before any commit; sending it inside the main transaction
    could therefore announce something that then rolls back (audit F-24).
    Notifications are best-effort: a failure here is logged and must not
    turn a successful action into an error.
    """
    try:
        send(*args, **kwargs)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Homework notification failed")


def _close_pending_offers(assignment) -> list:
    """Cancel offers where the helper hasn't produced work yet (used when
    the owner unshares or completes the assignment). Returns the cancelled
    submissions so the caller can notify them after the commit (F-11)."""
    return lifecycle.cancel_open_offers(assignment.id)


def _notify_offers_closed(owner_name: str, closed: list) -> None:
    for sub in closed:
        _notify_after_commit(
            notification_service.notify_homework_help_cancelled,
            sub.helper_id, owner_name, sub.title,
            canceller_is_requester=True, submission_id=sub.id,
        )


def _set_assignment_status(assignment, new_status: str) -> bool:
    """Single place that changes an assignment's status.

    Keeps ``completed_at`` consistent (set on the transition into
    "completed", cleared on any move out of it) and returns True only when
    this call is the not-completed -> completed transition, so callers can
    publish completion exactly once. Used by both PUT /assignments/<id> and
    the quick-actions endpoint so the two paths cannot diverge (audit F-02,
    F-11).
    """
    old_status = assignment.status
    assignment.status = new_status
    if new_status == "completed":
        if old_status != "completed":
            assignment.completed_at = datetime.utcnow()
            return True
        return False
    assignment.completed_at = None
    return False


def _publish_completion(user_id: int, assignment) -> None:
    """Announce a completed assignment to the owner's connections - but only
    if the owner shared it. A private assignment's title/subject must never
    reach the activity feed (audit F-02). Call AFTER the commit:
    record_live_activity commits internally."""
    if not assignment.is_shared_for_help:
        return
    record_live_activity(
        user_id,
        'completed_homework',
        {
            'assignment_id': assignment.id,
            'assignment_title': assignment.title,
            'subject': assignment.subject,
        }
    )



# ============================================================================
# PURE / NO-DB HELPERS
# ============================================================================

# Upper bound on shared assignments considered for one feed request.
FEED_MAX_CANDIDATES = 500


def _parse_pagination_params() -> tuple[int, str | None]:
    """Route-layer wrapper: pulls limit/cursor out of request.args, then
    delegates parsing/validation to the service."""
    return homework_service.parse_pagination_params(
        request.args.get("limit"), request.args.get("cursor")
    )


def _slice_by_cursor(items: list, cursor: str | None, limit: int) -> tuple[list, bool, str | None]:
    return homework_service.slice_by_cursor(items, cursor, limit)


# Sort modes -> (key extractor given the request's score map, descending,
# cursor encode, cursor decode). The list is sorted by (key, id) so the
# order is total and the keyset cursor is unambiguous (audit F-20).
def _keyset_sort(items: list, mode: str, scores: dict):
    difficulty_rank = {"easy": 1, "medium": 2, "hard": 3}
    iso = lambda v: v.isoformat()  # noqa: E731
    if mode == "priority":
        spec = (lambda x: scores[x.id], True, repr, float)
    elif mode == "due_date":
        spec = (lambda x: x.due_date, False, iso, datetime.fromisoformat)
    elif mode == "created_at":
        spec = (lambda x: x.created_at, True, iso, datetime.fromisoformat)
    else:  # difficulty
        spec = (lambda x: difficulty_rank.get(x.difficulty, 2), False, str, int)
    key_fn, descending, encode, decode = spec
    items.sort(key=lambda x: (key_fn(x), x.id), reverse=descending)
    return key_fn, descending, encode, decode


def _slice_keyset(items, spec, cursor, limit):
    key_fn, descending, encode, decode = spec
    return homework_service.slice_by_keyset(
        items, key_fn, cursor, limit, descending=descending, encode=encode, decode=decode
    )


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
    # SELECT ... FOR UPDATE: two completions for the same helper cannot both
    # read the same last-updated day and double-increment (audit F-12).
    # Transaction-scoped, so safe under PgBouncer transaction pooling; a
    # no-op on SQLite.
    user = (
        User.query.filter_by(id=helper_user_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not user:
        return None

    # total_helps_given counts every newly completed helpful help. It used to
    # sit behind the once-per-day streak check, so it counted days (F-06).
    user.total_helps_given = (user.total_helps_given or 0) + 1

    today = datetime.utcnow().date()
    last_updated = user.help_streak_last_updated.date() if user.help_streak_last_updated else None
    current = user.help_streak_current or 0
    longest = user.help_streak_longest or 0

    if last_updated == today:
        return {
            'current_streak': current,
            'longest_streak': longest,
            'is_new_record': False
        }

    if last_updated == today - timedelta(days=1):
        current += 1
    else:
        current = 1
    user.help_streak_current = current

    is_new_record = False
    if current > longest:
        user.help_streak_longest = longest = current
        is_new_record = True

    user.help_streak_last_updated = datetime.utcnow()

    return {
        'current_streak': user.help_streak_current,
        'longest_streak': user.help_streak_longest,
        'is_new_record': is_new_record
    }


# ============================================================================
# PERSONAL ASSIGNMENTS (Private To-Do List)
# ============================================================================

@homework_bp.route("/homework/<int:assignment_id>/helpers", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
def get_assignment_helpers(current_user, assignment_id):
    """Get all helpers for a specific assignment."""
    try:
        assignment = Assignment.query.get(assignment_id)
        if not assignment:
            return error_response("Assignment not found", 404)

        if assignment.user_id != current_user.id:
            return error_response("Unauthorized. You can only view helpers for your own assignments.", 403)

        submissions = HomeworkSubmission.query.filter(
            HomeworkSubmission.assignment_id == assignment_id,
            HomeworkSubmission.requester_id == current_user.id,
            lifecycle.not_cancelled(),
        ).order_by(
            case(
                (HomeworkSubmission.status == 'completed', 1),
                (HomeworkSubmission.status == 'reviewed', 2),
                (HomeworkSubmission.status == 'submitted', 3),
                (HomeworkSubmission.status == 'pending', 4),
                else_=5
            ),
            HomeworkSubmission.created_at.desc()
        ).all()

        # Existing helpers stay visible after the owner unshares; only an
        # assignment that was never shared (and has no helpers) is refused.
        if not assignment.is_shared_for_help and not submissions:
            return error_response("This assignment is not shared for help.", 400)

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

    except Exception:
        return _server_error("Get assignment helpers error", "Failed to load helpers")


@homework_bp.route("/activity/feed", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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

        cutoff_time = datetime.utcnow() - timedelta(hours=ACTIVITY_FEED_WINDOW_HOURS)

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
        return _server_error("Get activity feed error", "Failed to load activity feed")


@homework_bp.route("/activity/pulse", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
def get_activity_pulse(current_user):
    """Get recent anonymized global activity (not scoped to connections).

    Sibling of get_activity_feed above, kept in this file alongside it
    rather than split into a separate module — same imports
    (ActivityFeed, User), same pagination/response-envelope shape, and
    splitting them would mean also moving the existing route to avoid
    two files both claiming /activity/*, which is a larger change than
    this endpoint calls for.

    Unlike get_activity_feed, this is NOT connection-filtered — it's the
    initial-page-load equivalent of the activity_pulse_update WebSocket
    broadcast (services/activity_service.py::_broadcast_to_global_pulse):
    every row created with scope="global", run through the same
    anonymize_for_global used for the live push, so a client sees an
    identical shape whether a row arrived via this GET or over the
    socket. current_user is required (via token_required) purely to gate
    the endpoint to authenticated users, same as every other route in
    this file — the response itself carries no information scoped to
    current_user specifically.
    """
    try:
        from models import ActivityFeed
        from services.activity_service import anonymize_for_global

        cutoff_time = datetime.utcnow() - timedelta(minutes=ACTIVITY_PULSE_WINDOW_MINUTES)

        activities = ActivityFeed.query.filter(
            ActivityFeed.created_at >= cutoff_time,
            ActivityFeed.expires_at > datetime.utcnow()
        ).order_by(ActivityFeed.created_at.desc()).limit(30).all()

        actor_ids = {a.user_id for a in activities}
        actors_map = {u.id: u for u in User.query.filter(User.id.in_(actor_ids)).all()} if actor_ids else {}

        now = datetime.utcnow()
        pulse_items = []
        for activity in activities:
            user = actors_map.get(activity.user_id)
            if not user:
                continue

            anonymized = anonymize_for_global(user, activity)

            diff = now - activity.created_at
            seconds = diff.total_seconds()
            if seconds < 60:
                time_ago = "just now"
            elif seconds < 3600:
                time_ago = f"{int(seconds / 60)}m ago"
            else:
                time_ago = f"{int(seconds / 3600)}h ago"

            anonymized['time_ago'] = time_ago
            pulse_items.append(anonymized)

        return jsonify({
            "status": "success",
            "data": {
                "activities": pulse_items,
                "total": len(pulse_items)
            }
        })

    except Exception as e:
        return _server_error("Get activity pulse error", "Failed to load activity pulse")


@homework_bp.route("/homework/my-streak", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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
                "current_streak": homework_service.effective_help_streak(
                    current_user.help_streak_current, current_user.help_streak_last_updated
                ),
                "longest_streak": current_user.help_streak_longest,
                "last_updated": current_user.help_streak_last_updated.isoformat() if current_user.help_streak_last_updated else None,
                "streak_at_risk": streak_at_risk,
                "helped_today": last_updated == today if last_updated else False
            }
        })
    except Exception as e:
        return _server_error("Get streak error", "Failed to load streak")


@homework_bp.route("/homework/champions", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
def get_current_champions(current_user):
    """Champions for the most recent finished week (computed by the weekly
    job, services/champion_service.py) plus the caller's live progress for
    the current week."""
    try:
        from models import WeeklyChampion

        today = datetime.utcnow().date()
        current_week_start = today - timedelta(days=today.weekday())

        champion_week_start = db.session.query(db.func.max(WeeklyChampion.week_start)).scalar()
        champions = WeeklyChampion.query.filter(
            WeeklyChampion.week_start == champion_week_start
        ).all() if champion_week_start else []
        week_start = champion_week_start or current_week_start

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
                'subject': champion.subject or None,
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

        progress_end = current_week_start + timedelta(days=6)
        helps_this_week = HomeworkSubmission.query.filter(
            HomeworkSubmission.helper_id == current_user.id,
            HomeworkSubmission.status == 'completed',
            HomeworkSubmission.is_marked_helpful == True,
            func.date(HomeworkSubmission.feedback_at) >= current_week_start,
            func.date(HomeworkSubmission.feedback_at) <= progress_end
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
                # the finished week the champions belong to...
                "week_start": week_start.isoformat(),
                "week_end": (week_start + timedelta(days=6)).isoformat(),
                # ...and the current week the progress block counts
                "progress_week_start": current_week_start.isoformat(),
                "progress_week_end": progress_end.isoformat(),
                "your_progress": your_progress
            }
        })

    except Exception as e:
        return _server_error("Get champions error", "Failed to load champions")


@homework_bp.route("/assignments", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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
        if sort_by not in ("priority", "due_date", "created_at"):
            sort_by = "created_at"
        spec = _keyset_sort(assignments, sort_by, priority_scores)

        limit, cursor = _parse_pagination_params()
        page, has_more, next_cursor = _slice_keyset(assignments, spec, cursor, limit)

        # One grouped query for the whole page instead of one COUNT per
        # shared assignment (audit F-16).
        shared_ids = [a.id for a in page if a.is_shared_for_help]
        help_counts = dict(
            db.session.query(HomeworkSubmission.assignment_id, db.func.count(HomeworkSubmission.id))
            .filter(HomeworkSubmission.assignment_id.in_(shared_ids), lifecycle.not_cancelled())
            .group_by(HomeworkSubmission.assignment_id).all()
        ) if shared_ids else {}

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
                "help_requests_count": help_counts.get(assignment.id, 0)
            })

        suggestions = _get_smart_suggestions(assignments, current_user) if not cursor else []

        # Dashboard counters describe ALL of the user's assignments, not the
        # currently filtered list (audit F-23: with the default 'active'
        # filter, stats.completed was always 0). One grouped query, no rows
        # loaded.
        stat_rows = db.session.query(
            Assignment.status,
            db.func.count(Assignment.id),
            db.func.sum(case((Assignment.due_date < now, 1), else_=0)),
            db.func.sum(case((Assignment.is_shared_for_help.is_(True), 1), else_=0)),
        ).filter(Assignment.user_id == current_user.id).group_by(Assignment.status).all()
        by_status = {row[0]: row for row in stat_rows}

        def _count(status):
            return int(by_status[status][1]) if status in by_status else 0

        stats = {
            "not_started": _count("not_started"),
            "in_progress": _count("in_progress"),
            "completed": _count("completed"),
            "overdue": sum(int(r[2] or 0) for st, r in by_status.items() if st != "completed"),
            "shared_for_help": sum(int(r[3] or 0) for st, r in by_status.items() if st != "completed"),
        }

        return jsonify({
            "status": "success",
            "data": {
                "assignments": assignments_data,
                # size of the filtered list (what pagination walks)
                "total": len(assignments),
                "next_cursor": next_cursor,
                "has_more": has_more,
                "suggestions": suggestions,
                "stats": stats,
            }
        })

    except Exception as e:
        return _server_error("Get assignments error", "Failed to load assignments")


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
        "due_date": "2024-12-20T08:00:00Z",   (ISO 8601 instant; bare values are read as UTC)
        "difficulty": "easy|medium|hard",
        "estimated_hours": 3,
        "share_for_help": false
    }
    """
    body = request.get_json(silent=True)
    client_key = idempotency.normalize_key(body.get("idempotency_key")) if isinstance(body, dict) else None
    claimed = False
    try:
        try:
            fields = validate_assignment_create(body)
        except HomeworkValidationError as e:
            return error_response(e.message, 400)

        if client_key:
            state, stored = idempotency.begin("hw-create", current_user.id, client_key)
            if state == idempotency.IN_PROGRESS:
                return error_response("This assignment is already being created", 409)
            if state == idempotency.DONE:
                return success_response(
                    "Assignment created successfully! 📚", data=stored
                ), 200
            claimed = True

        assignment = Assignment(
            user_id=current_user.id,
            status="not_started",
            **fields,
        )

        # Mutation path: persist the computed priority score.
        assignment.priority_score = homework_service.calculate_priority_score(assignment)

        db.session.add(assignment)
        db.session.commit()

        response_data = {
            "id": assignment.id,
            "title": assignment.title,
            "priority_score": assignment.priority_score,
            "is_shared": assignment.is_shared_for_help,
        }
        if claimed:
            idempotency.finish("hw-create", current_user.id, client_key, response_data)
            claimed = False

        # Activity feed: only when shared for help — an assignment kept
        # private has nothing to tell a connection about, and firing here
        # unconditionally would mean every personal, unshared assignment
        # also generates a feed row.
        if assignment.is_shared_for_help:
            record_live_activity(
                current_user.id,
                'started_homework',
                {
                    'assignment_id': assignment.id,
                    'assignment_title': assignment.title,
                    'subject': assignment.subject,
                }
            )

        return success_response("Assignment created successfully! 📚", data=response_data), 201

    except Exception as e:
        db.session.rollback()
        return _server_error("Create assignment error", "Failed to create assignment")
    finally:
        if claimed:
            idempotency.abandon("hw-create", current_user.id, client_key)


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

        try:
            fields = validate_assignment_update(request.get_json(silent=True))
        except HomeworkValidationError as e:
            return error_response(e.message, 400)

        for column in ("title", "subject", "description", "due_date",
                       "difficulty", "estimated_hours", "resources"):
            if column in fields:
                setattr(assignment, column, fields[column])
        just_completed = False
        if "status" in fields:
            just_completed = _set_assignment_status(assignment, fields["status"])

        # Mutation path: persist the recomputed priority score.
        assignment.priority_score = homework_service.calculate_priority_score(assignment)

        db.session.commit()

        # Only on the actual not-completed -> completed transition, and only
        # for shared assignments (see _publish_completion).
        if just_completed:
            closed = _close_pending_offers(assignment)
            db.session.commit()
            _notify_offers_closed(current_user.name, closed)
            _publish_completion(current_user.id, assignment)

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
        return _server_error("Update assignment error", "Failed to update assignment")


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

        # Keep help history (audit F-03): offers still in flight are
        # cancelled (soft) and their helpers told; every submission is
        # detached from the assignment (assignment_id -> NULL) rather than
        # deleted, so completed help and feedback survive.
        closed = lifecycle.cancel_open_offers(
            assignment_id, statuses=(lifecycle.PENDING, lifecycle.ACCEPTED, lifecycle.SUBMITTED)
        )
        lifecycle.detach_from_assignment(assignment_id)

        db.session.delete(assignment)
        db.session.commit()

        _notify_offers_closed(current_user.name, closed)

        return success_response("Assignment deleted")

    except Exception as e:
        db.session.rollback()
        return _server_error("Delete assignment error", "Failed to delete assignment")


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

        data = request.get_json(silent=True)
        action = data.get("action") if isinstance(data, dict) else None

        just_completed = False
        closed = []
        if action == "mark_complete":
            just_completed = _set_assignment_status(assignment, "completed")
            message = "Assignment marked as complete! 🎉"

        elif action == "start_working":
            _set_assignment_status(assignment, "in_progress")
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
                # Offers with no work yet can't continue on a private
                # assignment: cancel them and tell the helpers (F-11).
                closed = _close_pending_offers(assignment)
                message = "Assignment unshared"
            else:
                return error_response("Assignment is not shared")
        else:
            return error_response("Invalid action")

        # Mutation path: persist the recomputed priority score.
        assignment.priority_score = homework_service.calculate_priority_score(assignment)
        db.session.commit()

        if just_completed:
            closed = _close_pending_offers(assignment)
            db.session.commit()

        _notify_offers_closed(current_user.name, closed)

        if just_completed:
            _publish_completion(current_user.id, assignment)

        return success_response(message, data={"status": assignment.status})

    except Exception as e:
        db.session.rollback()
        return _server_error("Quick action error", "Failed to perform action")


# ============================================================================
# HOMEWORK HELP SYSTEM (Shared with Connections)
# ============================================================================

@homework_bp.route("/homework/feed", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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

        # The subject picker must list every subject available, so it is
        # computed BEFORE the subject/difficulty filters narrow the set
        # (audit F-23: selecting "Math" used to collapse it to ['Math']).
        available_subjects = sorted(
            row[0] for row in
            query.with_entities(Assignment.subject)
            .filter(Assignment.subject.isnot(None), Assignment.subject != "")
            .distinct().all()
        )

        subject = request.args.get("subject")
        if subject:
            query = query.filter_by(subject=subject)

        difficulty = request.args.get("difficulty")
        if difficulty:
            query = query.filter_by(difficulty=difficulty)

        # Priority/difficulty ordering is computed in Python (see
        # homework_service.calculate_priority_score), so the candidate set is
        # capped rather than unbounded: soonest-due first, which is also what
        # the "urgency" sort surfaces (audit F-16).
        homework_items = query.order_by(Assignment.due_date.asc(), Assignment.id.asc()).limit(FEED_MAX_CANDIDATES).all()

        now = datetime.utcnow()
        priority_scores = {
            hw.id: homework_service.calculate_priority_score(hw, now=now) for hw in homework_items
        }

        sort_by = request.args.get("sort", "urgency")
        feed_mode = {"urgency": "priority", "recent": "created_at", "difficulty": "difficulty"}.get(
            sort_by, "priority"
        )
        spec = _keyset_sort(homework_items, feed_mode, priority_scores)

        limit, cursor = _parse_pagination_params()
        page, has_more, next_cursor = _slice_keyset(homework_items, spec, cursor, limit)

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
                lifecycle.not_cancelled(),
            ).all()
        } if homework_ids_on_page else {}

        help_count_rows = (
            db.session.query(
                HomeworkSubmission.assignment_id, func.count(HomeworkSubmission.id)
            )
            .filter(
                HomeworkSubmission.assignment_id.in_(homework_ids_on_page),
                lifecycle.not_cancelled(),
            )
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
                # Problem sheets etc. (audit F-14). The feed is already
                # limited to the owner's connections (Q-10: all connections
                # who can see the card).
                "resources": safe_resources_for_output(hw.resources),
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

        return jsonify({
            "status": "success",
            "data": {
                "homework": homework_data,
                "total": len(homework_items),
                "next_cursor": next_cursor,
                "has_more": has_more,
                "available_subjects": available_subjects,
                "filters_applied": {
                    "subject": subject,
                    "difficulty": difficulty
                }
            }
        })

    except Exception as e:
        return _server_error("Get homework feed error", "Failed to load homework feed")


@homework_bp.route("/homework/<int:assignment_id>/offer-help", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def offer_homework_help(current_user, assignment_id):
    """
    Offer to help with an assignment. Creates a HomeworkSubmission record.
    No body is needed. Idempotent per (assignment, helper): a second live
    offer is refused, enforced by a partial unique index (audit F-05).
    """
    try:
        assignment = Assignment.query.get(assignment_id)

        if not assignment:
            return error_response("Assignment not found", 404)

        if assignment.user_id == current_user.id:
            return error_response("Cannot help with your own assignment")

        if not assignment.is_shared_for_help:
            return error_response("This assignment is not shared for help", 403)

        if assignment.status == "completed":
            return error_response("This assignment is already completed", 400)

        connection = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == assignment.user_id),
                and_(Connection.requester_id == assignment.user_id, Connection.receiver_id == current_user.id)
            ),
            Connection.status == "accepted"
        ).first()

        if not connection:
            return error_response("Must be connected to help", 403)

        existing = HomeworkSubmission.query.filter(
            HomeworkSubmission.assignment_id == assignment_id,
            HomeworkSubmission.helper_id == current_user.id,
            lifecycle.not_cancelled(),
        ).first()

        if existing:
            return error_response("You're already helping with this assignment", 400)

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
        try:
            db.session.flush()  # populate submission.id; trips the unique index on a race
        except IntegrityError:
            db.session.rollback()
            return error_response("You're already helping with this assignment", 400)

        student = User.query.get(assignment.user_id)
        db.session.commit()

        _notify_after_commit(
            notification_service.notify_homework_help_offer,
            assignment.user_id, current_user.name, assignment.title, submission.id,
        )

        record_live_activity(
            current_user.id,
            'offered_help',
            # Audience is the HELPER's connections, who may not know the
            # requester: publish subject-level information only, never the
            # requester's identity or the assignment title (audit F-08).
            {'subject': assignment.subject}
        )

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
        return _server_error("Offer help error", "Failed to offer help")


@homework_bp.route("/homework/my-help-requests", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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
        else:
            query = query.filter(lifecycle.not_cancelled())

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
        return _server_error("Get help requests error", "Failed to load help requests")


@homework_bp.route("/homework/helping", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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
        else:
            query = query.filter(lifecycle.not_cancelled())

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
                "assignment_resources": safe_resources_for_output(assignment.resources) if assignment else [],
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
        return _server_error("Get helping with error", "Failed to load homework you're helping with")


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
                    "status": assignment.status,
                    "estimated_hours": assignment.estimated_hours,
                    "resources": safe_resources_for_output(assignment.resources),
                } if assignment else None,
                "created_at": submission.created_at.isoformat(),
                "i_am_requester": submission.requester_id == current_user.id,
                "i_am_helper": submission.helper_id == current_user.id
            }
        })

    except Exception as e:
        return _server_error("Get submission details error", "Failed to load submission details")


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

        previous_status = lifecycle._value(submission.status)
        if not lifecycle.can_transition(previous_status, lifecycle.SUBMITTED):
            return error_response("Cannot submit solution for this status", 400)

        if lifecycle.participants_blocked(submission.requester_id, submission.helper_id):
            return error_response("You can no longer work on this request", 403)

        # Validate BEFORE touching the session: nothing below may run (or
        # commit, e.g. via record_live_activity) for a request we reject.
        try:
            fields = validate_solution_submit(request.get_json(silent=True))
        except HomeworkValidationError as e:
            return error_response(e.message, 400)

        now = datetime.utcnow()
        first_response = submission.response_time_seconds is None

        values = {
            "solution_text": fields["solution_text"],
            "solution_resources": fields["resources"],
            "submitted_at": now,
            "status": lifecycle.SUBMITTED,
        }
        if first_response:
            values["response_time_seconds"] = int((now - submission.created_at).total_seconds())

        # Conditional on the status we just read: a concurrent cancel (or a
        # parallel first submission) makes this a no-op instead of
        # resurrecting/duplicating the submission (audit F-12).
        if not lifecycle.claim_submission(submission.id, {previous_status}, **values):
            db.session.rollback()
            return error_response("This request changed while you were submitting. Refresh and try again.", 409)

        db.session.commit()

        _notify_after_commit(
            notification_service.notify_homework_solution_submitted,
            submission.requester_id, current_user.name, submission.title, submission.id,
        )

        # Activity feed: only after the commit, and only for the first real
        # submission (record_live_activity commits internally).
        if first_response:
            record_live_activity(
                current_user.id,
                'submitted_solution',
                # Subject-level only: the helper's connections must not
                # learn who asked or what the assignment is (audit F-08).
                {'subject': submission.subject}
            )

        return success_response(
            "Solution submitted! The student will review it soon.",
            data={"submission_id": submission.id, "status": submission.status}
        )

    except Exception as e:
        db.session.rollback()
        return _server_error("Submit solution error", "Failed to submit solution")


@homework_bp.route("/homework/submission/<int:submission_id>/give-feedback", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def give_feedback(current_user, submission_id):
    """Give feedback on homework solution with quick reactions."""
    try:
        submission = HomeworkSubmission.query.get(submission_id)

        if not submission:
            return error_response("Submission not found", 404)

        if submission.requester_id != current_user.id:
            return error_response("Not authorized", 403)

        previous_status = lifecycle._value(submission.status)
        if previous_status not in (lifecycle.SUBMITTED, lifecycle.REVIEWED):
            return error_response("Cannot give feedback - solution not submitted yet")

        if lifecycle.participants_blocked(submission.requester_id, submission.helper_id):
            return error_response("You can no longer work on this request", 403)

        try:
            fields = validate_feedback(request.get_json(silent=True))
        except HomeworkValidationError as e:
            return error_response(e.message, 400)

        target_status = lifecycle.COMPLETED if fields["mark_complete"] else lifecycle.REVIEWED
        if not lifecycle.can_transition(previous_status, target_status):
            return error_response(
                "This solution already has feedback. Mark it complete or wait for a revision."
            )

        now = datetime.utcnow()
        values = {
            "feedback_text": fields["feedback_text"],
            "feedback_resources": fields["feedback_resources"],
            "feedback_at": now,
        }

        if fields["reaction_type"]:
            values["reaction_type"] = fields["reaction_type"]
            values["reaction_at"] = now
        if fields["rating"]:
            values["feedback_rating"] = fields["rating"]

        # "Helpful" is never assumed (audit F-06): it needs an explicit
        # positive signal and an explicit negative one always wins.
        is_helpful = homework_service.resolve_is_helpful(
            previously_helpful=bool(submission.is_marked_helpful),
            reaction_type=fields["reaction_type"],
            rating=fields["rating"],
            explicit=fields["is_helpful"],
        )
        values["is_marked_helpful"] = is_helpful
        values["status"] = target_status

        # Exactly one of N concurrent requests wins this conditional UPDATE;
        # the others must not repeat the notification/streak side effects
        # (audit F-12).
        if not lifecycle.claim_submission(submission.id, {previous_status}, **values):
            db.session.rollback()
            return error_response("Feedback was already submitted for this solution.", 409)

        streak_result = None
        if target_status == lifecycle.COMPLETED and is_helpful:
            streak_result = _update_help_streak(submission.helper_id)

        # One commit covers the feedback, the streak update (AUDIT ENG-8)...
        db.session.commit()

        # ...and the notification goes out only once it is durable (F-24).
        helper = User.query.get(submission.helper_id)
        if helper:
            _notify_after_commit(
                notification_service.notify_homework_feedback_received,
                helper.id, current_user.name, submission.title, submission.id,
            )

        # Activity feed: after the commits above, never before -
        # record_live_activity commits internally (see activity_service.py).
        if streak_result and streak_result.get('is_new_record'):
            record_live_activity(
                submission.helper_id,
                'achieved_streak',
                {
                    'current_streak': streak_result['current_streak'],
                    'longest_streak': streak_result['longest_streak'],
                },
                scope='global',
            )

        return success_response("Feedback submitted successfully! 🎉")

    except Exception as e:
        db.session.rollback()
        return _server_error("Give feedback error", "Failed to submit feedback")


@homework_bp.route("/homework/submission/<int:submission_id>/cancel", methods=["DELETE"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def cancel_help_request(current_user, submission_id):
    """
    Cancel a help request (soft cancel - the row is kept as history).
    - Requester can cancel while the help is pending or submitted.
    - Helper can cancel only while pending (no solution submitted yet).
    - Reviewed/completed help is a permanent record and cannot be cancelled
      (audit F-03).
    """
    try:
        submission = HomeworkSubmission.query.get(submission_id)

        if not submission:
            return error_response("Submission not found", 404)

        previous_status = lifecycle._value(submission.status)
        canceller_is_requester = submission.requester_id == current_user.id

        if canceller_is_requester:
            pass
        elif submission.helper_id == current_user.id:
            if previous_status != lifecycle.PENDING:
                return error_response("Cannot cancel after submitting solution", 403)
        else:
            return error_response("Not authorized", 403)

        if not lifecycle.can_transition(previous_status, lifecycle.CANCELLED):
            return error_response(
                "This help has already been completed or cancelled and can't be cancelled.", 409
            )

        if not lifecycle.claim_submission(submission.id, {previous_status}, status=lifecycle.CANCELLED):
            db.session.rollback()
            return error_response("This request changed. Refresh and try again.", 409)

        db.session.commit()

        notify_user_id = submission.helper_id if canceller_is_requester else submission.requester_id
        _notify_after_commit(
            notification_service.notify_homework_help_cancelled,
            notify_user_id, current_user.name, submission.title,
            canceller_is_requester=canceller_is_requester, submission_id=submission.id,
        )

        return success_response("Help request cancelled")

    except Exception as e:
        db.session.rollback()
        return _server_error("Cancel help request error", "Failed to cancel help request")


# ============================================================================
# ANALYTICS & STATS
# ============================================================================

@homework_bp.route("/homework/stats", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
def get_homework_stats(current_user):
    """Get comprehensive homework statistics for current user."""
    try:
        now = datetime.utcnow()

        # One grouped query per table instead of loading every row (F-16).
        status_counts = dict(
            db.session.query(Assignment.status, func.count(Assignment.id))
            .filter(Assignment.user_id == current_user.id)
            .group_by(Assignment.status)
            .all()
        )
        shared_count, overdue_count = db.session.query(
            func.coalesce(func.sum(case((Assignment.is_shared_for_help.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(case(
                ((and_(Assignment.due_date < now, Assignment.status != "completed")), 1),
                else_=0,
            )), 0),
        ).filter(Assignment.user_id == current_user.id).one()

        def _submission_counts(role_column):
            rows = (
                db.session.query(HomeworkSubmission.status, func.count(HomeworkSubmission.id))
                .filter(role_column == current_user.id, lifecycle.not_cancelled())
                .group_by(HomeworkSubmission.status)
                .all()
            )
            by_status = dict(rows)
            return {
                "total": sum(by_status.values()),
                "pending": by_status.get("pending", 0),
                "submitted": by_status.get("submitted", 0),
                "reviewed": by_status.get("reviewed", 0),
                "completed": by_status.get("completed", 0),
            }

        my_subjects = [
            row[0] for row in db.session.query(Assignment.subject)
            .filter(Assignment.user_id == current_user.id, Assignment.subject.isnot(None), Assignment.subject != "")
            .distinct().all()
        ]
        helping_subjects = [
            row[0] for row in db.session.query(HomeworkSubmission.subject)
            .filter(
                HomeworkSubmission.helper_id == current_user.id, lifecycle.not_cancelled(),
                HomeworkSubmission.subject.isnot(None), HomeworkSubmission.subject != "",
            )
            .distinct().all()
        ]

        stats = {
            "my_assignments": {
                "total": sum(status_counts.values()),
                "not_started": status_counts.get("not_started", 0),
                "in_progress": status_counts.get("in_progress", 0),
                "completed": status_counts.get("completed", 0),
                "shared_for_help": int(shared_count),
                "overdue": int(overdue_count),
            },
            "help_received": _submission_counts(HomeworkSubmission.requester_id),
            "help_given": _submission_counts(HomeworkSubmission.helper_id),
            "subjects": {
                "my_subjects": my_subjects,
                "helping_with": helping_subjects,
            },
        }

        return jsonify({"status": "success", "data": stats})

    except Exception as e:
        return _server_error("Get homework stats error", "Failed to load statistics")


@homework_bp.route("/homework/stats/charts", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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

        # Per-day counts via GROUP BY on the date, only for the 7-day window.
        helps_by_day = dict(
            db.session.query(func.date(HomeworkSubmission.created_at), func.count(HomeworkSubmission.id))
            .filter(
                HomeworkSubmission.helper_id == current_user.id,
                HomeworkSubmission.created_at >= seven_days_ago,
            )
            .group_by(func.date(HomeworkSubmission.created_at)).all()
        )
        created_by_day = dict(
            db.session.query(func.date(Assignment.created_at), func.count(Assignment.id))
            .filter(
                Assignment.user_id == current_user.id,
                Assignment.created_at >= seven_days_ago,
            )
            .group_by(func.date(Assignment.created_at)).all()
        )
        for day in daily_map:
            key = day.isoformat()
            daily_map[day]["helps_given"] = int(helps_by_day.get(key, helps_by_day.get(day, 0)))
            daily_map[day]["assignments_created"] = int(created_by_day.get(key, created_by_day.get(day, 0)))

        daily_activity = [daily_map[day] for day, _ in day_labels]

        helper_scope = (HomeworkSubmission.helper_id == current_user.id, lifecycle.not_cancelled())

        subject_rows = (
            db.session.query(
                func.coalesce(HomeworkSubmission.subject, "General"),
                func.count(HomeworkSubmission.id),
                func.coalesce(func.sum(case((HomeworkSubmission.status == "completed", 1), else_=0)), 0),
            )
            .filter(*helper_scope)
            .group_by(func.coalesce(HomeworkSubmission.subject, "General"))
            .all()
        )
        subject_completion = sorted([
            {
                "subject": subj,
                "total": int(total),
                "completed": int(completed),
                "rate": round((int(completed) / int(total)) * 100) if total else 0,
            }
            for subj, total, completed in subject_rows
        ], key=lambda x: x["total"], reverse=True)[:6]

        reaction_rows = (
            db.session.query(HomeworkSubmission.reaction_type, func.count(HomeworkSubmission.id))
            .filter(
                HomeworkSubmission.helper_id == current_user.id,
                HomeworkSubmission.reaction_type.isnot(None),
            )
            .group_by(HomeworkSubmission.reaction_type).all()
        )
        reaction_counts = {label: 0 for label in REACTION_LABELS.values()}
        for reaction_type, count in reaction_rows:
            # tolerate the legacy 'mind_blown' key written before F-18
            label = REACTION_LABELS.get("mindblown" if reaction_type == "mind_blown" else reaction_type)
            if label:
                reaction_counts[label] += int(count)

        reactions_received = [
            {"reaction": reaction, "count": count}
            for reaction, count in reaction_counts.items()
        ]

        total_timed, avg_seconds, fastest_seconds = (
            db.session.query(
                func.count(HomeworkSubmission.id),
                func.avg(HomeworkSubmission.response_time_seconds),
                func.min(HomeworkSubmission.response_time_seconds),
            )
            .filter(*helper_scope, HomeworkSubmission.response_time_seconds > 0)
            .one()
        )

        def fmt_duration(seconds):
            if seconds < 3600:
                return f"{round(seconds / 60)}m"
            elif seconds < 86400:
                return f"{round(seconds / 3600, 1)}h"
            else:
                return f"{round(seconds / 86400, 1)}d"

        response_time = None
        if total_timed:
            response_time = {
                "average": fmt_duration(float(avg_seconds)),
                "fastest": fmt_duration(fastest_seconds),
                "total_timed": int(total_timed),
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
        return _server_error("Get chart data error", "Failed to load chart data")
