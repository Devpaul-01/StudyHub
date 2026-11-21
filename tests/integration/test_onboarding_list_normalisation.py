"""OnboardingDetails list columns only ever hold short plain strings."""
import pytest


@pytest.mark.parametrize("field", ["subjects", "strong_subjects", "help_subjects", "study_preferences"])
def test_malformed_items_never_reach_the_database(db_session, make_user, make_onboarding_details, field):
    from models import OnboardingDetails
    user = make_user()
    ob = make_onboarding_details(user)

    setattr(ob, field, [{"a": 1}, ["nested"], 5, None, "  Python  ", "python", "<b>Go</b>", "x" * 150])
    db_session.commit()

    stored = getattr(OnboardingDetails.query.filter_by(user_id=user.id).first(), field)
    assert stored == ["Python", "bGo/b", "x" * 100]
    assert all(isinstance(item, str) for item in stored)


def test_non_list_values_become_empty_lists(db_session, make_user, make_onboarding_details):
    ob = make_onboarding_details(make_user())

    ob.subjects = "Math"
    ob.help_subjects = None
    db_session.commit()

    assert ob.subjects == []
    assert ob.help_subjects == []


def test_lists_are_capped(db_session, make_user, make_onboarding_details):
    ob = make_onboarding_details(make_user())

    ob.subjects = [f"subject {i}" for i in range(60)]
    db_session.commit()

    assert len(ob.subjects) == 30
