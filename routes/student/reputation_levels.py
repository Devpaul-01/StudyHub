"""
StudyHub - Reputation Level Table (single source of truth)

H-8 fix: this table and its lookup function used to be copy-pasted,
independently, in badges.py, leaderboard.py, and reputation.py — plus a
FOURTH, functionally-similar-but-not-identical implementation baked directly
into models.py::User.update_reputation_level (which used strict `<`
boundary comparisons instead of the inclusive `min <= x <= max` range table
the other three used). Those two boundary styles already disagreed at
x == 1000 (range-table callers returned "Expert"; update_reputation_level
returned "Master") before this fix.

Every module that needs to know a user's reputation tier now imports from
here instead of maintaining its own copy.
"""

REPUTATION_LEVELS = [
    {"min": 0,    "max": 50,     "name": "Newbie",      "icon": "🌱", "color": "#6B7280"},
    {"min": 51,   "max": 200,    "name": "Learner",     "icon": "📚", "color": "#3B82F6"},
    {"min": 201,  "max": 500,    "name": "Contributor", "icon": "🎓", "color": "#8B5CF6"},
    {"min": 501,  "max": 1000,   "name": "Expert",      "icon": "🌟", "color": "#F59E0B"},
    {"min": 1001, "max": 999999, "name": "Master",      "icon": "👑", "color": "#EF4444"},
]


def get_reputation_level(reputation_points):
    """
    Calculate a user's reputation level/tier based on points.

    Returns: dict with level info (min, max, name, icon, color).
    Points above the highest defined tier fall through to the last
    (highest) tier, same as every previous copy of this function did.
    """
    for level in REPUTATION_LEVELS:
        if level["min"] <= reputation_points <= level["max"]:
            return level
    return REPUTATION_LEVELS[-1]


def get_reputation_level_name(reputation_points):
    """Convenience wrapper — just the tier name (e.g. "Contributor")."""
    return get_reputation_level(reputation_points)["name"]
