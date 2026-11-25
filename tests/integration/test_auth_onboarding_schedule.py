"""The onboarding questionnaire stores the schedule in the format scoring reads."""
from services.connection_service import calculate_schedule_overlap
from services.onboarding_service import validate_onboarding_payload


def test_day_names_are_stored_capitalised_whatever_the_client_sent():
    cleaned, errors = validate_onboarding_payload(
        {"study_schedule": {"monday": ["morning"], "FRIDAY": ["evening", "evening"]}})

    assert errors == {}
    assert cleaned["study_schedule"] == {"Monday": ["morning"], "Friday": ["evening"]}


def test_same_day_in_different_cases_is_merged():
    cleaned, _ = validate_onboarding_payload(
        {"study_schedule": {"Monday": ["morning"], "monday": ["morning", "evening"]}})

    assert cleaned["study_schedule"] == {"Monday": ["morning", "evening"]}


def test_a_lowercase_client_still_overlaps_with_everyone_else():
    """Regression: "monday" was stored as sent and never matched "Monday"."""
    mine, _ = validate_onboarding_payload({"study_schedule": {"monday": ["morning"]}})

    assert calculate_schedule_overlap(mine["study_schedule"], {"Monday": ["morning"]}) > 0
