"""
services/reputation_service.py

Document 2 §3.1. Owns point-awarding logic and milestone-checking, moved
out of routes/student/reputation.py.

Moved verbatim in behavior (with one deliberate change, see below) from
reputation.py:
    - REPUTATION_ACTIONS
    - award_reputation
    - check_and_award_milestone

Deliberate behavior change during this move (flagged explicitly per
Document 1 §1's principle of not silently bundling fixes into a pure
move): award_reputation() used to call db.session.commit() internally.
Per Document 2 §5's transaction-boundary convention, service functions
mutate the session but do NOT commit — that's the calling route's job, so
several operations in one request succeed or fail together as one
transaction. The commit has been REMOVED here.

This is a real behavior change for two existing call sites that relied on
the internal commit with nothing committing afterward:
    - posts.py::mark_comment_helpful (committed its own changes BEFORE
      calling award_reputation, with nothing after it — fixed alongside
      this move to commit after the award_reputation call instead)
    - reputation.py::award_reputation_endpoint (had no commit anywhere in
      its try block — fixed alongside this move to commit after the
      award_reputation call)
Both fixes ship in the same pass as this move since leaving either
uncommitted would silently stop persisting reputation changes — see the
route-level comments at each fixed call site for the specific diff.

check_and_award_milestone was already commit-free (it only ever calls
award_reputation, never touches db.session directly), so no change there.

Per Document 2 §2's layering rule: no Flask imports, no `request`/`session`/`g`.
Level-up notifications are built via services.notification_service.notify_level_up(...)
rather than constructed inline here, keeping the notification field-list in
exactly one place (matching how badge_service.check_and_award_badge already
calls notification_service.notify_badge_earned(...)).

REMEDIATION NOTE (Phase 1 correction pass): this module previously imported
get_reputation_level from routes.student.reputation_levels, which is a
services -> routes import and violates the one dependency rule this whole
migration depends on. reputation_levels.py has moved to
services/reputation_levels.py (routes/student/reputation_levels.py is now
just a re-export shim); this file imports from the new location.
"""

from enum import Enum

from models import User, ReputationHistory, Post
from extensions import db
from services.reputation_levels import get_reputation_level
from services import notification_service


class ReputationAction(str, Enum):
    """
    Document 1 §3.3 B-5 fix: REPUTATION_ACTIONS' keys were previously
    referenced elsewhere as raw strings (e.g.
    posts.py::award_reputation(commenter.id, "comment_marked_solution", ...)),
    so a typo would silently no-op inside award_reputation's
    `if not action and not custom_points: return None` fallback instead of
    failing loudly. This enum gives call sites an attribute to reference
    instead (ReputationAction.COMMENT_MARKED_SOLUTION), so a typo now fails
    at import/attribute-access time.

    Kept in sync 1:1 with REPUTATION_ACTIONS below — the enum VALUE is the
    dict key, so `ReputationAction.POST_10_LIKES.value == "post_10_likes"`
    and `REPUTATION_ACTIONS[ReputationAction.POST_10_LIKES.value]` both work.
    """
    POST_10_LIKES = "post_10_likes"
    POST_50_LIKES = "post_50_likes"
    POST_100_LIKES = "post_100_likes"
    COMMENT_MARKED_SOLUTION = "comment_marked_solution"
    COMMENT_MARKED_HELPFUL = "comment_marked_helpful"
    POST_MARKED_HELPFUL = "post_marked_helpful"
    POST_DISLIKED = "post_disliked"
    CONTENT_REPORTED = "content_reported"
    HELPFUL_STREAK_7 = "helpful_streak_7"
    THREAD_CREATED = "thread_created"
    THREAD_COMPLETED = "thread_completed"


REPUTATION_ACTIONS = {
    "post_10_likes": {"points": 5, "description": "Post reached 10 likes"},
    "post_50_likes": {"points": 20, "description": "Post reached 50 likes"},
    "post_100_likes": {"points": 50, "description": "Post reached 100 likes"},
    "comment_marked_solution": {"points": 15, "description": "Comment marked as solution"},
    "comment_marked_helpful": {"points": 3, "description": "Comment marked helpful"},
    "post_marked_helpful": {"points": 5, "description": "Post marked helpful"},
    "post_disliked": {"points": -2, "description": "Post received dislike"},
    "content_reported": {"points": -10, "description": "Content reported"},
    "helpful_streak_7": {"points": 10, "description": "7 helpful reactions in a week"},
    "thread_created": {"points": 3, "description": "Created study thread"},
    "thread_completed": {"points": 10, "description": "Thread reached 10+ members"},
}


def award_reputation(user_id, action_key, related_type=None, related_id=None, custom_points=None):
    """
    Award (or deduct) reputation points for a user and record the change
    in ReputationHistory. Creates a level-up Notification if this change
    crosses a reputation-level threshold.

    Does NOT commit — the caller's existing try/except/commit block stays
    in charge of the transaction (Document 2 §5). Every existing caller
    must ensure a db.session.commit() happens after this call (either
    their own, or the route's shared one) — see the module docstring above
    for the two call sites that needed a fix alongside this move.

    Returns the ReputationHistory row (session-pending, not yet committed),
    or None if the user doesn't exist or no valid action/points were given.
    """
    user = User.query.get(user_id)
    if not user:
        return None

    action = REPUTATION_ACTIONS.get(action_key)
    if not action and not custom_points:
        return None

    points_change = custom_points if custom_points else action["points"]

    reputation_before = user.reputation
    user.reputation += points_change
    user.reputation = max(0, user.reputation)
    user.update_reputation_level()

    history = ReputationHistory(
        user_id=user_id,
        action=action_key if action_key else "custom",
        points_change=points_change,
        related_type=related_type,
        related_id=related_id,
        reputation_before=reputation_before,
        reputation_after=user.reputation
    )
    db.session.add(history)

    old_level = get_reputation_level(reputation_before)
    new_level = get_reputation_level(user.reputation)

    if old_level["name"] != new_level["name"]:
        # notify_level_up() calls db.session.add() + flush() only — it does
        # not commit, matching this function's own no-commit convention.
        notification_service.notify_level_up(user_id, new_level)

    return history


def check_and_award_milestone(user_id, post_id=None, comment_id=None):
    """
    Check whether a post/comment interaction just crossed a reputation
    milestone (post like-count thresholds today) and award reputation if so.

    Does NOT commit — same convention as award_reputation() above (this
    function only ever calls award_reputation, never touches db.session
    directly, so it was already commit-free before this move).
    """
    if post_id:
        post = Post.query.get(post_id)
        if post and post.student_id == user_id:
            if post.positive_reactions_count == 10:
                award_reputation(user_id, "post_10_likes", "post", post_id)
            elif post.positive_reactions_count == 50:
                award_reputation(user_id, "post_50_likes", "post", post_id)
            elif post.positive_reactions_count == 100:
                award_reputation(user_id, "post_100_likes", "post", post_id)
