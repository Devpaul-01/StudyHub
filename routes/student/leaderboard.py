"""
StudyHub – Leaderboard System
══════════════════════════════════════════════════════════════════════════════

Business logic (period scoring, nearby-user computation, snapshot creation,
score breakdown, etc.) now lives in services/leaderboard_service.py
(Document 1 §2, Document 2 §3.3, snapshot consolidation per Document 1
§6.3). This file is the thin HTTP layer: request-arg parsing, auth,
response envelope.

Philosophy (kept from the original, since it explains WHY these endpoints
are shaped the way they are):
  Students care far more about beating someone 3 ranks above them than
  about the mythical #1 spot. Period-scoped ranking, nearby-user context,
  rank movement arrows, streak display, connections leaderboard, and
  rising-stars all exist to create that healthy, addictive engagement loop.
"""

from __future__ import annotations

from flask import Blueprint, request, jsonify, current_app

from routes.student.helpers import token_required, success_response, error_response
from services import leaderboard_service

leaderboard_bp = Blueprint("student_leaderboard", __name__)

DEFAULT_LIMIT = leaderboard_service.DEFAULT_LIMIT
MAX_LIMIT = leaderboard_service.MAX_LIMIT
DEFAULT_NEARBY_RANGE = leaderboard_service.DEFAULT_NEARBY_RANGE
MAX_NEARBY_RANGE = leaderboard_service.MAX_NEARBY_RANGE


def _validate_period_or_error(period: str):
    """Route-layer wrapper: converts the service's (period, error_message)
    tuple into a Flask error_response(...) when invalid."""
    period, err_msg = leaderboard_service.validate_period(period)
    if err_msg:
        return period, error_response(err_msg, 400)
    return period, None


# ─────────────────────────────────────────────────────────────────────────────
# 1. GLOBAL LEADERBOARD
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/global", methods=["GET"])
@token_required
def get_global_leaderboard(current_user):
    """
    Main leaderboard with period & department filtering.

    Query params:
      period      all_time | weekly | monthly | daily  (default: all_time)
      department  filter by department string           (optional)
      page        page number                           (default: 1)
      limit       results per page, max 50             (default: 20)
    """
    try:
        period = request.args.get("period", "all_time").strip()
        department = request.args.get("department", "").strip() or None
        page = max(request.args.get("page", 1, type=int), 1)
        limit = min(request.args.get("limit", DEFAULT_LIMIT, type=int), MAX_LIMIT)

        period, err = _validate_period_or_error(period)
        if err:
            return err

        data = leaderboard_service.get_global_leaderboard(
            period, department, page, limit, viewer_id=current_user.id
        )
        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Global leaderboard error: {e}")
        return error_response("Failed to load leaderboard")


# ─────────────────────────────────────────────────────────────────────────────
# 2. DEPARTMENT LEADERBOARD
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/department", methods=["GET"])
@token_required
def get_department_leaderboard(current_user):
    """
    Department-scoped leaderboard. Defaults to current user's department;
    overrideable via ?department=X.
    """
    try:
        period = request.args.get("period", "all_time").strip()
        page = max(request.args.get("page", 1, type=int), 1)
        limit = min(request.args.get("limit", DEFAULT_LIMIT, type=int), MAX_LIMIT)

        period, err = _validate_period_or_error(period)
        if err:
            return err

        dept_override = request.args.get("department", "").strip() or None
        if dept_override:
            department = dept_override
        else:
            from models import StudentProfile
            profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
            department = profile.department if profile else None

        if not department:
            return error_response(
                "Department not found. Set your department in profile or pass ?department=X", 400
            )

        data = leaderboard_service.get_department_leaderboard(
            period, department, page, limit, viewer_id=current_user.id
        )
        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Department leaderboard error: {e}")
        return error_response("Failed to load department leaderboard")


# ─────────────────────────────────────────────────────────────────────────────
# 3. MY RANK CARD
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/me", methods=["GET"])
@token_required
def get_my_rank(current_user):
    """Full rank card: rank, score breakdown, nearby users, streaks, progress."""
    try:
        period = request.args.get("period", "weekly").strip()
        department = request.args.get("department", "").strip() or None

        period, err = _validate_period_or_error(period)
        if err:
            return err

        try:
            data = leaderboard_service.get_my_rank(current_user.id, period, department)
        except LookupError as e:
            return error_response(str(e), 404)

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"My rank error: {e}")
        return error_response("Failed to load your rank")


# ─────────────────────────────────────────────────────────────────────────────
# 4. NEARBY USERS
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/nearby", methods=["GET"])
@token_required
def get_nearby_users(current_user):
    """
    Get users immediately surrounding the current user in the rankings.

    Query params:
      period      all_time | weekly | monthly | daily  (default: weekly)
      range       users above + below to show (1–10, default 3)
      department  optional department scope
    """
    try:
        period = request.args.get("period", "weekly").strip()
        n_range = min(request.args.get("range", DEFAULT_NEARBY_RANGE, type=int), MAX_NEARBY_RANGE)
        department = request.args.get("department", "").strip() or None

        period, err = _validate_period_or_error(period)
        if err:
            return err

        my_score = leaderboard_service.get_user_period_score(current_user.id, period)
        my_rank = leaderboard_service.get_user_rank(current_user.id, period, department)

        nearby = leaderboard_service.get_nearby_users(
            current_user.id, period=period, department=department, n_range=n_range
        )

        return jsonify({
            "status": "success",
            "data": {
                "period": period,
                "your_rank": my_rank,
                "your_score": my_score,
                "nearby": nearby,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Nearby users error: {e}")
        return error_response("Failed to load nearby users")


# ─────────────────────────────────────────────────────────────────────────────
# 5. CONNECTIONS LEADERBOARD
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/connections", methods=["GET"])
@token_required
def get_connections_leaderboard(current_user):
    """Leaderboard scoped to current user's accepted connections (+ self)."""
    try:
        period = request.args.get("period", "weekly").strip()
        period, err = _validate_period_or_error(period)
        if err:
            return err

        data = leaderboard_service.get_connections_leaderboard(current_user.id, period)
        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Connections leaderboard error: {e}")
        return error_response("Failed to load connections leaderboard")


# ─────────────────────────────────────────────────────────────────────────────
# 6. RISING STARS
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/rising", methods=["GET"])
@token_required
def get_rising_stars(current_user):
    """Users with the biggest reputation gain in the past 7 days."""
    try:
        limit = min(request.args.get("limit", 10, type=int), 30)
        department = request.args.get("department", "").strip() or None

        data = leaderboard_service.get_rising_stars(limit, department, viewer_id=current_user.id)
        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Rising stars error: {e}")
        return error_response("Failed to load rising stars")


# ─────────────────────────────────────────────────────────────────────────────
# 7. LEADERBOARD STATS
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/stats", methods=["GET"])
@token_required
def get_leaderboard_stats(current_user):
    """Platform-wide engagement statistics."""
    try:
        data = leaderboard_service.get_leaderboard_stats()
        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Leaderboard stats error: {e}")
        return error_response("Failed to load stats")


# ─────────────────────────────────────────────────────────────────────────────
# 8. FILTERS
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/filters", methods=["GET"])
@token_required
def get_leaderboard_filters(current_user):
    """Returns all valid filter options: departments, periods, user's defaults."""
    try:
        data = leaderboard_service.get_leaderboard_filters(current_user.id)
        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Leaderboard filters error: {e}")
        return error_response("Failed to load filters")


# ─────────────────────────────────────────────────────────────────────────────
# 9. RANK HISTORY
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/rank-history", methods=["GET"])
@token_required
def get_rank_history(current_user):
    """Returns current user's rank over the last N weekly snapshots."""
    try:
        weeks = min(request.args.get("weeks", 8, type=int), 26)
        data = leaderboard_service.get_rank_history(current_user.id, weeks)
        return jsonify({"status": "success", "data": data})

    except ImportError:
        return jsonify({
            "status": "success",
            "data": {"history": [], "weeks_back": 0,
                     "note": "Snapshots table not yet created. Run migration first."},
        })
    except Exception as e:
        current_app.logger.error(f"Rank history error: {e}")
        return error_response("Failed to load rank history")


# ─────────────────────────────────────────────────────────────────────────────
# 10. SNAPSHOT CREATION  (cron / admin endpoint)
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/snapshot", methods=["POST"])
@token_required
def create_snapshot(current_user):
    """
    Create a leaderboard snapshot of current all-time rankings.

    Admin/moderator-only. Calls the same services.leaderboard_service.
    take_snapshot() used by scheduler.py's weekly/monthly cron jobs
    (Document 1 §6.3 consolidation) — this route is just the manual
    on-demand trigger.

    Body (JSON): {"type": "weekly" | "monthly"}  (default: "weekly")
    """
    try:
        if current_user.role not in ("admin", "moderator"):
            return error_response("Admin access required", 403)

        data = request.get_json() or {}
        snapshot_type = data.get("type", "weekly")
        if snapshot_type not in {"weekly", "monthly"}:
            return error_response("Invalid snapshot type. Use: weekly, monthly", 400)

        result = leaderboard_service.take_snapshot(snapshot_type)

        return success_response(
            f"Snapshot created ({snapshot_type})",
            data=result,
        ), 201

    except Exception as e:
        current_app.logger.error(f"Snapshot creation error: {e}")
        return error_response("Failed to create snapshot")


# ─────────────────────────────────────────────────────────────────────────────
# 11. SCORE BREAKDOWN
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/breakdown", methods=["GET"])
@token_required
def get_score_breakdown(current_user):
    """Transparent breakdown of how the current user's score is composed."""
    try:
        period = request.args.get("period", "weekly").strip()
        period, err = _validate_period_or_error(period)
        if err:
            return err

        try:
            data = leaderboard_service.get_score_breakdown(current_user.id, period)
        except LookupError as e:
            return error_response(str(e), 404)

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Score breakdown error: {e}")
        return error_response("Failed to load score breakdown")
