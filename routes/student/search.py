"""
StudyHub - Advanced Search System
Search users, posts, threads with intelligent filtering and ranking
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


search_bp = Blueprint("student_search", __name__)


# ============================================================================
# USER SEARCH
# ============================================================================
# ============================================================================
# UNIFIED SEARCH ENDPOINT
# ============================================================================

@search_bp.route("/search/unified", methods=["GET"])
@token_required
def unified_search(current_user):
    """
    Single endpoint that routes to appropriate search based on 'type' param
    
    Query params:
    - q: Search query (required, min 2 chars)
    - type: Search type - users|posts|threads|all (default: users)
    
    Additional filters based on type:
    
    For users:
    - department: Filter by department
    - class_level: Filter by class level
    - skills: Comma-separated skills
    - reputation_min: Minimum reputation
    - sort: relevance|reputation|name|recent
    
    For posts:
    - type: Post type (question|problem|discussion|resource|announcement)
    - department: Filter by department
    - tags: Comma-separated tags
    - solved: true|false
    - date_from: ISO format date
    - date_to: ISO format date
    - sort: relevance|recent|popular|trending
    
    For threads:
    - department: Filter by department
    - is_open: true|false
    - has_space: true|false
    
    For all:
    - limit: Results per category (default 5, max 10)
    
    Common params:
    - page: Page number (default 1)
    - per_page: Results per page (default 20, max 50)
    """
    try:
        # Get and validate query
        query_str = request.args.get("q", "").strip()
        search_type = request.args.get("type", "users").lower()
        
        # Validate query
        if not query_str:
            return error_response("Search query required")
        
        if len(query_str) < 2:
            return error_response("Search query too short (minimum 2 characters)")
        
        # Validate search type
        valid_types = ["users", "posts", "threads", "all"]
        if search_type not in valid_types:
            return error_response(f"Invalid search type. Must be one of: {', '.join(valid_types)}")
        
        # Route to appropriate search function
        if search_type == "users":
            return _search_users_unified(current_user, query_str, request.args)
        elif search_type == "posts":
            return _search_posts_unified(current_user, query_str, request.args)
        elif search_type == "threads":
            return _search_threads_unified(current_user, query_str, request.args)
        elif search_type == "all":
            return _search_all_unified(current_user, query_str, request.args)
        
    except Exception as e:
        current_app.logger.error(f"Unified search error: {str(e)}")
        return error_response("Search failed")


# ============================================================================
# INTERNAL SEARCH FUNCTIONS (Used by unified endpoint)
# ============================================================================

def _search_users_unified(current_user, query_str, args):
    """Internal function to search users"""
    try:
        search_pattern = f"%{query_str}%"
        
        # Base query - exclude self
        query = User.query.filter(
            User.id != current_user.id,
            User.status == "approved"
        )
        
        # Text search
        query = query.filter(
            or_(
                User.username.ilike(search_pattern),
                User.name.ilike(search_pattern)
            )
        )
        
        # Department filter
        department = args.get("department", "").strip()
        if department:
            query = query.join(StudentProfile).filter(
                StudentProfile.department == department
            )
        
        # Class level filter
        class_level = args.get("class_level", "").strip()
        if class_level:
            if not department:
                query = query.join(StudentProfile)
            query = query.filter(StudentProfile.class_name == class_level)
        
        # Skills filter
        skills_param = args.get("skills", "").strip()
        if skills_param:
            skills_list = [s.strip().lower() for s in skills_param.split(",")]
            # H-7 fix: '?|' is a PostgreSQL JSONB-only operator, not valid for
            # a plain db.JSON column (and not supported on SQLite at all).
            # OR-ing .contains() checks is dialect-portable.
            query = query.filter(or_(*[User.skills.contains([s]) for s in skills_list]))
        
        # Reputation filter
        rep_min = args.get("reputation_min", type=int)
        if rep_min:
            query = query.filter(User.reputation >= rep_min)
        
        # Sorting
        sort_by = args.get("sort", "relevance")
        if sort_by == "reputation":
            query = query.order_by(User.reputation.desc())
        elif sort_by == "name":
            query = query.order_by(User.name.asc())
        elif sort_by == "recent":
            query = query.order_by(User.joined_at.desc())
        else:  # relevance
            query = query.order_by(
                User.username.ilike(f"{query_str}%").desc(),
                User.reputation.desc()
            )
        
        # Pagination
        page = args.get("page", 1, type=int)
        per_page = min(args.get("per_page", 20, type=int), 50)
        
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Get connection status
        user_ids = [u.id for u in paginated.items]
        connections = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, 
                     Connection.receiver_id.in_(user_ids)),
                and_(Connection.requester_id.in_(user_ids), 
                     Connection.receiver_id == current_user.id)
            )
        ).all()
        
        connection_map = {}
        for conn in connections:
            other_id = (conn.receiver_id if conn.requester_id == current_user.id 
                       else conn.requester_id)
            if conn.status == "accepted":
                connection_map[other_id] = "connected"
            elif conn.status == "pending":
                connection_map[other_id] = ("pending_sent" if conn.requester_id == current_user.id 
                                           else "pending_received")
        
        # Format results
        users_data = []
        for user in paginated.items:
            privacy_settings = user.privacy_settings or {}
            profile_private = privacy_settings.get("set_profile_private", False)
            profile = StudentProfile.query.filter_by(user_id=user.id).first()
            
            users_data.append({
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "avatar": user.avatar,
                "bio": user.bio,
                "private": profile_private,
                "department": profile.department if profile else None,
                "class_level": profile.class_name if profile else None,
                "reputation": user.reputation if not profile_private else None,
                "reputation_level": user.reputation_level if not profile_private else None,
                "skills": user.skills[:5] if user.skills else [],
                "connection_status": connection_map.get(user.id, "none")
            })
        
        return jsonify({
            "status": "success",
            "search_type": "users",
            "data": {
                "users": users_data,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": paginated.total,
                    "pages": paginated.pages,
                    "has_next": paginated.has_next,
                    "has_prev": paginated.has_prev
                },
                "filters_applied": {
                    "query": query_str,
                    "department": department,
                    "class_level": class_level,
                    "skills": skills_param,
                    "sort": sort_by
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"User search error: {str(e)}")
        return error_response("User search failed")


def _search_posts_unified(current_user, query_str, args):
    """Internal function to search posts"""
    try:
        search_pattern = f"%{query_str}%"
        
        # Base query
        query = Post.query.filter(
            or_(
                Post.title.ilike(search_pattern),
                Post.text_content.ilike(search_pattern)
            )
        )
        
        # Post type filter
        post_type = args.get("post_type", "").strip()
        if post_type:
            query = query.filter(Post.post_type == post_type)
        
        # Department filter
        department = args.get("department", "").strip()
        if department:
            query = query.filter(Post.department == department)
        
        # Tags filter
        tags_param = args.get("tags", "").strip()
        if tags_param:
            tags_list = [t.strip().lower() for t in tags_param.split(",")]
            # H-7 fix: see note above — '?|' is Postgres-JSONB-only.
            query = query.filter(or_(*[Post.tags.contains([t]) for t in tags_list]))
        
        # Solved filter
        solved = args.get("solved")
        if solved is not None:
            is_solved = solved.lower() in ['true', '1', 'yes']
            query = query.filter(Post.is_solved == is_solved)
        
        # Date range filters
        date_from = args.get("date_from")
        if date_from:
            try:
                from_date = datetime.datetime.fromisoformat(date_from)
                query = query.filter(Post.posted_at >= from_date)
            except ValueError:
                pass
        
        date_to = args.get("date_to")
        if date_to:
            try:
                to_date = datetime.datetime.fromisoformat(date_to)
                query = query.filter(Post.posted_at <= to_date)
            except ValueError:
                pass
        
        # Sorting
        sort_by = args.get("sort", "recent")
        if sort_by == "popular":
            query = query.order_by(
                (Post.positive_reactions_count + Post.comments_count).desc()
            )
        elif sort_by == "trending":
            week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
            query = query.filter(Post.posted_at >= week_ago).order_by(
                (Post.positive_reactions_count * 2 + Post.comments_count).desc()
            )
        elif sort_by == "recent":
            query = query.order_by(Post.posted_at.desc())
        else:  # relevance
            query = query.order_by(
                Post.title.ilike(f"%{query_str}%").desc(),
                Post.posted_at.desc()
            )
        
        # Pagination
        page = args.get("page", 1, type=int)
        per_page = min(args.get("per_page", 20, type=int), 50)
        
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Format results
        posts_data = []
        for post in paginated.items:
            author = User.query.get(post.student_id)
            
            posts_data.append({
                "id": post.id,
                "title": post.title,
                "post_type": post.post_type,
                "department": post.department,
                "tags": post.tags,
                "excerpt": post.text_content[:200] if post.text_content else None,
                "reactions_count": post.positive_reactions_count,
                "comments_count": post.comments_count,
                "views": post.views_count,
                "is_solved": post.is_solved,
                "thread_enabled": post.thread_enabled,
                "posted_at": post.posted_at.isoformat(),
                "author": {
                    "id": author.id,
                    "username": author.username,
                    "name": author.name,
                    "avatar": author.avatar,
                    "reputation_level": author.reputation_level
                } if author else None
            })
        
        return jsonify({
            "status": "success",
            "search_type": "posts",
            "data": {
                "posts": posts_data,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": paginated.total,
                    "pages": paginated.pages,
                    "has_next": paginated.has_next,
                    "has_prev": paginated.has_prev
                },
                "filters_applied": {
                    "query": query_str,
                    "post_type": post_type,
                    "department": department,
                    "tags": tags_param,
                    "solved": solved,
                    "sort": sort_by
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Post search error: {str(e)}")
        return error_response("Post search failed")


def _search_threads_unified(current_user, query_str, args):
    """Internal function to search threads with complete member details"""
    try:
        search_pattern = f"%{query_str}%"
        
        # Base query
        query = Thread.query.filter(
            or_(
                Thread.title.ilike(search_pattern),
                Thread.description.ilike(search_pattern)
            )
        )
        
        # Department filter
        department = args.get("department", "").strip()
        if department:
            query = query.filter(Thread.department == department)
        
        # Open threads filter
        is_open = args.get("is_open")
        if is_open is not None:
            open_status = is_open.lower() in ['true', '1', 'yes']
            query = query.filter(Thread.is_open == open_status)
        
        # Has space filter
        has_space = args.get("has_space")
        if has_space and has_space.lower() in ['true', '1', 'yes']:
            query = query.filter(Thread.member_count < Thread.max_members)
        
        # Sort by recent activity
        query = query.order_by(Thread.last_activity.desc())
        
        # Pagination
        page = args.get("page", 1, type=int)
        per_page = min(args.get("per_page", 20, type=int), 50)
        
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Check user's membership
        thread_ids = [t.id for t in paginated.items]
        memberships = ThreadMember.query.filter(
            ThreadMember.thread_id.in_(thread_ids),
            ThreadMember.student_id == current_user.id
        ).all()
        member_thread_ids = {m.thread_id for m in memberships}
        
        # Format results with member details
        threads_data = []
        for thread in paginated.items:
            creator = User.query.get(thread.creator_id)
            post = Post.query.get(thread.post_id) if thread.post_id else None
            
            # Fetch all members for this thread
            thread_members = ThreadMember.query.filter_by(thread_id=thread.id).all()
            members_data = []
            
            for member in thread_members:
                author = User.query.get(member.student_id)
                if not author:
                    continue
                
                # Get student profile for department/class info
                profile = StudentProfile.query.filter_by(user_id=author.id).first()
                onboarding = OnboardingDetails.query.filter_by(user_id=author.id).first()
                
                # Check connection status between current user and this member
                connection = Connection.query.filter(
                    or_(
                        and_(Connection.requester_id == current_user.id, 
                             Connection.receiver_id == author.id),
                        and_(Connection.receiver_id == current_user.id, 
                             Connection.requester_id == author.id)
                    )
                ).first()
                
                members_data.append({
                    "id": author.id,
                    "name": author.name,
                    "username": author.username,
                    "avatar": author.avatar,
                    "connection_status": connection.status if connection else None,
                    "reputation": author.reputation,
                    "reputation_level": author.reputation_level,
                    "department": onboarding.department if onboarding else (profile.department if profile else None),
                    "class_level": onboarding.class_level if onboarding else (profile.class_name if profile else None),
                    "role": member.role,
                    "joined_at": member.joined_at.isoformat()
                })
            
            # Check for pending join request
            pending_request = ThreadJoinRequest.query.filter_by(
                thread_id=thread.id,
                requester_id=current_user.id,
                status='pending'
            ).first()
            
            threads_data.append({
                "id": thread.id,
                "title": thread.title,
                "description": thread.description,
                "avatar": thread.avatar,
                "department": thread.department,
                "tags": thread.tags or [],
                "is_open": thread.is_open,
                "member_count": thread.member_count,
                "max_members": thread.max_members,
                "has_space": thread.member_count < thread.max_members,
                "requires_approval": thread.requires_approval,
                "is_member": thread.id in member_thread_ids,
                "is_creator": thread.creator_id == current_user.id,
                "has_pending_request": pending_request is not None,
                "last_activity": thread.last_activity.isoformat(),
                "created_at": thread.created_at.isoformat(),
                "total_users": len(members_data),
                "members_data": members_data,
                "creator": {
                    "id": creator.id,
                    "username": creator.username,
                    "name": creator.name,
                    "avatar": creator.avatar,
                    "reputation_level": creator.reputation_level
                } if creator else None,
                "post": {
                    "id": post.id,
                    "title": post.title
                } if post else None
            })
        
        return jsonify({
            "status": "success",
            "search_type": "threads",
            "data": {
                "threads": threads_data,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": paginated.total,
                    "pages": paginated.pages,
                    "has_next": paginated.has_next,
                    "has_prev": paginated.has_prev
                },
                "filters_applied": {
                    "query": query_str,
                    "department": department,
                    "is_open": is_open,
                    "has_space": has_space
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Thread search error: {str(e)}")
        return error_response("Thread search failed")

def _search_all_unified(current_user, query_str, args):
    """Internal function to search across all types"""
    try:
        limit = min(args.get("limit", 5, type=int), 10)
        search_pattern = f"%{query_str}%"
        
        # Search Users
        users = User.query.filter(
            or_(
                User.username.ilike(search_pattern),
                User.name.ilike(search_pattern)
            ),
            User.id != current_user.id,
            User.status == "approved"
        ).order_by(User.reputation.desc()).limit(limit).all()
        
        users_data = []
        for user in users:
            profile = StudentProfile.query.filter_by(user_id=user.id).first()
            users_data.append({
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "avatar": user.avatar,
                "department": profile.department if profile else None,
                "reputation_level": user.reputation_level
            })
        
        # Search Posts
        posts = Post.query.filter(
            or_(
                Post.title.ilike(search_pattern),
                Post.text_content.ilike(search_pattern)
            )
        ).order_by(Post.posted_at.desc()).limit(limit).all()
        
        posts_data = []
        for post in posts:
            author = User.query.get(post.student_id)
            posts_data.append({
                "id": post.id,
                "title": post.title,
                "post_type": post.post_type,
                "reactions_count": post.positive_reactions_count,
                "comments_count": post.comments_count,
                "posted_at": post.posted_at.isoformat(),
                "author": {
                    "username": author.username,
                    "name": author.name
                } if author else None
            })
        
        # Search Threads
        threads = Thread.query.filter(
            or_(
                Thread.title.ilike(search_pattern),
                Thread.description.ilike(search_pattern)
            )
        ).order_by(Thread.last_activity.desc()).limit(limit).all()
        
        threads_data = []
        for thread in threads:
            threads_data.append({
                "id": thread.id,
                "title": thread.title,
                "member_count": thread.member_count,
                "is_open": thread.is_open,
                "department": thread.department
            })
        
        total_results = len(users_data) + len(posts_data) + len(threads_data)
        
        return jsonify({
            "status": "success",
            "search_type": "all",
            "data": {
                "query": query_str,
                "all": {
                    "users": users_data,
                    "posts": posts_data,
                    "threads": threads_data
                },
                "counts": {
                    "users": len(users_data),
                    "posts": len(posts_data),
                    "threads": len(threads_data),
                    "total": total_results
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Global search error: {str(e)}")
        return error_response("Global search failed")
              

@search_bp.route("/search/users", methods=["GET"])
@token_required
def search_users(current_user):
    """
    Search users with advanced filters
    
    Query params:
    - q: Search query (username, name)
    - department: Filter by department
    - class_level: Filter by class level
    - skills: Comma-separated skills to match
    - reputation_min: Minimum reputation
    - sort: Sort by (relevance, reputation, name, recent)
    - page: Page number
    - per_page: Results per page (max 50)
    """
    try:
        # Get search query
        query_str = request.args.get("q", "").strip()
        
        # Base query - exclude self
        query = User.query.filter(
            User.id != current_user.id,
            User.status == "approved"
        )
        
        # Text search
        if query_str:
            search_pattern = f"%{query_str}%"
            query = query.filter(
                or_(
                    User.username.ilike(search_pattern),
                    User.name.ilike(search_pattern)
                )
            )
            
        
        # Department filter
        department = request.args.get("department", "").strip()
        if department:
            query = query.join(StudentProfile).filter(
                StudentProfile.department == department
            )
        
        # Class level filter
        class_level = request.args.get("class_level", "").strip()
        if class_level:
            if not department:  # Join if not already joined
                query = query.join(StudentProfile)
            query = query.filter(StudentProfile.class_name == class_level)
            
        connected = request.args.get("connected", "").strip()
        if connected:
            connected_list = Connection.query.filter(
            or_(
            and_(Connection.requester_id == current_user.id, Connection.status == "accepted"),
            and_(Connection.receiver_id == current_user.id, Connection.status == "accepted")
            )).all()
            if connected_list:
                connected_ids = [
    c.requester_id if c.receiver_id == current_user.id else c.receiver_id
    for c in connected_list
]          
                query = query.filter(User.id.in_(connected_ids))
                
        
        # Skills filter
        skills_param = request.args.get("skills", "").strip()
        if skills_param:
            skills_list = [s.strip().lower() for s in skills_param.split(",")]
            # Filter users who have at least one matching skill.
            # H-7 fix: '?|' is a PostgreSQL JSONB-only operator; not valid
            # for a plain db.JSON column and unsupported on SQLite.
            query = query.filter(or_(*[User.skills.contains([s]) for s in skills_list]))
        
        # Reputation filter
        rep_min = request.args.get("reputation_min", type=int)
        if rep_min:
            query = query.filter(User.reputation >= rep_min)
        
        # Sorting
        sort_by = request.args.get("sort", "relevance")
        if sort_by == "reputation":
            query = query.order_by(User.reputation.desc())
        elif sort_by == "name":
            query = query.order_by(User.name.asc())
        elif sort_by == "recent":
            query = query.order_by(User.joined_at.desc())
        else:  # relevance (default)
            if query_str:
                # Prioritize username matches over name matches
                query = query.order_by(
                    User.username.ilike(f"{query_str}%").desc(),
                    User.reputation.desc()
                )
            else:
                query = query.order_by(User.reputation.desc())
        
        # Pagination
        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 20, type=int), 50)
        
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Get connection status for each user
        user_ids = [u.id for u in paginated.items]
        connections = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id.in_(user_ids)),
                and_(Connection.requester_id.in_(user_ids), Connection.receiver_id == current_user.id)
            )
        ).all()
        
        # Map connection statuses
        connection_map = {}
        for conn in connections:
            other_id = conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
            if conn.status == "accepted":
                connection_map[other_id] = "connected"
            elif conn.status == "pending":
                if conn.requester_id == current_user.id:
                    connection_map[other_id] = "pending_sent"
                else:
                    connection_map[other_id] = "pending_received"
        
        # Format results
        users_data = []
        for user in paginated.items:
            privacy_settings = user.privacy_settings or {}
            profile_private = privacy_settings.get("set_profile_private", False)
            profile = StudentProfile.query.filter_by(user_id=user.id).first()
            users_data.append({
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "avatar": user.avatar,
                "bio": user.bio,
                "private": profile_private,
                "department": profile.department if profile else None,
                "class_level": profile.class_name if profile else None,
                "reputation": user.reputation if  not profile_private else None,
                "reputation_level": user.reputation_level if not profile_private else None,
                "skills": user.skills[:5] if user.skills else [],
                "connection_status": connection_map.get(user.id, "none")
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "users": users_data,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": paginated.total,
                    "pages": paginated.pages,
                    "has_next": paginated.has_next,
                    "has_prev": paginated.has_prev
                },
                "filters_applied": {
                    "query": query_str,
                    "department": department,
                    "class_level": class_level,
                    "skills": skills_param,
                    "sort": sort_by
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"User search error: {str(e)}")
        return error_response("Search failed")


@search_bp.route("/search/users/top-contributors", methods=["GET"])
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
@token_required
def search_posts(current_user):
    """
    Search posts with advanced filters
    
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
        
        # Base query
        query = Post.query
        
        # Text search
        if query_str:
            search_pattern = f"%{query_str}%"
            query = query.filter(
                or_(
                    Post.title.ilike(search_pattern),
                    Post.text_content.ilike(search_pattern)
                )
            )
        
        # Post type filter
        post_type = request.args.get("type", "").strip()
        if post_type:
            query = query.filter(Post.post_type == post_type)
        
        # Department filter
        department = request.args.get("department", "").strip()
        if department:
            query = query.filter(Post.department == department)
        
        # Tags filter
        tags_param = request.args.get("tags", "").strip()
        if tags_param:
            tags_list = [t.strip().lower() for t in tags_param.split(",")]
            # Filter posts that have at least one matching tag
            for tag in tags_list:
                query = query.filter(
                    func.lower(func.cast(Post.tags, db.String)).like(f'%"{tag}"%')
                )
        
        # Solved filter (for questions/problems)
        solved = request.args.get("solved")
        if solved is not None:
            is_solved = solved.lower() in ['true', '1', 'yes']
            query = query.filter(Post.is_solved == is_solved)
        
        # Date range filter
        date_from = request.args.get("date_from")
        if date_from:
            try:
                from_date = datetime.datetime.fromisoformat(date_from)
                query = query.filter(Post.posted_at >= from_date)
            except ValueError:
                pass
        
        date_to = request.args.get("date_to")
        if date_to:
            try:
                to_date = datetime.datetime.fromisoformat(date_to)
                query = query.filter(Post.posted_at <= to_date)
            except ValueError:
                pass
        
        # Sorting
        sort_by = request.args.get("sort", "recent")
        if sort_by == "popular":
            # Sort by engagement (positive reactions + comments)
            query = query.order_by(
                (Post.positive_reactions_count + Post.comments_count).desc()
            )
        elif sort_by == "trending":
            # Recent posts with high engagement
            week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
            query = query.filter(Post.posted_at >= week_ago).order_by(
                (Post.positive_reactions_count * 2 + Post.comments_count + Post.views_count / 10).desc()
            )
        elif sort_by == "recent":
            query = query.order_by(Post.posted_at.desc())
        else:  # relevance
            if query_str:
                # Prioritize title matches
                query = query.order_by(
                    Post.title.ilike(f"%{query_str}%").desc(),
                    Post.posted_at.desc()
                )
            else:
                query = query.order_by(Post.posted_at.desc())
        
        # Pagination
        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 20, type=int), 50)
        
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Format results
        posts_data = []
        for post in paginated.items:
            author = User.query.get(post.student_id)
            
            posts_data.append({
                "id": post.id,
                "title": post.title,
                "post_type": post.post_type,
                "department": post.department,
                "tags": post.tags,
                "excerpt": post.text_content[:200] if post.text_content else None,
                "reactions_count": post.positive_reactions_count,
                "comments_count": post.comments_count,
                "views": post.views_count,
                "is_solved": post.is_solved,
                "thread_enabled": post.thread_enabled,
                "posted_at": post.posted_at.isoformat(),
                "author": {
                    "id": author.id,
                    "username": author.username,
                    "name": author.name,
                    "avatar": author.avatar,
                    "reputation_level": author.reputation_level
                } if author else None
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "posts": posts_data,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": paginated.total,
                    "pages": paginated.pages
                },
                "filters_applied": {
                    "query": query_str,
                    "type": post_type,
                    "department": department,
                    "tags": tags_param,
                    "solved": solved,
                    "sort": sort_by
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Post search error: {str(e)}")
        return error_response("Search failed")


@search_bp.route("/search/posts/unanswered", methods=["GET"])
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
@token_required
def search_threads(current_user):
    """
    Search collaboration threads
    
    Query params:
    - q: Search query (title, description)
    - department: Filter by department
    - is_open: Boolean - only open threads
    - has_space: Boolean - threads accepting members
    - page: Page number
    """
    try:
        query_str = request.args.get("q", "").strip()
        
        query = Thread.query
        
        # Text search
        if query_str:
            search_pattern = f"%{query_str}%"
            query = query.filter(
                or_(
                    Thread.title.ilike(search_pattern),
                    Thread.description.ilike(search_pattern)
                )
            )
        
        # Department filter
        department = request.args.get("department", "").strip()
        if department:
            query = query.filter(Thread.department == department)
        
        # Open threads filter
        is_open = request.args.get("is_open")
        if is_open is not None:
            open_status = is_open.lower() in ['true', '1', 'yes']
            query = query.filter(Thread.is_open == open_status)
        
        # Has space filter (not full)
        has_space = request.args.get("has_space")
        if has_space and has_space.lower() in ['true', '1', 'yes']:
            query = query.filter(Thread.member_count < Thread.max_members)
        
        # Sort by recent activity
        query = query.order_by(Thread.last_activity.desc())
        
        # Pagination
        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 20, type=int), 50)
        
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Check user's membership status for each thread
        thread_ids = [t.id for t in paginated.items]
        memberships = ThreadMember.query.filter(
            ThreadMember.thread_id.in_(thread_ids),
            ThreadMember.student_id == current_user.id
        ).all()
        member_thread_ids = {m.thread_id for m in memberships}
        
        # Format results
        threads_data = []
        for thread in paginated.items:
            creator = User.query.get(thread.creator_id)
            post = Post.query.get(thread.post_id)
            
            threads_data.append({
                "id": thread.id,
                "title": thread.title,
                "description": thread.description,
                "department": thread.department,
                "tags": thread.tags,
                "is_open": thread.is_open,
                "member_count": thread.member_count,
                "max_members": thread.max_members,
                "has_space": thread.member_count < thread.max_members,
                "is_member": thread.id in member_thread_ids,
                "is_creator": thread.creator_id == current_user.id,
                "last_activity": thread.last_activity.isoformat(),
                "created_at": thread.created_at.isoformat(),
                "creator": {
                    "id": creator.id,
                    "username": creator.username,
                    "name": creator.name,
                    "avatar": creator.avatar
                } if creator else None,
                "post": {
                    "id": post.id,
                    "title": post.title
                } if post else None
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "threads": threads_data,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": paginated.total,
                    "pages": paginated.pages
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Thread search error: {str(e)}")
        return error_response("Search failed")


@search_bp.route("/search/threads/open", methods=["GET"])
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
@token_required
def global_search(current_user):
    """
    Search across users, posts, and threads simultaneously
    Returns top results from each category
    
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
        search_pattern = f"%{query_str}%"
        
        # Search Users
        users = User.query.filter(
            or_(
                User.username.ilike(search_pattern),
                User.name.ilike(search_pattern)
            ),
            User.id != current_user.id,
            User.status == "approved"
        ).order_by(User.reputation.desc()).limit(limit).all()
        
        users_data = []
        for user in users:
            profile = StudentProfile.query.filter_by(user_id=user.id).first()
            users_data.append({
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "avatar": user.avatar,
                "department": profile.department if profile else None,
                "reputation_level": user.reputation_level
            })
        
        # Search Posts
        posts = Post.query.filter(
            or_(
                Post.title.ilike(search_pattern),
                Post.text_content.ilike(search_pattern)
            )
        ).order_by(Post.posted_at.desc()).limit(limit).all()
        
        posts_data = []
        for post in posts:
            author = User.query.get(post.student_id)
            posts_data.append({
                "id": post.id,
                "title": post.title,
                "post_type": post.post_type,
                "reactions_count": post.positive_reactions_count,
                "comments_count": post.comments_count,
                "posted_at": post.posted_at.isoformat(),
                "author": {
                    "username": author.username,
                    "name": author.name
                } if author else None
            })
        
        # Search Threads
        threads = Thread.query.filter(
            or_(
                Thread.title.ilike(search_pattern),
                Thread.description.ilike(search_pattern)
            )
        ).order_by(Thread.last_activity.desc()).limit(limit).all()
        
        threads_data = []
        for thread in threads:
            threads_data.append({
                "id": thread.id,
                "title": thread.title,
                "member_count": thread.member_count,
                "is_open": thread.is_open,
                "department": thread.department
            })
        
        total_results = len(users_data) + len(posts_data) + len(threads_data)
        
        return jsonify({
            "status": "success",
            "data": {
                "query": query_str,
                "results": {
                    "users": users_data,
                    "posts": posts_data,
                    "threads": threads_data
                },
                "counts": {
                    "users": len(users_data),
                    "posts": len(posts_data),
                    "threads": len(threads_data),
                    "total": total_results
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Global search error: {str(e)}")
        return error_response("Search failed")


# ============================================================================
# SEARCH SUGGESTIONS & AUTOCOMPLETE
# ============================================================================

@search_bp.route("/search/suggestions", methods=["GET"])
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
                    
                   
       