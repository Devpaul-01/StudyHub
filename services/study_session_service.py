"""
services/study_session_service.py

Pure business logic extracted from routes/student/study_sessions.py:
session templates, proposed-time validation. The WebSocket-emit helpers
(_emit, _partner_online) and the live-session/AI-streaming route bodies
stay in study_sessions.py — those are HTTP/WebSocket-layer concerns
(Flask-SocketIO context, streaming Response objects), not business logic,
per Document 2 §2.1's "what does NOT move to services/" guidance.

Per Document 2 §2's layering rule: no Flask imports, no request/session/g.
"""

from __future__ import annotations

import datetime


# ============================================================================
# SESSION TEMPLATES
# ============================================================================

VALID_TEMPLATE_IDS = {"exam_prep", "homework_help", "concept_review", "quick_study"}

TEMPLATE_DEFAULTS = {
    "exam_prep": {"duration": 120, "goal": "Review 3 chapters and solve practice problems"},
    "homework_help": {"duration": 60, "goal": "Complete homework problems"},
    "concept_review": {"duration": 90, "goal": "Master key concepts"},
    "quick_study": {"duration": 30, "goal": "Quick review or solve 5 problems"},
}

# Full template catalogue for GET /study-session/templates — kept as one
# source of truth alongside TEMPLATE_DEFAULTS/VALID_TEMPLATE_IDS above
# rather than a third, separately-maintained list.
SESSION_TEMPLATES = [
    {
        "id": "exam_prep",
        "name": "Exam Prep Session",
        "description": "Review concepts and practice problems for upcoming exam",
        "duration_minutes": 120,
        "default_goal": "Review 3 chapters and solve practice problems",
        "suggested_structure": "Review → Practice → Q&A",
        "icon": "📚",
    },
    {
        "id": "homework_help",
        "name": "Homework Help",
        "description": "Work through assignment together",
        "duration_minutes": 60,
        "default_goal": "Complete homework problems",
        "suggested_structure": "Work together → Review answers",
        "icon": "✍️",
    },
    {
        "id": "concept_review",
        "name": "Concept Review",
        "description": "Deep dive into understanding concepts",
        "duration_minutes": 90,
        "default_goal": "Master key concepts",
        "suggested_structure": "Explain → Examples → Practice",
        "icon": "🎯",
    },
    {
        "id": "quick_study",
        "name": "Quick Study Sprint",
        "description": "Short focused session",
        "duration_minutes": 30,
        "default_goal": "Quick review or solve 5 problems",
        "suggested_structure": "Focus → Quick review",
        "icon": "⚡",
    },
]


def get_template_defaults(template_id: str) -> dict:
    """Raises KeyError if template_id isn't valid — caller (route layer)
    is expected to validate against VALID_TEMPLATE_IDS first."""
    return TEMPLATE_DEFAULTS[template_id]


# ============================================================================
# PROPOSED-TIME VALIDATION  (pure)
# ============================================================================

def validate_proposed_times(times_list: list, max_times: int = 10) -> tuple[list[datetime.datetime], str | None]:
    """
    Parse and validate a list of ISO time strings.

    Returns (validated_datetimes, error_message | None). Invalid individual
    entries are silently dropped (matches original behavior); only an
    entirely-empty or entirely-unparseable input produces an error.
    """
    if not times_list:
        return [], "At least one proposed time required"
    if len(times_list) > max_times:
        return [], f"Maximum {max_times} proposed times allowed"

    validated = []
    for ts in times_list:
        try:
            validated.append(datetime.datetime.fromisoformat(str(ts).replace('Z', '+00:00')))
        except (ValueError, TypeError):
            pass

    if not validated:
        return [], "Invalid time format — use ISO 8601"

    return validated, None


def times_changed(old_time_strings: list[str] | None, new_time_strings: list[str]) -> bool:
    """
    Whether a reschedule actually changed the proposed-times set.
    Extracted from study_sessions.py::edit_study_session's inline
    old_set/new_set comparison — small enough to be worth naming so the
    "does this reschedule need to flip status back to rescheduled" logic
    has one, testable home.
    """
    old_set = set(old_time_strings or [])
    new_set = set(new_time_strings)
    return old_set != new_set


def confirmed_time_matches_proposed(confirmed_time: datetime.datetime, proposed_times: list) -> bool:
    """
    Whether `confirmed_time` matches one of the (possibly string-encoded)
    proposed times, comparing at minute resolution to tolerate
    timezone-naive vs aware datetime mixing — exactly the comparison
    study_sessions.py::confirm_study_session already did inline.

    Returns True if `proposed_times` is empty (nothing to validate against
    — matches the original "if proposed_normalized and ... not in" guard,
    which only enforces the check when there IS a proposed-times list).
    """
    proposed_normalized = []
    for p in (proposed_times or []):
        try:
            proposed_normalized.append(datetime.datetime.fromisoformat(str(p).replace('Z', '+00:00')))
        except (ValueError, TypeError):
            pass

    if not proposed_normalized:
        return True

    confirmed_str = confirmed_time.strftime("%Y-%m-%dT%H:%M")
    proposed_strs = [p.strftime("%Y-%m-%dT%H:%M") for p in proposed_normalized]
    return confirmed_str in proposed_strs
