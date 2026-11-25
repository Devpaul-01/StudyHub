"""
Onboarding service: "people you may want to study with" suggestions and the
"send connection requests" action offered at the end of the onboarding wizard.

Design rules (audit AUD-01, AUD-16):

* Callers are always authenticated. The user is taken from the access token,
  never from a URL parameter.
* Connecting during onboarding sends a normal *pending* request. Nobody is
  connected without the other person's consent.
* Existing rows are never overwritten: blocked, rejected, pending and
  accepted pairs are left exactly as they are and reported as skipped. The
  only state change is the same one the normal flow performs -- if the target
  already sent *us* a pending request, it is accepted.
* The candidate pool is bounded in SQL, so cost does not grow with the
  number of users.
* Scores are honest. When nothing matches, the fallback suggestions carry
  ``match_score = None`` instead of an invented number.
"""
import datetime
import logging

from sqlalchemy import and_, exists, or_
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Connection, OnboardingDetails, StudentProfile, User
from services import notification_service

logger = logging.getLogger(__name__)

# Upper bound on how many candidates are loaded and scored per request.
CANDIDATE_POOL_SIZE = 300
# How many suggestions are returned.
MAX_SUGGESTIONS = 10
FALLBACK_SUGGESTIONS = 6
# Minimum score for an algorithmic match to be shown.
MIN_MATCH_SCORE = 10
# Maximum number of users that can be requested in one bulk call.
MAX_BULK_CONNECT = 25


def _lower_set(values):
    return {str(v).lower() for v in (values or []) if isinstance(v, str)}


def _user_card(candidate, profile, c_onboard):
    return {
        "id": candidate.id,
        "username": candidate.username,
        "name": candidate.name,
        "avatar": candidate.avatar or "/static/default-avatar.png",
        "reputation": candidate.reputation,
        "reputation_level": candidate.reputation_level,
        "department": (profile.department if profile else None)
        or (c_onboard.department if c_onboard else None),
        "class_level": (profile.class_name if profile else None)
        or (c_onboard.class_level if c_onboard else None),
    }


def _candidate_query(user, my_department):
    """Approved users the requester has no connection row with (any status)."""
    already_related = exists().where(
        or_(
            and_(Connection.requester_id == user.id, Connection.receiver_id == User.id),
            and_(Connection.requester_id == User.id, Connection.receiver_id == user.id),
        )
    )
    query = (
        db.session.query(User, StudentProfile, OnboardingDetails)
        .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
        .outerjoin(OnboardingDetails, OnboardingDetails.user_id == User.id)
        .filter(User.id != user.id, User.status == "approved", ~already_related)
    )
    order = []
    if my_department:
        # Same-department candidates first, then by reputation.
        order.append((StudentProfile.department == my_department).desc())
    order.append(User.reputation.desc())
    order.append(User.id.asc())
    return query.order_by(*order).limit(CANDIDATE_POOL_SIZE)


def get_matches(user):
    """Return up to MAX_SUGGESTIONS suggestions for ``user``."""
    onboarding = OnboardingDetails.query.filter_by(user_id=user.id).first()
    my_profile = StudentProfile.query.filter_by(user_id=user.id).first()
    my_department = (onboarding.department if onboarding else None) or (
        my_profile.department if my_profile else None
    )

    rows = _candidate_query(user, my_department).all()
    matches = []

    for candidate, profile, c_onboard in rows:
        score = 0
        reasons = []

        c_department = (profile.department if profile else None) or (
            c_onboard.department if c_onboard else None
        )
        if my_department and c_department and my_department == c_department:
            score += 40
            reasons.append("Same department")

        if onboarding and c_onboard:
            shared = _lower_set(onboarding.subjects) & _lower_set(c_onboard.subjects)
            if shared:
                score += len(shared) * 10
                reasons.append(f"Studies {', '.join(sorted(shared)[:2])}")

            complementary = _lower_set(onboarding.help_subjects) & _lower_set(
                c_onboard.strong_subjects
            )
            if complementary:
                score += len(complementary) * 15
                reasons.append(f"Can help with {', '.join(sorted(complementary)[:2])}")

            if (
                onboarding.learning_style
                and c_onboard.learning_style
                and onboarding.learning_style == c_onboard.learning_style
            ):
                score += 10
                reasons.append("Same learning style")

            my_days = set((onboarding.study_schedule or {}).keys())
            their_days = set((c_onboard.study_schedule or {}).keys())
            if my_days & their_days:
                score += len(my_days & their_days) * 3
                reasons.append("Overlapping schedule")

        if (candidate.reputation or 0) >= 500:
            score += 10
            reasons.append(candidate.reputation_level or "High reputation")

        if score >= MIN_MATCH_SCORE:
            matches.append(
                {
                    "user": _user_card(candidate, profile, c_onboard),
                    "match_score": min(score, 99),
                    "reasons": reasons[:4],
                }
            )

    matches.sort(key=lambda m: m["match_score"], reverse=True)
    top = matches[:MAX_SUGGESTIONS]

    if not top:
        # No algorithmic match: show the most active members, honestly
        # labelled (no score) rather than with an invented percentage.
        for candidate, profile, c_onboard in rows[:FALLBACK_SUGGESTIONS]:
            top.append(
                {
                    "user": _user_card(candidate, profile, c_onboard),
                    "match_score": None,
                    "reasons": ["Active member"],
                }
            )
    return top


def normalize_target_ids(raw_ids, requester_id):
    """
    Validate a client-supplied id list. Returns (ids, error). ``ids`` is a
    de-duplicated list of positive ints without the requester; ``error`` is a
    message when the payload itself is malformed.
    """
    if not isinstance(raw_ids, list):
        return None, "ids must be a list of user ids"
    if len(raw_ids) > MAX_BULK_CONNECT:
        return None, f"You can send at most {MAX_BULK_CONNECT} requests at once"
    ids = []
    for value in raw_ids:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None, "ids must be a list of positive integers"
        if value != requester_id and value not in ids:
            ids.append(value)
    return ids, None


def connect_users(user, target_ids):
    """
    Send connection requests from ``user`` to every id in ``target_ids``.

    Returns a dict with ``sent`` (new pending requests), ``accepted``
    (reciprocal requests that were completed) and ``skipped`` (list of
    ``{"id", "reason"}``). Never raises for per-target problems.
    """
    result = {"sent": [], "accepted": [], "skipped": []}
    if not target_ids:
        return result

    targets = {
        u.id: u
        for u in User.query.filter(User.id.in_(target_ids), User.status == "approved").all()
    }
    existing = {}
    for conn in Connection.query.filter(
        or_(
            and_(Connection.requester_id == user.id, Connection.receiver_id.in_(target_ids)),
            and_(Connection.receiver_id == user.id, Connection.requester_id.in_(target_ids)),
        )
    ).all():
        other = conn.receiver_id if conn.requester_id == user.id else conn.requester_id
        existing[other] = conn

    now = datetime.datetime.utcnow()

    for target_id in target_ids:
        if target_id not in targets:
            result["skipped"].append({"id": target_id, "reason": "not_found"})
            continue

        conn = existing.get(target_id)
        if conn is not None:
            incoming_pending = conn.status == "pending" and conn.receiver_id == user.id
            if incoming_pending:
                conn.status = "accepted"
                conn.responded_at = now
                notification_service.notify_connection_accepted(target_id, user.name, user.id)
                result["accepted"].append(target_id)
            else:
                reason = "already_connected" if conn.status == "accepted" else (
                    "already_requested" if conn.status == "pending" else "unavailable"
                )
                result["skipped"].append({"id": target_id, "reason": reason})
            continue

        try:
            with db.session.begin_nested():
                db.session.add(
                    Connection(
                        requester_id=user.id,
                        receiver_id=target_id,
                        status="pending",
                        requested_at=now,
                        requester_notes="Requested during onboarding",
                    )
                )
        except IntegrityError:
            # Lost a race against the reverse-direction insert (uq_connections_pair).
            result["skipped"].append({"id": target_id, "reason": "already_requested"})
            continue

        notification_service.notify_connection_request(target_id, user.name, user.id)
        result["sent"].append(target_id)

    db.session.commit()
    return result


# ---------------------------------------------------------------------------
# Onboarding questionnaire validation (audit AUD-17)
# ---------------------------------------------------------------------------
CLASS_LEVELS = ("100 Level", "200 Level", "300 Level", "400 Level", "500 Level")
LEARNING_STYLES = ("visual", "auditory", "kinesthetic", "reading")
STUDY_PREFERENCES = ("group-study", "morning-study", "explaining-concepts", "practical-projects")
SESSION_LENGTHS = ("30-minutes", "1-hour", "1.5-hours", "2-hours")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
TIME_SLOTS = ("morning", "afternoon", "evening")

MAX_NAME_LENGTH = 100
MAX_DEPARTMENT_LENGTH = 100
MAX_SUBJECTS = 30
MAX_SUBJECT_LENGTH = 100


def _has_markup(text):
    """True if ``text`` contains an HTML angle bracket (names and subjects are plain text)."""
    return "<" in text or ">" in text


def _clean_string_list(value, field, errors, *, limit, item_length):
    if value is None or value == "":
        return []
    if not isinstance(value, list):
        errors[field] = f"{field} must be a list"
        return []
    if len(value) > limit:
        errors[field] = f"{field} can contain at most {limit} items"
        return []
    cleaned = []
    for item in value:
        if not isinstance(item, str):
            errors[field] = f"{field} must contain only text"
            return []
        item = item.strip()
        if not item:
            continue
        if len(item) > item_length:
            errors[field] = f"Each entry in {field} must be at most {item_length} characters"
            return []
        if _has_markup(item):
            errors[field] = f"Entries in {field} cannot contain '<' or '>'"
            return []
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


def _choice(value, field, allowed, errors):
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or value not in allowed:
        errors[field] = f"{field} must be one of: {', '.join(allowed)}"
        return ""
    return value


def validate_onboarding_payload(data):
    """
    Validate and normalise the onboarding questionnaire.

    Returns ``(cleaned, errors)``. ``errors`` maps field name -> message and is
    empty when the payload is acceptable. Department stays free text (the UI
    offers an "Other" option) but is length-limited; everything else is an
    enumeration or a bounded list.
    """
    errors = {}
    if not isinstance(data, dict):
        return None, {"_": "Invalid data"}

    name = data.get("name", "")
    if name is None:
        name = ""
    if not isinstance(name, str):
        errors["name"] = "name must be text"
        name = ""
    name = name.strip()
    if len(name) > MAX_NAME_LENGTH:
        errors["name"] = f"name must be at most {MAX_NAME_LENGTH} characters"
    elif _has_markup(name):
        errors["name"] = "name cannot contain '<' or '>'"

    department = data.get("department", "")
    if department is None:
        department = ""
    if not isinstance(department, str):
        errors["department"] = "department must be text"
        department = ""
    department = department.strip()
    if len(department) > MAX_DEPARTMENT_LENGTH:
        errors["department"] = f"department must be at most {MAX_DEPARTMENT_LENGTH} characters"
    elif _has_markup(department):
        errors["department"] = "department cannot contain '<' or '>'"

    schedule_raw = data.get("study_schedule", {})
    schedule = {}
    if schedule_raw in (None, ""):
        schedule_raw = {}
    if not isinstance(schedule_raw, dict):
        errors["study_schedule"] = "study_schedule must be an object"
    else:
        for day, slots in schedule_raw.items():
            if not isinstance(day, str) or day.lower() not in WEEKDAYS:
                errors["study_schedule"] = "study_schedule days must be weekday names"
                schedule = {}
                break
            if not isinstance(slots, list) or any(s not in TIME_SLOTS for s in slots):
                errors["study_schedule"] = f"study_schedule times must be from: {', '.join(TIME_SLOTS)}"
                schedule = {}
                break
            if slots:
                # Stored as "Monday": the casing connection_service's schedule
                # scoring compares against. A client that sends "monday" would
                # otherwise never overlap with anyone.
                merged = schedule.setdefault(day.capitalize(), [])
                merged.extend(s for s in dict.fromkeys(slots) if s not in merged)

    prefs = _clean_string_list(
        data.get("study_preferences"), "study_preferences", errors,
        limit=len(STUDY_PREFERENCES), item_length=MAX_SUBJECT_LENGTH,
    )
    if "study_preferences" not in errors and any(p not in STUDY_PREFERENCES for p in prefs):
        errors["study_preferences"] = f"study_preferences must be from: {', '.join(STUDY_PREFERENCES)}"

    cleaned = {
        "name": name,
        "department": department,
        "class_level": _choice(data.get("class_level"), "class_level", CLASS_LEVELS, errors),
        "subjects": _clean_string_list(
            data.get("subjects"), "subjects", errors, limit=MAX_SUBJECTS, item_length=MAX_SUBJECT_LENGTH),
        "help_subjects": _clean_string_list(
            data.get("help_subjects"), "help_subjects", errors, limit=MAX_SUBJECTS, item_length=MAX_SUBJECT_LENGTH),
        "strong_subjects": _clean_string_list(
            data.get("strong_subjects"), "strong_subjects", errors, limit=MAX_SUBJECTS, item_length=MAX_SUBJECT_LENGTH),
        "learning_style": _choice(data.get("learning_style"), "learning_style", LEARNING_STYLES, errors),
        "study_preferences": prefs,
        "study_schedule": schedule,
        "session_length": _choice(data.get("session_length"), "session_length", SESSION_LENGTHS, errors),
    }
    return cleaned, errors
