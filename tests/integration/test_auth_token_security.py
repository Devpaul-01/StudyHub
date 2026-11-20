"""
One-time tokens and sessions (audit AUD-07, AUD-08, AUD-14, AUD-21).
"""
import datetime

import pytest

from extensions import db
from models import EmailVerificationToken, PasswordResetToken, RefreshToken
from services import auth_service
from errors import ValidationError

pytestmark = pytest.mark.integration


class TestHashedStorage:
    def test_raw_tokens_are_never_stored(self, db_session, make_user):
        user = make_user()
        raw_reset = auth_service.issue_password_reset_token(user)
        raw_verify = auth_service.issue_email_verification_token(user)
        db_session.commit()

        stored = [r.token_hash for r in PasswordResetToken.query.all()] + \
                 [r.token_hash for r in EmailVerificationToken.query.all()]
        assert raw_reset not in stored and raw_verify not in stored
        assert auth_service.hash_one_time_token(raw_reset) in stored
        assert all(len(h) == 64 for h in stored)

    def test_issuing_a_new_token_invalidates_older_ones(self, db_session, make_user):
        user = make_user()
        first = auth_service.issue_password_reset_token(user)
        db_session.commit()
        second = auth_service.issue_password_reset_token(user)
        db_session.commit()

        with pytest.raises(ValidationError):
            auth_service.peek_password_reset_token(first)
        assert auth_service.peek_password_reset_token(second).id == user.id


class TestSingleUse:
    def test_second_consume_fails(self, db_session, make_user):
        user = make_user()
        raw = auth_service.issue_password_reset_token(user)
        db_session.commit()

        auth_service.consume_password_reset_token(raw)
        db_session.commit()
        with pytest.raises(ValidationError):
            auth_service.consume_password_reset_token(raw)

    def test_peek_does_not_spend_the_token(self, db_session, make_user):
        user = make_user()
        raw = auth_service.issue_password_reset_token(user)
        db_session.commit()
        auth_service.peek_password_reset_token(raw)
        auth_service.peek_password_reset_token(raw)
        auth_service.consume_password_reset_token(raw)

    def test_consume_does_not_commit_so_a_failure_leaves_it_unspent(self, db_session, make_user):
        user = make_user()
        raw = auth_service.issue_password_reset_token(user)
        db_session.commit()

        auth_service.consume_password_reset_token(raw)
        db_session.rollback()  # the caller failed afterwards

        assert auth_service.peek_password_reset_token(raw).id == user.id


class TestSetPasswordRoute:
    def _reset(self, client, raw, password="brandnew123"):
        return client.post("/student/set-password", json={
            "token": raw, "password": password, "confirm_password": password})

    def test_reset_revokes_every_refresh_token_and_other_reset_links(
        self, client, db_session, make_user, make_refresh_token
    ):
        user = make_user()
        live = make_refresh_token(user)
        other_device = make_refresh_token(user)
        raw = auth_service.issue_password_reset_token(user)
        db_session.commit()

        assert self._reset(client, raw).status_code == 200

        db_session.expire_all()
        assert RefreshToken.query.get(live.id).revoked is True
        assert RefreshToken.query.get(other_device.id).revoked is True
        assert PasswordResetToken.query.filter_by(used=False).count() == 0

    def test_link_cannot_be_used_twice(self, client, db_session, make_user):
        user = make_user()
        raw = auth_service.issue_password_reset_token(user)
        db_session.commit()
        assert self._reset(client, raw).status_code == 200
        assert self._reset(client, raw, "another12345").status_code == 400

    def test_validation_failure_does_not_burn_the_token(self, client, db_session, make_user):
        user = make_user()
        raw = auth_service.issue_password_reset_token(user)
        db_session.commit()
        assert self._reset(client, raw, "abc").status_code == 400          # too short
        assert self._reset(client, raw).status_code == 200                  # still valid

    def test_unverified_account_failure_leaves_token_unspent(self, client, db_session, make_user):
        user = make_user(email_verified=False)
        raw = auth_service.issue_password_reset_token(user)
        db_session.commit()
        assert self._reset(client, raw).status_code == 400
        db_session.expire_all()
        assert auth_service.peek_password_reset_token(raw).id == user.id

    def test_garbage_and_missing_tokens(self, client):
        assert client.post("/student/set-password", json={"password": "x" * 8, "confirm_password": "x" * 8}).status_code == 400
        assert self._reset(client, "nope").status_code == 400

    def test_success_message_does_not_print_none_for_users_without_username(self, client, db_session, make_user):
        user = make_user(username=None, pin="PENDING_VERIFICATION", status="pending_verification")
        raw = auth_service.issue_password_reset_token(user)
        db_session.commit()
        resp = self._reset(client, raw)
        assert resp.status_code == 200 and "None" not in resp.get_json()["message"]


def _login_cookie_pair(client, user):
    """Log in through the real route so refresh/csrf cookies exist."""
    return client.post("/student/login", json={"username_or_email": user.username, "password": "testpassword123"})


class TestRefreshRotation:
    def test_rejected_account_does_not_burn_its_refresh_token(self, client, db_session, make_user):
        user = make_user(status="approved")
        assert _login_cookie_pair(client, user).status_code == 200
        user.status = "suspended"
        db_session.commit()

        resp = client.post("/student/refresh-token", headers=_csrf(client))

        assert resp.status_code == 400
        # nothing was rotated: no successor row was created
        assert RefreshToken.query.filter(RefreshToken.replaced_by_id.isnot(None)).count() == 0

    def test_verified_user_mid_registration_can_refresh(self, client, db_session, make_user):
        user = make_user(email="mid@example.com", username=None, pin="PENDING_VERIFICATION",
                         status="pending_verification", email_verified=True)
        from routes.student.helpers import generate_tokens_for_user, set_auth_cookies
        from flask import make_response
        access, refresh = generate_tokens_for_user(user)
        db_session.commit()
        client.set_cookie("refresh_token", refresh)

        resp = client.post("/student/refresh-token", headers=_csrf(client))

        assert resp.status_code == 200, resp.get_json()

    def test_unverified_user_cannot_refresh(self, client, db_session, make_user):
        user = make_user(email="unv@example.com", username=None, pin="PENDING_VERIFICATION",
                         status="pending_verification", email_verified=False)
        from routes.student.helpers import generate_tokens_for_user
        _, refresh = generate_tokens_for_user(user)
        db_session.commit()
        client.set_cookie("refresh_token", refresh)
        assert client.post("/student/refresh-token", headers=_csrf(client)).status_code == 400

    def test_lost_rotation_race_is_handled_like_reuse_not_a_second_rotation(self, db_session, make_user, monkeypatch):
        user = make_user()
        raw = auth_service.issue_refresh_token(user)
        db_session.commit()
        row = RefreshToken.query.filter_by(token_hash=auth_service._hash_refresh_token(raw)).one()

        real_issue = auth_service.issue_refresh_token

        def issue_then_lose_race(u, family_id=None):
            token = real_issue(u, family_id=family_id)
            # another request rotates the same token right after we read it
            RefreshToken.query.filter_by(id=row.id).update(
                {"revoked": True, "revoked_at": datetime.datetime.utcnow()}, synchronize_session=False)
            return token

        monkeypatch.setattr(auth_service, "issue_refresh_token", issue_then_lose_race)

        # No valid successor exists -> treated as reuse: family revoked, error.
        with pytest.raises(ValidationError):
            auth_service.rotate_refresh_token(raw)
        live = RefreshToken.query.filter_by(family_id=row.family_id, revoked=False).count()
        assert live == 0


def _csrf(client):
    token = "t" * 16
    client.set_cookie("csrf_token", token)
    return {"X-CSRF-Token": token}
