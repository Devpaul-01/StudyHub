"""
services/badge_service.py

Badge definitions, awarding logic, and progress calculation.
Extracted from routes/student/badges.py (Document 1 §2, Document 2 §3.2).

Per Document 2 §2's layering rule: no Flask imports, no request/session/g.

Transaction convention: nothing on the request path commits here (there used to be a
``commit=True`` default on check_and_award_badge that committed the
caller's half-finished transaction). The caller -- a route or the
badge-check job -- owns the single commit. Everything that must only
happen after a successful commit (cache invalidation, notification
delivery) goes through services.after_commit.

Concurrency: a badge insert happens inside a SAVEPOINT; if a concurrent
request already awarded the same badge the UNIQUE(user_id, badge_id)
constraint rejects the insert, only the savepoint is rolled back, and the
duplicate is simply reported as "not newly awarded" -- it no longer
poisons the whole transaction. Badge.awarded_count is incremented with an
atomic SQL expression, never a Python read-modify-write.

Revocation policy: badges are permanent achievements and are never
revoked when the underlying numbers later fall.

Consumers:
  - routes/student/badges.py — the primary owner, every route becomes a
    thin wrapper around these functions.
  - routes/student/analytics.py::generate_insights — calls
    calculate_badge_progress directly today (imported at module level
    there to avoid a circular-import/repeated-lookup cost); switches to
    importing from here instead of from badges.py.
  - services/badge_triggers.py — schedules check_all_badges_for_user as a
    background job after the events that change badge inputs.
"""

from __future__ import annotations

import datetime
import logging
import os
from dataclasses import dataclass, asdict

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from models import (
    User, Badge, UserBadge, Comment, Connection, Thread, StudentProfile,
)
from extensions import db
from services import notification_service
from services import cache_service
from services.after_commit import run_after_commit

logger = logging.getLogger(__name__)


# ============================================================================
# BADGE DEFINITIONS (Seed Data)
# ============================================================================

BADGE_DEFINITIONS = [
    # ENGAGEMENT BADGES
    {
        "name": "First Post",
        "description": "Created your first post",
        "icon": "✍️",
        "category": "engagement",
        "rarity": "common",
        "criteria": {"posts_count": 1},
    },
    {
        "name": "Prolific Writer",
        "description": "Created 50 posts",
        "icon": "📝",
        "category": "engagement",
        "rarity": "rare",
        "criteria": {"posts_count": 50},
    },
    {
        "name": "Content Creator",
        "description": "Created 100 posts",
        "icon": "🎨",
        "category": "engagement",
        "rarity": "epic",
        "criteria": {"posts_count": 100},
    },
    {
        "name": "Helpful Contributor",
        "description": "Received 10 helpful reactions",
        "icon": "💡",
        "category": "quality",
        "rarity": "rare",
        "criteria": {"helpful_count": 10},
    },

    # QUALITY BADGES
    {
        "name": "Helpful Hero",
        "description": "Received 50 helpful reactions",
        "icon": "💡",
        "category": "quality",
        "rarity": "rare",
        "criteria": {"helpful_count": 50},
    },
    {
        "name": "Problem Solver",
        "description": "Had 10 answers marked as solutions",
        "icon": "🎯",
        "category": "quality",
        "rarity": "epic",
        "criteria": {"solutions_count": 10},
    },
    {
        "name": "Genius",
        "description": "Had 50 answers marked as solutions",
        "icon": "🧠",
        "category": "quality",
        "rarity": "legendary",
        "criteria": {"solutions_count": 50},
    },

    # CONSISTENCY BADGES
    {
        "name": "7-Day Streak",
        "description": "Active for 7 consecutive days",
        "icon": "🔥",
        "category": "consistency",
        "rarity": "rare",
        "criteria": {"login_streak": 7},
    },
    {
        "name": "30-Day Warrior",
        "description": "Active for 30 consecutive days",
        "icon": "⚔️",
        "category": "consistency",
        "rarity": "epic",
        "criteria": {"login_streak": 30},
    },
    {
        "name": "Unstoppable",
        "description": "Active for 100 consecutive days",
        "icon": "💎",
        "category": "consistency",
        "rarity": "legendary",
        "criteria": {"login_streak": 100},
    },

    # SOCIAL BADGES
    {
        "name": "Social Butterfly",
        "description": "Made 10 connections",
        "icon": "🦋",
        "category": "social",
        "rarity": "common",
        "criteria": {"connections_count": 10},
    },
    {
        "name": "Networker",
        "description": "Made 50 connections",
        "icon": "🤝",
        "category": "social",
        "rarity": "rare",
        "criteria": {"connections_count": 50},
    },
    {
        "name": "Thread Starter",
        "description": "Created 5 study threads",
        "icon": "🧵",
        "category": "social",
        "rarity": "rare",
        "criteria": {"threads_created": 5},
    },
    {
        "name": "Thread Leader",
        "description": "Created a thread with 10+ active members",
        "icon": "👑",
        "category": "social",
        "rarity": "epic",
        "criteria": {"thread_leader": True},
    },
    {
        "name": "Community Builder",
        "description": "Created 10 threads with 10+ members each",
        "icon": "🏗️",
        "category": "social",
        "rarity": "legendary",
        "criteria": {"threads_large": 10},
    },

    # MILESTONE BADGES
    {
        "name": "Early Adopter",
        "description": "Joined StudyHub in the first month",
        "icon": "🌟",
        "category": "milestone",
        "rarity": "epic",
        "criteria": {"early_adopter": True},
    },
    {
        "name": "Reputation Master",
        # 1001 == the start of the "Master" tier in services/reputation_levels
        # (1000 is still "Expert"), so the badge name matches the tier.
        "description": "Reached the Master level (1001+ reputation points)",
        "icon": "⭐",
        "category": "milestone",
        "rarity": "legendary",
        "criteria": {"reputation": 1001},
    },
    {
        "name": "Department Hero",
        "description": "Top 3 in your department leaderboard",
        "icon": "🏆",
        "category": "milestone",
        "rarity": "legendary",
        "criteria": {"department_rank": 3},
    },
]


# ============================================================================
# RESULT DATACLASS  (Document 2 §3.2 — replaces the ad hoc progress dict)
# ============================================================================

@dataclass
class BadgeProgress:
    current: int | float
    required: int | float
    percentage: float
    type: str
    remaining: int | float = 0
    message: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # Drop message when unset to match the original dict shape exactly
        # for the "special" (untrackable) badge case vs the normal case.
        if d.get("message") is None:
            d.pop("message", None)
        else:
            d.pop("remaining", None)
        return d


# ============================================================================
# SEEDING
# ============================================================================

# Rarity ordering for display. ORDER BY Badge.rarity DESC sorts the strings
# alphabetically (rare > legendary > epic > common), which is not a rarity
# order, so sort by this rank instead.
RARITY_RANK = {"common": 1, "rare": 2, "epic": 3, "legendary": 4}


def rarity_rank_expr():
    """SQL CASE expression mapping Badge.rarity to RARITY_RANK (unknown -> 0)."""
    from sqlalchemy import case
    return case(RARITY_RANK, value=Badge.rarity, else_=0)


_SEEDED_FIELDS = ("description", "icon", "category", "rarity", "criteria")


def seed_badges() -> dict:
    """
    Idempotent upsert of BADGE_DEFINITIONS (safe to run on every deploy and
    from several workers booting at once).

    Missing badges are inserted; existing ones have their description/
    icon/category/rarity/criteria refreshed to the current definition
    (``awarded_count`` and ``is_active`` are left alone), so a changed
    definition actually reaches the database. Commits -- this is a
    setup/CLI function, not part of the request path.

    Returns ``{"created": n, "updated": n, "unchanged": n}``.
    """
    for attempt in (1, 2):
        counts = {"created": 0, "updated": 0, "unchanged": 0}
        for badge_data in BADGE_DEFINITIONS:
            existing = Badge.query.filter_by(name=badge_data["name"]).first()
            if not existing:
                db.session.add(Badge(**{k: badge_data[k] for k in ("name",) + _SEEDED_FIELDS}))
                counts["created"] += 1
                continue
            changed = False
            for field in _SEEDED_FIELDS:
                if getattr(existing, field) != badge_data[field]:
                    setattr(existing, field, badge_data[field])
                    changed = True
            counts["updated" if changed else "unchanged"] += 1
        try:
            db.session.commit()
            logger.info("Badges seeded: %s", counts)
            return counts
        except IntegrityError:
            # Another process seeded the same rows between our SELECT and
            # INSERT; retry once -- the second pass sees them as existing.
            db.session.rollback()
            if attempt == 2:
                raise
    return counts  # pragma: no cover


# ============================================================================
# CRITERIA EVALUATION  (pure-ish — one query per criteria type, no writes)
# ============================================================================

def _early_adopter_cutoff(ctx: dict | None = None) -> datetime.datetime | None:
    """
    End of the early-adopter window: PLATFORM_LAUNCH_DATE (ISO date/time,
    env) + 30 days when configured, otherwise the earliest recorded
    ``joined_at`` + 30 days. Memoized in ``ctx`` for one evaluation pass.
    """
    if ctx is not None and "early_cutoff" in ctx:
        return ctx["early_cutoff"]
    cutoff = None
    launch = os.environ.get("PLATFORM_LAUNCH_DATE")
    if launch:
        try:
            cutoff = datetime.datetime.fromisoformat(launch) + datetime.timedelta(days=30)
        except ValueError:
            logger.warning("PLATFORM_LAUNCH_DATE=%r is not an ISO date; ignoring", launch)
    if cutoff is None:
        first_joined = (
            db.session.query(func.min(User.joined_at))
            .filter(User.joined_at.isnot(None))
            .scalar()
        )
        if first_joined is not None:
            cutoff = first_joined + datetime.timedelta(days=30)
    if ctx is not None:
        ctx["early_cutoff"] = cutoff
    return cutoff


def _user_qualifies(user: User, criteria: dict, ctx: dict | None = None) -> bool:
    """Evaluate a single badge's criteria dict against a user. Read-only."""
    user_id = user.id

    if "posts_count" in criteria:
        return user.total_posts >= criteria["posts_count"]

    if "helpful_count" in criteria:
        return user.total_helpful >= criteria["helpful_count"]

    if "solutions_count" in criteria:
        solutions = Comment.query.filter_by(student_id=user_id, is_solution=True).count()
        return solutions >= criteria["solutions_count"]

    if "login_streak" in criteria:
        return user.login_streak >= criteria["login_streak"]

    if "connections_count" in criteria:
        connections = Connection.query.filter(
            or_(Connection.requester_id == user_id, Connection.receiver_id == user_id),
            Connection.status == "accepted",
        ).count()
        return connections >= criteria["connections_count"]

    if "threads_created" in criteria:
        threads = Thread.query.filter_by(creator_id=user_id).count()
        return threads >= criteria["threads_created"]

    if "thread_leader" in criteria:
        large_thread = Thread.query.filter(
            Thread.creator_id == user_id, Thread.member_count >= 10
        ).first()
        return bool(large_thread)

    if "threads_large" in criteria:
        large_threads = Thread.query.filter(
            Thread.creator_id == user_id, Thread.member_count >= 10
        ).count()
        return large_threads >= criteria["threads_large"]

    if "reputation" in criteria:
        return user.reputation >= criteria["reputation"]

    if "early_adopter" in criteria:
        cutoff = _early_adopter_cutoff(ctx)
        if cutoff is None or user.joined_at is None:
            return False
        return user.joined_at <= cutoff

    if "department_rank" in criteria:
        if user.status != "approved":
            return False
        profile = StudentProfile.query.filter_by(user_id=user_id).first()
        if not profile or not profile.department:
            return False
        rank = (
            db.session.query(func.count(User.id))
            .join(StudentProfile)
            .filter(
                StudentProfile.department == profile.department,
                User.reputation > user.reputation,
                User.status == "approved",
            )
            .scalar()
            + 1
        )
        return rank <= criteria["department_rank"]

    return False


# ============================================================================
# AWARDING
# ============================================================================

def _award(user_id: int, badge: Badge, *, skip_cache_invalidation: bool = False) -> UserBadge | None:
    """
    Insert the UserBadge for an already-qualified user. Returns the new
    UserBadge, or None if a concurrent request awarded it first.
    Does NOT commit.
    """
    user_badge = UserBadge(user_id=user_id, badge_id=badge.id)
    try:
        # SAVEPOINT: a duplicate (UNIQUE user_id+badge_id) rolls back only
        # this insert, not the caller's whole transaction.
        with db.session.begin_nested():
            db.session.add(user_badge)
    except IntegrityError:
        logger.info("badge already awarded concurrently user_id=%s badge=%s", user_id, badge.name)
        return None

    # Atomic counter -- never `badge.awarded_count += 1` (lost updates).
    db.session.query(Badge).filter(Badge.id == badge.id).update(
        {Badge.awarded_count: Badge.awarded_count + 1}, synchronize_session=False
    )
    db.session.expire(badge, ["awarded_count"])

    # Delivery (unread counter + WebSocket push) is deferred until commit.
    notification_service.notify_badge_earned(user_id, badge)

    if not skip_cache_invalidation:
        run_after_commit(cache_service.delete, f"sh:1:badge:progress:{user_id}")

    logger.info("badge_awarded user_id=%s badge=%s", user_id, badge.name)
    return user_badge


def check_and_award_badge(
    user_id: int, badge_name: str, *, skip_cache_invalidation: bool = False
) -> UserBadge | None:
    """
    Check if a user qualifies for a named badge and award it if so.

    Never commits: the caller owns the transaction (see module docstring).
    ``skip_cache_invalidation`` lets a batching caller invalidate the
    user's badge-progress cache once itself.

    Returns the new UserBadge if awarded; None if already earned, not
    qualified, the user/badge is unknown, or it was awarded concurrently.
    A missing badge row is logged -- it means the badges table was never
    seeded (see seed_badges / ``flask seed-badges``).
    """
    badge = Badge.query.filter_by(name=badge_name, is_active=True).first()
    if not badge:
        logger.warning(
            "check_and_award_badge: badge %r not found or inactive -- "
            "has seed_badges() been run?", badge_name)
        return None

    if UserBadge.query.filter_by(user_id=user_id, badge_id=badge.id).first():
        return None

    user = User.query.get(user_id)
    if not user:
        return None

    if not _user_qualifies(user, badge.criteria):
        return None

    return _award(user_id, badge, skip_cache_invalidation=skip_cache_invalidation)


def check_all_badges_for_user(user_id: int) -> list[Badge]:
    """
    Evaluate every active badge for a user in one pass.

    Loads the badge list, the user and the user's earned set once (instead
    of re-querying them per badge), evaluates only unearned badges, and
    invalidates the progress cache once after commit. Does NOT commit.
    Returns the list of newly awarded Badge objects.
    """
    user = User.query.get(user_id)
    if not user:
        return []

    all_badges = Badge.query.filter_by(is_active=True).all()
    earned_ids = {
        row[0] for row in db.session.query(UserBadge.badge_id).filter_by(user_id=user_id)
    }

    awarded: list[Badge] = []
    ctx: dict = {}
    for badge in all_badges:
        if badge.id in earned_ids:
            continue
        if not _user_qualifies(user, badge.criteria, ctx):
            continue
        if _award(user_id, badge, skip_cache_invalidation=True):
            awarded.append(badge)

    if awarded:
        run_after_commit(cache_service.delete, f"sh:1:badge:progress:{user_id}")

    return awarded


# ============================================================================
# PROGRESS CALCULATION
# ============================================================================

def calculate_badge_progress(user_id: int, badge_id: int) -> dict | None:
    """
    Calculate a user's progress toward a specific badge.

    Returns a plain dict (via BadgeProgress.to_dict()) matching the exact
    shape the original badges.py function returned, so every existing
    caller (badges.py's routes, analytics.py::generate_insights) keeps
    working without a response-shape change.
    """
    badge = Badge.query.get(badge_id)
    user = User.query.get(user_id)

    if not badge or not user:
        return None

    criteria = badge.criteria
    current: int | float = 0
    required: int | float = 0
    progress_type = ""

    if "posts_count" in criteria:
        current, required, progress_type = user.total_posts, criteria["posts_count"], "posts"

    elif "helpful_count" in criteria:
        current, required, progress_type = user.total_helpful, criteria["helpful_count"], "helpful reactions"

    elif "solutions_count" in criteria:
        current = Comment.query.filter_by(student_id=user_id, is_solution=True).count()
        required, progress_type = criteria["solutions_count"], "solutions"

    elif "login_streak" in criteria:
        current, required, progress_type = user.login_streak, criteria["login_streak"], "day streak"

    elif "connections_count" in criteria:
        current = Connection.query.filter(
            or_(Connection.requester_id == user_id, Connection.receiver_id == user_id),
            Connection.status == "accepted",
        ).count()
        required, progress_type = criteria["connections_count"], "connections"

    elif "threads_created" in criteria:
        current = Thread.query.filter_by(creator_id=user_id).count()
        required, progress_type = criteria["threads_created"], "threads created"

    elif "reputation" in criteria:
        current, required, progress_type = user.reputation, criteria["reputation"], "reputation points"

    else:
        # Special / untrackable badges (thread_leader, threads_large, early_adopter,
        # department_rank) — matches the original "can't track progress" branch.
        return BadgeProgress(
            current=0, required=1, percentage=0, type="special",
            message="Complete special requirements",
        ).to_dict()

    percentage = min((current / required) * 100, 100) if required > 0 else 0

    return BadgeProgress(
        current=current,
        required=required,
        percentage=round(percentage, 1),
        type=progress_type,
        remaining=max(required - current, 0),
    ).to_dict()
