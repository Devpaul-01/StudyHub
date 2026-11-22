"""profile data cleanup and relaxed NOT NULL on student_profiles

Revision ID: a3c5d7e9f102
Revises: c3a1d94e7b52
Create Date: 2026-11-22 00:00:00.000000

First half of the profile production-readiness migration (expand step).
It is safe to run BEFORE the new application code is deployed and does not
remove anything:

* Relaxes NOT NULL on student_profiles.full_name (and pin, if a database
  still has it: the auth/onboarding migration c3a1d94e7b52 normally dropped
  it already). The new code no longer writes full_name (it is dropped by the
  next revision), so inserts from the new code must not fail on a database
  that has not been through the drop yet.
* Copies study schedules saved through the old profile editor
  (users.study_schedule) onto the onboarding record that matching actually
  reads, where that record has none.
* Aligns onboarding_details.department / class_level with the
  student_profiles values the rest of the app uses.
* Removes the dead set_dark_mode / send_weekly_notification keys from
  users.privacy_settings and turns stored string booleans ("false") into
  real booleans.
* Removes markup characters from name, department, skills, learning goals
  and the onboarding subject lists, drops non-string list items, and strips
  HTML tags from bios, so stored data satisfies the same rules the API now
  enforces.

Every step checks that the table/column exists first, so it also works on
databases that were created by db.create_all() at different points in time.
The downgrade is a no-op for data (the cleanup is not reversible) apart from
restoring NOT NULL where it can.
"""
import re

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3c5d7e9f102'
down_revision = 'c3a1d94e7b52'
branch_labels = None
depends_on = None


STUDY_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
STUDY_SLOTS = ("morning", "afternoon", "evening")
LIVE_PRIVACY_DEFAULTS = {
    "set_profile_private": False,
    "show_active_status": True,
    "show_in_activity_feed": True,
}
DEAD_PRIVACY_KEYS = ("set_dark_mode", "send_weekly_notification")
_TAG = re.compile(r"<[^>]*>")
_TAG_START = re.compile(r"<\s*[a-zA-Z/!?]")


# ── helpers ──────────────────────────────────────────────────────────────────

def _columns(bind, table):
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _strip_markup(text):
    return text.replace("<", "").replace(">", "")


def _clean_string_list(value, max_items, max_len):
    """Same rules as models._clean_string_list / the profile validators."""
    if not isinstance(value, list):
        return []
    cleaned, seen = [], set()
    for item in value:
        if not isinstance(item, str):
            continue
        text = " ".join(_strip_markup(item).split())[:max_len]
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _schedule_to_onboarding_format(schedule):
    """Lowercase-day / 4-slot profile format -> {"Monday": ["morning"]}."""
    result = {}
    if not isinstance(schedule, dict):
        return result
    for day, slots in schedule.items():
        if not isinstance(day, str) or day.capitalize() not in STUDY_DAYS or not isinstance(slots, list):
            continue
        kept = []
        for slot in slots:
            if isinstance(slot, str) and slot.lower() in STUDY_SLOTS and slot.lower() not in kept:
                kept.append(slot.lower())
        if kept:
            result[day.capitalize()] = kept
    return result


def _normalise_privacy(value):
    if not isinstance(value, dict):
        return value
    cleaned = {k: v for k, v in value.items() if k not in DEAD_PRIVACY_KEYS}
    for key, default in LIVE_PRIVACY_DEFAULTS.items():
        if key in cleaned and not isinstance(cleaned[key], bool):
            raw = cleaned[key]
            if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
                cleaned[key] = raw.strip().lower() == "true"
            else:
                cleaned[key] = default
    return cleaned


# ── steps ────────────────────────────────────────────────────────────────────

def _relax_not_null(bind):
    cols = _columns(bind, "student_profiles")
    if not ({"pin", "full_name"} & cols):
        return
    with op.batch_alter_table("student_profiles") as batch:
        if "pin" in cols:
            batch.alter_column("pin", existing_type=sa.String(200), nullable=True)
        if "full_name" in cols:
            batch.alter_column("full_name", existing_type=sa.String(120), nullable=True)


def _backfill_study_schedule(bind):
    if "study_schedule" not in _columns(bind, "users") or "study_schedule" not in _columns(bind, "onboarding_details"):
        return
    users = sa.table("users", sa.column("id"), sa.column("study_schedule", sa.JSON))
    onboarding = sa.table("onboarding_details", sa.column("id"), sa.column("user_id"),
                          sa.column("study_schedule", sa.JSON))

    mine = {row.id: row.study_schedule for row in bind.execute(sa.select(users.c.id, users.c.study_schedule))}
    for row in bind.execute(sa.select(onboarding.c.id, onboarding.c.user_id, onboarding.c.study_schedule)).fetchall():
        if row.study_schedule:               # onboarding already has one: it wins
            continue
        converted = _schedule_to_onboarding_format(mine.get(row.user_id))
        if converted:
            bind.execute(onboarding.update().where(onboarding.c.id == row.id).values(study_schedule=converted))


def _align_department_and_class(bind):
    sp_cols, ob_cols = _columns(bind, "student_profiles"), _columns(bind, "onboarding_details")
    if not ({"department", "class_name"} <= sp_cols and {"department", "class_level"} <= ob_cols):
        return
    sp = sa.table("student_profiles", sa.column("user_id"), sa.column("department"), sa.column("class_name"))
    ob = sa.table("onboarding_details", sa.column("id"), sa.column("user_id"),
                  sa.column("department"), sa.column("class_level"))
    profiles = {r.user_id: r for r in bind.execute(sa.select(sp.c.user_id, sp.c.department, sp.c.class_name))}
    for row in bind.execute(sa.select(ob.c.id, ob.c.user_id, ob.c.department, ob.c.class_level)).fetchall():
        profile = profiles.get(row.user_id)
        if not profile:
            continue
        values = {}
        if profile.department and profile.department != row.department and len(profile.department) <= 100:
            values["department"] = profile.department
        if profile.class_name and profile.class_name != row.class_level and len(profile.class_name) <= 20:
            values["class_level"] = profile.class_name
        if values:
            bind.execute(ob.update().where(ob.c.id == row.id).values(**values))


def _clean_privacy_settings(bind):
    if "privacy_settings" not in _columns(bind, "users"):
        return
    users = sa.table("users", sa.column("id"), sa.column("privacy_settings", sa.JSON))
    for row in bind.execute(sa.select(users.c.id, users.c.privacy_settings)).fetchall():
        cleaned = _normalise_privacy(row.privacy_settings)
        if cleaned != row.privacy_settings:
            bind.execute(users.update().where(users.c.id == row.id).values(privacy_settings=cleaned))


def _clean_user_text(bind):
    cols = _columns(bind, "users")
    wanted = [c for c in ("name", "bio", "skills", "learning_goals") if c in cols]
    if not wanted:
        return
    users = sa.table(
        "users", sa.column("id"), sa.column("name"), sa.column("bio"),
        sa.column("skills", sa.JSON), sa.column("learning_goals", sa.JSON),
    )
    for row in bind.execute(sa.select(users.c.id, *[users.c[c] for c in wanted])).fetchall():
        values = {}
        if "name" in wanted and isinstance(row.name, str) and ("<" in row.name or ">" in row.name):
            values["name"] = " ".join(_strip_markup(row.name).split()) or "Student"
        if "bio" in wanted and isinstance(row.bio, str) and _TAG_START.search(row.bio):
            values["bio"] = _TAG.sub("", row.bio).strip()
        if "skills" in wanted and row.skills is not None:
            cleaned = _clean_string_list(row.skills, 10, 50)
            if cleaned != row.skills:
                values["skills"] = cleaned
        if "learning_goals" in wanted and row.learning_goals is not None:
            cleaned = _clean_string_list(row.learning_goals, 5, 100)
            if cleaned != row.learning_goals:
                values["learning_goals"] = cleaned
        if values:
            bind.execute(users.update().where(users.c.id == row.id).values(**values))


def _clean_profile_and_onboarding_text(bind):
    if "department" in _columns(bind, "student_profiles"):
        sp = sa.table("student_profiles", sa.column("id"), sa.column("department"))
        for row in bind.execute(sa.select(sp.c.id, sp.c.department)).fetchall():
            if isinstance(row.department, str) and ("<" in row.department or ">" in row.department):
                bind.execute(sp.update().where(sp.c.id == row.id)
                             .values(department=" ".join(_strip_markup(row.department).split())))

    lists = [c for c in ("subjects", "strong_subjects", "help_subjects", "study_preferences")
             if c in _columns(bind, "onboarding_details")]
    if not lists:
        return
    ob = sa.table("onboarding_details", sa.column("id"), *[sa.column(c, sa.JSON) for c in lists])
    for row in bind.execute(sa.select(ob.c.id, *[ob.c[c] for c in lists])).fetchall():
        values = {}
        for c in lists:
            current = getattr(row, c)
            if current is None:
                continue
            cleaned = _clean_string_list(current, 30, 100)
            if cleaned != current:
                values[c] = cleaned
        if values:
            bind.execute(ob.update().where(ob.c.id == row.id).values(**values))


# ── entry points ─────────────────────────────────────────────────────────────

def upgrade():
    bind = op.get_bind()
    _relax_not_null(bind)
    _backfill_study_schedule(bind)
    _align_department_and_class(bind)
    _clean_privacy_settings(bind)
    _clean_user_text(bind)
    _clean_profile_and_onboarding_text(bind)


def downgrade():
    # Data cleanup is not reversible. Restore NOT NULL only where every row
    # satisfies it, otherwise leave the columns nullable.
    bind = op.get_bind()
    cols = _columns(bind, "student_profiles")
    sp = sa.table("student_profiles", sa.column("pin"), sa.column("full_name"))
    with op.batch_alter_table("student_profiles") as batch:
        for name, length in (("pin", 200), ("full_name", 120)):
            if name in cols:
                nulls = bind.execute(sa.select(sa.func.count()).select_from(sp).where(sp.c[name].is_(None))).scalar()
                if not nulls:
                    batch.alter_column(name, existing_type=sa.String(length), nullable=False)
