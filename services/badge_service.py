"""
services/badge_service.py

Badge definitions, awarding logic, and progress calculation.
Extracted from routes/student/badges.py (Document 1 §2, Document 2 §3.2).

Per Document 2 §2's layering rule: no Flask imports, no request/session/g.
The one deliberate exception to the "services never commit" convention
(Document 2 §5) is check_and_award_badge's `commit` parameter — kept
exactly as it was, for exactly the same reason: check_all_badges_for_user
needs to evaluate up to 18 badges without 18 separate round-trips.

Consumers:
  - routes/student/badges.py — the primary owner, every route becomes a
    thin wrapper around these functions.
  - routes/student/analytics.py::generate_insights — calls
    calculate_badge_progress directly today (imported at module level
    there to avoid a circular-import/repeated-lookup cost); switches to
    importing from here instead of from badges.py.
  - Anywhere check_all_badges_for_user needs to run after a significant
    user action (post created, streak updated, etc.) — homework_system.py
    and others may call this once wired up.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, asdict

from sqlalchemy import func, or_

from models import (
    User, Badge, UserBadge, Comment, Connection, Thread, StudentProfile,
)
from extensions import db
from services import notification_service

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
        "color": "#6B7280",
    },
    {
        "name": "Prolific Writer",
        "description": "Created 50 posts",
        "icon": "📝",
        "category": "engagement",
        "rarity": "rare",
        "criteria": {"posts_count": 50},
        "color": "#3B82F6",
    },
    {
        "name": "Content Creator",
        "description": "Created 100 posts",
        "icon": "🎨",
        "category": "engagement",
        "rarity": "epic",
        "criteria": {"posts_count": 100},
        "color": "#8B5CF6",
    },
    {
        "name": "Helpful Contributor",
        "description": "Received 10 helpful reactions",
        "icon": "💡",
        "category": "quality",
        "rarity": "rare",
        "criteria": {"helpful_count": 10},
        "color": "#3B82F6",
    },

    # QUALITY BADGES
    {
        "name": "Helpful Hero",
        "description": "Received 50 helpful reactions",
        "icon": "💡",
        "category": "quality",
        "rarity": "rare",
        "criteria": {"helpful_count": 50},
        "color": "#3B82F6",
    },
    {
        "name": "Problem Solver",
        "description": "Had 10 answers marked as solutions",
        "icon": "🎯",
        "category": "quality",
        "rarity": "epic",
        "criteria": {"solutions_count": 10},
        "color": "#8B5CF6",
    },
    {
        "name": "Genius",
        "description": "Had 50 answers marked as solutions",
        "icon": "🧠",
        "category": "quality",
        "rarity": "legendary",
        "criteria": {"solutions_count": 50},
        "color": "#EF4444",
    },

    # CONSISTENCY BADGES
    {
        "name": "7-Day Streak",
        "description": "Active for 7 consecutive days",
        "icon": "🔥",
        "category": "consistency",
        "rarity": "rare",
        "criteria": {"login_streak": 7},
        "color": "#F59E0B",
    },
    {
        "name": "30-Day Warrior",
        "description": "Active for 30 consecutive days",
        "icon": "⚔️",
        "category": "consistency",
        "rarity": "epic",
        "criteria": {"login_streak": 30},
        "color": "#8B5CF6",
    },
    {
        "name": "Unstoppable",
        "description": "Active for 100 consecutive days",
        "icon": "💎",
        "category": "consistency",
        "rarity": "legendary",
        "criteria": {"login_streak": 100},
        "color": "#EF4444",
    },

    # SOCIAL BADGES
    {
        "name": "Social Butterfly",
        "description": "Made 10 connections",
        "icon": "🦋",
        "category": "social",
        "rarity": "common",
        "criteria": {"connections_count": 10},
        "color": "#6B7280",
    },
    {
        "name": "Networker",
        "description": "Made 50 connections",
        "icon": "🤝",
        "category": "social",
        "rarity": "rare",
        "criteria": {"connections_count": 50},
        "color": "#3B82F6",
    },
    {
        "name": "Thread Starter",
        "description": "Created 5 study threads",
        "icon": "🧵",
        "category": "social",
        "rarity": "rare",
        "criteria": {"threads_created": 5},
        "color": "#3B82F6",
    },
    {
        "name": "Thread Leader",
        "description": "Created a thread with 10+ active members",
        "icon": "👑",
        "category": "social",
        "rarity": "epic",
        "criteria": {"thread_leader": True},
        "color": "#8B5CF6",
    },
    {
        "name": "Community Builder",
        "description": "Created 10 threads with 10+ members each",
        "icon": "🏗️",
        "category": "social",
        "rarity": "legendary",
        "criteria": {"threads_large": 10},
        "color": "#EF4444",
    },

    # MILESTONE BADGES
    {
        "name": "Early Adopter",
        "description": "Joined StudyHub in the first month",
        "icon": "🌟",
        "category": "milestone",
        "rarity": "epic",
        "criteria": {"early_adopter": True},
        "color": "#8B5CF6",
    },
    {
        "name": "Reputation Master",
        "description": "Reached 1000 reputation points",
        "icon": "⭐",
        "category": "milestone",
        "rarity": "legendary",
        "criteria": {"reputation": 1000},
        "color": "#EF4444",
    },
    {
        "name": "Department Hero",
        "description": "Top 3 in your department leaderboard",
        "icon": "🏆",
        "category": "milestone",
        "rarity": "legendary",
        "criteria": {"department_rank": 3},
        "color": "#EF4444",
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

def seed_badges() -> None:
    """
    Seed initial badges into the database. Run once during setup.

    Does commit (setup/CLI-only function, not part of the request path —
    no transaction-boundary conflict with Document 2 §5's convention).
    """
    for badge_data in BADGE_DEFINITIONS:
        existing = Badge.query.filter_by(name=badge_data["name"]).first()
        if not existing:
            badge = Badge(
                name=badge_data["name"],
                description=badge_data["description"],
                icon=badge_data["icon"],
                category=badge_data["category"],
                rarity=badge_data["rarity"],
                criteria=badge_data["criteria"],
            )
            db.session.add(badge)

    db.session.commit()
    logger.info("Badges seeded successfully")


# ============================================================================
# CRITERIA EVALUATION  (pure-ish — one query per criteria type, no writes)
# ============================================================================

def _user_qualifies(user: User, criteria: dict) -> bool:
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
        first_user = User.query.order_by(User.joined_at.asc()).first()
        if not first_user:
            return False
        cutoff = first_user.joined_at + datetime.timedelta(days=30)
        return user.joined_at <= cutoff

    if "department_rank" in criteria:
        profile = StudentProfile.query.filter_by(user_id=user_id).first()
        if not profile:
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

def check_and_award_badge(user_id: int, badge_name: str, *, commit: bool = True) -> UserBadge | None:
    """
    Check if a user qualifies for a named badge and award it if so.

    `commit` defaults to True (single-award call sites, e.g. an admin
    award endpoint). check_all_badges_for_user passes commit=False and
    does one commit after evaluating every badge — this parameter is a
    deliberate, documented exception to the "services never commit"
    convention (Document 2 §3.2 / §5), kept unchanged from the original
    badges.py implementation.

    Returns the new UserBadge if awarded, None if already earned or the
    user doesn't qualify.
    """
    badge = Badge.query.filter_by(name=badge_name).first()
    if not badge:
        return None

    existing = UserBadge.query.filter_by(user_id=user_id, badge_id=badge.id).first()
    if existing:
        return None

    user = User.query.get(user_id)
    if not user:
        return None

    if not _user_qualifies(user, badge.criteria):
        return None

    user_badge = UserBadge(user_id=user_id, badge_id=badge.id)
    db.session.add(user_badge)
    badge.awarded_count += 1

    notification_service.notify_badge_earned(user_id, badge)

    if commit:
        db.session.commit()

    return user_badge


def check_all_badges_for_user(user_id: int) -> list[Badge]:
    """
    Check all active badges for a user in one pass, committing once.

    Called after significant actions (post created, streak updated, etc.).
    Returns the list of newly awarded Badge objects.
    """
    all_badges = Badge.query.filter_by(is_active=True).all()
    awarded = []

    for badge in all_badges:
        result = check_and_award_badge(user_id, badge.name, commit=False)
        if result:
            awarded.append(badge)

    if awarded:
        db.session.commit()

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
