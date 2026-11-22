"""The onboarding questionnaire only accepts plain-text names, departments and subjects."""
import pytest

from services.onboarding_service import validate_onboarding_payload


@pytest.mark.parametrize("field", ["name", "department"])
@pytest.mark.parametrize("value", ['"><img src=x onerror=alert(1)>', "<script>alert(1)</script>", "a<b"])
def test_markup_in_text_fields_is_rejected(field, value):
    cleaned, errors = validate_onboarding_payload({field: value})

    assert field in errors
    assert "cannot contain" in errors[field]


@pytest.mark.parametrize("field", ["subjects", "help_subjects", "strong_subjects"])
def test_markup_in_subject_lists_is_rejected(field):
    cleaned, errors = validate_onboarding_payload({field: ["Maths", "<b>x</b>"]})

    assert field in errors
    assert cleaned[field] == []


def test_ordinary_text_passes():
    cleaned, errors = validate_onboarding_payload(
        {"name": "Ada O'Neil", "department": "Computer Science & Engineering", "subjects": ["C/C++", "C#"]})

    assert errors == {}
    assert cleaned["subjects"] == ["C/C++", "C#"]
