"""
StudyHub - Reputation System
══════════════════════════════════════════════════════════════════════════════

Personal reputation summary, history, stats, admin award, and the
levels-lookup endpoint. No leaderboard logic lives here anymore — see
Document 1 §6.2:

    - GET /reputation/leaderboard and GET /reputation/leaderboard/department
      were REMOVED entirely (not redirected/deprecated). leaderboard.py's
      GET /leaderboard/global (with department filter) and
      GET /leaderboard/department are the sole surviving equivalents.
    - GET /reputation/rising-stars is kept (URL stability for existing
      callers) but is now a thin wrapper around
      services/leaderboard_service.py::get_rising_stars — the single
      implementation, also used by leaderboard.py's own /leaderboard/rising.
      reputation.py and leaderboard.py used to each compute this slightly
      differently; that duplication is gone.

Business logic (point-awarding, REPUTATION_ACTIONS, milestone-checking)
lives in services/reputation_service.py (Document 2 §3.1) — this file is
the thin HTTP layer: request parsing, auth, response envelope.

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

from __future__ import annotations

import datetime

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func

from models import User, StudentProfile, ReputationHistory
from extensions import db
from routes.student.helpers import token_required, success_response, error_response
from services import cache_service

# H-8 fix: REPUTATION_LEVELS/get_reputation_level used to be duplicated here
# (and independently in badges.py, leaderboard.py, and
# models.py::User.update_reputation_level) — now imported from the single
# shared module so all of them stay in sync.
from services.reputation_levels import REPUTATION_LEVELS, get_reputation_level

# Document 2 §3.1: REPUTATION_ACTIONS, award_reputation, and
# check_and_award_milestone moved to services/reputation_service.py —
# imported here at the same names so every existing call site in this file
# (and in posts.py, which imports these from routes.student.reputation)
# keeps working. NOTE: award_reputation() no longer commits internally
# (Document 2 §5) — award_reputation_endpoint below commits explicitly,
# since it previously relied on the internal commit.
from services.reputation_service import (
    REPUTATION_ACTIONS,
    ReputationAction,
    award_reputation,
    check_and_award_milestone,
)

# Document 1 §6.2: rising-stars now has exactly one implementation, shared
# with leaderboard.py's /leaderboard/rising route.
from services import leaderboard_service
# Phase 5b (Document 4 §1): PUBLIC_READ-tier limiting on reputation reads —
# same reasoning as leaderboard.py (checked frequently, cheap to abuse).
from services.rate_limit_service import limiter, RateLimitTier, ip_key

reputation_bp = Blueprint("student_reputation", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# PURE HELPERS  (no DB calls)
# ─────────────────────────────────────────────────────────────────────────────

def next_level(points: int) -> dict | None:
    for idx, level in enumerate(REPUTATION_LEVELS):
        if level["min"] <= points <= level["max"]:
            if idx + 1 < len(REPUTATION_LEVELS):
                return REPUTATION_LEVELS[idx + 1]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 1. RISING STARS  (thin wrapper — Document 1 §6.2)
# ─────────────────────────────────────────────────────────────────────────────

@reputation_bp.route("/reputation/rising-stars", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_rising_stars(current_user):
    """
    Users with the highest reputation gain in the last 7 days.

    URL kept stable at /reputation/rising-stars for whatever caller depends
    on this specific path (Document 1 §6.2), but the computation itself is
    now delegated entirely to services/leaderboard_service.get_rising_stars
    — the same function backing leaderboard.py's GET /leaderboard/rising.
    There is exactly one rising-stars implementation now.
    """
    try:
        limit = min(request.args.get("limit", 10, type=int), 50)
        data = leaderboard_service.get_rising_stars(limit, viewer_id=current_user.id)
        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Rising stars error: {str(e)}")
        return error_response("Failed to load rising stars")


# ─────────────────────────────────────────────────────────────────────────────
# 2. PERSONAL REPUTATION SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

@cache_service.cached("sh:1:rep:me:{user_id}", ttl_seconds=120)
def _compute_my_reputation(user_id, reputation):
    """
    The actual computation behind GET /reputation/me, cache-aside per plan
    §4.2's row for this route (120s TTL). Entirely scoped to `user_id`
    throughout (own level, own rank-relative-to-others, own recent
    history) — no viewer/subject split needed the way the shared
    leaderboard pages require, since this route is inherently "my own
    summary" and every existing caller is `current_user` looking at
    themselves.

    `reputation` is passed explicitly (not re-read from a fresh User query
    inside this function) so the interpolated cache key can't silently
    diverge from the value actually used in the rank/percentile
    computation below — both come from the exact same current_user.reputation
    the route read a moment earlier. It does NOT participate in the cache
    key itself (only user_id does, matching plan §3.2's key pattern
    `sh:1:rep:me:{user_id}`) — reputation_service.py::award_reputation's
    existing `cache_service.delete(f"sh:1:rep:me:{user_id}")` call (plan
    §5.1) is what keeps this fresh immediately after every point change
    for this user; the 120s TTL is the fallback for the rank-relative-to-
    others component, which can shift from *other* users' reputation
    changes too (see plan §4.2's asymmetry note).
    """
    level = get_reputation_level(reputation)
    current_min = level["min"]
    current_max = level["max"]

    if current_max > current_min:
        current_percent = min(
            ((reputation - current_min) / (current_max - current_min)) * 100,
            100,
        )
    else:
        current_percent = 100.0

    next_level_data = None
    for idx, lvl in enumerate(REPUTATION_LEVELS):
        if lvl["name"] == level["name"]:
            if idx < len(REPUTATION_LEVELS) - 1:
                next_level_info = REPUTATION_LEVELS[idx + 1]
                points_needed = next_level_info["min"] - reputation
                level_range = next_level_info["min"] - level["min"]
                progress_percentage = (
                    ((reputation - level["min"]) / level_range) * 100
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
        .filter(User.reputation > reputation, User.status == "approved")
        .scalar()
        + 1
    )
    total_users = User.query.filter_by(status="approved").count()

    recent_changes = (
        ReputationHistory.query.filter_by(user_id=user_id)
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

    return {
        "reputation": {
            "points": reputation,
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
    }


@reputation_bp.route("/reputation/me", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_my_reputation(current_user):
    try:
        data = _compute_my_reputation(current_user.id, current_user.reputation)
        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Get reputation error: {str(e)}")
        return error_response("Failed to load reputation data")


# ─────────────────────────────────────────────────────────────────────────────
# 3. REPUTATION HISTORY
# ─────────────────────────────────────────────────────────────────────────────

@reputation_bp.route("/reputation/history", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
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


# ─────────────────────────────────────────────────────────────────────────────
# 4. PLATFORM STATS
# ─────────────────────────────────────────────────────────────────────────────

@cache_service.cached("sh:1:rep:stats", ttl_seconds=300)
def _compute_reputation_stats():
    """
    The actual computation behind GET /reputation/stats, cache-aside per
    plan §4.2's row for this route (300s TTL fallback only — platform-wide,
    not user-scoped, no natural per-mutation hook makes sense at this
    granularity). Distinct key from sh:1:lb:stats (leaderboard_service.py's
    get_leaderboard_stats) — the two are separate platform-wide aggregates
    computed by different functions over different queries and must not
    share a cache key, per plan §17.3.

    average_reputation is cast to float explicitly: func.avg() over an
    Integer column can return a Decimal via some DB-API drivers, which
    cache_service's json.dumps() can't serialize — this would otherwise
    surface as a silent "Redis SET failed" warning on every call (fails
    open per plan §8, so it wouldn't break the route, but it would quietly
    mean this endpoint never actually gets cached).
    """
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

    return {
        "active_students": total_active,
        "average_reputation": float(average_reputation) if average_reputation is not None else None,
        "top_department": department,
        "points": int(points) if points else 0,
    }


@reputation_bp.route("/reputation/stats", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def reputation_stats(current_user):
    try:
        data = _compute_reputation_stats()
        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Reputation stats error: {str(e)}")
        return error_response("Failed to load reputation stats")


# ─────────────────────────────────────────────────────────────────────────────
# 5. ADMIN AWARD — REMOVED (Document 3 §6.3, confirmed)
#
# POST /reputation/award was unreachable dead code: token_required (before
# Document 3 §6.1's role_required fix) hardcoded a student-only role check,
# so no account could ever pass both that gate AND this endpoint's own
# inline "admin/system only" check. There is no admin blueprint, admin UI,
# or CLI tool anywhere in the codebase that calls this route, and every
# legitimate reputation award already happens automatically via
# award_reputation()/check_and_award_milestone() triggered by real user
# actions. Confirmed for outright removal rather than fix-and-keep.
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# 6. LEVELS LOOKUP  (public — no auth required)
# ─────────────────────────────────────────────────────────────────────────────

@reputation_bp.route("/reputation/levels", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
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
