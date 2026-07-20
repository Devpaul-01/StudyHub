"""
StudyHub – Leaderboard System
══════════════════════════════════════════════════════════════════════════════

Philosophy:
  Students care far more about beating someone 3 ranks above them than about
  the mythical #1 spot.  This system creates healthy addiction through:
    • Period-scoped ranking (daily/weekly resets level the playing field)
    • Nearby-user context (the core psychological hook)
    • Rank movement arrows (loss-aversion triggers daily check-ins)
    • Streak display (Duolingo-style consistency reward)
    • Connections leaderboard (social comparison with friends)
    • Rising-stars (gives new users visible momentum to chase)

Scoring philosophy:
  All-time  → User.reputation  (proven, accumulated value)
  Weekly    → SUM(ReputationHistory.points_change, 7d)
  Monthly   → SUM(ReputationHistory.points_change, 30d)
  Daily     → SUM(ReputationHistory.points_change, 1d)

  Period leaderboards let any motivated student climb regardless of when
  they joined, preventing veteran domination and keeping the board fresh.
"""

from __future__ import annotations

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func, desc, asc, and_, or_, case
import datetime
from datetime import timedelta

from models import (
    User, StudentProfile, ReputationHistory, UserActivity,
    Connection, UserBadge, Badge, WeeklyChampion, Notification,
)
from extensions import db
from routes.student.helpers import token_required, success_response, error_response
# H-8 fix: REPUTATION_LEVELS used to be duplicated here (and independently
# in badges.py, reputation.py, and models.py::User.update_reputation_level)
# — now imported from the single shared module.
from routes.student.reputation_levels import REPUTATION_LEVELS, get_reputation_level

leaderboard_bp = Blueprint("student_leaderboard", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

VALID_PERIODS = {"daily", "weekly", "monthly", "all_time"}
PERIOD_DAYS   = {"daily": 1, "weekly": 7, "monthly": 30}
DEFAULT_LIMIT = 20
MAX_LIMIT     = 50
DEFAULT_NEARBY_RANGE = 3
MAX_NEARBY_RANGE     = 10


# ─────────────────────────────────────────────────────────────────────────────
# PURE HELPERS  (no DB calls – fast, testable)
# ─────────────────────────────────────────────────────────────────────────────

def _period_start(period: str):
    """UTC start datetime for a period string; None for all_time."""
    days = PERIOD_DAYS.get(period)
    return datetime.datetime.utcnow() - timedelta(days=days) if days else None


def _rep_level(reputation: int) -> dict:
    """
    Return reputation-level dict for a reputation value.

    Thin local alias over the shared get_reputation_level() (H-8) — kept so
    every existing call site in this file (_rep_level(...)) didn't need to
    be touched.
    """
    return get_reputation_level(reputation)


def _validate_period(period: str):
    """Return (period, error_response | None)."""
    if period not in VALID_PERIODS:
        return period, error_response(
            f"Invalid period. Valid options: {', '.join(sorted(VALID_PERIODS))}", 400
        )
    return period, None


# ─────────────────────────────────────────────────────────────────────────────
# DB BATCH HELPERS  (batch-load to avoid N+1 queries)
# ─────────────────────────────────────────────────────────────────────────────

def _profile_map(user_ids: list) -> dict:
    """Batch-fetch {user_id → StudentProfile}."""
    if not user_ids:
        return {}
    profiles = StudentProfile.query.filter(StudentProfile.user_id.in_(user_ids)).all()
    return {p.user_id: p for p in profiles}


def _connection_map(current_user_id: int, user_ids: list) -> dict:
    """Batch-fetch {user_id → connection_status} relative to current_user."""
    if not user_ids:
        return {}
    conns = Connection.query.filter(
        or_(
            and_(Connection.requester_id == current_user_id, Connection.receiver_id.in_(user_ids)),
            and_(Connection.receiver_id == current_user_id, Connection.requester_id.in_(user_ids)),
        )
    ).all()
    result = {}
    for c in conns:
        other = c.receiver_id if c.requester_id == current_user_id else c.requester_id
        result[other] = c.status
    return result


def _user_map(user_ids: list) -> dict:
    """Batch-fetch {user_id → User}."""
    if not user_ids:
        return {}
    users = User.query.filter(User.id.in_(user_ids)).all()
    return {u.id: u for u in users}


def _old_rank_map(user_ids: list, snapshot_type: str = "weekly") -> dict:
    """
    Batch-fetch last week's global_rank for each user from LeaderboardSnapshot.
    Returns {user_id → old_global_rank}.
    Gracefully returns {} if the table doesn't exist yet (first deploy).
    """
    try:
        # Import here to avoid circular import issues if snapshot model is
        # added to models.py after initial migration
        from models import LeaderboardSnapshot  # noqa

        week_ago      = datetime.datetime.utcnow() - timedelta(days=6)
        two_weeks_ago = datetime.datetime.utcnow() - timedelta(days=15)

        snaps = (
            LeaderboardSnapshot.query
            .filter(
                LeaderboardSnapshot.user_id.in_(user_ids),
                LeaderboardSnapshot.snapshot_type == snapshot_type,
                LeaderboardSnapshot.created_at.between(two_weeks_ago, week_ago),
            )
            .order_by(LeaderboardSnapshot.created_at.desc())
            .all()
        )

        result = {}
        for s in snaps:
            if s.user_id not in result:
                result[s.user_id] = s.global_rank
        return result
    except Exception:
        return {}


def _top_badge(user_id: int) -> dict | None:
    """Return the user's highest-rarity badge (for leaderboard display card)."""
    RARITY_ORDER = {"legendary": 0, "epic": 1, "rare": 2, "common": 3}
    ub = (
        UserBadge.query
        .filter_by(user_id=user_id)
        .join(Badge, Badge.id == UserBadge.badge_id)
        .filter(Badge.is_active.is_(True))
        .order_by(
            case(RARITY_ORDER, value=Badge.rarity, else_=99).asc()
        )
        .first()
    )
    if not ub:
        return None
    b = Badge.query.get(ub.badge_id)
    if not b:
        return None
    return {"name": b.name, "icon": b.icon, "rarity": b.rarity}


# ─────────────────────────────────────────────────────────────────────────────
# SCORE QUERY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _alltime_rows(department: str | None, limit: int, offset: int):
    """
    Return (rows, total) for all-time leaderboard.
    Rows have .user_id and .score attributes.
    """
    q = (
        db.session.query(
            User.id.label("user_id"),
            User.reputation.label("score"),
        )
        .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
        .filter(User.status == "approved")
    )
    if department:
        q = q.filter(StudentProfile.department == department)

    total = q.count()
    rows  = q.order_by(desc("score"), asc(User.id)).limit(limit).offset(offset).all()
    return rows, total


def _period_rows(period: str, department: str | None, limit: int, offset: int):
    """
    Return (rows, total) for period-based leaderboard.
    Aggregates ReputationHistory.
    Rows have .user_id and .score attributes.
    """
    start = _period_start(period)

    # Base aggregation (only positive point events for period ranking;
    # negative points are still visible via total reputation on profile)
    # Using net sum (including negatives) so quality matters, not just volume.
    q = (
        db.session.query(
            ReputationHistory.user_id.label("user_id"),
            func.sum(ReputationHistory.points_change).label("score"),
        )
        .join(User, User.id == ReputationHistory.user_id)
        .outerjoin(StudentProfile, StudentProfile.user_id == ReputationHistory.user_id)
        .filter(
            ReputationHistory.created_at >= start,
            User.status == "approved",
        )
    )
    if department:
        q = q.filter(StudentProfile.department == department)

    q = q.group_by(ReputationHistory.user_id)

    # Total = number of users with activity in period
    subq  = q.subquery()
    total = db.session.query(func.count()).select_from(subq).scalar() or 0
    rows  = q.order_by(desc("score"), asc(ReputationHistory.user_id)).limit(limit).offset(offset).all()
    return rows, total


def _user_period_score(user_id: int, period: str) -> int:
    """Compute current user's score for a given period."""
    if period == "all_time":
        u = User.query.get(user_id)
        return u.reputation if u else 0
    start = _period_start(period)
    val = (
        db.session.query(func.sum(ReputationHistory.points_change))
        .filter(
            ReputationHistory.user_id == user_id,
            ReputationHistory.created_at >= start,
        )
        .scalar()
    )
    return int(val or 0)


def _user_rank_alltime(user_id: int, department: str | None = None) -> int:
    """Count approved users with higher all-time reputation → rank."""
    user = User.query.get(user_id)
    if not user:
        return 0
    q = (
        db.session.query(func.count(User.id))
        .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
        .filter(
            User.reputation > user.reputation,
            User.status == "approved",
        )
    )
    if department:
        q = q.filter(StudentProfile.department == department)
    return (q.scalar() or 0) + 1


def _user_rank_period(user_id: int, period: str, department: str | None = None) -> int:
    """Count users with higher period score → rank."""
    user_score = _user_period_score(user_id, period)
    start = _period_start(period)

    subq = (
        db.session.query(
            ReputationHistory.user_id,
            func.sum(ReputationHistory.points_change).label("total"),
        )
        .join(User, User.id == ReputationHistory.user_id)
        .outerjoin(StudentProfile, StudentProfile.user_id == ReputationHistory.user_id)
        .filter(
            ReputationHistory.created_at >= start,
            User.status == "approved",
        )
    )
    if department:
        subq = subq.filter(StudentProfile.department == department)
    subq = subq.group_by(ReputationHistory.user_id).subquery()

    count = (
        db.session.query(func.count())
        .filter(subq.c.total > user_score)
        .scalar()
    ) or 0
    return count + 1


def _get_user_rank(user_id: int, period: str, department: str | None = None) -> int:
    if period == "all_time":
        return _user_rank_alltime(user_id, department)
    return _user_rank_period(user_id, period, department)


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_entry(
    rank: int,
    user: User,
    profile,
    score: int,
    current_user_id: int,
    conn_map: dict,
    rank_change: int | None = None,
) -> dict:
    """Build a standardised leaderboard entry dict."""
    level = _rep_level(user.reputation)
    return {
        "rank":           rank,
        "rank_change":    rank_change,   # +N up, -N down, 0 stable, None = no history
        "connection_status": conn_map.get(user.id),
        "is_you":         user.id == current_user_id,
        "user": {
            "id":          user.id,
            "username":    user.username,
            "name":        user.name,
            "avatar":      user.avatar,
            "department":  profile.department if profile else None,
            "class_level": profile.class_name if profile else None,
        },
        "score": score,          # period score (or all-time rep for all_time)
        "reputation": {
            "total": user.reputation,
            "level": {
                "name":  level["name"],
                "icon":  level["icon"],
                "color": level["color"],
            },
        },
        "streaks": {
            "login_streak":       user.login_streak,
            "help_streak_current": user.help_streak_current,
        },
        "stats": {
            "total_posts":      user.total_posts,
            "total_helpful":    user.total_helpful,
            "total_helps_given": user.total_helps_given,
        },
    }


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
        period     = request.args.get("period", "all_time").strip()
        department = request.args.get("department", "").strip() or None
        page       = max(request.args.get("page", 1, type=int), 1)
        limit      = min(request.args.get("limit", DEFAULT_LIMIT, type=int), MAX_LIMIT)
        offset     = (page - 1) * limit

        period, err = _validate_period(period)
        if err:
            return err

        # ── Query rows & total ────────────────────────────────────────────────
        if period == "all_time":
            rows, total = _alltime_rows(department, limit, offset)
        else:
            rows, total = _period_rows(period, department, limit, offset)

        # ── Batch-load auxiliary data ─────────────────────────────────────────
        user_ids   = [r.user_id for r in rows]
        umap       = _user_map(user_ids)
        pmap       = _profile_map(user_ids)
        cmap       = _connection_map(current_user.id, user_ids)
        old_ranks  = _old_rank_map(user_ids)   # for rank-change arrows

        # ── Build entries ─────────────────────────────────────────────────────
        entries = []
        for i, row in enumerate(rows):
            user    = umap.get(row.user_id)
            if not user:
                continue
            rank        = offset + i + 1
            old_rank    = old_ranks.get(user.id)
            rank_change = (old_rank - rank) if old_rank else None

            entry = _build_entry(
                rank=rank,
                user=user,
                profile=pmap.get(user.id),
                score=int(row.score or 0),
                current_user_id=current_user.id,
                conn_map=cmap,
                rank_change=rank_change,
            )
            entries.append(entry)

        # ── Current user's position (shown even if not on this page) ──────────
        your_score      = _user_period_score(current_user.id, period)
        your_rank       = _get_user_rank(current_user.id, period, department)
        your_percentile = round(((total - your_rank + 1) / total) * 100, 1) if total > 0 else 0.0

        return jsonify({
            "status": "success",
            "data": {
                "leaderboard": entries,
                "period":      period,
                "department":  department,
                "pagination": {
                    "page":     page,
                    "limit":    limit,
                    "total":    total,
                    "has_more": (offset + limit) < total,
                },
                "your_position": {
                    "rank":        your_rank,
                    "score":       your_score,
                    "percentile":  your_percentile,
                    "total_users": total,
                },
            },
        })

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
    Department-scoped leaderboard.
    Defaults to current user's department; overrideable via ?department=X.

    Query params:
      department  override department string  (optional, defaults to user's dept)
      period      all_time | weekly | monthly | daily  (default: all_time)
      page        page number                          (default: 1)
      limit       max 50                              (default: 20)
    """
    try:
        period     = request.args.get("period", "all_time").strip()
        page       = max(request.args.get("page", 1, type=int), 1)
        limit      = min(request.args.get("limit", DEFAULT_LIMIT, type=int), MAX_LIMIT)
        offset     = (page - 1) * limit

        period, err = _validate_period(period)
        if err:
            return err

        # Resolve department
        dept_override = request.args.get("department", "").strip() or None
        if dept_override:
            department = dept_override
        else:
            profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
            department = profile.department if profile else None

        if not department:
            return error_response("Department not found. Set your department in profile or pass ?department=X", 400)

        if period == "all_time":
            rows, total = _alltime_rows(department, limit, offset)
        else:
            rows, total = _period_rows(period, department, limit, offset)

        user_ids = [r.user_id for r in rows]
        umap     = _user_map(user_ids)
        pmap     = _profile_map(user_ids)
        cmap     = _connection_map(current_user.id, user_ids)
        old_ranks = _old_rank_map(user_ids)

        entries = []
        for i, row in enumerate(rows):
            user = umap.get(row.user_id)
            if not user:
                continue
            rank        = offset + i + 1
            old_rank    = old_ranks.get(user.id)
            rank_change = (old_rank - rank) if old_rank else None
            entries.append(
                _build_entry(rank, user, pmap.get(user.id), int(row.score or 0),
                             current_user.id, cmap, rank_change)
            )

        your_score = _user_period_score(current_user.id, period)
        your_rank  = _get_user_rank(current_user.id, period, department)
        your_pct   = round(((total - your_rank + 1) / total) * 100, 1) if total > 0 else 0.0

        return jsonify({
            "status": "success",
            "data": {
                "leaderboard": entries,
                "department":  department,
                "period":      period,
                "pagination": {
                    "page":     page,
                    "limit":    limit,
                    "total":    total,
                    "has_more": (offset + limit) < total,
                },
                "your_position": {
                    "rank":        your_rank,
                    "score":       your_score,
                    "percentile":  your_pct,
                    "total_users": total,
                },
            },
        })

    except Exception as e:
        current_app.logger.error(f"Department leaderboard error: {e}")
        return error_response("Failed to load department leaderboard")


# ─────────────────────────────────────────────────────────────────────────────
# 3. MY RANK CARD  (most psychologically powerful endpoint)
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/me", methods=["GET"])
@token_required
def get_my_rank(current_user):
    """
    Full rank card for the current user: rank, score breakdown, nearby users,
    streaks, progress, and weekly champion status.

    This is the endpoint students will check obsessively because it shows:
      - Exact rank & percentile
      - How many points to next milestone
      - Direct competitors just above/below
      - Streak status
      - Rank movement since last week

    Query params:
      period      all_time | weekly | monthly | daily  (default: weekly)
      department  optional department scope
    """
    try:
        period     = request.args.get("period", "weekly").strip()
        department = request.args.get("department", "").strip() or None
        n_nearby   = 3  # show 3 above + 3 below

        period, err = _validate_period(period)
        if err:
            return err

        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found", 404)

        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
        dept    = department or (profile.department if profile else None)

        # ── Scores & rank ──────────────────────────────────────────────────────
        my_score    = _user_period_score(current_user.id, period)
        global_rank = _get_user_rank(current_user.id, period, None)
        dept_rank   = _get_user_rank(current_user.id, period, dept) if dept else None

        # Total eligible users
        if period == "all_time":
            total_global = db.session.query(func.count(User.id)).filter(User.status == "approved").scalar() or 1
        else:
            start = _period_start(period)
            subq  = (
                db.session.query(ReputationHistory.user_id)
                .join(User, User.id == ReputationHistory.user_id)
                .filter(ReputationHistory.created_at >= start, User.status == "approved")
                .group_by(ReputationHistory.user_id)
                .subquery()
            )
            total_global = db.session.query(func.count()).select_from(subq).scalar() or 1

        percentile = round(((total_global - global_rank + 1) / total_global) * 100, 1)

        # ── Rank movement (compare to last week's snapshot) ────────────────────
        rank_change = None
        try:
            from models import LeaderboardSnapshot
            week_ago      = datetime.datetime.utcnow() - timedelta(days=6)
            two_weeks_ago = datetime.datetime.utcnow() - timedelta(days=15)
            snap = (
                LeaderboardSnapshot.query
                .filter(
                    LeaderboardSnapshot.user_id == current_user.id,
                    LeaderboardSnapshot.snapshot_type == "weekly",
                    LeaderboardSnapshot.created_at.between(two_weeks_ago, week_ago),
                )
                .order_by(LeaderboardSnapshot.created_at.desc())
                .first()
            )
            if snap and snap.global_rank:
                rank_change = snap.global_rank - global_rank  # positive = moved up
        except Exception:
            pass

        # ── Score breakdown ────────────────────────────────────────────────────
        level        = _rep_level(user.reputation)
        next_thresh  = next(
            (lvl["min"] for lvl in REPUTATION_LEVELS if lvl["min"] > user.reputation), None
        )
        points_to_next = (next_thresh - user.reputation) if next_thresh else 0
        progress_pct   = 0
        if next_thresh:
            lvl_min = level["min"]
            lvl_range = next_thresh - lvl_min
            progress_pct = round(((user.reputation - lvl_min) / max(lvl_range, 1)) * 100, 1)

        # Weekly reputation gain
        weekly_start = datetime.datetime.utcnow() - timedelta(days=7)
        weekly_gain = (
            db.session.query(func.sum(ReputationHistory.points_change))
            .filter(
                ReputationHistory.user_id == current_user.id,
                ReputationHistory.created_at >= weekly_start,
                ReputationHistory.points_change > 0,
            )
            .scalar()
        ) or 0

        # Active days this month (for consistency score)
        month_start = datetime.datetime.utcnow() - timedelta(days=30)
        active_days = (
            db.session.query(func.count(UserActivity.id))
            .filter(
                UserActivity.user_id == current_user.id,
                UserActivity.activity_date >= month_start.date(),
                UserActivity.activity_score > 0,
            )
            .scalar()
        ) or 0

        # ── Nearby users ───────────────────────────────────────────────────────
        nearby = _get_nearby_for_user(
            user_id=current_user.id,
            my_score=my_score,
            my_rank=global_rank,
            period=period,
            department=None,
            n_range=n_nearby,
        )

        # Build nearby entries with connection info
        nearby_user_ids = [u["user"]["id"] for u in nearby]
        nearby_cmap     = _connection_map(current_user.id, nearby_user_ids)
        for entry in nearby:
            entry["connection_status"] = nearby_cmap.get(entry["user"]["id"])

        # ── Weekly champion status ──────────────────────────────────────────────
        champion_status = None
        today = datetime.date.today()
        champ = (
            WeeklyChampion.query
            .filter(
                WeeklyChampion.user_id == current_user.id,
                WeeklyChampion.week_end >= today,
            )
            .first()
        )
        if champ:
            champion_status = {
                "type":       champ.champion_type,
                "subject":    champ.subject,
                "help_count": champ.help_count,
            }

        return jsonify({
            "status": "success",
            "data": {
                "period":       period,
                "rank": {
                    "global":      global_rank,
                    "department":  dept_rank,
                    "department_name": dept,
                    "change":      rank_change,    # +N moved up, -N dropped, 0 stable, None no history
                    "percentile":  percentile,
                    "total_users": total_global,
                },
                "score": {
                    "period_score":  my_score,
                    "all_time":      user.reputation,
                    "weekly_gain":   int(weekly_gain),
                    "active_days_30d": active_days,
                },
                "level": {
                    "current":       level,
                    "points_to_next": points_to_next,
                    "progress_pct":   progress_pct,
                },
                "streaks": {
                    "login_streak":         user.login_streak,
                    "help_streak_current":  user.help_streak_current,
                    "help_streak_longest":  user.help_streak_longest,
                },
                "stats": {
                    "total_posts":       user.total_posts,
                    "total_helpful":     user.total_helpful,
                    "total_helps_given": user.total_helps_given,
                    "first_responder":   user.first_responder_count,
                },
                "nearby_users":     nearby,
                "weekly_champion":  champion_status,
            },
        })

    except Exception as e:
        current_app.logger.error(f"My rank error: {e}")
        return error_response("Failed to load your rank")


# ─────────────────────────────────────────────────────────────────────────────
# 4. NEARBY USERS  (public endpoint, heavily used)
# ─────────────────────────────────────────────────────────────────────────────

def _get_nearby_for_user(
    user_id: int,
    my_score: int,
    my_rank: int,
    period: str,
    department: str | None,
    n_range: int,
) -> list:
    """
    Core nearby-user computation.
    Returns a list of leaderboard entry dicts for users just above and below.
    Includes current user in the middle.
    """
    user    = User.query.get(user_id)
    profile = StudentProfile.query.filter_by(user_id=user_id).first() if user_id else None

    entries = []

    if period == "all_time":
        base_filter = [User.status == "approved", User.id != user_id]
        if department:
            base_filter.append(
                User.id.in_(
                    db.session.query(StudentProfile.user_id)
                    .filter(StudentProfile.department == department)
                )
            )

        # Users just ABOVE current user (smallest scores still greater than mine)
        above_rows = (
            db.session.query(User.id.label("user_id"), User.reputation.label("score"))
            .filter(User.reputation > my_score, *base_filter)
            .order_by(asc("score"), asc(User.id))
            .limit(n_range)
            .all()
        )
        above_rows = list(reversed(above_rows))  # flip to highest-first

        # Users just BELOW current user (highest scores still less than mine)
        below_rows = (
            db.session.query(User.id.label("user_id"), User.reputation.label("score"))
            .filter(User.reputation < my_score, *base_filter)
            .order_by(desc("score"), asc(User.id))
            .limit(n_range)
            .all()
        )

    else:
        start = _period_start(period)

        def _period_nearby_query(score_filter_op, order_by_col, lim):
            """Helper: aggregated nearby query for period mode."""
            q = (
                db.session.query(
                    ReputationHistory.user_id.label("user_id"),
                    func.sum(ReputationHistory.points_change).label("score"),
                )
                .join(User, User.id == ReputationHistory.user_id)
                .filter(
                    ReputationHistory.created_at >= start,
                    User.status == "approved",
                    User.id != user_id,
                )
            )
            if department:
                q = q.join(StudentProfile, StudentProfile.user_id == ReputationHistory.user_id)
                q = q.filter(StudentProfile.department == department)
            q = q.group_by(ReputationHistory.user_id).having(score_filter_op)
            q = q.order_by(order_by_col, asc(ReputationHistory.user_id)).limit(lim)
            return q.all()

        score_col = func.sum(ReputationHistory.points_change)
        above_rows = list(reversed(
            _period_nearby_query(score_col > my_score, asc(score_col), n_range)
        ))
        below_rows = _period_nearby_query(score_col < my_score, desc(score_col), n_range)

    # ── Batch-load all nearby users (2 queries instead of N*2) ──────────────
    all_nearby_ids = [r.user_id for r in above_rows] + [r.user_id for r in below_rows]
    nearby_umap = _user_map(all_nearby_ids)
    nearby_pmap = _profile_map(all_nearby_ids)

    # ── Assign ranks and build entries ────────────────────────────────────────
    rank_cursor = my_rank - len(above_rows)

    for i, row in enumerate(above_rows):
        u = nearby_umap.get(row.user_id)
        p = nearby_pmap.get(row.user_id)
        if u:
            entries.append(_build_entry(
                rank=rank_cursor + i,
                user=u,
                profile=p,
                score=int(row.score or 0),
                current_user_id=user_id,
                conn_map={},
            ))

    # Insert self
    if user:
        entries.append(_build_entry(
            rank=my_rank,
            user=user,
            profile=profile,
            score=my_score,
            current_user_id=user_id,
            conn_map={},
        ))

    for i, row in enumerate(below_rows):
        u = nearby_umap.get(row.user_id)
        p = nearby_pmap.get(row.user_id)
        if u:
            entries.append(_build_entry(
                rank=my_rank + i + 1,
                user=u,
                profile=p,
                score=int(row.score or 0),
                current_user_id=user_id,
                conn_map={},
            ))

    return entries


@leaderboard_bp.route("/leaderboard/nearby", methods=["GET"])
@token_required
def get_nearby_users(current_user):
    """
    Get users immediately surrounding the current user in the rankings.

    This is the most psychologically powerful endpoint. Students don't obsess
    over rank #1 — they obsess over the person 2 spots above them.

    Query params:
      period      all_time | weekly | monthly | daily  (default: weekly)
      range       users above + below to show (1–10, default 3)
      department  optional department scope
    """
    try:
        period     = request.args.get("period", "weekly").strip()
        n_range    = min(request.args.get("range", DEFAULT_NEARBY_RANGE, type=int), MAX_NEARBY_RANGE)
        department = request.args.get("department", "").strip() or None

        period, err = _validate_period(period)
        if err:
            return err

        my_score = _user_period_score(current_user.id, period)
        my_rank  = _get_user_rank(current_user.id, period, department)

        nearby = _get_nearby_for_user(
            user_id=current_user.id,
            my_score=my_score,
            my_rank=my_rank,
            period=period,
            department=department,
            n_range=n_range,
        )

        # Add connection statuses
        nearby_ids  = [e["user"]["id"] for e in nearby]
        cmap        = _connection_map(current_user.id, nearby_ids)
        for entry in nearby:
            entry["connection_status"] = cmap.get(entry["user"]["id"])

        return jsonify({
            "status": "success",
            "data": {
                "period":      period,
                "your_rank":   my_rank,
                "your_score":  my_score,
                "nearby":      nearby,
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
    """
    Leaderboard scoped to current user's accepted connections (+ self).

    Social comparison with known people is the most engaging form of competition.
    Students care more about beating their classmates than anonymous strangers.

    Query params:
      period  all_time | weekly | monthly | daily  (default: weekly)
    """
    try:
        period = request.args.get("period", "weekly").strip()
        period, err = _validate_period(period)
        if err:
            return err

        # Accepted connection IDs (both directions)
        conns = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.status == "accepted"),
                and_(Connection.receiver_id  == current_user.id, Connection.status == "accepted"),
            )
        ).all()

        friend_ids = set()
        for c in conns:
            friend_ids.add(
                c.receiver_id if c.requester_id == current_user.id else c.requester_id
            )
        friend_ids.add(current_user.id)  # always include self
        friend_ids_list = list(friend_ids)

        # ── Query scores for these users ──────────────────────────────────────
        if period == "all_time":
            rows = (
                db.session.query(User.id.label("user_id"), User.reputation.label("score"))
                .filter(User.id.in_(friend_ids_list), User.status == "approved")
                .order_by(desc("score"), asc(User.id))
                .all()
            )
        else:
            start = _period_start(period)
            rows = (
                db.session.query(
                    ReputationHistory.user_id.label("user_id"),
                    func.sum(ReputationHistory.points_change).label("score"),
                )
                .join(User, User.id == ReputationHistory.user_id)
                .filter(
                    ReputationHistory.user_id.in_(friend_ids_list),
                    ReputationHistory.created_at >= start,
                    User.status == "approved",
                )
                .group_by(ReputationHistory.user_id)
                .order_by(desc("score"), asc(ReputationHistory.user_id))
                .all()
            )

        # Users with 0 period activity aren't in rows – add them at bottom
        found_ids = {r.user_id for r in rows}
        missing   = [uid for uid in friend_ids_list if uid not in found_ids]
        zero_users = User.query.filter(User.id.in_(missing), User.status == "approved").all()

        umap  = _user_map([r.user_id for r in rows])
        pmap  = _profile_map(friend_ids_list)
        cmap  = _connection_map(current_user.id, friend_ids_list)

        entries = []
        for i, row in enumerate(rows):
            user = umap.get(row.user_id)
            if not user:
                continue
            entries.append(
                _build_entry(i + 1, user, pmap.get(user.id), int(row.score or 0),
                             current_user.id, cmap)
            )

        # Append zero-scorers at the end
        base_rank = len(entries) + 1
        for j, user in enumerate(zero_users):
            entries.append(
                _build_entry(base_rank + j, user, pmap.get(user.id), 0,
                             current_user.id, cmap)
            )

        # Find current user's position in this list
        your_entry = next((e for e in entries if e["is_you"]), None)

        return jsonify({
            "status": "success",
            "data": {
                "leaderboard":   entries,
                "period":        period,
                "total_friends": len(entries),
                "your_rank":     your_entry["rank"] if your_entry else None,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Connections leaderboard error: {e}")
        return error_response("Failed to load connections leaderboard")


# ─────────────────────────────────────────────────────────────────────────────
# 6. RISING STARS  (momentum leaderboard)
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/rising", methods=["GET"])
@token_required
def get_rising_stars(current_user):
    """
    Users with the biggest reputation gain in the past 7 days.

    Gives new or previously-inactive students visible momentum to compete
    with, preventing veteran dominance and making the board feel freshly
    contested every week.

    Query params:
      limit       max 30  (default: 10)
      department  optional department filter
    """
    try:
        limit      = min(request.args.get("limit", 10, type=int), 30)
        department = request.args.get("department", "").strip() or None
        week_ago   = datetime.datetime.utcnow() - timedelta(days=7)

        q = (
            db.session.query(
                ReputationHistory.user_id.label("user_id"),
                func.sum(ReputationHistory.points_change).label("weekly_gain"),
                User.username,
                User.name,
                User.avatar,
                User.reputation,
                User.reputation_level,
                User.login_streak,
                User.help_streak_current,
                StudentProfile.department,
                StudentProfile.class_name,
            )
            .join(User, User.id == ReputationHistory.user_id)
            .outerjoin(StudentProfile, StudentProfile.user_id == ReputationHistory.user_id)
            .filter(
                ReputationHistory.created_at >= week_ago,
                ReputationHistory.points_change > 0,
                User.status == "approved",
            )
        )
        if department:
            q = q.filter(StudentProfile.department == department)

        rows = (
            q.group_by(
                ReputationHistory.user_id,
                User.username, User.name, User.avatar,
                User.reputation, User.reputation_level,
                User.login_streak, User.help_streak_current,
                StudentProfile.department, StudentProfile.class_name,
            )
            .order_by(desc("weekly_gain"))
            .limit(limit)
            .all()
        )

        rising_ids = [r.user_id for r in rows]
        cmap = _connection_map(current_user.id, rising_ids)
        level_cache = {}

        data = []
        for idx, row in enumerate(rows, start=1):
            rep   = row.reputation or 0
            level = level_cache.get(rep) or _rep_level(rep)
            level_cache[rep] = level

            data.append({
                "rank":       idx,
                "weekly_gain": int(row.weekly_gain or 0),
                "is_you":     row.user_id == current_user.id,
                "connection_status": cmap.get(row.user_id),
                "user": {
                    "id":          row.user_id,
                    "username":    row.username,
                    "name":        row.name,
                    "avatar":      row.avatar,
                    "department":  row.department,
                    "class_level": row.class_name,
                },
                "reputation": {
                    "total": rep,
                    "level": {"name": level["name"], "icon": level["icon"], "color": level["color"]},
                },
                "streaks": {
                    "login_streak":        row.login_streak or 0,
                    "help_streak_current": row.help_streak_current or 0,
                },
            })

        return jsonify({
            "status": "success",
            "data": {
                "rising_stars": data,
                "period_days":  7,
                "department":   department,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Rising stars error: {e}")
        return error_response("Failed to load rising stars")


# ─────────────────────────────────────────────────────────────────────────────
# 7. LEADERBOARD STATS
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/stats", methods=["GET"])
@token_required
def get_leaderboard_stats(current_user):
    """
    Platform-wide engagement statistics.
    Useful for dashboards and making students feel part of an active community.
    """
    try:
        week_ago   = datetime.datetime.utcnow() - timedelta(days=7)
        month_ago  = datetime.datetime.utcnow() - timedelta(days=30)

        # Total approved users
        total_users = db.session.query(func.count(User.id)).filter(User.status == "approved").scalar() or 0

        # Active users this week
        active_week = (
            db.session.query(func.count(func.distinct(ReputationHistory.user_id)))
            .join(User, User.id == ReputationHistory.user_id)
            .filter(
                ReputationHistory.created_at >= week_ago,
                User.status == "approved",
            )
            .scalar()
        ) or 0

        # Total reputation earned this week
        week_rep = (
            db.session.query(func.sum(ReputationHistory.points_change))
            .filter(
                ReputationHistory.created_at >= week_ago,
                ReputationHistory.points_change > 0,
            )
            .scalar()
        ) or 0

        # Average reputation of all students
        avg_rep = db.session.query(func.avg(User.reputation)).filter(User.status == "approved").scalar()
        avg_rep = round(float(avg_rep or 0), 1)

        # Most competitive department (highest sum of reputation)
        top_dept_row = (
            db.session.query(
                StudentProfile.department,
                func.sum(User.reputation).label("dept_rep"),
                func.count(User.id).label("member_count"),
            )
            .join(User, User.id == StudentProfile.user_id)
            .filter(User.status == "approved", StudentProfile.department.isnot(None))
            .group_by(StudentProfile.department)
            .order_by(desc("dept_rep"))
            .first()
        )

        top_department = None
        if top_dept_row:
            top_department = {
                "name":         top_dept_row.department,
                "total_rep":    int(top_dept_row.dept_rep or 0),
                "member_count": int(top_dept_row.member_count or 0),
            }

        # This week's top gainer (different from global top)
        top_gainer_row = (
            db.session.query(
                ReputationHistory.user_id,
                func.sum(ReputationHistory.points_change).label("gain"),
            )
            .join(User, User.id == ReputationHistory.user_id)
            .filter(
                ReputationHistory.created_at >= week_ago,
                ReputationHistory.points_change > 0,
                User.status == "approved",
            )
            .group_by(ReputationHistory.user_id)
            .order_by(desc("gain"))
            .first()
        )

        top_gainer = None
        if top_gainer_row:
            u = User.query.get(top_gainer_row.user_id)
            if u:
                top_gainer = {
                    "user_id":     u.id,
                    "name":        u.name,
                    "username":    u.username,
                    "avatar":      u.avatar,
                    "weekly_gain": int(top_gainer_row.gain or 0),
                }

        # Overall top scorer (all-time)
        top_all_time = (
            db.session.query(User)
            .filter(User.status == "approved")
            .order_by(desc(User.reputation))
            .first()
        )
        top_scorer = None
        if top_all_time:
            level = _rep_level(top_all_time.reputation)
            top_scorer = {
                "user_id":    top_all_time.id,
                "name":       top_all_time.name,
                "username":   top_all_time.username,
                "avatar":     top_all_time.avatar,
                "reputation": top_all_time.reputation,
                "level":      level["name"],
            }

        return jsonify({
            "status": "success",
            "data": {
                "total_students":      total_users,
                "active_this_week":    active_week,
                "week_rep_earned":     int(week_rep),
                "avg_reputation":      avg_rep,
                "top_department":      top_department,
                "top_gainer_this_week": top_gainer,
                "top_scorer_all_time": top_scorer,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Leaderboard stats error: {e}")
        return error_response("Failed to load stats")


# ─────────────────────────────────────────────────────────────────────────────
# 8. FILTERS  (tell frontend what options are available)
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/filters", methods=["GET"])
@token_required
def get_leaderboard_filters(current_user):
    """
    Returns all valid filter options: departments, periods, user's defaults.
    Frontend uses this to populate filter dropdowns without hardcoding.
    """
    try:
        departments = (
            db.session.query(StudentProfile.department)
            .join(User, User.id == StudentProfile.user_id)
            .filter(User.status == "approved", StudentProfile.department.isnot(None))
            .group_by(StudentProfile.department)
            .order_by(StudentProfile.department.asc())
            .all()
        )
        dept_list = [row[0] for row in departments]

        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
        my_dept = profile.department if profile else None

        periods = [
            {"key": "daily",    "label": "Today",      "description": "Points earned in last 24 hours"},
            {"key": "weekly",   "label": "This Week",  "description": "Points earned in last 7 days"},
            {"key": "monthly",  "label": "This Month", "description": "Points earned in last 30 days"},
            {"key": "all_time", "label": "All Time",   "description": "Total lifetime reputation"},
        ]

        return jsonify({
            "status": "success",
            "data": {
                "periods":     periods,
                "departments": dept_list,
                "your_department": my_dept,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Leaderboard filters error: {e}")
        return error_response("Failed to load filters")


# ─────────────────────────────────────────────────────────────────────────────
# 9. RANK HISTORY  (chart data for user's rank over time)
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/rank-history", methods=["GET"])
@token_required
def get_rank_history(current_user):
    """
    Returns current user's rank over the last N weekly snapshots.
    Powers the rank-over-time chart on the profile page.

    Query params:
      weeks   how many past snapshots to return (default 8, max 26)
    """
    try:
        from models import LeaderboardSnapshot

        weeks = min(request.args.get("weeks", 8, type=int), 26)

        snaps = (
            LeaderboardSnapshot.query
            .filter(
                LeaderboardSnapshot.user_id == current_user.id,
                LeaderboardSnapshot.snapshot_type == "weekly",
            )
            .order_by(LeaderboardSnapshot.created_at.desc())
            .limit(weeks)
            .all()
        )
        snaps = list(reversed(snaps))  # chronological order

        history = [
            {
                "date":       s.created_at.strftime("%Y-%m-%d"),
                "global_rank": s.global_rank,
                "dept_rank":   s.department_rank,
                "score":       s.score,
            }
            for s in snaps
        ]

        return jsonify({
            "status": "success",
            "data": {
                "history":    history,
                "weeks_back": weeks,
            },
        })

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
    Call this weekly via a cron job or admin panel.

    Only admins or the system should call this.  The snapshot data powers
    rank-movement arrows across the entire leaderboard.

    Body (JSON):
      type    "weekly" | "monthly"  (default: "weekly")

    Security note: add an admin role check before deploying to production.
    """
    try:
        if current_user.role not in ("admin", "moderator"):
            return error_response("Admin access required", 403)

        data          = request.get_json() or {}
        snapshot_type = data.get("type", "weekly")
        if snapshot_type not in {"weekly", "monthly"}:
            return error_response("Invalid snapshot type. Use: weekly, monthly", 400)

        from models import LeaderboardSnapshot

        # Build all-time rankings (only approved users, ordered by reputation)
        ranked = (
            db.session.query(
                User.id,
                User.reputation,
            )
            .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
            .filter(User.status == "approved")
            .order_by(desc(User.reputation), asc(User.id))
            .all()
        )

        # Build per-department ranks in one pass
        dept_rows = (
            db.session.query(
                User.id,
                StudentProfile.department,
            )
            .join(StudentProfile, StudentProfile.user_id == User.id)
            .filter(User.status == "approved", StudentProfile.department.isnot(None))
            .order_by(StudentProfile.department, desc(User.reputation), asc(User.id))
            .all()
        )

        # Compute dept ranks
        dept_rank_map = {}  # {user_id: dept_rank}
        dept_counters = {}
        for uid, dept in dept_rows:
            if dept not in dept_counters:
                dept_counters[dept] = 0
            dept_counters[dept] += 1
            dept_rank_map[uid] = dept_counters[dept]

        now        = datetime.datetime.utcnow()
        created    = 0
        skipped    = 0

        # Pre-fetch all user IDs that already have a snapshot today for this type.
        # This avoids an N+1 SELECT inside the loop below.
        today_snapped = {
            row.user_id
            for row in (
                LeaderboardSnapshot.query
                .filter(
                    LeaderboardSnapshot.snapshot_type == snapshot_type,
                    func.date(LeaderboardSnapshot.created_at) == now.date(),
                )
                .with_entities(LeaderboardSnapshot.user_id)
                .all()
            )
        }

        for global_rank, (uid, reputation) in enumerate(ranked, start=1):
            if uid in today_snapped:
                skipped += 1
                continue

            snap = LeaderboardSnapshot(
                user_id=uid,
                snapshot_type=snapshot_type,
                global_rank=global_rank,
                department_rank=dept_rank_map.get(uid),
                score=reputation,
                created_at=now,
            )
            db.session.add(snap)
            created += 1

        db.session.commit()

        return success_response(
            f"Snapshot created ({snapshot_type})",
            data={"created": created, "skipped": skipped, "total_ranked": len(ranked)},
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Snapshot creation error: {e}")
        return error_response("Failed to create snapshot")


# ─────────────────────────────────────────────────────────────────────────────
# 11. SCORE BREAKDOWN  (transparent scoring – builds trust)
# ─────────────────────────────────────────────────────────────────────────────

@leaderboard_bp.route("/leaderboard/breakdown", methods=["GET"])
@token_required
def get_score_breakdown(current_user):
    """
    Transparent breakdown of how the current user's score is composed.
    Transparency builds trust; users are more motivated when they understand
    exactly what actions move the needle.

    Query params:
      period  all_time | weekly | monthly | daily  (default: weekly)
    """
    try:
        period = request.args.get("period", "weekly").strip()
        period, err = _validate_period(period)
        if err:
            return err

        user    = User.query.get(current_user.id)
        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()

        # Period reputation history
        history_q = ReputationHistory.query.filter_by(user_id=current_user.id)
        if period != "all_time":
            history_q = history_q.filter(ReputationHistory.created_at >= _period_start(period))

        history_rows = history_q.order_by(ReputationHistory.created_at.desc()).limit(50).all()

        # Group by action type
        by_action = {}
        total_positive = 0
        total_negative = 0
        for h in history_rows:
            action = h.action
            by_action.setdefault(action, {"count": 0, "total_points": 0})
            by_action[action]["count"]        += 1
            by_action[action]["total_points"] += h.points_change
            if h.points_change > 0:
                total_positive += h.points_change
            else:
                total_negative += h.points_change

        # Recent activity (last 10 events)
        recent = [
            {
                "action":         h.action,
                "points_change":  h.points_change,
                "created_at":     h.created_at.isoformat(),
                "related_type":   h.related_type,
                "related_id":     h.related_id,
            }
            for h in history_rows[:10]
        ]

        # Activity streak bonus explanation
        consistency_bonus_explanation = (
            f"Your {user.login_streak}-day login streak + "
            f"{user.help_streak_current}-day help streak contribute "
            "to your display momentum badge."
        )

        return jsonify({
            "status": "success",
            "data": {
                "period":            period,
                "total_period_score": total_positive + total_negative,
                "total_positive":    total_positive,
                "total_negative":    total_negative,
                "by_action":         by_action,
                "recent_events":     recent,
                "all_time_rep":      user.reputation,
                "level":             _rep_level(user.reputation),
                "streaks": {
                    "login_streak":         user.login_streak,
                    "help_streak_current":  user.help_streak_current,
                    "help_streak_longest":  user.help_streak_longest,
                },
                "consistency_note": consistency_bonus_explanation,
                "scoring_tips": [
                    "💡 Answers marked as solutions earn 15 pts",
                    "🔥 7-day help streak earns a bonus 10 pts",
                    "⚡ Helpful comments earn 3 pts each",
                    "📝 Posts reaching 10 likes earn 5 pts",
                    "🏆 Posts reaching 50 likes earn 20 pts",
                ],
            },
        })

    except Exception as e:
        current_app.logger.error(f"Score breakdown error: {e}")
        return error_response("Failed to load score breakdown")
