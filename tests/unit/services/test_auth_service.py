"""
Tests for services/auth_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.1. rotate_refresh_token is the
highest-priority test in this file -- it's the actual stolen-token-
detection mechanism, and the grace-window vs full-family-revocation
distinction is a genuinely tricky boundary worth testing on both sides
explicitly, with freezegun rather than real sleeps.
"""

import datetime
import hashlib

import pytest
from freezegun import freeze_time
from werkzeug.security import check_password_hash

from services import auth_service
from errors import ValidationError
from models import UserActivity, RefreshToken


# ============================================================================
# update_login_streak
# ============================================================================

def test_update_login_streak_first_ever_login(db_session, make_user):
    user = make_user(login_streak=0, last_active=None)
    now = datetime.datetime(2026, 1, 15, 10, 0, 0)
    auth_service.update_login_streak(user, now=now)
    assert user.login_streak == 1
    assert user.last_active == now


def test_update_login_streak_same_day_unchanged(db_session, make_user):
    user = make_user(login_streak=5, last_active=datetime.datetime(2026, 1, 15, 8, 0, 0))
    now = datetime.datetime(2026, 1, 15, 20, 0, 0)
    auth_service.update_login_streak(user, now=now)
    assert user.login_streak == 5


def test_update_login_streak_next_day_increments(db_session, make_user):
    user = make_user(login_streak=5, last_active=datetime.datetime(2026, 1, 15, 23, 0, 0))
    now = datetime.datetime(2026, 1, 16, 1, 0, 0)
    auth_service.update_login_streak(user, now=now)
    assert user.login_streak == 6


def test_update_login_streak_gap_resets_to_one(db_session, make_user):
    user = make_user(login_streak=10, last_active=datetime.datetime(2026, 1, 10, 12, 0, 0))
    now = datetime.datetime(2026, 1, 15, 12, 0, 0)
    auth_service.update_login_streak(user, now=now)
    assert user.login_streak == 1


# ============================================================================
# record_activity
# ============================================================================

def test_record_activity_creates_row_when_none_exists(db_session, make_user):
    user = make_user()
    today = datetime.date(2026, 1, 15)
    activity = auth_service.record_activity(user.id, "post", today=today)
    assert activity.activity_score == 1
    assert activity.posts_created == 1


def test_record_activity_increments_existing_row(db_session, make_user):
    user = make_user()
    today = datetime.date(2026, 1, 15)
    auth_service.record_activity(user.id, "post", today=today)
    auth_service.record_activity(user.id, "post", today=today)

    assert UserActivity.query.filter_by(user_id=user.id, activity_date=today).count() == 1
    row = UserActivity.query.filter_by(user_id=user.id, activity_date=today).first()
    assert row.activity_score == 2
    assert row.posts_created == 2


def test_record_activity_unrecognized_type_only_bumps_score(db_session, make_user):
    user = make_user()
    today = datetime.date(2026, 1, 15)
    activity = auth_service.record_activity(user.id, "login", today=today)
    assert activity.activity_score == 1
    assert activity.posts_created == 0
    assert activity.comments_created == 0


def test_record_activity_swallows_exceptions_returns_none(db_session, make_user, monkeypatch):
    user = make_user()

    def _boom(*a, **kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(auth_service, "_get_or_create_today_activity", _boom)
    result = auth_service.record_activity(user.id, "post")
    assert result is None


# ============================================================================
# record_login_and_commit
# ============================================================================

def test_record_login_and_commit_normal_case(db_session, make_user):
    user = make_user(login_streak=0, last_active=None)
    updated = auth_service.record_login_and_commit(user)
    assert updated.login_streak == 1
    assert UserActivity.query.filter_by(user_id=user.id).count() == 1


# ============================================================================
# finalize_password
# ============================================================================

def test_finalize_password_updates_user_and_profile_hash(db_session, make_user, make_student_profile):
    user = make_user(email="pw@example.com", email_verified=True)
    profile = make_student_profile(user)

    updated_user = auth_service.finalize_password("pw@example.com", "NewSecurePass123")

    assert check_password_hash(updated_user.pin, "NewSecurePass123")
    assert check_password_hash(profile.pin, "NewSecurePass123")


def test_finalize_password_no_profile_still_succeeds(db_session, make_user):
    user = make_user(email="pw2@example.com", email_verified=True)
    updated_user = auth_service.finalize_password("pw2@example.com", "AnotherPass456")
    assert check_password_hash(updated_user.pin, "AnotherPass456")


def test_finalize_password_unverified_email_raises_lookup_error(db_session, make_user):
    make_user(email="unverified@example.com", email_verified=False)
    with pytest.raises(LookupError):
        auth_service.finalize_password("unverified@example.com", "Whatever123")


def test_finalize_password_no_such_user_raises_lookup_error(db_session):
    with pytest.raises(LookupError):
        auth_service.finalize_password("nobody@example.com", "Whatever123")


# ============================================================================
# password reset tokens
# ============================================================================

def test_issue_password_reset_token_returns_raw_token_and_persists_row(db_session, make_user):
    user = make_user()
    now = datetime.datetime(2026, 1, 1, 12, 0, 0)
    with freeze_time(now):
        raw_token = auth_service.issue_password_reset_token(user)
    db_session.commit()

    from models import PasswordResetToken
    row = PasswordResetToken.query.filter_by(token=raw_token).first()
    assert row is not None
    assert row.used is False
    assert row.expires_at == now + datetime.timedelta(hours=1)


def test_consume_password_reset_token_valid(db_session, make_user, make_password_reset_token):
    user = make_user()
    token_row = make_password_reset_token(user)

    result_user = auth_service.consume_password_reset_token(token_row.token)

    assert result_user.id == user.id
    refreshed = type(token_row).query.get(token_row.id)
    assert refreshed.used is True
    assert refreshed.used_at is not None


def test_consume_password_reset_token_already_used_raises(db_session, make_user, make_password_reset_token):
    user = make_user()
    token_row = make_password_reset_token(user, used=True)
    with pytest.raises(ValidationError):
        auth_service.consume_password_reset_token(token_row.token)


def test_consume_password_reset_token_expired_raises(db_session, make_user, make_password_reset_token):
    user = make_user()
    token_row = make_password_reset_token(
        user, expires_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
    )
    with pytest.raises(ValidationError):
        auth_service.consume_password_reset_token(token_row.token)


def test_consume_password_reset_token_nonexistent_raises(db_session):
    with pytest.raises(ValidationError):
        auth_service.consume_password_reset_token("this-token-does-not-exist")


def test_consume_password_reset_token_empty_raises_no_query(db_session):
    with pytest.raises(ValidationError):
        auth_service.consume_password_reset_token("")
    with pytest.raises(ValidationError):
        auth_service.consume_password_reset_token(None)


# ============================================================================
# email verification tokens (same shape, 5h expiry)
# ============================================================================

def test_issue_email_verification_token_5h_expiry(db_session, make_user):
    user = make_user()
    now = datetime.datetime(2026, 1, 1, 12, 0, 0)
    with freeze_time(now):
        raw_token = auth_service.issue_email_verification_token(user)
    db_session.commit()

    from models import EmailVerificationToken
    row = EmailVerificationToken.query.filter_by(token=raw_token).first()
    assert row.expires_at == now + datetime.timedelta(hours=5)


def test_consume_email_verification_token_valid(db_session, make_user, make_email_verification_token):
    user = make_user()
    token_row = make_email_verification_token(user)
    result_user = auth_service.consume_email_verification_token(token_row.token)
    assert result_user.id == user.id
    refreshed = type(token_row).query.get(token_row.id)
    assert refreshed.used is True


def test_consume_email_verification_token_expired_raises(db_session, make_user, make_email_verification_token):
    user = make_user()
    token_row = make_email_verification_token(
        user, expires_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
    )
    with pytest.raises(ValidationError):
        auth_service.consume_email_verification_token(token_row.token)


def test_consume_email_verification_token_already_used_raises(db_session, make_user, make_email_verification_token):
    user = make_user()
    token_row = make_email_verification_token(user, used=True)
    with pytest.raises(ValidationError):
        auth_service.consume_email_verification_token(token_row.token)


# ============================================================================
# refresh tokens
# ============================================================================

def test_issue_refresh_token_hashes_and_never_stores_raw(db_session, make_user):
    user = make_user()
    raw_token = auth_service.issue_refresh_token(user)
    db_session.commit()

    expected_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    row = RefreshToken.query.filter_by(token_hash=expected_hash).first()
    assert row is not None
    assert row.token_hash != raw_token


def test_issue_refresh_token_no_family_id_generates_new_family(db_session, make_user):
    user = make_user()
    raw_token = auth_service.issue_refresh_token(user)
    db_session.commit()
    row = RefreshToken.query.filter_by(
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest()
    ).first()
    assert row.family_id  # non-empty


def test_issue_refresh_token_reuses_given_family_id(db_session, make_user):
    user = make_user()
    raw_token = auth_service.issue_refresh_token(user, family_id="my-family-123")
    db_session.commit()
    row = RefreshToken.query.filter_by(
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest()
    ).first()
    assert row.family_id == "my-family-123"


def test_issue_refresh_token_expires_in_7_days(db_session, make_user):
    user = make_user()
    now = datetime.datetime(2026, 1, 1, 12, 0, 0)
    with freeze_time(now):
        raw_token = auth_service.issue_refresh_token(user)
    db_session.commit()
    row = RefreshToken.query.filter_by(
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest()
    ).first()
    assert row.expires_at == now + datetime.timedelta(days=7)


def test_rotate_refresh_token_normal_rotation(db_session, make_user):
    user = make_user()
    raw_token = auth_service.issue_refresh_token(user)
    db_session.commit()
    old_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    old_row = RefreshToken.query.filter_by(token_hash=old_hash).first()

    returned_user, new_raw_token = auth_service.rotate_refresh_token(raw_token)

    assert returned_user.id == user.id
    assert new_raw_token is not None

    old_row_fresh = RefreshToken.query.get(old_row.id)
    assert old_row_fresh.revoked is True
    assert old_row_fresh.revoked_at is not None

    new_hash = hashlib.sha256(new_raw_token.encode()).hexdigest()
    new_row = RefreshToken.query.filter_by(token_hash=new_hash).first()
    assert new_row is not None
    assert new_row.family_id == old_row_fresh.family_id
    assert old_row_fresh.replaced_by_id == new_row.id


def test_rotate_refresh_token_unknown_token_raises(db_session):
    with pytest.raises(ValidationError):
        auth_service.rotate_refresh_token("totally-unknown-token")


def test_rotate_refresh_token_expired_not_revoked_raises(db_session, make_user, make_refresh_token):
    user = make_user()
    raw = "expired-raw-token"
    token_row = make_refresh_token(
        user,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=datetime.datetime.utcnow() - datetime.timedelta(days=1),
    )
    with pytest.raises(ValidationError):
        auth_service.rotate_refresh_token(raw)


def test_rotate_refresh_token_reuse_outside_grace_window_revokes_whole_family(
    db_session, make_user, make_refresh_token
):
    """The core security property this function exists for: a revoked
    token presented again, outside the 10s grace window, revokes EVERY
    non-revoked token in the family -- not just the one presented."""
    user = make_user()
    family_id = "family-under-attack"

    raw_a = "raw-token-a"
    hash_a = hashlib.sha256(raw_a.encode()).hexdigest()
    with freeze_time("2026-01-01 12:00:00"):
        token_a = make_refresh_token(user, token_hash=hash_a, family_id=family_id)

    # A second, still-valid token in the same family (simulates another
    # active session descended from the same login).
    raw_b = "raw-token-b"
    hash_b = hashlib.sha256(raw_b.encode()).hexdigest()
    token_b = make_refresh_token(user, token_hash=hash_b, family_id=family_id)

    # Revoke token_a as if it had already been rotated away, long enough
    # ago that we're outside the 10s grace window.
    with freeze_time("2026-01-01 12:00:00"):
        token_a.revoked = True
        token_a.revoked_at = datetime.datetime(2026, 1, 1, 12, 0, 0)
        db_session.commit()

    with freeze_time("2026-01-01 12:00:30"):  # 30s later -- outside the 10s grace window
        with pytest.raises(ValidationError):
            auth_service.rotate_refresh_token(raw_a)

    # Every non-revoked token in the family must now be revoked, not just token_a.
    token_b_fresh = RefreshToken.query.get(token_b.id)
    assert token_b_fresh.revoked is True


def test_rotate_refresh_token_reuse_within_grace_window_with_valid_successor(
    db_session, make_user, make_refresh_token
):
    """Legitimate multi-tab race: Tab A rotated the token, Tab B presents
    the now-superseded token moments later. Must NOT raise, must NOT
    revoke the family, must return (user, None)."""
    user = make_user()
    family_id = "family-multi-tab"

    raw_old = "raw-old"
    hash_old = hashlib.sha256(raw_old.encode()).hexdigest()

    raw_new = "raw-new-successor"
    hash_new = hashlib.sha256(raw_new.encode()).hexdigest()

    with freeze_time("2026-01-01 12:00:00"):
        new_row = make_refresh_token(user, token_hash=hash_new, family_id=family_id)
        old_row = make_refresh_token(
            user, token_hash=hash_old, family_id=family_id,
            revoked=True, revoked_at=datetime.datetime(2026, 1, 1, 12, 0, 0),
            replaced_by_id=new_row.id,
        )

    with freeze_time("2026-01-01 12:00:05"):  # 5s later -- within the 10s grace window
        returned_user, new_raw_token = auth_service.rotate_refresh_token(raw_old)

    assert returned_user.id == user.id
    assert new_raw_token is None  # signals the route layer: do NOT overwrite the cookie

    # Family must NOT have been revoked by this grace-window path.
    new_row_fresh = RefreshToken.query.get(new_row.id)
    assert new_row_fresh.revoked is False


def test_rotate_refresh_token_reuse_within_grace_window_but_invalid_successor_revokes_family(
    db_session, make_user, make_refresh_token
):
    """If the successor itself is no longer valid (e.g. also revoked or
    expired), the grace-window exception must NOT apply -- falls through
    to full family revocation."""
    user = make_user()
    family_id = "family-bad-successor"

    raw_old = "raw-old-2"
    hash_old = hashlib.sha256(raw_old.encode()).hexdigest()
    raw_new = "raw-new-2"
    hash_new = hashlib.sha256(raw_new.encode()).hexdigest()

    with freeze_time("2026-01-01 12:00:00"):
        new_row = make_refresh_token(
            user, token_hash=hash_new, family_id=family_id, revoked=True,
            revoked_at=datetime.datetime(2026, 1, 1, 12, 0, 0),
        )
        old_row = make_refresh_token(
            user, token_hash=hash_old, family_id=family_id,
            revoked=True, revoked_at=datetime.datetime(2026, 1, 1, 12, 0, 0),
            replaced_by_id=new_row.id,
        )

    with freeze_time("2026-01-01 12:00:05"):  # within 10s window, but successor is revoked
        with pytest.raises(ValidationError):
            auth_service.rotate_refresh_token(raw_old)


def test_revoke_refresh_token_family_revokes_every_token(db_session, make_user, make_refresh_token):
    user = make_user()
    family_id = "family-to-logout"
    raw_a = "logout-raw-a"
    raw_b = "logout-raw-b"
    token_a = make_refresh_token(user, token_hash=hashlib.sha256(raw_a.encode()).hexdigest(), family_id=family_id)
    token_b = make_refresh_token(user, token_hash=hashlib.sha256(raw_b.encode()).hexdigest(), family_id=family_id)

    auth_service.revoke_refresh_token_family(raw_a)

    assert RefreshToken.query.get(token_a.id).revoked is True
    assert RefreshToken.query.get(token_b.id).revoked is True


def test_revoke_refresh_token_family_unknown_token_noops(db_session):
    # Must not raise -- logout must always succeed even with a stale cookie.
    auth_service.revoke_refresh_token_family("unknown-token-value")


def test_revoke_refresh_token_family_empty_token_noops(db_session):
    auth_service.revoke_refresh_token_family("")
    auth_service.revoke_refresh_token_family(None)
