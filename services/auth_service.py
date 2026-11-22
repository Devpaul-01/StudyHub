"""
services/auth_service.py

Login-streak/activity-recording business logic, plus password finalization,
extracted from routes/student/auth.py (Document 1 §5, Document 2 §3.10 /
§5's auth_service.finalize_password design).

Per Document 2 §2's layering rule: no Flask imports, no request/session/g,
no cookie-setting (that's HTTP-layer and stays in auth.py). This module
also doesn't send email — utils.py's send_password_reset/
send_verification_email/generate_verification_token stay where they are;
they're already reasonably isolated and not duplicated anywhere.

Transaction boundary (Document 2 §5): record_activity and
update_login_streak mutate the session but do not commit — the caller
(auth.py's login/oauth-callback routes) commits once, alongside token
generation, in the same transaction. This exactly matches the original
auth.py behavior and docstrings, just relocated.
"""

from __future__ import annotations

import datetime
import logging
import secrets

from werkzeug.security import generate_password_hash
from sqlalchemy.exc import IntegrityError

from models import User, UserActivity
from extensions import db

logger = logging.getLogger(__name__)

# Value stored in User.pin while no password has been chosen yet. It can
# never match a password hash, so password login is impossible until
# finalize_password() replaces it.
PENDING_PIN = "PENDING_VERIFICATION"


# ============================================================================
# DAILY ACTIVITY RECORDING
# ============================================================================

# Maps an activity_type to the specific counter column it should bump, in
# addition to the generic activity_score used by the heatmap.
_ACTIVITY_COUNTER_FIELDS = {
    "post": "posts_created",
    "comment": "comments_created",
    "message": "messages_sent",
    "helpful": "helpful_count",
}


def _get_or_create_today_activity(user_id: int, today: datetime.date | None = None) -> UserActivity:
    """
    Get (or create) the UserActivity row for `user_id` for today's date.

    `today` should be a date derived from UTC — never
    datetime.date.today(), which uses the server's *local* clock and will
    disagree with `last_active` (stored in UTC), causing streaks/activity
    rows to fall on the wrong day near midnight.

    Does not commit — caller is responsible for committing the session.
    """
    if today is None:
        today = datetime.datetime.utcnow().date()
    activity = UserActivity.query.filter_by(user_id=user_id, activity_date=today).first()
    if not activity:
        activity = UserActivity(
            user_id=user_id,
            activity_date=today,
            activity_score=0,
            posts_created=0,
            comments_created=0,
            messages_sent=0,
            helpful_count=0,
        )
        db.session.add(activity)
    return activity


def record_activity(user_id: int, activity_type: str, score: int = 1, today: datetime.date | None = None):
    """
    Record one unit of activity for the analytics/heatmap system.

    `activity_type` examples: "login", "register", "post", "comment",
    "message", "helpful". Unrecognized types only bump activity_score (no
    specific counter column exists for them yet).

    `today` should be the same UTC date used by update_login_streak for
    this request, so the activity row and the streak never disagree about
    which calendar day "today" is.

    Does not commit — caller should commit alongside any other changes in
    the same request.
    """
    try:
        activity = _get_or_create_today_activity(user_id, today=today)
        activity.activity_score += score

        counter_field = _ACTIVITY_COUNTER_FIELDS.get(activity_type)
        if counter_field:
            setattr(activity, counter_field, getattr(activity, counter_field) + 1)

        return activity
    except Exception as e:
        logger.error(f"record_activity error ({activity_type}, user {user_id}): {str(e)}")
        return None


def update_login_streak(user: User, now: datetime.datetime | None = None) -> None:
    """
    Update `user.login_streak` based on consecutive calendar days logged in,
    using `user.last_active` as the timestamp of the previous login.

    Rules:
      - First-ever login            -> streak = 1
      - Same calendar day as before -> streak unchanged
      - Logged in yesterday         -> streak += 1
      - Any bigger gap              -> streak resets to 1

    `now` (if given) is the UTC datetime to treat as "now" — pass this
    through to record_activity()'s `today` argument so the streak and the
    daily activity row always agree on which calendar day it is. Both
    `last_active` and `now` MUST be UTC.

    Mutates `user` in place (login_streak, last_active). Does not commit.
    """
    now = now or datetime.datetime.utcnow()
    today = now.date()
    last_login_date = user.last_active.date() if user.last_active else None

    if last_login_date == today:
        pass
    elif last_login_date == today - datetime.timedelta(days=1):
        user.login_streak = (user.login_streak or 0) + 1
    else:
        user.login_streak = 1

    user.last_active = now


def record_login_and_commit(user: User) -> User:
    """
    Update login streak + record 'login' activity, then commit — recovering
    automatically if a concurrent request (e.g. a double-clicked login
    button) already inserted today's UserActivity row first.

    UserActivity has a UniqueConstraint('user_id', 'activity_date'). The
    get-or-create in _get_or_create_today_activity is a check-then-insert
    with no row lock, so two simultaneous logins for the same user on the
    same day can both decide "no row yet" and both try to insert one. The
    loser's commit fails with IntegrityError; caught here, rolled back, and
    retried once.

    This is one of the few service functions that commits — kept exactly
    as in the original auth.py, since the retry-on-IntegrityError pattern
    needs the commit to be right here to detect the race.
    """
    now = datetime.datetime.utcnow()
    update_login_streak(user, now=now)
    record_activity(user.id, "login", today=now.date())
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        user = User.query.get(user.id)
        update_login_streak(user, now=now)
        record_activity(user.id, "login", today=now.date())
        db.session.commit()
    return user


# ============================================================================
# PASSWORD FINALIZATION  (Document 2 §3.10 / Document 1 §5 auth_service design)
#
# Extracted the shared "decode token -> look up user -> hash password ->
# update mirrored `pin` columns" sequence duplicated between
# complete_registration and set_password in auth.py.
# ============================================================================

def finalize_password(email: str, password: str) -> User:
    """
    Hash `password` and store it in User.pin (the password-hash column; its
    planned rename to password_hash is still open). StudentProfile no longer
    keeps a second copy.

    Raises LookupError if no verified user exists for `email`. Does not
    commit — caller (complete_registration / set_password routes) commits
    alongside whatever else changes in that request (username, status,
    etc.).
    """
    user = User.query.filter_by(email=email, email_verified=True).first()
    if not user:
        raise LookupError("User not found or email not verified")

    user.pin = generate_password_hash(password)
    return user


# ============================================================================
# ONE-TIME TOKENS  (password reset + email verification)
#
# Opaque random tokens, backed by a DB row, single-use and individually
# revocable. Only the SHA-256 hash is stored (audit AUD-14): the raw value
# exists only in the emailed link, so a database leak does not yield usable
# links.
#
# Consuming a token is ONE conditional UPDATE ("... WHERE used = false AND
# expires_at > now"), so two concurrent requests (double click, two
# instances) can never both succeed -- exactly one sees rowcount == 1
# (audit AUD-08). The consume functions do NOT commit: the caller changes
# whatever the token authorises (password, email_verified) in the same
# transaction and commits once, so a failure in between leaves the token
# unspent instead of burned.
# ============================================================================

PASSWORD_RESET_TTL = datetime.timedelta(hours=1)
EMAIL_VERIFICATION_TTL = datetime.timedelta(hours=5)


def hash_one_time_token(raw_token: str) -> str:
    import hashlib
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _issue_one_time_token(model, user: "User", ttl: datetime.timedelta) -> str:
    """Invalidate the user's earlier unused tokens, add a fresh one, return the raw value."""
    now = datetime.datetime.utcnow()
    model.query.filter(
        model.user_id == user.id, model.used.is_(False)
    ).update({"used": True, "used_at": now}, synchronize_session=False)

    raw_token = secrets.token_urlsafe(32)
    db.session.add(model(
        user_id=user.id,
        token_hash=hash_one_time_token(raw_token),
        expires_at=now + ttl,
    ))
    return raw_token


def _peek_one_time_token(model, raw_token: str, message: str):
    """Return the valid, unspent row for ``raw_token`` or raise ValidationError."""
    from errors import ValidationError

    if not raw_token or not isinstance(raw_token, str):
        raise ValidationError(message)
    row = model.query.filter_by(token_hash=hash_one_time_token(raw_token)).first()
    if not row or not row.is_valid():
        raise ValidationError(message)
    return row


def _consume_one_time_token(model, raw_token: str, message: str) -> "User":
    from errors import ValidationError

    if not raw_token or not isinstance(raw_token, str):
        raise ValidationError(message)

    token_hash = hash_one_time_token(raw_token)
    now = datetime.datetime.utcnow()
    spent = model.query.filter(
        model.token_hash == token_hash,
        model.used.is_(False),
        model.expires_at > now,
    ).update({"used": True, "used_at": now}, synchronize_session=False)
    if spent != 1:
        raise ValidationError(message)

    row = model.query.filter_by(token_hash=token_hash).first()
    user = User.query.get(row.user_id)
    if not user:
        raise ValidationError("User not found")
    return user


_RESET_INVALID = "This password reset link is invalid or has expired."
_VERIFY_INVALID = "This verification link is invalid or has expired."


def issue_password_reset_token(user: "User") -> str:
    """Create a reset token (1 hour), invalidating earlier ones. Does not commit."""
    from models import PasswordResetToken
    return _issue_one_time_token(PasswordResetToken, user, PASSWORD_RESET_TTL)


def peek_password_reset_token(raw_token: str) -> "User":
    """Read-only validity check (opening/previewing the link must not spend it)."""
    from models import PasswordResetToken
    row = _peek_one_time_token(PasswordResetToken, raw_token, _RESET_INVALID)
    user = User.query.get(row.user_id)
    if not user:
        from errors import ValidationError
        raise ValidationError("User not found")
    return user


def consume_password_reset_token(raw_token: str) -> "User":
    """Atomically spend a reset token. Does NOT commit -- see module section note."""
    from models import PasswordResetToken
    return _consume_one_time_token(PasswordResetToken, raw_token, _RESET_INVALID)


def issue_email_verification_token(user: "User") -> str:
    """Create an email-verification token (5 hours), invalidating earlier ones. Does not commit."""
    from models import EmailVerificationToken
    return _issue_one_time_token(EmailVerificationToken, user, EMAIL_VERIFICATION_TTL)


def consume_email_verification_token(raw_token: str) -> "User":
    """Atomically spend a verification token. Does NOT commit."""
    from models import EmailVerificationToken
    return _consume_one_time_token(EmailVerificationToken, raw_token, _VERIFY_INVALID)


# ============================================================================
# REFRESH TOKENS  (Auth-flow-audit fix, Finding #6)
#
# Rotation-on-use + reuse detection for the long-lived refresh token,
# replacing the previous stateless-JWT-reissued-unchanged design (see
# RefreshToken model docstring in models.py for the full rationale).
#
# Only the SHA-256 hash of the raw token is ever stored/queried — the raw
# value is generated here, returned to the caller (route layer puts it in
# the httponly cookie), and never persisted or logged.
# ============================================================================

def _hash_refresh_token(raw_token: str) -> str:
    import hashlib
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_refresh_token(user: "User", family_id: str | None = None) -> str:
    """
    Generate and persist a new opaque refresh token for `user`.

    `family_id` is None for a brand-new login (a fresh family is started);
    pass the previous token's family_id when rotating an existing session
    so the whole chain stays linked for reuse-detection revocation.

    Does not commit — caller commits alongside whatever else it does in
    that request (matches every other token-issuance function in this
    module).

    Returns the raw token string — this is what goes in the refresh_token
    cookie. Never stored or logged in raw form.
    """
    from models import RefreshToken

    raw_token = secrets.token_urlsafe(48)
    resolved_family_id = family_id or secrets.token_urlsafe(16)

    token_row = RefreshToken(
        user_id=user.id,
        token_hash=_hash_refresh_token(raw_token),
        family_id=resolved_family_id,
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(days=7),
    )
    db.session.add(token_row)

    return raw_token


REFRESH_REUSE_GRACE_SECONDS = 10


def account_may_hold_session(user: "User") -> bool:
    """
    May this account keep a refresh-able session?

    Approved accounts, plus users who are still registering but have a
    verified email (so a 30 minute access token does not strand them
    half-way through onboarding). The route layer decides what such users
    may *call* (see role_required / token_required_allow_pending).
    """
    if user.status == "approved":
        return True
    return user.status in ("pending_onboarding", "pending_verification") and bool(user.email_verified)


def _handle_revoked_refresh_token(token_row: "RefreshToken"):
    """
    A revoked token was presented. Inside the grace window and with a still-
    valid successor this is the multi-tab race (both tabs refresh at once)
    and we hand back the user without minting another token. Anything else
    is treated as theft: the whole family is revoked.
    """
    from models import RefreshToken
    from errors import ValidationError

    within_grace = (
        token_row.revoked_at is not None
        and (datetime.datetime.utcnow() - token_row.revoked_at).total_seconds() <= REFRESH_REUSE_GRACE_SECONDS
    )
    successor = RefreshToken.query.get(token_row.replaced_by_id) if token_row.replaced_by_id else None

    if within_grace and successor and successor.is_valid():
        user = User.query.get(token_row.user_id)
        if not user:
            raise ValidationError("User not found")
        logger.info(
            "Refresh token reuse within grace window for user %s, family %s - multi-tab race, not revoking.",
            token_row.user_id, token_row.family_id,
        )
        return user, None

    logger.warning(
        "Refresh token reuse detected for user %s, family %s - revoking family.",
        token_row.user_id, token_row.family_id,
    )
    RefreshToken.query.filter_by(family_id=token_row.family_id, revoked=False).update(
        {"revoked": True, "revoked_at": datetime.datetime.utcnow()}
    )
    db.session.commit()
    raise ValidationError("Refresh token expired. Please login again.")


def rotate_refresh_token(raw_token: str) -> tuple["User", "str | None"]:
    """
    Validate ``raw_token`` and rotate it: revoke the old row (linked through
    replaced_by_id) and issue a successor in the same family.

    * Rotation is a compare-and-set ("UPDATE ... WHERE revoked = false"), so
      two concurrent requests presenting the same token cannot both rotate
      it; the loser is handled like any other reuse (multi-tab grace or
      family revocation) -- audit AUD-08.
    * The account status is checked BEFORE rotating, so a rejected account
      does not consume (and lose) its refresh token -- audit AUD-21.
    * Returns ``(user, new_raw_token)``; ``new_raw_token`` is None in the
      multi-tab grace case (the caller must not overwrite the cookie).

    Raises ValidationError for unknown/expired tokens, reuse outside the
    grace window (family revoked) and ineligible accounts. Commits.
    """
    from models import RefreshToken
    from errors import ValidationError

    if not raw_token:
        raise ValidationError("Refresh token is required")

    token_row = RefreshToken.query.filter_by(token_hash=_hash_refresh_token(raw_token)).first()
    if not token_row:
        raise ValidationError("Invalid refresh token")

    if token_row.revoked:
        return _handle_revoked_refresh_token(token_row)

    now = datetime.datetime.utcnow()
    if now >= token_row.expires_at:
        raise ValidationError("Refresh token expired. Please login again.")

    user = User.query.get(token_row.user_id)
    if not user:
        raise ValidationError("User not found")
    if not account_may_hold_session(user):
        raise ValidationError("Account not active")

    new_raw_token = issue_refresh_token(user, family_id=token_row.family_id)
    db.session.flush()
    new_row = RefreshToken.query.filter_by(token_hash=_hash_refresh_token(new_raw_token)).first()

    claimed = RefreshToken.query.filter(
        RefreshToken.id == token_row.id, RefreshToken.revoked.is_(False)
    ).update(
        {"revoked": True, "revoked_at": now, "replaced_by_id": new_row.id},
        synchronize_session=False,
    )
    if claimed != 1:
        # Somebody else rotated this token between our read and our write.
        db.session.delete(new_row)
        db.session.flush()
        db.session.refresh(token_row)
        return _handle_revoked_refresh_token(token_row)

    db.session.commit()
    return user, new_raw_token


def invalidate_password_reset_tokens(user_id: int) -> None:
    """Mark every unused reset token of a user as used (after a successful reset). Does not commit."""
    from models import PasswordResetToken
    PasswordResetToken.query.filter(
        PasswordResetToken.user_id == user_id, PasswordResetToken.used.is_(False)
    ).update({"used": True, "used_at": datetime.datetime.utcnow()}, synchronize_session=False)


def revoke_all_refresh_tokens(user_id: int) -> int:
    """Revoke every live refresh token of a user (password reset). Does not commit."""
    from models import RefreshToken
    return RefreshToken.query.filter(
        RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False)
    ).update(
        {"revoked": True, "revoked_at": datetime.datetime.utcnow()},
        synchronize_session=False,
    )


def revoke_refresh_token_family(raw_token: str) -> None:
    """
    Revoke every token in the family that `raw_token` belongs to (used on
    logout — ends this session's whole rotation chain, so a copy of an
    older, already-superseded token from this same session can't be used
    to mint further access tokens after logout).

    Silently no-ops if the token is unknown — logout must always succeed
    from the caller's perspective even if the cookie was already stale.
    Commits immediately, matching the other revoking functions here.
    """
    from models import RefreshToken

    if not raw_token:
        return

    token_hash = _hash_refresh_token(raw_token)
    token_row = RefreshToken.query.filter_by(token_hash=token_hash).first()
    if not token_row:
        return

    RefreshToken.query.filter_by(family_id=token_row.family_id, revoked=False).update(
        {"revoked": True, "revoked_at": datetime.datetime.utcnow()}
    )
    db.session.commit()
