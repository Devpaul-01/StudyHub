"""Audit CON-14: available-now schedule matching and online definition."""
import datetime
import types
import pytest

from extensions import db

pytestmark = pytest.mark.integration


def _freeze(monkeypatch, when):
    import routes.student.connections.discovery as disc

    class FakeDT(datetime.datetime):
        @classmethod
        def utcnow(cls):
            return when

    monkeypatch.setattr(disc, "datetime", types.SimpleNamespace(
        datetime=FakeDT, timedelta=datetime.timedelta, date=datetime.date, time=datetime.time))


def _setup(make_user, make_connection, make_onboarding_details, schedule, last_active=None, in_session=False):
    me, friend = make_user(status="approved"), make_user(status="approved")
    friend.last_active = last_active
    friend.in_study_session = in_session
    make_connection(me, friend, status="accepted")
    make_onboarding_details(friend, study_schedule=schedule)
    db.session.commit()
    return me, friend


def _get(client, auth_headers, me):
    return client.get("/student/connections/available-now", headers=auth_headers(me)).get_json()["data"]


def test_capitalised_day_key_matches_schedule(client, make_user, make_connection, make_onboarding_details, auth_headers, monkeypatch):
    monday_9am = datetime.datetime(2026, 11, 23, 9, 0)      # a Monday
    _freeze(monkeypatch, monday_9am)
    me, friend = _setup(make_user, make_connection, make_onboarding_details, {"Monday": ["morning"]},
                        last_active=monday_9am - datetime.timedelta(days=3))

    data = _get(client, auth_headers, me)

    assert data["total"] == 1
    entry = data["available_now"][0]
    assert entry["availability"]["schedule_available"] is True
    assert entry["availability"]["is_online"] is False


def test_no_slot_at_night_and_wrong_day_does_not_match(client, make_user, make_connection, make_onboarding_details, auth_headers, monkeypatch):
    night = datetime.datetime(2026, 11, 23, 23, 30)
    _freeze(monkeypatch, night)
    me, friend = _setup(make_user, make_connection, make_onboarding_details,
                        {"Monday": ["morning", "afternoon", "evening", "night"]},
                        last_active=night - datetime.timedelta(days=3))
    assert _get(client, auth_headers, me)["total"] == 0   # nothing scheduled "now" at 23:30


def test_online_uses_shared_service_including_study_session(client, make_user, make_connection, make_onboarding_details, auth_headers):
    # last_active is old but the user is in a study session -> online per
    # online_status_service (the old inline 30-minute check said offline).
    old = datetime.datetime.utcnow() - datetime.timedelta(hours=5)
    me, friend = _setup(make_user, make_connection, make_onboarding_details, {}, last_active=old, in_session=True)
    entry = _get(client, auth_headers, me)["available_now"][0]
    assert entry["availability"]["is_online"] is True
