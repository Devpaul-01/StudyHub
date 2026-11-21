"""
StudyHub - User Profile Management
Handles profile viewing, editing, stats, badges, and customization
"""

from flask import Blueprint, request, jsonify, current_app, render_template
from werkzeug.utils import secure_filename
from sqlalchemy import func, desc, or_, and_          # FIX: added and_
from sqlalchemy.orm import joinedload                  # FIX: added for N+1 elimination
import os
import datetime
from flask import session

from models import (
    User, StudentProfile, Post, Comment, ThreadJoinRequest , Thread, ThreadMember,
    UserBadge, Badge, ReputationHistory, UserActivity, Connection, Notification,
    PostReaction, Bookmark, OnboardingDetails,
)
from extensions import db
from errors import ValidationError
from routes.student.helpers import (
    token_required, success_response, error_response,
    save_file, ALLOWED_IMAGE_EXT,
)
# Phase 5b (Document 4 §1): PUBLIC_READ on profile/stats reads (own and
# others'), WRITE_HEAVY on edits (academic info, avatar, update, skills,
# goals, pin/unpin, study schedule, visibility settings), BURST_OK on
# avatar delete (cheap state change).
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key, ip_key

profile_bp = Blueprint("student_profile", __name__)


# Privacy settings a user may change, with the value used when a key has
# never been stored. Only keys listed here are accepted by the API.
PRIVACY_SETTING_DEFAULTS = {
    "set_profile_private":   False,
    "show_active_status":    True,
    "show_in_activity_feed": True,
}
# Keys older clients may still send. They no longer do anything and are
# ignored rather than rejected so a stale browser tab keeps working.
_LEGACY_PRIVACY_KEYS = frozenset({"set_dark_mode", "send_weekly_notification"})


def _server_error(log_label, message="Something went wrong. Please try again."):
    """Roll back, log the full traceback and return a 500 response.

    Unexpected exceptions are server faults, not client mistakes, so they
    must be visible to 5xx monitoring and carry a stack trace in Sentry.
    """
    db.session.rollback()
    current_app.logger.exception(log_label)
    return error_response(message, 500)


# ============================================================================
# PROFILE DATA — own profile
# ============================================================================

@profile_bp.route("/profile/me/data", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_my_profile_data(current_user):
    """Return full profile data for the logged-in user's own profile view."""
    try:
        profile    = StudentProfile.query.filter_by(user_id=current_user.id).first()
        onboarding = OnboardingDetails.query.filter_by(user_id=current_user.id).first()

        connections_count = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id  == current_user.id,
            ),
            Connection.status == "accepted",
        ).count()

        return jsonify({
            "status": "success",
            "data": {
                "id":               current_user.id,
                "name":             current_user.name,
                "username":         current_user.username,
                "bio":              current_user.bio,
                "avatar":           current_user.avatar,
                "department":       profile.department if profile else None,
                "class_level":      profile.class_name if profile else None,
                "reputation":       current_user.reputation,
                "reputation_level": current_user.reputation_level,
                "login_streak":     current_user.login_streak,
                "joined_at":        current_user.joined_at.isoformat(),
                "last_active": (
                    current_user.last_active.isoformat()
                    if current_user.last_active else None
                ),
                "stats": {
                    "total_posts":       current_user.total_posts,
                    "total_helpful":     current_user.total_helpful,
                    "total_helps_given": current_user.total_helps_given,
                    "connections_count": connections_count,
                },
                "learning_goals": current_user.learning_goals or [],
                "help_streak": {
                    "current": current_user.help_streak_current,
                    "longest": current_user.help_streak_longest,
                },
                "onboarding": {
                    "subjects":           onboarding.subjects          if onboarding else [],
                    "strong_subjects":    onboarding.strong_subjects   if onboarding else [],
                    "help_subjects":      onboarding.help_subjects     if onboarding else [],
                    "learning_style":     onboarding.learning_style    if onboarding else None,
                    "study_preferences":  onboarding.study_preferences if onboarding else [],
                    "class_level":        onboarding.class_level       if onboarding else None,
                } if onboarding else None,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get my profile data error: {str(e)}")
        return error_response("Failed to load profile data")


# ============================================================================
# MY POSTS
# ============================================================================

@profile_bp.route("/profile/my-posts", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_my_posts(current_user):
    """
    Returns current user's posts.
    Query params: ?type=all | pinned | questions | resources | discussions
    """
    try:
        post_type_filter = request.args.get("type", "all").strip()

        query = Post.query.filter_by(student_id=current_user.id)

        if post_type_filter == "pinned":
            query = query.filter(Post.is_pinned == True)
        elif post_type_filter == "questions":
            query = query.filter(Post.post_type.in_(["question", "problem"]))
        elif post_type_filter == "resources":
            query = query.filter(Post.post_type == "resource")
        elif post_type_filter == "discussions":
            query = query.filter(Post.post_type == "discussion")

        posts = query.order_by(
            Post.is_pinned.desc(),
            Post.posted_at.desc(),
        ).limit(30).all()

        if not posts:
            return jsonify({
                "status": "success",
                "data": {"posts": [], "total": 0, "filter": post_type_filter},
            })

        post_ids = [p.id for p in posts]

        # ── 1. Reactions (need full row for reaction_type) ──────────────────
        reactions_map = {
            r.post_id: r
            for r in PostReaction.query.filter(
                PostReaction.student_id == current_user.id,
                PostReaction.post_id.in_(post_ids),
            ).all()
        }

        # ── 1b. Viewer's bookmarks (one batched query) ───────────────────────
        bookmarks_map = {
            b.post_id: b
            for b in Bookmark.query.filter(
                Bookmark.student_id == current_user.id,
                Bookmark.post_id.in_(post_ids),
            ).all()
        }

        # ── 2. Thread info ───────────────────────────────────────────────────
        thread_enabled_ids = [p.id for p in posts if p.thread_enabled]
        threads_map      = {}   # post_id  -> Thread
        thread_join_map  = {}   # thread_id -> join status
        thread_member_set = set()  # thread_ids where user is member

        if thread_enabled_ids:
            threads = Thread.query.filter(
                Thread.post_id.in_(thread_enabled_ids)
            ).all()
            threads_map = {t.post_id: t for t in threads}

            thread_ids = [t.id for t in threads]
            if thread_ids:
                from models import ThreadJoinRequest  # already imported if at top
                join_reqs = ThreadJoinRequest.query.filter(
                    ThreadJoinRequest.thread_id.in_(thread_ids),
                    ThreadJoinRequest.requester_id == current_user.id,
                ).all()
                thread_join_map = {jr.thread_id: jr.status for jr in join_reqs}

                members = ThreadMember.query.filter(
                    ThreadMember.thread_id.in_(thread_ids),
                    ThreadMember.student_id == current_user.id,
                ).all()
                thread_member_set = {m.thread_id for m in members}

        # ── Assemble ─────────────────────────────────────────────────────────
        posts_data = []
        for p in posts:
            reaction     = reactions_map.get(p.id)
            thread       = threads_map.get(p.id) if p.thread_enabled else None
            thread_id    = thread.id if thread else None
            requested_thread  = thread_join_map.get(thread.id) if thread else None
            is_member    = (thread.id in thread_member_set) if thread else False

            posts_data.append({
                "id":           p.id,
                "title":        p.title,
                "text_content": (
                    (p.text_content[:150] + "…")
                    if p.text_content and len(p.text_content) > 150
                    else p.text_content
                ),
                "post_type":       p.post_type,
                "department":      p.department,
                "tags":            p.tags or [],
                "resources":       p.resources or [],          # full array, not bool
                "thread_enabled":  bool(p.thread_enabled),
                "thread_id":       thread_id,
                "is_pinned":       bool(p.is_pinned),
                "is_solved":       bool(p.is_solved),
                "likes_count":     p.positive_reactions_count or 0,
                "comments_count":  p.comments_count or 0,
                "views_count":     p.views_count or 0,         # renamed from "views"
                "bookmark_count":  p.bookmark_count or 0,
                "posted_at":       p.posted_at.isoformat(),
                "is_author":       True,                       # always true on this endpoint
                "user_interactions": {
                    "user_reacted":       bool(reaction),
                    "reaction_type":      reaction.reaction_type if reaction else None,
                    "user_followed":      False,               # own posts — not applicable
                    "requested_thread":   requested_thread,
                    "is_thread_member":   is_member,
                    "bookmarked":         p.id in bookmarks_map,
                    "bookmark_folder_id": (
                        bookmarks_map[p.id].folder_id if p.id in bookmarks_map else None
                    ),
                },
            })

        return jsonify({
            "status": "success",
            "data": {
                "posts":  posts_data,
                "total":  len(posts_data),
                "filter": post_type_filter,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get my posts error: {str(e)}")
        return error_response("Failed to load posts")

# ============================================================================
# MY STATS
# ============================================================================

@profile_bp.route("/profile/my-stats", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_my_stats(current_user):
    """Comprehensive stats for the Stats tab."""
    try:
        threads_joined  = ThreadMember.query.filter_by(student_id=current_user.id).count()
        threads_created = Thread.query.filter_by(creator_id=current_user.id).count()

        total_comments    = Comment.query.filter_by(student_id=current_user.id).count()
        questions_solved  = Comment.query.filter_by(student_id=current_user.id, is_solution=True).count()
        questions_answered = Comment.query.join(Post).filter(
            Comment.student_id == current_user.id,
            Post.post_type.in_(["question", "problem"]),
        ).count()

        resources_shared = Post.query.filter_by(
            student_id=current_user.id, post_type="resource"
        ).count()

        connections_count = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id  == current_user.id,
            ),
            Connection.status == "accepted",
        ).count()

        reactions_received = db.session.query(func.count(PostReaction.id)).join(
            Post, PostReaction.post_id == Post.id
        ).filter(Post.student_id == current_user.id).scalar() or 0

        # "Marked helpful" = how many of this user's comments other users
        # marked helpful; maintained on User.total_helpful by
        # posts/comments.py::mark_comment_helpful.
        helpful_received = current_user.total_helpful or 0

        return jsonify({
            "status": "success",
            "data": {
                "posts": {
                    "total":           current_user.total_posts,
                    "resources_shared": resources_shared,
                },
                "engagement": {
                    "total_comments":    total_comments,
                    "reactions_received": reactions_received,
                    "helpful_received":   helpful_received,
                    "questions_answered": questions_answered,
                    "questions_solved":   questions_solved,
                },
                "threads": {
                    "joined":  threads_joined,
                    "created": threads_created,
                },
                "help": {
                    "total_helps_given":    current_user.total_helps_given,
                    "total_helps_received": current_user.total_helps_received,
                    "streak_current":       current_user.help_streak_current,
                    "streak_longest":       current_user.help_streak_longest,
                    "total_helpful":        current_user.total_helpful,
                },
                "connections": connections_count,
                "reputation": {
                    "points":       current_user.reputation,
                    "level":        current_user.reputation_level,
                    "login_streak": current_user.login_streak,
                },
            },
        })

    except Exception as e:
        current_app.logger.error(f"My stats error: {str(e)}")
        return error_response("Failed to load stats")


# ============================================================================
# ACADEMIC INFO
# ============================================================================

@profile_bp.route("/profile/academic-info", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_academic_info(current_user):
    """Returns the current user's academic / onboarding details."""
    try:
        onboarding = OnboardingDetails.query.filter_by(user_id=current_user.id).first()
        profile    = StudentProfile.query.filter_by(user_id=current_user.id).first()

        if not onboarding:
            return jsonify({
                "status": "success",
                "data": {
                    "department":        profile.department if profile else None,
                    "class_level":       profile.class_name if profile else None,
                    "subjects":          [],
                    "strong_subjects":   [],
                    "help_subjects":     [],
                    "learning_style":    None,
                    "study_preferences": [],
                },
            })

        return jsonify({
            "status": "success",
            "data": {
                "department":        onboarding.department,
                "class_level":       onboarding.class_level,
                "subjects":          onboarding.subjects          or [],
                "strong_subjects":   onboarding.strong_subjects   or [],
                "help_subjects":     onboarding.help_subjects     or [],
                "learning_style":    onboarding.learning_style,
                "study_preferences": onboarding.study_preferences or [],
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get academic info error: {str(e)}")
        return error_response("Failed to load academic info")


@profile_bp.route("/profile/academic-info", methods=["PUT"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def update_academic_info(current_user):
    """Update academic / onboarding details."""
    try:
        data = request.get_json(silent=True)
        if not data:
            return error_response("No data provided")

        onboarding = OnboardingDetails.query.filter_by(user_id=current_user.id).first()
        if not onboarding:
            return error_response("Academic profile not found")

        changes = []

        if "subjects" in data and isinstance(data["subjects"], list):
            onboarding.subjects = data["subjects"][:15]
            changes.append("subjects")

        if "strong_subjects" in data and isinstance(data["strong_subjects"], list):
            onboarding.strong_subjects = data["strong_subjects"][:10]
            changes.append("strong_subjects")

        if "help_subjects" in data and isinstance(data["help_subjects"], list):
            onboarding.help_subjects = data["help_subjects"][:10]
            changes.append("help_subjects")

        if "learning_style" in data and data["learning_style"]:
            onboarding.learning_style = str(data["learning_style"])[:300]
            changes.append("learning_style")

        if "study_preferences" in data and isinstance(data["study_preferences"], list):
            onboarding.study_preferences = data["study_preferences"][:10]
            changes.append("study_preferences")

        onboarding.last_updated = datetime.datetime.utcnow()
        db.session.commit()

        return success_response("Academic info updated", data={"changes": changes})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update academic info error: {str(e)}")
        return error_response("Failed to update academic info")


# ============================================================================
# AVATAR
# ============================================================================

@profile_bp.route("/profile/avatar/upload", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def upload_avatar(current_user):
    """Upload profile picture to Cloudinary (or local fallback)."""
    try:
        try:
            from services.storage import cloudinary_storage, filename_service
            CLOUD_AVAILABLE = True
        except ImportError:
            CLOUD_AVAILABLE = False

        if "avatar" not in request.files:
            return error_response("No file provided")

        file = request.files["avatar"]
        if not file or not file.filename:
            return error_response("Invalid file")

        filename = secure_filename(file.filename)
        allowed  = {"jpg", "jpeg", "png", "gif", "webp"}
        ext      = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in allowed:
            return error_response(f"Invalid file type. Allowed: {', '.join(allowed)}")

        # Document 3 §3: cheap extension check above is early rejection
        # only — validate_and_normalize_image() is what makes an SVG (or
        # anything else) mislabeled as .jpg structurally impossible to
        # sneak through, since it only ever hands Cloudinary bytes that
        # Pillow itself decoded and re-encoded from a genuine raster image.
        from services.upload_validation_service import validate_and_normalize_image
        try:
            clean_file = validate_and_normalize_image(file)
        except ValidationError as e:
            return error_response(str(e))

        if CLOUD_AVAILABLE:
            folder             = f"avatars/{current_user.id}"
            generated_filename = f"avatar_{current_user.id}"
            result = cloudinary_storage.upload_file(
                clean_file, folder, generated_filename, resource_type="image"
            )
            if not result.get("success"):
                return error_response(f"Upload failed: {result.get('error', 'Unknown error')}")
            avatar_url = result["url"]
        else:
            avatar_url = save_file(file, "avatars", ALLOWED_IMAGE_EXT)
            avatar_url = f"/static/upload/avatars/{avatar_url}"

        current_user.avatar = avatar_url
        db.session.commit()

        return success_response("Avatar updated successfully", data={"avatar_url": avatar_url})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Avatar upload error: {str(e)}")
        return error_response("Failed to upload avatar")


@profile_bp.route("/profile/avatar", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def remove_avatar(current_user):
    """Remove profile picture (reset to default)."""
    try:
        current_user.avatar = None
        db.session.commit()
        return success_response("Avatar removed")
    except Exception as e:
        db.session.rollback()   # FIX: was missing rollback
        current_app.logger.error(f"Remove avatar error: {str(e)}")
        return error_response("Failed to remove avatar")


# ============================================================================
# PUSH NOTIFICATIONS (Batch 5)
# ============================================================================
#
# User.fcm_token (models.py) was read extensively by
# services/push_notifications.py and by every existing push-notification
# call site (e.g. routes/student/connections/crud.py's connection-request
# alerts) — but nothing anywhere ever WROTE to it, so every one of those
# existing calls has always sent to an empty token and done nothing. This
# is the missing write side; see app.py for the matching fix that makes
# PushNotificationService.initialize() actually get called at startup,
# since a saved token alone still wouldn't be enough without that.

@profile_bp.route("/profile/fcm-token", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def save_fcm_token(current_user):
    """
    Save (or update) the current device's FCM token for push notifications.

    Body: { "token": "<fcm token string>" }

    One column, one token: this only supports a single device per
    account, the same limitation the column itself already had (it's a
    single db.Column, not a related table) — saving a new token from a
    second device silently replaces the first device's token, the same
    way logging into any single-session system elsewhere in this app
    already works. A real multi-device upgrade would need a separate
    table and is out of scope here; this fixes the "nothing writes this
    at all" gap, not the one-token-per-account design.
    """
    try:
        data = request.get_json(silent=True) or {}
        token = (data.get("token") or "").strip()

        if not token:
            return error_response("A token is required")
        if len(token) > 500:  # matches User.fcm_token's db.String(500)
            return error_response("Token is too long")

        current_user.fcm_token = token
        db.session.commit()
        return success_response("Push notification token saved")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Save FCM token error: {str(e)}")
        return error_response("Failed to save push notification token")


@profile_bp.route("/profile/fcm-token", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def clear_fcm_token(current_user):
    """Clear this account's saved FCM token (e.g. on logout or opt-out)."""
    try:
        current_user.fcm_token = None
        db.session.commit()
        return success_response("Push notification token cleared")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Clear FCM token error: {str(e)}")
        return error_response("Failed to clear push notification token")


# ============================================================================
# OWN PROFILE PAGE
# ============================================================================

@profile_bp.route("/profile/me", methods=["GET"])
@token_required
def own_profile_page(current_user):
    """Serve the app shell opened on the profile tab.

    Batch 1 fix: "profile/profile.html" doesn't exist under templates/.
    post/feed2.html is the single-page app shell used for every other page
    (see /profile/homepage below); static/js/feed/feed.init.js checks for
    this exact path and opens straight to the profile tab. The profile data
    itself comes from GET /profile/me/data.
    """
    return render_template("post/feed2.html")


# ============================================================================
# VISIBILITY SETTINGS
# ============================================================================

@profile_bp.route("/profile/visibility-settings", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def visibility_settings(current_user):
    """Update privacy settings.

    Partial updates are supported: only the keys present in the body are
    changed, everything else keeps its stored value. Values must be real
    booleans.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("Request body must be a JSON object")

    unknown = [k for k in data if k not in PRIVACY_SETTING_DEFAULTS
               and k not in _LEGACY_PRIVACY_KEYS]
    if unknown:
        return error_response(f"Unknown setting: {str(unknown[0])[:50]}")

    updates = {}
    for key in PRIVACY_SETTING_DEFAULTS:
        if key in data:
            if not isinstance(data[key], bool):
                return error_response(f"{key} must be true or false")
            updates[key] = data[key]
    if not updates:
        return error_response("No settings provided")

    try:
        # Lock the row so two concurrent partial updates cannot overwrite
        # each other's keys (the whole JSON value is rewritten on save).
        user = (User.query.filter_by(id=current_user.id)
                .with_for_update().first())
        if not user:
            return error_response("User not found")

        privacy = dict(user.privacy_settings or {})
        privacy.update(updates)
        user.privacy_settings = privacy
        db.session.commit()
        return success_response("Visibility settings saved successfully")

    except Exception:
        return _server_error("Privacy settings error",
                             "Failed to set profile visibility settings")


@profile_bp.route("/profile/visibility-settings", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_visibility_settings(current_user):
    try:
        privacy = current_user.privacy_settings or {}
        return jsonify({
            "status": "success",
            "data": {
                key: bool(privacy.get(key, default))
                for key, default in PRIVACY_SETTING_DEFAULTS.items()
            },
        })

    except Exception:
        return _server_error("Profile visibility error",
                             "Failed to load profile visibility settings")


# ============================================================================
# VIEW ANOTHER USER'S PROFILE
# ============================================================================

@profile_bp.route("/profile/view/<username>", methods=["GET"])
@token_required
def view_profile_page(current_user, username):
    """
    Batch 4: standalone public-profile PAGE.

    GET /profile/<username> below is an existing, working JSON API route
    (not a page — it has no render_template call) that this project's own
    frontend already tries to navigate to for viewing another user's
    profile — see viewProfile() in static/js/feed/feed.utils.js, which
    sends the browser to /profile/<username> on a username click. That
    always 404'd for two separate reasons: it's missing the /student
    blueprint prefix this whole app is registered under, AND even with
    the correct prefix it would have hit this JSON API, returning raw
    data instead of a page.

    This route exists at a different path (/profile/view/<username>,
    not /profile/<username>) specifically so it doesn't collide with or
    change the existing JSON API's contract — anything else already
    calling GET /student/profile/<username> expecting JSON keeps working
    exactly as before. The page itself fetches that same JSON API via
    the browser's own request (see static/js/profile-view/
    profile-view.api.js), the same way search.html fetches search data.
    """
    return render_template("profile-view.html")


def _connection_status(viewer, other):
    """Relationship of ``viewer`` to ``other`` as a client-facing string."""
    if viewer.id == other.id:
        return "none"

    connection = Connection.query.filter(
        or_(
            and_(Connection.requester_id == viewer.id, Connection.receiver_id == other.id),
            and_(Connection.requester_id == other.id,  Connection.receiver_id == viewer.id),
        )
    ).first()
    if not connection:
        return "none"
    if connection.status == "accepted":
        return "connected"
    if connection.status == "blocked":
        return "blocked"
    if connection.status == "pending":
        if connection.requester_id == viewer.id:
            return "pending_sent"
        return "pending_received"
    return "none"


@profile_bp.route("/profile/<username>", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def view_profile(current_user, username):
    """View any user's profile.

    * Your own profile and public profiles return the full payload.
    * A private profile returns a short payload unless the viewer is an
      accepted connection of the owner.
    * If either user has blocked the other the profile is "not found".
    """
    try:
        user = User.query.filter_by(username=username).first()
        if not user:
            return error_response("User not found", 404)

        profile = StudentProfile.query.filter_by(user_id=user.id).first()
        if not profile:
            return error_response("Profile not found", 404)

        is_own_profile    = current_user.id == user.id
        connection_status = _connection_status(current_user, user)

        # A block hides the profile in both directions. Answer exactly as
        # for a missing user so the response does not confirm the block.
        if connection_status == "blocked":
            return error_response("User not found", 404)

        privacy         = user.privacy_settings or {}
        profile_private = bool(privacy.get("set_profile_private", False))
        show_active     = bool(privacy.get("show_active_status", True))

        # Decide visibility BEFORE running the expensive queries below.
        if profile_private and not is_own_profile and connection_status != "connected":
            pinned_posts = [
                {
                    "id":        p.id,
                    "title":     p.title,
                    "post_type": p.post_type,
                    "posted_at": p.posted_at.isoformat(),
                }
                for p in Post.query.filter_by(student_id=user.id, is_pinned=True).all()
            ]
            return jsonify({
                "status": "success",
                "message": "This profile is private — only a few details are visible to you.",
                "data": {
                    "type": "private",
                    "user": {
                        "id":          user.id,
                        "username":    user.username,
                        "name":        user.name,
                        "avatar":      user.avatar,
                        "department":  profile.department,
                        "class_level": profile.class_name,
                        "pinned_posts": pinned_posts,
                    },
                    "privacy": {
                        "is_private": True,
                        "message":    "This profile is private. Connect to view details.",
                    },
                    "connection_status": connection_status,
                    "last_active":       user.last_active.isoformat() if show_active and user.last_active else None,
                    "is_own_profile":    False,
                },
            })

        # ── Stats ────────────────────────────────────────────────────────────
        total_posts    = Post.query.filter_by(student_id=user.id).count()
        total_comments = Comment.query.filter_by(student_id=user.id).count()
        total_threads  = ThreadMember.query.filter_by(student_id=user.id).count()

        # ── Badges ───────────────────────────────────────────────────────────
        user_badges = (
            UserBadge.query
            .filter_by(user_id=user.id)
            .join(Badge)
            .order_by(UserBadge.is_featured.desc(), Badge.rarity.desc())
            .limit(6)
            .all()
        )
        badges_data = [
            {
                "id":          ub.badge.id,
                "name":        ub.badge.name,
                "description": ub.badge.description,
                "icon":        ub.badge.icon,
                "rarity":      ub.badge.rarity,
                "earned_at":   ub.earned_at.isoformat(),
                "is_featured": ub.is_featured,
            }
            for ub in user_badges
        ]

        # ── Recent posts ─────────────────────────────────────────────────────
        recent_posts = (
            Post.query
            .filter_by(student_id=user.id)
            .order_by(Post.is_pinned.desc(), Post.posted_at.desc())
            .limit(5)
            .all()
        )
        posts_data = [
            {
                "id":             p.id,
                "title":          p.title,
                "post_type":      p.post_type,
                "likes_count":    p.positive_reactions_count or 0,  # FIX: was p.likes_count
                "comments_count": p.comments_count,
                "posted_at":      p.posted_at.isoformat(),
                "is_solved":      p.is_solved,
            }
            for p in recent_posts
        ]

        active_threads = (
            db.session.query(Thread)
            .join(ThreadMember, Thread.id == ThreadMember.thread_id)
            .filter(ThreadMember.student_id == user.id)
            .order_by(Thread.last_activity.desc())
            .limit(5)
            .all()
        )
        threads_data = [
            {
                "id":            t.id,
                "title":         t.title,
                "member_count":  t.member_count,
                "is_creator":    t.creator_id == user.id,
                "last_activity": t.last_activity.isoformat(),
            }
            for t in active_threads
        ]

        return jsonify({
            "status": "success",
            "data": {
                "type": "public",
                "user": {
                    "id":               user.id,
                    "username":         user.username,
                    "name":             user.name,
                    "bio":              user.bio,
                    "avatar":           user.avatar,
                    "department":       profile.department,
                    "class_level":      profile.class_name,
                    "reputation":       user.reputation,
                    "reputation_level": user.reputation_level,
                    # Activity details are only sent when the owner allows
                    # it (own profile always sees their own values).
                    "login_streak":     user.login_streak if show_active or is_own_profile else None,
                    "joined_at":        user.joined_at.isoformat(),
                    "last_active":      (user.last_active.isoformat()
                                         if user.last_active and (show_active or is_own_profile) else None),
                    "active_status":    show_active,
                },
                "stats": {
                    "total_posts":          total_posts,
                    "total_comments":       total_comments,
                    "total_threads":        total_threads,
                    "total_helpful":        user.total_helpful,
                },
                "badges":           badges_data,
                "recent_posts":     posts_data,
                "active_threads":   threads_data,
                "skills":           user.skills or [],
                "learning_goals":   user.learning_goals or [],
                "connection_status": connection_status,
                "is_own_profile":   is_own_profile,
            },
        })

    except Exception:
        return _server_error("View profile error", "Failed to load profile")


# ============================================================================
# PROFILE EDITING
# ============================================================================

@profile_bp.route("/profile/update", methods=["PATCH"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def update_profile(current_user):
    """Update profile info: name, bio, department, class."""
    try:
        data    = request.get_json(silent=True) or request.form.to_dict()
        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()

        if not profile:
            return error_response("Profile not found", 404)

        changes = []

        if "name" in data and data["name"].strip():
            new_name = data["name"].strip()
            if len(new_name) < 3:
                return error_response("Name must be at least 3 characters")
            if new_name != current_user.name:
                current_user.name  = new_name
                profile.full_name  = new_name
                changes.append("name")

        if "bio" in data:
            new_bio = data["bio"].strip()
            if len(new_bio) > 500:
                return error_response("Bio must be less than 500 characters")
            if new_bio != current_user.bio:
                current_user.bio = new_bio
                changes.append("bio")

        if "department" in data and data["department"].strip():
            new_dept = data["department"].strip()
            if new_dept != profile.department:
                profile.department = new_dept
                changes.append("department")

        if "class_level" in data and data["class_level"].strip():
            new_class = data["class_level"].strip()
            if new_class != profile.class_name:
                profile.class_name = new_class
                changes.append("class_level")

        if changes:
            db.session.commit()
            return success_response(
                "Profile updated successfully",
                data={
                    "changes": changes,
                    "user": {
                        "name":       current_user.name,
                        "bio":        current_user.bio,
                        "department": profile.department,
                        "class_level": profile.class_name,
                    },
                },
            )

        return success_response("No changes made")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Profile update error: {str(e)}")
        return error_response("Failed to update profile")


# ============================================================================
# SKILLS & LEARNING GOALS
# ============================================================================

@profile_bp.route("/profile/skills", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def add_skill(current_user):
    """Add a skill to profile. Body: {"skill": "Python"}"""
    try:
        data  = request.get_json()
        skill = data.get("skill", "").strip()

        if not skill:
            return error_response("Skill name required")
        if len(skill) > 50:
            return error_response("Skill name too long (max 50 chars)")

        skills = current_user.skills or []

        if any(s.lower() == skill.lower() for s in skills):
            return error_response("Skill already added")
        if len(skills) >= 10:
            return error_response("Maximum 10 skills allowed")

        skills.append(skill)
        current_user.skills = skills
        db.session.commit()

        return success_response("Skill added", data={"skills": skills}), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Add skill error: {str(e)}")
        return error_response("Failed to add skill")


@profile_bp.route("/profile/skills/<skill_name>", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def remove_skill(current_user, skill_name):
    """Remove a skill from profile."""
    try:
        skills = [s for s in (current_user.skills or []) if s.lower() != skill_name.lower()]
        current_user.skills = skills
        db.session.commit()
        return success_response("Skill removed", data={"skills": skills})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Remove skill error: {str(e)}")
        return error_response("Failed to remove skill")


@profile_bp.route("/profile/learning-goals", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def add_learning_goal(current_user):
    """Add learning goal. Body: {"goal": "Learn Machine Learning"}"""
    try:
        data = request.get_json()
        goal = data.get("goal", "").strip()

        if not goal:
            return error_response("Goal required")
        if len(goal) > 100:
            return error_response("Goal too long (max 100 chars)")

        goals = current_user.learning_goals or []

        if goal in goals:
            return error_response("Goal already added")
        if len(goals) >= 5:
            return error_response("Maximum 5 learning goals allowed")

        goals.append(goal)
        current_user.learning_goals = goals
        db.session.commit()

        return success_response("Learning goal added", data={"learning_goals": goals}), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Add goal error: {str(e)}")
        return error_response("Failed to add goal")


@profile_bp.route("/profile/learning-goals/<int:index>", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def remove_learning_goal(current_user, index):
    """Remove learning goal by index."""
    try:
        goals = current_user.learning_goals or []

        if index < 0 or index >= len(goals):
            return error_response("Invalid goal index")

        goals.pop(index)
        current_user.learning_goals = goals
        db.session.commit()

        return success_response("Learning goal removed", data={"learning_goals": goals})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Remove goal error: {str(e)}")
        return error_response("Failed to remove goal")


# ============================================================================
# PIN / UNPIN POSTS
# ============================================================================

@profile_bp.route("/profile/pin-post/<int:post_id>", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def pin_post(current_user, post_id):
    """Pin a post to profile."""
    try:
        post         = Post.query.filter_by(id=post_id, student_id=current_user.id).first()
        total_pinned = Post.query.filter_by(student_id=current_user.id, is_pinned=True).count()

        if not post:
            return error_response("Post not found or not yours")
        if post.is_pinned:
            return success_response("Post has already been pinned")
        if total_pinned >= 5:
            return error_response("You can't pin more than 5 posts — unpin one first")

        post.is_pinned = True
        db.session.commit()
        return success_response("Post pinned successfully")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Pin post error: {str(e)}")
        return error_response("Failed to pin post")


@profile_bp.route("/profile/unpin-post/<int:post_id>", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def unpin_post(current_user, post_id):
    """Unpin a post from profile."""
    try:
        post = Post.query.filter_by(id=post_id, student_id=current_user.id).first()
        if not post:
            return error_response("Post not found or not yours")

        post.is_pinned = False
        db.session.commit()
        return success_response("Post unpinned successfully")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Unpin post error: {str(e)}")
        return error_response("Failed to unpin post")


# ============================================================================
# STUDY SCHEDULE
# ============================================================================

@profile_bp.route("/profile/study-schedule", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_study_schedule(current_user):
    """Get user's study schedule."""
    schedule = current_user.study_schedule or {}

    default_schedule = {
        "monday": [], "tuesday": [], "wednesday": [],
        "thursday": [], "friday": [], "saturday": [], "sunday": [],
    }

    return jsonify({
        "status": "success",
        "data": {"study_schedule": {**default_schedule, **schedule}},
    })


@profile_bp.route("/profile/study-schedule", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def update_study_schedule(current_user):
    """Update study schedule."""
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            return error_response("Schedule must be an object")

        valid_days  = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
        valid_times = {"morning", "afternoon", "evening", "night"}

        schedule = {}
        for day, times in data.items():
            day_lower = day.lower()
            if day_lower not in valid_days:
                return error_response(f"Invalid day: {day}")
            if not isinstance(times, list):
                return error_response(f"Times for {day} must be a list")
            for t in times:
                if t not in valid_times:
                    return error_response(f"Invalid time slot: {t}")
            schedule[day_lower] = times

        current_user.study_schedule = schedule
        db.session.commit()

        return success_response("Study schedule updated", data={"study_schedule": schedule})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update study schedule error: {str(e)}")
        return error_response("Failed to update study schedule")


# ============================================================================
# POPULAR SKILLS (public endpoint)
# ============================================================================

@profile_bp.route("/skills/popular", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
def get_popular_skills():
    """Get list of popular skills across all users."""
    try:
        users = User.query.filter(User.skills.isnot(None)).all()

        skill_counts = {}
        for u in users:
            for skill in (u.skills or []):
                key = skill.lower()
                if key in skill_counts:
                    skill_counts[key]["count"] += 1
                else:
                    skill_counts[key] = {"name": skill, "count": 1}

        popular = sorted(skill_counts.values(), key=lambda x: x["count"], reverse=True)[:50]

        return jsonify({
            "status": "success",
            "data": {
                "skills":   [s["name"] for s in popular],
                "detailed": popular,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get popular skills error: {str(e)}")
        return error_response("Failed to load skills")


# ============================================================================
# HOMEPAGE
# ============================================================================

@profile_bp.route("/profile/homepage", methods=["GET"])
def homepage():
    return render_template("post/feed2.html")
