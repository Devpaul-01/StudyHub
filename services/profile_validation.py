"""Validation and normalisation for user-editable profile fields.

Pure functions (no Flask, no DB) so they can be unit-tested and reused by
the profile routes and the registration flow.

Rules
-----
* Short free-text fields (name, department, skills, goals, subjects) are
  plain text: HTML angle brackets and control characters are rejected so
  the value can never carry markup into a template that forgets to
  escape it. Rendering code must still escape output - this is defence
  in depth, not a replacement.
* The bio may contain ``<`` / ``>`` as ordinary characters ("I <3 maths")
  but not anything that looks like the start of a tag.
* Every failure raises :class:`ProfileValidationError` with a message that
  is safe to show to the user.
"""
import re

__all__ = [
    "ProfileValidationError",
    "CLASS_LEVELS",
    "STUDY_DAYS",
    "STUDY_SLOTS",
    "clean_plain_text",
    "clean_bio",
    "clean_text_list",
    "clean_class_level",
    "clean_study_schedule",
    "strip_markup_chars",
]

# Matches the class levels offered during onboarding plus the legacy
# labels understood by the mentor-matching hierarchy.
CLASS_LEVELS = (
    "100 Level", "200 Level", "300 Level", "400 Level", "500 Level",
    "Freshman", "Sophomore", "Junior", "Senior",
)

STUDY_DAYS = (
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)
# Matches onboarding and ``connection_service.calculate_schedule_overlap``:
# stored day keys are capitalised ("Monday") and slots are these three values.
STUDY_SLOTS = ("morning", "afternoon", "evening")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TAG_START = re.compile(r"<\s*[a-zA-Z/!?]")
_WHITESPACE = re.compile(r"\s+")


class ProfileValidationError(ValueError):
    """Raised when a profile field fails validation (message is user-facing)."""


def strip_markup_chars(text):
    """Remove ``<`` and ``>`` (used when cleaning legacy stored values)."""
    return text.replace("<", "").replace(">", "")


def clean_plain_text(value, field, *, max_len, min_len=1, allow_empty=False):
    """Validate a single-line plain-text value and return it normalised."""
    if not isinstance(value, str):
        raise ProfileValidationError(f"{field} must be text")
    text = _CONTROL_CHARS.sub("", value)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        if allow_empty:
            return ""
        raise ProfileValidationError(f"{field} cannot be empty")
    if "<" in text or ">" in text:
        raise ProfileValidationError(f"{field} cannot contain '<' or '>'")
    if len(text) < min_len:
        raise ProfileValidationError(
            f"{field} must be at least {min_len} characters")
    if len(text) > max_len:
        raise ProfileValidationError(
            f"{field} must be at most {max_len} characters")
    return text


def clean_bio(value, *, max_len=500):
    """Validate a multi-line bio. Empty string is allowed."""
    if not isinstance(value, str):
        raise ProfileValidationError("Bio must be text")
    text = _CONTROL_CHARS.sub("", value.replace("\r\n", "\n")).strip()
    if _TAG_START.search(text):
        raise ProfileValidationError("Bio cannot contain HTML tags")
    if len(text) > max_len:
        raise ProfileValidationError(
            f"Bio must be at most {max_len} characters")
    return text


def clean_text_list(value, field, *, max_items, max_item_len):
    """Validate a list of short plain-text items.

    Items are trimmed and de-duplicated case-insensitively (first spelling
    wins). Anything that is not a list of strings is rejected rather than
    silently coerced.
    """
    if not isinstance(value, list):
        raise ProfileValidationError(f"{field} must be a list")
    result, seen = [], set()
    for item in value:
        text = clean_plain_text(item, f"{field} item", max_len=max_item_len)
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    if len(result) > max_items:
        raise ProfileValidationError(
            f"{field} allows at most {max_items} items")
    return result


def clean_class_level(value):
    """Validate a class level against the canonical onboarding set."""
    text = clean_plain_text(value, "Class level", max_len=20)
    for level in CLASS_LEVELS:
        if text.lower() == level.lower():
            return level
    raise ProfileValidationError(
        "Class level must be one of: " + ", ".join(CLASS_LEVELS))


def clean_study_schedule(value):
    """Validate ``{day: [slot, ...]}`` and drop days with no slots.

    Days are accepted in any case and returned capitalised, the format the
    onboarding flow stores and the schedule-overlap scorer reads.
    """
    if not isinstance(value, dict):
        raise ProfileValidationError("Schedule must be an object")
    result = {}
    for day, slots in value.items():
        if not isinstance(day, str) or day.lower() not in STUDY_DAYS:
            raise ProfileValidationError(f"Unknown day: {str(day)[:20]}")
        if not isinstance(slots, list):
            raise ProfileValidationError(f"Slots for {day} must be a list")
        cleaned = []
        for slot in slots:
            if not isinstance(slot, str) or slot.lower() not in STUDY_SLOTS:
                raise ProfileValidationError(
                    f"Unknown time slot for {day}: {str(slot)[:20]}")
            slot = slot.lower()
            if slot not in cleaned:
                cleaned.append(slot)
        if cleaned:
            result[day.capitalize()] = cleaned
    return result
