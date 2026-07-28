"""
StudyHub - Badges & Achievements System
Award badges for accomplishments, track progress, and showcase achievements

Business logic (BADGE_DEFINITIONS, awarding, progress calculation) now
lives in services/badge_service.py (Document 1 §2, Document 2 §3.2) — this
file is the thin HTTP layer: request parsing, auth, response envelope.
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func, and_, or_

from models import User, Badge, UserBadge, StudentProfile, Connection
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)
from services.reputation_levels import get_reputation_level
from services import badge_service

badges_bp = Blueprint("student_badges", __name__)


# ============================================================================
# BADGE ENDPOINTS
# ============================================================================

@badges_bp.route("/badges/available", methods=["GET"])
@token_required
def get_available_badges(current_user):
    """
    Get all available badges in the system

    Query params:
    - category: Filter by category
    - rarity: Filter by rarity
    """
    try:
        category = request.args.get("category", "").strip()
        rarity = request.args.get("rarity", "").strip()

        query = Badge.query.filter_by(is_active=True)

        if category:
            query = query.filter_by(category=category)

        if rarity:
            query = query.filter_by(rarity=rarity)

        badges = query.order_by(
            Badge.rarity.desc(),
            Badge.awarded_count.desc()
        ).all()

        all_user_badges = UserBadge.query.filter_by(user_id=current_user.id).all()
        user_badge_map = {ub.badge_id: ub for ub in all_user_badges}
        user_badge_ids = set(user_badge_map.keys())

        badges_data = []
        for badge in badges:
            ub = user_badge_map.get(badge.id)
            has_earned = ub is not None

            badges_data.append({
                "id": badge.id,
                "name": badge.name,
                "description": badge.description,
                "icon": badge.icon,
                "category": badge.category,
                "rarity": badge.rarity,
                "awarded_count": badge.awarded_count,
                "has_earned": has_earned,
                "earned_at": ub.earned_at.isoformat() if ub else None
            })

        categories = {}
        for badge in badges_data:
            cat = badge["category"]
            categories.setdefault(cat, []).append(badge)

        return jsonify({
            "status": "success",
            "data": {
                "badges": badges_data,
                "by_category": categories,
                "total": len(badges_data),
                "earned": len(user_badge_ids)
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get available badges error: {str(e)}")
        return error_response("Failed to load badges")


@badges_bp.route("/badges/my-badges", methods=["GET"])
@token_required
def get_my_badges(current_user):
    """Get all badges earned by current user."""
    try:
        user_badges = UserBadge.query.filter_by(
            user_id=current_user.id
        ).order_by(UserBadge.earned_at.desc()).all()

        badges_data = []
        for ub in user_badges:
            badge = Badge.query.get(ub.badge_id)
            if badge:
                badges_data.append({
                    "id": badge.id,
                    "name": badge.name,
                    "description": badge.description,
                    "icon": badge.icon,
                    "category": badge.category,
                    "rarity": badge.rarity,
                    "earned_at": ub.earned_at.isoformat(),
                    "is_featured": ub.is_featured
                })

        by_rarity = {}
        for badge in badges_data:
            by_rarity.setdefault(badge["rarity"], []).append(badge)

        return jsonify({
            "status": "success",
            "data": {
                "badges": badges_data,
                "by_rarity": by_rarity,
                "total_earned": len(badges_data),
                "featured": [b for b in badges_data if b["is_featured"]]
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get my badges error: {str(e)}")
        return error_response("Failed to load your badges")


@badges_bp.route("/badges/progress", methods=["GET"])
@token_required
def get_badge_progress(current_user):
    """Get progress toward all unearned badges."""
    try:
        earned_badge_ids = [ub.badge_id for ub in UserBadge.query.filter_by(user_id=current_user.id).all()]

        unearned_badges = Badge.query.filter(
            Badge.is_active == True,
            Badge.id.notin_(earned_badge_ids)
        ).all()

        progress_data = []
        for badge in unearned_badges:
            progress = badge_service.calculate_badge_progress(current_user.id, badge.id)

            if progress:
                progress_data.append({
                    "badge": {
                        "id": badge.id,
                        "name": badge.name,
                        "description": badge.description,
                        "icon": badge.icon,
                        "category": badge.category,
                        "rarity": badge.rarity
                    },
                    "progress": progress
                })

        progress_data.sort(key=lambda x: x["progress"]["percentage"], reverse=True)

        return jsonify({
            "status": "success",
            "data": {
                "progress": progress_data,
                "total_unearned": len(progress_data)
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get badge progress error: {str(e)}")
        return error_response("Failed to load badge progress")


@badges_bp.route("/badges/<int:badge_id>/details", methods=["GET"])
@token_required
def get_badge_details(current_user, badge_id):
    """Get detailed information about a specific badge, including requirements and progress."""
    try:
        badge = Badge.query.get(badge_id)

        if not badge:
            return error_response("Badge not found", 404)

        user_badge = UserBadge.query.filter_by(
            user_id=current_user.id,
            badge_id=badge_id
        ).first()

        has_earned = bool(user_badge)

        progress = None
        if not has_earned:
            progress = badge_service.calculate_badge_progress(current_user.id, badge_id)

        recent_earners = UserBadge.query.filter_by(
            badge_id=badge_id
        ).order_by(UserBadge.earned_at.desc()).limit(10).all()

        earner_ids = [ub.user_id for ub in recent_earners]
        earner_users = (
            {u.id: u for u in User.query.filter(User.id.in_(earner_ids)).all()}
            if earner_ids else {}
        )

        earners_data = []
        for ub in recent_earners:
            user = earner_users.get(ub.user_id)
            if user:
                earners_data.append({
                    "username": user.username,
                    "name": user.name,
                    "avatar": user.avatar,
                    "earned_at": ub.earned_at.isoformat()
                })

        return jsonify({
            "status": "success",
            "data": {
                "badge": {
                    "id": badge.id,
                    "name": badge.name,
                    "description": badge.description,
                    "icon": badge.icon,
                    "category": badge.category,
                    "rarity": badge.rarity,
                    "criteria": badge.criteria,
                    "awarded_count": badge.awarded_count
                },
                "has_earned": has_earned,
                "earned_at": user_badge.earned_at.isoformat() if user_badge else None,
                "progress": progress,
                "recent_earners": earners_data
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get badge details error: {str(e)}")
        return error_response("Failed to load badge details")


@badges_bp.route("/badges/feature/<int:badge_id>", methods=["POST"])
@token_required
def feature_badge(current_user, badge_id):
    """Feature a badge on profile (show prominently). Max 3 featured badges per user."""
    try:
        user_badge = UserBadge.query.filter_by(
            user_id=current_user.id,
            badge_id=badge_id
        ).first()

        if not user_badge:
            return error_response("You haven't earned this badge", 404)

        featured_count = UserBadge.query.filter_by(
            user_id=current_user.id,
            is_featured=True
        ).count()

        if featured_count >= 3 and not user_badge.is_featured:
            return error_response("Maximum 3 featured badges allowed", 400)

        user_badge.is_featured = not user_badge.is_featured
        db.session.commit()

        return success_response(
            f"Badge {'featured' if user_badge.is_featured else 'unfeatured'}",
            data={"is_featured": user_badge.is_featured}
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Feature badge error: {str(e)}")
        return error_response("Failed to feature badge")


@badges_bp.route("/badges/award", methods=["POST"])
@token_required
def award_badge_endpoint(current_user):
    """
    Internal endpoint to award a badge directly. Restricted to admin and
    system accounts only.

    NOTE (Document 3 §6.3): this endpoint is flagged for possible removal
    outright — pending your confirmation per the security-review document.
    Left in place, still admin-gated, until that's decided.

    Body: {"user_id": 123, "badge_name": "Helpful Hero"}
    """
    if current_user.role not in ("admin", "system"):
        return error_response("Forbidden", 403)

    try:
        data = request.get_json()

        user_id = data.get("user_id")
        badge_name = data.get("badge_name")

        if not user_id or not badge_name:
            return error_response("user_id and badge_name required")

        user_badge = badge_service.check_and_award_badge(user_id, badge_name)

        if not user_badge:
            return error_response("Badge already earned or user doesn't qualify")

        badge = Badge.query.get(user_badge.badge_id)

        return success_response(
            f"Badge '{badge.name}' awarded",
            data={
                "user_id": user_id,
                "badge": {
                    "id": badge.id,
                    "name": badge.name,
                    "icon": badge.icon,
                    "rarity": badge.rarity
                },
                "earned_at": user_badge.earned_at.isoformat()
            }
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Award badge error: {str(e)}")
        return error_response("Failed to award badge")


@badges_bp.route("/badges/top-earners", methods=["GET"])
@token_required
def top_earners(current_user):
    """Top 20 users by badge count, with connection status relative to current_user."""
    try:
        leaderboard_data = []

        top_rows = (
            db.session.query(
                User.id,
                func.count(UserBadge.id).label("badge_count"),
                User.username,
                User.name,
                User.avatar,
                User.reputation,
                User.reputation_level,
                User.total_helpful,
                StudentProfile.department,
                StudentProfile.class_name,
            )
            .join(UserBadge, UserBadge.user_id == User.id)
            .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
            .group_by(
                User.id,
                User.username,
                User.name,
                User.avatar,
                User.reputation,
                User.reputation_level,
                User.total_helpful,
                StudentProfile.department,
                StudentProfile.class_name,
            )
            .order_by(func.count(UserBadge.id).desc())
            .limit(20)
            .all()
        )

        if not top_rows:
            return jsonify({"status": "success", "data": leaderboard_data})

        top_user_ids = [row.id for row in top_rows]

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

        for idx, row in enumerate(top_rows, start=1):
            level = get_reputation_level(row.reputation)

            leaderboard_data.append({
                "rank": idx,
                "status": connection_map.get(row.id),
                "user": {
                    "id": row.id,
                    "username": row.username,
                    "name": row.name,
                    "avatar": row.avatar,
                    "department": row.department,
                    "class_level": row.class_name,
                },
                "reputation": {
                    "points": row.reputation,
                    "level": {
                        "name": level["name"],
                        "icon": level["icon"],
                        "color": level["color"],
                    },
                },
                "stats": {
                    "total_badges": row.badge_count,
                    "total_helpful": row.total_helpful,
                },
                "is_you": row.id == current_user.id,
            })

        return jsonify({"status": "success", "data": leaderboard_data})

    except Exception as e:
        current_app.logger.error(f"Load badges error: {str(e)}")
        return error_response("Failed to load top badge earners")


@badges_bp.route("/badges/check-all", methods=["POST"])
@token_required
def check_all_badges(current_user):
    """Manually trigger badge check for current user."""
    try:
        awarded = badge_service.check_all_badges_for_user(current_user.id)

        if not awarded:
            return success_response("No new badges earned")

        badges_data = [{
            "name": b.name,
            "icon": b.icon,
            "rarity": b.rarity
        } for b in awarded]

        return success_response(
            f"Earned {len(awarded)} new badge(s)!",
            data={"badges": badges_data}
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Check all badges error: {str(e)}")
        return error_response("Failed to check badges")


@badges_bp.route("/badges/user-badges/<int:id>", methods=["GET"])
@token_required
def get_user_badges(current_user, id):
    """Get all badges earned by a particular user."""
    try:
        user_badges = UserBadge.query.filter_by(
            user_id=id
        ).order_by(UserBadge.earned_at.desc()).all()

        badges_data = []
        for ub in user_badges:
            badge = Badge.query.get(ub.badge_id)
            if badge:
                badges_data.append({
                    "id": badge.id,
                    "name": badge.name,
                    "description": badge.description,
                    "icon": badge.icon,
                    "category": badge.category,
                    "rarity": badge.rarity,
                    "earned_at": ub.earned_at.isoformat(),
                    "is_featured": ub.is_featured
                })

        by_rarity = {}
        for badge in badges_data:
            by_rarity.setdefault(badge["rarity"], []).append(badge)

        return jsonify({
            "status": "success",
            "data": {
                "badges": badges_data,
                "by_rarity": by_rarity,
                "total_earned": len(badges_data),
                "featured": [b for b in badges_data if b["is_featured"]]
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get my badges error: {str(e)}")
        return error_response("Failed to load user badges badges")
