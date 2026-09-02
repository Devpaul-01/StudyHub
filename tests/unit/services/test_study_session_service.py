"""
Tests for services/study_session_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §8.5.
"""

import datetime

from services import study_session_service


# ============================================================================
# validate_proposed_times
# ============================================================================

def test_validate_proposed_times_empty_list_errors():
    result, error = study_session_service.validate_proposed_times([])
    assert result == []
    assert error == "At least one proposed time required"


def test_validate_proposed_times_too_many_errors():
    times = [f"2026-01-{d:02d}T10:00:00" for d in range(1, 13)]  # 12 > max_times=10
    result, error = study_session_service.validate_proposed_times(times)
    assert result == []
    assert error == "Maximum 10 proposed times allowed"


def test_validate_proposed_times_valid_iso_strings_parsed():
    times = ["2026-01-15T10:00:00", "2026-01-16T14:30:00Z"]
    result, error = study_session_service.validate_proposed_times(times)
    assert error is None
    assert len(result) == 2
    assert all(isinstance(t, datetime.datetime) for t in result)


def test_validate_proposed_times_invalid_entries_silently_dropped():
    times = ["2026-01-15T10:00:00", "not-a-real-date", "also garbage"]
    result, error = study_session_service.validate_proposed_times(times)
    assert error is None
    assert len(result) == 1


def test_validate_proposed_times_all_invalid_errors():
    result, error = study_session_service.validate_proposed_times(["garbage", "more garbage"])
    assert result == []
    assert error == "Invalid time format — use ISO 8601"


# ============================================================================
# times_changed
# ============================================================================

def test_times_changed_identical_different_order_false():
    old = ["2026-01-15T10:00:00", "2026-01-16T10:00:00"]
    new = ["2026-01-16T10:00:00", "2026-01-15T10:00:00"]
    assert study_session_service.times_changed(old, new) is False


def test_times_changed_genuinely_different_true():
    old = ["2026-01-15T10:00:00"]
    new = ["2026-01-16T10:00:00"]
    assert study_session_service.times_changed(old, new) is True


def test_times_changed_none_old_treated_as_empty():
    assert study_session_service.times_changed(None, ["2026-01-15T10:00:00"]) is True
    assert study_session_service.times_changed(None, []) is False


# ============================================================================
# confirmed_time_matches_proposed
# ============================================================================

def test_confirmed_time_matches_exact_minute():
    confirmed = datetime.datetime(2026, 1, 15, 10, 0, 0)
    proposed = ["2026-01-15T10:00:00"]
    assert study_session_service.confirmed_time_matches_proposed(confirmed, proposed) is True


def test_confirmed_time_matches_ignoring_seconds():
    confirmed = datetime.datetime(2026, 1, 15, 10, 0, 47)  # different seconds
    proposed = ["2026-01-15T10:00:00"]
    assert study_session_service.confirmed_time_matches_proposed(confirmed, proposed) is True


def test_confirmed_time_no_match_false():
    confirmed = datetime.datetime(2026, 1, 15, 11, 0, 0)
    proposed = ["2026-01-15T10:00:00"]
    assert study_session_service.confirmed_time_matches_proposed(confirmed, proposed) is False


def test_confirmed_time_empty_proposed_returns_true_unconditionally():
    """Documented permissive default: nothing to validate against ->
    True. Worth a dedicated test since an empty list should not
    silently reject every confirmation attempt."""
    confirmed = datetime.datetime(2026, 1, 15, 11, 0, 0)
    assert study_session_service.confirmed_time_matches_proposed(confirmed, []) is True
    assert study_session_service.confirmed_time_matches_proposed(confirmed, None) is True


# ============================================================================
# get_template_defaults
# ============================================================================

def test_get_template_defaults_valid_id():
    result = study_session_service.get_template_defaults("exam_prep")
    assert result["duration"] == 120


def test_get_template_defaults_invalid_id_raises_keyerror():
    import pytest
    with pytest.raises(KeyError):
        study_session_service.get_template_defaults("not_a_real_template")
