"""
services/study_buddy_service.py

Not explicitly named in the blueprint documents (Document 1/2 don't list a
dedicated study-buddy service), but calculate_match_score() is a textbook
case for Document 2 §4's decision test #2: "a pure function of its inputs,
with no DB access at all (scoring, formatting, validation)" → service
layer, and specifically "a good candidate for the first unit tests written
against this codebase" — exactly the same category as
connection_service.calculate_compatibility_score or
homework_service.calculate_priority_score, just for a part of the app the
blueprint didn't get to by name.

Moved verbatim (no behavior change) from study_buddy.py.

Per Document 2 §2's layering rule: no Flask imports, no `request`/`session`/`g`.
This function never touched Flask or the DB directly even before the move
(it already only reads from ORM objects and plain dicts passed in by the
caller), so this is a pure relocation.
"""

import datetime


def calculate_match_score(user1, user2, user1_prefs, user2_prefs,
                          profile1=None, profile2=None,
                          user2_success_count=0):
    """
    Calculate compatibility score between two users (0-100).

    OPTIMIZED: Accepts pre-loaded profile objects and success count so the
    caller can batch-load them instead of firing per-call DB queries.

    Score breakdown:
    - Subject overlap:  40 pts
    - Availability:     30 pts
    - Department match: 10 pts
    - Activity level:   10 pts
    - Success rate:     10 pts
    """
    score = 0

    # 1. Subject overlap (40 pts max)
    if user1_prefs and user2_prefs:
        needs1   = {s.lower() for s in user1_prefs.get("needs_help", [])}
        good_at2 = {s.lower() for s in user2_prefs.get("good_at", [])}
        needs2   = {s.lower() for s in user2_prefs.get("needs_help", [])}
        good_at1 = {s.lower() for s in user1_prefs.get("good_at", [])}
        total_overlap = len(needs1 & good_at2) + len(needs2 & good_at1)
        score += min(total_overlap * 10, 40)

    # 2. Availability overlap (30 pts max)
    if user1_prefs and user2_prefs:
        avail1 = set(user1_prefs.get("available_days", []))
        avail2 = set(user2_prefs.get("available_days", []))
        score += min(len(avail1 & avail2) * 5, 30)

    # 3. Department match (10 pts) — uses pre-loaded profiles, no DB hit
    if profile1 and profile2 and profile1.department == profile2.department:
        score += 10

    # 4. Activity level (10 pts)
    week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    if user1.last_active and user1.last_active >= week_ago:
        score += 5
    if user2.last_active and user2.last_active >= week_ago:
        score += 5

    # 5. Success rate (10 pts max) — uses pre-computed count, no DB hit
    score += min(user2_success_count * 2, 10)

    return min(score, 100)
