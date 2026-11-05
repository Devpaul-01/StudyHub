"""
tests/integration/factories_ext.py

Additive factory functions for models named as gaps in
INTEGRATION_TEST_IMPLEMENTATION_PLAN.md §2.2 ("Gaps requiring new
factories"): OnboardingDetails, a raw Badge row (without going through
seed_badges()), and Notification. (The StudyBuddyRequest/StudyBuddyMatch
factories that used to live here were removed along with the
study-buddy feature and its models.)

Deliberately kept in tests/integration/ rather than added to
tests/unit/factories.py — the instruction was to reuse the unit suite's
existing infrastructure unchanged, not modify it as a side effect of this
pass. Follows the exact same plain-function-taking-db_session style as
tests/unit/factories.py (see that file's own module docstring for why
factory_boy was declined) for consistency.
"""

import datetime


def make_onboarding_details(db_session, user, **overrides):
    from models import OnboardingDetails

    defaults = dict(
        user_id=user.id,
        email=user.email,
        department=None,
        class_level=None,
        subjects=[],
        learning_style=None,
        study_preferences=[],
        help_subjects=[],
        strong_subjects=[],
        study_schedule={},
        session_length=None,
    )
    defaults.update(overrides)
    row = OnboardingDetails(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def make_badge(db_session, **overrides):
    """
    Raw Badge row, bypassing badge_service.seed_badges() — for tests that
    need a SPECIFIC, targeted badge (e.g. a custom criteria dict) rather
    than the full real BADGE_DEFINITIONS set. Tests exercising the real
    award flow against real badge names ("Problem Solver", "Genius", etc.
    as used by posts/comments.py::mark_solution /
    comments.py::mark_comment_helpful) should use the seeded_badges
    fixture instead, so the exact criteria dicts match production.
    """
    from models import Badge

    defaults = dict(
        name=overrides.pop("name", f"Test Badge {id(overrides)}"),
        description="A test badge",
        icon="🏅",
        category="engagement",
        rarity="common",
        criteria={"posts_count": 1},
        awarded_count=0,
        is_active=True,
    )
    defaults.update(overrides)
    badge = Badge(**defaults)
    db_session.add(badge)
    db_session.flush()
    return badge


def make_notification(db_session, user, **overrides):
    from models import Notification

    defaults = dict(
        user_id=user.id,
        title="Test Notification",
        body="Test body",
        notification_type="test",
        is_read=False,
        created_at=datetime.datetime.utcnow(),
    )
    defaults.update(overrides)
    row = Notification(**defaults)
    db_session.add(row)
    db_session.flush()
    return row

