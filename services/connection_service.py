"""
services/connection_service.py

Document 2 §3.4. Owns connection-blocking logic, moved out of
routes/student/helpers.py — helpers.py is being repositioned as an
HTTP-layer-only file (Document 2 §2.1), and blocking logic is exactly the
kind of business logic this migration is about relocating.

is_user_blocked / block_connection / unblock_connection are moved here
VERBATIM (no behavior change) from helpers.py. helpers.py keeps a thin
re-export shim for one release cycle so existing callers (e.g.
connections.py's `from routes.student.helpers import block_connection,
unblock_connection`) keep working unchanged — see Document 5's rollout
notes on this specific move ("it's the one place this migration touches
code that was *just* fixed in the prior phase, so it gets extra care").

Per Document 2 §5's transaction-boundary convention: these functions
mutate the session (db.session.add / attribute assignment / db.session.delete)
but do NOT call db.session.commit() or db.session.rollback() — that stays
the calling route's responsibility. This is unchanged from how these
functions already behaved in helpers.py.

Per Document 2 §2's layering rule: no Flask imports, no `request`/`session`/`g`.
"""

import datetime
import html
import logging
from collections import Counter

import bleach
from sqlalchemy import or_, and_, func
from sqlalchemy.exc import IntegrityError

from models import Connection, Post
from extensions import db
# Plan §4.8/§17.7: get_mutual_connection_count and the single-pair
# get_connection_health wrapper (not the batch form — see its own docstring
# below) are cached. Both use manual cache-aside rather than the @cached
# decorator: the mutual-count key needs canonical (min, max) pair ordering
# per plan §3.2 ("Pairs use canonical ordering"), which the decorator's
# literal-parameter-name interpolation can't express — {user1_id}:{user2_id}
# would produce two different keys for the same logical pair depending on
# call-argument order, exactly the bug §3.2 warns against. Computing the
# canonical key here, before the cache lookup, is the only way to satisfy
# that requirement without modifying cache_service.py itself (which Phase 2
# must not touch — every cache in this plan is a consumer of the Phase 0
# API, not a modifier of it).
from services import cache_service

logger = logging.getLogger(__name__)


def _find_connection_between(user_a_id, user_b_id, status=None):
    """Find the Connection row between two users, in either direction."""
    query = Connection.query.filter(
        or_(
            and_(Connection.requester_id == user_a_id, Connection.receiver_id == user_b_id),
            and_(Connection.requester_id == user_b_id, Connection.receiver_id == user_a_id),
        )
    )
    if status:
        query = query.filter(Connection.status == status)
    return query.first()


def is_user_blocked(user_a_id, user_b_id):
    """
    Return (blocked_by_a, blocked_by_b): whether user_a has blocked user_b,
    and whether user_b has blocked user_a. At most one of these can be True
    at a time under the current one-row-per-pair model.

    This is the single "is this pair blocked" check — use it everywhere
    instead of re-deriving blocking direction from requester_id/receiver_id.
    """
    connection = _find_connection_between(user_a_id, user_b_id, status="blocked")

    if not connection or not connection.blocked_by_id:
        return False, False

    return connection.blocked_by_id == user_a_id, connection.blocked_by_id == user_b_id


def _pair_filter(user_a_id, user_b_id):
    return or_(
        and_(Connection.requester_id == user_a_id, Connection.receiver_id == user_b_id),
        and_(Connection.requester_id == user_b_id, Connection.receiver_id == user_a_id),
    )


def block_connection(blocker_id, blocked_id):
    """
    Block `blocked_id` on behalf of `blocker_id`.

    Audit CON-10 / CON-08:
      * IDEMPOTENT and never steals ownership: if the pair is already
        blocked (by either user) the existing row is returned untouched, so a
        blocked user can no longer "re-block" to take over the block.
      * ATOMIC: a single conditional UPDATE (status != 'blocked'); if there is
        no row yet, the INSERT runs inside a SAVEPOINT and a lost race
        (uq_connections_pair) falls back to re-reading/updating the row.

    Reuses the existing Connection row (requester_id/receiver_id are never
    mutated). Does not commit; the caller owns the transaction.

    Returns the Connection row.
    """
    now = datetime.datetime.utcnow()

    def _try_update():
        return Connection.query.filter(
            _pair_filter(blocker_id, blocked_id),
            Connection.status != "blocked",
        ).update(
            {
                "status": "blocked",
                "blocked_by_id": blocker_id,
                "responded_at": now,
            },
            synchronize_session=False,
        )

    if _try_update():
        db.session.expire_all()
        return _find_connection_between(blocker_id, blocked_id)

    existing = _find_connection_between(blocker_id, blocked_id)
    if existing:
        return existing  # already blocked: keep the original owner

    connection = Connection(
        requester_id=blocker_id,
        receiver_id=blocked_id,
        status="blocked",
        blocked_by_id=blocker_id,
        requested_at=now,
        responded_at=now,
    )
    try:
        with db.session.begin_nested():
            db.session.add(connection)
            db.session.flush()
    except IntegrityError:
        # Lost a creation race: another request inserted the pair first.
        if _try_update():
            db.session.expire_all()
        return _find_connection_between(blocker_id, blocked_id)

    return connection


def unblock_connection(unblocker_id, other_id, restore_to_accepted=False):
    """
    Remove a block between `unblocker_id` and `other_id`. Only the user who
    created the block (Connection.blocked_by_id) may remove it.

    Audit CON-02: unblocking ALWAYS deletes the row. The two users are no
    longer connected and need a fresh request to reconnect, so unblocking can
    never manufacture a mutual connection nobody agreed to.

    `restore_to_accepted` is accepted for backward compatibility with old
    callers and is deliberately IGNORED.

    Does not commit. Returns (success: bool, error_message: str | None).
    """
    connection = _find_connection_between(unblocker_id, other_id, status="blocked")

    if not connection:
        return False, "User is not blocked"

    if connection.blocked_by_id != unblocker_id:
        return False, "Not authorized"

    # Conditional delete: no-op if someone else changed the row meanwhile.
    deleted = Connection.query.filter(
        Connection.id == connection.id,
        Connection.status == "blocked",
        Connection.blocked_by_id == unblocker_id,
    ).delete(synchronize_session=False)
    if not deleted:
        return False, "User is not blocked"

    db.session.expire_all()
    return True, None


# ============================================================================
# ATOMIC STATE TRANSITIONS  (audit CON-08)
#
# Every transition is ONE conditional UPDATE/DELETE whose WHERE clause carries
# the expected current state, so concurrent requests (different workers) can
# never both "win": the loser sees rowcount == 0. No locks / new infra needed.
# All return the affected row count; none commit.
# ============================================================================

def accept_pending_connection(connection_id, receiver_id):
    """pending -> accepted, only by the receiver. Returns rowcount (0 or 1)."""
    return Connection.query.filter(
        Connection.id == connection_id,
        Connection.receiver_id == receiver_id,
        Connection.status == "pending",
    ).update(
        {
            "status": "accepted",
            "responded_at": datetime.datetime.utcnow(),
            "receiver_seen": True,
            "requester_seen": False,  # requester has an unseen response
        },
        synchronize_session=False,
    )


def reject_pending_connection(connection_id, receiver_id):
    """pending -> rejected, only by the receiver. Returns rowcount (0 or 1)."""
    return Connection.query.filter(
        Connection.id == connection_id,
        Connection.receiver_id == receiver_id,
        Connection.status == "pending",
    ).update(
        {
            "status": "rejected",
            "responded_at": datetime.datetime.utcnow(),
            "receiver_seen": True,
            "requester_seen": False,
        },
        synchronize_session=False,
    )


def cancel_pending_connection(connection_id, requester_id):
    """Delete a still-pending request, only by its requester. Returns rowcount."""
    return Connection.query.filter(
        Connection.id == connection_id,
        Connection.requester_id == requester_id,
        Connection.status == "pending",
    ).delete(synchronize_session=False)


def remove_accepted_connection(user_a_id, user_b_id):
    """Delete an ACCEPTED connection between the pair. Returns rowcount."""
    return Connection.query.filter(
        _pair_filter(user_a_id, user_b_id),
        Connection.status == "accepted",
    ).delete(synchronize_session=False)


def reopen_rejected_connection(connection_id, new_requester_id, new_receiver_id, message):
    """
    rejected -> pending (audit CON-03).

    Swaps direction when the re-requester was the original receiver so the
    row always reads requester -> receiver for the NEW request, resets both
    seen flags and never writes NULL into the NOT NULL responded_at column.
    Returns rowcount (0 if the row is no longer 'rejected').
    """
    now = datetime.datetime.utcnow()
    return Connection.query.filter(
        Connection.id == connection_id,
        Connection.status == "rejected",
    ).update(
        {
            "requester_id": new_requester_id,
            "receiver_id": new_receiver_id,
            "status": "pending",
            "requested_at": now,
            "responded_at": now,
            "request_message": message,
            "receiver_seen": False,
            "requester_seen": True,
            "blocked_by_id": None,
        },
        synchronize_session=False,
    )


# ============================================================================
# SHARED HELPERS
# ============================================================================

def notify_after_commit(fn, *args, **kwargs):
    """
    Run a notification-sending callable in its OWN transaction AFTER the
    business transaction has committed (audit CON-09 / CON-17).

    notification_service.notify() flushes a row, increments a Redis counter
    and emits a WebSocket event: the latter two cannot be rolled back, so they
    must never run before the business commit succeeds. A notification
    failure never fails the (already committed) business operation.
    """
    try:
        fn(*args, **kwargs)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.warning("Post-commit notification failed (non-critical)", exc_info=True)


def clean_user_text(value, max_len, field="Message"):
    """
    Validate + sanitize free text (audit CON-04).

    Strips every HTML tag with bleach, then unescapes the entities bleach adds
    so the STORED value is plain text (the frontend escapes on render).
    Returns None for None/blank. Raises ValueError for non-strings or
    over-length input.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    cleaned = html.unescape(bleach.clean(value, tags=[], strip=True)).strip()
    if len(cleaned) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters")
    return cleaned or None


def get_accepted_partner_ids(user_id):
    """Set of user ids with an ACCEPTED connection to `user_id` (1 query)."""
    rows = db.session.query(Connection.requester_id, Connection.receiver_id).filter(
        or_(Connection.requester_id == user_id, Connection.receiver_id == user_id),
        Connection.status == "accepted",
    ).all()
    return {r if q == user_id else q for q, r in rows}


def invalidate_pair_caches(user_a_id, user_b_id):
    """Drop the per-pair mutual-count and health caches (audit CON-20)."""
    low, high = min(user_a_id, user_b_id), max(user_a_id, user_b_id)
    try:
        cache_service.delete(f"sh:1:conn:mutual:{low}:{high}")
        cache_service.delete(f"sh:1:conn:health:{user_a_id}:{user_b_id}")
        cache_service.delete(f"sh:1:conn:health:{user_b_id}:{user_a_id}")
    except Exception:
        logger.warning("Pair cache invalidation failed (non-critical)", exc_info=True)


def can_message(sender_id, receiver_id):
    """
    Check whether sender is allowed to DM receiver.

    Moved here (unchanged behavior) from messages.py, per Document 2 §4's
    decision test: pure predicate, no HTTP dependency, and conceptually
    about connection state rather than message content — the same
    reasoning that put is_user_blocked/block_connection/unblock_connection
    here.

    Rules:
    1. Must have an accepted connection, OR
    2. (system-message exception — not currently implemented; see original
       docstring in messages.py, kept verbatim below for context)

    Note: Thread members CANNOT DM — must connect first.
    """
    if sender_id == receiver_id:
        return False

    # Check for accepted connection
    connection = _find_connection_between(sender_id, receiver_id, status="accepted")

    return connection is not None


# ============================================================================
# COMPATIBILITY SCORING / PROFILE DATA GATHERING  (Document 2 §3.4)
#
# PHASE-1 CORRECTNESS FIX: these were specified in Document 2 §3.4 as
# belonging to this service ("calculate_compatibility",
# "calculate_compatibility_score", "calculate_schedule_overlap",
# "get_user_top_topics", "gather_user_data", "get_mutual_connection_count",
# "get_connection_health_batch") but the move was never actually done —
# they were still defined directly inside routes/student/connections.py.
# Moved here now (verbatim behavior, two bug fixes bundled in and called
# out explicitly below), with connections.py updated to import them
# instead of defining its own copies.
# ============================================================================

def get_user_top_topics(user_id, limit=3):
    """Get user's most discussed topics from posts and activity"""
    try:
        # Get tags from user's posts
        posts = Post.query.filter_by(student_id=user_id).limit(30).all()

        all_tags = []
        for post in posts:
            if post.tags:
                all_tags.extend(post.tags)

        if not all_tags:
            return []

        topic_counts = Counter(all_tags)
        return [topic for topic, _ in topic_counts.most_common(limit)]
    except Exception:
        logger.error("Error getting top topics", exc_info=True)
        return []


def calculate_compatibility_score(compatibility_data):
    """Calculate numerical compatibility score (0-100)"""
    score = 0

    # Shared subjects (30 points max)
    shared_count = len(compatibility_data.get('shared_subjects', []))
    score += min(shared_count * 10, 30)

    # They can help you (40 points max)
    help_count = len(compatibility_data['complementary_skills'].get('they_can_help_with', []))
    score += min(help_count * 20, 40)

    # Schedule overlap (20 points max)
    schedule_overlap = compatibility_data.get('schedule_overlap', 0)
    score += min(schedule_overlap * 0.2, 20)

    # Department match (10 points)
    if compatibility_data.get('department_match', False):
        score += 10

    return min(int(score), 100)


def gather_user_data(user):
    """Gather all relevant data about a user for AI analysis"""
    try:
        profile = user.student_profile
        onboarding = user.onboarding_details

        return {
            "name": user.name,
            "username": user.username,
            "bio": user.bio or "No bio yet",
            "department": profile.department if profile else "Unknown",
            "class_name": profile.class_name if profile else "Unknown",
            "reputation": user.reputation,
            "reputation_level": user.reputation_level,
            "strong_subjects": onboarding.strong_subjects if onboarding else [],
            "help_subjects": onboarding.help_subjects if onboarding else [],
            "learning_style": onboarding.learning_style if onboarding else "Not specified",
            "study_preferences": onboarding.study_preferences if onboarding else [],
            "badges": [ub.badge.name for ub in user.badges.limit(3)] if user.badges else []
        }
    except Exception:
        logger.error("Error gathering user data", exc_info=True)
        return {}


def calculate_compatibility(current_user_data, target_user_data):
    """Calculate compatibility metrics between two users"""
    try:
        current_subjects = set(
            current_user_data.get('strong_subjects', []) +
            current_user_data.get('help_subjects', [])
        )
        target_subjects = set(
            target_user_data.get('strong_subjects', []) +
            target_user_data.get('help_subjects', [])
        )

        shared_subjects = list(current_subjects & target_subjects)

        they_can_help = list(
            set(target_user_data.get('strong_subjects', [])) &
            set(current_user_data.get('help_subjects', []))
        )

        you_can_help = list(
            set(current_user_data.get('strong_subjects', [])) &
            set(target_user_data.get('help_subjects', []))
        )

        return {
            "shared_subjects": shared_subjects,
            "complementary_skills": {
                "they_can_help_with": they_can_help,
                "you_can_help_with": you_can_help
            },
            "schedule_overlap": 0,  # Filled in by caller via calculate_schedule_overlap
            "department_match": current_user_data.get('department') == target_user_data.get('department')
        }
    except Exception:
        logger.error("Error calculating compatibility", exc_info=True)
        return {
            "shared_subjects": [],
            "complementary_skills": {"they_can_help_with": [], "you_can_help_with": []},
            "schedule_overlap": 0,
            "department_match": False
        }


def calculate_schedule_overlap(schedule1, schedule2):
    """
    Calculate percentage of overlapping study times.

    Contract (previously undocumented, per Document 1 §3.3's B-5 naming
    cleanup): `schedule1`/`schedule2` are dicts keyed by day name
    ("Monday".."Sunday", exactly as produced by the onboarding UI) whose
    values are lists drawn from {"morning", "afternoon", "evening"}
    (lowercase). Day keys are matched case-sensitively against this exact
    casing; day/time values that don't match this contract are silently
    treated as non-overlapping rather than raising.
    """
    if not schedule1 or not schedule2:
        return 0

    overlap_count = 0
    total_slots = 0

    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    times = ['morning', 'afternoon', 'evening']

    for day in days:
        if day in schedule1 and day in schedule2:
            user1_times = set(schedule1[day])
            user2_times = set(schedule2[day])

            total_slots += len(times)

            overlaps = user1_times & user2_times
            overlap_count += len(overlaps)

    return int((overlap_count / max(total_slots, 1)) * 100) if total_slots > 0 else 0


def get_recent_activity(user_id):
    """Get user's recent activity metrics"""
    from models import Comment

    try:
        seven_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)

        recent_posts = Post.query.filter_by(student_id=user_id) \
            .filter(Post.posted_at >= seven_days_ago).count()

        recent_helpful = Comment.query.filter_by(student_id=user_id) \
            .filter(Comment.helpful_count > 0) \
            .filter(Comment.posted_at >= seven_days_ago).count()

        from models import ThreadMember
        active_threads = ThreadMember.query.filter_by(student_id=user_id).count()

        popular_topics = get_user_top_topics(user_id, limit=3)

        return {
            "recent_posts": recent_posts,
            "recent_helpful_comments": recent_helpful,
            "active_threads": active_threads,
            "popular_topics": popular_topics
        }
    except Exception:
        logger.error("Error getting recent activity", exc_info=True)
        return {
            "recent_posts": 0,
            "recent_helpful_comments": 0,
            "active_threads": 0,
            "popular_topics": []
        }


def get_user_onboarding_preview(user_id):
    """
    Short onboarding-details preview (subjects/strong_subjects/help_subjects/
    learning_style/study_preferences/session_length/has_schedule), used
    across connection listing/discovery/detail endpoints.

    BUG FIX bundled into this move: the original copy of this function
    (in connections.py) caught its own exceptions by logging a mismatched
    message ("Available connections error" — clearly copy-pasted from a
    different function) and then returning `error_response(...)`, a Flask
    JSON-response helper, instead of `None`. Every call site treats this
    function's return value as `dict | None` (e.g. `onboarding or {}`), so
    on the (rare) exception path the original code would have handed a
    Flask Response object into code expecting a dict, and — since
    error_response()/Flask's request context aren't available outside an
    HTTP request — would have raised a second, more confusing exception
    from inside a service function. Fixed to log accurately and return
    None, matching every caller's actual expectation.
    """
    from models import OnboardingDetails

    try:
        onboarding = OnboardingDetails.query.filter_by(user_id=user_id).first()

        if not onboarding:
            return None

        return {
            "subjects": onboarding.subjects[:3] if onboarding.subjects else [],
            "strong_subjects": onboarding.strong_subjects[:3] if onboarding.strong_subjects else [],
            "help_subjects": onboarding.help_subjects[:3] if onboarding.help_subjects else [],
            "learning_style": onboarding.learning_style,
            "study_preferences": onboarding.study_preferences[:3] if onboarding.study_preferences else [],
            "session_length": onboarding.session_length,
            "has_schedule": bool(onboarding.study_schedule)
        }
    except Exception:
        logger.error(f"get_user_onboarding_preview error (user_id={user_id})", exc_info=True)
        return None


def get_mutual_connection_count(user1_id, user2_id):
    """Get count of mutual connections between two users"""
    # Plan §3.2/§4.8/§17.7: cache-aside, TTL 300s, keyed by the canonical
    # (min, max) ordering of the pair — matches the existing
    # create_conversation_key() pattern already used in
    # messages.py/websocket_messages.py for conversation keys, per §3.2's
    # explicit instruction to reuse that convention. Without this, calling
    # get_mutual_connection_count(482, 117) and get_mutual_connection_count(
    # 117, 482) would populate two separate cache entries for the same
    # logical pair.
    low_id, high_id = min(user1_id, user2_id), max(user1_id, user2_id)
    cache_key = f"sh:1:conn:mutual:{low_id}:{high_id}"
    cached_count = cache_service.get(cache_key)
    if cached_count is not None:
        return cached_count

    try:
        # Audit CON-15: computed in SQL (INTERSECT of the two partner-id sets)
        # instead of loading both users' whole connection lists into Python.
        def _partners(uid):
            a = db.session.query(Connection.receiver_id.label("pid")).filter(
                Connection.requester_id == uid, Connection.status == "accepted")
            b = db.session.query(Connection.requester_id.label("pid")).filter(
                Connection.receiver_id == uid, Connection.status == "accepted")
            return a.union(b)

        count = db.session.query(func.count()).select_from(
            _partners(user1_id).intersect(_partners(user2_id)).subquery()
        ).scalar() or 0
        # Short TTL: third-party connection changes (A<->C) can't invalidate
        # the (A,B) key, so staleness is bounded instead of 5 minutes.
        cache_service.set(cache_key, count, ttl_seconds=60)
        return count

    except Exception:
        logger.error("Get mutual count error", exc_info=True)
        return 0


def get_connection_health_batch(user_id, other_user_ids):
    """
    Batch connection-health computation for `user_id` against every id in
    `other_user_ids` — 2 queries total regardless of how many pairs are
    being scored, instead of the N+1 the original per-pair
    get_connection_health(user_id, other_user_id) produced when called in
    a loop (Document 1 §2.1.1's flagged fix, applied here rather than at
    the eventual file-split point since the fix is independent of where
    the code physically lives).

    Returns {other_user_id: health_dict}. A pair with no accepted
    connection is simply omitted from the result (matches the original
    single-pair function returning None for "no connection found").
    """
    from models import ThreadMember

    result = {}
    if not other_user_ids:
        return result

    try:
        # One query: every accepted connection involving user_id
        my_connections = Connection.query.filter(
            or_(
                Connection.requester_id == user_id,
                Connection.receiver_id == user_id
            ),
            Connection.status == "accepted"
        ).all()
        conn_by_other = {}
        for c in my_connections:
            other_id = c.receiver_id if c.requester_id == user_id else c.requester_id
            conn_by_other[other_id] = c

        relevant_ids = [oid for oid in other_user_ids if oid in conn_by_other]
        if not relevant_ids:
            return result

        # One query: this user's thread memberships
        my_thread_ids = {
            t.thread_id for t in ThreadMember.query.filter_by(student_id=user_id).all()
        }

        # One query: thread memberships for every relevant "other" user at once
        other_memberships = ThreadMember.query.filter(
            ThreadMember.student_id.in_(relevant_ids)
        ).all()
        other_thread_ids_map = {}
        for tm in other_memberships:
            other_thread_ids_map.setdefault(tm.student_id, set()).add(tm.thread_id)

        now = datetime.datetime.utcnow()

        # Audit CON-23: recency = last real interaction (latest message in
        # either direction), falling back to when the connection was made.
        # ONE grouped query for all relevant partners.
        from models import Message
        last_msg = {}
        msg_rows = db.session.query(
            Message.sender_id, Message.receiver_id, func.max(Message.sent_at)
        ).filter(
            or_(
                and_(Message.sender_id == user_id, Message.receiver_id.in_(relevant_ids)),
                and_(Message.receiver_id == user_id, Message.sender_id.in_(relevant_ids)),
            )
        ).group_by(Message.sender_id, Message.receiver_id).all()
        for snd, rcv, ts in msg_rows:
            partner = rcv if snd == user_id else snd
            if ts and (partner not in last_msg or ts > last_msg[partner]):
                last_msg[partner] = ts

        for other_id in relevant_ids:
            connection = conn_by_other[other_id]
            score = 100

            last_interaction = last_msg.get(other_id) or connection.responded_at or connection.requested_at
            days_since = (now - last_interaction).days if last_interaction else 999

            if days_since > 30:
                score -= 40
            elif days_since > 14:
                score -= 20
            elif days_since > 7:
                score -= 10

            shared_threads = len(my_thread_ids & other_thread_ids_map.get(other_id, set()))
            score += min(shared_threads * 10, 30)

            score = max(0, min(100, score))

            if score < 40:
                suggestion = "💤 Haven't connected in a while. Send them a message!"
            elif score < 70:
                suggestion = "👍 Good connection. Schedule a study session?"
            else:
                suggestion = "🔥 Strong connection! Keep it up."

            result[other_id] = {
                "health_score": score,
                "health_percent": float(score),
                "suggestion": suggestion,
                "last_interaction_days": days_since,
                "shared_threads": shared_threads,
            }

        return result

    except Exception:
        logger.error("Connection health batch error", exc_info=True)
        return result


_NO_CONNECTION_SENTINEL = {"__no_connection__": True}


def get_connection_health(user_id, other_user_id):
    """
    Single-pair convenience wrapper over get_connection_health_batch, for
    the one legitimate single-pair caller (get_connection_details). Every
    listing endpoint (list_connections, get_online_connections,
    get_online_connections_by_department) should call
    get_connection_health_batch(...) once before its loop instead of this,
    to avoid reintroducing the N+1 this batch form exists to fix.

    Returns the same dict shape as before, or None if no accepted
    connection exists between the pair (matches original behavior).

    Plan §4.8/§17.7: cache-aside, TTL 180s — deliberately only this
    single-pair form is cached, not get_connection_health_batch (the batch
    form already avoids the N+1 that would otherwise motivate caching it,
    and its cache key would need to represent an arbitrary id-list, which
    doesn't fit this plan's per-key scoping). A "no accepted connection"
    result (None) is itself a legitimate, cacheable outcome — it means
    genuinely fewer DB round-trips for a pair that's repeatedly checked
    despite being unconnected — but cache_service.get() returning None is
    otherwise indistinguishable from a cache miss, so a sentinel value is
    stored in Redis for the None case and translated back to None here.
    """
    cache_key = f"sh:1:conn:health:{user_id}:{other_user_id}"
    cached = cache_service.get(cache_key)
    if cached is not None:
        return None if cached == _NO_CONNECTION_SENTINEL else cached

    health = get_connection_health_batch(user_id, [other_user_id]).get(other_user_id)
    cache_service.set(
        cache_key,
        health if health is not None else _NO_CONNECTION_SENTINEL,
        ttl_seconds=180,
    )
    return health
