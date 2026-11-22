"""Query-parameter bounds for the leaderboard endpoints."""
import pytest


def _seed(make_user, n=52):
    for i in range(n):
        make_user(reputation=i)
    return make_user(reputation=1)


@pytest.mark.parametrize("limit", ["-1", "0", "-999", "abc"])
def test_global_limit_is_clamped_to_a_valid_page_size(
    client, make_user, auth_headers, fakeredis_client, limit
):
    viewer = _seed(make_user)
    resp = client.get(f"/student/leaderboard/global?limit={limit}", headers=auth_headers(viewer))

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert 1 <= len(data["leaderboard"]) <= 50
    assert data["pagination"]["limit"] >= 1


def test_global_limit_above_the_maximum_is_capped(client, make_user, auth_headers, fakeredis_client):
    viewer = _seed(make_user)
    data = client.get("/student/leaderboard/global?limit=500", headers=auth_headers(viewer)).get_json()["data"]
    assert len(data["leaderboard"]) == 50
    assert data["pagination"]["limit"] == 50


def test_huge_page_number_is_clamped_not_a_server_error(client, make_user, auth_headers, fakeredis_client):
    viewer = _seed(make_user)
    resp = client.get(f"/student/leaderboard/global?page={10**18}", headers=auth_headers(viewer))
    assert resp.status_code == 200
    assert resp.get_json()["data"]["pagination"]["page"] <= 1000


@pytest.mark.parametrize("value", ["-1", "0"])
def test_nearby_range_and_rising_limit_and_history_weeks_are_clamped(
    client, make_user, auth_headers, fakeredis_client, value
):
    viewer = _seed(make_user)
    for path, key in (("nearby", "range"), ("rising", "limit"), ("rank-history", "weeks")):
        resp = client.get(f"/student/leaderboard/{path}?{key}={value}", headers=auth_headers(viewer))
        assert resp.status_code == 200, path


def test_unknown_department_returns_an_empty_board_and_caches_nothing(
    client, make_user, make_student_profile, auth_headers, fakeredis_client
):
    viewer = make_user(reputation=5)
    make_student_profile(viewer, department="CS")

    before = set(fakeredis_client.keys("sh:1:lb:global:*"))
    resp = client.get("/student/leaderboard/global?department=Not%20A%20Real%20Dept", headers=auth_headers(viewer))

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["leaderboard"] == [] and data["pagination"]["total"] == 0
    assert set(fakeredis_client.keys("sh:1:lb:global:*")) == before


def test_department_filter_is_case_insensitive_and_canonicalised(
    client, make_user, make_student_profile, auth_headers, fakeredis_client
):
    viewer = make_user(reputation=5, status="approved")
    make_student_profile(viewer, department="Computer Science")

    data = client.get("/student/leaderboard/global?department=computer%20%20SCIENCE",
                      headers=auth_headers(viewer)).get_json()["data"]

    assert data["department"] == "Computer Science"
    assert len(data["leaderboard"]) == 1


@pytest.mark.parametrize("path", ["me", "nearby", "rising"])
def test_unknown_department_on_per_user_endpoints_is_a_400(
    client, make_user, make_student_profile, auth_headers, fakeredis_client, path
):
    viewer = make_user(reputation=5)
    make_student_profile(viewer, department="CS")
    resp = client.get(f"/student/leaderboard/{path}?department=Nope", headers=auth_headers(viewer))
    assert resp.status_code == 400
