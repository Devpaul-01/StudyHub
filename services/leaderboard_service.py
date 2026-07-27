"""
services/leaderboard_service.py

All leaderboard computation: global/department/period rankings, nearby-user
context, connections leaderboard, rising stars, snapshot creation, and score
breakdown. Extracted from routes/student/leaderboard.py (Document 1 §2,
Document 2 §3.3), plus the snapshot-consolidation described in Document 1
§6.3 (this becomes the SOLE snapshot implementation — scheduler.py's
_take_snapshot and leaderboard.py's manual admin endpoint both become thin
callers of take_snapshot() below).

Per Document 2 §2's layering rule: no Flask imports, no request/session/g.
`viewer_id`/`current_user_id` are threaded through as explicit parameters
instead of read from Flask context — this is the pattern used consistently
across every service in this migration, so this module has zero Flask
dependency despite needing "who is asking" for connection-status annotation.

Bug fix bundled in per Document 1 §4 point 5: _old_rank_map's bare
`except Exception: return {}` is narrowed to `except (ImportError, ...)`
so a genuine bug elsewhere doesn't get silently swallowed as "no snapshot
data" — see the function below for the exact exception set used, chosen
because LeaderboardSnapshot may not exist yet (ImportError) or the table
may not exist yet at the DB level (OperationalError-equivalent, caught via
SQLAlchemyError since the DB-API-specific exception varies by backend).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, desc, asc, and_, or_, case
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import (
    User, StudentProfile, ReputationHistory, UserActivity,
    Connection, UserBadge, Badge, WeeklyChampion,
)
from routes.student.reputation_levels import REPUTATION_LEVELS, get_reputation_level


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

VALID_PERIODS = {"daily", "weekly", "monthly", "all_time"}
PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
DEFAULT_NEARBY_RANGE = 3
MAX_NEARBY_RANGE = 10


# ─────────────────────────────────────────────────────────────────────────────
# RESULT DATACLASSES  (Document 2 §3.3 — replace ad hoc dict shapes)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SnapshotResult:
    created: int
    skipped: int
    total_ranked: int

    def to_dict(self) -> dict:
        return {"created": self.created, "skipped": self.skipped, "total_ranked": self.total_ranked}


@dataclass
class LeaderboardPage:
    entries: list
    period: str
    pagination: dict
    your_position: dict
    department: str | None = None

    def to_dict(self) -> dict:
        d = {
            "leaderboard": self.entries,
            "period": self.period,
            "pagination": self.pagination,
            "your_position": self.your_position,
        }
        if self.department is not None:
            d["department"] = self.department
        return d


# ─────────────────────────────────────────────────────────────────────────────
# PURE HELPERS  (no DB calls)
# ─────────────────────────────────────────────────────────────────────────────

def _period_start(period: str):
    """UTC start datetime for a period string; None for all_time."""
    days = PERIOD_DAYS.get(period)
    return datetime.datetime.utcnow() - timedelta(days=days) if days else None


def rep_level(reputation: int) -> dict:
    """Thin alias over the shared get_reputation_level(), kept for call-site brevity."""
    return get_reputation_level(reputation)


def validate_period(period: str) -> tuple[str, str | None]:
    """Return (period, error_message | None). Route layer converts the
    error message into whatever response shape it uses (error_response /
    raised ValidationError)."""
    if period not in VALID_PERIODS:
        return period, f"Invalid period. Valid options: {', '.join(sorted(VALID_PERIODS))}"
    return period, None


# ─────────────────────────────────────────────────────────────────────────────
# DB BATCH HELPERS  (batch-load to avoid N+1 queries)
# ─────────────────────────────────────────────────────────────────────────────

def _profile_map(user_ids: list) -> dict:
    """Batch-fetch {user_id -> StudentProfile}."""
    if not user_ids:
        return {}
    profiles = StudentProfile.query.filter(StudentProfile.user_id.in_(user_ids)).all()
    return {p.user_id: p for p in profiles}


def _connection_map(current_user_id: int, user_ids: list) -> dict:
    """Batch-fetch {user_id -> connection_status} relative to current_user."""
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
    """Batch-fetch {user_id -> User}."""
    if not user_ids:
        return {}
    users = User.query.filter(User.id.in_(user_ids)).all()
    return {u.id: u for u in users}


def _old_rank_map(user_ids: list, snapshot_type: str = "weekly") -> dict:
    """
    Batch-fetch last week's global_rank for each user from LeaderboardSnapshot.
    Returns {user_id -> old_global_rank}.

    Document 1 §4 point 5 fix: narrowed from a bare `except Exception` to
    the specific failure modes this can actually hit — the snapshot model
    not existing yet (ImportError, e.g. pre-migration) or a DB-level
    failure querying it (SQLAlchemyError, e.g. table not created yet). A
    genuine bug elsewhere (a NameError from a typo, say) is no longer
    silently swallowed as "no snapshot data."
    """
    try:
        from models import LeaderboardSnapshot  # noqa

        week_ago = datetime.datetime.utcnow() - timedelta(days=6)
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
    except (ImportError, SQLAlchemyError) as exc:
        import logging
        logging.getLogger(__name__).warning(f"_old_rank_map unavailable: {exc}")
        return {}


def _top_badge(user_id: int) -> dict | None:
    """Return the user's highest-rarity badge (for leaderboard display card)."""
    RARITY_ORDER = {"legendary": 0, "epic": 1, "rare": 2, "common": 3}
    ub = (
        UserBadge.query
        .filter_by(user_id=user_id)
        .join(Badge, Badge.id == UserBadge.badge_id)
        .filter(Badge.is_active.is_(True))
        .order_by(case(RARITY_ORDER, value=Badge.rarity, else_=99).asc())
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
    """Return (rows, total) for all-time leaderboard. Rows have .user_id/.score."""
    q = (
        db.session.query(User.id.label("user_id"), User.reputation.label("score"))
        .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
        .filter(User.status == "approved")
    )
    if department:
        q = q.filter(StudentProfile.department == department)

    total = q.count()
    rows = q.order_by(desc("score"), asc(User.id)).limit(limit).offset(offset).all()
    return rows, total


def _period_rows(period: str, department: str | None, limit: int, offset: int):
    """Return (rows, total) for period-based leaderboard (aggregates ReputationHistory)."""
    start = _period_start(period)

    q = (
        db.session.query(
            ReputationHistory.user_id.label("user_id"),
            func.sum(ReputationHistory.points_change).label("score"),
        )
        .join(User, User.id == ReputationHistory.user_id)
        .outerjoin(StudentProfile, StudentProfile.user_id == ReputationHistory.user_id)
        .filter(ReputationHistory.created_at >= start, User.status == "approved")
    )
    if department:
        q = q.filter(StudentProfile.department == department)

    q = q.group_by(ReputationHistory.user_id)

    subq = q.subquery()
    total = db.session.query(func.count()).select_from(subq).scalar() or 0
    rows = q.order_by(desc("score"), asc(ReputationHistory.user_id)).limit(limit).offset(offset).all()
    return rows, total


def _user_period_score(user_id: int, period: str) -> int:
    """Compute a user's score for a given period."""
    if period == "all_time":
        u = User.query.get(user_id)
        return u.reputation if u else 0
    start = _period_start(period)
    val = (
        db.session.query(func.sum(ReputationHistory.points_change))
        .filter(ReputationHistory.user_id == user_id, ReputationHistory.created_at >= start)
        .scalar()
    )
    return int(val or 0)


def _user_rank_alltime(user_id: int, department: str | None = None) -> int:
    """Count approved users with higher all-time reputation -> rank."""
    user = User.query.get(user_id)
    if not user:
        return 0
    q = (
        db.session.query(func.count(User.id))
        .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
        .filter(User.reputation > user.reputation, User.status == "approved")
    )
    if department:
        q = q.filter(StudentProfile.department == department)
    return (q.scalar() or 0) + 1


def _user_rank_period(user_id: int, period: str, department: str | None = None) -> int:
    """Count users with higher period score -> rank."""
    user_score = _user_period_score(user_id, period)
    start = _period_start(period)

    subq = (
        db.session.query(
            ReputationHistory.user_id,
            func.sum(ReputationHistory.points_change).label("total"),
        )
        .join(User, User.id == ReputationHistory.user_id)
        .outerjoin(StudentProfile, StudentProfile.user_id == ReputationHistory.user_id)
        .filter(ReputationHistory.created_at >= start, User.status == "approved")
    )
    if department:
        subq = subq.filter(StudentProfile.department == department)
    subq = subq.group_by(ReputationHistory.user_id).subquery()

    count = db.session.query(func.count()).filter(subq.c.total > user_score).scalar() or 0
    return count + 1


def get_user_rank(user_id: int, period: str, department: str | None = None) -> int:
    if period == "all_time":
        return _user_rank_alltime(user_id, department)
    return _user_rank_period(user_id, period, department)


def get_user_period_score(user_id: int, period: str) -> int:
    """Public wrapper over _user_period_score — the one route callers
    (leaderboard.py::get_nearby_users) should use instead of reaching into
    the underscore-prefixed internal helper directly."""
    return _user_period_score(user_id, period)


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
    level = rep_level(user.reputation)
    return {
        "rank": rank,
        "rank_change": rank_change,
        "connection_status": conn_map.get(user.id),
        "is_you": user.id == current_user_id,
        "user": {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "avatar": user.avatar,
            "department": profile.department if profile else None,
            "class_level": profile.class_name if profile else None,
        },
        "score": score,
        "reputation": {
            "total": user.reputation,
            "level": {"name": level["name"], "icon": level["icon"], "color": level["color"]},
        },
        "streaks": {
            "login_streak": user.login_streak,
            "help_streak_current": user.help_streak_current,
        },
        "stats": {
            "total_posts": user.total_posts,
            "total_helpful": user.total_helpful,
            "total_helps_given": user.total_helps_given,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. GLOBAL / DEPARTMENT LEADERBOARD
# ─────────────────────────────────────────────────────────────────────────────

def get_global_leaderboard(
    period: str, department: str | None, page: int, limit: int, *, viewer_id: int
) -> dict:
    """Main leaderboard with period & department filtering. Returns a dict
    (LeaderboardPage.to_dict()) ready for jsonify."""
    limit = min(limit, MAX_LIMIT)
    offset = (page - 1) * limit

    if period == "all_time":
        rows, total = _alltime_rows(department, limit, offset)
    else:
        rows, total = _period_rows(period, department, limit, offset)

    user_ids = [r.user_id for r in rows]
    umap = _user_map(user_ids)
    pmap = _profile_map(user_ids)
    cmap = _connection_map(viewer_id, user_ids)
    old_ranks = _old_rank_map(user_ids)

    entries = []
    for i, row in enumerate(rows):
        user = umap.get(row.user_id)
        if not user:
            continue
        rank = offset + i + 1
        old_rank = old_ranks.get(user.id)
        rank_change = (old_rank - rank) if old_rank else None
        entries.append(_build_entry(rank, user, pmap.get(user.id), int(row.score or 0), viewer_id, cmap, rank_change))

    your_score = _user_period_score(viewer_id, period)
    your_rank = get_user_rank(viewer_id, period, department)
    your_percentile = round(((total - your_rank + 1) / total) * 100, 1) if total > 0 else 0.0

    return LeaderboardPage(
        entries=entries,
        period=period,
        department=department,
        pagination={
            "page": page, "limit": limit, "total": total,
            "has_more": (offset + limit) < total,
        },
        your_position={
            "rank": your_rank, "score": your_score,
            "percentile": your_percentile, "total_users": total,
        },
    ).to_dict()


def get_department_leaderboard(
    period: str, department: str, page: int, limit: int, *, viewer_id: int
) -> dict:
    """Department-scoped leaderboard. `department` must already be resolved
    by the caller (defaults to viewer's own department at the route layer)."""
    return get_global_leaderboard(period, department, page, limit, viewer_id=viewer_id)


# ─────────────────────────────────────────────────────────────────────────────
# 2. NEARBY USERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_nearby_for_user(
    user_id: int, my_score: int, my_rank: int, period: str,
    department: str | None, n_range: int,
) -> list:
    """Core nearby-user computation. Includes the viewer themself in the middle."""
    user = User.query.get(user_id)
    profile = StudentProfile.query.filter_by(user_id=user_id).first() if user_id else None

    entries = []

    if period == "all_time":
        base_filter = [User.status == "approved", User.id != user_id]
        if department:
            base_filter.append(
                User.id.in_(
                    db.session.query(StudentProfile.user_id).filter(StudentProfile.department == department)
                )
            )

        above_rows = (
            db.session.query(User.id.label("user_id"), User.reputation.label("score"))
            .filter(User.reputation > my_score, *base_filter)
            .order_by(asc("score"), asc(User.id))
            .limit(n_range)
            .all()
        )
        above_rows = list(reversed(above_rows))

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
        above_rows = list(reversed(_period_nearby_query(score_col > my_score, asc(score_col), n_range)))
        below_rows = _period_nearby_query(score_col < my_score, desc(score_col), n_range)

    all_nearby_ids = [r.user_id for r in above_rows] + [r.user_id for r in below_rows]
    nearby_umap = _user_map(all_nearby_ids)
    nearby_pmap = _profile_map(all_nearby_ids)

    rank_cursor = my_rank - len(above_rows)

    for i, row in enumerate(above_rows):
        u = nearby_umap.get(row.user_id)
        p = nearby_pmap.get(row.user_id)
        if u:
            entries.append(_build_entry(rank_cursor + i, u, p, int(row.score or 0), user_id, {}))

    if user:
        entries.append(_build_entry(my_rank, user, profile, my_score, user_id, {}))

    for i, row in enumerate(below_rows):
        u = nearby_umap.get(row.user_id)
        p = nearby_pmap.get(row.user_id)
        if u:
            entries.append(_build_entry(my_rank + i + 1, u, p, int(row.score or 0), user_id, {}))

    return entries


def get_nearby_users(
    user_id: int, period: str = "weekly", department: str | None = None, n_range: int = DEFAULT_NEARBY_RANGE
) -> list:
    """Users immediately surrounding `user_id` in the rankings, with
    connection-status annotated in (this is the route-facing entry point;
    _get_nearby_for_user is also called internally by get_my_rank)."""
    n_range = min(n_range, MAX_NEARBY_RANGE)
    my_score = _user_period_score(user_id, period)
    my_rank = get_user_rank(user_id, period, department)

    nearby = _get_nearby_for_user(user_id, my_score, my_rank, period, department, n_range)

    nearby_ids = [e["user"]["id"] for e in nearby]
    cmap = _connection_map(user_id, nearby_ids)
    for entry in nearby:
        entry["connection_status"] = cmap.get(entry["user"]["id"])

    return nearby


# ─────────────────────────────────────────────────────────────────────────────
# 3. MY RANK CARD
# ─────────────────────────────────────────────────────────────────────────────

def get_my_rank(user_id: int, period: str = "weekly", department: str | None = None) -> dict:
    """Full rank card: rank, score breakdown, nearby users, streaks,
    progress, and weekly champion status."""
    n_nearby = 3

    user = User.query.get(user_id)
    if not user:
        raise LookupError("User not found")

    profile = StudentProfile.query.filter_by(user_id=user_id).first()
    dept = department or (profile.department if profile else None)

    my_score = _user_period_score(user_id, period)
    global_rank = get_user_rank(user_id, period, None)
    dept_rank = get_user_rank(user_id, period, dept) if dept else None

    if period == "all_time":
        total_global = db.session.query(func.count(User.id)).filter(User.status == "approved").scalar() or 1
    else:
        start = _period_start(period)
        subq = (
            db.session.query(ReputationHistory.user_id)
            .join(User, User.id == ReputationHistory.user_id)
            .filter(ReputationHistory.created_at >= start, User.status == "approved")
            .group_by(ReputationHistory.user_id)
            .subquery()
        )
        total_global = db.session.query(func.count()).select_from(subq).scalar() or 1

    percentile = round(((total_global - global_rank + 1) / total_global) * 100, 1)

    rank_change = None
    try:
        from models import LeaderboardSnapshot
        week_ago = datetime.datetime.utcnow() - timedelta(days=6)
        two_weeks_ago = datetime.datetime.utcnow() - timedelta(days=15)
        snap = (
            LeaderboardSnapshot.query
            .filter(
                LeaderboardSnapshot.user_id == user_id,
                LeaderboardSnapshot.snapshot_type == "weekly",
                LeaderboardSnapshot.created_at.between(two_weeks_ago, week_ago),
            )
            .order_by(LeaderboardSnapshot.created_at.desc())
            .first()
        )
        if snap and snap.global_rank:
            rank_change = snap.global_rank - global_rank
    except (ImportError, SQLAlchemyError):
        pass

    level = rep_level(user.reputation)
    next_thresh = next((lvl["min"] for lvl in REPUTATION_LEVELS if lvl["min"] > user.reputation), None)
    points_to_next = (next_thresh - user.reputation) if next_thresh else 0
    progress_pct = 0
    if next_thresh:
        lvl_min = level["min"]
        lvl_range = next_thresh - lvl_min
        progress_pct = round(((user.reputation - lvl_min) / max(lvl_range, 1)) * 100, 1)

    weekly_start = datetime.datetime.utcnow() - timedelta(days=7)
    weekly_gain = (
        db.session.query(func.sum(ReputationHistory.points_change))
        .filter(
            ReputationHistory.user_id == user_id,
            ReputationHistory.created_at >= weekly_start,
            ReputationHistory.points_change > 0,
        )
        .scalar()
    ) or 0

    month_start = datetime.datetime.utcnow() - timedelta(days=30)
    active_days = (
        db.session.query(func.count(UserActivity.id))
        .filter(
            UserActivity.user_id == user_id,
            UserActivity.activity_date >= month_start.date(),
            UserActivity.activity_score > 0,
        )
        .scalar()
    ) or 0

    nearby = _get_nearby_for_user(
        user_id=user_id, my_score=my_score, my_rank=global_rank,
        period=period, department=None, n_range=n_nearby,
    )
    nearby_user_ids = [u["user"]["id"] for u in nearby]
    nearby_cmap = _connection_map(user_id, nearby_user_ids)
    for entry in nearby:
        entry["connection_status"] = nearby_cmap.get(entry["user"]["id"])

    champion_status = None
    today = datetime.date.today()
    champ = (
        WeeklyChampion.query
        .filter(WeeklyChampion.user_id == user_id, WeeklyChampion.week_end >= today)
        .first()
    )
    if champ:
        champion_status = {
            "type": champ.champion_type, "subject": champ.subject, "help_count": champ.help_count,
        }

    return {
        "period": period,
        "rank": {
            "global": global_rank, "department": dept_rank, "department_name": dept,
            "change": rank_change, "percentile": percentile, "total_users": total_global,
        },
        "score": {
            "period_score": my_score, "all_time": user.reputation,
            "weekly_gain": int(weekly_gain), "active_days_30d": active_days,
        },
        "level": {"current": level, "points_to_next": points_to_next, "progress_pct": progress_pct},
        "streaks": {
            "login_streak": user.login_streak,
            "help_streak_current": user.help_streak_current,
            "help_streak_longest": user.help_streak_longest,
        },
        "stats": {
            "total_posts": user.total_posts,
            "total_helpful": user.total_helpful,
            "total_helps_given": user.total_helps_given,
            "first_responder": user.first_responder_count,
        },
        "nearby_users": nearby,
        "weekly_champion": champion_status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. CONNECTIONS LEADERBOARD
# ─────────────────────────────────────────────────────────────────────────────

def get_connections_leaderboard(user_id: int, period: str = "weekly") -> dict:
    """Leaderboard scoped to a user's accepted connections (+ self)."""
    conns = Connection.query.filter(
        or_(
            and_(Connection.requester_id == user_id, Connection.status == "accepted"),
            and_(Connection.receiver_id == user_id, Connection.status == "accepted"),
        )
    ).all()

    friend_ids = set()
    for c in conns:
        friend_ids.add(c.receiver_id if c.requester_id == user_id else c.requester_id)
    friend_ids.add(user_id)
    friend_ids_list = list(friend_ids)

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

    found_ids = {r.user_id for r in rows}
    missing = [uid for uid in friend_ids_list if uid not in found_ids]
    zero_users = User.query.filter(User.id.in_(missing), User.status == "approved").all()

    umap = _user_map([r.user_id for r in rows])
    pmap = _profile_map(friend_ids_list)
    cmap = _connection_map(user_id, friend_ids_list)

    entries = []
    for i, row in enumerate(rows):
        user = umap.get(row.user_id)
        if not user:
            continue
        entries.append(_build_entry(i + 1, user, pmap.get(user.id), int(row.score or 0), user_id, cmap))

    base_rank = len(entries) + 1
    for j, user in enumerate(zero_users):
        entries.append(_build_entry(base_rank + j, user, pmap.get(user.id), 0, user_id, cmap))

    your_entry = next((e for e in entries if e["is_you"]), None)

    return {
        "leaderboard": entries,
        "period": period,
        "total_friends": len(entries),
        "your_rank": your_entry["rank"] if your_entry else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. RISING STARS
# ─────────────────────────────────────────────────────────────────────────────

def get_rising_stars(limit: int = 10, department: str | None = None, *, viewer_id: int) -> dict:
    """
    Users with the biggest reputation gain in the past 7 days.

    Document 1 §6.2: this is now the SOLE rising-stars implementation —
    reputation.py's route becomes a thin wrapper calling this, since
    reputation.py::get_rising_stars and this file's version used to compute
    the same thing slightly differently.
    """
    limit = min(limit, 30)
    week_ago = datetime.datetime.utcnow() - timedelta(days=7)

    q = (
        db.session.query(
            ReputationHistory.user_id.label("user_id"),
            func.sum(ReputationHistory.points_change).label("weekly_gain"),
            User.username, User.name, User.avatar, User.reputation,
            User.reputation_level, User.login_streak, User.help_streak_current,
            StudentProfile.department, StudentProfile.class_name,
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
            ReputationHistory.user_id, User.username, User.name, User.avatar,
            User.reputation, User.reputation_level, User.login_streak,
            User.help_streak_current, StudentProfile.department, StudentProfile.class_name,
        )
        .order_by(desc("weekly_gain"))
        .limit(limit)
        .all()
    )

    rising_ids = [r.user_id for r in rows]
    cmap = _connection_map(viewer_id, rising_ids)
    level_cache = {}

    data = []
    for idx, row in enumerate(rows, start=1):
        rep = row.reputation or 0
        level = level_cache.get(rep) or rep_level(rep)
        level_cache[rep] = level

        data.append({
            "rank": idx,
            "weekly_gain": int(row.weekly_gain or 0),
            "is_you": row.user_id == viewer_id,
            "connection_status": cmap.get(row.user_id),
            "user": {
                "id": row.user_id, "username": row.username, "name": row.name,
                "avatar": row.avatar, "department": row.department, "class_level": row.class_name,
            },
            "reputation": {
                "total": rep,
                "level": {"name": level["name"], "icon": level["icon"], "color": level["color"]},
            },
            "streaks": {
                "login_streak": row.login_streak or 0,
                "help_streak_current": row.help_streak_current or 0,
            },
        })

    return {"rising_stars": data, "period_days": 7, "department": department}


# ─────────────────────────────────────────────────────────────────────────────
# 6. LEADERBOARD STATS
# ─────────────────────────────────────────────────────────────────────────────

def get_leaderboard_stats() -> dict:
    """Platform-wide engagement statistics."""
    week_ago = datetime.datetime.utcnow() - timedelta(days=7)

    total_users = db.session.query(func.count(User.id)).filter(User.status == "approved").scalar() or 0

    active_week = (
        db.session.query(func.count(func.distinct(ReputationHistory.user_id)))
        .join(User, User.id == ReputationHistory.user_id)
        .filter(ReputationHistory.created_at >= week_ago, User.status == "approved")
        .scalar()
    ) or 0

    week_rep = (
        db.session.query(func.sum(ReputationHistory.points_change))
        .filter(ReputationHistory.created_at >= week_ago, ReputationHistory.points_change > 0)
        .scalar()
    ) or 0

    avg_rep = db.session.query(func.avg(User.reputation)).filter(User.status == "approved").scalar()
    avg_rep = round(float(avg_rep or 0), 1)

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
            "name": top_dept_row.department,
            "total_rep": int(top_dept_row.dept_rep or 0),
            "member_count": int(top_dept_row.member_count or 0),
        }

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
                "user_id": u.id, "name": u.name, "username": u.username,
                "avatar": u.avatar, "weekly_gain": int(top_gainer_row.gain or 0),
            }

    top_all_time = (
        db.session.query(User).filter(User.status == "approved").order_by(desc(User.reputation)).first()
    )
    top_scorer = None
    if top_all_time:
        level = rep_level(top_all_time.reputation)
        top_scorer = {
            "user_id": top_all_time.id, "name": top_all_time.name, "username": top_all_time.username,
            "avatar": top_all_time.avatar, "reputation": top_all_time.reputation, "level": level["name"],
        }

    return {
        "total_students": total_users,
        "active_this_week": active_week,
        "week_rep_earned": int(week_rep),
        "avg_reputation": avg_rep,
        "top_department": top_department,
        "top_gainer_this_week": top_gainer,
        "top_scorer_all_time": top_scorer,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7. FILTERS
# ─────────────────────────────────────────────────────────────────────────────

def get_leaderboard_filters(viewer_id: int) -> dict:
    """Valid filter options: departments, periods, viewer's default department."""
    departments = (
        db.session.query(StudentProfile.department)
        .join(User, User.id == StudentProfile.user_id)
        .filter(User.status == "approved", StudentProfile.department.isnot(None))
        .group_by(StudentProfile.department)
        .order_by(StudentProfile.department.asc())
        .all()
    )
    dept_list = [row[0] for row in departments]

    profile = StudentProfile.query.filter_by(user_id=viewer_id).first()
    my_dept = profile.department if profile else None

    periods = [
        {"key": "daily", "label": "Today", "description": "Points earned in last 24 hours"},
        {"key": "weekly", "label": "This Week", "description": "Points earned in last 7 days"},
        {"key": "monthly", "label": "This Month", "description": "Points earned in last 30 days"},
        {"key": "all_time", "label": "All Time", "description": "Total lifetime reputation"},
    ]

    return {"periods": periods, "departments": dept_list, "your_department": my_dept}


# ─────────────────────────────────────────────────────────────────────────────
# 8. RANK HISTORY
# ─────────────────────────────────────────────────────────────────────────────

def get_rank_history(user_id: int, weeks: int = 8) -> dict:
    """Return the viewer's rank over the last N weekly snapshots."""
    from models import LeaderboardSnapshot

    weeks = min(weeks, 26)

    snaps = (
        LeaderboardSnapshot.query
        .filter(LeaderboardSnapshot.user_id == user_id, LeaderboardSnapshot.snapshot_type == "weekly")
        .order_by(LeaderboardSnapshot.created_at.desc())
        .limit(weeks)
        .all()
    )
    snaps = list(reversed(snaps))

    history = [
        {
            "date": s.created_at.strftime("%Y-%m-%d"),
            "global_rank": s.global_rank,
            "dept_rank": s.department_rank,
            "score": s.score,
        }
        for s in snaps
    ]

    return {"history": history, "weeks_back": weeks}


# ─────────────────────────────────────────────────────────────────────────────
# 9. SNAPSHOT CREATION  (Document 1 §6.3 consolidation)
#
# This is now the SOLE snapshot implementation. scheduler.py's
# _take_snapshot and leaderboard.py's POST /leaderboard/snapshot admin
# route both become thin callers of this function — this version is
# chosen as the "keeper" because it already has the one-snapshot-per-
# type-per-day idempotency guard AND the cleaner two-query department-
# rank computation that scheduler.py's version has, matching the
# recommendation in Document 1 §6.3.
# ─────────────────────────────────────────────────────────────────────────────

def take_snapshot(snapshot_type: str = "weekly") -> dict:
    """
    Create a leaderboard snapshot of current all-time rankings.

    Does COMMIT — this is one of the few service functions that does,
    same exception category as badge_service.check_and_award_badge:
    snapshot creation is itself the atomic unit of work (a bulk insert of
    N rows that must succeed or fail together), not part of a larger
    multi-step request the route layer is coordinating.

    Returns a plain dict via SnapshotResult.to_dict().
    """
    from models import LeaderboardSnapshot

    now = datetime.datetime.utcnow()

    already_ran = (
        db.session.query(LeaderboardSnapshot)
        .filter(
            LeaderboardSnapshot.snapshot_type == snapshot_type,
            func.date(LeaderboardSnapshot.created_at) == now.date(),
        )
        .first()
    )
    if already_ran:
        return SnapshotResult(created=0, skipped=0, total_ranked=0).to_dict()

    ranked = (
        db.session.query(User.id, User.reputation)
        .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
        .filter(User.status == "approved")
        .order_by(desc(User.reputation), asc(User.id))
        .all()
    )

    if not ranked:
        return SnapshotResult(created=0, skipped=0, total_ranked=0).to_dict()

    dept_rows = (
        db.session.query(User.id, StudentProfile.department)
        .join(StudentProfile, StudentProfile.user_id == User.id)
        .filter(User.status == "approved", StudentProfile.department.isnot(None))
        .order_by(StudentProfile.department, desc(User.reputation), asc(User.id))
        .all()
    )

    dept_rank_map: dict[int, int] = {}
    dept_counters: dict[str, int] = {}
    for uid, dept in dept_rows:
        dept_counters[dept] = dept_counters.get(dept, 0) + 1
        dept_rank_map[uid] = dept_counters[dept]

    created = 0
    skipped = 0

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

    return SnapshotResult(created=created, skipped=skipped, total_ranked=len(ranked)).to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# 10. SCORE BREAKDOWN
# ─────────────────────────────────────────────────────────────────────────────

def get_score_breakdown(user_id: int, period: str = "weekly") -> dict:
    """Transparent breakdown of how a user's score is composed."""
    user = User.query.get(user_id)
    if not user:
        raise LookupError("User not found")

    history_q = ReputationHistory.query.filter_by(user_id=user_id)
    if period != "all_time":
        history_q = history_q.filter(ReputationHistory.created_at >= _period_start(period))

    history_rows = history_q.order_by(ReputationHistory.created_at.desc()).limit(50).all()

    by_action = {}
    total_positive = 0
    total_negative = 0
    for h in history_rows:
        action = h.action
        by_action.setdefault(action, {"count": 0, "total_points": 0})
        by_action[action]["count"] += 1
        by_action[action]["total_points"] += h.points_change
        if h.points_change > 0:
            total_positive += h.points_change
        else:
            total_negative += h.points_change

    recent = [
        {
            "action": h.action, "points_change": h.points_change,
            "created_at": h.created_at.isoformat(),
            "related_type": h.related_type, "related_id": h.related_id,
        }
        for h in history_rows[:10]
    ]

    consistency_bonus_explanation = (
        f"Your {user.login_streak}-day login streak + "
        f"{user.help_streak_current}-day help streak contribute "
        "to your display momentum badge."
    )

    return {
        "period": period,
        "total_period_score": total_positive + total_negative,
        "total_positive": total_positive,
        "total_negative": total_negative,
        "by_action": by_action,
        "recent_events": recent,
        "all_time_rep": user.reputation,
        "level": rep_level(user.reputation),
        "streaks": {
            "login_streak": user.login_streak,
            "help_streak_current": user.help_streak_current,
            "help_streak_longest": user.help_streak_longest,
        },
        "consistency_note": consistency_bonus_explanation,
        "scoring_tips": [
            "💡 Answers marked as solutions earn 15 pts",
            "🔥 7-day help streak earns a bonus 10 pts",
            "⚡ Helpful comments earn 3 pts each",
            "📝 Posts reaching 10 likes earn 5 pts",
            "🏆 Posts reaching 50 likes earn 20 pts",
        ],
    }
