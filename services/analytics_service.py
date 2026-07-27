"""
services/analytics_service.py

Not explicitly named in the blueprint documents. calculate_engagement_rate
and get_activity_level are pure functions of their numeric inputs (no DB
access, no Flask dependency) — Document 2 §4's decision test #2 puts these
squarely in the service layer, same category as the reputation/badge/
compatibility/priority scoring functions the blueprint does name.

Moved verbatim (no behavior change) from analytics.py.

NOT moved (deliberately, flagged rather than silently worked around):
    - generate_insights(user_id) and get_average_user_stats() — both do
      heavy DB querying (Document 2 §4's test #3: "orchestrate multiple
      DB reads" — still service-layer-shaped, just not zero-DB-access
      "pure"). generate_insights specifically also imports
      calculate_badge_progress from routes/student/badges.py, which isn't
      a service yet (services/badge_service.py per Document 2 §3.2 doesn't
      exist in this codebase yet) — moving generate_insights here as-is
      would make this service import from routes/*, violating Document 2
      §2's layering rule. Left in analytics.py until badge_service.py
      exists, same reasoning as posts.py::check_helpful_milestones.

Per Document 2 §2's layering rule: no Flask imports, no `request`/`session`/`g`.
"""


def calculate_engagement_rate(views, likes, comments):
    """Calculate engagement rate as percentage"""
    if views == 0:
        return 0
    total_engagement = likes + (comments * 2)  # Comments worth more
    return round((total_engagement / views) * 100, 1)


def get_activity_level(activity_score):
    """Categorize activity level"""
    if activity_score >= 50:
        return {"level": "Very Active", "color": "#10B981", "emoji": "🔥"}
    elif activity_score >= 30:
        return {"level": "Active", "color": "#3B82F6", "emoji": "⚡"}
    elif activity_score >= 10:
        return {"level": "Moderate", "color": "#F59E0B", "emoji": "📊"}
    else:
        return {"level": "Low", "color": "#6B7280", "emoji": "💤"}
