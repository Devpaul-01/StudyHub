"""
services/reputation_service.py

Owns point-awarding logic and milestone-checking (moved out of
routes/student/reputation.py by Document 2 §3.1).

Transaction convention: functions here mutate the session but do NOT
commit -- the calling route owns the (single) transaction so a user action
and its reward succeed or fail together. Side effects that must not
outlive a rolled-back transaction (cache invalidation, notifications'
Redis/WebSocket delivery, badge-check job enqueue) are registered through
services.after_commit and only run after a successful COMMIT.

Correctness properties of award_reputation():

  * Concurrency: the user's row is locked (SELECT ... FOR UPDATE) before
    the balance is read, so two simultaneous awards to the same user
    serialize instead of both reading N and both writing N + delta (the
    classic lost update). On SQLite (tests) FOR UPDATE is a no-op, but
    SQLite already serializes writers.
  * Idempotency: one-shot awards pass an ``idempotency_key``. A key that
    already exists in the ledger returns the existing row and changes
    nothing; the UNIQUE index on reputation_history.idempotency_key is the
    database-level backstop.
  * Ledger integrity: ``points_change`` records the EFFECTIVE delta
    (reputation_after - reputation_before), so SUM(points_change)
    reproduces users.reputation even when the zero floor clamps a
    deduction.
  * Reversal: reverse_reputation() appends a compensating row (the ledger
    stays append-only) for the newest not-yet-reversed award of an
    action/related-object pair.

Per Document 2 §2's layering rule: no Flask imports, no request/session/g.
"""

import logging
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from models import User, ReputationHistory, Post
from extensions import db
from services.reputation_levels import get_reputation_level
from services import notification_service
from services import cache_service
from services.after_commit import run_after_commit

logger = logging.getLogger(__name__)


# "active" marks actions that some code path in the application actually
# awards today. Inactive entries are kept so the point values are defined
# in one place when they are wired up, but they are NOT advertised to users
# (see awardable_actions(), used by GET /reputation/levels).
REPUTATION_ACTIONS = {
    "post_10_likes": {"points": 5, "description": "Post reached 10 likes", "active": True},
    "post_50_likes": {"points": 20, "description": "Post reached 50 likes", "active": True},
    "post_100_likes": {"points": 50, "description": "Post reached 100 likes", "active": True},
    "comment_marked_solution": {"points": 15, "description": "Comment marked as solution", "active": True},
    "comment_marked_helpful": {"points": 3, "description": "Comment marked helpful", "active": True},
    "post_marked_helpful": {"points": 5, "description": "Post marked helpful", "active": False},
    "post_disliked": {"points": -2, "description": "Post received dislike", "active": False},
    "content_reported": {"points": -10, "description": "Content reported", "active": False},
    "helpful_streak_7": {"points": 10, "description": "7 helpful reactions in a week", "active": False},
    "thread_created": {"points": 3, "description": "Created study thread", "active": False},
    "thread_completed": {"points": 10, "description": "Thread reached 10+ members", "active": False},
}

# (like-count threshold, action key) -- ascending.
LIKE_MILESTONES = (
    (10, "post_10_likes"),
    (50, "post_50_likes"),
    (100, "post_100_likes"),
)


def awardable_actions():
    """REPUTATION_ACTIONS restricted to the ones that can really be earned."""
    return {k: v for k, v in REPUTATION_ACTIONS.items() if v.get("active")}


def action_points(action_key):
    """Point value of an action (single source of truth for UI/notification text)."""
    return REPUTATION_ACTIONS[action_key]["points"]


# ----------------------------------------------------------------------------
# Cache invalidation (runs after commit -- see module docstring)
# ----------------------------------------------------------------------------

def _invalidate_reputation_caches(user_id):
    """
    Invalidate the user's own point-total/rank/breakdown/analytics caches.

    Deliberately NOT invalidated: sh:1:lb:global:* (any period/department/
    page combination). One user's change could in principle shift every
    other user's relative rank on a global page; hard-invalidating every
    cached leaderboard page on every reputation change would defeat the
    cache. The 60 s TTL on lb:global:* is the deliberate trade-off (plan
    §4.2/§5.1): leaderboard *position* pages can lag by up to 60 s.

    rank/nearby/breakdown entries are keyed by a per-user version, so a
    single bump invalidates all of them in O(1) (no keyspace SCAN).
    """
    cache_service.delete(f"sh:1:rep:me:{user_id}")
    cache_service.bump_user_version(user_id)
    cache_service.delete(f"sh:1:an:overview:{user_id}")
    cache_service.delete(f"sh:1:badge:progress:{user_id}")


def _mark_duplicate(history):
    """
    Detached read-only view of an already-recorded award, flagged
    ``duplicate=True``. A copy rather than the ORM row itself: the session's
    identity map hands every caller the same instance, so flagging the real
    row would also flip ``duplicate`` on the object the original call
    returned.
    """
    return SimpleNamespace(
        id=history.id,
        user_id=history.user_id,
        action=history.action,
        points_change=history.points_change,
        related_type=history.related_type,
        related_id=history.related_id,
        reputation_before=history.reputation_before,
        reputation_after=history.reputation_after,
        idempotency_key=history.idempotency_key,
        duplicate=True,
        leveled_up=False,
        new_level_name=None,
    )


def _lock_user(user_id):
    """Load the user with a row lock, refreshing any stale identity-map copy."""
    return (
        db.session.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .populate_existing()
        .first()
    )


def award_reputation(
    user_id,
    action_key,
    related_type=None,
    related_id=None,
    custom_points=None,
    idempotency_key=None,
):
    """
    Award (or deduct) reputation points and record the change in
    ReputationHistory. Creates a level-up notification if the change
    crosses a level threshold.

    Does NOT commit -- the caller's transaction owns that.

    Returns the ReputationHistory row (session-pending), carrying the
    plain attributes ``leveled_up``, ``new_level_name`` and ``duplicate``
    (True when ``idempotency_key`` had already been used, in which case
    nothing was changed). Returns None if the user does not exist or no
    valid action/points were given.
    """
    action = REPUTATION_ACTIONS.get(action_key)
    if action is None and custom_points is None:
        logger.warning("award_reputation: unknown action %r (user_id=%s) -- ignored",
                       action_key, user_id)
        return None

    requested = custom_points if custom_points is not None else action["points"]
    if not requested:
        return None

    user = _lock_user(user_id)
    if not user:
        return None

    if idempotency_key:
        existing = ReputationHistory.query.filter_by(idempotency_key=idempotency_key).first()
        if existing:
            return _mark_duplicate(existing)

    reputation_before = user.reputation or 0
    reputation_after = max(0, reputation_before + requested)

    history = ReputationHistory(
        user_id=user_id,
        action=action_key if action_key else "custom",
        # Effective delta -- see module docstring (ledger integrity).
        points_change=reputation_after - reputation_before,
        related_type=related_type,
        related_id=related_id,
        reputation_before=reputation_before,
        reputation_after=reputation_after,
        idempotency_key=idempotency_key,
    )

    if idempotency_key:
        # Insert inside a savepoint: if a concurrent request slipped the
        # same key in, the UNIQUE index rejects it and only the savepoint
        # is rolled back -- the outer transaction (and the user's balance,
        # not yet touched) stay intact.
        try:
            with db.session.begin_nested():
                db.session.add(history)
        except IntegrityError:
            existing = ReputationHistory.query.filter_by(idempotency_key=idempotency_key).first()
            if existing:
                return _mark_duplicate(existing)
            raise
    else:
        db.session.add(history)

    user.reputation = reputation_after
    user.update_reputation_level()

    run_after_commit(_invalidate_reputation_caches, user_id)

    old_level = get_reputation_level(reputation_before)
    new_level = get_reputation_level(reputation_after)
    leveled_up = old_level["name"] != new_level["name"]

    if leveled_up:
        # notify_level_up() only adds + flushes; delivery is deferred.
        notification_service.notify_level_up(user_id, new_level)

    logger.info(
        "reputation_award user_id=%s action=%s requested=%s applied=%s before=%s after=%s "
        "related=%s:%s key=%s leveled_up=%s",
        user_id, history.action, requested, history.points_change,
        reputation_before, reputation_after, related_type, related_id,
        idempotency_key, leveled_up,
    )

    # Plain attributes (not a return-type change): callers that want a
    # reputation_level_up live activity check history.leveled_up.
    history.duplicate = False
    history.leveled_up = leveled_up
    history.new_level_name = new_level["name"] if leveled_up else None

    return history


def reverse_reputation(user_id, action_key, related_type, related_id):
    """
    Take back the newest not-yet-reversed award of ``action_key`` for the
    given related object by appending a compensating (negative) ledger row.

    Idempotent: each award can be reversed at most once (UNIQUE key
    ``rev:<award row id>``). Returns the new ReputationHistory row, or None
    if there was nothing to reverse. Does NOT commit.
    """
    awards = (
        ReputationHistory.query
        .filter_by(user_id=user_id, action=action_key,
                   related_type=related_type, related_id=related_id)
        .filter(ReputationHistory.points_change > 0)
        .order_by(ReputationHistory.id.desc())
        .all()
    )
    for award in awards:
        reversal_key = f"rev:{award.id}"
        if ReputationHistory.query.filter_by(idempotency_key=reversal_key).first():
            continue
        return award_reputation(
            user_id,
            f"{action_key}_reversed",
            related_type,
            related_id,
            custom_points=-award.points_change,
            idempotency_key=reversal_key,
        )
    return None


def check_and_award_milestone(user_id, post_id=None):
    """
    Award each like-count milestone the post has reached and not yet been
    paid for -- exactly once per (post, milestone), ever.

    The trigger is ``count >= threshold`` plus the ledger, not an equality
    test on a mutable counter: toggling a like around a boundary can no
    longer re-award, and a counter that jumped past a boundary (two
    simultaneous likes) still pays the milestone it skipped.

    Legacy rows written before idempotency keys existed (key IS NULL) are
    matched on (action, post) so they are never paid a second time.

    Does NOT commit. Returns the list of newly created history rows.
    """
    if not post_id:
        return []
    post = Post.query.get(post_id)
    if not post or post.student_id != user_id:
        return []

    count = post.positive_reactions_count or 0
    reached = [(t, a) for t, a in LIKE_MILESTONES if count >= t]
    if not reached:
        return []

    already = {
        row[0]
        for row in db.session.query(ReputationHistory.action)
        .filter(
            ReputationHistory.user_id == user_id,
            ReputationHistory.related_type == "post",
            ReputationHistory.related_id == post_id,
            ReputationHistory.action.in_([a for _, a in reached]),
        )
        .all()
    }

    awarded = []
    for _, action_key in reached:
        if action_key in already:
            continue
        history = award_reputation(
            user_id, action_key, "post", post_id,
            idempotency_key=f"{action_key}:post:{post_id}",
        )
        if history is not None and not history.duplicate:
            awarded.append(history)
    return awarded
