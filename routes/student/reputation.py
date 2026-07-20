"""
StudyHub - Reputation System
Tracks user reputation through helpful actions and quality contributions
Includes leaderboards, history tracking, and auto-award logic

Reputation Points:
+5  → Post gets 10 likes
+10 → Comment marked as solution
+15 → Post marked helpful (reaction)
+20 → Post gets 50 likes
-2  → Post gets disliked
-10 → Content reported and confirmed

Reputation Levels:
0-50:    🌱 Newbie
51-200:  📚 Learner
201-500: 🎓 Contributor
501-1K:  🌟 Expert
1K+:     👑 Master
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func, desc, and_, or_, case
import datetime

from models import (
    User, StudentProfile, ReputationHistory, Post, Comment, Connection,
    PostReaction, PostReport, Badge, UserBadge
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)
# H-8 fix: REPUTATION_LEVELS/get_reputation_level used to be duplicated here
# (and independently in badges.py, leaderboard.py, and
# models.py::User.update_reputation_level) — now imported from the single
# shared module so all of them stay in sync.
from routes.student.reputation_levels import REPUTATION_LEVELS, get_reputation_level

reputation_bp = Blueprint("student_reputation", __name__)


# ============================================================================
# REPUTATION CONSTANTS
# ============================================================================

REPUTATION_ACTIONS = {
    "post_10_likes": {"points": 5, "description": "Post reached 10 likes"},
    "post_50_likes": {"points": 20, "description": "Post reached 50 likes"},
    "post_100_likes": {"points": 50, "description": "Post reached 100 likes"},
    "comment_marked_solution": {"points": 15, "description": "Comment marked as solution"},
    "comment_marked_helpful": {"points": 3, "description": "Comment marked helpful"},
    "post_marked_helpful": {"points": 5, "description": "Post marked helpful"},
    "post_disliked": {"points": -2, "description": "Post received dislike"},
    "content_reported": {"points": -10, "description": "Content reported"},
    "helpful_streak_7": {"points": 10, "description": "7 helpful reactions in a week"},
    "thread_created": {"points": 3, "description": "Created study thread"},
    "thread_completed": {"points": 10, "description": "Thread reached 10+ members"},
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def next_level(points):
    for idx, level in enumerate(REPUTATION_LEVELS):
        if level["min"] <= points <= level["max"]:
            if idx + 1 < len(REPUTATION_LEVELS):
                return REPUTATION_LEVELS[idx + 1]
    return None


def award_reputation(user_id, action_key, related_type=None, related_id=None, custom_points=None):
    user = User.query.get(user_id)
    if not user:
        return None

    action = REPUTATION_ACTIONS.get(action_key)
    if not action and not custom_points:
        return None

    points_change = custom_points if custom_points else action["points"]
    description = action["description"] if action else f"Custom: {points_change} points"

    reputation_before = user.reputation
    user.reputation += points_change
    user.reputation = max(0, user.reputation)
    user.update_reputation_level()

    history = ReputationHistory(
        user_id=user_id,
        action=action_key if action_key else "custom",
        points_change=points_change,
        related_type=related_type,
        related_id=related_id,
        reputation_before=reputation_before,
        reputation_after=user.reputation
    )
    db.session.add(history)

    old_level = get_reputation_level(reputation_before)
    new_level = get_reputation_level(user.reputation)

    if old_level["name"] != new_level["name"]:
        from models import Notification
        notification = Notification(
            user_id=user_id,
            title=f"Level Up! You're now a {new_level['name']}!",
            body=f"You've reached {user.reputation} reputation points {new_level['icon']}",
            notification_type="reputation_level_up",
            related_type="user",
            related_id=user_id
        )
        db.session.add(notification)

    db.session.commit()
    return history


def check_and_award_milestone(user_id, post_id=None, comment_id=None):
    if post_id:
        post = Post.query.get(post_id)
        if post and post.student_id == user_id:
            if post.positive_reactions_count == 10:
                award_reputation(user_id, "post_10_likes", "post", post_id)
            elif post.positive_reactions_count == 50:
                award_reputation(user_id, "post_50_likes", "post", post_id)
            elif post.positive_reactions_count == 100:
                award_reputation(user_id, "post_100_likes", "post", post_id)


# ============================================================================
# REPUTATION ENDPOINTS
# ============================================================================

@reputation_bp.route("/reputation/rising-stars", methods=["GET"])
@token_required
def get_rising_stars(current_user):
    """Get users with highest reputation gain in last 7 days."""
    try:
        limit = min(request.args.get('limit', 10, type=int), 50)
        week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)

        rising_rows = (
            db.session.query(
                ReputationHistory.user_id,
                func.sum(ReputationHistory.points_change).label("weekly_gain"),
                User.username,
                User.name,
                User.avatar,
                User.reputation,
                User.reputation_level,
                StudentProfile.department,
            )
            .join(User, User.id == ReputationHistory.user_id)
            .outerjoin(StudentProfile, StudentProfile.user_id == ReputationHistory.user_id)
            .filter(
                ReputationHistory.created_at >= week_ago,
                ReputationHistory.points_change > 0,
            )
            .group_by(
                ReputationHistory.user_id,
                User.username,
                User.name,
                User.avatar,
                User.reputation,
                User.reputation_level,
                StudentProfile.department,
            )
            .order_by(desc("weekly_gain"))
            .limit(limit)
            .all()
        )

        if not rising_rows:
            return jsonify({"status": "success", "data": {"rising_stars": []}})

        rising_user_ids = [row.user_id for row in rising_rows]
        connections = Connection.query.filter(
            or_(
                and_(
                    Connection.requester_id == current_user.id,
                    Connection.receiver_id.in_(rising_user_ids),
                ),
                and_(
                    Connection.receiver_id == current_user.id,
                    Connection.requester_id.in_(rising_user_ids),
                ),
            )
        ).all()

        connection_map = {}
        for conn in connections:
            other_id = (
                conn.receiver_id
                if conn.requester_id == current_user.id
                else conn.requester_id
            )
            connection_map[other_id] = conn.status

        rising_data = [
            {
                "user": {
                    "id": row.user_id,
                    "username": row.username,
                    "name": row.name,
                    "avatar": row.avatar,
                    "reputation": row.reputation,
                    "reputation_level": row.reputation_level,
                    "department": row.department,
                },
                "weekly_gain": int(row.weekly_gain),
                "is_you": row.user_id == current_user.id,
                "status": connection_map.get(row.user_id),
                "trend": "🔥" if row.weekly_gain > 100 else "⚡",
            }
            for row in rising_rows
        ]

        return jsonify({"status": "success", "data": {"rising_stars": rising_data}})

    except Exception as e:
        current_app.logger.error(f"Rising stars error: {str(e)}")
        return error_response("Failed to load rising stars")


@reputation_bp.route("/badges/top-earners", methods=["GET"])
@token_required
def get_top_badge_earners(current_user):
    """
    Get users with the most badges earned.

    Query params:
    - limit: Max users to return (default 10, max 50)
    """
    try:
        limit = min(request.args.get("limit", 10, type=int), 50)

        top_rows = (
            db.session.query(
                UserBadge.user_id,
                func.count(UserBadge.id).label("badge_count"),
                User.username,
                User.name,
                User.avatar,
                User.reputation,
                User.reputation_level,
                StudentProfile.department,
            )
            .join(User, User.id == UserBadge.user_id)
            .outerjoin(StudentProfile, StudentProfile.user_id == UserBadge.user_id)
            .filter(User.status == "approved")
            .group_by(
                UserBadge.user_id,
                User.username,
                User.name,
                User.avatar,
                User.reputation,
                User.reputation_level,
                StudentProfile.department,
            )
            .order_by(desc("badge_count"))
            .limit(limit)
            .all()
        )

        if not top_rows:
            return jsonify({"status": "success", "data": {"top_earners": []}})

        top_user_ids = [row.user_id for row in top_rows]
        connections = Connection.query.filter(
            or_(
                and_(
                    Connection.requester_id == current_user.id,
                    Connection.receiver_id.in_(top_user_ids),
                ),
                and_(
                    Connection.receiver_id == current_user.id,
                    Connection.requester_id.in_(top_user_ids),
                ),
            )
        ).all()

        connection_map = {}
        for conn in connections:
            other_id = (
                conn.receiver_id
                if conn.requester_id == current_user.id
                else conn.requester_id
            )
            connection_map[other_id] = conn.status

        badge_preview_rows = (
            db.session.query(UserBadge.user_id, Badge.name, Badge.icon)
            .join(Badge, Badge.id == UserBadge.badge_id)
            .filter(UserBadge.user_id.in_(top_user_ids))
            .order_by(UserBadge.user_id, UserBadge.earned_at.desc())
            .all()
        )

        badge_preview_map = {}
        for user_id, badge_name, badge_icon in badge_preview_rows:
            if user_id not in badge_preview_map:
                badge_preview_map[user_id] = []
            if len(badge_preview_map[user_id]) < 3:
                badge_preview_map[user_id].append({"name": badge_name, "icon": badge_icon})

        current_user_badge_count = (
            db.session.query(func.count(UserBadge.id))
            .filter(UserBadge.user_id == current_user.id)
            .scalar()
            or 0
        )
        your_rank = (
            db.session.query(func.count())
            .select_from(
                db.session.query(UserBadge.user_id)
                .group_by(UserBadge.user_id)
                .having(func.count(UserBadge.id) > current_user_badge_count)
                .subquery()
            )
            .scalar()
            + 1
        )

        top_earners = [
            {
                "rank": idx,
                "user": {
                    "id": row.user_id,
                    "username": row.username,
                    "name": row.name,
                    "avatar": row.avatar,
                    "reputation": row.reputation,
                    "reputation_level": row.reputation_level,
                    "department": row.department,
                },
                "badge_count": row.badge_count,
                "badge_previews": badge_preview_map.get(row.user_id, []),
                "is_you": row.user_id == current_user.id,
                "connection_status": connection_map.get(row.user_id),
            }
            for idx, row in enumerate(top_rows, 1)
        ]

        return jsonify({
            "status": "success",
            "data": {
                "top_earners": top_earners,
                "your_rank": your_rank,
                "your_badge_count": current_user_badge_count,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Top badge earners error: {str(e)}")
        return error_response("Failed to load top badge earners")


@reputation_bp.route("/reputation/me", methods=["GET"])
@token_required
def get_my_reputation(current_user):
    try:
        level = get_reputation_level(current_user.reputation)
        current_min = level["min"]
        current_max = level["max"]

        if current_max > current_min:
            current_percent = min(
                ((current_user.reputation - current_min) / (current_max - current_min)) * 100,
                100,
            )
        else:
            current_percent = 100.0

        next_level_data = None
        for idx, lvl in enumerate(REPUTATION_LEVELS):
            if lvl["name"] == level["name"]:
                if idx < len(REPUTATION_LEVELS) - 1:
                    next_level_info = REPUTATION_LEVELS[idx + 1]
                    points_needed = next_level_info["min"] - current_user.reputation
                    level_range = next_level_info["min"] - level["min"]
                    progress_percentage = (
                        ((current_user.reputation - level["min"]) / level_range) * 100
                        if level_range > 0
                        else 0
                    )
                    next_level_data = {
                        "name": next_level_info["name"],
                        "icon": next_level_info["icon"],
                        "min_points": next_level_info["min"],
                        "points_needed": max(points_needed, 0),
                        "level_range": level_range,
                        "progress_percentage": round(max(0, min(progress_percentage, 100)), 1),
                    }
                break

        rank = (
            db.session.query(func.count(User.id))
            .filter(User.reputation > current_user.reputation, User.status == "approved")
            .scalar()
            + 1
        )
        total_users = User.query.filter_by(status="approved").count()

        recent_changes = (
            ReputationHistory.query.filter_by(user_id=current_user.id)
            .order_by(ReputationHistory.created_at.desc())
            .limit(5)
            .all()
        )

        changes_data = [
            {
                "action": c.action,
                "points_change": c.points_change,
                "reputation_after": c.reputation_after,
                "related_type": c.related_type,
                "related_id": c.related_id,
                "created_at": c.created_at.isoformat(),
            }
            for c in recent_changes
        ]

        return jsonify({
            "status": "success",
            "data": {
                "reputation": {
                    "points": current_user.reputation,
                    "level": {
                        "name": level["name"],
                        "icon": level["icon"],
                        "color": level["color"],
                        "min": level["min"],
                        "max": level["max"],
                        "current_percent": round(current_percent, 1),
                    },
                    "next_level": next_level_data,
                    "rank": {
                        "global": rank,
                        "total_users": total_users,
                        "percentile": round((1 - (rank / total_users)) * 100, 1) if total_users > 0 else 0,
                    },
                },
                "recent_changes": changes_data,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get reputation error: {str(e)}")
        return error_response("Failed to load reputation data")


@reputation_bp.route("/reputation/history", methods=["GET"])
@token_required
def get_reputation_history(current_user):
    try:
        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 20, type=int), 50)
        action_filter = request.args.get("action", "").strip()

        query = ReputationHistory.query.filter_by(user_id=current_user.id)
        if action_filter:
            query = query.filter_by(action=action_filter)

        paginated = query.order_by(ReputationHistory.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        history_data = [
            {
                "id": r.id,
                "action": r.action,
                "points_change": r.points_change,
                "reputation_before": r.reputation_before,
                "reputation_after": r.reputation_after,
                "related_type": r.related_type,
                "related_id": r.related_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in paginated.items
        ]

        total_gained = (
            db.session.query(func.sum(ReputationHistory.points_change))
            .filter(ReputationHistory.user_id == current_user.id, ReputationHistory.points_change > 0)
            .scalar()
            or 0
        )
        total_lost = abs(
            db.session.query(func.sum(ReputationHistory.points_change))
            .filter(ReputationHistory.user_id == current_user.id, ReputationHistory.points_change < 0)
            .scalar()
            or 0
        )

        return jsonify({
            "status": "success",
            "data": {
                "history": history_data,
                "summary": {
                    "total_gained": int(total_gained),
                    "total_lost": int(total_lost),
                    "net_change": int(total_gained - total_lost),
                    "current_reputation": current_user.reputation,
                },
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": paginated.total,
                    "pages": paginated.pages,
                },
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get reputation history error: {str(e)}")
        return error_response("Failed to load reputation history")


@reputation_bp.route("/reputation/leaderboard", methods=["GET"])
@token_required
def get_global_leaderboard(current_user):
    try:
        limit = min(request.args.get("limit", 50, type=int), 100)

        top_users = User.query.filter_by(status="approved").order_by(User.reputation.desc()).limit(limit).all()

        if not top_users:
            return jsonify({"status": "success", "data": {"leaderboard": [], "your_rank": None, "total_users": 0}})

        top_user_ids = [u.id for u in top_users]

        profiles = StudentProfile.query.filter(StudentProfile.user_id.in_(top_user_ids)).all()
        profile_map = {p.user_id: p for p in profiles}

        connections = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id.in_(top_user_ids)),
                and_(Connection.receiver_id == current_user.id, Connection.requester_id.in_(top_user_ids)),
            )
        ).all()
        connection_map = {}
        for conn in connections:
            other_id = conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
            connection_map[other_id] = conn.status

        leaderboard_data = []
        for idx, user in enumerate(top_users, 1):
            profile = profile_map.get(user.id)
            level = get_reputation_level(user.reputation)
            leaderboard_data.append({
                "rank": idx,
                "status": connection_map.get(user.id),
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "name": user.name,
                    "avatar": user.avatar,
                    "department": profile.department if profile else None,
                },
                "reputation": {
                    "points": user.reputation,
                    "level": {"name": level["name"], "icon": level["icon"], "color": level["color"]},
                },
                "stats": {"total_posts": user.total_posts, "total_helpful": user.total_helpful},
                "is_you": user.id == current_user.id,
            })

        your_rank = None
        if current_user.id not in [u["user"]["id"] for u in leaderboard_data]:
            your_rank = (
                db.session.query(func.count(User.id))
                .filter(User.reputation > current_user.reputation, User.status == "approved")
                .scalar()
                + 1
            )

        return jsonify({
            "status": "success",
            "data": {
                "leaderboard": leaderboard_data,
                "your_rank": your_rank,
                "total_users": User.query.filter_by(status="approved").count(),
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get leaderboard error: {str(e)}")
        return error_response("Failed to load leaderboard")


@reputation_bp.route("/reputation/leaderboard/department", methods=["GET"])
@token_required
def get_department_leaderboard(current_user):
    try:
        limit = min(request.args.get("limit", 50, type=int), 100)
        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
        department = profile.department if profile else None

        top_users = (
            db.session.query(User)
            .join(StudentProfile)
            .filter(StudentProfile.department == department, User.status == "approved")
            .order_by(User.reputation.desc())
            .limit(limit)
            .all()
        )

        top_user_ids = [u.id for u in top_users]

        profiles = StudentProfile.query.filter(StudentProfile.user_id.in_(top_user_ids)).all()
        profile_map = {p.user_id: p for p in profiles}

        connections = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id.in_(top_user_ids)),
                and_(Connection.receiver_id == current_user.id, Connection.requester_id.in_(top_user_ids)),
            )
        ).all()
        connection_map = {}
        for conn in connections:
            other_id = conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
            connection_map[other_id] = conn.status

        leaderboard_data = []
        for idx, user in enumerate(top_users, 1):
            p = profile_map.get(user.id)
            level = get_reputation_level(user.reputation)
            leaderboard_data.append({
                "rank": idx,
                "status": connection_map.get(user.id),
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "name": user.name,
                    "avatar": user.avatar,
                    "class_level": p.class_name if p else None,
                },
                "reputation": {
                    "points": user.reputation,
                    "level": {"name": level["name"], "icon": level["icon"], "color": level["color"]},
                },
                "stats": {"total_posts": user.total_posts, "total_helpful": user.total_helpful},
                "is_you": user.id == current_user.id,
            })

        your_rank = None
        current_profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
        if current_profile and current_profile.department == department:
            your_rank = (
                db.session.query(func.count(User.id))
                .join(StudentProfile)
                .filter(
                    StudentProfile.department == department,
                    User.reputation > current_user.reputation,
                    User.status == "approved",
                )
                .scalar()
                + 1
            )

        total_dept_users = (
            db.session.query(func.count(User.id))
            .join(StudentProfile)
            .filter(StudentProfile.department == department, User.status == "approved")
            .scalar()
        )

        return jsonify({
            "status": "success",
            "data": {"leaderboard": leaderboard_data, "your_rank": your_rank, "total_users": total_dept_users},
        })

    except Exception as e:
        current_app.logger.error(f"Get department leaderboard error: {str(e)}")
        return error_response("Failed to load department leaderboard")


@reputation_bp.route("/reputation/stats", methods=["GET"])
@token_required
def reputation_stats(current_user):
    try:
        last_days = datetime.datetime.utcnow() - datetime.timedelta(days=30)
        total_active = User.query.filter(User.last_active > last_days).count()
        average_reputation = db.session.query(func.avg(User.reputation)).scalar()

        top_department = (
            db.session.query(StudentProfile.department, func.sum(User.reputation).label("department_points"))
            .join(StudentProfile, User.id == StudentProfile.user_id)
            .group_by(StudentProfile.department)
            .order_by(func.sum(User.reputation).desc())
            .first()
        )

        department = top_department[0] if top_department else None
        points = top_department[1] if top_department else 0

        return jsonify({
            "status": "success",
            "data": {
                "active_students": total_active,
                "average_reputation": average_reputation,
                "top_department": department,
                "points": points,
            },
        })

    except Exception as e:
        # FIX: previously used raw jsonify({"status": "error", ...}) — inconsistent
        # with every other endpoint which uses error_response().  Also added the
        # logger call that was missing from this function.
        current_app.logger.error(f"Reputation stats error: {str(e)}")
        return error_response("Failed to load reputation stats")


@reputation_bp.route("/reputation/award", methods=["POST"])
@token_required
def award_reputation_endpoint(current_user):
    # FIX: previously had no authorization check — any authenticated student
    # could grant arbitrary reputation points to any account (including their own).
    if current_user.role not in ("admin", "system"):
        return error_response("Forbidden", 403)

    try:
        data = request.get_json()
        user_id = data.get("user_id")
        action = data.get("action")
        related_type = data.get("related_type")
        related_id = data.get("related_id")
        custom_points = data.get("points")

        if not user_id or (not action and not custom_points):
            return error_response("user_id and (action or points) required")

        history = award_reputation(user_id, action, related_type, related_id, custom_points)

        if not history:
            return error_response("Failed to award reputation")

        user = User.query.get(user_id)

        return success_response(
            "Reputation awarded",
            data={
                "user_id": user_id,
                "points_change": history.points_change,
                "new_reputation": user.reputation,
                "new_level": user.reputation_level,
            },
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Award reputation error: {str(e)}")
        return error_response("Failed to award reputation")


@reputation_bp.route("/reputation/levels", methods=["GET"])
def get_reputation_levels():
    return jsonify({
        "status": "success",
        "data": {
            "levels": REPUTATION_LEVELS,
            "actions": [
                {"key": key, "points": value["points"], "description": value["description"]}
                for key, value in REPUTATION_ACTIONS.items()
            ],
        },
    })
