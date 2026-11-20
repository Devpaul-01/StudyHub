"""Account-status gate in role_required (audit AUD-04)."""
import pytest

pytestmark = pytest.mark.integration

SENTINEL = "PENDING_VERIFICATION"


def _pending(make_user, status="pending_verification", verified=True):
    return make_user(
        email="pending@example.com", username=None, pin=SENTINEL,
        status=status, email_verified=verified,
    )


def test_pending_user_is_rejected_on_regular_routes(client, make_user, auth_headers):
    user = _pending(make_user)
    resp = client.get("/student/analytics/comparison", headers=auth_headers(user))
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "registration_incomplete"


@pytest.mark.parametrize("status", ["pending_verification", "pending_onboarding"])
def test_pending_user_may_use_opt_in_routes(client, make_user, auth_headers, status):
    user = _pending(make_user, status=status)
    assert client.get("/student/users/me", headers=auth_headers(user)).status_code == 200
    assert client.get("/student/auth/me", headers=auth_headers(user)).status_code == 200


def test_unverified_pending_user_is_rejected_even_on_opt_in_routes(client, make_user, auth_headers):
    user = _pending(make_user, verified=False)
    assert client.get("/student/users/me", headers=auth_headers(user)).status_code == 403


def test_suspended_user_is_rejected_everywhere(client, make_user, auth_headers):
    user = make_user(email="susp@example.com", status="suspended")
    assert client.get("/student/users/me", headers=auth_headers(user)).status_code == 403
    assert client.get("/student/analytics/comparison", headers=auth_headers(user)).status_code == 403


def test_approved_user_is_unaffected(client, make_user, auth_headers):
    user = make_user(email="ok@example.com", status="approved")
    assert client.get("/student/users/me", headers=auth_headers(user)).status_code == 200
