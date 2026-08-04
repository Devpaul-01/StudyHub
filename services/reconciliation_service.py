"""
services/reconciliation_service.py

Document 4 §3.3 point 2: a scheduled safety-net job that compares stored
denormalized counters against a fresh COUNT(*)/SUM(...) and either
auto-corrects small drifts or just logs+alerts on larger ones.

Split into its own file (rather than living inside leaderboard_service.py)
per Document 4 §3.3's own note ("or its own services/reconciliation_service.py
if it grows") — reconciliation covers Post and Thread counters, which have
nothing to do with leaderboard/reputation, so keeping it separate avoids an
unrelated-concerns file per Document 1 §4's "a file split is only done when
every resulting file is independently readable" principle.

Per Document 2 §2's layering rule: no Flask imports, no `request`/`session`/`g`.

DISTINCTION THAT DRIVES THIS FILE'S DESIGN (Document 4 §3.3 point 2):
  - "Display-only" counters: a wrong number is a cosmetic bug. Auto-corrected.
  - "Capacity-gating" counters: a wrong number could be actively over/under-
    admitting something (e.g. Thread.member_count vs max_members). Only
    alerted on, never silently auto-corrected, since auto-correcting risks
    masking a real upstream bug that's actively mis-admitting members.

This job is a safety net, not a primary consistency mechanism (Document 4
§3.3), so it's designed to run infrequently (weekly, alongside the existing
leaderboard snapshot jobs) rather than on every request.
"""

import logging
import datetime
from dataclasses import dataclass, field

from models import Post, Comment, Bookmark, PostView, PostReaction, Thread, ThreadMember, ThreadMessage
from extensions import db

logger = logging.getLogger(__name__)


# ============================================================================
# REPORT SHAPE
# ============================================================================

@dataclass
class CounterDrift:
    model: str
    row_id: int
    column: str
    stored_value: int
    actual_value: int
    auto_corrected: bool


@dataclass
class ReconciliationReport:
    checked_at: datetime.datetime
    counters_checked: int = 0
    drifts_found: list = field(default_factory=list)      # list[CounterDrift]
    corrected_count: int = 0
    alerted_only_count: int = 0

    def has_drift(self):
        return len(self.drifts_found) > 0


# ============================================================================
# CONSTANTS — which counters get auto-corrected vs. alert-only
#
# Per Document 4 §3.3 point 2: display-only counters are corrected
# automatically; counters that gate a business rule (capacity checks) are
# alert-only.
# ============================================================================

# Display-only Post counters — a wrong number here is cosmetic.
_POST_DISPLAY_COUNTERS = ["comments_count", "bookmark_count", "views_count", "helpful_reactions_count"]

# Thread.member_count gates Thread.max_members capacity checks elsewhere in
# the codebase — alert-only, never auto-corrected (Document 4 §3.3 point 2's
# explicit example).
_THREAD_CAPACITY_GATING_COUNTERS = ["member_count"]

# Thread.message_count is display-only (nothing gates capacity on it).
_THREAD_DISPLAY_COUNTERS = ["message_count"]


# ============================================================================
# SOURCE-OF-TRUTH QUERIES
#
# Each of these returns {row_id: actual_count} for every row that has at
# least one related record, batch-computed with a single GROUP BY query
# rather than per-row COUNT(*) calls (matching the batch-loading pattern
# used throughout this codebase's other services, e.g. leaderboard_service).
# Rows with zero related records are absent from these dicts and are
# treated as actual_value == 0 by the callers below.
# ============================================================================

def _post_comments_actual():
    rows = (
        db.session.query(Comment.post_id, db.func.count(Comment.id))
        .filter(Comment.is_deleted.is_(False))
        .group_by(Comment.post_id)
        .all()
    )
    return {post_id: count for post_id, count in rows}


def _post_bookmarks_actual():
    rows = (
        db.session.query(Bookmark.post_id, db.func.count(Bookmark.id))
        .group_by(Bookmark.post_id)
        .all()
    )
    return {post_id: count for post_id, count in rows}


def _post_views_actual():
    rows = (
        db.session.query(PostView.post_id, db.func.count(PostView.id))
        .group_by(PostView.post_id)
        .all()
    )
    return {post_id: count for post_id, count in rows}


def _post_helpful_reactions_actual():
    rows = (
        db.session.query(PostReaction.post_id, db.func.count(PostReaction.id))
        .filter(PostReaction.reaction_type == "helpful")
        .group_by(PostReaction.post_id)
        .all()
    )
    return {post_id: count for post_id, count in rows}


def _thread_members_actual():
    rows = (
        db.session.query(ThreadMember.thread_id, db.func.count(ThreadMember.id))
        .group_by(ThreadMember.thread_id)
        .all()
    )
    return {thread_id: count for thread_id, count in rows}


def _thread_messages_actual():
    rows = (
        db.session.query(ThreadMessage.thread_id, db.func.count(ThreadMessage.id))
        .filter(ThreadMessage.is_deleted.is_(False))
        .group_by(ThreadMessage.thread_id)
        .all()
    )
    return {thread_id: count for thread_id, count in rows}


# ============================================================================
# RECONCILIATION — POSTS (all display-only, all auto-corrected)
# ============================================================================

def _reconcile_post_counters(report):
    comments_actual = _post_comments_actual()
    bookmarks_actual = _post_bookmarks_actual()
    views_actual = _post_views_actual()
    helpful_actual = _post_helpful_reactions_actual()

    actuals_by_column = {
        "comments_count": comments_actual,
        "bookmark_count": bookmarks_actual,
        "views_count": views_actual,
        "helpful_reactions_count": helpful_actual,
    }

    posts = Post.query.with_entities(
        Post.id, Post.comments_count, Post.bookmark_count,
        Post.views_count, Post.helpful_reactions_count,
    ).all()

    stored_by_column = {
        "comments_count": {},
        "bookmark_count": {},
        "views_count": {},
        "helpful_reactions_count": {},
    }
    for post_id, comments_count, bookmark_count, views_count, helpful_reactions_count in posts:
        stored_by_column["comments_count"][post_id] = comments_count or 0
        stored_by_column["bookmark_count"][post_id] = bookmark_count or 0
        stored_by_column["views_count"][post_id] = views_count or 0
        stored_by_column["helpful_reactions_count"][post_id] = helpful_reactions_count or 0

    for column in _POST_DISPLAY_COUNTERS:
        actual_map = actuals_by_column[column]
        stored_map = stored_by_column[column]

        for post_id, stored_value in stored_map.items():
            actual_value = actual_map.get(post_id, 0)
            report.counters_checked += 1
            if stored_value == actual_value:
                continue

            drift = CounterDrift(
                model="Post", row_id=post_id, column=column,
                stored_value=stored_value, actual_value=actual_value,
                auto_corrected=True,
            )
            report.drifts_found.append(drift)
            report.corrected_count += 1

            Post.query.filter_by(id=post_id).update({column: actual_value})

    if report.corrected_count:
        db.session.commit()


# ============================================================================
# RECONCILIATION — THREADS (message_count auto-corrected, member_count alert-only)
# ============================================================================

def _reconcile_thread_counters(report):
    members_actual = _thread_members_actual()
    messages_actual = _thread_messages_actual()

    threads = Thread.query.with_entities(Thread.id, Thread.member_count, Thread.message_count).all()

    committed_any = False

    for thread_id, stored_member_count, stored_message_count in threads:
        stored_member_count = stored_member_count or 0
        stored_message_count = stored_message_count or 0

        actual_member_count = members_actual.get(thread_id, 0)
        actual_message_count = messages_actual.get(thread_id, 0)

        # member_count: capacity-gating — alert only, never auto-correct.
        report.counters_checked += 1
        if stored_member_count != actual_member_count:
            drift = CounterDrift(
                model="Thread", row_id=thread_id, column="member_count",
                stored_value=stored_member_count, actual_value=actual_member_count,
                auto_corrected=False,
            )
            report.drifts_found.append(drift)
            report.alerted_only_count += 1
            logger.warning(
                "[Reconciliation] Thread %s member_count drift: stored=%s actual=%s "
                "(capacity-gating counter — NOT auto-corrected, needs manual review)",
                thread_id, stored_member_count, actual_member_count,
            )

        # message_count: display-only — auto-correct.
        report.counters_checked += 1
        if stored_message_count != actual_message_count:
            drift = CounterDrift(
                model="Thread", row_id=thread_id, column="message_count",
                stored_value=stored_message_count, actual_value=actual_message_count,
                auto_corrected=True,
            )
            report.drifts_found.append(drift)
            report.corrected_count += 1
            Thread.query.filter_by(id=thread_id).update({"message_count": actual_message_count})
            committed_any = True

    if committed_any:
        db.session.commit()


# ============================================================================
# ENTRY POINT
# ============================================================================

def reconcile_denormalized_counts():
    """
    Compare stored denormalized counters against fresh COUNT(*) queries and
    either auto-correct (display-only counters) or log+alert (capacity-
    gating counters), per Document 4 §3.3 point 2.

    Intended to run on a low-frequency schedule (weekly) as a safety net,
    not a primary consistency mechanism — see scheduler.py's job
    registration. Returns a ReconciliationReport summarizing what was
    found/corrected, which the caller logs.
    """
    report = ReconciliationReport(checked_at=datetime.datetime.utcnow())

    _reconcile_post_counters(report)
    _reconcile_thread_counters(report)

    if report.has_drift():
        logger.warning(
            "[Reconciliation] Found %d counter drift(s) out of %d checked — "
            "%d auto-corrected, %d alert-only (needs manual review)",
            len(report.drifts_found), report.counters_checked,
            report.corrected_count, report.alerted_only_count,
        )
    else:
        logger.info(
            "[Reconciliation] All %d counters checked — no drift found",
            report.counters_checked,
        )

    return report
