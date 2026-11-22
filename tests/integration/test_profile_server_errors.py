"""Unexpected exceptions in profile routes become 500s with a logged traceback."""
import logging

import pytest


class _Exploding:
    """Stands in for a model class whose queries blow up."""
    @property
    def query(self):
        raise RuntimeError("database is on fire")


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_unexpected_failure_is_a_500_with_a_traceback(
    app, client, make_user, make_student_profile, auth_headers, monkeypatch
):
    user = make_user()
    make_student_profile(user)
    monkeypatch.setattr("routes.student.profile.Connection", _Exploding())
    handler = _ListHandler()
    app.logger.addHandler(handler)   # the app logger does not propagate to caplog

    try:
        resp = client.get("/student/profile/me/data", headers=auth_headers(user))
    finally:
        app.logger.removeHandler(handler)

    assert resp.status_code == 500
    assert resp.get_json()["message"] == "Failed to load profile data"
    assert "database is on fire" not in resp.get_data(as_text=True)
    records = [r for r in handler.records if "Get my profile data error" in r.getMessage()]
    assert records and records[0].exc_info, "traceback must be attached to the log record"


def test_validation_failures_stay_400(client, make_user, make_student_profile, auth_headers, csrf_headers):
    user = make_user()
    make_student_profile(user)

    resp = client.patch("/student/profile/update", json={"name": 5},
                        headers={**auth_headers(user), **csrf_headers(client)})

    assert resp.status_code == 400
