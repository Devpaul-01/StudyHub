"""Alembic migrations for the profile clean-up, run against a SQLite file.

The 'old' schema is built from raw table definitions (only the tables the
migrations touch), data that exercises every cleanup rule is inserted, then
the real migration scripts are run through Flask-Migrate in a subprocess.
"""
import json
import pathlib
import subprocess
import sys

import pytest
import sqlalchemy as sa

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tests/integration/migration_runner.py"
# Parent of the cleanup revision: the auth/onboarding migration on main, which
# already dropped student_profiles.{pin, username, status, date_of_birth,
# guardian_*}.
BASE_REVISION = "c3a1d94e7b52"
CLEANUP_REVISION = "a3c5d7e9f102"
DROP_REVISION = "d4e6f8a0b213"


def _run(db_file, action, revision):
    result = subprocess.run([sys.executable, str(RUNNER), str(db_file), action, revision],
                            capture_output=True, text=True, cwd=ROOT, timeout=180)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-3000:]


def _build_old_schema(engine):
    md = sa.MetaData()
    sa.Table("users", md,
             sa.Column("id", sa.Integer, primary_key=True), sa.Column("name", sa.String(100)),
             sa.Column("bio", sa.Text), sa.Column("skills", sa.JSON), sa.Column("learning_goals", sa.JSON),
             sa.Column("study_schedule", sa.JSON), sa.Column("privacy_settings", sa.JSON))
    sp = sa.Table("student_profiles", md,
                  sa.Column("id", sa.Integer, primary_key=True),
                  sa.Column("user_id", sa.Integer, nullable=False, unique=True),
                  sa.Column("full_name", sa.String(120), nullable=False),
                  sa.Column("department", sa.String(100), index=True),
                  sa.Column("class_name", sa.String(50), index=True),
                  sa.Column("registered_at", sa.DateTime))
    sa.Index("idx_student_profiles_dept_user", sp.c.department, sp.c.user_id)
    sa.Table("onboarding_details", md,
             sa.Column("id", sa.Integer, primary_key=True), sa.Column("user_id", sa.Integer),
             sa.Column("department", sa.String(50)), sa.Column("class_level", sa.String(20)),
             sa.Column("subjects", sa.JSON), sa.Column("strong_subjects", sa.JSON),
             sa.Column("help_subjects", sa.JSON), sa.Column("study_preferences", sa.JSON),
             sa.Column("study_schedule", sa.JSON))
    sa.Table("profile_change_history", md,
             sa.Column("id", sa.Integer, primary_key=True), sa.Column("user_id", sa.Integer, nullable=False),
             sa.Column("field_changed", sa.String(100), nullable=False))
    sa.Table("alembic_version", md, sa.Column("version_num", sa.String(32), nullable=False))
    md.create_all(engine)
    return md


def _seed(engine, md):
    t = md.tables
    with engine.begin() as c:
        c.execute(t["users"].insert(), [
            {"id": 1, "name": '"><img src=x onerror=alert(1)>', "bio": "hello <script>alert(1)</script>world",
             "skills": ["Python", {"a": 1}, "python", "<b>Go</b>", 5],
             "learning_goals": ["Learn <i>ML</i>"],
             "study_schedule": {"monday": ["morning", "night"], "FRIDAY": ["Evening"], "funday": ["morning"]},
             "privacy_settings": {"set_profile_private": "false", "show_active_status": "true",
                                  "show_in_activity_feed": 1, "set_dark_mode": True,
                                  "send_weekly_notification": False, "future_key": 7}},
            {"id": 2, "name": "Plain Name", "bio": "I <3 maths", "skills": ["SQL"], "learning_goals": [],
             "study_schedule": {"tuesday": ["morning"]}, "privacy_settings": {"set_profile_private": True}},
            {"id": 3, "name": "No Onboarding", "bio": None, "skills": None, "learning_goals": None,
             "study_schedule": {"sunday": ["evening"]}, "privacy_settings": None},
        ])
        c.execute(t["student_profiles"].insert(), [
            {"user_id": 1, "full_name": "x", "department": "Biology", "class_name": "200 Level"},
            {"user_id": 2, "full_name": "y", "department": "Physics", "class_name": "Junior"},
            {"user_id": 3, "full_name": "z", "department": "<b>Law</b>", "class_name": "Senior"},
        ])
        c.execute(t["onboarding_details"].insert(), [
            {"id": 1, "user_id": 1, "department": "Chemistry", "class_level": "100 Level",
             "subjects": ["Math", {"x": 1}], "strong_subjects": "oops", "help_subjects": ["<b>A</b>", "a"],
             "study_preferences": [], "study_schedule": {}},
            {"id": 2, "user_id": 2, "department": "Physics", "class_level": "Junior",
             "subjects": ["Art"], "strong_subjects": [], "help_subjects": [], "study_preferences": [],
             "study_schedule": {"Wednesday": ["morning"]}},
        ])
        c.execute(t["profile_change_history"].insert(), [{"user_id": 1, "field_changed": "name"}])
        c.execute(t["alembic_version"].insert(), [{"version_num": BASE_REVISION}])


@pytest.fixture
def migrated_db(tmp_path):
    db_file = tmp_path / "old.db"
    engine = sa.create_engine(f"sqlite:///{db_file}")
    md = _build_old_schema(engine)
    _seed(engine, md)
    engine.dispose()
    return db_file


def _rows(db_file, sql):
    engine = sa.create_engine(f"sqlite:///{db_file}")
    try:
        with engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(sa.text(sql))]
    finally:
        engine.dispose()


def _load(value):
    return json.loads(value) if isinstance(value, str) else value


def _columns(db_file, table):
    return {c["name"] for c in sa.inspect(sa.create_engine(f"sqlite:///{db_file}")).get_columns(table)}


class TestCleanupRevision:
    @pytest.fixture(autouse=True)
    def _upgrade(self, migrated_db):
        _run(migrated_db, "upgrade", CLEANUP_REVISION)
        self.db = migrated_db

    def test_full_name_becomes_nullable_and_nothing_is_dropped(self):
        info = {r["name"]: r for r in _rows(self.db, "PRAGMA table_info(student_profiles)")}
        assert info["full_name"]["notnull"] == 0
        assert {"full_name", "registered_at", "department", "class_name"} <= set(info)
        assert "study_schedule" in _columns(self.db, "users")

    def test_old_profile_schedule_is_copied_in_matching_format_only_when_onboarding_has_none(self):
        rows = {r["user_id"]: _load(r["study_schedule"]) for r in _rows(self.db, "SELECT user_id, study_schedule FROM onboarding_details")}
        assert rows[1] == {"Monday": ["morning"], "Friday": ["evening"]}   # night / unknown day dropped
        assert rows[2] == {"Wednesday": ["morning"]}                        # existing onboarding schedule wins

    def test_department_and_class_follow_the_student_profile(self):
        rows = {r["user_id"]: r for r in _rows(self.db, "SELECT user_id, department, class_level FROM onboarding_details")}
        assert (rows[1]["department"], rows[1]["class_level"]) == ("Biology", "200 Level")
        assert (rows[2]["department"], rows[2]["class_level"]) == ("Physics", "Junior")

    def test_privacy_settings_lose_dead_keys_and_get_real_booleans(self):
        rows = {r["id"]: _load(r["privacy_settings"]) for r in _rows(self.db, "SELECT id, privacy_settings FROM users")}
        assert rows[1] == {"set_profile_private": False, "show_active_status": True,
                           "show_in_activity_feed": True, "future_key": 7}
        assert rows[2] == {"set_profile_private": True}
        assert rows[3] is None          # JSON null stays null

    def test_markup_is_removed_from_user_text_and_lists(self):
        users = {r["id"]: r for r in _rows(self.db, "SELECT id, name, bio, skills, learning_goals FROM users")}
        assert "<" not in users[1]["name"] and ">" not in users[1]["name"]
        assert "<script" not in users[1]["bio"]
        assert _load(users[1]["skills"]) == ["Python", "bGo/b"]
        assert _load(users[1]["learning_goals"]) == ["Learn iML/i"]
        assert users[2]["name"] == "Plain Name" and users[2]["bio"] == "I <3 maths"
        assert _load(users[3]["skills"]) is None

    def test_profile_and_onboarding_text_are_cleaned(self):
        dept = {r["user_id"]: r["department"] for r in _rows(self.db, "SELECT user_id, department FROM student_profiles")}
        assert dept[3] == "bLaw/b"
        ob = {r["user_id"]: r for r in _rows(self.db, "SELECT user_id, subjects, strong_subjects, help_subjects FROM onboarding_details")}
        assert _load(ob[1]["subjects"]) == ["Math"]
        assert _load(ob[1]["strong_subjects"]) == []
        assert _load(ob[1]["help_subjects"]) == ["bA/b", "a"]


class TestDropRevision:
    def test_drops_the_unused_objects_and_keeps_everything_else(self, migrated_db):
        _run(migrated_db, "upgrade", DROP_REVISION)

        tables = {r["name"] for r in _rows(migrated_db, "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "profile_change_history" not in tables
        assert _columns(migrated_db, "student_profiles") == {"id", "user_id", "department", "class_name"}
        assert "study_schedule" not in _columns(migrated_db, "users")
        assert {"id", "name", "bio", "skills", "privacy_settings"} <= _columns(migrated_db, "users")
        indexes = {r["name"] for r in _rows(migrated_db, "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_student_profiles_dept_user" in indexes
        assert _rows(migrated_db, "SELECT user_id, department FROM student_profiles ORDER BY user_id")[0] == {
            "user_id": 1, "department": "Biology"}
        assert _rows(migrated_db, "SELECT version_num FROM alembic_version") == [{"version_num": DROP_REVISION}]

    def test_downgrade_restores_the_structure(self, migrated_db):
        _run(migrated_db, "upgrade", DROP_REVISION)
        _run(migrated_db, "downgrade", CLEANUP_REVISION)

        assert {"full_name", "registered_at"} <= _columns(migrated_db, "student_profiles")
        assert "study_schedule" in _columns(migrated_db, "users")
        tables = {r["name"] for r in _rows(migrated_db, "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "profile_change_history" in tables

    def test_upgrade_is_safe_on_a_database_that_never_had_the_objects(self, tmp_path):
        db_file = tmp_path / "new.db"
        engine = sa.create_engine(f"sqlite:///{db_file}")
        md = sa.MetaData()
        sa.Table("users", md, sa.Column("id", sa.Integer, primary_key=True), sa.Column("name", sa.String(100)))
        sa.Table("student_profiles", md, sa.Column("id", sa.Integer, primary_key=True),
                 sa.Column("user_id", sa.Integer), sa.Column("department", sa.String(100)))
        sa.Table("alembic_version", md, sa.Column("version_num", sa.String(32), nullable=False))
        md.create_all(engine)
        with engine.begin() as c:
            c.execute(md.tables["alembic_version"].insert(), [{"version_num": BASE_REVISION}])
        engine.dispose()

        _run(db_file, "upgrade", DROP_REVISION)

        assert _rows(db_file, "SELECT version_num FROM alembic_version") == [{"version_num": DROP_REVISION}]
