"""
Tests for routes/student/helpers.py token logic — _build_access_token,
decode_token, role_required (tested via the token_required/admin_required
aliases).

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.2. role_required is tested against
a real Flask test_request_context() rather than mocking request/g.
"""

import datetime

import jwt
import pytest
from freezegun import freeze_time

from routes.student import helpers
from errors import AuthorizationError


# ============================================================================
# _build_access_token
# ============================================================================

def test_build_access_token_returns_str_not_bytes(app, make_user, db_session):
    user = make_user(role="student")
    token = helpers._build_access_token(user)
    assert isinstance(token, str)


def test_build_access_token_roundtrip_claims(app, make_user, db_session):
    user = make_user(role="student", username="alice", name="Alice A", email="alice@example.com")
    token = helpers._build_access_token(user)

    payload = jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
    assert payload["user_id"] == user.id
    assert payload["username"] == "alice"
    assert payload["name"] == "Alice A"
    assert payload["email"] == "alice@example.com"
    assert payload["role"] == "student"


def test_build_access_token_expiry_is_30_minutes(app, make_user, db_session):
    user = make_user()
    with freeze_time("2026-01-01 12:00:00"):
        token = helpers._build_access_token(user)
        payload = jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
        exp = datetime.datetime.utcfromtimestamp(payload["exp"])
        assert exp == datetime.datetime(2026, 1, 1, 12, 30, 0)


# ============================================================================
# decode_token
# ============================================================================

def test_decode_token_valid(app, make_user, db_session):
    user = make_user()
    token = helpers._build_access_token(user)
    payload = helpers.decode_token(token)
    assert payload["user_id"] == user.id


def test_decode_token_expired_raises(app, make_user, db_session):
    user = make_user()
    with freeze_time("2026-01-01 12:00:00"):
        token = helpers._build_access_token(user)
    with freeze_time("2026-01-01 13:00:00"):  # 60 min later, past the 30 min exp
        with pytest.raises(jwt.ExpiredSignatureError):
            helpers.decode_token(token)


def test_decode_token_wrong_secret_raises(app, make_user, db_session):
    user = make_user()
    bad_token = jwt.encode(
        {"user_id": user.id, "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=30)},
        "a-completely-different-secret",
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidTokenError):
        helpers.decode_token(bad_token)


# ============================================================================
# role_required (token_required / admin_required aliases)
# ============================================================================

def _protected_view(user):
    """A trivial view function decorated per-test below."""
    return {"called_with_user_id": user.id}, 200


def test_token_required_no_auth_returns_401(app, db_session):
    view = helpers.token_required(_protected_view)
    with app.test_request_context("/"):
        response = view()
    assert response[1] == 401


def test_token_required_valid_cookie_calls_view_and_sets_g(app, make_user, db_session):
    user = make_user(role="student")
    token = helpers._build_access_token(user)
    view = helpers.token_required(_protected_view)

    with app.test_request_context("/", headers={"Cookie": f"access_token={token}"}):
        from flask import g
        result = view()
        assert result[0]["called_with_user_id"] == user.id
        assert g.current_user_id == user.id


def test_token_required_bearer_header_takes_precedence(app, make_user, db_session):
    """Confirms header-based auth works and, when both header and cookie
    are present, the header wins (per the plan's explicit case (c))."""
    correct_user = make_user(role="student")
    wrong_user = make_user(role="student")
    correct_token = helpers._build_access_token(correct_user)
    wrong_token = helpers._build_access_token(wrong_user)

    view = helpers.token_required(_protected_view)
    with app.test_request_context(
        "/",
        headers={
            "Authorization": f"Bearer {correct_token}",
            "Cookie": f"access_token={wrong_token}",
        },
    ):
        result = view()
    assert result[0]["called_with_user_id"] == correct_user.id


def test_token_required_wrong_role_raises_authorization_error(app, make_user, db_session):
    """This is the specific regression the module's own docstring flags:
    role_required must actually enforce the role set, not silently allow
    every authenticated user through."""
    admin_user = make_user(role="admin")
    token = helpers._build_access_token(admin_user)
    view = helpers.token_required(_protected_view)  # token_required == role_required("student")

    with app.test_request_context("/", headers={"Cookie": f"access_token={token}"}):
        with pytest.raises(AuthorizationError):
            view()


def test_admin_required_allows_admin_role(app, make_user, db_session):
    admin_user = make_user(role="admin")
    token = helpers._build_access_token(admin_user)
    view = helpers.admin_required(_protected_view)

    with app.test_request_context("/", headers={"Cookie": f"access_token={token}"}):
        result = view()
    assert result[0]["called_with_user_id"] == admin_user.id


def test_admin_required_allows_system_role(app, make_user, db_session):
    system_user = make_user(role="system")
    token = helpers._build_access_token(system_user)
    view = helpers.admin_required(_protected_view)

    with app.test_request_context("/", headers={"Cookie": f"access_token={token}"}):
        result = view()
    assert result[0]["called_with_user_id"] == system_user.id


def test_token_required_deleted_user_returns_401(app, make_user, db_session):
    """Valid token, but the user row is gone by the time the request is
    handled (e.g. deleted between token issuance and use)."""
    user = make_user(role="student")
    token = helpers._build_access_token(user)

    from extensions import db
    db.session.delete(user)
    db.session.commit()

    view = helpers.token_required(_protected_view)
    with app.test_request_context("/", headers={"Cookie": f"access_token={token}"}):
        response = view()
    assert response[1] == 401


def test_token_required_expired_token_returns_401(app, make_user, db_session):
    user = make_user(role="student")
    with freeze_time("2026-01-01 12:00:00"):
        token = helpers._build_access_token(user)

    view = helpers.token_required(_protected_view)
    with freeze_time("2026-01-01 13:00:00"):
        with app.test_request_context("/", headers={"Cookie": f"access_token={token}"}):
            response = view()
    assert response[1] == 401
    assert "expired" in response[0].get_json()["message"].lower()


def test_token_required_garbage_token_returns_401_no_unhandled_raise(app, db_session):
    view = helpers.token_required(_protected_view)
    with app.test_request_context("/", headers={"Cookie": "access_token=not.a.valid.jwt"}):
        response = view()
    assert response[1] == 401
    assert "invalid" in response[0].get_json()["message"].lower()
