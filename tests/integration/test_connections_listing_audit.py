"""Audit CON-15 / CON-19 / CON-23: list endpoints."""
import datetime
import pytest

from extensions import db

pytestmark = pytest.mark.integration


def test_list_is_ordered_paginated_and_reports_true_total(client, make_user, make_connection, auth_headers):
    me = make_user(status="approved")
    others = [make_user(status="approved") for _ in range(5)]
    base = datetime.datetime.utcnow()
    for i, o in enumerate(others):
        c = make_connection(me, o, status="accepted")
        c.responded_at = base - datetime.timedelta(days=i)  # others[0] is the newest
    db.session.commit()

    page1 = client.get("/student/connections/list?limit=2", headers=auth_headers(me)).get_json()
    assert page1["total"] == 5 and len(page1["data"]) == 2
    assert page1["has_more"] is True and page1["next_cursor"] == "2"
    assert [d["user"]["id"] for d in page1["data"]] == [others[0].id, others[1].id]

    page2 = client.get(f"/student/connections/list?limit=2&cursor={page1['next_cursor']}",
                       headers=auth_headers(me)).get_json()
    assert [d["user"]["id"] for d in page2["data"]] == [others[2].id, others[3].id]

    last = client.get("/student/connections/list?limit=2&cursor=4", headers=auth_headers(me)).get_json()
    assert len(last["data"]) == 1 and last["has_more"] is False and last["next_cursor"] is None


def test_list_bad_params_fall_back_to_defaults(client, make_user, make_connection, auth_headers):
    me, other = make_user(status="approved"), make_user(status="approved")
    make_connection(me, other, status="accepted")
    db.session.commit()
    r = client.get("/student/connections/list?limit=abc&cursor=-5", headers=auth_headers(me))
    assert r.status_code == 200 and r.get_json()["total"] == 1


def test_list_includes_health_fields(client, make_user, make_connection, auth_headers):
    me, other = make_user(status="approved"), make_user(status="approved")
    make_connection(me, other, status="accepted")
    db.session.commit()
    item = client.get("/student/connections/list", headers=auth_headers(me)).get_json()["data"][0]
    assert 0 <= item["health_score"] <= 100 and item["suggestion"]


def test_received_requests_show_request_message_not_private_notes(client, make_user, make_connection, auth_headers):
    a, b = make_user(status="approved"), make_user(status="approved")
    make_connection(a, b, status="pending", request_message="hello b",
                    requester_notes="my private note about b")
    db.session.commit()

    data = client.get("/student/connections/requests/received", headers=auth_headers(b)).get_json()["data"]
    assert data[0]["message"] == "hello b"

    sent = client.get("/student/connections/requests/sent", headers=auth_headers(a)).get_json()["data"]
    assert sent[0]["your_message"] == "hello b"


def test_requests_are_paginated(client, make_user, make_connection, auth_headers):
    me = make_user(status="approved")
    for _ in range(3):
        make_connection(make_user(status="approved"), me, status="pending")
    db.session.commit()
    r = client.get("/student/connections/requests/received?limit=2", headers=auth_headers(me)).get_json()
    assert r["total"] == 3 and len(r["data"]) == 2 and r["has_more"] is True
