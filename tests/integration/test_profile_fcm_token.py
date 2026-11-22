"""Integration tests for the FCM token endpoints and logout clearing."""
from models import User

URL = "/student/profile/fcm-token"


def _headers(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


def test_saving_a_token_detaches_it_from_other_accounts(
    client, db_session, make_user, auth_headers, csrf_headers
):
    """Regression: two accounts could hold the same device token."""
    first, second = make_user(), make_user()
    client.post(URL, json={"token": "device-token"}, headers=_headers(client, auth_headers, csrf_headers, first))

    client.post(URL, json={"token": "device-token"}, headers=_headers(client, auth_headers, csrf_headers, second))

    db_session.expire_all()
    assert User.query.get(first.id).fcm_token is None
    assert User.query.get(second.id).fcm_token == "device-token"
    assert User.query.filter_by(fcm_token="device-token").count() == 1


def test_other_accounts_tokens_are_untouched(client, db_session, make_user, auth_headers, csrf_headers):
    first, second = make_user(fcm_token="first-device"), make_user()

    client.post(URL, json={"token": "second-device"}, headers=_headers(client, auth_headers, csrf_headers, second))

    db_session.expire_all()
    assert User.query.get(first.id).fcm_token == "first-device"


def test_token_validation(client, make_user, auth_headers, csrf_headers):
    user = make_user()
    h = _headers(client, auth_headers, csrf_headers, user)

    assert client.post(URL, json={}, headers=h).status_code == 400
    assert client.post(URL, json={"token": "x" * 501}, headers=h).status_code == 400


def test_logout_clears_the_users_push_token(client, db_session, make_user, auth_headers, csrf_headers):
    """Regression: push notifications kept reaching a device after logout."""
    user = make_user(fcm_token="device-token")
    headers = auth_headers(user)
    # Logout identifies the user from the access_token cookie.
    client.set_cookie("access_token", headers["Authorization"].split(" ", 1)[1])

    resp = client.post("/student/logout", headers={**headers, **csrf_headers(client)})

    assert resp.status_code == 200
    db_session.expire_all()
    assert User.query.get(user.id).fcm_token is None
