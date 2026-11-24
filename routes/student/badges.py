"""
StudyHub - Badges & Achievements System
Award badges for accomplishments, track progress, and showcase achievements

Business logic (BADGE_DEFINITIONS, awarding, progress calculation) now
lives in services/badge_service.py (Document 1 §2, Document 2 §3.2) — this
file is the thin HTTP layer: request parsing, auth, response envelope.
"""

from __future__ import annotations

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func, and_, or_

from models import User, Badge, UserBadge, Connection
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)
from services.reputation_levels import get_reputation_level
from services import badge_service

# Document 1 §6.2 / §4 point 3: top_earners is badge-count-based (a
# genuinely different leaderboard from reputation.py's/leaderboard.py's
# reputation-based ones, so it's kept as its own endpoint) but reuses
# leaderboard_service's batch-loading helpers instead of hand-rolling its
# own {user_id -> User}/{user_id -> connection_status} maps a second time.
# These are the same _user_map/_connection_map helpers get_global_leaderboard
# and get_rising_stars already use — importing them here means there is
# exactly one implementation of "batch load users" and "batch load
# connection status relative to viewer" in the codebase, not two.
from services.leaderboard_service import _user_map, _connection_map, _profile_map
from services import cache_service
# Phase 5b (Document 4 §1): BURST_OK for the feature-toggle/check-all write
# actions (low-risk, small state changes); PUBLIC_READ for the top-earners
# leaderboard-style read.
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key

badges_bp = Blueprint("student_badges", __name__)


# ============================================================================
# BADGE ENDPOINTS
# ============================================================================

@badges_bp.route("/badges/available", methods=["GET"])
@token_required
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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
            badge_service.rarity_rank_expr().desc(),
            Badge.awarded_count.desc(),
            Badge.id.asc()
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


def _earned_badges_payload(user_id):
    """
    Badges earned by ``user_id`` in the shape used by both
    GET /badges/my-badges and GET /badges/user-badges/<id>: one joined
    query (no per-row Badge lookup), newest first.
    """
    rows = (
        db.session.query(UserBadge, Badge)
        .join(Badge, Badge.id == UserBadge.badge_id)
        .filter(UserBadge.user_id == user_id)
        .order_by(UserBadge.earned_at.desc(), UserBadge.id.desc())
        .all()
    )
    badges_data = [
        {
            "id": badge.id,
            "name": badge.name,
            "description": badge.description,
            "icon": badge.icon,
            "category": badge.category,
            "rarity": badge.rarity,
            "earned_at": ub.earned_at.isoformat(),
            "is_featured": ub.is_featured,
        }
        for ub, badge in rows
    ]
    by_rarity = {}
    for badge in badges_data:
        by_rarity.setdefault(badge["rarity"], []).append(badge)
    return {
        "badges": badges_data,
        "by_rarity": by_rarity,
        "total_earned": len(badges_data),
        "featured": [b for b in badges_data if b["is_featured"]],
    }


@badges_bp.route("/badges/my-badges", methods=["GET"])
@token_required
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
def get_my_badges(current_user):
    """Get all badges earned by current user."""
    try:
        return jsonify({"status": "success", "data": _earned_badges_payload(current_user.id)})

    except Exception as e:
        current_app.logger.error(f"Get my badges error: {str(e)}")
        return error_response("Failed to load your badges")


@cache_service.cached("sh:1:badge:progress:{user_id}", ttl_seconds=180)
def _compute_badge_progress(user_id):
    """
    The actual computation behind GET /badges/progress, cache-aside per
    plan §4.3's row for this route (180s TTL fallback + hard invalidation
    on any badge award for this user — that invalidation lives in
    services/badge_service.py::check_and_award_badge/check_all_badges_for_user,
    keyed identically: sh:1:badge:progress:{user_id}, per plan §5.2).
    Entirely scoped to user_id — for every unearned badge, this runs a
    distinct calculate_badge_progress() query shaped by that badge's
    criteria (up to ~18 queries per request in the worst case per plan
    §4.3), which is exactly the cost this cache exists to amortize.
    """
    earned_badge_ids = [ub.badge_id for ub in UserBadge.query.filter_by(user_id=user_id).all()]

    unearned_badges = Badge.query.filter(
        Badge.is_active == True,
        Badge.id.notin_(earned_badge_ids)
    ).all()

    progress_data = []
    for badge in unearned_badges:
        progress = badge_service.calculate_badge_progress(user_id, badge.id)

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

    return {
        "progress": progress_data,
        "total_unearned": len(progress_data)
    }


@badges_bp.route("/badges/progress", methods=["GET"])
@token_required
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
def get_badge_progress(current_user):
    """Get progress toward all unearned badges."""
    try:
        data = _compute_badge_progress(current_user.id)
        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Get badge progress error: {str(e)}")
        return error_response("Failed to load badge progress")


@badges_bp.route("/badges/<int:badge_id>/details", methods=["GET"])
@token_required
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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
            {u.id: u for u in User.query.filter(
                User.id.in_(earner_ids), User.status == "approved").all()}
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
                "is_featured": bool(user_badge and user_badge.is_featured),
                "progress": progress,
                "recent_earners": earners_data
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get badge details error: {str(e)}")
        return error_response("Failed to load badge details")


@badges_bp.route("/badges/feature/<int:badge_id>", methods=["POST"])
@token_required
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
def feature_badge(current_user, badge_id):
    """
    Feature / unfeature a badge on the profile (max 3 featured per user).

    Body (JSON, optional): ``{"featured": true|false}`` sets the state
    explicitly, which makes the call idempotent -- a retry or double-tap
    cannot flip it back. Without a body it toggles (legacy behaviour).
    The user's row is locked while counting so two concurrent requests
    cannot both slip past the limit of 3.
    """
    try:
        payload = request.get_json(silent=True) or {}
        desired = payload.get("featured")
        if desired is not None and not isinstance(desired, bool):
            return error_response("'featured' must be true or false", 400)

        # Serialize concurrent feature requests for this user.
        db.session.query(User.id).filter(User.id == current_user.id).with_for_update().first()

        user_badge = UserBadge.query.filter_by(
            user_id=current_user.id,
            badge_id=badge_id
        ).first()

        if not user_badge:
            return error_response("You haven't earned this badge", 404)

        target = (not user_badge.is_featured) if desired is None else desired

        if target and not user_badge.is_featured:
            featured_count = UserBadge.query.filter_by(
                user_id=current_user.id,
                is_featured=True
            ).count()
            if featured_count >= 3:
                return error_response("Maximum 3 featured badges allowed", 400)

        user_badge.is_featured = target
        db.session.commit()

        return success_response(
            f"Badge {'featured' if user_badge.is_featured else 'unfeatured'}",
            data={"is_featured": user_badge.is_featured}
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Feature badge error: {str(e)}")
        return error_response("Failed to feature badge")


# ─────────────────────────────────────────────────────────────────────────
# POST /badges/award — REMOVED (Document 3 §6.3, confirmed)
#
# Was unreachable dead code: token_required (before Document 3 §6.1's
# role_required fix) hardcoded a student-only role check, so no account
# could ever pass both that gate AND this endpoint's own inline
# "admin/system only" check. There is no admin blueprint, admin UI, or
# CLI tool anywhere in the codebase that calls this route, and every
# legitimate badge award already happens automatically via
# check_and_award_badge()/check_all_badges_for_user() triggered by real
# user actions. Confirmed for outright removal rather than fix-and-keep.
# ─────────────────────────────────────────────────────────────────────────


TOP_EARNERS_DEFAULT = 20
TOP_EARNERS_MAX = 50


@cache_service.cached("sh:1:badge:top_earners", ttl_seconds=120)
def _compute_top_earners():
    """
    Viewer-INDEPENDENT top-50-by-badge-count computation (the route slices to ?limit=), cache-aside per
    plan §4.3's row for this route (120s TTL fallback only — per-viewer
    connection-status component makes hard invalidation on every badge
    award impractical, same fan-out reasoning as plan §4.9). Key is
    `sh:1:badge:top_earners` per plan §3.2 — no user_id, since the ranked
    list of top badge earners genuinely doesn't vary by who's asking.

    `status` (connection status relative to viewer) and `is_you` are NOT
    computed here — same split as leaderboard_service.py's
    _get_global_leaderboard_page/_get_rising_stars_page, and for the
    identical reason: this cache entry is shared across every viewer, so
    it must never bake in one particular viewer's connection graph or
    identity. top_earners() below overlays real per-viewer values after
    the cache lookup.
    """
    # Only approved accounts are ranked (suspended/pending users must not
    # appear on a public leaderboard). Ties on badge count break
    # deterministically -- earliest first badge, then lowest user id -- so
    # equal counts cannot reorder between cache refreshes.
    rank_rows = (
        db.session.query(
            UserBadge.user_id,
            func.count(UserBadge.id).label("badge_count"),
        )
        .join(User, User.id == UserBadge.user_id)
        .filter(User.status == "approved")
        .group_by(UserBadge.user_id)
        .order_by(
            func.count(UserBadge.id).desc(),
            func.min(UserBadge.earned_at).asc(),
            UserBadge.user_id.asc(),
        )
        .limit(TOP_EARNERS_MAX)
        .all()
    )

    if not rank_rows:
        return []

    top_user_ids = [row.user_id for row in rank_rows]

    umap = _user_map(top_user_ids)
    pmap = _profile_map(top_user_ids)

    leaderboard_data = []
    for idx, row in enumerate(rank_rows, start=1):
        user = umap.get(row.user_id)
        if not user:
            continue

        profile = pmap.get(row.user_id)
        level = get_reputation_level(user.reputation)

        leaderboard_data.append({
            "rank": idx,
            "status": None,  # placeholder — overwritten per-viewer below
            "user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "avatar": user.avatar,
                "department": profile.department if profile else None,
                "class_level": profile.class_name if profile else None,
            },
            "reputation": {
                "points": user.reputation,
                "level": {
                    "name": level["name"],
                    "icon": level["icon"],
                    "color": level["color"],
                },
            },
            "stats": {
                "total_badges": row.badge_count,
                "total_helpful": user.total_helpful,
            },
            "is_you": False,  # placeholder — overwritten per-viewer below
        })

    return leaderboard_data


@badges_bp.route("/badges/top-earners", methods=["GET"])
@token_required
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
def top_earners(current_user):
    """
    Top users by badge count (default 20, ``?limit=`` up to 50), approved
    accounts only, with connection status relative to current_user.

    A genuinely different leaderboard from reputation.py's/leaderboard.py's
    (badge-count-based, not reputation-based) so it's kept as its own
    endpoint per Document 1 §6.2 — but the {user_id -> User},
    {user_id -> StudentProfile}, and {user_id -> connection_status} batch
    lookups below now reuse services/leaderboard_service.py's
    _user_map/_profile_map/_connection_map instead of hand-rolling a
    second copy of each, per Document 1 §6.2's third bullet.

    The shared ranking computation is delegated to _compute_top_earners
    above (cache-aside); this function's own job is only the live
    per-viewer overlay (status, is_you), computed fresh on every call
    regardless of whether the shared list was a cache hit.
    """
    try:
        try:
            limit = int(request.args.get("limit", TOP_EARNERS_DEFAULT))
        except (TypeError, ValueError):
            limit = TOP_EARNERS_DEFAULT
        limit = max(1, min(limit, TOP_EARNERS_MAX))

        leaderboard_data = [dict(e) for e in (_compute_top_earners() or [])[:limit]]

        if not leaderboard_data:
            return jsonify({"status": "success", "data": []})

        top_user_ids = [entry["user"]["id"] for entry in leaderboard_data]
        connection_map = _connection_map(current_user.id, top_user_ids)

        for entry in leaderboard_data:
            entry["status"] = connection_map.get(entry["user"]["id"])
            entry["is_you"] = entry["user"]["id"] == current_user.id

        return jsonify({"status": "success", "data": leaderboard_data})

    except Exception as e:
        current_app.logger.error(f"Load badges error: {str(e)}")
        return error_response("Failed to load top badge earners")


@badges_bp.route("/badges/check-all", methods=["POST"])
@token_required
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
def check_all_badges(current_user):
    """Manually trigger badge check for current user."""
    try:
        awarded = badge_service.check_all_badges_for_user(current_user.id)
        db.session.commit()   # the service never commits; this route owns the transaction

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
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
def get_user_badges(current_user, id):
    """Get all badges earned by a particular (approved, non-blocked) user."""
    try:
        target = User.query.get(id)
        if not target or target.status != "approved":
            return error_response("User not found", 404)

        if id != current_user.id:
            blocked = Connection.query.filter(
                Connection.status == "blocked",
                or_(
                    and_(Connection.requester_id == current_user.id, Connection.receiver_id == id),
                    and_(Connection.requester_id == id, Connection.receiver_id == current_user.id),
                ),
            ).first()
            if blocked:
                return error_response("User not found", 404)

        return jsonify({"status": "success", "data": _earned_badges_payload(id)})

    except Exception as e:
        current_app.logger.error(f"Get user badges error: {str(e)}")
        return error_response("Failed to load user badges")
