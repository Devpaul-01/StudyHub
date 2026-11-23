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
from services.reputation_levels import REPUTATION_LEVELS, get_reputation_level
from services import cache_service


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

VALID_PERIODS = {"daily", "weekly", "monthly", "all_time"}
PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
DEFAULT_NEARBY_RANGE = 3
MAX_NEARBY_RANGE = 10
MAX_PAGE = 1000
MAX_RISING_LIMIT = 30
MAX_HISTORY_WEEKS = 26


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


def clamp(value, lo: int, hi: int) -> int:
    """Clamp a possibly-missing query-string integer into [lo, hi].

    min() alone let 0 and negative values through: PostgreSQL rejects a
    negative LIMIT (HTTP 500) while SQLite treats it as "no limit", which
    bypassed the page-size cap. Values also end up in cache keys, so an
    unbounded range meant unbounded key cardinality.
    """
    if value is None:
        return lo
    return max(lo, min(int(value), hi))


def resolve_department(department: str | None) -> str | None:
    """Return the canonical department for a user-supplied filter value.

    Matches case-insensitively against the departments that actually exist
    (the cached filter list). Raises ValueError for anything else, so a
    free-text query parameter can never mint new cache keys or reach a query.
    """
    if not department:
        return None
    wanted = " ".join(str(department).split()).casefold()
    for known in _get_leaderboard_filters_shared()["departments"]:
        if known.casefold() == wanted:
            return known
    raise ValueError("Unknown department")


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


def _rank_change_applies(period: str, department: str | None) -> bool:
    """Rank movement is only comparable on the all-time, all-department board."""
    return period == "all_time" and not department


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

def _score_rows(period: str, department: str | None):
    """Subquery of (user_id, score): everyone who is on the given board.

    all_time: every approved user, scored by User.reputation.
    period:   approved users with ledger activity in the window, scored by the
              sum of points_change (so net-zero and negative scores are real
              entries; users with no activity are simply absent).
    """
    if period == "all_time":
        q = (
            db.session.query(
                User.id.label("user_id"),
                func.coalesce(User.reputation, 0).label("score"),
            )
            .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
            .filter(User.status == "approved")
        )
        if department:
            q = q.filter(StudentProfile.department == department)
        return q.subquery()

    q = (
        db.session.query(
            ReputationHistory.user_id.label("user_id"),
            func.sum(ReputationHistory.points_change).label("score"),
        )
        .join(User, User.id == ReputationHistory.user_id)
        .outerjoin(StudentProfile, StudentProfile.user_id == ReputationHistory.user_id)
        .filter(ReputationHistory.created_at >= _period_start(period), User.status == "approved")
    )
    if department:
        q = q.filter(StudentProfile.department == department)
    return q.group_by(ReputationHistory.user_id).subquery()


def _ranked_scores(period: str, department: str | None):
    """The single source of truth for ranking.

    Adds three window columns to _score_rows():
      rank  - competition rank: tied users share a rank, the next rank skips
              ("1, 2, 2, 4"). This is what every surface displays.
      pos   - unique 1-based position, ordered (score DESC, user_id ASC). Used
              only to slice pages and the "near you" window deterministically.
      total - number of users on the board.
    Because the list, pagination and nearby all read these same columns they
    cannot disagree about a user's rank, even when scores tie.
    """
    base = _score_rows(period, department)
    return db.session.query(
        base.c.user_id,
        base.c.score,
        func.rank().over(order_by=base.c.score.desc()).label("rank"),
        func.row_number().over(order_by=(base.c.score.desc(), base.c.user_id.asc())).label("pos"),
        func.count().over().label("total"),
    ).subquery()


def _ranked_page(period: str, department: str | None, limit: int, offset: int):
    """Return (rows, total); rows have .user_id/.score/.rank in board order."""
    ranked = _ranked_scores(period, department)
    rows = (
        db.session.query(ranked.c.user_id, ranked.c.score, ranked.c.rank, ranked.c.total)
        .filter(ranked.c.pos > offset, ranked.c.pos <= offset + limit)
        .order_by(ranked.c.pos)
        .all()
    )
    if rows:
        return rows, int(rows[0].total)
    # Page past the end: the window columns have no row to report a total on.
    total = db.session.query(func.count()).select_from(ranked).scalar() or 0
    return rows, int(total)


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


def _compute_standing(user_id: int, period: str, department: str | None) -> dict:
    """Where one user stands on one board, in a single aggregation pass.

    Returns {score, rank, pos, total, on_board}:
      score    - the user's own score for the period (always computed)
      rank     - competition rank (the same number the list shows), or None
                 when the user cannot be ranked on this board: not an
                 approved user, or the board is for a department they are not
                 in (previously they were shown a rank inside it anyway)
      pos      - unique position (score DESC, id ASC) if on the board
      total    - users on the board
      on_board - whether the user has a row on the board (period boards only
                 contain users with activity in the window)
    """
    user = db.session.get(User, user_id)
    if not user:
        return {"score": 0, "rank": None, "pos": None, "total": 0, "on_board": False}

    score = _user_period_score(user_id, period)
    eligible = user.status == "approved"
    if eligible and department:
        own_dept = (
            db.session.query(StudentProfile.department)
            .filter(StudentProfile.user_id == user_id)
            .scalar()
        )
        eligible = own_dept == department

    base = _score_rows(period, department)
    total, higher, tied_before, on_board = (
        db.session.query(
            func.count(),
            func.coalesce(func.sum(case((base.c.score > score, 1), else_=0)), 0),
            func.coalesce(
                func.sum(case(((base.c.score == score) & (base.c.user_id < user_id), 1), else_=0)), 0
            ),
            func.coalesce(func.sum(case((base.c.user_id == user_id, 1), else_=0)), 0),
        )
        .select_from(base)
        .one()
    )
    on_board = bool(on_board) and eligible
    return {
        "score": score,
        "rank": int(higher) + 1 if eligible else None,
        "pos": int(higher) + int(tied_before) + 1 if on_board else None,
        "total": int(total),
        "on_board": on_board,
    }


@cache_service.cached(
    "sh:1:lb:standing:{user_id}:{period}:{department_key}", ttl_seconds=60,
    version_key="sh:1:ver:{user_id}",
)
def _get_viewer_standing_cached(user_id: int, period: str, department_key: str) -> dict:
    return _compute_standing(user_id, period, None if department_key == "_" else department_key)


def get_viewer_standing(user_id: int, period: str, department: str | None = None) -> dict:
    """Cached _compute_standing. Shared by the list overlay, /me and /nearby so
    one page load does one aggregation instead of three. The viewer's own
    changes are live (their cache version is bumped on commit); other users'
    movement lags by the 60 s TTL, same as the shared pages."""
    return _get_viewer_standing_cached(user_id, period, _dept_key(department))


def _percentile(total: int, rank: int | None) -> float:
    if not total or not rank:
        return 0.0
    return round(((total - rank + 1) / total) * 100, 1)


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

def _dept_key(department: str | None) -> str:
    """Missing/absent scope uses a literal underscore, never an empty
    string or None — plan §3.2. Keeps a typo-empty-string department from
    being visually indistinguishable from "no department filter" in a
    redis-cli SCAN/KEYS listing."""
    return department if department else "_"


@cache_service.cached(
    "sh:1:lb:global:{period}:{department_key}:{page}:{limit}", ttl_seconds=60
)
def _get_global_leaderboard_page(
    period: str, department_key: str, page: int, limit: int
) -> dict:
    """
    Viewer-INDEPENDENT leaderboard page: entries (with is_you always False
    and connection_status always None — placeholders, never real per-viewer
    data) plus pagination. Cached, shared across every viewer who requests
    the same period/department/page/limit combination, per plan §3.2/§11 —
    the leaderboard's content genuinely doesn't vary by viewer, so this is
    the piece worth sharing one Redis entry for.

    `your_position` is deliberately NOT computed or returned here — it's
    viewer-specific and is layered on live, per-request, by the public
    get_global_leaderboard() below, exactly as plan §11/§17.3 specify:
    "the only viewer-specific piece of a leaderboard response is
    your_position ... computed OUTSIDE the cached page specifically so the
    shared cache entry never needs to vary by viewer." The `is_you`/
    `connection_status` fields on each entry are the same category of
    viewer-specific data and get the identical treatment here (real values
    filled in after the cache lookup, never baked into the cached blob) —
    caching them as `is_you: True` for whichever viewer's request happened
    to populate the cache would leak that viewer's identity into every
    other viewer's response.

    department_key is the already-`_dept_key()`-normalized department
    string (never None) purely so it interpolates cleanly into the cache
    key template — the underscore-for-missing convention from plan §3.2.
    """
    department = None if department_key == "_" else department_key
    limit = min(limit, MAX_LIMIT)
    offset = (page - 1) * limit

    rows, total = _ranked_page(period, department, limit, offset)

    user_ids = [r.user_id for r in rows]
    umap = _user_map(user_ids)
    pmap = _profile_map(user_ids)
    # Snapshots hold ALL-TIME, department-agnostic ranks, so movement is only
    # meaningful on that exact board. On weekly/monthly/daily or department
    # boards the comparison would be between two different rankings.
    old_ranks = _old_rank_map(user_ids) if _rank_change_applies(period, department) else {}

    entries = []
    for row in rows:
        user = umap.get(row.user_id)
        if not user:
            continue
        rank = int(row.rank)
        old_rank = old_ranks.get(user.id)
        rank_change = (old_rank - rank) if old_rank else None
        # current_user_id=None -> is_you always False; conn_map={} -> every
        # connection_status is None. Both are overwritten with real,
        # per-viewer values by get_global_leaderboard() after the cache
        # lookup, never persisted in this shared cache entry.
        entries.append(_build_entry(rank, user, pmap.get(user.id), int(row.score or 0), None, {}, rank_change))

    return {
        "entries": entries,
        "period": period,
        "department": department,
        "pagination": {
            "page": page, "limit": limit, "total": total,
            "has_more": (offset + limit) < total,
        },
    }


def _empty_page(period, department, page, limit, viewer_id) -> dict:
    return LeaderboardPage(
        entries=[],
        period=period,
        department=department,
        pagination={"page": page, "limit": limit, "total": 0, "has_more": False},
        your_position={
            "rank": None, "score": _user_period_score(viewer_id, period),
            "percentile": 0.0, "total_users": 0,
        },
    ).to_dict()


def get_global_leaderboard(
    period: str, department: str | None, page: int, limit: int, *, viewer_id: int
) -> dict:
    """Main leaderboard with period & department filtering. Returns a dict
    (LeaderboardPage.to_dict()-shaped) ready for jsonify.

    Cache-aside per plan §4.2 (60s TTL, key includes period/department/
    page/limit per plan §10's pagination-key-correctness requirement). The
    shared, viewer-independent part of the page (entries + pagination) is
    handled by _get_global_leaderboard_page above, which is what actually
    carries the @cached decorator; this function's own job is exactly the
    per-viewer overlay described there: real is_you/connection_status per
    entry, plus your_position, computed live on every call regardless of
    whether the shared page was a cache hit or miss.
    """
    try:
        department = resolve_department(department)
    except ValueError:
        # A department nobody belongs to is an empty board, not an error. It is
        # answered here, uncached, so arbitrary strings cannot mint cache keys.
        return _empty_page(period, department, page, limit, viewer_id)

    page_data = _get_global_leaderboard_page(period, _dept_key(department), page, limit)

    limit = page_data["pagination"]["limit"]
    total = page_data["pagination"]["total"]

    # Overlay live per-viewer data onto the (possibly cached, possibly
    # shared-with-other-viewers) entries: real connection status and
    # is_you, never trusted from the cached blob itself.
    entries = page_data["entries"]
    user_ids = [e["user"]["id"] for e in entries]
    cmap = _connection_map(viewer_id, user_ids)
    for entry in entries:
        entry["is_you"] = entry["user"]["id"] == viewer_id
        entry["connection_status"] = cmap.get(entry["user"]["id"])

    standing = get_viewer_standing(viewer_id, period, department)
    your_score, your_rank = standing["score"], standing["rank"]
    your_percentile = _percentile(total, your_rank)

    return LeaderboardPage(
        entries=entries,
        period=page_data["period"],
        department=page_data["department"],
        pagination=page_data["pagination"],
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
def _compute_nearby(user_id: int, period: str, department: str | None, n_range: int) -> list:
    """The viewer plus up to n_range users above and below, by shared position.

    Built from the same ranked query as the list, so every entry carries the
    rank the list shows for that user. Users tied with the viewer are included
    (they used to be silently dropped). Returns [] when the viewer cannot be
    ranked on this board.
    """
    n_range = clamp(n_range, 1, MAX_NEARBY_RANGE)
    standing = get_viewer_standing(user_id, period, department)
    if standing["rank"] is None:
        return []

    user = db.session.get(User, user_id)
    profile = StudentProfile.query.filter_by(user_id=user_id).first()

    # Off-board viewers (no activity this week) are shown where they would sit.
    anchor = float(standing["pos"]) if standing["on_board"] else (standing["rank"] - 1) + 0.5

    ranked = _ranked_scores(period, department)
    rows = (
        db.session.query(ranked.c.user_id, ranked.c.score, ranked.c.rank, ranked.c.pos)
        .filter(
            ranked.c.pos >= anchor - n_range,
            ranked.c.pos <= anchor + n_range,
            ranked.c.user_id != user_id,
        )
        .order_by(ranked.c.pos)
        .all()
    )
    umap = _user_map([r.user_id for r in rows])
    pmap = _profile_map([r.user_id for r in rows])

    def entry_for(row):
        u = umap.get(row.user_id)
        return _build_entry(int(row.rank), u, pmap.get(row.user_id), int(row.score or 0), user_id, {}) if u else None

    above = [e for e in (entry_for(r) for r in rows if r.pos < anchor) if e]
    below = [e for e in (entry_for(r) for r in rows if r.pos > anchor) if e]
    me = _build_entry(standing["rank"], user, profile, standing["score"], user_id, {})
    return above + [me] + below


@cache_service.cached(
    "sh:1:lb:rank:{user_id}:nearby:{period}:{department_key}:{n_range}", ttl_seconds=60,
    version_key="sh:1:ver:{user_id}",
)
def _get_nearby_users_cached(
    user_id: int, period: str, department_key: str, n_range: int
) -> list:
    """Cached body of get_nearby_users.

    department_key is the already-normalised (never None) department: the
    @cached decorator builds its key from the raw call arguments, so the
    public wrapper normalises before calling in. Scoped entirely to user_id,
    so there is no per-viewer overlay; connection_status can lag by the 60 s
    TTL, an accepted trade-off.
    """
    department = None if department_key == "_" else department_key
    nearby = _compute_nearby(user_id, period, department, n_range)

    cmap = _connection_map(user_id, [e["user"]["id"] for e in nearby])
    for entry in nearby:
        entry["connection_status"] = cmap.get(entry["user"]["id"])
    return nearby


def get_nearby_users(
    user_id: int, period: str = "weekly", department: str | None = None,
    n_range: int = DEFAULT_NEARBY_RANGE,
) -> list:
    """
    Users immediately surrounding `user_id` in the rankings, with
    connection-status annotated in (this is the route-facing entry point;
    get_my_rank builds its own, uncached window with _compute_nearby).

    Thin public entry point: normalizes `department` into the `_`-for-
    missing convention (plan §3.2) BEFORE calling the actual @cached
    implementation (_get_nearby_users_cached, above) — same ordering
    requirement as get_my_rank's identical split, see that function's
    docstring for the full explanation of why normalization can't happen
    inside the decorated function itself.
    """
    return _get_nearby_users_cached(user_id, period, _dept_key(department), n_range)


# ─────────────────────────────────────────────────────────────────────────────
# 3. MY RANK CARD
# ─────────────────────────────────────────────────────────────────────────────


@cache_service.cached(
    "sh:1:lb:rank:{user_id}:{period}:{department_key}", ttl_seconds=60,
    version_key="sh:1:ver:{user_id}",
)
def _get_my_rank_cached(user_id: int, period: str, department_key: str) -> dict:
    """
    Cached body of get_my_rank, below. Requires `department_key` as an
    already-normalized, non-None string (never the raw, possibly-None
    `department` argument) — see get_my_rank's docstring for why this
    split exists: the @cached decorator interpolates its key template from
    the RAW arguments a caller passes, not from any value the function body
    reassigns internally, so normalization has to happen before this
    function is ever called, not inside it.

    Cache-aside per plan §4.2's `/leaderboard/me` row: 60s TTL fallback +
    hard invalidation on the viewing user's own reputation change. Key is
    `sh:1:lb:rank:{user_id}:{period}:{department|_}`, matching plan §3.2's
    table exactly — this is the key reputation_service.py::award_reputation
    pattern-deletes via `sh:1:lb:rank:{user_id}:*` immediately on every
    reputation change for this user (plan §5.1), so point-total-driven
    fields here are always fresh; only fields that could change for a
    reason OTHER than this user's own reputation (e.g. someone else's
    weekly_champion status, or a connection accepting them, changing
    connection_status inside `nearby`) rely on the 60s TTL fallback.

    Entirely scoped to `user_id` throughout (rank, nearby users, streaks,
    connection_status relative to user_id) — there is no "other viewer"
    concept for this endpoint, so unlike get_global_leaderboard there's no
    split needed between a shared cached blob and a live per-viewer
    overlay; the whole return value is safe to cache and serve back
    verbatim on a hit.
    """
    department = None if department_key == "_" else department_key

    n_nearby = 3

    user = User.query.get(user_id)
    if not user:
        raise LookupError("User not found")

    profile = StudentProfile.query.filter_by(user_id=user_id).first()
    dept = department or (profile.department if profile else None)

    global_standing = get_viewer_standing(user_id, period, None)
    my_score = global_standing["score"]
    global_rank = global_standing["rank"]
    total_global = global_standing["total"] or 1
    # Department rank is None when the requested department is not the user's.
    dept_rank = get_viewer_standing(user_id, period, dept)["rank"] if dept else None

    percentile = _percentile(total_global, global_rank)

    # Rank movement compares against an all-time snapshot, so it is only
    # meaningful for the all-time card (see _rank_change_applies).
    rank_change = None
    if _rank_change_applies(period, None) and global_rank:
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

    nearby = _compute_nearby(user_id, period, None, n_nearby)
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


def get_my_rank(user_id: int, period: str = "weekly", department: str | None = None) -> dict:
    """
    Full rank card: rank, score breakdown, nearby users, streaks,
    progress, and weekly champion status.

    Thin public entry point: normalizes `department` into the `_`-for-
    missing convention (plan §3.2) BEFORE calling the actual @cached
    implementation (_get_my_rank_cached, above) — this ordering matters:
    @cached interpolates its key template from the raw arguments the
    caller passes in, so the normalization has to happen out here, not
    inside the decorated function, or every real call (which never passes
    department_key explicitly) would bake the literal string "None" into
    the cache key instead of "_".
    """
    return _get_my_rank_cached(user_id, period, _dept_key(department))


# ─────────────────────────────────────────────────────────────────────────────
# 4. CONNECTIONS LEADERBOARD
# ─────────────────────────────────────────────────────────────────────────────

@cache_service.cached(
    "sh:1:lb:connections:{user_id}:{period}", ttl_seconds=60, version_key="sh:1:ver:{user_id}"
)
def get_connections_leaderboard(user_id: int, period: str = "weekly") -> dict:
    """Leaderboard scoped to a user's accepted connections (+ self).

    Cache-aside per plan §4.2's `/leaderboard/connections` row: 60s TTL.
    Genuinely per-viewer (depends on the specific connection graph). The
    viewer's own reputation changes retire it through their cache version;
    changes to *friends'* scores or the graph itself are deliberately not
    fanned out (unbounded invalidation) and lag by the TTL.
    """
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

    # Score every approved member of the circle, including those with no
    # activity in the window (score 0), BEFORE ordering. Zero-score members
    # used to be appended after the sorted rows, which ranked a friend on 0
    # below a friend on -5.
    approved = {
        u.id: u
        for u in User.query.filter(User.id.in_(friend_ids_list), User.status == "approved").all()
    }
    if period == "all_time":
        scores = {uid: int(u.reputation or 0) for uid, u in approved.items()}
    else:
        scores = {uid: 0 for uid in approved}
        for row in (
            db.session.query(
                ReputationHistory.user_id, func.sum(ReputationHistory.points_change).label("score")
            )
            .filter(
                ReputationHistory.user_id.in_(list(approved)),
                ReputationHistory.created_at >= _period_start(period),
            )
            .group_by(ReputationHistory.user_id)
            .all()
        ):
            scores[row.user_id] = int(row.score or 0)

    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    pmap = _profile_map(list(approved))
    cmap = _connection_map(user_id, list(approved))

    entries = []
    rank = 0
    previous_score = None
    for position, (uid, score) in enumerate(ordered, start=1):
        if score != previous_score:      # competition ranking: ties share a rank
            rank, previous_score = position, score
        entries.append(_build_entry(rank, approved[uid], pmap.get(uid), score, user_id, cmap))

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

@cache_service.cached("sh:1:lb:rising:{limit}:{department_key}", ttl_seconds=300)
def _get_rising_stars_page(limit: int, department_key: str) -> dict:
    """
    Viewer-INDEPENDENT rising-stars page. Cached, shared across every
    viewer who requests the same limit/department, per plan §3.2's key
    pattern (`sh:1:lb:rising:{limit}:{department|_}` — deliberately no
    user_id in the key, since "who gained the most this week" doesn't vary
    by who's asking).

    is_you/connection_status are NOT computed here — same split as
    _get_global_leaderboard_page above, and for the identical reason: this
    cache entry is shared, so it must never bake in one particular
    viewer's identity or connection graph. The public get_rising_stars()
    below overlays real per-viewer values after the cache lookup.
    """
    department = None if department_key == "_" else department_key
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

    level_cache = {}
    data = []
    for idx, row in enumerate(rows, start=1):
        rep = row.reputation or 0
        level = level_cache.get(rep) or rep_level(rep)
        level_cache[rep] = level

        data.append({
            "rank": idx,
            "weekly_gain": int(row.weekly_gain or 0),
            "is_you": False,  # placeholder — overwritten per-viewer below
            "connection_status": None,  # placeholder — overwritten per-viewer below
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


def get_rising_stars(limit: int = 10, department: str | None = None, *, viewer_id: int) -> dict:
    """
    Users with the biggest reputation gain in the past 7 days.

    Document 1 §6.2: this is now the SOLE rising-stars implementation —
    reputation.py's route becomes a thin wrapper calling this, since
    reputation.py::get_rising_stars and this file's version used to compute
    the same thing slightly differently.

    Cache-aside per plan §4.2's `/leaderboard/rising` row: TTL fallback
    only, 300s — "who gained the most this week" tolerates several minutes
    of staleness by nature. The shared computation is delegated to
    _get_rising_stars_page above; this function's job is only the live
    per-viewer overlay (is_you, connection_status), computed fresh on
    every call regardless of whether the shared page was a cache hit.
    """
    page = _get_rising_stars_page(limit, _dept_key(department))

    data = page["rising_stars"]
    rising_ids = [entry["user"]["id"] for entry in data]
    cmap = _connection_map(viewer_id, rising_ids)
    for entry in data:
        entry["is_you"] = entry["user"]["id"] == viewer_id
        entry["connection_status"] = cmap.get(entry["user"]["id"])

    return {"rising_stars": data, "period_days": page["period_days"], "department": page["department"]}


# ─────────────────────────────────────────────────────────────────────────────
# 6. LEADERBOARD STATS
# ─────────────────────────────────────────────────────────────────────────────

@cache_service.cached("sh:1:lb:stats", ttl_seconds=300)
def get_leaderboard_stats() -> dict:
    """Platform-wide engagement statistics.

    Cache-aside per plan §4.2's `/leaderboard/stats` row: TTL fallback
    only, 300s. Platform-wide, not user-scoped — no natural per-mutation
    hook makes sense at this granularity (every reputation change on the
    platform could in principle move one of these aggregates), so this
    relies on its TTL alone, same reasoning as get_leaderboard_stats'
    sibling platform-wide aggregates elsewhere in this module.
    """
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

@cache_service.cached("sh:1:lb:filters:shared", ttl_seconds=1800)
def _get_leaderboard_filters_shared() -> dict:
    """
    The department list + static periods list — shared across every
    viewer, cached per plan §4.2.1's split: `your_department` is NOT
    included here (see get_leaderboard_filters below), avoiding a wasteful
    {user_id}-scoped cache entry for data that's 95% identical across
    every user. 1800s TTL: department changes are rare (a profile edit),
    and departments are drawn from a fixed enum-like list, so a half-hour
    staleness window on a newly-added department is a non-issue.
    """
    departments = (
        db.session.query(StudentProfile.department)
        .join(User, User.id == StudentProfile.user_id)
        .filter(User.status == "approved", StudentProfile.department.isnot(None))
        .group_by(StudentProfile.department)
        .order_by(StudentProfile.department.asc())
        .all()
    )
    dept_list = [row[0] for row in departments]

    periods = [
        {"key": "daily", "label": "Today", "description": "Points earned in last 24 hours"},
        {"key": "weekly", "label": "This Week", "description": "Points earned in last 7 days"},
        {"key": "monthly", "label": "This Month", "description": "Points earned in last 30 days"},
        {"key": "all_time", "label": "All Time", "description": "Total lifetime reputation"},
    ]

    return {"periods": periods, "departments": dept_list}


def get_leaderboard_filters(viewer_id: int) -> dict:
    """Valid filter options: departments, periods, viewer's default department.

    Split cache per plan §4.2.1: the shared `departments`/`periods` part is
    cached (see _get_leaderboard_filters_shared, 1800s TTL); `your_department`
    is read live off the viewer's own StudentProfile on every call — it's
    already a single cheap indexed lookup with zero extra query cost worth
    caching, and composing it here means this cache never needs a
    per-viewer key.
    """
    shared = _get_leaderboard_filters_shared()

    profile = StudentProfile.query.filter_by(user_id=viewer_id).first()
    my_dept = profile.department if profile else None

    return {
        "periods": shared["periods"],
        "departments": shared["departments"],
        "your_department": my_dept,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8. RANK HISTORY
# ─────────────────────────────────────────────────────────────────────────────

@cache_service.cached("sh:1:lb:history:{user_id}:{weeks}", ttl_seconds=3600)
def get_rank_history(user_id: int, weeks: int = 8) -> dict:
    """Return the viewer's rank over the last N weekly snapshots.

    Cache-aside per plan §4.2's `/leaderboard/rank-history` row: TTL
    fallback only, 3600s — the underlying data provably cannot change more
    than once a week (only take_snapshot() writes LeaderboardSnapshot
    rows), so an hour of cache staleness is a bound, not a typical case.
    Deliberately NOT hard-invalidated by take_snapshot() (plan §5.5):
    invalidating every user's rank-history cache on every weekly snapshot
    run would be a pointless full-table invalidation sweep for data whose
    entire point is a long TTL; the 3600s TTL already guarantees the new
    snapshot is visible within an hour of the job running.
    """
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

    # ── Cache warming (plan §13, §17.3) ─────────────────────────────────
    # Only the single default leaderboard view (weekly, no department,
    # page 1) is warmed — not every period/department combination, per
    # plan §13's explicit scoping: this is plausibly the single hottest
    # cache key in the app (every leaderboard-tab visit hits it), and
    # warming it right after the snapshot job touches every approved
    # user's row anyway means the very first post-snapshot read (whenever
    # the first user checks it Sunday morning) doesn't pay the uncached
    # cost. Only fires for the weekly snapshot, matching the plan's
    # "Sunday 00:05 UTC job" framing — a monthly snapshot doesn't share
    # this cache key's period and isn't warmed here.
    #
    # Deletes any existing (possibly still-fresh-but-now-stale-relative-
    # to-this-snapshot) entry first, then calls _get_global_leaderboard_page
    # directly so the @cached decorator repopulates it — this reuses the
    # exact same shared, viewer-independent computation get_global_leaderboard()
    # calls on every normal request, so there is no separate "warming"
    # code path to keep in sync with the real one.
    if snapshot_type == "weekly":
        default_key = f"sh:1:lb:global:weekly:_:1:{DEFAULT_LIMIT}"
        cache_service.delete(default_key)
        _get_global_leaderboard_page("weekly", "_", 1, DEFAULT_LIMIT)

    return SnapshotResult(created=created, skipped=skipped, total_ranked=len(ranked)).to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# 10. SCORE BREAKDOWN
# ─────────────────────────────────────────────────────────────────────────────

@cache_service.cached(
    "sh:1:lb:breakdown:{user_id}:{period}", ttl_seconds=120, version_key="sh:1:ver:{user_id}"
)
def get_score_breakdown(user_id: int, period: str = "weekly") -> dict:
    """Transparent breakdown of how a user's score is composed.

    Cache-aside per plan §4.2's `/leaderboard/breakdown` row: 120s TTL
    fallback + hard invalidation on the viewing user's own reputation
    change. Key matches plan §3.2's `sh:1:lb:breakdown:{user_id}:{period}`
    exactly, which is what reputation_service.py::award_reputation
    pattern-deletes via `sh:1:lb:breakdown:{user_id}:*` (plan §5.1).
    Entirely scoped to user_id — no viewer/subject split needed.
    """
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
