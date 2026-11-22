"""Audit CON-15: suggestion candidate selection is relationship-aware and deterministic."""
import datetime
import pytest

from extensions import db

pytestmark = pytest.mark.integration


def _ids(resp):
    data = resp.get_json()["data"]
    return data


@pytest.fixture
def world(make_user, make_connection, make_onboarding_details, make_student_profile):
    me = make_user(status="approved")
    related = {
        s: make_user(status="approved") for s in ("pending", "accepted", "rejected", "blocked")
    }
    for status, other in related.items():
        kw = {"blocked_by_id": me.id} if status == "blocked" else {}
        make_connection(me, other, status=status, **kw)
    candidates = [make_user(status="approved") for _ in range(3)]
    now = datetime.datetime.utcnow()
    for i, c in enumerate(candidates):
        c.last_active = now - datetime.timedelta(hours=i)
    for u in [me, *related.values(), *candidates]:
        make_student_profile(u, department="Computer Science")
        make_onboarding_details(u, subjects=["Physics"], strong_subjects=["Physics"])
    db.session.commit()
    return me, related, candidates


def test_flat_suggestions_exclude_every_related_user_and_include_strangers(client, world, auth_headers):
    me, related, candidates = world
    resp = client.get("/student/connections/suggestions/flat", headers=auth_headers(me))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for other in related.values():
        assert f'"id": {other.id},' not in body and f'"id":{other.id},' not in body
    for c in candidates:
        assert str(c.username) in body


def test_suggestions_endpoint_runs_and_excludes_related_users(client, world, auth_headers):
    me, related, candidates = world
    resp = client.get("/student/connections/suggestions", headers=auth_headers(me))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for other in related.values():
        assert other.username not in body
