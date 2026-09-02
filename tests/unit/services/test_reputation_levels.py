"""
Tests for services/reputation_levels.py.

Per the plan's §7.4: this table's own docstring documents a real,
previously-shipped bug where two implementations disagreed at exactly
1000 points ("Expert" vs "Master"). The 1000/1001 boundary is tested
explicitly and by name to guard against that regression recurring.
"""

import pytest

from services.reputation_levels import get_reputation_level, get_reputation_level_name


@pytest.mark.parametrize(
    "points,expected_name",
    [
        (0, "Newbie"),
        (50, "Newbie"),
        (51, "Learner"),
        (200, "Learner"),
        (201, "Contributor"),
        (500, "Contributor"),
        (501, "Expert"),
        (1000, "Expert"),        # historically-buggy boundary
        (1001, "Master"),        # historically-buggy boundary
        (999999, "Master"),
        (5_000_000, "Master"),   # above the top range -> falls through to last tier
    ],
    ids=[
        "min-0-newbie", "max-50-newbie", "min-51-learner", "max-200-learner",
        "min-201-contributor", "max-500-contributor", "min-501-expert",
        "max-1000-expert-not-master", "min-1001-master-not-expert",
        "max-999999-master", "above-top-range-falls-through-to-master",
    ],
)
def test_get_reputation_level_boundaries(points, expected_name):
    assert get_reputation_level(points)["name"] == expected_name


def test_get_reputation_level_returns_full_dict_shape():
    level = get_reputation_level(100)
    assert set(level.keys()) == {"min", "max", "name", "icon", "color"}


@pytest.mark.parametrize(
    "points,expected_name",
    [(0, "Newbie"), (1000, "Expert"), (1001, "Master")],
)
def test_get_reputation_level_name_matches_get_reputation_level(points, expected_name):
    assert get_reputation_level_name(points) == expected_name
    assert get_reputation_level_name(points) == get_reputation_level(points)["name"]
