"""purge_stale_auth_data_job: abandoned sign-ups and expired tokens (audit AUD-06/AUD-20)."""
import datetime

import pytest

from models import (EmailVerificationToken, Notification, PasswordResetToken, RefreshToken,
                    StudentProfile, User)
from services import auth_service
from services.jobs.maintenance_jobs import purge_stale_auth_data_job

pytestmark = pytest.mark.integration

SENTINEL = "PENDING_VERIFICATION"
DAY = datetime.timedelta(days=1)


def _signup(make_user, make_student_profile, email, age_days, **overrides):
    fields = dict(email=email, username=None, pin=SENTINEL, status="pending_verification",
                  email_verified=False, google_id=None,
                  joined_at=datetime.datetime.utcnow() - age_days * DAY)
    fields.update(overrides)
    user = make_user(**fields)
    make_student_profile(user)
    return user


def test_old_unverified_signup_is_deleted_with_its_rows(db_session, make_user, make_student_profile):
    user = _signup(make_user, make_student_profile, "old@example.com", 8)
    auth_service.issue_email_verification_token(user)
    db_session.add(Notification(user_id=user.id, notification_type="welcome", title="hi", body="hi"))
    db_session.commit()
    uid = user.id

    result = purge_stale_auth_data_job()

    assert result["accounts_deleted"] == 1
    assert User.query.get(uid) is None
    assert StudentProfile.query.filter_by(user_id=uid).count() == 0
    assert EmailVerificationToken.query.filter_by(user_id=uid).count() == 0
    assert Notification.query.filter_by(user_id=uid).count() == 0


def test_recent_signup_is_kept(db_session, make_user, make_student_profile):
    user = _signup(make_user, make_student_profile, "new@example.com", 6)
    db_session.commit()
    assert purge_stale_auth_data_job()["accounts_deleted"] == 0
    assert User.query.get(user.id) is not None


@pytest.mark.parametrize("overrides", [
    {"email_verified": True},                      # verified: the owner proved the address
    {"status": "pending_onboarding"},              # Google-style pending account
    {"status": "approved"},
    {"google_id": "google-sub"},                   # linked to Google
])
def test_other_accounts_are_never_deleted(db_session, make_user, make_student_profile, overrides):
    user = _signup(make_user, make_student_profile, "keep@example.com", 30, **overrides)
    db_session.commit()
    purge_stale_auth_data_job()
    assert User.query.get(user.id) is not None


def test_address_is_free_again_after_the_purge(client, db_session, make_user, make_student_profile, monkeypatch):
    from services.job_queue import email_queue
    monkeypatch.setattr(email_queue, "enqueue", lambda *a, **k: type("J", (), {"id": 1})())
    _signup(make_user, make_student_profile, "again@example.com", 10)
    db_session.commit()
    purge_stale_auth_data_job()
    assert client.post("/student/register", json={"full_name": "Real Owner", "email": "again@example.com"}).status_code == 200
    assert User.query.filter_by(email="again@example.com").one().name == "Real Owner"


def test_expired_tokens_are_deleted_after_the_retention_period(db_session, make_user,
                                                               make_password_reset_token,
                                                               make_email_verification_token,
                                                               make_refresh_token):
    user = make_user()
    old = datetime.datetime.utcnow() - 31 * DAY
    recent = datetime.datetime.utcnow() - 2 * DAY
    make_password_reset_token(user, expires_at=old)
    keep_reset = make_password_reset_token(user, expires_at=recent)
    make_email_verification_token(user, expires_at=old)
    old_refresh = make_refresh_token(user, expires_at=old)
    keep_refresh = make_refresh_token(user, expires_at=datetime.datetime.utcnow() + DAY)
    db_session.commit()
    keep_reset_id, keep_refresh_id, old_refresh_id = keep_reset.id, keep_refresh.id, old_refresh.id

    result = purge_stale_auth_data_job()

    assert result["tokens_deleted"] == 3
    assert [r.id for r in PasswordResetToken.query.all()] == [keep_reset_id]
    assert EmailVerificationToken.query.count() == 0
    assert {r.id for r in RefreshToken.query.all()} == {keep_refresh_id}
    assert RefreshToken.query.get(old_refresh_id) is None


def test_job_is_idempotent(db_session, make_user, make_student_profile):
    _signup(make_user, make_student_profile, "idem@example.com", 9)
    db_session.commit()
    assert purge_stale_auth_data_job()["accounts_deleted"] == 1
    assert purge_stale_auth_data_job() == {"accounts_deleted": 0, "accounts_skipped": 0, "tokens_deleted": 0}


def test_scheduler_wrapper_enqueues_the_job(app, monkeypatch):
    import scheduler
    from services import job_queue
    enqueued = []

    class _Job:
        id = "job-1"

    monkeypatch.setattr(scheduler, "_run_locked", lambda job_id, work: (enqueued.append(job_id), work()))
    monkeypatch.setattr(job_queue.maintenance_queue, "enqueue",
                        lambda fn, **kw: enqueued.append(fn.__name__) or _Job())

    scheduler._job_auth_cleanup(app)

    assert enqueued == ["auth_cleanup", "purge_stale_auth_data_job"]
