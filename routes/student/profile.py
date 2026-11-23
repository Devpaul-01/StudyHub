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
from services import cache_service
from services.profile_validation import (
    ProfileValidationError, STUDY_DAYS, STUDY_SLOTS, clean_bio, clean_class_level,
    clean_plain_text, clean_study_schedule, clean_text_list,
)
from routes.student.helpers import (
    token_required, success_response, error_response,
    save_file, ALLOWED_IMAGE_EXT,
)
# Phase 5b (Document 4 §1): PUBLIC_READ on profile/stats reads (own and
# others'), WRITE_HEAVY on edits (academic info, avatar, update, skills,
# goals, pin/unpin, study schedule, visibility settings), BURST_OK on
# avatar delete (cheap state change).
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key

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
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
def get_my_profile_data(current_user):
    """Return full profile data for the logged-in user's own profile view."""
    try:
        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()

        connections_count = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id  == current_user.id,
            ),
            Connection.status == "accepted",
        ).count()

        total_posts = Post.query.filter_by(student_id=current_user.id).count()

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
                "stats": {
                    # Live count, same source as the public profile, so the
                    # two can never disagree.
                    "total_posts":       total_posts,
                    "total_helpful":     current_user.total_helpful,
                    "connections_count": connections_count,
                },
                "skills":         current_user.skills or [],
                "learning_goals": current_user.learning_goals or [],
            },
        })

    except Exception:
        return _server_error("Get my profile data error", "Failed to load profile data")


# ============================================================================
# MY POSTS
# ============================================================================

@profile_bp.route("/profile/my-posts", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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

    except Exception:
        return _server_error("Get my posts error", "Failed to load posts")

# ============================================================================
# MY STATS
# ============================================================================

@profile_bp.route("/profile/my-stats", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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
                    "total":           Post.query.filter_by(student_id=current_user.id).count(),
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

    except Exception:
        return _server_error("My stats error", "Failed to load stats")


# ============================================================================
# ACADEMIC INFO
# ============================================================================

@profile_bp.route("/profile/academic-info", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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

    except Exception:
        return _server_error("Get academic info error", "Failed to load academic info")


# (field, max items kept, max characters per item). Lists longer than the
# cap are cut to the cap, as the API has always documented.
_ACADEMIC_LISTS = (
    ("subjects",          15, 50),
    ("strong_subjects",   10, 50),
    ("help_subjects",     10, 50),
    ("study_preferences", 10, 50),
)


@profile_bp.route("/profile/academic-info", methods=["PUT"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def update_academic_info(current_user):
    """Update academic / onboarding details.

    Every list item must be a short plain-text string: these values are
    read by other users' discovery and matching code, so one malformed
    entry must never be storable.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return error_response("No data provided")

    try:
        updates = {}
        for field, cap, item_len in _ACADEMIC_LISTS:
            if field in data:
                value = data[field]
                if not isinstance(value, list):
                    raise ProfileValidationError(f"{field} must be a list")
                updates[field] = clean_text_list(
                    value[:cap], field, max_items=cap, max_item_len=item_len)

        if "learning_style" in data:
            style = data["learning_style"]
            # An empty value (or null) clears the learning style.
            updates["learning_style"] = (
                None if style in (None, "")
                else clean_plain_text(style, "Learning style", max_len=300)
            )
    except ProfileValidationError as e:
        return error_response(str(e))

    try:
        onboarding = OnboardingDetails.query.filter_by(user_id=current_user.id).first()
        if not onboarding:
            return error_response("Academic profile not found")

        changes = list(updates)
        for field, value in updates.items():
            setattr(onboarding, field, value)

        if changes:
            onboarding.last_updated = datetime.datetime.utcnow()
            db.session.commit()

        return success_response("Academic info updated", data={"changes": changes})

    except Exception:
        return _server_error("Update academic info error", "Failed to update academic info")


# ============================================================================
# AVATAR
# ============================================================================

AVATAR_MAX_PIXELS    = 25_000_000   # reject anything bigger before decoding it
AVATAR_MAX_DIMENSION = 512          # avatars are shown at 28-144 px
AVATAR_UPLOAD_TIMEOUT = 20          # seconds to wait for Cloudinary


def _avatar_public_id(user_id):
    """Cloudinary public id used by upload_avatar (folder + file name)."""
    return f"avatars/{user_id}/avatar_{user_id}"


@profile_bp.route("/profile/avatar/upload", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def upload_avatar(current_user):
    """Upload a profile picture to Cloudinary.

    The image is validated, re-encoded by Pillow and shrunk to at most
    AVATAR_MAX_DIMENSION pixels before it is sent anywhere.
    """
    try:
        from services.storage import cloudinary_storage

        if "avatar" not in request.files:
            return error_response("No file provided")

        file = request.files["avatar"]
        if not file or not file.filename:
            return error_response("Invalid file")

        filename = secure_filename(file.filename)
        allowed  = {"jpg", "jpeg", "png", "gif", "webp"}
        ext      = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in allowed:
            return error_response(f"Invalid file type. Allowed: {', '.join(sorted(allowed))}")

        # Cheap extension check above is early rejection only —
        # validate_and_normalize_image() is what makes an SVG (or anything
        # else) mislabeled as .jpg structurally impossible to sneak
        # through, since it only ever hands Cloudinary bytes that Pillow
        # itself decoded and re-encoded from a genuine raster image.
        from services.upload_validation_service import validate_and_normalize_image
        try:
            clean_file = validate_and_normalize_image(
                file, max_pixels=AVATAR_MAX_PIXELS, max_dimension=AVATAR_MAX_DIMENSION)
        except ValidationError as e:
            return error_response(str(e))

        user_id = current_user.id
        # Nothing is pending, so this just ends the read transaction and
        # gives the pooled DB connection back before the (slow) network
        # call, so a slow Cloudinary cannot pin connections.
        db.session.commit()

        result = cloudinary_storage.upload_file(
            clean_file, f"avatars/{user_id}", f"avatar_{user_id}",
            resource_type="image", timeout=AVATAR_UPLOAD_TIMEOUT,
        )
        if not result.get("success"):
            # Provider messages can contain account details; log them, show
            # the user something generic.
            current_app.logger.error(
                "Avatar upload rejected by storage for user %s: %s", user_id, result.get("error"))
            return error_response("Could not upload the image. Please try again.", 502)
        avatar_url = result["url"]

        user = User.query.get(user_id)
        user.avatar = avatar_url
        db.session.commit()

        return success_response("Avatar updated successfully", data={"avatar_url": avatar_url})

    except Exception:
        return _server_error("Avatar upload error", "Failed to upload avatar")


@profile_bp.route("/profile/avatar", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def remove_avatar(current_user):
    """Remove the profile picture (reset to default) and delete the stored image."""
    try:
        had_avatar = bool(current_user.avatar)
        user_id    = current_user.id
        current_user.avatar = None
        db.session.commit()
    except Exception:
        return _server_error("Remove avatar error", "Failed to remove avatar")

    if had_avatar:
        # Best effort: the profile is already updated, so a storage hiccup
        # must not turn this into an error. The image stays reachable by its
        # URL until it is deleted, hence the attempt.
        try:
            from services.storage import cloudinary_storage
            result = cloudinary_storage.delete_file(_avatar_public_id(user_id), "image")
            if not result.get("success"):
                current_app.logger.warning(
                    "Could not delete avatar asset for user %s: %s", user_id, result.get("error"))
        except Exception:
            current_app.logger.exception("Avatar asset deletion failed for user %s", user_id)

    return success_response("Avatar removed")


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

    One column, one token per account (the column's own design), and a
    token belongs to one account: registering it detaches it from any
    other account that held it.
    """
    try:
        data = request.get_json(silent=True) or {}
        token = (data.get("token") or "").strip()

        if not token:
            return error_response("A token is required")
        if len(token) > 500:  # matches User.fcm_token's db.String(500)
            return error_response("Token is too long")

        # A device token belongs to exactly one account. If another account
        # on this device registered it earlier (shared computer, switching
        # accounts), detach it from them so their notifications stop
        # arriving here.
        User.query.filter(
            User.fcm_token == token, User.id != current_user.id,
        ).update({User.fcm_token: None}, synchronize_session=False)

        current_user.fcm_token = token
        db.session.commit()
        return success_response("Push notification token saved")
    except Exception:
        return _server_error("Save FCM token error", "Failed to save push notification token")


@profile_bp.route("/profile/fcm-token", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def clear_fcm_token(current_user):
    """Clear this account's saved FCM token (e.g. on logout or opt-out)."""
    try:
        current_user.fcm_token = None
        db.session.commit()
        return success_response("Push notification token cleared")
    except Exception:
        return _server_error("Clear FCM token error", "Failed to clear push notification token")


# ============================================================================
# OWN PROFILE PAGE
# ============================================================================

@profile_bp.route("/profile/me", methods=["GET"])
def own_profile_page():
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
        user = _locked_user(current_user.id)
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
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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
def view_profile_page(username):
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

    The shell contains no user data, so it is served without a token: a
    logged-out visitor gets the page, whose first API call returns 401 and
    sends them to the login screen (core/api.js) instead of showing raw JSON.
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
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
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
    """Update profile info: name, bio, department, class level.

    Only keys present in the body are changed. Text is validated as plain
    text (see services/profile_validation.py); department and class level
    are also mirrored onto the onboarding record so both copies agree.
    """
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    if not isinstance(data, dict):
        return error_response("Request body must be a JSON object")

    try:
        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
        if not profile:
            return error_response("Profile not found", 404)

        changes = []

        try:
            new_name = new_bio = new_dept = new_class = None
            if "name" in data:
                new_name = clean_plain_text(data["name"], "Name", min_len=3, max_len=80)
            if "bio" in data:
                new_bio = clean_bio(data["bio"], max_len=500)
            if "department" in data:
                new_dept = clean_plain_text(data["department"], "Department", max_len=50)
            if "class_level" in data:
                new_class = clean_class_level(data["class_level"])
        except ProfileValidationError as e:
            return error_response(str(e))

        if new_name is not None and new_name != current_user.name:
            current_user.name = new_name
            changes.append("name")

        if new_bio is not None and new_bio != (current_user.bio or ""):
            current_user.bio = new_bio
            changes.append("bio")

        onboarding = None
        if new_dept is not None or new_class is not None:
            onboarding = OnboardingDetails.query.filter_by(user_id=current_user.id).first()

        if new_dept is not None and new_dept != profile.department:
            profile.department = new_dept
            changes.append("department")
        if new_class is not None and new_class != profile.class_name:
            profile.class_name = new_class
            changes.append("class_level")

        if onboarding is not None:
            if new_dept is not None:
                onboarding.department = new_dept
            if new_class is not None:
                onboarding.class_level = new_class

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

    except Exception:
        return _server_error("Profile update error", "Failed to update profile")


# ============================================================================
# SKILLS & LEARNING GOALS
# ============================================================================

MAX_SKILLS         = 10
MAX_LEARNING_GOALS = 5
MAX_PINNED_POSTS   = 5


def _locked_user(user_id):
    """Load the user row with a row lock for read-modify-write updates.

    Skills, goals and pins are rewritten as a whole value, so two
    concurrent requests (two tabs, retries, two app instances) could
    otherwise overwrite each other or exceed a limit. SQLite ignores the
    lock, which is fine for tests.
    """
    # populate_existing(): the request's user object is already in the
    # session (loaded before the lock), and without it the locked SELECT
    # would hand back that stale copy instead of the row as it is now.
    return (User.query.filter_by(id=user_id)
            .with_for_update().populate_existing().first())


def _json_object():
    """Parsed JSON body, or None if it is not a JSON object."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


@profile_bp.route("/profile/skills", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def add_skill(current_user):
    """Add a skill to profile. Body: {"skill": "Python"}"""
    data = _json_object()
    if data is None:
        return error_response("Request body must be a JSON object")

    try:
        skill = clean_plain_text(data.get("skill", ""), "Skill", max_len=50)
    except ProfileValidationError as e:
        return error_response(str(e))

    try:
        user   = _locked_user(current_user.id)
        skills = list(user.skills or [])

        if any(s.lower() == skill.lower() for s in skills):
            return error_response("Skill already added")
        if len(skills) >= MAX_SKILLS:
            return error_response(f"Maximum {MAX_SKILLS} skills allowed")

        skills.append(skill)
        user.skills = skills
        db.session.commit()

        return success_response("Skill added", data={"skills": skills}), 201

    except Exception:
        return _server_error("Add skill error", "Failed to add skill")


@profile_bp.route("/profile/skills", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def remove_skill(current_user):
    """Remove a skill. Body: {"skill": "C/C++"}

    The name travels in the body, not the URL, so names containing
    "/", "#", "?" or "." can be removed like any other.
    """
    data = _json_object()
    if data is None or not isinstance(data.get("skill"), str) or not data["skill"].strip():
        return error_response("Skill name required")
    wanted = data["skill"].strip().lower()

    try:
        user   = _locked_user(current_user.id)
        skills = [s for s in (user.skills or []) if s.lower() != wanted]
        user.skills = skills
        db.session.commit()
        return success_response("Skill removed", data={"skills": skills})

    except Exception:
        return _server_error("Remove skill error", "Failed to remove skill")


@profile_bp.route("/profile/learning-goals", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def add_learning_goal(current_user):
    """Add learning goal. Body: {"goal": "Learn Machine Learning"}"""
    data = _json_object()
    if data is None:
        return error_response("Request body must be a JSON object")

    try:
        goal = clean_plain_text(data.get("goal", ""), "Goal", max_len=100)
    except ProfileValidationError as e:
        return error_response(str(e))

    try:
        user  = _locked_user(current_user.id)
        goals = list(user.learning_goals or [])

        if any(g.lower() == goal.lower() for g in goals):
            return error_response("Goal already added")
        if len(goals) >= MAX_LEARNING_GOALS:
            return error_response(f"Maximum {MAX_LEARNING_GOALS} learning goals allowed")

        goals.append(goal)
        user.learning_goals = goals
        db.session.commit()

        return success_response("Learning goal added", data={"learning_goals": goals}), 201

    except Exception:
        return _server_error("Add goal error", "Failed to add goal")


@profile_bp.route("/profile/learning-goals", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def remove_learning_goal(current_user):
    """Remove a learning goal by its text. Body: {"goal": "Learn Machine Learning"}

    Removing by value (not list position) means a stale page cannot delete
    a different goal than the one the user clicked.
    """
    data = _json_object()
    if data is None or not isinstance(data.get("goal"), str) or not data["goal"].strip():
        return error_response("Goal required")
    wanted = data["goal"].strip().lower()

    try:
        user  = _locked_user(current_user.id)
        goals = [g for g in (user.learning_goals or []) if g.lower() != wanted]
        user.learning_goals = goals
        db.session.commit()

        return success_response("Learning goal removed", data={"learning_goals": goals})

    except Exception:
        return _server_error("Remove goal error", "Failed to remove goal")


# ============================================================================
# PIN / UNPIN POSTS
# ============================================================================

@profile_bp.route("/profile/pin-post/<int:post_id>", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def pin_post(current_user, post_id):
    """Pin a post to profile (at most MAX_PINNED_POSTS)."""
    try:
        # Serialise pin/unpin per user so the limit below cannot be raced.
        _locked_user(current_user.id)

        post = Post.query.filter_by(id=post_id, student_id=current_user.id).first()
        if not post:
            return error_response("Post not found or not yours")
        if post.is_pinned:
            return success_response("Post has already been pinned")

        total_pinned = Post.query.filter_by(student_id=current_user.id, is_pinned=True).count()
        if total_pinned >= MAX_PINNED_POSTS:
            return error_response(
                f"You can't pin more than {MAX_PINNED_POSTS} posts — unpin one first")

        post.is_pinned = True
        db.session.commit()
        return success_response("Post pinned successfully")

    except Exception:
        return _server_error("Pin post error", "Failed to pin post")


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

    except Exception:
        return _server_error("Unpin post error", "Failed to unpin post")


# ============================================================================
# STUDY SCHEDULE
# ============================================================================

def _schedule_for_client(stored):
    """Onboarding-format schedule ({"Monday": [...]}) -> all 7 lowercase days."""
    schedule = {day.lower(): [] for day in STUDY_DAYS}
    if isinstance(stored, dict):
        for day, slots in stored.items():
            key = str(day).lower()
            if key in schedule and isinstance(slots, list):
                schedule[key] = [s for s in slots
                                 if isinstance(s, str) and s.lower() in STUDY_SLOTS]
    return schedule


@profile_bp.route("/profile/study-schedule", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
def get_study_schedule(current_user):
    """Get the user's study schedule.

    The schedule lives on the onboarding record, the same copy the
    compatibility and discovery code score, so what is shown here is what
    matching actually uses.
    """
    onboarding = OnboardingDetails.query.filter_by(user_id=current_user.id).first()
    stored = onboarding.study_schedule if onboarding else {}
    return jsonify({
        "status": "success",
        "data": {"study_schedule": _schedule_for_client(stored)},
    })


@profile_bp.route("/profile/study-schedule", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def update_study_schedule(current_user):
    """Replace the study schedule.

    Body: {"monday": ["morning", "evening"], ...}. Days and slots are
    case-insensitive; days with no slots may be omitted.
    """
    data = request.get_json(silent=True)
    try:
        schedule = clean_study_schedule(data)
    except ProfileValidationError as e:
        return error_response(str(e))

    try:
        onboarding = OnboardingDetails.query.filter_by(user_id=current_user.id).first()
        if not onboarding:
            return error_response("Academic profile not found", 404)

        onboarding.study_schedule = schedule
        onboarding.last_updated = datetime.datetime.utcnow()
        db.session.commit()

        return success_response(
            "Study schedule updated",
            data={"study_schedule": _schedule_for_client(schedule)},
        )

    except Exception:
        return _server_error("Update study schedule error", "Failed to update study schedule")


# ============================================================================
# POPULAR SKILLS (public endpoint)
# ============================================================================

POPULAR_SKILLS_CACHE_KEY = "sh:1:skills:popular"
POPULAR_SKILLS_CACHE_TTL = 300   # seconds; popularity data tolerates staleness


@profile_bp.route("/skills/popular", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
def get_popular_skills(current_user):
    """Most common skills across all users (for the skill autocomplete).

    The result is the same for everyone and expensive to compute (it reads
    every user's skill list), so it is cached in Redis for a few minutes
    and shared by all app instances.
    """
    try:
        cached = cache_service.get(POPULAR_SKILLS_CACHE_KEY)
        if cached is not None:
            return jsonify({"status": "success", "data": cached})

        skill_counts = {}
        # Only the skills column, not whole User rows.
        for (skills,) in db.session.query(User.skills).filter(User.skills.isnot(None)):
            for skill in (skills or []):
                if not isinstance(skill, str):
                    continue
                key = skill.lower()
                if key in skill_counts:
                    skill_counts[key]["count"] += 1
                else:
                    skill_counts[key] = {"name": skill, "count": 1}

        popular = sorted(skill_counts.values(), key=lambda x: x["count"], reverse=True)[:50]
        data = {"skills": [s["name"] for s in popular], "detailed": popular}

        cache_service.set(POPULAR_SKILLS_CACHE_KEY, data, ttl_seconds=POPULAR_SKILLS_CACHE_TTL)
        return jsonify({"status": "success", "data": data})

    except Exception:
        return _server_error("Get popular skills error", "Failed to load skills")


# ============================================================================
# HOMEPAGE
# ============================================================================

@profile_bp.route("/profile/homepage", methods=["GET"])
def homepage():
    return render_template("post/feed2.html")
