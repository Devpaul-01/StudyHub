"""
services/homework_validation.py

Request-payload validation for the homework feature (audit findings F-01,
F-09, F-10). Pure functions: no Flask imports, no request/session/g, no DB.

Every function either returns a cleaned value or raises
HomeworkValidationError with a user-presentable message. Routes catch that
exception and return a 4xx; anything else is a genuine server fault.

Why one module: create/update (and the submission endpoints) previously each
re-implemented a different subset of these checks, so the same field could be
accepted by one path and rejected (or crash) on another.
"""

from __future__ import annotations

import datetime
import math
from urllib.parse import urlparse


class HomeworkValidationError(ValueError):
    """A client-side input problem. ``field`` names the offending field."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


# ----------------------------------------------------------------------------
# Limits / allow-lists
# ----------------------------------------------------------------------------

ASSIGNMENT_STATUSES = ("not_started", "in_progress", "completed")
DIFFICULTIES = ("easy", "medium", "hard")
RESOURCE_TYPES = ("image", "video", "document", "pdf", "audio")

MAX_TITLE_LEN = 200          # matches assignments.title String(200)
MAX_SUBJECT_LEN = 100        # matches assignments.subject String(100)
MAX_DESCRIPTION_LEN = 5_000
MAX_SOLUTION_LEN = 20_000
MAX_FEEDBACK_LEN = 5_000
MAX_RESOURCES = 10
MAX_RESOURCE_URL_LEN = 2_048
MAX_RESOURCE_FILENAME_LEN = 255
MAX_ESTIMATED_HOURS = 1_000


# ----------------------------------------------------------------------------
# Generic helpers
# ----------------------------------------------------------------------------

def require_json_object(data) -> dict:
    """The request body must be a JSON object. ``request.get_json(silent=True)``
    returns None for a missing/invalid body; reject that (and lists/strings)
    with a clear message instead of crashing on ``.get``."""
    if not isinstance(data, dict):
        raise HomeworkValidationError("Request body must be a JSON object")
    return data


def clean_text(
    value,
    field: str,
    *,
    max_len: int,
    required: bool = False,
    default: str = "",
) -> str:
    """Return a stripped string. ``None`` becomes ``default`` unless required."""
    if value is None:
        if required:
            raise HomeworkValidationError(f"{field.replace('_', ' ').capitalize()} is required", field)
        return default
    if not isinstance(value, str):
        raise HomeworkValidationError(f"{field.replace('_', ' ').capitalize()} must be text", field)
    cleaned = value.strip()
    if required and not cleaned:
        raise HomeworkValidationError(f"{field.replace('_', ' ').capitalize()} is required", field)
    if len(cleaned) > max_len:
        raise HomeworkValidationError(
            f"{field.replace('_', ' ').capitalize()} must be at most {max_len} characters", field
        )
    return cleaned


def clean_choice(value, field: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        raise HomeworkValidationError(
            f"{field.replace('_', ' ').capitalize()} must be one of: {', '.join(allowed)}", field
        )
    return value.strip().lower()


def clean_bool(value, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise HomeworkValidationError(f"{field.replace('_', ' ').capitalize()} must be true or false", field)


def clean_estimated_hours(value) -> float | None:
    """None/'' clears the estimate; otherwise a finite number in (0, MAX]."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise HomeworkValidationError("Estimated hours must be a number", "estimated_hours")
    try:
        hours = float(value)
    except (TypeError, ValueError):
        raise HomeworkValidationError("Estimated hours must be a number", "estimated_hours")
    if not math.isfinite(hours) or hours <= 0 or hours > MAX_ESTIMATED_HOURS:
        raise HomeworkValidationError(
            f"Estimated hours must be greater than 0 and at most {MAX_ESTIMATED_HOURS}",
            "estimated_hours",
        )
    return hours


# ----------------------------------------------------------------------------
# Due dates (F-10)
# ----------------------------------------------------------------------------

def parse_due_date(value) -> datetime.datetime:
    """
    Parse an ISO-8601 instant and return a **naive UTC** datetime (the
    representation the ``assignments.due_date`` column and every comparison
    in the backend use).

    * ``2026-12-01T10:00:00Z`` / ``...+01:00``  -> converted to UTC, tzinfo dropped.
    * ``2026-12-01T10:00:00`` (no offset)        -> already treated as UTC. The web
      client always sends an explicit instant (``Date.toISOString()``); a bare
      value can only come from an older client or an API integration, and is
      interpreted as UTC, the documented contract.
    """
    if not isinstance(value, str) or not value.strip():
        raise HomeworkValidationError("Due date is required", "due_date")
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(raw)
    except ValueError:
        raise HomeworkValidationError("Invalid due date format (use ISO 8601)", "due_date")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def require_future(due_date: datetime.datetime, *, now: datetime.datetime | None = None) -> None:
    now = now or datetime.datetime.utcnow()
    if due_date < now:
        raise HomeworkValidationError("Due date must be in the future", "due_date")


# ----------------------------------------------------------------------------
# Resources / attachments (F-01, F-09)
# ----------------------------------------------------------------------------

def clean_resources(value, field: str = "resources") -> list[dict]:
    """
    Validate an attachment list and return a normalised copy containing ONLY
    ``url``, ``type`` and ``filename`` (unknown keys are dropped so arbitrary
    JSON cannot be stored).

    URLs must be absolute http(s) with a host. ``javascript:``, ``data:``,
    ``vbscript:`` and scheme-less values are rejected, so a stored resource can
    never become an executable link in any client.
    """
    if value is None or value == []:
        return []
    if not isinstance(value, list):
        raise HomeworkValidationError("Resources must be an array", field)
    if len(value) > MAX_RESOURCES:
        raise HomeworkValidationError(f"At most {MAX_RESOURCES} attachments are allowed", field)

    cleaned: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise HomeworkValidationError("Each resource must be an object", field)

        url = item.get("url")
        if not url or not isinstance(url, str):
            raise HomeworkValidationError("Each resource must have a url", field)
        url = url.strip()
        if len(url) > MAX_RESOURCE_URL_LEN:
            raise HomeworkValidationError("Resource url is too long", field)
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
            raise HomeworkValidationError("Resource url contains invalid characters", field)
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            raise HomeworkValidationError("Resource url must be an http(s) link", field)

        rtype = item.get("type")
        if not rtype or not isinstance(rtype, str):
            raise HomeworkValidationError("Each resource must have a type", field)
        rtype = rtype.strip().lower()
        if rtype not in RESOURCE_TYPES:
            raise HomeworkValidationError(
                f"Resource type must be one of: {', '.join(RESOURCE_TYPES)}", field
            )

        filename = item.get("filename")
        if filename is None:
            filename = ""
        if not isinstance(filename, str):
            raise HomeworkValidationError("Resource filename must be text", field)
        filename = filename.strip()
        if len(filename) > MAX_RESOURCE_FILENAME_LEN:
            raise HomeworkValidationError("Resource filename is too long", field)

        cleaned.append({"url": url, "type": rtype, "filename": filename})
    return cleaned


# ----------------------------------------------------------------------------
# Assignment payloads
# ----------------------------------------------------------------------------

def validate_assignment_create(data) -> dict:
    """Validate a POST /assignments body; returns column-ready values."""
    data = require_json_object(data)

    due_date = parse_due_date(data.get("due_date"))
    require_future(due_date)

    return {
        "title": clean_text(data.get("title"), "title", max_len=MAX_TITLE_LEN, required=True),
        "subject": clean_text(data.get("subject"), "subject", max_len=MAX_SUBJECT_LEN),
        "description": clean_text(data.get("description"), "description", max_len=MAX_DESCRIPTION_LEN),
        "due_date": due_date,
        "difficulty": clean_choice(data.get("difficulty", "medium"), "difficulty", DIFFICULTIES),
        "estimated_hours": clean_estimated_hours(data.get("estimated_hours")),
        "resources": clean_resources(data.get("resources")),
        "is_shared_for_help": clean_bool(data.get("share_for_help", False), "share_for_help"),
    }


def validate_assignment_update(data) -> dict:
    """
    Validate a PUT /assignments/<id> body. Returns ONLY the keys that were
    supplied (column-ready). Unlike create, a due date is not required to be
    in the future: users legitimately edit an already-overdue assignment.
    """
    data = require_json_object(data)
    out: dict = {}

    if "title" in data:
        out["title"] = clean_text(data["title"], "title", max_len=MAX_TITLE_LEN, required=True)
    if "subject" in data:
        out["subject"] = clean_text(data["subject"], "subject", max_len=MAX_SUBJECT_LEN)
    if "description" in data:
        out["description"] = clean_text(data["description"], "description", max_len=MAX_DESCRIPTION_LEN)
    if "due_date" in data:
        out["due_date"] = parse_due_date(data["due_date"])
    if "difficulty" in data:
        out["difficulty"] = clean_choice(data["difficulty"], "difficulty", DIFFICULTIES)
    if "estimated_hours" in data:
        out["estimated_hours"] = clean_estimated_hours(data["estimated_hours"])
    if "resources" in data:
        out["resources"] = clean_resources(data["resources"])
    if "status" in data:
        out["status"] = clean_choice(data["status"], "status", ASSIGNMENT_STATUSES)
    return out


# ----------------------------------------------------------------------------
# Submission payloads (F-07, F-09)
# ----------------------------------------------------------------------------

# One source of truth for reaction keys (audit F-18): validation, the CHECK
# constraint, the UI picker and the stats chart all use these keys.
REACTION_LABELS = {
    "thanks": "Thanks 🙏",
    "lifesaver": "Lifesaver 🔥",
    "mindblown": "Mind Blown 🧠",
    "perfect": "Perfect ⭐",
}
REACTION_TYPES = tuple(REACTION_LABELS)


def validate_solution_submit(data) -> dict:
    """Validate a submit-solution body -> {"solution_text", "resources"}."""
    data = require_json_object(data)
    return {
        "solution_text": clean_text(
            data.get("solution_text"), "solution_text", max_len=MAX_SOLUTION_LEN, required=True
        ),
        "resources": clean_resources(data.get("resources"), "resources"),
    }


def validate_feedback(data) -> dict:
    """
    Validate a give-feedback body. Returns column-ready values; ``reaction_type``
    and ``rating`` are None when not supplied. ``is_helpful`` is None when the
    client did not state a view (the route decides the default).
    """
    data = require_json_object(data)

    reaction = data.get("reaction_type")
    if reaction is not None:
        reaction = clean_choice(reaction, "reaction_type", REACTION_TYPES)

    rating = data.get("rating")
    if rating is not None:
        if isinstance(rating, bool):
            raise HomeworkValidationError("Rating must be a whole number from 1 to 5", "rating")
        try:
            as_float = float(rating)
        except (TypeError, ValueError):
            raise HomeworkValidationError("Rating must be a whole number from 1 to 5", "rating")
        if not math.isfinite(as_float) or as_float != int(as_float) or not 1 <= int(as_float) <= 5:
            raise HomeworkValidationError("Rating must be a whole number from 1 to 5", "rating")
        rating = int(as_float)

    is_helpful = data.get("is_helpful")
    if is_helpful is not None:
        is_helpful = clean_bool(is_helpful, "is_helpful")

    return {
        "feedback_text": clean_text(data.get("feedback_text"), "feedback_text", max_len=MAX_FEEDBACK_LEN),
        "feedback_resources": clean_resources(data.get("feedback_resources"), "feedback_resources"),
        "reaction_type": reaction,
        "rating": rating,
        "is_helpful": is_helpful,
        "mark_complete": clean_bool(data.get("mark_complete", True), "mark_complete"),
    }
