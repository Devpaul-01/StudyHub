"""
Tests for services/homework_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.4. calculate_priority_score is a
pure numeric formula with 3 independent dimensions (urgency tier x
difficulty x status) plus a capped bonus term -- each dimension's
boundaries are tested independently, plus one full combination to
confirm the formula composes multiplicatively as documented.
"""

import datetime

import pytest

from services import homework_service


# ============================================================================
# _urgency_score / get_urgency_level boundaries (kept in sync -- both
# must flip at the same hour values)
# ============================================================================

@pytest.mark.parametrize(
    "hours,expected_score,expected_label",
    [
        (-5, 100, "overdue"),
        (0, 90, "urgent"),
        (23.9, 90, "urgent"),
        (24, 70, "soon"),
        (47.9, 70, "soon"),
        (48, 50, "this_week"),
        (167.9, 50, "this_week"),
        (168, 30, "upcoming"),
        (500, 30, "upcoming"),
    ],
    ids=[
        "overdue", "urgent-min", "urgent-max", "soon-min", "soon-max",
        "this_week-min", "this_week-max", "upcoming-min", "upcoming-far",
    ],
)
def test_urgency_score_and_level_boundaries_agree(hours, expected_score, expected_label):
    assert homework_service._urgency_score(hours) == expected_score
    assert homework_service.get_urgency_level(hours) == expected_label


# ============================================================================
# calculate_priority_score — pure, deterministic clock via `now`
# ============================================================================

class _FakeAssignment:
    def __init__(self, due_date, difficulty="medium", status="not_started", estimated_hours=0):
        self.due_date = due_date
        self.difficulty = difficulty
        self.status = status
        self.estimated_hours = estimated_hours


NOW = datetime.datetime(2026, 1, 1, 12, 0, 0)


@pytest.mark.parametrize("difficulty,multiplier", [("easy", 1.0), ("medium", 1.3), ("hard", 1.6)])
def test_priority_score_difficulty_multipliers(difficulty, multiplier):
    a = _FakeAssignment(due_date=NOW + datetime.timedelta(hours=10), difficulty=difficulty, status="not_started")
    score = homework_service.calculate_priority_score(a, now=NOW)
    # urgency=90 (0-24h), status_multiplier not_started=1.2, +0 bonus
    assert score == pytest.approx(90 * multiplier * 1.2)


def test_priority_score_unknown_difficulty_falls_back_to_1_3():
    a = _FakeAssignment(due_date=NOW + datetime.timedelta(hours=10), difficulty="nonsense", status="not_started")
    score = homework_service.calculate_priority_score(a, now=NOW)
    assert score == pytest.approx(90 * 1.3 * 1.2)


@pytest.mark.parametrize(
    "status,multiplier", [("not_started", 1.2), ("in_progress", 1.0), ("completed", 0.1)]
)
def test_priority_score_status_multipliers(status, multiplier):
    a = _FakeAssignment(due_date=NOW + datetime.timedelta(hours=10), difficulty="medium", status=status)
    score = homework_service.calculate_priority_score(a, now=NOW)
    assert score == pytest.approx(90 * 1.3 * multiplier)


def test_priority_score_hours_bonus_zero_hours():
    a = _FakeAssignment(due_date=NOW + datetime.timedelta(hours=200), estimated_hours=0)
    score = homework_service.calculate_priority_score(a, now=NOW)
    assert score == pytest.approx(30 * 1.3 * 1.2 + 0)


def test_priority_score_hours_bonus_5_hours():
    a = _FakeAssignment(due_date=NOW + datetime.timedelta(hours=200), estimated_hours=5)
    score = homework_service.calculate_priority_score(a, now=NOW)
    assert score == pytest.approx(30 * 1.3 * 1.2 + 10)  # 5*2=10


def test_priority_score_hours_bonus_capped_at_20():
    a = _FakeAssignment(due_date=NOW + datetime.timedelta(hours=200), estimated_hours=15)  # 15*2=30 -> capped 20
    score = homework_service.calculate_priority_score(a, now=NOW)
    assert score == pytest.approx(30 * 1.3 * 1.2 + 20)


def test_priority_score_full_combination_multiplicative_not_additive():
    a = _FakeAssignment(
        due_date=NOW - datetime.timedelta(hours=1),  # overdue -> urgency 100
        difficulty="hard", status="not_started", estimated_hours=3,
    )
    score = homework_service.calculate_priority_score(a, now=NOW)
    expected = (100 * 1.6 * 1.2) + min(3 * 2, 20)
    assert score == pytest.approx(expected)


# ============================================================================
# slice_by_cursor
# ============================================================================

class _Item:
    def __init__(self, id_):
        self.id = id_


def test_slice_by_cursor_no_cursor_starts_at_zero():
    items = [_Item(i) for i in range(5)]
    page, has_more, next_cursor = homework_service.slice_by_cursor(items, None, limit=2)
    assert [i.id for i in page] == [0, 1]
    assert has_more is True
    assert next_cursor == "1"


def test_slice_by_cursor_valid_cursor_resumes_after_it():
    items = [_Item(i) for i in range(5)]
    page, has_more, next_cursor = homework_service.slice_by_cursor(items, "1", limit=2)
    assert [i.id for i in page] == [2, 3]


def test_slice_by_cursor_cursor_not_present_falls_back_to_start():
    items = [_Item(i) for i in range(5)]
    page, has_more, next_cursor = homework_service.slice_by_cursor(items, "999", limit=2)
    assert [i.id for i in page] == [0, 1]


def test_slice_by_cursor_last_page_has_more_false_next_cursor_none():
    items = [_Item(i) for i in range(4)]
    page, has_more, next_cursor = homework_service.slice_by_cursor(items, "1", limit=2)
    assert [i.id for i in page] == [2, 3]
    assert has_more is False
    assert next_cursor is None


def test_slice_by_cursor_second_to_last_page_has_more_true():
    items = [_Item(i) for i in range(5)]
    page, has_more, next_cursor = homework_service.slice_by_cursor(items, None, limit=4)
    assert has_more is True
    assert next_cursor == "3"


# ============================================================================
# parse_pagination_params
# ============================================================================

def test_parse_pagination_params_none_limit_uses_default():
    limit, cursor = homework_service.parse_pagination_params(None, "abc")
    assert limit == homework_service.DEFAULT_PAGE_SIZE
    assert cursor == "abc"


def test_parse_pagination_params_above_max_clamped():
    limit, _ = homework_service.parse_pagination_params(999, None)
    assert limit == homework_service.MAX_PAGE_SIZE


def test_parse_pagination_params_zero_or_negative_clamped_to_1():
    assert homework_service.parse_pagination_params(0, None)[0] == 1
    assert homework_service.parse_pagination_params(-5, None)[0] == 1


def test_parse_pagination_params_non_numeric_falls_back_to_default():
    limit, _ = homework_service.parse_pagination_params("not-a-number", None)
    assert limit == homework_service.DEFAULT_PAGE_SIZE


# ============================================================================
# get_smart_suggestions
# ============================================================================

class _FakeAssignmentFull:
    def __init__(self, id_, title, due_date, difficulty, status, is_shared_for_help=False):
        self.id = id_
        self.title = title
        self.due_date = due_date
        self.difficulty = difficulty
        self.status = status
        self.is_shared_for_help = is_shared_for_help


def test_smart_suggestions_no_active_assignments_empty():
    assignments = [_FakeAssignmentFull(1, "Done", NOW, "easy", "completed")]
    assert homework_service.get_smart_suggestions(assignments, now=NOW) == []


def test_smart_suggestions_urgent_hard_included():
    assignments = [
        _FakeAssignmentFull(1, "Hard One", NOW + datetime.timedelta(hours=10), "hard", "not_started")
    ]
    suggestions = homework_service.get_smart_suggestions(assignments, now=NOW)
    assert any(s["type"] == "urgent_hard" for s in suggestions)


def test_smart_suggestions_share_for_help_included():
    assignments = [
        _FakeAssignmentFull(1, "Hard Not Shared", NOW + datetime.timedelta(hours=200), "hard", "not_started", is_shared_for_help=False)
    ]
    suggestions = homework_service.get_smart_suggestions(assignments, now=NOW)
    assert any(s["type"] == "share_for_help" for s in suggestions)


def test_smart_suggestions_quick_win_included():
    assignments = [
        _FakeAssignmentFull(1, "Easy One", NOW + datetime.timedelta(hours=200), "easy", "not_started")
    ]
    suggestions = homework_service.get_smart_suggestions(assignments, now=NOW)
    assert any(s["type"] == "quick_win" for s in suggestions)


def test_smart_suggestions_overdue_included():
    assignments = [
        _FakeAssignmentFull(1, "Late One", NOW - datetime.timedelta(hours=5), "medium", "in_progress")
    ]
    suggestions = homework_service.get_smart_suggestions(assignments, now=NOW)
    assert any(s["type"] == "overdue" for s in suggestions)


def test_smart_suggestions_capped_at_2_first_categories_win():
    """More than 2 qualifying categories exist -- result is capped at 2,
    and the FIRST-CHECKED categories (urgent_hard, then share_for_help,
    per the function's own fixed check order) win the cut."""
    assignments = [
        _FakeAssignmentFull(1, "Urgent Hard", NOW + datetime.timedelta(hours=10), "hard", "not_started", is_shared_for_help=True),
        _FakeAssignmentFull(2, "Easy Quick Win", NOW + datetime.timedelta(hours=200), "easy", "not_started"),
        _FakeAssignmentFull(3, "Overdue One", NOW - datetime.timedelta(hours=5), "medium", "in_progress"),
    ]
    suggestions = homework_service.get_smart_suggestions(assignments, now=NOW)
    assert len(suggestions) == 2
    types = [s["type"] for s in suggestions]
    assert types == ["urgent_hard", "quick_win"]  # share_for_help doesn't qualify (is_shared_for_help=True); overdue loses the cut
