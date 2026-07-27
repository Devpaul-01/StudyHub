"""
services/homework_service.py

Priority scoring and pagination helpers for the homework/assignment system.
Extracted from routes/student/homework_system.py and models.py::Assignment.
calculate_priority (Document 2 §6's fully-worked example).

Per Document 2 §6: calculate_priority_score() is a PURE function — it reads
the assignment's due_date/difficulty/status/estimated_hours but never
mutates the ORM object or touches db.session. This is a deliberate
improvement over the prior "compute into assignment.priority_score, then
never commit" pattern (H-5 fix): that was safe, but relied on nobody
adding a stray commit later in the same request. Read-only routes
(get_my_assignments, get_homework_feed) now never touch the ORM attribute
at all; only the three genuine mutation paths (create, update,
quick-actions) explicitly assign the return value back to
assignment.priority_score.

Per Document 2 §2's layering rule: no Flask imports, no request/session/g.
"""

from __future__ import annotations

import datetime

from models import Assignment


# ============================================================================
# PRIORITY SCORING  (pure — accepts `now` for deterministic testing)
# ============================================================================

_DIFFICULTY_MULTIPLIERS = {"easy": 1.0, "medium": 1.3, "hard": 1.6}
_STATUS_MULTIPLIERS = {"not_started": 1.2, "in_progress": 1.0, "completed": 0.1}


def _urgency_score(hours_until_due: float) -> int:
    if hours_until_due < 0:
        return 100
    elif hours_until_due < 24:
        return 90
    elif hours_until_due < 48:
        return 70
    elif hours_until_due < 168:
        return 50
    else:
        return 30


def calculate_priority_score(assignment: Assignment, *, now: datetime.datetime | None = None) -> float:
    """
    Pure priority-score calculation. Does NOT mutate `assignment` or touch
    the DB session — callers explicitly decide whether to persist the
    result via `assignment.priority_score = calculate_priority_score(assignment)`.

    `now` defaults to datetime.utcnow() but accepting it as a parameter is
    what makes this trivially unit-testable with a fixed clock.
    """
    now = now or datetime.datetime.utcnow()
    hours_until_due = (assignment.due_date - now).total_seconds() / 3600

    urgency = _urgency_score(hours_until_due)
    difficulty_multiplier = _DIFFICULTY_MULTIPLIERS.get(assignment.difficulty, 1.3)
    status_multiplier = _STATUS_MULTIPLIERS.get(assignment.status, 1.0)
    hours_bonus = min((assignment.estimated_hours or 0) * 2, 20)

    return (urgency * difficulty_multiplier * status_multiplier) + hours_bonus


def get_urgency_level(hours_until_due: float) -> str:
    """Categorical urgency label used for display (separate from the
    numeric priority score, which also feeds into sort order)."""
    if hours_until_due < 0:
        return "overdue"
    elif hours_until_due < 24:
        return "urgent"
    elif hours_until_due < 48:
        return "soon"
    elif hours_until_due < 168:
        return "this_week"
    else:
        return "upcoming"


# ============================================================================
# PAGINATION  (already-improved cursor pagination, moved unchanged)
# ============================================================================

DEFAULT_PAGE_SIZE = 15
MAX_PAGE_SIZE = 50


def parse_pagination_params(limit_raw, cursor_raw) -> tuple[int, str | None]:
    """
    Parse `limit`/`cursor` request args (already extracted as plain values
    by the route layer — this function takes no Flask dependency).
    """
    try:
        limit = int(limit_raw) if limit_raw is not None else DEFAULT_PAGE_SIZE
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE_SIZE
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    return limit, cursor_raw


def slice_by_cursor(items: list, cursor, limit: int) -> tuple[list, bool, str | None]:
    """
    Given a fully sorted list of ORM objects (each with `.id`), return
    (page, has_more, next_cursor).

    H-4 note preserved from the original: finding the cursor's position is
    an O(1) dict lookup (built once per call) rather than an O(n) linear
    scan repeated on every page request.

    Does NOT solve the deeper architectural issue also flagged in the
    original comment: for priority-based sort, the full matching set still
    has to be loaded and sorted in Python because priority is a
    time-dependent value not persisted per-request. True DB-level keyset
    pagination for priority order would require either a periodically-
    refreshed snapshot column or moving priority computation into SQL
    itself — both larger, behavior-changing decisions intentionally out of
    scope here. due_date/created_at sort modes sort on plain persisted
    columns and would be straightforward to migrate to real SQL-level
    keyset pagination in a follow-up.
    """
    start_index = 0
    if cursor:
        try:
            cursor_id = int(cursor)
            id_to_index = {item.id: i for i, item in enumerate(items)}
            if cursor_id in id_to_index:
                start_index = id_to_index[cursor_id] + 1
        except (TypeError, ValueError):
            start_index = 0

    page = items[start_index:start_index + limit]
    has_more = (start_index + limit) < len(items)
    next_cursor = str(page[-1].id) if page and has_more else None

    return page, has_more, next_cursor


# ============================================================================
# SMART SUGGESTIONS  (pure — operates on already-loaded assignment list)
# ============================================================================

def get_smart_suggestions(assignments: list[Assignment], *, now: datetime.datetime | None = None) -> list[dict]:
    """
    Generate up to 2 smart suggestions based on the user's currently active
    assignments. Pure function over an already-loaded list — no DB access.
    """
    now = now or datetime.datetime.utcnow()
    suggestions = []

    active = [a for a in assignments if a.status in ("not_started", "in_progress")]
    if not active:
        return []

    urgent_hard = [
        a for a in active
        if a.difficulty == "hard" and (a.due_date - now).total_seconds() / 3600 < 48
    ]
    if urgent_hard:
        hours = round((urgent_hard[0].due_date - now).total_seconds() / 3600, 1)
        suggestions.append({
            "type": "urgent_hard",
            "message": f"⚠️ Start '{urgent_hard[0].title}' soon - it's hard and due in {hours} hours",
            "assignment_id": urgent_hard[0].id,
            "action": "start_working",
        })

    hard_not_shared = [a for a in active if a.difficulty == "hard" and not a.is_shared_for_help]
    if hard_not_shared:
        suggestions.append({
            "type": "share_for_help",
            "message": f"💡 '{hard_not_shared[0].title}' looks tough - consider sharing it to get help from connections",
            "assignment_id": hard_not_shared[0].id,
            "action": "share_for_help",
        })

    easy_ones = [a for a in active if a.difficulty == "easy" and a.status == "not_started"]
    if easy_ones:
        suggestions.append({
            "type": "quick_win",
            "message": f"✨ Quick win: '{easy_ones[0].title}' is easy - knock it out!",
            "assignment_id": easy_ones[0].id,
            "action": "start_working",
        })

    overdue = [a for a in active if (a.due_date - now).total_seconds() < 0]
    if overdue:
        suggestions.append({
            "type": "overdue",
            "message": f"🚨 '{overdue[0].title}' is overdue - prioritize this!",
            "assignment_id": overdue[0].id,
            "action": "start_working",
        })

    return suggestions[:2]
