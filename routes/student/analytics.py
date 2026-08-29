"""
StudyHub - Analytics & Insights System
Provides engaging stats, activity tracking, and AI-like insights

Features:
- Personal dashboard with key metrics
- Activity heatmap (90-day contribution graph)
- Engagement breakdown (posts, comments, threads)
- AI-like insights and suggestions
- Comparison with average users
- Goal tracking and progress
- Post-specific analytics
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func, desc, and_, or_, case
import datetime

from models import (
    User, StudentProfile, Post, Comment, Thread, ThreadMember,
    PostReaction, PostView, UserActivity, Connection,
    ReputationHistory, UserBadge, ThreadMessage, Bookmark, Badge
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)
# Imported at module level to avoid repeated lookups inside generate_insights
# and to eliminate the circular-import risk of a function-level import.
from services.badge_service import calculate_badge_progress

# Document 2 §4: calculate_engagement_rate and get_activity_level are pure
# functions of numeric inputs, moved to services/analytics_service.py.
# generate_insights/get_average_user_stats stay here — see
# services/analytics_service.py's module docstring for why (they do heavy
# DB querying, and generate_insights depends on routes/student/badges.py,
# which isn't a service yet).
from services.analytics_service import calculate_engagement_rate, get_activity_level
# Phase 5b (Document 4 §1): PUBLIC_READ across the board — every route in
# this file is a computed stats/analytics read, several doing non-trivial
# aggregation (overview, engagement, comparison) that's worth protecting
# from being hammered.
from services.rate_limit_service import limiter, RateLimitTier, ip_key
# Plan §4.4/§17.5: manual cache-aside (not the @cached decorator) around
# each route's computed data dict — the decorator's JSON-serialization
# contract requires a plain JSON-serializable return value, but every
# route here returns jsonify(...) (a Flask Response object, not
# serializable), so the cache boundary sits around the dict construction,
# with jsonify(...) applied outside it on both the hit and miss paths.
from services import cache_service

analytics_bp = Blueprint("student_analytics", __name__)

# ---------------------------------------------------------------------------
# SQLite's strftime('%w', …) returns 0=Sunday … 6=Saturday.
# Python's calendar.day_name is 0=Monday … 6=Sunday — a different mapping.
# This explicit list avoids that off-by-one entirely.
# ---------------------------------------------------------------------------
_SQLITE_WEEKDAY_NAMES = [
    "Sunday", "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday",
]

# Allowed values for the export endpoint's ?type= query parameter.
_VALID_EXPORT_TYPES = {"activity", "reputation", "posts", "overview"}



def generate_insights(user_id):
    insights = []
    user = User.query.get(user_id)

    if not user:  # Guard against missing user
        return insights

    # Insight 1: Best posting day
    posts_by_day = db.session.query(
        func.strftime('%w', Post.posted_at).label('day'),
        func.count(Post.id).label('count'),
        func.avg(Post.positive_reactions_count).label('avg_likes')
    ).filter(
        Post.student_id == user_id,
        Post.posted_at >= datetime.datetime.utcnow() - datetime.timedelta(days=30)
    ).group_by('day').all()

    if posts_by_day:
        # Filter out rows where avg_likes is None before any math
        valid_days = [p for p in posts_by_day if p.avg_likes is not None]
        if valid_days:
            best_day = max(valid_days, key=lambda x: x.avg_likes)
            # FIX: use _SQLITE_WEEKDAY_NAMES — strftime('%w') gives 0=Sunday…6=Saturday,
            # which does NOT match Python's calendar.day_name (0=Monday…6=Sunday).
            day_name = _SQLITE_WEEKDAY_NAMES[int(best_day.day)]
            avg_likes_overall = sum(p.avg_likes for p in valid_days) / len(valid_days)
            pct_above = int(best_day.avg_likes / avg_likes_overall * 100) if avg_likes_overall else 0
            insights.append({
                "type": "timing",
                "icon": "📅",
                "title": "Best Posting Day",
                "message": f"Your posts get {best_day.avg_likes:.1f} likes on {day_name}s - {pct_above}% above average!",
                "actionable": f"Try posting more on {day_name}s"
            })

    avg_response_time = db.session.query(
        func.avg(
            func.julianday(Comment.posted_at) - func.julianday(Post.posted_at)
        ) * 24
    ).join(Post, Comment.post_id == Post.id).filter(
        Comment.student_id == user_id
    ).scalar()

    if avg_response_time is not None and avg_response_time < 3:
        insights.append({
            "type": "engagement",
            "icon": "⚡",
            "title": "Quick Responder",
            "message": f"You respond to posts in {avg_response_time:.1f} hours on average - faster than 75% of users!",
            "actionable": "Your quick responses help build reputation"
        })

    # Insight 3: Trending content
    recent_post = Post.query.filter_by(student_id=user_id).order_by(
        Post.posted_at.desc()
    ).first()

    if recent_post:
        views_today = PostView.query.filter(
            PostView.post_id == recent_post.id,
            func.date(PostView.viewed_at) == datetime.date.today()
        ).count()

        if views_today >= 20:
            insights.append({
                "type": "trending",
                "icon": "🔥",
                "title": "You're Trending!",
                "message": f'Your post "{recent_post.title[:40]}..." has {views_today} views today!',
                "actionable": "Keep engaging with comments to maintain momentum"
            })

    # Insight 4: Badge progress
    # calculate_badge_progress is now imported at module level (top of file).
    earned_badge_ids = [ub.badge_id for ub in UserBadge.query.filter_by(user_id=user_id).all()]
    unearned = Badge.query.filter(Badge.id.notin_(earned_badge_ids), Badge.is_active == True).all()

    closest_badge = None
    highest_progress = 0

    for badge in unearned:
        progress = calculate_badge_progress(user_id, badge.id)
        # Guard: progress must be a dict with a numeric 'percentage'
        if progress and isinstance(progress, dict) and progress.get("percentage", 0) > highest_progress:
            highest_progress = progress["percentage"]
            closest_badge = (badge, progress)

    if closest_badge and highest_progress >= 50:
        badge, progress = closest_badge
        # Guard: ensure expected keys exist before formatting
        remaining = progress.get('remaining', '?')
        prog_type = progress.get('type', 'actions')
        insights.append({
            "type": "achievement",
            "icon": "🏆",
            "title": "Badge Almost Unlocked!",
            "message": f"{badge.icon} {remaining} more {prog_type} to earn '{badge.name}'",
            "actionable": f"You're {highest_progress:.0f}% there!"
        })

    # Insight 5: Audience engagement
    # Resolve profile once and guard against None
    student_profile = StudentProfile.query.filter_by(user_id=user_id).first()
    if student_profile and student_profile.department:
        dept_filter = StudentProfile.department == student_profile.department

        dept_rank = db.session.query(func.count(User.id)).join(StudentProfile).filter(
            dept_filter,
            User.reputation > user.reputation
        ).scalar() or 0

        total_dept_users = db.session.query(func.count(User.id)).join(StudentProfile).filter(
            dept_filter
        ).scalar() or 0

        if total_dept_users > 0:
            percentile = round((1 - ((dept_rank + 1) / total_dept_users)) * 100)
            if percentile >= 90:
                insights.append({
                    "type": "achievement",
                    "icon": "🌟",
                    "title": "Top Contributor",
                    "message": f"You're in the top {100 - percentile}% of your department!",
                    "actionable": "Your expertise is valued by the community"
                })

    # Insight 6: Consistency check
    if getattr(user, 'login_streak', 0) and user.login_streak >= 7:
        insights.append({
            "type": "consistency",
            "icon": "🔥",
            "title": "On Fire!",
            "message": f"{user.login_streak} day streak! You're building a strong learning habit.",
            "actionable": "Don't break the streak - come back tomorrow!"
        })

    return insights[:5]


@cache_service.cached("sh:1:an:platform_avg", ttl_seconds=600)
def get_average_user_stats():
    """Calculate platform-wide average statistics.

    Always returns a dict with all four keys (values are 0 when there are no
    approved users) so callers never receive a KeyError.

    Plan §4.4.1/§17.5: cached once, shared across every viewer (this
    function takes no arguments that vary the result, so the @cached
    decorator's key_template needs no {placeholders} — see cache_service.py's
    own docstring for that pattern). This function already returns a plain
    dict, satisfying @cached's JSON-serialization contract directly, unlike
    the route functions elsewhere in this file which wrap their data in
    jsonify(...) and therefore use manual cache-aside instead (see
    get_comparison_stats below, which computes each viewer's personal
    multipliers live against this cached shared input rather than caching
    per-viewer).
    """
    total_users = User.query.filter_by(status="approved").count()

    # FIX: previously returned {} on zero users, causing KeyError in callers.
    if total_users == 0:
        return {
            "avg_posts": 0,
            "avg_reputation": 0,
            "avg_helpful": 0,
            "avg_connections": 0,
        }

    avg_stats = {
        "avg_posts": db.session.query(func.avg(User.total_posts)).scalar() or 0,
        "avg_reputation": db.session.query(func.avg(User.reputation)).scalar() or 0,
        "avg_helpful": db.session.query(func.avg(User.total_helpful)).scalar() or 0,
        "avg_connections": db.session.query(
            func.count(Connection.id)
        ).filter(Connection.status == "accepted").scalar() / total_users,
    }

    return avg_stats


# ============================================================================
# ANALYTICS ENDPOINTS
# ============================================================================

@analytics_bp.route("/analytics/overview", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_analytics_overview(current_user):
    """
    Personal dashboard with key metrics
    Shows monthly impact, rank, reputation change, activity level
    """
    try:
        # Plan §4.2/§17.5: cache-aside, TTL 120s. Hard-invalidated at the
        # exact moment the viewing user's own reputation changes (see
        # services/reputation_service.py::award_reputation, plan §5.1) —
        # this TTL is the backstop for the case an invalidation is missed,
        # not the primary correctness mechanism.
        cache_key = f"sh:1:an:overview:{current_user.id}"
        data = cache_service.get(cache_key)
        if data is not None:
            return jsonify({"status": "success", "data": data})

        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()

        # Calculate week-over-week changes
        week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)

        # This week's stats
        this_week_views = db.session.query(func.count(PostView.id)).join(
            Post, PostView.post_id == Post.id
        ).filter(
            Post.student_id == current_user.id,
            PostView.viewed_at >= week_ago
        ).scalar() or 0

        # AUDIT §4.2 FIX: this used to query PostReaction.reaction_type ==
        # "helpful", but no live code path ever creates a PostReaction with
        # that reaction_type — react_to_post hardcodes reaction_type="like"
        # (the multi-reaction system was intentionally replaced by a single
        # like-toggle; see that route's own docstring). This query always
        # evaluated to 0; replaced with the literal value to stop running a
        # real query against an always-empty filter. Response shape
        # (hero_stats.helpful_count) is unchanged — still honestly 0.
        this_week_helpful = 0

        this_week_rep = db.session.query(
            func.sum(ReputationHistory.points_change)
        ).filter(
            ReputationHistory.user_id == current_user.id,
            ReputationHistory.created_at >= week_ago
        ).scalar() or 0

        # FIX: guard against missing profile — previously crashed with
        # AttributeError when a user had no StudentProfile row.
        if profile and profile.department:
            dept_rank = db.session.query(func.count(User.id)).join(StudentProfile).filter(
                StudentProfile.department == profile.department,
                User.reputation > current_user.reputation,
                User.status == "approved"
            ).scalar() + 1
        else:
            dept_rank = None

        # Activity level this week
        week_activity = db.session.query(
            func.sum(UserActivity.activity_score)
        ).filter(
            UserActivity.user_id == current_user.id,
            UserActivity.activity_date >= datetime.date.today() - datetime.timedelta(days=7)
        ).scalar() or 0

        activity_level = get_activity_level(week_activity)

        data = {
            "hero_stats": {
                "monthly_views": this_week_views,
                "helpful_count": this_week_helpful,
                "department_rank": dept_rank,
                "reputation_change": int(this_week_rep),
                "activity_level": activity_level
            },
            "current_stats": {
                "total_posts": current_user.total_posts,
                "total_reputation": current_user.reputation,
                "reputation_level": current_user.reputation_level,
                "login_streak": current_user.login_streak,
                "total_helpful": current_user.total_helpful
            },
            "quick_facts": {
                "joined_at": current_user.joined_at.isoformat(),
                "days_active": (datetime.datetime.utcnow() - current_user.joined_at).days,
                "department": profile.department if profile else None,
                "class_level": profile.class_name if profile else None
            }
        }

        cache_service.set(cache_key, data, ttl_seconds=120)

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Analytics overview error: {str(e)}")
        return error_response("Failed to load analytics overview")


@analytics_bp.route("/analytics/activity-heatmap", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_activity_heatmap(current_user):
    """
    GitHub-style contribution heatmap for last 90 days
    Shows daily activity score with color intensity
    """
    try:
        days = request.args.get("days", 90, type=int)
        days = min(days, 365)  # Max 1 year

        # Plan §4.4/§17.5: cache-aside, TTL 300s, TTL fallback only (no
        # natural per-mutation hook — heatmap changes are daily-granular,
        # so a 5-minute window is invisible relative to the data's own
        # resolution). `days` is part of the key since it's a varying
        # request parameter that changes the result set (plan §10's
        # discipline: every parameter affecting the result belongs in the
        # key, not just page numbers).
        cache_key = f"sh:1:an:heatmap:{current_user.id}:{days}"
        data = cache_service.get(cache_key)
        if data is not None:
            return jsonify({"status": "success", "data": data})

        start_date = datetime.date.today() - datetime.timedelta(days=days)

        # Get all activity records
        activities = UserActivity.query.filter(
            UserActivity.user_id == current_user.id,
            UserActivity.activity_date >= start_date
        ).order_by(UserActivity.activity_date.asc()).all()

        # Create date map
        activity_map = {}
        for activity in activities:
            activity_map[activity.activity_date.isoformat()] = {
                "date": activity.activity_date.isoformat(),
                "score": activity.activity_score,
                "posts": activity.posts_created,
                "comments": activity.comments_created,
                "messages": activity.messages_sent,
                "helpful": activity.helpful_count,
                "level": 0  # Will calculate based on score
            }

        # Fill in missing dates with zero activity
        current_date = start_date
        while current_date <= datetime.date.today():
            date_str = current_date.isoformat()
            if date_str not in activity_map:
                activity_map[date_str] = {
                    "date": date_str,
                    "score": 0,
                    "posts": 0,
                    "comments": 0,
                    "messages": 0,
                    "helpful": 0,
                    "level": 0
                }
            else:
                # Calculate intensity level (0-4 for 5 color levels)
                score = activity_map[date_str]["score"]
                if score == 0:
                    level = 0
                elif score <= 5:
                    level = 1
                elif score <= 15:
                    level = 2
                elif score <= 30:
                    level = 3
                else:
                    level = 4
                activity_map[date_str]["level"] = level

            current_date += datetime.timedelta(days=1)

        # Convert to sorted list
        heatmap_data = sorted(activity_map.values(), key=lambda x: x["date"])

        # Calculate summary stats
        total_active_days = sum(1 for day in heatmap_data if day["score"] > 0)
        total_score = sum(day["score"] for day in heatmap_data)
        avg_daily_score = round(total_score / days, 1) if days > 0 else 0

        # Find best day
        best_day = max(heatmap_data, key=lambda x: x["score"]) if heatmap_data else None

        data = {
            "heatmap": heatmap_data,
            "summary": {
                "total_days": days,
                "active_days": total_active_days,
                "total_score": total_score,
                "avg_daily_score": avg_daily_score,
                "best_day": best_day,
                "current_streak": current_user.login_streak
            }
        }

        cache_service.set(cache_key, data, ttl_seconds=300)

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Activity heatmap error: {str(e)}")
        return error_response("Failed to load activity heatmap")


@analytics_bp.route("/analytics/engagement", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_engagement_stats(current_user):
    """
    Detailed engagement breakdown for posts, comments, threads
    """
    try:
        # Plan §4.4/§17.5: cache-aside, TTL 180s, TTL fallback only.
        cache_key = f"sh:1:an:engage:{current_user.id}"
        data = cache_service.get(cache_key)
        if data is not None:
            return jsonify({"status": "success", "data": data})

        # Post engagement
        post_stats = db.session.query(
            func.count(Post.id).label('total'),
            func.sum(Post.views_count).label('total_views'),
            func.sum(Post.positive_reactions_count).label('total_likes'),
            func.sum(Post.comments_count).label('total_comments'),
            func.avg(Post.positive_reactions_count).label('avg_likes'),
            func.avg(Post.views_count).label('avg_views')
        ).filter(Post.student_id == current_user.id).first()

        # Best performing post
        best_post = Post.query.filter_by(student_id=current_user.id).order_by(
            (Post.positive_reactions_count + Post.comments_count).desc()
        ).first()

        # Comment engagement
        comment_stats = db.session.query(
            func.count(Comment.id).label('total'),
            func.sum(Comment.likes_count).label('total_likes'),
            func.count(case((Comment.is_solution == True, 1))).label('solutions')
        ).filter(Comment.student_id == current_user.id).first()

        # Thread participation
        thread_stats = db.session.query(
            func.count(ThreadMember.id).label('joined'),
            func.sum(ThreadMember.messages_sent).label('messages_sent')
        ).filter(ThreadMember.student_id == current_user.id).first()

        threads_created = Thread.query.filter_by(creator_id=current_user.id).count()

        # Calculate engagement rate
        engagement_rate = calculate_engagement_rate(
            post_stats.total_views or 0,
            post_stats.total_likes or 0,
            post_stats.total_comments or 0
        )

        data = {
            "posts": {
                "total_created": post_stats.total or 0,
                "total_views": post_stats.total_views or 0,
                "total_likes": post_stats.total_likes or 0,
                "total_comments": post_stats.total_comments or 0,
                "avg_likes_per_post": round(post_stats.avg_likes or 0, 1),
                "avg_views_per_post": round(post_stats.avg_views or 0, 1),
                "engagement_rate": engagement_rate,
                "best_post": {
                    "id": best_post.id,
                    "title": best_post.title,
                    "likes": best_post.positive_reactions_count,
                    "comments": best_post.comments_count,
                    "views": best_post.views_count
                } if best_post else None
            },
            "comments": {
                "total_created": comment_stats.total or 0,
                "total_likes": comment_stats.total_likes or 0,
                "marked_helpful": current_user.total_helpful,
                "marked_solution": comment_stats.solutions or 0
            },
            "threads": {
                "created": threads_created,
                "joined": thread_stats.joined or 0,
                "messages_sent": thread_stats.messages_sent or 0
            }
        }

        cache_service.set(cache_key, data, ttl_seconds=180)

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Engagement stats error: {str(e)}")
        return error_response("Failed to load engagement stats")


@analytics_bp.route("/analytics/impact", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_impact_metrics(current_user):
    """
    Shows how many people the user has helped
    People reached, questions answered, resources shared
    """
    try:
        # Plan §4.4/§17.5: cache-aside, TTL 180s, TTL fallback only.
        cache_key = f"sh:1:an:impact:{current_user.id}"
        data = cache_service.get(cache_key)
        if data is not None:
            return jsonify({"status": "success", "data": data})

        # Unique users who viewed your posts
        people_reached = db.session.query(
            func.count(func.distinct(PostView.user_id))
        ).join(Post, PostView.post_id == Post.id).filter(
            Post.student_id == current_user.id,
            PostView.user_id != current_user.id
        ).scalar() or 0

        # Questions you answered
        questions_answered = Comment.query.join(Post).filter(
            Comment.student_id == current_user.id,
            Post.post_type.in_(["question", "problem"])
        ).count()

        # Questions you solved
        questions_solved = Comment.query.filter_by(
            student_id=current_user.id,
            is_solution=True
        ).count()

        # Resources shared
        resources_shared = Post.query.filter_by(
            student_id=current_user.id,
            post_type="resource"
        ).count()

        # Times bookmarked by others
        times_bookmarked = Bookmark.query.join(Post).filter(
            Post.student_id == current_user.id,
            Bookmark.student_id != current_user.id
        ).count()

        # Active connections
        connections_count = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).count()

        # Study buddies helped (future feature - placeholder)
        study_buddies = 0

        data = {
            "impact": {
                "people_reached": people_reached,
                "questions_answered": questions_answered,
                "questions_solved": questions_solved,
                "resources_shared": resources_shared,
                "times_bookmarked": times_bookmarked,
                "active_connections": connections_count,
                "study_buddies_helped": study_buddies,
                "total_helpful": current_user.total_helpful
            },
            "impact_score": (
                people_reached * 1 +
                questions_solved * 10 +
                current_user.total_helpful * 5 +
                resources_shared * 3
            )
        }

        cache_service.set(cache_key, data, ttl_seconds=180)

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Impact metrics error: {str(e)}")
        return error_response("Failed to load impact metrics")


@analytics_bp.route("/analytics/insights", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_insights(current_user):
    """
    AI-like insights and personalized suggestions
    """
    try:
        # Plan §4.4/§17.5: cache-aside, TTL 300s, TTL fallback only — also
        # avoids re-running the same badge-progress-calculation loop that
        # generate_insights() does internally (Insight 4), which
        # /badges/progress independently caches too. generated_at is part
        # of the cached payload (frozen at write time), not recomputed on
        # a hit, since a live timestamp on stale-relative-to-DB data would
        # misrepresent when these insights were actually computed.
        cache_key = f"sh:1:an:insights:{current_user.id}"
        data = cache_service.get(cache_key)
        if data is not None:
            return jsonify({"status": "success", "data": data})

        insights = generate_insights(current_user.id)

        data = {
            "insights": insights,
            "generated_at": datetime.datetime.utcnow().isoformat()
        }

        cache_service.set(cache_key, data, ttl_seconds=300)

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Insights error: {str(e)}")
        return error_response("Failed to generate insights")


@analytics_bp.route("/analytics/comparison", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_comparison_stats(current_user):
    """
    Compare user's stats with platform averages
    """
    try:
        # Plan §4.4.1: the split lives entirely inside get_average_user_stats()
        # (now @cache_service.cached, key sh:1:an:platform_avg, TTL 600s) —
        # this call is unchanged, and everything below (connections_count,
        # every multiplier) is deliberately NOT cached per-viewer: it's
        # cheap in-process arithmetic against the cached shared averages,
        # not a query, so a per-viewer cache entry would only add memory
        # footprint for no benefit.
        avg_stats = get_average_user_stats()

        # Calculate multipliers
        posts_multiplier = (current_user.total_posts / avg_stats["avg_posts"]) if avg_stats["avg_posts"] > 0 else 0
        rep_multiplier = (current_user.reputation / avg_stats["avg_reputation"]) if avg_stats["avg_reputation"] > 0 else 0
        helpful_multiplier = (current_user.total_helpful / avg_stats["avg_helpful"]) if avg_stats["avg_helpful"] > 0 else 0

        connections_count = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).count()

        connections_multiplier = (connections_count / avg_stats["avg_connections"]) if avg_stats["avg_connections"] > 0 else 0

        return jsonify({
            "status": "success",
            "data": {
                "your_stats": {
                    "posts": current_user.total_posts,
                    "reputation": current_user.reputation,
                    "helpful": current_user.total_helpful,
                    "connections": connections_count
                },
                "average_stats": {
                    "posts": round(avg_stats["avg_posts"], 1),
                    "reputation": round(avg_stats["avg_reputation"], 1),
                    "helpful": round(avg_stats["avg_helpful"], 1),
                    "connections": round(avg_stats["avg_connections"], 1)
                },
                "comparison": {
                    "posts": {
                        "multiplier": round(posts_multiplier, 1),
                        "status": "above" if posts_multiplier > 1 else "below"
                    },
                    "reputation": {
                        "multiplier": round(rep_multiplier, 1),
                        "status": "above" if rep_multiplier > 1 else "below"
                    },
                    "helpful": {
                        "multiplier": round(helpful_multiplier, 1),
                        "status": "above" if helpful_multiplier > 1 else "below"
                    },
                    "connections": {
                        "multiplier": round(connections_multiplier, 1),
                        "status": "above" if connections_multiplier > 1 else "below"
                    }
                }
            }
        })

    except Exception as e:
        current_app.logger.error(f"Comparison stats error: {str(e)}")
        return error_response("Failed to load comparison")


@analytics_bp.route("/analytics/post/<int:post_id>", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_post_analytics(current_user, post_id):
    """
    Detailed analytics for a specific post
    """
    try:
        post = Post.query.get(post_id)

        if not post:
            return error_response("Post not found", 404)

        if post.student_id != current_user.id:
            return error_response("Can only view analytics for your own posts", 403)

        # View timeline (last 30 days)
        thirty_days_ago = datetime.date.today() - datetime.timedelta(days=30)

        views_by_day = db.session.query(
            func.date(PostView.viewed_at).label('view_date'),
            func.count(PostView.id).label('views')
        ).filter(
            PostView.post_id == post_id,
            func.date(PostView.viewed_at) >= thirty_days_ago
        ).group_by(func.date(PostView.viewed_at)).order_by(func.date(PostView.viewed_at).asc()).all()

        # FIX: SQLite returns func.date() as a plain string, not a date object.
        # Calling .isoformat() on a string raises AttributeError, so we check
        # the type before deciding whether to call it.
        timeline = [{
            "date": v.view_date if isinstance(v.view_date, str) else v.view_date.isoformat(),
            "views": v.views
        } for v in views_by_day]

        # Reaction breakdown
        reactions = db.session.query(
            PostReaction.reaction_type,
            func.count(PostReaction.id).label('count')
        ).filter(PostReaction.post_id == post_id).group_by(
            PostReaction.reaction_type
        ).all()

        reaction_breakdown = {r.reaction_type: r.count for r in reactions}

        # Engagement rate
        engagement_rate = calculate_engagement_rate(
            post.views_count,
            post.positive_reactions_count,
            post.comments_count
        )

        return jsonify({
            "status": "success",
            "data": {
                "post": {
                    "id": post.id,
                    "title": post.title,
                    "posted_at": post.posted_at.isoformat()
                },
                "metrics": {
                    "views": post.views_count,
                    "reactions": post.positive_reactions_count,
                    "dislikes": post.dislikes_count,
                    "comments": post.comments_count,
                    "bookmarks": post.bookmark_count,
                    "engagement_rate": engagement_rate
                },
                "reactions": reaction_breakdown,
                "timeline": timeline
            }
        })

    except Exception as e:
        current_app.logger.error(f"Post analytics error: {str(e)}")
        return error_response("Failed to load post analytics")


@analytics_bp.route("/analytics/weekly-summary", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_weekly_summary(current_user):
    """
    Weekly digest data (for email/notification)
    """
    try:
        # Plan §4.4/§17.5: cache-aside, TTL 3600s — the route's own name
        # and purpose (a weekly digest) justifies an hour of staleness
        # outright, per plan §4.2's table for this row.
        cache_key = f"sh:1:an:weekly_summary:{current_user.id}"
        data = cache_service.get(cache_key)
        if data is not None:
            return jsonify({"status": "success", "data": data})

        week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)

        # New reputation earned
        rep_change = db.session.query(
            func.sum(ReputationHistory.points_change)
        ).filter(
            ReputationHistory.user_id == current_user.id,
            ReputationHistory.created_at >= week_ago
        ).scalar() or 0

        # New badges earned
        new_badges = UserBadge.query.filter(
            UserBadge.user_id == current_user.id,
            UserBadge.earned_at >= week_ago
        ).count()

        # Posts created this week
        posts_created = Post.query.filter(
            Post.student_id == current_user.id,
            Post.posted_at >= week_ago
        ).count()

        # AUDIT §4.2 FIX: same always-empty reaction_type="helpful" filter
        # as get_analytics_overview's this_week_helpful above — see that
        # site's comment for the full explanation. Replaced with the
        # literal 0 it always evaluated to; response shape
        # (summary.people_helped) is unchanged.
        helpful_this_week = 0

        # New connections
        new_connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted",
            Connection.responded_at >= week_ago
        ).count()

        # FIX: rep_change from func.sum() can be a Decimal or float.
        # Cast to int so the message renders as "25" not "25.0000".
        rep_change_int = int(rep_change)

        data = {
            "period": "last_7_days",
            "summary": {
                "reputation_earned": rep_change_int,
                "badges_earned": new_badges,
                "posts_created": posts_created,
                "people_helped": helpful_this_week,
                "new_connections": new_connections
            },
            "message": f"Great week! You earned {rep_change_int} reputation and helped {helpful_this_week} people! 🎉"
        }

        cache_service.set(cache_key, data, ttl_seconds=3600)

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Weekly summary error: {str(e)}")
        return error_response("Failed to load weekly summary")


@analytics_bp.route("/analytics/export", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def export_analytics(current_user):
    """
    Export analytics data as CSV
    Returns JSON that frontend can convert to CSV
    """
    try:
        export_type = request.args.get("type", "overview")

        # FIX: sanitise the export_type parameter against an explicit allowlist
        # so unrecognised values fall back to "overview" instead of silently
        # executing the default branch for unexpected input.
        if export_type not in _VALID_EXPORT_TYPES:
            export_type = "overview"

        if export_type == "activity":
            # Export activity heatmap
            ninety_days_ago = datetime.date.today() - datetime.timedelta(days=90)
            activities = UserActivity.query.filter(
                UserActivity.user_id == current_user.id,
                UserActivity.activity_date >= ninety_days_ago
            ).order_by(UserActivity.activity_date.asc()).all()

            export_data = [{
                "date": a.activity_date.isoformat(),
                "posts_created": a.posts_created,
                "comments_created": a.comments_created,
                "messages_sent": a.messages_sent,
                "helpful_count": a.helpful_count,
                "activity_score": a.activity_score
            } for a in activities]

        elif export_type == "reputation":
            # Export reputation history
            history = ReputationHistory.query.filter_by(
                user_id=current_user.id
            ).order_by(ReputationHistory.created_at.desc()).all()

            export_data = [{
                "date": h.created_at.isoformat(),
                "action": h.action,
                "points_change": h.points_change,
                "reputation_before": h.reputation_before,
                "reputation_after": h.reputation_after
            } for h in history]

        elif export_type == "posts":
            # Export post performance
            posts = Post.query.filter_by(student_id=current_user.id).all()

            export_data = [{
                "id": p.id,
                "title": p.title,
                "post_type": p.post_type,
                "posted_at": p.posted_at.isoformat(),
                "views": p.views_count,
                "likes": p.positive_reactions_count,
                "comments": p.comments_count,
                "bookmarks": p.bookmark_count,
                "is_solved": p.is_solved
            } for p in posts]

        else:
            # Overview export
            export_data = [{
                "metric": "Total Posts",
                "value": current_user.total_posts
            }, {
                "metric": "Total Reputation",
                "value": current_user.reputation
            }, {
                "metric": "Reputation Level",
                "value": current_user.reputation_level
            }, {
                "metric": "Total Helpful",
                "value": current_user.total_helpful
            }, {
                "metric": "Login Streak",
                "value": current_user.login_streak
            }]

        return jsonify({
            "status": "success",
            "data": {
                "export_type": export_type,
                "records": export_data,
                "generated_at": datetime.datetime.utcnow().isoformat()
            }
        })

    except Exception as e:
        current_app.logger.error(f"Export analytics error: {str(e)}")
        return error_response("Failed to export analytics")
