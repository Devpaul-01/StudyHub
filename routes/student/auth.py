# routes/student/auth.py

from flask import Blueprint, request, jsonify, redirect, url_for, current_app, make_response, render_template, session
from werkzeug.security import check_password_hash
import re
import random
from flask_dance.contrib.google import make_google_blueprint, google
from flask_dance.consumer import oauth_authorized
from sqlalchemy import or_, and_
from sqlalchemy.orm import joinedload
import jwt
import datetime
import os

from models import User, StudentProfile, Notification, OnboardingDetails, Connection, UserActivity
from extensions import db
# Token issuance/verification (generate_tokens_for_user, decode_token, verify_token)
# lives exclusively in .helpers now — utils.py no longer duplicates these, so
# everything JWT-related is imported from a single source of truth here.
from utils import send_password_reset, send_verification_email
from .helpers import (
    generate_tokens_for_user, decode_token, verify_token, token_required,
    success_response, error_response, set_auth_cookies, clear_auth_cookies,
    _build_access_token,
)
# Phase 5b (Document 4 §1): SENSITIVE_AUTH-tier rate limiting on every
# pre-auth route in this file (login, register, password-reset trigger,
# complete-registration) — the specific gap Document 03 §C flagged as
# unthrottled brute-force/email-enumeration risk. ip_key() since these are
# all pre-authentication (no user identity yet to key on).
from services.rate_limit_service import limiter, RateLimitTier, ip_key
# Activity/streak recording and password finalization now live in
# services/auth_service.py (Document 2 §3.10); notification construction
# goes through services/notification_service.py (Document 2 §3.9).
from services import auth_service, notification_service
from errors import ValidationError

auth_bp = Blueprint("student_auth", __name__)

# ============================================================================
# CONSTANTS
# ============================================================================
DEPARTMENTS = [
    "Architecture", "Computer Science", "Engineering (Civil)", "Engineering (Electrical)",
    "Engineering (Mechanical)", "Medicine & Surgery", "Pharmacy", "Nursing", "Law",
    "Accounting", "Business Administration", "Economics", "Mass Communication", "English",
    "History", "Biology", "Chemistry", "Physics", "Mathematics", "Statistics",
    "Psychology", "Sociology", "Political Science", "Agricultural Science",
    "Fine Arts", "Music", "Theatre Arts",
]

CLASS_LEVELS = ["100 Level", "200 Level", "300 Level", "400 Level", "500 Level"]

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
    "notification_category": [],
    "enable_notification": True,
    "send_email_notification": False,
}

connection_settings = {
    "enable_sound": True,
}

privacy_settings = {
    "set_profile_private": False,
    "show_active_status": True,
    "set_dark_mode": False,
    "send_weekly_notification": True,   # FIX: was "send_weeekly_notifications" (typo + wrong key)
}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
def get_json_data():
    """Safely get JSON data from request."""
    try:
        if request.is_json:
            return request.get_json(force=True, silent=True)
        data = request.get_data(as_text=True)
        if data:
            import json
            return json.loads(data)
        return None
    except Exception as e:
        current_app.logger.error(f"JSON parsing error: {str(e)}")
        return None


def is_valid_email(email):
    """Returns True if the email address looks valid."""
    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    return re.match(pattern, email) is not None


def _is_request_authorized_for_email(email):
    """
    Return True when the current request is allowed to act on behalf of `email`.

    Two accepted proofs:
      1. The Google OAuth session contains this email (new user mid-onboarding,
         before they have a JWT).
      2. A valid JWT access-token cookie belongs to this email.
    """
    # --- Google OAuth session (new users have no token yet) ---
    if session.get("google_email") == email:
        return True

    # --- JWT cookie ---
    token = request.cookies.get("access_token")
    if token:
        try:
            payload = decode_token(token)
            if payload.get("email") == email:
                return True
        except Exception:
            pass

    return False


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


# record_activity/update_login_streak/_get_or_create_today_activity/
# _record_login_and_commit now live in services/auth_service.py
# (Document 2 §3.10). auth.py's routes call auth_service.record_activity(...)
# and auth_service.record_login_and_commit(...) directly.


# ============================================================================
# GOOGLE OAUTH
# ============================================================================
@auth_bp.route("users/me", methods=["GET"])
@token_required
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
            is_google_account = bool(existing_user.google_id) or existing_user.pin == "PENDING_VERIFICATION"

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

            # ✅ APPROVED USER → Login
            if existing_user.status == "approved":
                existing_user = auth_service.record_login_and_commit(existing_user)
                access_token, refresh_token_val = generate_tokens_for_user(existing_user)
                response = make_response(redirect("/student/profile/homepage"))
                set_auth_cookies(response, access_token, refresh_token_val)
                current_app.logger.info(f"Google login: existing user {email}")
                return response

            # ✅ NEEDS ONBOARDING → Go to onboard (FIXED!)
            if existing_user.status == "pending_onboarding":
                db.session.commit()
                # Auth-flow-audit fix (Finding #1/#5 interaction): onboard()
                # and complete_registration() authorize via
                # _is_request_authorized_for_email(), which accepts either
                # this session value or a JWT cookie. Returning Google
                # users redirected here have neither set yet at this point
                # in the flow — without this, a legitimate returning user
                # would be incorrectly blocked by the same ownership check
                # that (correctly) now blocks unauthenticated callers in
                # complete_registration(). Set the session proof here,
                # exactly as the brand-new-user branch already does.
                session["google_email"] = email
                session["google_name"]  = existing_user.name
                session["google_id"]    = google_sub
                current_app.logger.info(f"Google login: user {email} needs onboarding")
                return redirect(f"/student/onboard/{email}")

            # ✅ NEEDS COMPLETE-REGISTRATION → Go to complete-registration
            if existing_user.status == "pending_verification":
                db.session.commit()
                session["google_email"] = email
                session["google_name"]  = existing_user.name
                session["google_id"]    = google_sub
                current_app.logger.info(f"Google login: user {email} needs complete-registration")
                return redirect(f"/student/complete-registration?email={email}")

            # Fallback for any other status
            db.session.commit()
            session["google_email"] = email
            session["google_name"]  = existing_user.name
            session["google_id"]    = google_sub
            current_app.logger.info(f"Google login: user {email} in unknown status: {existing_user.status}")
            return redirect(f"/student/complete-registration?email={email}")

        # ── 2. Brand-new user ─────────────────────────────────────────────────
        new_user = User(
            name=name,
            email=email,
            role="student",
            pin="PENDING_VERIFICATION",
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
            date_of_birth=None,
            pin="PENDING_VERIFICATION",
            status="incomplete",
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

        session["google_name"]  = name
        session["google_id"]    = google_sub
        session["google_email"] = email

        current_app.logger.info(f"Google signup: new user {email} created, redirecting to onboarding")
        return redirect(f"/student/onboard/{email}")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Google OAuth error: {str(e)}")
        return redirect(url_for("student.student_auth.login") + "?error=oauth_failed")


@auth_bp.route("/google_temp_info")
def temp_info():
    """Get temporary OAuth info from session."""
    return jsonify({
        "status": "success",
        "email": session.get("google_email"),
        "name":  session.get("google_name"),
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
@token_required
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
@auth_bp.route("/onboard/suggestions-by-email/<email>", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
def onboard_suggestions_by_email(email):
    """Get study-buddy suggestions using email directly."""
    try:
        if not email:
            return error_response("Email required")

        user = User.query.filter_by(email=email).first()
        if not user:
            return error_response("User not found")

        matches = generate_onboarding_matches(user.id)

        if not matches:
            top_users = (
                User.query
                .filter(User.id != user.id, User.status == "approved")
                .order_by(User.reputation.desc())
                .limit(5)
                .all()
            )
            matches = [
                {
                    "user": {
                        "id":               tu.id,
                        "username":         tu.username,
                        "name":             tu.name,
                        "avatar":           tu.avatar or "/static/default-avatar.png",
                        "reputation":       tu.reputation,
                        "reputation_level": tu.reputation_level,
                    },
                    "match_score": random.randint(50, 70),
                    "reasons": ["Top contributor", "Active member"],
                }
                for tu in top_users
            ]

        return success_response("Suggestions generated", data={"matches": matches})

    except Exception as e:
        current_app.logger.error(f"Suggestions error: {str(e)}")
        return error_response("Failed to generate suggestions")


@auth_bp.route("/onboard/request-all/<email>", methods=["POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def request_all(email):
    """Send connection requests to a list of user IDs during onboarding."""
    try:
        if not email:
            return error_response("Email not found")

        # FIX: verify the caller is the owner of this email
        if not _is_request_authorized_for_email(email):
            return error_response("Unauthorized", 401)

        user = User.query.filter_by(email=email).first()
        if not user:
            return error_response("User not found")

        data = request.get_json()
        ids  = (data or {}).get("ids", [])

        if ids:
            for rid in ids:
                # FIX: skip if a connection already exists in either direction
                existing = Connection.query.filter(
                    or_(
                        and_(Connection.requester_id == user.id, Connection.receiver_id == rid),
                        and_(Connection.requester_id == rid,    Connection.receiver_id == user.id),
                    )
                ).first()
                if not existing:
                    db.session.add(Connection(
                        status="pending",
                        requester_id=user.id,
                        receiver_id=rid,
                        requested_at=datetime.datetime.utcnow(),
                    ))

        db.session.commit()
        return success_response("Connection request sent successfully")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"request_all error: {str(e)}")
        return error_response("An error occurred sending connection requests")


@auth_bp.route("/onboard/<email>", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key, methods=["POST"])
def onboard(email):
    """Handle onboarding — GET renders the page, POST saves data."""

    if request.method == "GET":
        return render_template("onboard.html")

    # POST ──────────────────────────────────────────────────────────────────
    try:
        # FIX: verify the caller owns this email before writing any data
        if not _is_request_authorized_for_email(email):
            return error_response("Unauthorized", 401)

        data = request.get_json()
        if not data:
            return error_response("No data provided")

        user = User.query.filter_by(email=email).first()
        if not user:
            return error_response("User not found")

        student_profile = user.student_profile

        onboarding_details = OnboardingDetails.query.filter_by(user_id=user.id).first()
        if not onboarding_details:
            onboarding_details = OnboardingDetails(user_id=user.id, email=email)
            db.session.add(onboarding_details)

        name              = data.get("name", "").strip()
        department        = data.get("department", "")
        class_level       = data.get("class_level", "")
        subjects          = data.get("subjects", [])
        learning_style    = data.get("learning_style", "")
        study_preferences = data.get("study_preferences", [])
        help_subjects     = data.get("help_subjects", [])
        strong_subjects   = data.get("strong_subjects", [])
        study_schedule    = data.get("study_schedule", {})
        session_length    = data.get("session_length", "")

        if name:
            user.name = name
            if student_profile:
                student_profile.full_name = name

        if student_profile:
            student_profile.department = department
            student_profile.class_name = class_level

        onboarding_details.department        = department
        onboarding_details.class_level       = class_level
        onboarding_details.subjects          = subjects
        onboarding_details.learning_style    = learning_style
        onboarding_details.study_preferences = study_preferences
        onboarding_details.help_subjects     = help_subjects
        onboarding_details.strong_subjects   = strong_subjects
        onboarding_details.study_schedule    = study_schedule
        onboarding_details.session_length    = session_length
        onboarding_details.last_updated      = datetime.datetime.utcnow()
        user.status = "pending_verification"

        db.session.commit()
        

        access_token, refresh_token = generate_tokens_for_user(user)

        # Auth-flow-audit fix (Finding #5): clear stale Google-OAuth
        # session state now that a real JWT cookie exists for this user —
        # the session value is no longer needed for authorization past
        # this point, and leaving it around only risks it being reused as
        # a stale authorization proof on a shared device.
        _clear_google_oauth_session()

        response = make_response(success_response(
            "Onboarding details saved successfully",
            data={
                "user": {
                    "id":       user.id,
                    "name":     user.name,
                    "username": user.username,
                    "email":    user.email,
                },
                "redirect": f"/student/complete-registration/{user.email}",
            },
        ))
        set_auth_cookies(response, access_token, refresh_token)
        return response

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Onboarding error: {str(e)}")
        return error_response("Failed to save onboarding data")   # FIX: no str(e) leak


def generate_onboarding_matches(user_id):
    """
    Generate study-buddy matches based on onboarding data.

    FIX: uses joinedload to load onboarding_details in one query
    instead of issuing one query per candidate (N+1 eliminated).
    """
    try:
        user = User.query.get(user_id)
        if not user:
            current_app.logger.error(f"User {user_id} not found")
            return []

        progress = OnboardingDetails.query.filter_by(user_id=user_id).first()
        if not progress:
            current_app.logger.warning(f"No onboarding data for user {user_id}")
            return []

        # FIX: single query — onboarding_details eager-loaded, no per-row queries
        potential_matches = (
            User.query
            .filter(User.id != user.id, User.status == "approved")
            .options(joinedload(User.onboarding_details))
            .all()
        )

        matches = []

        for candidate in potential_matches:
            cand_progress = candidate.onboarding_details
            if not cand_progress:
                continue

            score   = 0
            reasons = []

            # Same department (20 pts)
            if cand_progress.department == progress.department:
                score += 20
                reasons.append(f"Same major ({progress.department})")

            # Same subjects (up to 30 pts)
            common_subjects = set(progress.subjects or []) & set(cand_progress.subjects or [])
            if common_subjects:
                score += min(len(common_subjects) * 10, 30)
                reasons.append(f"Studying {', '.join(list(common_subjects)[:2])}")

            # Complementary strengths (up to 25 pts)
            helpful_overlap = set(progress.help_subjects or []) & set(cand_progress.strong_subjects or [])
            if helpful_overlap:
                score += min(len(helpful_overlap) * 10, 25)
                reasons.append(f"Can help you with {list(helpful_overlap)[0]}")

            # Schedule overlap (up to 25 pts)
            user_avail = {
                f"{day}_{t}"
                for day, times in (progress.study_schedule or {}).items()
                for t in times
            }
            cand_avail = {
                f"{day}_{t}"
                for day, times in (cand_progress.study_schedule or {}).items()
                for t in times
            }
            time_overlap = len(user_avail & cand_avail)
            if time_overlap:
                score += min(time_overlap * 5, 25)
                reasons.append("Available at same times")

            if score >= 40:
                matches.append({
                    "user": {
                        "id":               candidate.id,
                        "username":         candidate.username,
                        "name":             candidate.name,
                        "avatar":           candidate.avatar or "/static/images/default-avatar.png",
                        "reputation":       candidate.reputation,
                        "reputation_level": candidate.reputation_level,
                    },
                    "match_score": score,
                    "reasons":     reasons[:4],
                })

        matches.sort(key=lambda x: x["match_score"], reverse=True)
        return matches[:5]

    except Exception as e:
        current_app.logger.error(f"Error generating matches: {str(e)}")
        return []


@auth_bp.route("/onboard/suggestions/<token>", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
def onboard_suggestions(token):
    """Get study-buddy suggestions based on onboarding data (token-based)."""
    try:
        email = verify_token(token)

        if isinstance(email, dict) and "error" in email:
            return error_response(email["error"])

        user = User.query.filter_by(email=email).first()
        if not user:
            return error_response("User not found")

        matches = generate_onboarding_matches(user.id)

        if not matches:
            top_users = (
                User.query
                .filter(User.id != user.id, User.status == "approved")
                .order_by(User.reputation.desc())
                .limit(5)
                .all()
            )
            matches = [
                {
                    "user": {
                        "id":               tu.id,
                        "username":         tu.username,
                        "name":             tu.name,
                        "avatar":           tu.avatar or "/static/images/default-avatar.png",
                        "reputation":       tu.reputation,
                        "reputation_level": tu.reputation_level,
                    },
                    "match_score": random.randint(50, 70),
                    "reasons": ["Top contributor", "Active member"],
                }
                for tu in top_users
            ]

        return success_response("Suggestions generated", data={"matches": matches})

    except Exception as e:
        current_app.logger.error(f"Suggestions error: {str(e)}")
        return error_response("Failed to generate suggestions")


# ============================================================================
# REGISTER
# ============================================================================
@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key, methods=["POST"])
def register():
    """Registration endpoint."""
    if request.method == "GET":
        return render_template("auth/register.html")

    current_app.logger.info("=== REGISTER REQUEST ===")
    current_app.logger.info(f"Content-Type: {request.content_type}")

    try:
        data = get_json_data()
        if data is None:
            return error_response("Invalid JSON data received")

        full_name       = data.get("full_name", "").strip()
        email           = data.get("email", "").strip().lower()
        google_verified_claim = bool(data.get("google_verified", False))

        if not all([full_name, email]):
            return error_response("All fields are required")
        if not is_valid_email(email):
            return error_response("Invalid email format")

        # Auth-flow-audit fix (related to Findings #1/#2): google_verified
        # was previously a client-asserted boolean taken at face value —
        # any direct POST to this endpoint could set
        # {"google_verified": true} and skip the email-verification step
        # entirely (email_verified=True, status="pending_verification"
        # instead of requiring the verify-email link), with no proof the
        # caller actually completed Google OAuth for this address. The
        # legitimate frontend flow only ever sets this flag after a real
        # GET /google_temp_info success, which reflects
        # session["google_email"] having been set by an actual Google
        # OAuth callback — so that same server-side session value is the
        # correct thing to check here, not the client's claim.
        google_verified = google_verified_claim and session.get("google_email") == email

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            current_app.logger.error(f"Email {email} already exists")
            return error_response("Email already registered")

        # If this is a genuinely Google-verified registration, capture the
        # Google account id too — matches google_callback()'s own new-user
        # path and lets is_google_account (see google_callback, Finding #2)
        # keep recognizing this account as Google-originated later, even
        # after complete_registration() sets a real password hash on `pin`.
        google_sub_for_register = session.get("google_id") if google_verified else None

        # FIX: dict() copies so each user gets independent mutable dicts
        new_user = User(
            name=full_name,
            email=email,
            role="student",
            pin="PENDING_VERIFICATION",
            status="pending_verification",  # ✅ Same for both (was pending_onboarding for Google)
            email_verified=google_verified,
            google_id=google_sub_for_register,
            privacy_settings=dict(privacy_settings),
            notification_settings=dict(notification_settings),
            connection_settings=dict(connection_settings),
        )
        db.session.add(new_user)
        db.session.flush()

        student_profile = StudentProfile(
            user_id=new_user.id,
            full_name=full_name,
            date_of_birth=None,
            pin="PENDING_VERIFICATION",
            status="incomplete",
            department="",
            class_name="",
        )
        db.session.add(student_profile)

        notification_service.notify_welcome(
            new_user.id, email.split('@')[0],
            features_link=url_for("student.student_auth.features"),
            rich=True,
        )
        auth_service.record_activity(new_user.id, "register", score=5)
        db.session.commit()

        if google_verified:
            current_app.logger.info(f"Google-verified registration for {email}")
            _clear_google_oauth_session()
            return success_response(
                "Account created! Let's set up your profile.",
                data={
                    "google_verified": True,
                    "redirect_url": f"/student/onboard/{email}",  # ✅ FIX: Was complete-registration
                },
            )

        # Auth-flow-audit fix (Finding #3, Important): previously issued a
        # stateless JWT (generate_verification_token) that stayed valid and
        # reusable for its full 5-hour life regardless of how many times it
        # was used, and auto-logged in on every first-time success — a
        # leaked/forwarded verification email was a live session-hijack
        # vector for that whole window. Now uses the same opaque,
        # single-use, DB-backed token pattern already used for password
        # resets (auth_service.issue_email_verification_token /
        # consume_email_verification_token), so a captured link stops
        # working the instant it's used once.
        token = auth_service.issue_email_verification_token(new_user)
        db.session.commit()

        verification_url = url_for("student.student_auth.verify_email_api", token=token, _external=True)
        send_verification_email(email, verification_url)

        return success_response("Registration successful! Check your email for verification link.")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Registration error: {str(e)}")
        return error_response("Registration failed. Please try again.")


# ============================================================================
# LOGIN
# ============================================================================
@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key, methods=["POST"])
def login():
    """Login endpoint."""
    if request.method == "GET":
        return render_template("auth/login.html")

    try:
        data = get_json_data()
        if data is None:
            return error_response("Invalid JSON data received")

        username_or_email = data.get("username_or_email", "").strip().lower()
        password          = data.get("password", "")

        if not username_or_email or not password:
            return error_response("Username/Email and password required")

        user = User.query.filter(
            or_(User.username == username_or_email, User.email == username_or_email)
        ).first()

        if not user:
            return error_response("Invalid credentials")

        if user.pin == "PENDING_VERIFICATION":
            return error_response("Please complete your registration. Check your email for the verification link.")

        if not user.email_verified:
            return error_response("Please verify your email before logging in.")

        if not user.username:
            return error_response("Please complete your registration.")

        if not check_password_hash(user.pin, password):
            return error_response("Invalid credentials")

        if user.status != "approved":
            return error_response("Your account is pending approval.")

        user = auth_service.record_login_and_commit(user)

        access_token, refresh_token = generate_tokens_for_user(user)

        # Auth-flow-audit fix (Finding #5): clear any stale Google-OAuth
        # session state on every successful login, not only when the
        # frontend remembers to call /clear-session.
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

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Login error: {str(e)}")
        return error_response("Login failed. Please try again.")   # FIX: no str(e) leak


# ============================================================================
# PASSWORD RESET / EMAIL VERIFICATION
# ============================================================================
@auth_bp.route("/validate-user", methods=["POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def validate_user():
    """
    Validate user and send password reset email.

    Document 3 §4: issues an opaque, DB-backed PasswordResetToken
    (services/auth_service.py::issue_password_reset_token) — revocable
    and single-use.

    Auth-flow-audit note (Finding #3): email verification (register() /
    verify_email_api()) was originally left on the shared stateless JWT
    per this section's scoping decision, but was subsequently migrated to
    the same opaque/single-use/DB-backed pattern used here
    (EmailVerificationToken), because a leaked/forwarded verification
    link turned out to be a real session-hijack vector (a successful
    verification auto-logs the user in) — see auth_service.py's
    EMAIL VERIFICATION TOKENS section for the full rationale. This
    function and its token flow are unaffected by that change.
    """
    try:
        data       = request.get_json()
        user_input = data.get("data")

        if not user_input:
            return error_response("Kindly enter email or username")

        result = User.query.filter(
            or_(User.email == user_input, User.username == user_input)
        ).first()

        if not result:
            return error_response("User not found, kindly check inputted value")

        reset_token = auth_service.issue_password_reset_token(result)
        db.session.commit()

        verification_url = url_for("student.student_auth.reset_password_api", token=reset_token, _external=True)
        send_password_reset(result.email, verification_url)

        return success_response("A password reset link has been sent to your email.")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Password reset error: {str(e)}")
        return error_response("Password reset failed. Please try again.")   # FIX: no str(e) leak

@auth_bp.route("/verify-reset/<token>", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def reset_password_api(token):
    """
    Verify password reset token.

    Document 3 §4: this is now a read-only PEEK at the PasswordResetToken
    row (is_valid() check only — does NOT mark it used) so the link can
    be safely opened/previewed without burning the single-use token; the
    token is actually consumed in set_password() below, at the point the
    password is genuinely changed.
    """
    if request.method == "GET":
        return render_template("auth/verify_reset.html")

    from models import PasswordResetToken

    current_app.logger.info(f"🔍 [verify-reset] Verifying token: {token[:20]}...")

    reset_row = PasswordResetToken.query.filter_by(token=token).first()
    
    if not reset_row:
        current_app.logger.warning(f"❌ [verify-reset] Token not found in database: {token[:20]}...")
        return error_response("This password reset link is invalid or has expired.")

    current_app.logger.info(f"📊 [verify-reset] Token found:")
    current_app.logger.info(f"   - user_id: {reset_row.user_id}")
    current_app.logger.info(f"   - used: {reset_row.used}")
    current_app.logger.info(f"   - expires_at: {reset_row.expires_at}")
    current_app.logger.info(f"   - is_valid: {reset_row.is_valid()}")

    if not reset_row.is_valid():
        current_app.logger.warning(f"❌ [verify-reset] Token is INVALID:")
        if reset_row.used:
            current_app.logger.warning(f"   - Token already used")
        if reset_row.expires_at < datetime.datetime.utcnow():
            current_app.logger.warning(f"   - Token expired at: {reset_row.expires_at}")
        return error_response("This password reset link is invalid or has expired.")

    user = User.query.get(reset_row.user_id)
    if not user:
        current_app.logger.warning(f"❌ [verify-reset] User not found for token: {token[:20]}...")
        return error_response("User not found")

    current_app.logger.info(f"✅ [verify-reset] Token verified successfully for user: {user.email}")
    
    return success_response(
        "Password Reset Link Verified!",
        data={
            "email": user.email,
            "token": token,
            "redirect_url": f"/student/set-password?token={token}&email={user.email}",
        },
    )

@auth_bp.route("/set-password", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key, methods=["POST"])
def set_password():
    """
    Set new password after reset.

    Document 3 §4: consumes the opaque PasswordResetToken (marks it used,
    single-use enforced) via auth_service.consume_password_reset_token —
    this is the point the reset link is actually spent, distinct from the
    read-only peek in reset_password_api() above. finalize_password()
    still does the actual pin/password_hash write, unchanged.
    """
    if request.method == "GET":
        return render_template("auth/set_password.html")

    from models import PasswordResetToken

    data = request.get_json() or {}
    token = data.get("token") or request.args.get("token")
    password = data.get("password", "")
    confirm_password = data.get("confirm_password", "")

    current_app.logger.info(f"🔍 [set-password] Received request:")
    current_app.logger.info(f"   - token: {token[:20] if token else 'MISSING'}...")
    current_app.logger.info(f"   - password length: {len(password) if password else 0}")
    current_app.logger.info(f"   - confirm_password length: {len(confirm_password) if confirm_password else 0}")

    if not token:
        current_app.logger.warning("❌ [set-password] No token provided in request")
        return error_response("Reset token is required")

    # ✅ Check token exists and is valid BEFORE consuming
    reset_row = PasswordResetToken.query.filter_by(token=token).first()
    
    if not reset_row:
        current_app.logger.warning(f"❌ [set-password] Token not found in database: {token[:20]}...")
        return error_response("This password reset link is invalid or has expired.")

    current_app.logger.info(f"📊 [set-password] Token found:")
    current_app.logger.info(f"   - user_id: {reset_row.user_id}")
    current_app.logger.info(f"   - used: {reset_row.used}")
    current_app.logger.info(f"   - expires_at: {reset_row.expires_at}")
    current_app.logger.info(f"   - is_valid: {reset_row.is_valid()}")

    if not reset_row.is_valid():
        current_app.logger.warning(f"❌ [set-password] Token is INVALID:")
        if reset_row.used:
            current_app.logger.warning(f"   - Token already used")
        if reset_row.expires_at < datetime.datetime.utcnow():
            current_app.logger.warning(f"   - Token expired at: {reset_row.expires_at}")
        return error_response("This password reset link is invalid or has expired.")

    if not all([password, confirm_password]):
        current_app.logger.warning("❌ [set-password] Missing password or confirm_password")
        return error_response("All fields are required")
    
    if password != confirm_password:
        current_app.logger.warning("❌ [set-password] Passwords do not match")
        return error_response("Passwords do not match")
    
    if len(password) < 6:
        current_app.logger.warning("❌ [set-password] Password too short")
        return error_response("Password must be at least 6 characters")

    try:
        current_app.logger.info(f"🔄 [set-password] Attempting to consume token for user_id: {reset_row.user_id}")
        
        # consume_password_reset_token commits internally (Document 2 §5's
        # documented second exception) the moment the token is marked used,
        # independent of whatever finalize_password does below.
        reset_user = auth_service.consume_password_reset_token(token)
        
        current_app.logger.info(f"✅ [set-password] Token consumed successfully for: {reset_user.email}")
        
        user = auth_service.finalize_password(reset_user.email, password)
        
        current_app.logger.info(f"✅ [set-password] Password updated successfully for: {user.email}")
        
    except LookupError as e:
        current_app.logger.error(f"❌ [set-password] LookupError: {str(e)}")
        return error_response(str(e))
    except ValidationError as e:
        current_app.logger.error(f"❌ [set-password] ValidationError: {str(e)}")
        return error_response(str(e))
    except Exception as e:
        current_app.logger.error(f"❌ [set-password] Unexpected error: {str(e)}")
        return error_response("Failed to reset password. Please try again.")

    db.session.commit()
    return success_response(
        f"Password reset complete, @{user.username}!",
        data={"redirect_url": "/student/login"},
    )
    
    
    
@auth_bp.route("/verify-email/<token>", methods=["GET", "POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def verify_email_api(token):
    """API endpoint for email verification."""
    if request.method == "GET":
        return render_template("auth/verify-email.html")

    # Auth-flow-audit fix (Finding #3, Important): previously called
    # verify_token(token) (JWT decode) here, which succeeded identically
    # on every call for the token's full 5-hour life — a leaked/forwarded
    # verification link could be replayed to auto-login as the user at
    # any point in that window, not just once. Now consumes the opaque,
    # single-use EmailVerificationToken issued by register() — the token
    # itself is marked used on first successful verification, so a
    # replay of the same link no longer decodes/succeeds at all.
    #
    # The pre-existing "already verified" idempotent-response branch is
    # preserved for genuine double-clicks/page-refreshes: if the token
    # was already consumed (ValidationError) but the underlying account
    # is already verified+approved, we still return the friendly
    # already-verified payload instead of a hard error. Any other
    # invalid/expired/unknown token is a hard error, exactly as before.
    from errors import ValidationError

    try:
        user = auth_service.consume_email_verification_token(token)
    except ValidationError:
        # Token unknown, expired, or already used. If the account tied to
        # this token was already fully verified/approved, this is almost
        # certainly a harmless replay (double-click, page refresh, email
        # link-preview bot) — keep the existing friendly response rather
        # than surfacing an error for a state the user already reached.
        # We can't recover which user a used/unknown token belonged to,
        # so this can only be detected via the token row itself.
        from models import EmailVerificationToken
        stale_row = EmailVerificationToken.query.filter_by(token=token).first()
        if stale_row:
            stale_user = User.query.get(stale_row.user_id)
            # email_verified=True alone is the correct signal that this
            # token already did its job successfully on an earlier call —
            # status only reaches "approved" much later, after the user
            # also completes onboarding + complete-registration, so
            # gating on status=="approved" here would show a hard error
            # for a legitimate second click of the link during that
            # in-between window, even though verification itself already
            # genuinely succeeded.
            if stale_user and stale_user.email_verified:
                return success_response(
                    "Email already verified!",
                    data={
                        "email": stale_user.email,
                        "already_verified": True,
                    },
                )
        return error_response("This verification link is invalid or has expired.")

    try:
        email = user.email

        if user.email_verified and user.status == "approved":
            return success_response(
                "Email already verified!",
                data={
                    "email": email,
                    "already_verified": True,
                },
            )

        user.email_verified = True
        db.session.commit()

        # ✅ AUTO-LOGIN: Generate tokens like login does
        access_token, refresh_token = generate_tokens_for_user(user)

        # ✅ Set auth cookies
        response = make_response(success_response(
            "Email verified successfully!",
            data={
                "email": email,
                "redirect_url": f"/student/onboard/{email}",
            },
        ))
        set_auth_cookies(response, access_token, refresh_token)

        current_app.logger.info(f"Email verified and auto-login for {email}")
        return response

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Verification error: {str(e)}")
        return error_response("Verification failed. Please try again.")


@auth_bp.route("/check-username", methods=["POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def check_username():
    """Check if a username is available."""
    try:
        data = get_json_data()
        if not data:
            return error_response("No data provided")

        username = data.get("username", "").strip().lower()
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
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key, methods=["POST"])
def complete_registration():
    """Complete registration with username and password."""
    if request.method == "GET":
        email = request.args.get("email")
        token = request.args.get("token")
        
        if token:
            decoded_email = verify_token(token)
            if isinstance(decoded_email, dict) and "error" in decoded_email:
                return render_template("auth/complete-registration.html", error=decoded_email["error"])
            email = decoded_email
        
        if email:
            user = User.query.filter_by(email=email).first()
            if user:
                # ✅ FIX: Only redirect to onboard if onboarding NOT done
                if user.status == "pending_onboarding":
                    return redirect(f"/student/onboard/{email}")
                
                # ✅ FIX: pending_verification → show complete-registration
                # (don't redirect)
                
                # ✅ FIX: approved → redirect to homepage
                if user.status == "approved":
                    return redirect("/student/profile/homepage")
        
        return render_template("auth/complete-registration.html")

    # ── POST ──────────────────────────────────────────────────────────────────
    try:
        data = get_json_data()
        if not data:
            return error_response("No data provided")

        token            = data.get("token") or request.args.get("token")
        email            = data.get("email", "").strip().lower()
        password         = data.get("password", "")
        confirm_password = data.get("confirm_password", "")
        username         = data.get("username", "").strip().lower()

        if token:
            decoded_email = verify_token(token)
            if isinstance(decoded_email, dict) and "error" in decoded_email:
                return error_response(decoded_email["error"])
            email = decoded_email

        if not all([email, password, confirm_password, username]):
            return error_response("All fields are required")

        # Auth-flow-audit fix (Finding #1, Critical): previously this route
        # trusted a bare `email` string from the request body/query with no
        # proof the caller owns that address — a token (if present) proved
        # ownership, but the frontend never sends one, so in practice every
        # call reached this point unauthenticated. That let anyone set the
        # username/password for ANY pending_verification account just by
        # knowing its email (full account takeover).
        #
        # Fix: require the same proof of ownership onboard() already
        # requires — either a Google-OAuth session for this exact email, or
        # a valid JWT access-token cookie for this exact email. A verified
        # `token` (JWT-encoded email, from the verification-email link) is
        # ALSO still accepted as proof, since decoding it above already
        # establishes the caller followed a link sent to that address.
        if not token and not _is_request_authorized_for_email(email):
            return error_response("Unauthorized", 401)

        if password != confirm_password:
            return error_response("Passwords do not match")
        if len(password) < 6:
            return error_response("Password must be at least 6 characters")
        if not re.match(r"^[a-z0-9]{3,20}$", username):
            return error_response("Username must be 3-20 lowercase letters and numbers only")

        user = User.query.filter_by(email=email).first()
        if not user:
            return error_response("User not found")

        if User.query.filter_by(username=username).first():
            return error_response("Username already taken")

        try:
            user = auth_service.finalize_password(email, password)
        except LookupError as e:
            return error_response(str(e))

        user.username = username
        user.status   = "approved"

        student_profile = StudentProfile.query.filter_by(user_id=user.id).first()
        if student_profile:
            student_profile.username = username
            student_profile.status   = "active"

        db.session.commit()

        # Auth-flow-audit fix (Finding #5): this is the final step of the
        # onboarding chain (account now "approved") — clear any leftover
        # Google-OAuth session state so it can't be reused as a stale
        # authorization proof for this email on a shared device afterward.
        _clear_google_oauth_session()

        return success_response(
            f"Registration complete! Welcome, @{username}!",
            data={"username": username},
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Complete registration error: {str(e)}")
        return error_response("Registration failed. Please try again.")


@auth_bp.route("/reset-password", methods=["GET"])
def reset_password():
    """Render password reset request page."""
    return render_template("auth/reset_request.html")





@auth_bp.route("/refresh-token", methods=["POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def refresh_token():
    """
    Refresh access token using the refresh-token cookie.

    Auth-flow-audit fix (Finding #6, best-option implementation):
    previously decoded a stateless refresh JWT and reissued a new access
    token while leaving the SAME refresh token in place for its full
    7-day life — no rotation, no way to revoke a specific session, no way
    to detect a stolen refresh token being replayed.

    Now calls auth_service.rotate_refresh_token(), which:
      - validates the presented token against the DB-backed RefreshToken
        table (see models.py for the full design),
      - if valid: revokes it and issues a brand-new refresh token in the
        same rotation family, returned here and set as the new cookie
        alongside a new access token,
      - if the token was already revoked and reuse is detected OUTSIDE a
        short (10s) grace window: treats this as a compromise signal,
        revokes the ENTIRE token family server-side, and raises — forcing
        this and every other session descended from that original login
        to re-authenticate via a fresh login.
      - if the token was already revoked but reuse falls WITHIN that
        grace window and a valid successor token exists: treated as a
        legitimate multi-tab race (two tabs refreshing around the same
        moment), not theft — a working access token is returned but no
        new refresh-token cookie is set (see rotate_refresh_token's own
        docstring for the full reasoning; this is what makes
        new_refresh_token possibly None below).

    Access-token issuance for a rotated refresh goes through the same
    _build_access_token() helper used by a fresh login
    (helpers.generate_tokens_for_user), resolving what used to be a
    second, independently hand-rolled payload dict in this route that had
    already silently drifted from the login path once.
    """
    from errors import ValidationError

    try:
        # FIX: renamed local var to avoid shadowing the function name
        refresh_tok = request.cookies.get("refresh_token")
        if not refresh_tok:
            return error_response("Refresh token not found")

        try:
            user, new_refresh_token = auth_service.rotate_refresh_token(refresh_tok)
        except ValidationError as e:
            # Invalid, expired, or reuse-detected (family revoked either
            # way) — the client cannot recover without a fresh login.
            # Clear cookies so the frontend's own state matches reality
            # rather than holding onto now-dead credentials.
            response = make_response(error_response(str(e)))
            clear_auth_cookies(response)
            return response

        if user.status != "approved":
            return error_response("Account not active")

        # Auth-flow-audit fix (Finding #7): now uses the same
        # _build_access_token() helper generate_tokens_for_user() uses for
        # a fresh login, instead of a second hand-rolled payload dict.
        # This also fixes a latent claim-shape drift the duplication had
        # already produced: this route's payload was missing the "name"
        # claim present in the login-issued token (nothing currently reads
        # payload["name"], so it was harmless today, but it's exactly the
        # kind of divergence that duplication risks and this collapses it
        # to one definition.
        new_access_token = _build_access_token(user)

        response = make_response(success_response(
            "Token refreshed",
            data={"user": {"id": user.id, "username": user.username, "email": user.email, "name": user.name}},
        ))
        # Refresh token IS now rotated on every use (see docstring above) —
        # set_auth_cookies is called with the new refresh token so the
        # rotated value replaces the old one in the client's cookie jar.
        #
        # Grace-window case: rotate_refresh_token() returns
        # new_refresh_token=None when this request landed in the
        # multi-tab-race grace window (see that function's docstring) —
        # set_auth_cookies already treats refresh_token=None as "leave the
        # existing refresh_token cookie alone," which is exactly correct
        # here: another tab already installed the current valid refresh
        # token for this family, and this response only needs to hand back
        # a working access token, not overwrite it again.
        set_auth_cookies(response, new_access_token, new_refresh_token)
        return response

    except Exception as e:
        current_app.logger.error(f"Token refresh error: {str(e)}")
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
