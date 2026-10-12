"""
Tests for services/analytics_service.py.

Both functions here are pure (no DB, no Flask — see the module's own
docstring), so these are plain unit tests. Previously zero tests existed
for this module at all.
"""

import pytest

from services.analytics_service import calculate_engagement_rate, get_activity_level


# ============================================================================
# calculate_engagement_rate
# ============================================================================

def test_calculate_engagement_rate_zero_views_returns_zero_without_dividing():
    """views == 0 must short-circuit rather than raise ZeroDivisionError."""
    assert calculate_engagement_rate(views=0, likes=10, comments=5) == 0


def test_calculate_engagement_rate_comments_weighted_double_likes():
    # total_engagement = likes + comments*2 = 10 + 4*2 = 18; 18/100*100 = 18.0
    assert calculate_engagement_rate(views=100, likes=10, comments=4) == 18.0


def test_calculate_engagement_rate_no_engagement_returns_zero_point_zero():
    assert calculate_engagement_rate(views=100, likes=0, comments=0) == 0.0


def test_calculate_engagement_rate_rounds_to_one_decimal_place():
    # total_engagement = 1 + 0 = 1; 1/3*100 = 33.333... -> 33.3
    assert calculate_engagement_rate(views=3, likes=1, comments=0) == 33.3


def test_calculate_engagement_rate_can_exceed_100_percent():
    # Nothing in the function caps the result at 100.
    # total_engagement = 50 + 50*2 = 150; 150/10*100 = 1500.0
    assert calculate_engagement_rate(views=10, likes=50, comments=50) == 1500.0


# ============================================================================
# get_activity_level — boundary behavior
# ============================================================================

@pytest.mark.parametrize(
    "score,expected_level",
    [
        (0, "Low"),
        (9, "Low"),
        (10, "Moderate"),   # lower boundary, inclusive
        (29, "Moderate"),
        (30, "Active"),     # lower boundary, inclusive
        (49, "Active"),
        (50, "Very Active"),  # lower boundary, inclusive
        (1000, "Very Active"),
    ],
    ids=[
        "0-low", "9-low", "10-moderate-boundary", "29-moderate",
        "30-active-boundary", "49-active", "50-very-active-boundary",
        "1000-very-active",
    ],
)
def test_get_activity_level_boundaries(score, expected_level):
    assert get_activity_level(score)["level"] == expected_level


def test_get_activity_level_returns_full_dict_shape():
    result = get_activity_level(25)
    assert set(result.keys()) == {"level", "color", "emoji"}


def test_get_activity_level_negative_score_falls_through_to_low():
    """Nothing excludes negative scores; they fall into the lowest tier."""
    result = get_activity_level(-5)
    assert result["level"] == "Low"
