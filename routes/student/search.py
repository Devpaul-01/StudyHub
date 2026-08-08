"""
StudyHub - Advanced Search System
Search users, posts, threads with intelligent filtering and ranking

Search logic (query building, filtering, pagination, formatting) now lives
in services/search_service.py — the ONE search implementation per
Document 1 §6.1. GET /search/unified?type=X and the dedicated GET
/search/users, /search/posts, /search/threads, /search/global endpoints
all call the exact same service functions now; there is no second,
independently-drifting "_unified" code path anymore.

Redesign note (confirmed safe since the frontend wasn't yet integrated):
search_service.search_threads() returns thread SUMMARIES only — the old
_search_threads_unified's full per-thread members_data array (every
member + a per-member connection-status lookup, for every thread on the
page) is gone. Full member lists are available via the existing
GET /threads/<id>/members endpoint.
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import or_, and_, func, desc
import datetime

from models import (
    User, StudentProfile, Post, Thread, ThreadMember,
    Comment, PostReaction, Connection,
    OnboardingDetails, ThreadJoinRequest
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)
from services import search_service
# Phase 5b (Document 4 §1): PUBLIC_READ-tier limiting on search endpoints —
# these are read-only, can be hit anonymously-in-spirit even though most
# require a token, and are the kind of expensive-query endpoint that's
# cheap to hammer and costly to serve. ip_key() per the ticket's explicit
# table entry for search.py.
from services.rate_limit_service import limiter, RateLimitTier, ip_key, user_or_ip_key


search_bp = Blueprint("student_search", __name__)


# ============================================================================
# UNIFIED SEARCH ENDPOINT
# ============================================================================

def _parse_common_list_arg(args, key: str) -> list[str] | None:
    """`?skills=a,b,c` / `?tags=a,b,c` -> ["a", "b", "c"] or None if absent."""
    raw = args.get(key, "").strip()
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


def _parse_bool_arg(args, key: str) -> bool | None:
    raw = args.get(key)
    if raw is None:
        return None
    return raw.lower() in ("true", "1", "yes")


@search_bp.route("/search/unified", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def unified_search(current_user):
    """
    Single endpoint that routes to appropriate search based on 'type' param.

    Query params:
    - q: Search query (required, min 2 chars)
    - type: Search type - users|posts|threads|all (default: users)

    Additional filters based on type:

    For users:
    - department, class_level, skills (comma-separated), reputation_min, sort

    For posts:
    - post_type, department, tags (comma-separated), solved, date_from,
      date_to, sort

    For threads:
    - department, is_open, has_space

    For all:
    - limit: Results per category (default 5, max 10)

    Common params:
    - page: Page number (default 1)
    - per_page: Results per page (default 20, max 50)
    """
    try:
        query_str = request.args.get("q", "").strip()
        search_type = request.args.get("type", "users").lower()

        if not query_str:
            return error_response("Search query required")

        if len(query_str) < 2:
            return error_response("Search query too short (minimum 2 characters)")

        valid_types = ["users", "posts", "threads", "all"]
        if search_type not in valid_types:
            return error_response(f"Invalid search type. Must be one of: {', '.join(valid_types)}")

        args = request.args
        page = args.get("page", 1, type=int)
        per_page = args.get("per_page", 20, type=int)

        if search_type == "users":
            data = search_service.search_users(
                query_str,
                department=args.get("department", "").strip() or None,
                class_level=args.get("class_level", "").strip() or None,
                skills=_parse_common_list_arg(args, "skills"),
                reputation_min=args.get("reputation_min", type=int),
                sort=args.get("sort", "relevance"),
                page=page, per_page=per_page,
                viewer_id=current_user.id,
            )
            return jsonify({"status": "success", "search_type": "users", "data": data})

        elif search_type == "posts":
            data = search_service.search_posts(
                query_str,
                post_type=args.get("post_type", "").strip() or None,
                department=args.get("department", "").strip() or None,
                tags=_parse_common_list_arg(args, "tags"),
                solved=_parse_bool_arg(args, "solved"),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
                sort=args.get("sort", "recent"),
                page=page, per_page=per_page,
            )
            return jsonify({"status": "success", "search_type": "posts", "data": data})

        elif search_type == "threads":
            data = search_service.search_threads(
                query_str,
                department=args.get("department", "").strip() or None,
                is_open=_parse_bool_arg(args, "is_open"),
                has_space=_parse_bool_arg(args, "has_space"),
                page=page, per_page=per_page,
                viewer_id=current_user.id,
            )
            return jsonify({"status": "success", "search_type": "threads", "data": data})

        elif search_type == "all":
            limit = min(args.get("limit", 5, type=int), 10)
            data = search_service.search_all(query_str, limit_per_type=limit, viewer_id=current_user.id)
            return jsonify({"status": "success", "search_type": "all", "data": data})

    except Exception as e:
        current_app.logger.error(f"Unified search error: {str(e)}")
        return error_response("Search failed")


@search_bp.route("/search/users", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def search_users(current_user):
    """
    Search users with advanced filters.

    Query params:
    - q: Search query (username, name)
    - department: Filter by department
    - class_level: Filter by class level
    - skills: Comma-separated skills to match
    - reputation_min: Minimum reputation
    - connected: if truthy, restrict to accepted connections only
    - sort: Sort by (relevance, reputation, name, recent)
    - page: Page number
    - per_page: Results per page (max 50)
    """
    try:
        query_str = request.args.get("q", "").strip()
        department = request.args.get("department", "").strip() or None
        class_level = request.args.get("class_level", "").strip() or None
        connected_only = bool(request.args.get("connected", "").strip())
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)

        data = search_service.search_users(
            query_str,
            department=department,
            class_level=class_level,
            skills=_parse_common_list_arg(request.args, "skills"),
            reputation_min=request.args.get("reputation_min", type=int),
            connected_only=connected_only,
            sort=request.args.get("sort", "relevance"),
            page=page, per_page=per_page,
            viewer_id=current_user.id,
        )

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"User search error: {str(e)}")
        return error_response("Search failed")


@search_bp.route("/search/users/top-contributors", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def top_contributors(current_user):
    """
    Get top contributors by reputation
    
    Query params:
    - department: Filter by department
    - period: time period (week, month, all_time)
    - limit: Number of results (max 50)
    """
    try:
        department = request.args.get("department", "").strip()
        period = request.args.get("period", "all_time")
        limit = min(request.args.get("limit", 20, type=int), 50)
        
        query = User.query.filter(User.status == "approved")
        
        # Department filter
        if department:
            query = query.join(StudentProfile).filter(
                StudentProfile.department == department
            )
        
        # Period filter (based on recent activity)
        if period == "week":
            cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=7)
            query = query.filter(User.last_active >= cutoff)
        elif period == "month":
            cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=30)
            query = query.filter(User.last_active >= cutoff)
        
        # Sort by reputation
        top_users = query.order_by(User.reputation.desc()).limit(limit).all()
        
        users_data = []
        for idx, user in enumerate(top_users, 1):
            profile = StudentProfile.query.filter_by(user_id=user.id).first()
            users_data.append({
                "rank": idx,
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "avatar": user.avatar,
                "department": profile.department if profile else None,
                "reputation": user.reputation,
                "reputation_level": user.reputation_level,
                "total_posts": user.total_posts,
                "total_helpful": user.total_helpful
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "top_contributors": users_data,
                "period": period,
                "department": department if department else "All Departments"
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Top contributors error: {str(e)}")
        return error_response("Failed to load top contributors")


# ============================================================================
# POST SEARCH
# ============================================================================

@search_bp.route("/search/posts", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def search_posts(current_user):
    """
    Search posts with advanced filters.

    Query params:
    - q: Search query (title, content)
    - type: Post type filter
    - department: Filter by department
    - tags: Comma-separated tags
    - solved: Boolean - only solved/unsolved questions
    - date_from: Start date (ISO format)
    - date_to: End date (ISO format)
    - sort: Sort by (relevance, recent, popular, trending)
    - page: Page number
    - per_page: Results per page
    """
    try:
        query_str = request.args.get("q", "").strip()

        data = search_service.search_posts(
            query_str,
            post_type=request.args.get("type", "").strip() or None,
            department=request.args.get("department", "").strip() or None,
            tags=_parse_common_list_arg(request.args, "tags"),
            solved=_parse_bool_arg(request.args, "solved"),
            date_from=request.args.get("date_from"),
            date_to=request.args.get("date_to"),
            sort=request.args.get("sort", "recent"),
            page=request.args.get("page", 1, type=int),
            per_page=request.args.get("per_page", 20, type=int),
        )

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Post search error: {str(e)}")
        return error_response("Search failed")


@search_bp.route("/search/posts/unanswered", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def unanswered_posts(current_user):
    """
    Get unanswered questions/problems - great for earning reputation!
    
    Query params:
    - department: Filter by department
    - limit: Number of results
    """
    try:
        department = request.args.get("department", "").strip()
        tags = request.args.get('tags')
        
        limit = min(request.args.get("limit", 20, type=int), 50)
        
        query = Post.query.filter(
            Post.post_type.in_(["question", "problem", "discussion"]),
            Post.is_solved == False,
            Post.comments_count == 0  # No comments yet
        )
        
        if department:
            query = query.filter(Post.department == department)
        if tags:
            tags_list = [t.strip().lower() for t in tags.split(",")]
            query = query.filter(Post.tags.in_(tags))
        
        # Sort by recent first
        unanswered = query.order_by(Post.posted_at.desc()).limit(limit).all()
        
        posts_data = []
        for post in unanswered:
            author = User.query.get(post.student_id)
            posts_data.append({
                "id": post.id,
                "title": post.title,
                "post_type": post.post_type,
                "department": post.department,
                "tags": post.tags,
                "posted_at": post.posted_at.isoformat(),
                "author": {
                    "id": author.id,
                    "username": author.username,
                    "name": author.name
                } if author else None
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "unanswered_posts": posts_data,
                "total": len(posts_data),
                "department": department if department else "All Departments"
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Unanswered posts error: {str(e)}")
        return error_response("Failed to load unanswered posts")


@search_bp.route("/search/posts/trending", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def trending_posts(current_user):
    """
    Get trending posts - hot discussions right now
    """
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")
        profile = user.student_profile
        default_dept = profile.department if profile else ""
        department = request.args.get("department", default_dept or "").strip()
        limit = min(request.args.get("limit", 20, type=int), 50)

        query = Post.query
        if department:
            query = query.filter(Post.department == department)

        posts = query.order_by(
            (Post.positive_reactions_count * 2 + Post.comments_count * 1.5 + Post.views_count / 10).desc()
        ).limit(limit).all()

        # Batch-fetch authors
        author_ids = {p.student_id for p in posts}
        authors_map = {u.id: u for u in User.query.filter(User.id.in_(author_ids)).all()}

        posts_data = []
        for post in posts:
            author = authors_map.get(post.student_id)
            posts_data.append({
                "id": post.id,
                "title": post.title,
                "post_type": post.post_type,
                "department": post.department,
                "likes_count": post.positive_reactions_count,
                "comments_count": post.comments_count,
                "views": post.views_count,
                "posted_at": post.posted_at.isoformat(),
                "author": {
                    "username": author.username,
                    "name": author.name,
                    "avatar": author.avatar,
                } if author else None,
            })

        return jsonify({
            "status": "success",
            "data": {
                "trending_posts": posts_data,
                "source": "live_calculation",
            },
        })
    except Exception as e:
        current_app.logger.error(f"Trending posts error: {str(e)}")
        return error_response("Failed to load trending posts")

# ============================================================================
# THREAD SEARCH
# ============================================================================

@search_bp.route("/search/threads", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def search_threads(current_user):
    """
    Search collaboration threads.

    Query params:
    - q: Search query (title, description)
    - department: Filter by department
    - is_open: Boolean - only open threads
    - has_space: Boolean - threads accepting members
    - page: Page number

    Redesign note (Document 1 §6.1): returns thread SUMMARIES only — full
    per-thread member lists are no longer embedded in search results. Use
    GET /threads/<id>/members for that.
    """
    try:
        query_str = request.args.get("q", "").strip()

        data = search_service.search_threads(
            query_str,
            department=request.args.get("department", "").strip() or None,
            is_open=_parse_bool_arg(request.args, "is_open"),
            has_space=_parse_bool_arg(request.args, "has_space"),
            page=request.args.get("page", 1, type=int),
            per_page=request.args.get("per_page", 20, type=int),
            viewer_id=current_user.id,
        )

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Thread search error: {str(e)}")
        return error_response("Search failed")


@search_bp.route("/search/threads/open", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def open_threads(current_user):
    """
    Get threads currently accepting new members
    Perfect for joining study groups!
    """
    try:
        department = request.args.get("department", "").strip()
        limit = min(request.args.get("limit", 20, type=int), 50)
        
        query = Thread.query.filter(
            Thread.is_open == True,
            Thread.member_count < Thread.max_members
        )
        
        if department:
            query = query.filter(Thread.department == department)
        
        open_threads = query.order_by(Thread.last_activity.desc()).limit(limit).all()
        
        # Check membership
        thread_ids = [t.id for t in open_threads]
        memberships = ThreadMember.query.filter(
            ThreadMember.thread_id.in_(thread_ids),
            ThreadMember.student_id == current_user.id
        ).all()
        member_thread_ids = {m.thread_id for m in memberships}
        
        threads_data = []
        for thread in open_threads:
            creator = User.query.get(thread.creator_id)
            threads_data.append({
                "id": thread.id,
                "title": thread.title,
                "description": thread.description,
                "department": thread.department,
                "member_count": thread.member_count,
                "max_members": thread.max_members,
                "spaces_left": thread.max_members - thread.member_count,
                "is_member": thread.id in member_thread_ids,
                "last_activity": thread.last_activity.isoformat(),
                "creator": {
                    "username": creator.username,
                    "name": creator.name
                } if creator else None
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "open_threads": threads_data,
                "total": len(threads_data)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Open threads error: {str(e)}")
        return error_response("Failed to load open threads")
        

# ============================================================================
# GLOBAL SEARCH (Search Everything)
# ============================================================================

@search_bp.route("/search/global", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def global_search(current_user):
    """
    Search across users, posts, and threads simultaneously.
    Returns top results from each category.

    Query param:
    - q: Search query (required)
    - limit: Results per category (default 5, max 10)
    """
    try:
        query_str = request.args.get("q", "").strip()

        if not query_str:
            return error_response("Search query required")

        if len(query_str) < 2:
            return error_response("Search query too short (minimum 2 characters)")

        limit = min(request.args.get("limit", 5, type=int), 10)

        data = search_service.search_all(query_str, limit_per_type=limit, viewer_id=current_user.id)

        return jsonify({
            "status": "success",
            "data": {
                "query": data["query"],
                "results": data["all"],
                "counts": data["counts"],
            }
        })

    except Exception as e:
        current_app.logger.error(f"Global search error: {str(e)}")
        return error_response("Search failed")


@search_bp.route("/search/suggestions", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def search_suggestions(current_user):
    """
    Get search suggestions based on partial query
    Used for autocomplete in search bar
    
    Query param:
    - q: Partial query (minimum 2 chars)
    - type: Suggestion type (users, posts, threads, all)
    """
    try:
        query_str = request.args.get("q", "").strip()
        suggestion_type = request.args.get("type", "all")
        
        if len(query_str) < 2:
            return jsonify({
                "status": "success",
                "data": {"suggestions": []}
            })
        
        suggestions = []
        search_pattern = f"{query_str}%"  # Prefix match for better autocomplete
        
        # User suggestions
        if suggestion_type in ["users", "all"]:
            users = User.query.filter(
                or_(
                    User.username.ilike(search_pattern),
                    User.name.ilike(search_pattern)
                ),
                User.status == "approved"
            ).limit(5).all()
            
            for user in users:
                suggestions.append({
                    "type": "user",
                    "id": user.id,
                    "text": user.username,
                    "display": f"@{user.username} - {user.name}",
                    "avatar": user.avatar
                })
        
        # Post suggestions (by title)
        if suggestion_type in ["posts", "all"]:
            posts = Post.query.filter(
                Post.title.ilike(search_pattern)
            ).order_by(Post.posted_at.desc()).limit(5).all()
            
            for post in posts:
                suggestions.append({
                    "type": "post",
                    "id": post.id,
                    "text": post.title,
                    "display": f"📄 {post.title}",
                    "post_type": post.post_type
                })
        
        # Thread suggestions
        if suggestion_type in ["threads", "all"]:
            threads = Thread.query.filter(
                Thread.title.ilike(search_pattern)
            ).limit(5).all()
            
            for thread in threads:
                suggestions.append({
                    "type": "thread",
                    "id": thread.id,
                    "text": thread.title,
                    "display": f"🧵 {thread.title}",
                    "member_count": thread.member_count
                })
        
        return jsonify({
            "status": "success",
            "data": {
                "query": query_str,
                "suggestions": suggestions[:10]  # Limit to 10 total
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Search suggestions error: {str(e)}")
        return error_response("Failed to get suggestions")


@search_bp.route("/search/tags/popular", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
def popular_tags():
    """
    Get most popular tags across all posts
    Used for tag suggestions and discovery
    
    No auth required - public endpoint
    """
    try:
        limit = min(request.args.get("limit", 30, type=int), 100)
        
        # Get all posts with tags
        posts = Post.query.filter(Post.tags.isnot(None)).all()
        
        # Count tag occurrences
        tag_counts = {}
        for post in posts:
            if post.tags:
                for tag in post.tags:
                    tag_lower = tag.lower()
                    if tag_lower in tag_counts:
                        tag_counts[tag_lower]["count"] += 1
                    else:
                        tag_counts[tag_lower] = {
                            "tag": tag,
                            "count": 1
                        }
        
        # Sort by popularity
        popular = sorted(
            tag_counts.values(),
            key=lambda x: x["count"],
            reverse=True
        )[:limit]
        
        return jsonify({
            "status": "success",
            "data": {
                "popular_tags": [t["tag"] for t in popular],
                "detailed": popular
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Popular tags error: {str(e)}")
        return error_response("Failed to load popular tags")


# ============================================================================
# ADVANCED FILTERS & DISCOVERY
# ============================================================================

@search_bp.route("/search/filters/departments", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
def get_departments():
    """
    Get list of all departments with student counts
    Used pfor filter dropdowns
    """
    try:
        departments = db.session.query(
            StudentProfile.department,
            func.count(StudentProfile.id).label('student_count')
        ).group_by(StudentProfile.department).order_by(
            StudentProfile.department.asc()
        ).all()
        
        dept_data = [{
            "name": dept,
            "student_count": count
        } for dept, count in departments]
        
        return jsonify({
            "status": "success",
            "data": {"departments": dept_data}
        })
        
    except Exception as e:
        current_app.logger.error(f"Get departments error: {str(e)}")
        return error_response("Failed to load departments")


@search_bp.route("/search/filters/class-levels", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
def get_class_levels():
    """
    Get list of all class levels with student counts
    """
    try:
        class_levels = db.session.query(
            StudentProfile.class_name,
            func.count(StudentProfile.id).label('student_count')
        ).group_by(StudentProfile.class_name).order_by(
            StudentProfile.class_name.asc()
        ).all()
        
        levels_data = [{
            "name": level,
            "student_count": count
        } for level, count in class_levels]
        
        return jsonify({
            "status": "success",
            "data": {"class_levels": levels_data}
        })
        
    except Exception as e:
        current_app.logger.error(f"Get class levels error: {str(e)}")
        return error_response("Failed to load class levels")


@search_bp.route("/search/discovery/for-you", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def personalized_discovery(current_user):
    """
    Personalized content discovery based on user's interests
    
    Shows:
    - Posts in your department
    - Posts with tags matching your skills
    - Threads you might be interested in
    - Users with similar interests
    """
    try:
        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
        
        # Get user's interests
        user_skills = [s.lower() for s in (current_user.skills or [])]
        user_dept = profile.department if profile else None
        
        # Recommended posts (same department, relevant tags)
        posts_query = Post.query
        
        if user_dept:
            posts_query = posts_query.filter(Post.department == user_dept)
        
        # Get recent popular posts
        week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        recommended_posts = posts_query.filter(
            Post.posted_at >= week_ago
        ).order_by(
            (Post.positive_reactions_count + Post.comments_count).desc()
        ).limit(10).all()
        
        posts_data = []
        for post in recommended_posts:
            author = User.query.get(post.student_id)
            posts_data.append({
                "id": post.id,
                "title": post.title,
                "post_type": post.post_type,
                "likes_count": post.positive_reactions_count,
                "comments_count": post.comments_count,
                "author": {
                    "username": author.username
                } if author else None
            })
        
        # Recommended threads (same department, has space)
        recommended_threads = Thread.query.filter(
            Thread.department == user_dept,
            Thread.is_open == True,
            Thread.member_count < Thread.max_members
        ).order_by(Thread.last_activity.desc()).limit(5).all()
        
        threads_data = []
        for thread in recommended_threads:
            threads_data.append({
                "id": thread.id,
                "title": thread.title,
                "member_count": thread.member_count,
                "max_members": thread.max_members
            })
        
        # Recommended users (same department, not connected)
        existing_connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            )
        ).all()
        
        excluded_ids = [current_user.id]
        for conn in existing_connections:
            excluded_ids.append(
                conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
            )
        
        recommended_users = User.query.join(StudentProfile).filter(
            StudentProfile.department == user_dept,
            User.id.notin_(excluded_ids),
            User.status == "approved"
        ).order_by(User.reputation.desc()).limit(5).all()
        
        users_data = []
        for user in recommended_users:
            user_profile = StudentProfile.query.filter_by(user_id=user.id).first()
            users_data.append({
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "avatar": user.avatar,
                "reputation_level": user.reputation_level
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "recommended_posts": posts_data,
                "recommended_threads": threads_data,
                "recommended_users": users_data,
                "based_on": {
                    "department": user_dept,
                    "skills": user_skills[:3]
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Personalized discovery error: {str(e)}")
        return error_response("Failed to load recommendations")


# ============================================================================
# SEARCH HISTORY (Optional - for better UX)
# ============================================================================

@search_bp.route("/search/history", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def search_history(current_user):
    """
    Get user's recent search queries
    Stored in user metadata for quick access
    """
    try:
        metadata = current_user.user_metadata if current_user.user_metadata else {}
        search_history = metadata.get("search_history", [])
        
        return jsonify({
            "status": "success",
            "data": {
                "recent_searches": search_history[-10:]  # Last 10 searches
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Search history error: {str(e)}")
        return error_response("Failed to load search history")


@search_bp.route("/search/history", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def save_search_query(current_user):
    """
    Save a search query to user's history
    
    Body: {"query": "machine learning"}
    """
    try:
        data = request.get_json()
        query = data.get("query", "").strip()
        
        if not query or len(query) < 2:
            return error_response("Invalid query")
        
        metadata = current_user.user_metadata if current_user.user_metadata else {}
        search_history = metadata.get("search_history", [])
        
        # Remove duplicate if exists
        if query in search_history:
            search_history.remove(query)
        
        # Add to beginning
        search_history.insert(0, query)
        
        # Keep only last 20 searches
        search_history = search_history[:20]
        
        metadata["search_history"] = search_history
        current_user.user_metadata = metadata
        
        db.session.commit()
        
        return success_response("Search saved")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Save search error: {str(e)}")
        return error_response("Failed to save search")


@search_bp.route("/search/history", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def clear_search_history(current_user):
    """
    Clear all search history
    """
    try:
        metadata = current_user.user_metadata if current_user.user_metadata else {}
        metadata["search_history"] = []
        current_user.user_metadata = metadata
        
        db.session.commit()
        
        return success_response("Search history cleared")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Clear search history error: {str(e)}")
        return error_response("Failed to clear history")
                    
                   
       