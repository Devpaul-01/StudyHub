# routes/student/auth.py

from flask import Blueprint, request, jsonify, redirect, url_for, current_app, make_response, render_template, session
from werkzeug.security import check_password_hash, generate_password_hash
import secrets
import time
import re
from urllib.parse import quote
from flask_dance.contrib.google import make_google_blueprint, google
from flask_dance.consumer import oauth_authorized
from sqlalchemy import or_, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
import jwt
import datetime
import os

from models import User, StudentProfile, Notification, OnboardingDetails, Connection, UserActivity
from extensions import db
# Token issuance/verification (generate_tokens_for_user, decode_token)
# lives exclusively in .helpers now — utils.py no longer duplicates these, so
# everything JWT-related is imported from a single source of truth here.
from utils import send_password_reset, send_verification_email
from .helpers import (
    generate_tokens_for_user, decode_token, token_required,
    token_required_allow_pending, PENDING_STATUSES,
    success_response, error_response, set_auth_cookies, clear_auth_cookies,
    _build_access_token, public_url,
)
# Phase 5b (Document 4 §1): SENSITIVE_AUTH-tier rate limiting on every
# pre-auth route in this file (login, register, password-reset trigger,
# complete-registration) — the specific gap Document 03 §C flagged as
# unthrottled brute-force/email-enumeration risk. ip_key() since these are
# all pre-authentication (no user identity yet to key on).
from services.rate_limit_service import limiter, RateLimitTier, ip_key, allow_action, reset_action
# Activity/streak recording and password finalization now live in
# services/auth_service.py (Document 2 §3.10); notification construction
# goes through services/notification_service.py (Document 2 §3.9).
from services import auth_service, notification_service, onboarding_service
from services.activity_service import record_live_activity
from errors import ValidationError

auth_bp = Blueprint("student_auth", __name__)

# ============================================================================
# CONSTANTS
# ============================================================================
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID")

_resolved_client_id     = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or CLIENT_ID
_resolved_client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or CLIENT_SECRET



google_bp = make_google_blueprint(
    client_id=_resolved_client_id,
    client_secret=_resolved_client_secret,
    scope=[
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ],
    redirect_to="google.google_callback",
)

# ============================================================================
# DEFAULT SETTINGS
# Must be defined BEFORE any route or function that references them.
# ============================================================================
notification_settings = {
    "enable_notification_sound": True,
    "enable_notification": True,
}

connection_settings = {
    "enable_sound": True,
}

privacy_settings = {
    "set_profile_private": False,
    "show_active_status": True,
    "set_dark_mode": False,
    "send_weekly_notification": True,   # FIX: was "send_weeekly_notifications" (typo + wrong key)
    "show_in_activity_feed": True,      # opt-out for the Live Activity feed (services/activity_service.py)
}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
def get_json_data():
    """
    Return the parsed JSON body, or None.

    Only requests that declare a JSON content type are parsed. Accepting
    other content types (for example text/plain) would let a cross-site
    HTML form send a body without a CORS preflight, which is the protection
    the CSRF-exempt pre-auth routes rely on (audit AUD-02).
    """
    if not request.is_json:
        return None
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def is_valid_email(email):
    """Returns True if the email address looks valid."""
    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    return re.match(pattern, email) is not None


def _normalize_email(email):
    return (email or "").strip().lower()


def _user_from_access_cookie():
    """Return the User identified by a valid access_token cookie, else None."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = decode_token(token)
    except Exception:
        return None
    return User.query.get(payload.get("user_id"))


# How long the Google sign-in proof stays valid. The Flask session is a signed
# client-side cookie, so it cannot be revoked server-side; a short lifetime
# limits how long a copied cookie is useful (audit AUD-10).
GOOGLE_PROOF_TTL_SECONDS = 30 * 60


def _set_google_proof(email, name, google_sub):
    session["google_email"] = email
    session["google_name"] = name
    session["google_id"] = google_sub
    session["google_proof_at"] = int(time.time())


def _google_session_email():
    """The Google-verified email in the session, if the proof is still fresh."""
    email = _normalize_email(session.get("google_email"))
    issued = session.get("google_proof_at")
    if not email or not isinstance(issued, int):
        return None
    if time.time() - issued > GOOGLE_PROOF_TTL_SECONDS:
        return None
    return email


def _is_request_authorized_for_email(email):
    """
    Return True when the current request is allowed to act on behalf of `email`.

    Two accepted proofs:
      1. A fresh Google OAuth session for this email (new Google user
         mid-onboarding, before they hold a JWT).
      2. A valid JWT access-token cookie belonging to the user with this email.

    Emails are compared case-insensitively.
    """
    email = _normalize_email(email)
    if not email:
        return False

    if _google_session_email() == email:
        return True

    user = _user_from_access_cookie()
    return bool(user and _normalize_email(user.email) == email)


def _clear_google_oauth_session():
    """
    Auth-flow-audit fix (Finding #5): clears the transient Google-OAuth
    session state (google_email/google_name/google_id).

    Previously this only happened when the frontend explicitly called
    POST /clear-session, which not every successful-auth code path
    triggers. That left a window on shared/public devices where a stale
    session["google_email"] from an abandoned OAuth attempt (or a prior
    user's session) remained a valid authorization proof in
    _is_request_authorized_for_email() for onboarding/complete-registration
    routes, for an unrelated email, from a different browser session.

    Call this from every route that represents a completed, successful
    authentication event server-side — do not rely solely on the frontend
    remembering to call /clear-session.
    """
    session.pop("google_email", None)
    session.pop("google_name", None)
    session.pop("google_id", None)
    session.pop("google_proof_at", None)


# record_activity/update_login_streak/_get_or_create_today_activity/
# _record_login_and_commit now live in services/auth_service.py
# (Document 2 §3.10). auth.py's routes call auth_service.record_activity(...)
# and auth_service.record_login_and_commit(...) directly.


# ============================================================================
# GOOGLE OAUTH
# ============================================================================
@auth_bp.route("users/me", methods=["GET"])
@token_required_allow_pending
def current_user(current_user):
    existing_user = User.query.get(current_user.id)

    if not existing_user:
        return jsonify({"status": "error", "message": "User not found"}), 404

    return jsonify({
        "status": "success",
        "data": {
            "user": {
                "id":               existing_user.id,
                "username":         existing_user.username,
                "email":            existing_user.email,
                "name":             existing_user.name,
                "avatar":           existing_user.avatar,
                "bio":              existing_user.bio,
                "reputation":       existing_user.reputation,
                "reputation_level": existing_user.reputation_level,
                "role":             existing_user.role,
                "status":           existing_user.status,
                "email_verified":   existing_user.email_verified,
                "joined_at":        existing_user.joined_at.isoformat() if existing_user.joined_at else None,
                "last_active":      existing_user.last_active.isoformat() if existing_user.last_active else None,
                "login_streak":     existing_user.login_streak,
                "total_posts":      existing_user.total_posts,
                "total_helpful":    existing_user.total_helpful,
                "in_study_session": existing_user.in_study_session,
            }
        },
    })


@google_bp.route("/start")
def google_start():
    """Redirect to Google OAuth."""
    return redirect(url_for("google.login"))

@google_bp.route("/callback")
def google_callback():
    """Handle Google OAuth callback.

    Flow:
      1. Approved user    → log in, go to homepage
      2. Needs onboarding → redirect to onboard
      3. Needs complete-registration → redirect to complete-registration
      4. Brand-new user   → create account, go to onboarding
    """
    try:
        if not google.authorized:
            return redirect(url_for("student.student_auth.login") + "?error=oauth_failed")

        resp = google.get("/oauth2/v2/userinfo")
        if not resp.ok:
            return redirect(url_for("student.student_auth.login") + "?error=oauth_failed")

        google_info = resp.json()
        email = google_info.get("email", "").lower().strip()
        name  = google_info.get("name", "")
        # Auth-flow-audit fix (Finding #2, Critical): Google's stable,
        # per-account subject identifier ("id" on the v2 userinfo
        # endpoint — equivalent to the "sub" claim on the OIDC id_token).
        # Unlike email, this can never be reused/reassigned/re-registered
        # elsewhere, so it's the correct anchor for "is this literally the
        # same Google account we saw before" rather than trusting an email
        # string match alone.
        google_sub = google_info.get("id")

        if not email:
            return redirect(url_for("student.student_auth.login") + "?error=oauth_failed")

        # Google can return an address the account holder never verified
        # (accounts may be created with a third-party address). Only a
        # verified address proves ownership of the mailbox (audit AUD-10).
        if google_info.get("verified_email") is not True:
            current_app.logger.warning("Google OAuth: unverified email address - refusing")
            return redirect(url_for("student.student_auth.login") + "?error=email_not_verified")

        # ── 1. Existing user ─────────────────────────────────────────────────
        existing_user = User.query.filter_by(email=email).first()

        if existing_user:
            # Auth-flow-audit fix (Finding #2, Critical): previously, any
            # existing_user row matching this email was logged straight in
            # on Google auth alone — including accounts originally created
            # via password registration, which have no relationship to
            # this Google account at all. That let anyone who could get
            # Google to authenticate a given email (e.g. a shared/former
            # mailbox) log into a StudyHub account they never registered
            # and don't hold the password for, with zero further proof.
            #
            # Fix: only allow Google-initiated login for accounts that
            # were themselves created via Google (google_id already set
            # and matching), or for legacy Google-created accounts that
            # predate this column (google_id is NULL AND the account has
            # no usable password — pin is still the sentinel "PENDING_
            # VERIFICATION"/unset value — meaning password login was never
            # actually possible for it, so backfilling google_id here is
            # safe and not a new grant of access). Any account that DOES
            # have a real password on file is a password-created account
            # and Google auth must not bypass it.
            if existing_user.google_id and google_sub and existing_user.google_id != google_sub:
                # Extremely unlikely (would require Google reassigning a
                # sub, which it doesn't), but if it ever happened this is
                # not a safe auto-login — refuse rather than guess.
                current_app.logger.warning(
                    f"Google OAuth: sub mismatch for {email} — refusing login"
                )
                return redirect(url_for("student.student_auth.login") + "?error=oauth_failed")

            # Determine account origin purely from google_id — NOT from
            # whether a password/pin has been set. complete_registration()
            # (see that route) sets a real password hash on EVERY account
            # regardless of origin, Google-created included, so users who
            # signed up via Google can also log in with a password later.
            # Using pin-state as a proxy for "was this a password
            # registration" would incorrectly flag every Google user who
            # has finished onboarding as a password account and lock them
            # out of Google login going forward.
            #
            # - google_id already set  -> definitely a Google account.
            # - google_id NULL AND pin is still the untouched sentinel
            #   ("PENDING_VERIFICATION") -> a legacy Google-created row
            #   from before this column existed; it has never had a real
            #   password set, so no password-based access exists to
            #   protect, and backfilling google_id now is not a new grant.
            # - google_id NULL AND pin is a real hash -> a password
            #   account. Google auth must not bypass it.
            # A registration that was started with an email link but never
            # finished has no password. Google just proved control of this
            # mailbox, so the account is explicitly claimed for this Google
            # identity and its email counts as verified.
            claimable = (not existing_user.google_id) and existing_user.pin == auth_service.PENDING_PIN
            is_google_account = bool(existing_user.google_id) or claimable

            if not is_google_account:
                # A real password has been set for this email and it was
                # never linked to this Google account — this is a
                # password-created account. Do not log in via Google.
                current_app.logger.warning(
                    f"Google OAuth: {email} already registered via password — refusing Google login"
                )
                return redirect(
                    url_for("student.student_auth.login")
                    + "?error=account_exists_use_password"
                )

            # Backfill google_id for legacy Google-created rows that
            # predate this column, and keep it current otherwise.
            if google_sub and existing_user.google_id != google_sub:
                existing_user.google_id = google_sub
            if claimable and not existing_user.email_verified:
                existing_user.email_verified = True

            # ✅ APPROVED USER → Login
            if existing_user.status == "approved":
                existing_user = auth_service.record_login_and_commit(existing_user)
                access_token, refresh_token_val = generate_tokens_for_user(existing_user)
                response = make_response(redirect("/student/profile/homepage"))
                set_auth_cookies(response, access_token, refresh_token_val)
                current_app.logger.info(f"Google login: user {existing_user.id}")
                return response

            # ✅ STILL REGISTERING → continue where they left off
            if existing_user.status in PENDING_STATUSES:
                db.session.commit()
                # onboard() / complete_registration() accept this fresh
                # Google proof for users who do not hold a JWT yet.
                _set_google_proof(email, existing_user.name, google_sub)
                if existing_user.status == "pending_onboarding":
                    return redirect("/student/onboard/" + quote(email))
                return redirect("/student/complete-registration?email=" + quote(email))

            # Any other status (for example suspended): no session, no proof.
            db.session.commit()
            current_app.logger.info(f"Google login refused: user {existing_user.id} status {existing_user.status}")
            return redirect(url_for("student.student_auth.login") + "?error=account_inactive")

        # ── 2. Brand-new user ─────────────────────────────────────────────────
        name = (name or "").strip()[:100] or email.split("@")[0]
        new_user = User(
            name=name,
            email=email,
            role="student",
            pin=auth_service.PENDING_PIN,
            status="pending_onboarding",
            email_verified=True,
            google_id=google_sub,
            privacy_settings=dict(privacy_settings),
            notification_settings=dict(notification_settings),
            connection_settings=dict(connection_settings),
        )
        db.session.add(new_user)
        db.session.flush()

        student_profile = StudentProfile(
            user_id=new_user.id,
            full_name=name,
            department="",
            class_name="",
        )
        db.session.add(student_profile)

        notification_service.notify_welcome(
            new_user.id, name,
            features_link=url_for("student.student_auth.features"),
            rich=False,
        )
        auth_service.record_activity(new_user.id, "register", score=5)
        db.session.commit()

        _set_google_proof(email, name, google_sub)

        current_app.logger.info("Google signup: new user created, redirecting to onboarding")
        return redirect("/student/onboard/" + quote(email))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Google OAuth error: {str(e)}")
        return redirect(url_for("student.student_auth.login") + "?error=oauth_failed")


@auth_bp.route("/google_temp_info")
def temp_info():
    """Get temporary OAuth info from session."""
    fresh_email = _google_session_email()
    return jsonify({
        "status": "success",
        "email": fresh_email,
        "name":  session.get("google_name") if fresh_email else None,
    })


@auth_bp.route("/clear-session", methods=["POST"])
def clear_session():
    """Clear OAuth session data."""
    _clear_google_oauth_session()
    return jsonify({"status": "success"})


# ============================================================================
# MISC AUTH ROUTES
# ============================================================================
@auth_bp.route("/auth/me", methods=["GET"])
@token_required_allow_pending
def get_current_user(current_user):
    return jsonify({
        "status": "success",
        "data": {
            "user": {
                "id":       current_user.id,
                "name":     current_user.name,
                "username": current_user.username,
                "avatar":   current_user.avatar,
            }
        },
    })


@auth_bp.route("/features", methods=["GET"])
def features():
    return render_template("features.html")


@auth_bp.route("/demo", methods=["GET", "POST"])
def demo():
    return render_template("demo.html")


# ============================================================================
# ONBOARDING
# ============================================================================
@auth_bp.route("/onboard/<email>", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.REGISTRATION, key_func=ip_key, methods=["POST"])
def onboard(email):
    """
    GET renders the onboarding page. POST saves the questionnaire.

    * Callers must prove they own `email` (access cookie or Google session).
    * Onboarding is optional for becoming an approved user; it only enriches
      the profile used for suggestions.
    * Approved users may re-submit to update their preferences. That never
      changes their account status or issues new tokens (audit AUD-05).
    * Users who are still registering move from pending_onboarding to
      pending_verification and receive their session cookies here.
    """
    if request.method == "GET":
        return render_template("onboard.html")

    try:
        if not _is_request_authorized_for_email(email):
            return error_response("Unauthorized", 401)

        cleaned, errors = onboarding_service.validate_onboarding_payload(get_json_data())
        if errors:
            first = next(iter(errors.values()))
            return error_response(first, 400, errors=errors)

        user = User.query.filter_by(email=_normalize_email(email)).first()
        if not user:
            return error_response("User not found", 404)

        if user.status not in ("approved",) + PENDING_STATUSES:
            return error_response("This account cannot be updated right now.", 403)

        student_profile = user.student_profile
        onboarding_details = OnboardingDetails.query.filter_by(user_id=user.id).first()
        if not onboarding_details:
            onboarding_details = OnboardingDetails(user_id=user.id)
            db.session.add(onboarding_details)

        if cleaned["name"]:
            user.name = cleaned["name"]
            if student_profile:
                student_profile.full_name = cleaned["name"]

        # Department / class are mirrored on StudentProfile (used by profile
        # and discovery pages) and OnboardingDetails (used by matching).
        if student_profile:
            student_profile.department = cleaned["department"]
            student_profile.class_name = cleaned["class_level"]

        onboarding_details.department        = cleaned["department"]
        onboarding_details.class_level       = cleaned["class_level"]
        onboarding_details.subjects          = cleaned["subjects"]
        onboarding_details.learning_style    = cleaned["learning_style"]
        onboarding_details.study_preferences = cleaned["study_preferences"]
        onboarding_details.help_subjects     = cleaned["help_subjects"]
        onboarding_details.strong_subjects   = cleaned["strong_subjects"]
        onboarding_details.study_schedule    = cleaned["study_schedule"]
        onboarding_details.session_length    = cleaned["session_length"]
        onboarding_details.last_updated      = datetime.datetime.utcnow()

        user_payload = {
            "id": user.id, "name": user.name,
            "username": user.username, "email": user.email,
        }

        if user.status == "approved":
            db.session.commit()
            return success_response("Preferences updated", data={"user": user_payload})

        if not user.email_verified:
            db.session.rollback()
            return error_response("Please verify your email first.", 403)

        if user.status == "pending_onboarding":
            user.status = "pending_verification"
        db.session.commit()

        access_token, refresh_token = generate_tokens_for_user(user)

        # A real session now exists; the Google proof is no longer needed.
        _clear_google_oauth_session()

        response = make_response(success_response(
            "Onboarding details saved successfully",
            data={
                "user": user_payload,
                "redirect": "/student/complete-registration?email=" + quote(user.email),
            },
        ))
        set_auth_cookies(response, access_token, refresh_token)
        return response

    except Exception:
        db.session.rollback()
        current_app.logger.error("Onboarding error", exc_info=True)
        return error_response("Failed to save onboarding data")


# ============================================================================
# REGISTER
# ============================================================================
GENERIC_SIGNUP_MESSAGE = (
    "Check your email for the next step. If you already have an account, "
    "sign in or reset your password instead."
)


def _send_verification_link(user):
    """
    Issue a fresh verification token (older unused ones are invalidated),
    commit it and queue the email. Raises if the email cannot be queued.
    """
    token = auth_service.issue_email_verification_token(user)
    db.session.commit()
    send_verification_email(
        user.email, public_url("student.student_auth.verify_email_api", token=token)
    )


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.REGISTRATION, key_func=ip_key, methods=["POST"])
def register():
    """
    Start a registration with a name and an email address.

    The response is the same whether or not the address is already known
    (no account enumeration). Signing up again with an address whose
    registration was never finished simply sends a fresh link, which is also
    how a user recovers from a lost or expired email (audit AUD-03/06).
    """
    if request.method == "GET":
        return render_template("auth/register.html")

    try:
        data = get_json_data()
        if data is None:
            return error_response("Invalid JSON data received")

        full_name = data.get("full_name", "")
        email = data.get("email", "")
        if not isinstance(full_name, str) or not isinstance(email, str):
            return error_response("Invalid data")
        full_name = full_name.strip()
        email = _normalize_email(email)

        if not all([full_name, email]):
            return error_response("All fields are required")
        if len(full_name) > 100 or len(email) > 120:
            return error_response("Name or email is too long")
        if not is_valid_email(email):
            return error_response("Invalid email format")

        if not allow_action("register", email, 5, 3600):
            return error_response("Too many attempts for this email. Please try again later.", 429)

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            if existing_user.status in PENDING_STATUSES:
                try:
                    _send_verification_link(existing_user)
                except Exception:
                    db.session.rollback()
                    current_app.logger.error("Could not queue verification email", exc_info=True)
                    return error_response(
                        "We couldn't send the email right now. Please try again in a few minutes.", 503)
            return success_response(GENERIC_SIGNUP_MESSAGE)

        new_user = User(
            name=full_name,
            email=email,
            role="student",
            pin=auth_service.PENDING_PIN,
            status="pending_verification",
            email_verified=False,
            privacy_settings=dict(privacy_settings),
            notification_settings=dict(notification_settings),
            connection_settings=dict(connection_settings),
        )
        db.session.add(new_user)
        db.session.flush()

        db.session.add(StudentProfile(
            user_id=new_user.id,
            full_name=full_name,
            department="",
            class_name="",
        ))

        notification_service.notify_welcome(
            new_user.id, email.split('@')[0],
            features_link=url_for("student.student_auth.features"),
            rich=True,
        )
        auth_service.record_activity(new_user.id, "register", score=5)
        token = auth_service.issue_email_verification_token(new_user)

        # User, profile and token are committed together; only then is the
        # email queued. If queueing fails the account is not stranded: signing
        # up again with the same address sends a new link.
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()  # lost a race for the same email
            return success_response(GENERIC_SIGNUP_MESSAGE)

        try:
            send_verification_email(
                email, public_url("student.student_auth.verify_email_api", token=token))
        except Exception:
            current_app.logger.error("Could not queue verification email", exc_info=True)
            return error_response(
                "We couldn't send the email right now. Please try again in a few minutes.", 503)

        return success_response(GENERIC_SIGNUP_MESSAGE)

    except Exception:
        db.session.rollback()
        current_app.logger.error("Registration error", exc_info=True)
        return error_response("Registration failed. Please try again.")


@auth_bp.route("/resend-verification", methods=["POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def resend_verification():
    """
    Send a new verification/resume link to a registration that was started
    but not finished. Always answers the same way, whether or not the
    address is known.
    """
    try:
        data = get_json_data() or {}
        email = data.get("email", "")
        if not isinstance(email, str) or not is_valid_email(_normalize_email(email)):
            return error_response("Please enter a valid email address")
        email = _normalize_email(email)

        if not allow_action("resend_verification", email, 3, 3600):
            return error_response("Too many requests for this email. Please try again later.", 429)

        user = User.query.filter_by(email=email).first()
        if user and user.status in PENDING_STATUSES:
            try:
                _send_verification_link(user)
            except Exception:
                db.session.rollback()
                current_app.logger.error("Could not queue verification email", exc_info=True)
        return success_response("If that email is waiting to be verified, we've sent a new link.")
    except Exception:
        db.session.rollback()
        current_app.logger.error("Resend verification error", exc_info=True)
        return error_response("Request failed. Please try again.")


# ============================================================================
# LOGIN
# ============================================================================
_dummy_password_hash = None


def _dummy_hash():
    """A real hash to compare against when the account does not exist, so unknown and known accounts take similar time."""
    global _dummy_password_hash
    if _dummy_password_hash is None:
        _dummy_password_hash = generate_password_hash(secrets.token_urlsafe(16))
    return _dummy_password_hash


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.AUTH_ATTEMPT, key_func=ip_key, methods=["POST"])
def login():
    """
    Log in with username/email and password.

    Anything that is not a correct password for an existing account gets the
    same "Invalid credentials" answer (no account or state enumeration, audit
    AUD-11). The state of the account is only described to someone who has
    proven they know its password. Failed attempts are also counted per
    account across all IPs.
    """
    if request.method == "GET":
        return render_template("auth/login.html")

    try:
        data = get_json_data()
        if data is None:
            return error_response("Invalid JSON data received")

        username_or_email = data.get("username_or_email", "")
        password          = data.get("password", "")
        if not isinstance(username_or_email, str) or not isinstance(password, str):
            return error_response("Invalid data")
        username_or_email = username_or_email.strip().lower()

        if not username_or_email or not password:
            return error_response("Username/Email and password required")

        if not allow_action("login", username_or_email, 10, 900):
            return error_response("Too many sign-in attempts. Please wait a few minutes and try again.", 429)

        user = User.query.filter(
            or_(User.username == username_or_email, User.email == username_or_email)
        ).first()

        has_password = bool(user) and user.pin != auth_service.PENDING_PIN
        password_ok = check_password_hash(user.pin if has_password else _dummy_hash(), password)
        if not (user and has_password and password_ok):
            return error_response("Invalid credentials")

        reset_action("login", username_or_email)

        if not user.email_verified:
            return error_response("Please verify your email before logging in.")

        # Verified user who set a password (for example through "forgot
        # password") but never chose a username: sign them in and send them
        # to finish -- they are not locked out of their own registration.
        if user.status in PENDING_STATUSES:
            access_token, refresh_token = generate_tokens_for_user(user)
            response = make_response(success_response(
                "Let's finish setting up your account.",
                data={
                    "user": {"id": user.id, "name": user.name, "username": user.username, "email": user.email},
                    "redirect": "/student/complete-registration?email=" + quote(user.email),
                },
            ))
            set_auth_cookies(response, access_token, refresh_token)
            return response

        if user.status != "approved" or not user.username:
            return error_response("Your account is not active.")

        user = auth_service.record_login_and_commit(user)

        access_token, refresh_token = generate_tokens_for_user(user)
        _clear_google_oauth_session()

        response = make_response(success_response(
            f"Welcome back, @{user.username}!",
            data={
                "user": {
                    "id":       user.id,
                    "name":     user.name,
                    "username": user.username,
                    "email":    user.email,
                },
                "redirect": "/student/profile/homepage",
                "login_streak": user.login_streak,
            },
        ))
        set_auth_cookies(response, access_token, refresh_token)
        return response

    except Exception:
        db.session.rollback()
        current_app.logger.error("Login error", exc_info=True)
        return error_response("Login failed. Please try again.")


# ============================================================================
# PASSWORD RESET / EMAIL VERIFICATION
# ============================================================================
GENERIC_RESET_MESSAGE = "If an account exists for that email or username, a password reset link has been sent."


@auth_bp.route("/validate-user", methods=["POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def validate_user():
    """
    "Forgot password": email a single-use reset link (valid 1 hour).

    The answer is identical for known and unknown accounts. Requests are
    also limited per account, so one address cannot be mail-flooded from many
    IPs. A registration whose email was never verified gets a verification
    link instead (a reset cannot verify an address).
    """
    try:
        data = get_json_data()
        user_input = data.get("data") if data else None
        if not isinstance(user_input, str) or not user_input.strip():
            return error_response("Kindly enter your email or username")
        user_input = user_input.strip().lower()

        if not allow_action("reset_request", user_input, 3, 3600):
            return error_response("Too many requests for this account. Please try again later.", 429)

        user = User.query.filter(
            or_(User.email == user_input, User.username == user_input)
        ).first()

        if user:
            try:
                if not user.email_verified:
                    if user.status in PENDING_STATUSES:
                        _send_verification_link(user)
                else:
                    reset_token = auth_service.issue_password_reset_token(user)
                    db.session.commit()
                    send_password_reset(
                        user.email, public_url("student.student_auth.reset_password_api", token=reset_token))
            except Exception:
                db.session.rollback()
                current_app.logger.error("Could not send password reset email", exc_info=True)

        return success_response(GENERIC_RESET_MESSAGE)

    except Exception:
        db.session.rollback()
        current_app.logger.error("Password reset error", exc_info=True)
        return error_response("Password reset failed. Please try again.")


@auth_bp.route("/verify-reset/<token>", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def reset_password_api(token):
    """
    Check a reset link. Read-only: opening or previewing the link does not
    spend it; it is spent by set_password() when the password changes.
    """
    if request.method == "GET":
        return render_template("auth/verify_reset.html")

    from errors import ValidationError

    try:
        user = auth_service.peek_password_reset_token(token)
    except ValidationError as e:
        return error_response(str(e))

    return success_response(
        "Password Reset Link Verified!",
        data={
            "email": user.email,
            "token": token,
            "redirect_url": "/student/set-password?token=" + quote(token) + "&email=" + quote(user.email),
        },
    )


@auth_bp.route("/set-password", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key, methods=["POST"])
def set_password():
    """
    Choose a new password with a reset token.

    The token is spent, the password written and every session of the user
    revoked in ONE transaction: if anything fails the token stays usable, and
    two simultaneous requests cannot both succeed (audit AUD-07/AUD-08).
    """
    if request.method == "GET":
        return render_template("auth/set_password.html")

    from errors import ValidationError

    data = get_json_data() or {}
    token = data.get("token") or request.args.get("token")
    password = data.get("password", "")
    confirm_password = data.get("confirm_password", "")

    if not isinstance(token, str) or not token:
        return error_response("Reset token is required")
    if not isinstance(password, str) or not isinstance(confirm_password, str):
        return error_response("Invalid data")
    if not all([password, confirm_password]):
        return error_response("All fields are required")
    if password != confirm_password:
        return error_response("Passwords do not match")
    if len(password) < 6:
        return error_response("Password must be at least 6 characters")

    try:
        reset_user = auth_service.consume_password_reset_token(token)
        user = auth_service.finalize_password(reset_user.email, password)
        auth_service.invalidate_password_reset_tokens(user.id)
        auth_service.revoke_all_refresh_tokens(user.id)
        db.session.commit()
    except (ValidationError, LookupError) as e:
        db.session.rollback()
        return error_response(str(e))
    except Exception:
        db.session.rollback()
        current_app.logger.error("Password reset failed", exc_info=True)
        return error_response("Failed to reset password. Please try again.")

    return success_response(
        "Password reset complete. You can now sign in.",
        data={"redirect_url": "/student/login"},
    )


@auth_bp.route("/verify-email/<token>", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def verify_email_api(token):
    """
    Verify an email address with a single-use token and sign the user in so
    they can continue with onboarding / complete-registration.

    The token is spent atomically (see auth_service) and the verification is
    committed in the same transaction. Re-opening an already used link only
    gets a friendly answer in the browser that is already signed in; anyone
    else is told to sign in or request a new link.
    """
    if request.method == "GET":
        return render_template("auth/verify-email.html")

    from errors import ValidationError
    from models import EmailVerificationToken

    try:
        user = auth_service.consume_email_verification_token(token)
    except ValidationError:
        # Nothing was changed (the conditional UPDATE matched no row).
        stale_row = EmailVerificationToken.query.filter_by(
            token_hash=auth_service.hash_one_time_token(token)).first()
        stale_user = User.query.get(stale_row.user_id) if stale_row else None
        if stale_row and stale_row.used and stale_user and stale_user.email_verified:
            session_user = _user_from_access_cookie()
            if session_user and session_user.id == stale_user.id:
                return success_response(
                    "Email already verified!",
                    data={"email": stale_user.email, "already_verified": True},
                )
            return error_response(
                "This link was already used. Sign in to continue, or request a new link from the sign-up page.",
                data={"already_verified": True, "login_url": "/student/login"},
            )
        return error_response("This verification link is invalid or has expired.")

    try:
        email = user.email

        if user.status == "approved":
            db.session.commit()
            return success_response(
                "Email already verified!",
                data={"email": email, "already_verified": True},
            )

        if not auth_service.account_may_hold_session(user) and user.status not in PENDING_STATUSES:
            db.session.rollback()
            return error_response("This account is not active.", 403)

        user.email_verified = True
        db.session.commit()

        access_token, refresh_token = generate_tokens_for_user(user)
        response = make_response(success_response(
            "Email verified successfully!",
            data={"email": email, "redirect_url": "/student/onboard/" + quote(email)},
        ))
        set_auth_cookies(response, access_token, refresh_token)
        return response

    except Exception:
        db.session.rollback()
        current_app.logger.error("Verification error", exc_info=True)
        return error_response("Verification failed. Please try again.")


@auth_bp.route("/check-username", methods=["POST"])
@limiter.limit(RateLimitTier.USERNAME_CHECK, key_func=ip_key)
def check_username():
    """Check if a username is available."""
    try:
        data = get_json_data()
        if not data:
            return error_response("No data provided")

        username = data.get("username", "")
        if not isinstance(username, str):
            return error_response("Invalid username format")
        username = username.strip().lower()
        if not username:
            return error_response("Username required")
        if not re.match(r"^[a-z0-9]{3,20}$", username):
            return error_response("Invalid username format")

        existing = User.query.filter_by(username=username).first()
        if existing:
            return error_response("Username taken")

        return success_response("Username available", data={"available": True})

    except Exception as e:
        current_app.logger.error(f"Check username error: {str(e)}")
        return error_response("Check failed")


@auth_bp.route("/complete-registration", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.REGISTRATION, key_func=ip_key, methods=["POST"])
def complete_registration():
    """
    Final registration step: choose a username and a password.

    Only users who are still registering (pending_* status, verified email,
    no username/password chosen yet) can use it. Approved accounts get 409:
    changing credentials of an existing account is not this route's job
    (audit AUD-02). Onboarding is optional -- it is not required first.
    """
    if request.method == "GET":
        email = _normalize_email(request.args.get("email"))
        if email:
            user = User.query.filter_by(email=email).first()
            if user and user.status == "approved":
                return redirect("/student/profile/homepage")
        return render_template("auth/complete-registration.html")

    # ── POST ──────────────────────────────────────────────────────────────────
    try:
        data = get_json_data()
        if not data:
            return error_response("Invalid JSON data received")

        fields = {k: data.get(k, "") for k in ("email", "password", "confirm_password", "username")}
        if not all(isinstance(v, str) for v in fields.values()):
            return error_response("Invalid data")

        email            = _normalize_email(fields["email"])
        password         = fields["password"]
        confirm_password = fields["confirm_password"]
        username         = fields["username"].strip().lower()

        if not all([email, password, confirm_password, username]):
            return error_response("All fields are required")

        # Proof of ownership: access cookie for this email, or the Google
        # session for this exact email.
        if not _is_request_authorized_for_email(email):
            return error_response("Unauthorized", 401)

        user = User.query.filter_by(email=email).first()
        if not user:
            return error_response("User not found", 404)

        if user.status == "approved":
            return error_response("Registration is already complete.", 409)
        if user.status not in PENDING_STATUSES:
            return error_response("This account cannot complete registration.", 403)
        if not user.email_verified:
            return error_response("Please verify your email first.", 403)

        if password != confirm_password:
            return error_response("Passwords do not match")
        if len(password) < 6:
            return error_response("Password must be at least 6 characters")
        if not re.match(r"^[a-z0-9]{3,20}$", username):
            return error_response("Username must be 3-20 lowercase letters and numbers only")

        if User.query.filter(User.username == username, User.id != user.id).first():
            return error_response("Username already taken")

        try:
            user = auth_service.finalize_password(email, password)
        except LookupError as e:
            return error_response(str(e))

        user.username = username
        user.status   = "approved"

        student_profile = StudentProfile.query.filter_by(user_id=user.id).first()

        try:
            db.session.commit()
        except IntegrityError:
            # Lost a race for the username (unique constraint).
            db.session.rollback()
            return error_response("Username already taken")

        # Activity feed: fire only now -- this is the point a user becomes a
        # real, connectable member (status="approved").
        record_live_activity(
            user.id,
            'user_signed_up',
            {
                'name': user.name,
                'department': student_profile.department if student_profile else None,
                'avatar': user.avatar,
            },
            scope='global',
        )

        _clear_google_oauth_session()

        # Issue a fresh session so the access token carries the real
        # username/status (users who skipped onboarding, or arrived through
        # the Google session only, have no cookies yet).
        access_token, refresh_token = generate_tokens_for_user(user)
        response = make_response(success_response(
            f"Registration complete! Welcome, @{username}!",
            data={"username": username},
        ))
        set_auth_cookies(response, access_token, refresh_token)
        return response

    except Exception:
        db.session.rollback()
        current_app.logger.error("Complete registration error", exc_info=True)
        return error_response("Registration failed. Please try again.")


@auth_bp.route("/reset-password", methods=["GET"])
def reset_password():
    """Render password reset request page."""
    return render_template("auth/reset_request.html")





@auth_bp.route("/refresh-token", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=ip_key)
def refresh_token():
    """
    Exchange the refresh-token cookie for a new access token.

    The refresh token is rotated on every use (see
    auth_service.rotate_refresh_token for rotation, reuse detection and the
    multi-tab grace window). Account eligibility is checked before the token
    is rotated, so a rejected account does not burn its token.
    """
    from errors import ValidationError

    try:
        refresh_tok = request.cookies.get("refresh_token")
        if not refresh_tok:
            return error_response("Refresh token not found")

        try:
            user, new_refresh_token = auth_service.rotate_refresh_token(refresh_tok)
        except ValidationError as e:
            # Invalid, expired, reused or not-eligible: the client cannot
            # recover without a fresh login, so drop its dead credentials.
            response = make_response(error_response(str(e)))
            clear_auth_cookies(response)
            return response

        new_access_token = _build_access_token(user)
        response = make_response(success_response(
            "Token refreshed",
            data={"user": {"id": user.id, "username": user.username, "email": user.email, "name": user.name}},
        ))
        # new_refresh_token is None in the multi-tab grace case; set_auth_cookies
        # then leaves the refresh cookie another tab already installed alone.
        set_auth_cookies(response, new_access_token, new_refresh_token)
        return response

    except Exception:
        current_app.logger.error("Token refresh error", exc_info=True)
        return error_response("Token refresh failed")


@auth_bp.route("/verify-auth", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=ip_key)
def verify_auth():
    """Verify if user is authenticated."""
    try:
        access_token = request.cookies.get("access_token")
        if not access_token:
            return jsonify({"status": "error", "authenticated": False, "message": "No token found"}), 401

        try:
            payload = decode_token(access_token)
        except jwt.ExpiredSignatureError:
            return jsonify({"status": "error", "authenticated": False, "message": "Token expired", "should_refresh": True}), 401
        except jwt.InvalidTokenError:
            return jsonify({"status": "error", "authenticated": False, "message": "Invalid token"}), 401

        user = User.query.get(payload.get("user_id"))
        if not user:
            return jsonify({"status": "error", "authenticated": False, "message": "User not found"}), 401

        return jsonify({
            "status": "success",
            "authenticated": True,
            "data": {
                "user": {
                    "id":       user.id,
                    "username": user.username,
                    "email":    user.email,
                    "name":     user.name,
                    "avatar":   user.avatar,
                    "role":     user.role,
                    "status":   user.status,
                }
            },
        }), 200

    except Exception as e:
        current_app.logger.error(f"Verify auth error: {str(e)}")
        return jsonify({"status": "error", "authenticated": False, "message": "Verification failed"}), 500


@auth_bp.route("/logout", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=ip_key)
def logout():
    """
    Logout user.

    Auth-flow-audit fix (Finding #8): GET support removed. A GET-based
    logout is CSRF-forgeable — a third-party page can trigger it with a
    plain <img src="https://.../student/logout"> tag, since browsers
    don't apply CSRF protections to simple GET requests/navigations, the
    way they effectively do for cross-origin POST-with-body fetches under
    this app's cookie + CORS configuration. Confirmed no frontend page
    linked directly to GET /logout — api.js's logout() already exclusively
    uses api.post('/logout') — so this is a pure hardening change with no
    frontend behavior change required.
    """
    try:
        # Document 3 §1.5: identify the user (if any) before clearing the
        # cookie, so we can proactively disconnect their active WebSocket
        # session(s) too — access_token is now the WS credential as well,
        # so a stale-but-connected socket surviving a logout is a gap this
        # closes. Best-effort: any failure here must never block logout
        # itself from succeeding.
        user_id = None
        try:
            access_token = request.cookies.get("access_token")
            if access_token:
                payload = decode_token(access_token)
                user_id = payload.get("user_id")
                if user_id:
                    from services.websocket_messages import message_ws_manager
                    message_ws_manager.disconnect_user(user_id)
        except Exception:
            pass

        # Stop push notifications for this account reaching the device it
        # just logged out of (it may be shared, or used by someone else
        # next). Best-effort: never blocks logout.
        try:
            if user_id:
                User.query.filter_by(id=user_id).update(
                    {User.fcm_token: None}, synchronize_session=False)
                db.session.commit()
        except Exception:
            db.session.rollback()

        # Auth-flow-audit fix (Finding #6 side effect): previously logout
        # only cleared cookies client-side — the refresh token itself
        # remained valid server-side (it was a stateless JWT with nothing
        # to revoke) for the rest of its 7-day life. A copy of that
        # cookie (stolen, or simply retained by another tab/device that
        # didn't just log out) could still mint new access tokens after
        # "logout". Now that refresh tokens are DB-backed and revocable,
        # explicitly revoke this session's whole rotation family here.
        # Best-effort, same reasoning as the WS-disconnect block above:
        # never let a failure here block logout from succeeding.
        try:
            refresh_tok = request.cookies.get("refresh_token")
            if refresh_tok:
                auth_service.revoke_refresh_token_family(refresh_tok)
        except Exception:
            pass

        response = make_response(success_response("Logged out successfully"))

        clear_auth_cookies(response)
        return response

    except Exception as e:
        current_app.logger.error(f"Logout error: {str(e)}")
        return error_response("Logout failed")
