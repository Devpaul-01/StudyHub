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
from routes.student.helpers import (
    token_required, success_response, error_response,
    save_file, ALLOWED_IMAGE_EXT,
)

profile_bp = Blueprint("student_profile", __name__)


# ============================================================================
# PROFILE DATA — own profile
# ============================================================================

@profile_bp.route("/profile/me/data", methods=["GET"])
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
                "posted_at":       p.posted_at.isoformat(),
                "is_author":       True,                       # always true on this endpoint
                "user_interactions": {
                    "user_reacted":       bool(reaction),
                    "reaction_type":      reaction.reaction_type if reaction else None,
                    "user_followed":      False,               # own posts — not applicable
                    "requested_thread":   requested_thread,
                    "is_thread_member":   is_member,
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

        helpful_received = db.session.query(func.count(PostReaction.id)).join(
            Post, PostReaction.post_id == Post.id
        ).filter(
            Post.student_id == current_user.id,
            PostReaction.reaction_type == "helpful",
        ).scalar() or 0

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

        if CLOUD_AVAILABLE:
            folder             = f"avatars/{current_user.id}"
            generated_filename = f"avatar_{current_user.id}"
            result = cloudinary_storage.upload_file(
                file, folder, generated_filename, resource_type="image"
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
# HELP SUGGESTIONS
# ============================================================================

@profile_bp.route("/profile/help/suggestions", methods=["GET"])
@token_required
def help_suggestions(current_user):
    """
    Suggest users who can help with subjects the current user needs help with.
    Matches help_subjects ↔ strong_subjects.

    FIX: uses joinedload on onboarding_details and student_profile to
    eliminate the N+1 query pattern (was one extra query per candidate user).
    """
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")

        onboarding_details = OnboardingDetails.query.filter_by(user_id=user.id).first()
        if not onboarding_details:
            return error_response("Onboarding details not found")

        help_subjects = onboarding_details.help_subjects or []
        if not help_subjects:
            return jsonify({
                "status": "success",
                "message": "You haven't specified any subjects you need help with yet",
                "data": {"suggestions": [], "total": 0},
            })

        # FIX: single query — both relationships eager-loaded
        all_users = (
            User.query
            .filter(User.id != user.id, User.onboarding_details.has())
            .options(
                joinedload(User.onboarding_details),
                joinedload(User.student_profile),
            )
            .all()
        )

        suggestions = []

        for potential_helper in all_users:
            details = potential_helper.onboarding_details
            if not details or not details.strong_subjects:
                continue

            overlap = set(help_subjects) & set(details.strong_subjects)
            if not overlap:
                continue

            profile      = potential_helper.student_profile   # no extra query
            match_score  = (len(overlap) / len(help_subjects)) * 100

            suggestions.append({
                "user": {
                    "id":               potential_helper.id,
                    "name":             potential_helper.name,
                    "username":         potential_helper.username,
                    "avatar":           potential_helper.avatar,
                    "reputation":       potential_helper.reputation,
                    "reputation_level": potential_helper.reputation_level,
                },
                "profile": {
                    "department": profile.department if profile else None,
                    "class_level": profile.class_name if profile else None,
                },
                "match_details": {
                    "can_help_with": list(overlap),
                    "overlap_count": len(overlap),
                    "match_score":   round(match_score, 1),
                },
            })

        suggestions.sort(
            key=lambda x: (x["match_details"]["match_score"], x["user"]["reputation"]),
            reverse=True,
        )

        return jsonify({
            "status": "success",
            "data": {
                "your_help_subjects": help_subjects,
                "suggestions":        suggestions[:10],
                "total":              len(suggestions[:10]),
            },
        })

    except Exception as e:
        current_app.logger.error(f"Help suggestions error: {str(e)}")
        return error_response("Error encountered while loading help suggestions")


@profile_bp.route("/profile/can-help/suggestions", methods=["GET"])
@token_required
def can_help_suggestions(current_user):
    """
    Suggest users the current user can help (based on strong_subjects).
    Matches strong_subjects ↔ help_subjects.

    FIX: joinedload eliminates N+1 queries.
    """
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")

        onboarding_details = OnboardingDetails.query.filter_by(user_id=user.id).first()
        if not onboarding_details:
            return error_response("Onboarding details not found")

        strong_subjects = onboarding_details.strong_subjects or []
        if not strong_subjects:
            return jsonify({
                "status": "success",
                "message": "You haven't specified any strong subjects yet",
                "data": {"suggestions": [], "total": 0},
            })

        # FIX: single query — both relationships eager-loaded
        all_users = (
            User.query
            .filter(User.id != user.id, User.onboarding_details.has())
            .options(
                joinedload(User.onboarding_details),
                joinedload(User.student_profile),
            )
            .all()
        )

        suggestions = []

        for person_needing_help in all_users:
            details = person_needing_help.onboarding_details
            if not details or not details.help_subjects:
                continue

            overlap = set(strong_subjects) & set(details.help_subjects)
            if not overlap:
                continue

            profile     = person_needing_help.student_profile   # no extra query
            match_score = (len(overlap) / len(details.help_subjects)) * 100

            suggestions.append({
                "user": {
                    "id":               person_needing_help.id,
                    "name":             person_needing_help.name,
                    "username":         person_needing_help.username,
                    "avatar":           person_needing_help.avatar,
                    "reputation":       person_needing_help.reputation,
                    "reputation_level": person_needing_help.reputation_level,
                },
                "profile": {
                    "department": profile.department if profile else None,
                    "class_level": profile.class_name if profile else None,
                },
                "match_details": {
                    "needs_help_with": list(overlap),
                    "overlap_count":   len(overlap),
                    "match_score":     round(match_score, 1),
                },
            })

        suggestions.sort(
            key=lambda x: (x["match_details"]["match_score"], x["user"]["reputation"]),
            reverse=True,
        )

        return jsonify({
            "status": "success",
            "data": {
                "your_strong_subjects": strong_subjects,
                "suggestions":          suggestions[:10],
                "total":                len(suggestions[:10]),
            },
        })

    except Exception as e:
        current_app.logger.error(f"Can help suggestions error: {str(e)}")
        return error_response("Error encountered while loading suggestions")


# ============================================================================
# OWN FULL PROFILE
# ============================================================================

@profile_bp.route("/profile/me", methods=["GET", "POST"])
@token_required
def get_own_profile(current_user):
    """Get current user's full profile including stats, badges, and activity."""
    if request.method == "GET":
        return render_template("profile/profile.html")

    try:
        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
        if not profile:
            return error_response("Profile not found", 404)

        total_posts    = Post.query.filter_by(student_id=current_user.id).count()
        total_comments = Comment.query.filter_by(student_id=current_user.id).count()
        total_threads  = ThreadMember.query.filter_by(student_id=current_user.id).count()

        # FIX: PostLike does not exist — replaced with PostReaction throughout
        posts_liked = db.session.query(func.count(PostReaction.id)).join(
            Post, PostReaction.post_id == Post.id
        ).filter(Post.student_id == current_user.id).scalar() or 0

        helpful_count = db.session.query(func.count(PostReaction.id)).join(
            Post, PostReaction.post_id == Post.id
        ).filter(
            Post.student_id == current_user.id,
            PostReaction.reaction_type == "helpful",
        ).scalar() or 0

        user_badges = (
            UserBadge.query
            .filter_by(user_id=current_user.id)
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

        recent_posts = (
            Post.query
            .filter_by(student_id=current_user.id)
            .order_by(Post.posted_at.desc())
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
                "is_pinned":      p.is_pinned,
                "is_solved":      p.is_solved,
            }
            for p in recent_posts
        ]

        active_threads = (
            db.session.query(Thread)
            .join(ThreadMember, Thread.id == ThreadMember.thread_id)
            .filter(ThreadMember.student_id == current_user.id)
            .order_by(Thread.last_activity.desc())
            .limit(5)
            .all()
        )
        threads_data = [
            {
                "id":            t.id,
                "title":         t.title,
                "member_count":  t.member_count,
                "is_creator":    t.creator_id == current_user.id,
                "last_activity": t.last_activity.isoformat(),
            }
            for t in active_threads
        ]

        thirty_days_ago = datetime.date.today() - datetime.timedelta(days=30)
        activity_data   = UserActivity.query.filter(
            UserActivity.user_id       == current_user.id,
            UserActivity.activity_date >= thirty_days_ago,
        ).order_by(UserActivity.activity_date.asc()).all()

        heatmap = [
            {
                "date":     act.activity_date.isoformat(),
                "score":    act.activity_score,
                "posts":    act.posts_created,
                "comments": act.comments_created,
            }
            for act in activity_data
        ]

        skills         = current_user.skills         or []
        learning_goals = current_user.learning_goals or []

        privacy         = current_user.privacy_settings or {}
        profile_private = privacy.get("set_profile_private", False)
        show_active     = privacy.get("show_active_status", True)
        dark_mode       = privacy.get("set_dark_mode", False)
        # FIX: variable was referenced but never assigned — extract from dict
        send_weekly_notification = privacy.get("send_weekly_notification", False)

        return jsonify({
            "status": "success",
            "data": {
                "user": {
                    "id":                       current_user.id,
                    "username":                 current_user.username,
                    "name":                     current_user.name,
                    "bio":                      current_user.bio,
                    "avatar":                   current_user.avatar,
                    "department":               profile.department,
                    "class_level":              profile.class_name,
                    "reputation":               current_user.reputation,
                    "reputation_level":         current_user.reputation_level,
                    "login_streak":             current_user.login_streak,
                    "joined_at":                current_user.joined_at.isoformat(),
                    "last_active":              current_user.last_active.isoformat() if show_active else None,
                    "profile_private":          profile_private,
                    "show_active_status":       show_active,
                    "send_weekly_notification": send_weekly_notification,  # FIX: now defined
                    "set_dark_mode":            dark_mode,
                },
                "stats": {
                    "total_posts":    total_posts,
                    "total_comments": total_comments,
                    "total_threads":  total_threads,
                    "posts_liked":    posts_liked,
                    "helpful_count":  helpful_count,
                    "total_helpful":  current_user.total_helpful,
                },
                "badges":           badges_data,
                "active_threads":   threads_data,
                "activity_heatmap": heatmap,
                "skills":           skills,
                "learning_goals":   learning_goals,
                "is_own_profile":   True,
                "can_message":      False,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get own profile error: {str(e)}")
        return error_response("Failed to load profile")


# ============================================================================
# VISIBILITY SETTINGS
# ============================================================================

@profile_bp.route("/profile/visibility-settings", methods=["POST"])
@token_required
def visibility_settings(current_user):
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")

        privacy = user.privacy_settings or {}
        data    = request.get_json() or {}

        profile_private          = data.get("set_profile_private",     False)
        show_active_status       = data.get("show_active_status",       True)
        dark_mode                = data.get("set_dark_mode",            False)
        send_weekly_notification = data.get("send_weekly_notification", True)

        privacy["set_profile_private"]     = profile_private
        privacy["show_active_status"]      = show_active_status
        privacy["set_dark_mode"]           = dark_mode
        privacy["send_weekly_notification"] = send_weekly_notification

        user.privacy_settings = privacy
        db.session.commit()
        return success_response("Visibility settings saved successfully")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Privacy settings error: {str(e)}")
        return error_response("Failed to set profile visibility settings")


@profile_bp.route("/profile/visibility-settings", methods=["GET"])
@token_required
def get_visibility_settings(current_user):
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")

        privacy = user.privacy_settings or {}

        profile_private          = privacy.get("set_profile_private",     False)
        show_active_status       = privacy.get("show_active_status",       True)
        dark_mode                = privacy.get("set_dark_mode",            False)
        # FIX: variable was named weekly_notifications but used as weekly_summary → unified
        send_weekly_notification = privacy.get("send_weekly_notification", True)

        return jsonify({
            "status": "success",
            "data": {
                "set_profile_private":     profile_private,
                "show_active_status":      show_active_status,
                "set_dark_mode":           dark_mode,
                "send_weekly_notification": send_weekly_notification,  # FIX: was weekly_summary (NameError)
            },
        })

    except Exception as e:
        current_app.logger.error(f"Profile visibility error: {str(e)}")
        return error_response("Failed to load profile visibility settings")


# ============================================================================
# VIEW ANOTHER USER'S PROFILE
# ============================================================================

@profile_bp.route("/profile/<username>", methods=["GET"])
@token_required
def view_profile(current_user, username):
    """View any user's complete profile."""
    try:
        user = User.query.filter_by(username=username).first()
        if not user:
            return error_response("User not found")

        profile = StudentProfile.query.filter_by(user_id=user.id).first()
        if not profile:
            return error_response("Profile not found")

        # ── Connection status ────────────────────────────────────────────────
        connection_status = "none"

        if current_user.id != user.id:
            connection = Connection.query.filter(
                or_(
                    and_(Connection.requester_id == current_user.id, Connection.receiver_id == user.id),
                    and_(Connection.requester_id == user.id,         Connection.receiver_id == current_user.id),
                )
            ).first()

            if connection:
                if connection.status == "accepted":
                    connection_status = "connected"
                elif connection.status == "blocked":
                    connection_status = "blocked"          # FIX: was incorrectly "accepted"
                elif connection.status == "pending" and connection.requester_id == current_user.id:
                    connection_status = "pending_sent"     # FIX: was "pending sent" (space)
                elif connection.status == "pending" and connection.receiver_id == current_user.id:
                    # FIX: was `connection.status = "peding_received"` — ORM mutation + typo
                    connection_status = "pending_received"

        # ── Stats ────────────────────────────────────────────────────────────
        total_posts    = Post.query.filter_by(student_id=user.id).count()
        total_comments = Comment.query.filter_by(student_id=user.id).count()
        total_threads  = ThreadMember.query.filter_by(student_id=user.id).count()

        # FIX: PostLike does not exist — replaced with PostReaction
        posts_liked = db.session.query(func.count(PostReaction.id)).join(
            Post, PostReaction.post_id == Post.id
        ).filter(Post.student_id == user.id).scalar() or 0

        helpful_count = db.session.query(func.count(PostReaction.id)).join(
            Post, PostReaction.post_id == Post.id
        ).filter(
            Post.student_id == user.id,
            PostReaction.reaction_type == "helpful",
        ).scalar() or 0

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

        # FIX: serialize pinned_posts — was returning raw ORM objects (not JSON-serializable)
        pinned_posts_raw = Post.query.filter_by(student_id=user.id, is_pinned=True).all()
        pinned_posts = [
            {
                "id":        p.id,
                "title":     p.title,
                "post_type": p.post_type,
                "posted_at": p.posted_at.isoformat(),
            }
            for p in pinned_posts_raw
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

        total_created_threads = Thread.query.filter_by(creator_id=user.id).count()

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

        thirty_days_ago = datetime.date.today() - datetime.timedelta(days=30)
        activity_data   = UserActivity.query.filter(
            UserActivity.user_id       == user.id,
            UserActivity.activity_date >= thirty_days_ago,
        ).order_by(UserActivity.activity_date.asc()).all()

        heatmap = [
            {
                "date":     act.activity_date.isoformat(),
                "score":    act.activity_score,
                "posts":    act.posts_created,
                "comments": act.comments_created,
            }
            for act in activity_data
        ]

        skills         = user.skills         or []
        learning_goals = user.learning_goals or []

        # ── Privacy ──────────────────────────────────────────────────────────
        privacy = user.privacy_settings or {}
        # FIX: key was "profile_private" but is saved as "set_profile_private"
        profile_private = privacy.get("set_profile_private", False)
        show_active     = privacy.get("show_active_status",  True)

        if profile_private:
            return jsonify({
                "status": "success",
                "message": "This profile is private — only a few details are visible to you.",
                "data": {
                    "type": "private",
                    "user": {
                        "id":         user.id,
                        "username":   user.username,
                        "name":       user.name,
                        "avatar":     user.avatar,
                        "department": profile.department,
                        "class_level": profile.class_name,
                        "pinned_posts": pinned_posts,   # FIX: now serialized dicts, not ORM objects
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
                    "login_streak":     user.login_streak,
                    "joined_at":        user.joined_at.isoformat(),
                    "last_active":      user.last_active.isoformat() if user.last_active else None,
                    "active_status":    show_active,
                },
                "stats": {
                    "total_posts":          total_posts,
                    "total_comments":       total_comments,
                    "total_threads":        total_threads,
                    "total_created_threads": total_created_threads,
                    "posts_liked":          posts_liked,
                    "helpful_count":        helpful_count,
                    "total_helpful":        user.total_helpful,
                },
                "badges":           badges_data,
                "recent_posts":     posts_data,
                "active_threads":   threads_data,
                "activity_heatmap": heatmap,
                "skills":           skills,
                "learning_goals":   learning_goals,
                "connection_status": connection_status,
                "is_own_profile":   False,
            },
        })

    except Exception as e:
        current_app.logger.error(f"View profile error: {str(e)}")
        return error_response("Failed to load profile")


# ============================================================================
# PROFILE EDITING
# ============================================================================

@profile_bp.route("/profile/update", methods=["PATCH"])
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
