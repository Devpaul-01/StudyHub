"""
tests/integration/test_homework_idempotent_create.py

Audit F-25: a retried/double-submitted create must not produce two rows when
the client sends the same idempotency key, and must keep working when Redis is
unavailable.
"""

import datetime

import pytest

pytestmark = pytest.mark.integration


def _payload(**extra):
    due = (datetime.datetime.utcnow() + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"title": "Essay", "due_date": due, **extra}


def _post(client, user, auth_headers, csrf_headers, body):
    return client.post(
        "/student/assignments", json=body, headers={**auth_headers(user), **csrf_headers(client)}
    )


def _count(user):
    from models import Assignment

    return Assignment.query.filter_by(user_id=user.id).count()


def test_same_key_creates_one_row_and_replays_response(
    client, db_session, make_user, auth_headers, csrf_headers, fakeredis_client
):
    user = make_user()
    body = _payload(idempotency_key="abcdef123456")

    first = _post(client, user, auth_headers, csrf_headers, body)
    second = _post(client, user, auth_headers, csrf_headers, body)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json()["data"]["id"] == first.get_json()["data"]["id"]
    assert _count(user) == 1


def test_different_keys_create_separate_rows(
    client, db_session, make_user, auth_headers, csrf_headers, fakeredis_client
):
    user = make_user()
    _post(client, user, auth_headers, csrf_headers, _payload(idempotency_key="key-number-0001"))
    _post(client, user, auth_headers, csrf_headers, _payload(idempotency_key="key-number-0002"))
    assert _count(user) == 2


def test_key_is_scoped_per_user(
    client, db_session, make_user, auth_headers, csrf_headers, fakeredis_client
):
    a, b = make_user(), make_user()
    body = _payload(idempotency_key="shared-key-1234")
    assert _post(client, a, auth_headers, csrf_headers, body).status_code == 201
    assert _post(client, b, auth_headers, csrf_headers, body).status_code == 201
    assert _count(a) == 1 and _count(b) == 1


def test_in_flight_duplicate_gets_409(
    client, db_session, make_user, auth_headers, csrf_headers, fakeredis_client
):
    from services import idempotency_service as idem

    user = make_user()
    assert idem.begin("hw-create", user.id, "inflight-key-1")[0] == idem.NEW

    resp = _post(client, user, auth_headers, csrf_headers, _payload(idempotency_key="inflight-key-1"))

    assert resp.status_code == 409
    assert _count(user) == 0


def test_failed_validation_releases_the_key(
    client, db_session, make_user, auth_headers, csrf_headers, fakeredis_client
):
    user = make_user()
    bad = _post(client, user, auth_headers, csrf_headers, {"title": "", "idempotency_key": "retry-key-0001"})
    assert bad.status_code == 400

    good = _post(client, user, auth_headers, csrf_headers, _payload(idempotency_key="retry-key-0001"))
    assert good.status_code == 201


def test_invalid_key_is_ignored(
    client, db_session, make_user, auth_headers, csrf_headers, fakeredis_client
):
    user = make_user()
    body = _payload(idempotency_key="x")  # too short -> no guard
    assert _post(client, user, auth_headers, csrf_headers, body).status_code == 201
    assert _post(client, user, auth_headers, csrf_headers, body).status_code == 201
    assert _count(user) == 2


def test_redis_down_fails_open(
    client, db_session, make_user, auth_headers, csrf_headers, raising_redis_client
):
    user = make_user()
    resp = _post(client, user, auth_headers, csrf_headers, _payload(idempotency_key="abcdef123456"))
    assert resp.status_code == 201
    assert _count(user) == 1
