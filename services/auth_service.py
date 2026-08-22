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

from models import User, StudentProfile, UserActivity
from extensions import db

logger = logging.getLogger(__name__)


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
    Hash `password` and write it to both User.pin and StudentProfile.pin
    (the mirrored password-storage columns — see Document 1 §3.3's planned
    pin -> password_hash rename, not yet applied).

    Raises LookupError if no verified user exists for `email`. Does not
    commit — caller (complete_registration / set_password routes) commits
    alongside whatever else changes in that request (username, status,
    etc.).
    """
    user = User.query.filter_by(email=email, email_verified=True).first()
    if not user:
        raise LookupError("User not found or email not verified")

    hashed_password = generate_password_hash(password)
    user.pin = hashed_password

    student_profile = StudentProfile.query.filter_by(user_id=user.id).first()
    if student_profile:
        student_profile.pin = hashed_password

    return user


# ============================================================================
# PASSWORD RESET TOKENS  (Document 3 §4)
#
# PasswordResetToken now used exclusively for the password-reset flow.
# Unlike the stateless JWT approach still used for email verification,
# these are opaque random tokens backed by a DB row, so they're
# individually revocable and single-use. Expiry confirmed at 1 hour
# (shortened from the JWT's 5h — this token is now revocable/single-use,
# so a shorter window is reasonable additional hardening).
# ============================================================================

def issue_password_reset_token(user: "User") -> str:
    """
    Generate an opaque, unguessable password-reset token (NOT a JWT — no
    need for it to be independently verifiable, since it's looked up by
    exact match against the stored row) and persist it.

    Does not commit — caller (the /validate-user route) commits alongside
    whatever else it does in that request, consistent with every other
    service function in this module.

    Returns the raw token string — this, not a JWT, is what gets emailed.
    """
    from models import PasswordResetToken

    raw_token = secrets.token_urlsafe(32)

    reset_row = PasswordResetToken(
        user_id=user.id,
        token=raw_token,
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1),
    )
    db.session.add(reset_row)

    return raw_token


def consume_password_reset_token(raw_token: str) -> "User":
    """
    Look up `raw_token`, validate it via PasswordResetToken.is_valid()
    (not used and not expired), mark it used, and return the associated
    User.

    Raises ValidationError if the token doesn't exist, is expired, or has
    already been used.

    This is one of the rare service functions that DOES commit
    immediately upon successfully marking the token used — documented as
    the second named exception to the Document 2 §5 no-commit convention
    (the first being badge_service.check_and_award_badge's commit=True
    parameter). The reasoning (Document 3 §4): "mark this token used"
    must be atomic with "the password was actually changed" — if the
    route layer's own commit were the only thing finalizing this, a crash
    between marking-used and committing could leave a window where the
    token is reusable. Marking-used commits here, independent of whatever
    the route does afterward with the new password.
    """
    from models import PasswordResetToken
    from errors import ValidationError

    if not raw_token:
        raise ValidationError("Reset token is required")

    reset_row = PasswordResetToken.query.filter_by(token=raw_token).first()

    if not reset_row or not reset_row.is_valid():
        raise ValidationError("This password reset link is invalid or has expired.")

    reset_row.used = True
    reset_row.used_at = datetime.datetime.utcnow()
    db.session.commit()

    user = User.query.get(reset_row.user_id)
    if not user:
        raise ValidationError("User not found")

    return user


# ============================================================================
# EMAIL VERIFICATION TOKENS  (Auth-flow-audit fix, Finding #3)
#
# Same opaque/single-use/DB-backed pattern as the PasswordResetToken pair
# above, applied to email verification. Replaces the stateless JWT
# (utils.generate_verification_token / helpers.verify_token) previously
# used for this flow, which stayed valid for its full 5-hour life
# regardless of use. Expiry kept at 5 hours to match the previous JWT's
# window — no behavior change to how long a verification link lasts,
# only to whether it can be reused.
# ============================================================================

def issue_email_verification_token(user: "User") -> str:
    """
    Generate an opaque, unguessable email-verification token and persist
    it. Does not commit — caller commits alongside whatever else it does
    in that request (matches issue_password_reset_token's convention).

    Returns the raw token string — this is what gets emailed.
    """
    from models import EmailVerificationToken

    raw_token = secrets.token_urlsafe(32)

    token_row = EmailVerificationToken(
        user_id=user.id,
        token=raw_token,
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=5),
    )
    db.session.add(token_row)

    return raw_token


def consume_email_verification_token(raw_token: str) -> "User":
    """
    Look up `raw_token`, validate it via EmailVerificationToken.is_valid()
    (not used and not expired), mark it used, and return the associated
    User.

    Raises ValidationError if the token doesn't exist, is expired, or has
    already been used.

    Commits immediately upon marking the token used, for the identical
    reason consume_password_reset_token does: "mark this token used" must
    be atomic with "the email was actually verified" so a leaked/replayed
    link can't slip through a window between the two.
    """
    from models import EmailVerificationToken
    from errors import ValidationError

    if not raw_token:
        raise ValidationError("Verification token is required")

    token_row = EmailVerificationToken.query.filter_by(token=raw_token).first()

    if not token_row or not token_row.is_valid():
        raise ValidationError("This verification link is invalid or has expired.")

    token_row.used = True
    token_row.used_at = datetime.datetime.utcnow()
    db.session.commit()

    user = User.query.get(token_row.user_id)
    if not user:
        raise ValidationError("User not found")

    return user


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


def rotate_refresh_token(raw_token: str) -> tuple["User", "str | None"]:
    """
    Validate `raw_token`, and if valid, rotate it: mark the old row
    revoked (linked via replaced_by_id to the new row) and issue a new
    refresh token in the same family.

    Reuse detection: if the token's hash matches a row that is already
    `revoked`, this is a replay of a token that was already rotated away
    (either by a legitimate prior refresh, or — the scenario this
    actually defends against — an attacker replaying a stolen token after
    the legitimate client already rotated past it, or vice versa). Either
    way, a revoked token being presented again is a strong signal the
    token (or one of its descendants) has leaked, so the ENTIRE family is
    revoked immediately, forcing every session descended from that login
    to re-authenticate. This is the standard refresh-token-rotation
    reuse-detection pattern.

    GRACE WINDOW (auth-flow-audit follow-up, caught during frontend/backend
    consistency review of Finding #6): a strict "any reuse of a revoked
    token kills the family" rule breaks a legitimate, common scenario —
    two browser tabs for the same user, both holding the same refresh
    token cookie, whose access tokens happen to expire around the same
    moment. Tab A refreshes first and rotates the token; Tab B, unaware,
    presents the now-superseded token moments later. Without a grace
    window this looks identical to token theft and logs the user out of
    every tab. To distinguish the two: only a token's IMMEDIATE successor
    generation is allowed a short (10s) grace period to still mint an
    access token off the OLD token's identity, WITHOUT re-rotating again
    (Tab B gets a working access token but no new refresh-token cookie
    write — it simply continues using the refresh token Tab A already
    installed, since that's the current valid one for this family).
    Reuse of anything older than one generation back, or reuse of a
    once-revoked token after the grace window has elapsed, is still
    treated as a compromise signal and revokes the family immediately —
    the security property this function exists for is unchanged for any
    actual replay attack, which has no reason to arrive within a ~10s
    window of the legitimate client's own refresh.

    Raises ValidationError if the token is unknown/expired, or if reuse
    was detected outside the grace window (family revoked) — both cases
    the route layer should treat identically: clear cookies, require a
    fresh login.

    Commits immediately (same "must be atomic" reasoning as
    consume_password_reset_token / consume_email_verification_token) —
    revocation and the issuance of the replacement must not be split
    across the route layer's own commit.
    """
    from models import RefreshToken
    from errors import ValidationError

    REUSE_GRACE_SECONDS = 10

    if not raw_token:
        raise ValidationError("Refresh token is required")

    token_hash = _hash_refresh_token(raw_token)
    token_row = RefreshToken.query.filter_by(token_hash=token_hash).first()

    if not token_row:
        raise ValidationError("Invalid refresh token")

    if token_row.revoked:
        within_grace = (
            token_row.revoked_at is not None
            and (datetime.datetime.utcnow() - token_row.revoked_at).total_seconds() <= REUSE_GRACE_SECONDS
        )
        successor = (
            RefreshToken.query.get(token_row.replaced_by_id)
            if token_row.replaced_by_id else None
        )

        if within_grace and successor and successor.is_valid():
            # Legitimate multi-tab race, not a replay: hand back an access
            # token for the same user without minting yet another refresh
            # token — the successor token Tab A already installed remains
            # the one valid refresh token for this family. The caller
            # (refresh_token() route) is expected to NOT overwrite the
            # refresh_token cookie in this branch; see that route.
            user = User.query.get(token_row.user_id)
            if not user:
                raise ValidationError("User not found")
            logger.info(
                f"Refresh token reuse within grace window for user "
                f"{token_row.user_id}, family {token_row.family_id} — "
                f"treated as multi-tab race, not revoking."
            )
            return user, None

        # Outside the grace window (or no valid successor to fall back
        # on) — treat as a compromise signal and kill the whole family.
        logger.warning(
            f"Refresh token reuse detected for user {token_row.user_id}, "
            f"family {token_row.family_id} — revoking family."
        )
        RefreshToken.query.filter_by(family_id=token_row.family_id, revoked=False).update(
            {"revoked": True, "revoked_at": datetime.datetime.utcnow()}
        )
        db.session.commit()
        raise ValidationError("Refresh token expired. Please login again.")

    if datetime.datetime.utcnow() >= token_row.expires_at:
        raise ValidationError("Refresh token expired. Please login again.")

    user = User.query.get(token_row.user_id)
    if not user:
        raise ValidationError("User not found")

    new_raw_token = issue_refresh_token(user, family_id=token_row.family_id)
    db.session.flush()  # get the new row's id for replaced_by_id

    new_row = RefreshToken.query.filter_by(token_hash=_hash_refresh_token(new_raw_token)).first()
    token_row.revoked = True
    token_row.revoked_at = datetime.datetime.utcnow()
    if new_row:
        token_row.replaced_by_id = new_row.id

    db.session.commit()

    return user, new_raw_token


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
