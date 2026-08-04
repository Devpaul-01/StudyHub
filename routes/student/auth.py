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
from utils import generate_verification_token, send_password_reset, send_verification_email
from .helpers import (
    generate_tokens_for_user, decode_token, verify_token, token_required,
    success_response, error_response, set_auth_cookies, clear_auth_cookies,
)
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

google_bp = make_google_blueprint(
    client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID") or CLIENT_ID,
    client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or CLIENT_SECRET,
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
      2. Partially set up → complete-registration (set username/password)
      3. Brand-new user   → create account, go to onboarding
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

        if not email:
            return redirect(url_for("student.student_auth.login") + "?error=oauth_failed")

        # ── 1. Existing user ─────────────────────────────────────────────────
        existing_user = User.query.filter_by(email=email).first()

        if existing_user:
            if existing_user.status == "approved":
                existing_user = auth_service.record_login_and_commit(existing_user)

                access_token, refresh_token_val = generate_tokens_for_user(existing_user)
                response = make_response(redirect("/student/profile/homepage"))
                set_auth_cookies(response, access_token, refresh_token_val)
                current_app.logger.info(f"Google login: existing user {email}")
                return response

            current_app.logger.info(f"Google login: incomplete user {email}, redirecting to complete-registration")
            return redirect(f"/student/complete-registration?email={email}")

        # ── 2. Brand-new user ─────────────────────────────────────────────────
        # FIX: use dict() copies so each user gets independent mutable dicts
        new_user = User(
            name=name,
            email=email,
            role="student",
            pin="PENDING_VERIFICATION",
            status="pending_onboarding",
            email_verified=True,
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
    session.pop("google_email", None)
    session.pop("google_name", None)
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

        db.session.commit()

        access_token, refresh_token = generate_tokens_for_user(user)

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
        google_verified = bool(data.get("google_verified", False))

        if not all([full_name, email]):
            return error_response("All fields are required")
        if not is_valid_email(email):
            return error_response("Invalid email format")

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            current_app.logger.error(f"Email {email} already exists")
            return error_response("Email already registered")

        # FIX: dict() copies so each user gets independent mutable dicts
        new_user = User(
            name=full_name,
            email=email,
            role="student",
            pin="PENDING_VERIFICATION",
            status="pending_onboarding" if google_verified else "pending_verification",
            email_verified=google_verified,
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
            session.pop("google_email", None)
            session.pop("google_name", None)
            token_for_setup = generate_verification_token(email)
            redirect_url = (
                f"/student/complete-registration?token={token_for_setup}"
                if token_for_setup else "/student/complete-registration"
            )
            return success_response(
                "Account created! Let's set up your profile.",
                data={
                    "google_verified": True,
                    "redirect_url": redirect_url,
                },
            )

        token = generate_verification_token(email)
        if not token:
            return error_response("Error generating verification token")

        verification_url = url_for("student.student_auth.verify_email_api", token=token, _external=True)
        send_verification_email(email, verification_url)

        return success_response("Registration successful! Check your email for verification link.")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Registration error: {str(e)}")
        return error_response("Registration failed. Please try again.")   # FIX: no str(e) leak


# ============================================================================
# LOGIN
# ============================================================================
@auth_bp.route("/login", methods=["GET", "POST"])
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
def validate_user():
    """
    Validate user and send password reset email.

    Document 3 §4: issues an opaque, DB-backed PasswordResetToken
    (services/auth_service.py::issue_password_reset_token) instead of the
    shared stateless JWT — this flow is now revocable and single-use.
    Email verification (register/verify-email) is unaffected and keeps
    using generate_verification_token/verify_token, per §4's explicit
    scoping ("no comparable account-takeover risk from a verification
    link being revocable, so adding statefulness there is unnecessary").
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

    reset_row = PasswordResetToken.query.filter_by(token=token).first()
    if not reset_row or not reset_row.is_valid():
        return error_response("This password reset link is invalid or has expired.")

    user = User.query.get(reset_row.user_id)
    if not user:
        return error_response("User not found")

    return success_response(
        "Password Reset Link Verified!",
        data={
            "email": user.email,
            "token": token,
            "redirect_url": f"/student/set-password?token={token}",
        },
    )


@auth_bp.route("/verify-email/<token>", methods=["GET", "POST"])
def verify_email_api(token):
    """API endpoint for email verification."""
    if request.method == "GET":
        return render_template("auth/verify-email.html")

    try:
        email = verify_token(token)
        if isinstance(email, dict) and "error" in email:
            return error_response(email["error"])

        user = User.query.filter_by(email=email).first()
        if not user:
            return error_response("User not found")

        if user.email_verified and user.status == "approved":
            return success_response(
                "Email already verified!",
                data={
                    "email": email,
                    "token": token,
                    "already_verified": True,
                },
            )

        user.email_verified = True
        db.session.commit()

        return success_response(
            "Email verified successfully!",
            data={
                "email": email,
                "token": token,
                "redirect_url": f"/student/complete-registration?token={token}",
            },
        )

    except Exception as e:
        current_app.logger.error(f"Verification error: {str(e)}")
        return error_response("Verification failed. Please try again.")


@auth_bp.route("/check-username", methods=["POST"])
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
def complete_registration():
    """Complete registration with username and password."""
    if request.method == "GET":
        return render_template("auth/complete-registration.html")

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
        if password != confirm_password:
            return error_response("Passwords do not match")
        if len(password) < 6:
            return error_response("Password must be at least 6 characters")
        if not re.match(r"^[a-z0-9]{3,20}$", username):
            return error_response("Username must be 3-20 lowercase letters and numbers only")

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

        return success_response(
            f"Registration complete! Welcome, @{username}!",
            data={"username": username},
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Complete registration error: {str(e)}")
        return error_response("Registration failed. Please try again.")   # FIX: no str(e) leak


@auth_bp.route("/reset-password", methods=["GET"])
def reset_password():
    """Render password reset request page."""
    return render_template("auth/reset_request.html")


@auth_bp.route("/set-password", methods=["GET", "POST"])
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

    data             = request.get_json() or {}
    token            = data.get("token") or request.args.get("token")
    password         = data.get("password", "")
    confirm_password = data.get("confirm_password", "")

    if not token:
        return error_response("Reset token is required")
    if not all([password, confirm_password]):
        return error_response("All fields are required")
    if password != confirm_password:
        return error_response("Passwords do not match")
    if len(password) < 6:
        return error_response("Password must be at least 6 characters")

    try:
        # consume_password_reset_token commits internally (Document 2 §5's
        # documented second exception) the moment the token is marked used,
        # independent of whatever finalize_password does below.
        reset_user = auth_service.consume_password_reset_token(token)
        user = auth_service.finalize_password(reset_user.email, password)
    except LookupError as e:
        return error_response(str(e))
    except ValidationError as e:
        return error_response(str(e))

    db.session.commit()
    return success_response(
        f"Password reset complete, @{user.username}!",
        data={"redirect_url": "/student/login"},
    )


@auth_bp.route("/refresh-token", methods=["POST"])
def refresh_token():
    """Refresh access token using the refresh-token cookie."""
    try:
        # FIX: renamed local var to avoid shadowing the function name
        refresh_tok = request.cookies.get("refresh_token")
        if not refresh_tok:
            return error_response("Refresh token not found")

        try:
            payload = decode_token(refresh_tok)
        except jwt.ExpiredSignatureError:
            return error_response("Refresh token expired. Please login again.")
        except jwt.InvalidTokenError:
            return error_response("Invalid refresh token")

        user = User.query.get(payload.get("user_id"))
        if not user or user.status != "approved":
            return error_response("Account not active")

        secret = os.environ.get("SECRET_KEY")
        access_payload = {
            "user_id":  user.id,
            "email":    user.email,
            "role":     user.role,
            "username": user.username,
            "exp":      datetime.datetime.utcnow() + datetime.timedelta(minutes=30),  # FIX: was 50
        }
        new_access_token = jwt.encode(access_payload, secret, algorithm="HS256")
        if isinstance(new_access_token, bytes):
            new_access_token = new_access_token.decode("utf-8")

        response = make_response(success_response(
            "Token refreshed",
            data={"user": {"id": user.id, "username": user.username, "email": user.email, "name": user.name}},
        ))
        # Document 3 §1.2/§1.3: refresh_token itself is NOT rotated on every
        # refresh (confirmed out of scope for this phase — rotation-on-use
        # is a further hardening option, flagged for a future phase, not
        # required here). set_auth_cookies with no refresh_token argument
        # reissues access_token (+ csrf_token, since it's tied to
        # access_token's lifetime per §1.2's table) without touching the
        # existing refresh_token cookie.
        set_auth_cookies(response, new_access_token)
        return response

    except Exception as e:
        current_app.logger.error(f"Token refresh error: {str(e)}")
        return error_response("Token refresh failed")


@auth_bp.route("/verify-auth", methods=["GET"])
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


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    """Logout user."""
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

        if request.method == "GET":
            response = make_response(redirect(url_for("student.student_auth.login")))
        else:
            response = make_response(success_response("Logged out successfully"))

        clear_auth_cookies(response)
        return response

    except Exception as e:
        current_app.logger.error(f"Logout error: {str(e)}")
        return error_response("Logout failed")
