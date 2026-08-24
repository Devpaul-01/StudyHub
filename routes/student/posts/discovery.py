"""
StudyHub - Posts: Discovery (tags, resources)

Split from posts.py per Document 1 (Architecture Refactor) §2.3 as part of
Phase 2 (God-file splitting). This is a pure move — function bodies,
decorators, routes, and logic are unchanged from the original posts.py.
See routes/student/posts/__init__.py for the sub-blueprint aggregation
that re-exposes all routes under the same paths as before.
"""

from flask import Blueprint, request, jsonify, current_app, Response, stream_with_context
import json
from werkzeug.utils import secure_filename
from sqlalchemy import or_, and_, func, desc
from sqlalchemy.orm import aliased
import datetime
import re
import os
import time
import traceback
from routes.student.reputation import check_and_award_milestone

import mimetypes
from datetime import date, timedelta
from collections import defaultdict
import logging

import random
logger = logging.getLogger(__name__)

from models import (
    User, StudentProfile, Post, Comment, Connection, PostReaction, PostReport,
    Bookmark, PostFollow, Mention, Notification, ReputationHistory, BookmarkFolder,
    ThreadMember, UserActivity, PostView, CommentHelpfulMark, CommentLike,
    ThreadJoinRequest, Thread
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response,
    save_file, ALLOWED_IMAGE_EXT, ALLOWED_DOCUMENT_EXT
)

from services.post_service import (
    extract_public_id,
    update_post_reaction_count,
    detect_and_create_mentions,
    check_spam,
    update_user_activity,
)
# Phase 5b (Document 4 §1): PUBLIC_READ across the board — this file is
# entirely discovery/read endpoints (resources, tag search, tag popularity).
from services.rate_limit_service import limiter, RateLimitTier, ip_key

import cloudinary

try:
    from services.storage import cloudinary_storage, filename_service
    STORAGE_AVAILABLE = True
    logger.info("Storage module available")
except ImportError as e:
    STORAGE_AVAILABLE = False
    logger.warning(f"Storage module not available: {str(e)}")
except Exception as e:
    STORAGE_AVAILABLE = False
    logger.warning(f"Storage initialization failed: {str(e)}")

import base64

posts_discovery_bp = Blueprint("posts_discovery", __name__)
@posts_discovery_bp.route("/comments/<int:comment_id>/resources", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def comment_resources(current_user, comment_id):
    try:
        comment = Comment.query.get(comment_id)
        if not comment:
            return error_response("Comment not found")
        resources = comment.resources
        return jsonify({"status": "success", "data":{"id": comment.id, "resources": resources}})
    except Exception as e:
        current_app.logger.error(f"Comment resources error: ", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to load comment resources"}), 500

@posts_discovery_bp.route("/posts/<int:post_id>/resources", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def post_resources(current_user, post_id):
    try:
        post = Post.query.get(post_id)
        if not post:
            return error_response("Post not found")
        resources = post.resources
        return jsonify({"status": "success", "data":{"id": post.id, "resources": resources}})
    except Exception as e:
        current_app.logger.error(f"Post resources error: ", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to load post resources"}), 500
        
# C-5 fix: removed ~300 lines of dead code here (the AI-assisted
# refine_post/draft_post endpoints — their @posts_discovery_bp.route decorators
# had already been manually commented out, but the full function bodies
# were still shipping in this file). If AI-assisted post refinement is
# still wanted, re-implement it as a tracked feature using the same
# provider_manager/StudyAssistant pattern as ask_learnora_about_post()
# above, rather than restoring this block.

@posts_discovery_bp.route("/posts/tags/<tag>", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_posts_by_tag(current_user, tag):
    """
    Get posts filtered by tag with PAGINATION
    """
    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        
        # ✅ FIXED: Cast the COLUMN (not just the param) to JSONB, then use the @> operator
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy import cast
        
        query = Post.query.filter(cast(Post.tags, JSONB).contains([tag]))
        query = query.order_by(Post.posted_at.desc())
        
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)

        # ════════════════════════════════════════════════════════════════════
        # BATCH LOAD EVERYTHING (Document 4 §4 — same batching approach as
        # posts/crud.py::get_feed, applied here to eliminate the per-post
        # comments/author/connection/reaction/bookmark/follow/thread N+1s)
        # ════════════════════════════════════════════════════════════════════
        page_posts = paginated.items
        page_post_ids = [p.id for p in page_posts]
        page_author_ids = list({p.student_id for p in page_posts})

        # Authors
        authors_map = {u.id: u for u in User.query.filter(User.id.in_(page_author_ids)).all()} if page_author_ids else {}

        # Connections (only need the "other side" of each pair once)
        other_author_ids = [aid for aid in page_author_ids if aid != current_user.id]
        connections_map = {}
        if other_author_ids:
            conns = Connection.query.filter(
                or_(
                    and_(Connection.requester_id == current_user.id, Connection.receiver_id.in_(other_author_ids)),
                    and_(Connection.receiver_id == current_user.id, Connection.requester_id.in_(other_author_ids))
                )
            ).all()
            for c in conns:
                other = c.receiver_id if c.requester_id == current_user.id else c.requester_id
                connections_map[other] = c.status

        # Current-user reactions / bookmarks / follows
        reactions_map = {
            r.post_id: r
            for r in PostReaction.query.filter(
                PostReaction.post_id.in_(page_post_ids),
                PostReaction.student_id == current_user.id
            ).all()
        } if page_post_ids else {}

        bookmarks_map = {
            b.post_id: b
            for b in Bookmark.query.filter(
                Bookmark.post_id.in_(page_post_ids),
                Bookmark.student_id == current_user.id
            ).all()
        } if page_post_ids else {}

        follows_map = {
            f.post_id: f
            for f in PostFollow.query.filter(
                PostFollow.post_id.in_(page_post_ids),
                PostFollow.student_id == current_user.id
            ).all()
        } if page_post_ids else {}

        # Threads (only for thread-enabled posts on this page)
        thread_enabled_ids = [p.id for p in page_posts if p.thread_enabled]
        threads_map = {}
        thread_join_map = {}
        thread_member_set = set()
        if thread_enabled_ids:
            threads = Thread.query.filter(Thread.post_id.in_(thread_enabled_ids)).all()
            threads_map = {t.post_id: t for t in threads}

            thread_ids = [t.id for t in threads]
            if thread_ids:
                join_reqs = ThreadJoinRequest.query.filter(
                    ThreadJoinRequest.thread_id.in_(thread_ids),
                    ThreadJoinRequest.requester_id == current_user.id
                ).all()
                thread_join_map = {jr.thread_id: jr for jr in join_reqs}

                members = ThreadMember.query.filter(
                    ThreadMember.thread_id.in_(thread_ids),
                    ThreadMember.student_id == current_user.id
                ).all()
                thread_member_set = {m.thread_id for m in members}

        # Top-2 comments per post (same windowed-query technique get_feed uses)
        comments_by_post = defaultdict(list)
        if page_post_ids:
            rank_col = func.row_number().over(
                partition_by=Comment.post_id,
                order_by=[Comment.is_solution.desc(), Comment.likes_count.desc()]
            ).label("rn")

            ranked_subq = (
                db.session.query(Comment, rank_col)
                .filter(
                    Comment.post_id.in_(page_post_ids),
                    Comment.parent_id == None,
                    Comment.is_deleted == False
                )
                .subquery()
            )

            CommentAlias = aliased(Comment, ranked_subq)
            top_comments_all = (
                db.session.query(CommentAlias)
                .filter(ranked_subq.c.rn <= 2)
                .all()
            )
            for c in top_comments_all:
                comments_by_post[c.post_id].append(c)
        else:
            top_comments_all = []

        # Batch-load comment authors + current-user comment likes
        comment_author_ids = list({c.student_id for c in top_comments_all})
        comment_authors_map = {
            u.id: u for u in User.query.filter(User.id.in_(comment_author_ids)).all()
        } if comment_author_ids else {}

        all_comment_ids = [c.id for c in top_comments_all]
        comment_liked_set = set()
        if all_comment_ids:
            liked_rows = CommentLike.query.filter(
                CommentLike.student_id == current_user.id,
                CommentLike.comment_id.in_(all_comment_ids)
            ).all()
            comment_liked_set = {lk.comment_id for lk in liked_rows}

        # Build response (same structure as feed)
        posts_data = []
        for post in page_posts:
            comments_data = []
            for comment in comments_by_post.get(post.id, []):
                c_author = comment_authors_map.get(comment.student_id)
                if not c_author:
                    continue
                comments_data.append({
                    'id': comment.id,
                    "likes_count": comment.likes_count,
                    "user_id": c_author.id,
                    "username": c_author.username,
                    "name": c_author.name,
                    "avatar": c_author.avatar,
                    "posted_at": comment.posted_at.isoformat(),
                    "is_solution": comment.is_solution,
                    "helpful_count": comment.helpful_count,
                    "resources": comment.resources,
                    "has_liked": comment.id in comment_liked_set,
                    "text_content": comment.text_content
                })

            # Get post author
            author = authors_map.get(post.student_id)

            # Initialize default values
            connection_status = None
            is_solved = None
            is_pinned = None
            requested_thread = False
            is_member = False

            # Check connection status
            if author and author.id != current_user.id:
                connection_status = connections_map.get(author.id)

            # Check user reactions
            user_reacted = reactions_map.get(post.id)
            user_bookmarked = bookmarks_map.get(post.id)
            user_followed = follows_map.get(post.id)

            # Check thread request
            if post.thread_enabled:
                thread = threads_map.get(post.id)
                if thread:
                    requested_thread = thread_join_map.get(thread.id)
                    is_member = thread.id in thread_member_set

            # Check if solvable type
            if post.post_type in ["problem", "question"]:
                is_solved = post.is_solved
            
            # Check if pinned (only for author)
            if post.student_id == current_user.id:
                is_pinned = post.is_pinned

            posts_data.append({
                "id": post.id,
                "title": post.title,
                "excerpt": post.text_content,
                "post_type": post.post_type,
                "department": post.department,
                "tags": post.tags,
                "resources": post.resources,
                "thread_enabled": post.thread_enabled,
                "bookmarks_count": post.bookmark_count,
                "is_solved": is_solved,
                "is_pinned": is_pinned,
                "reactions_count": post.positive_reactions_count or 0,
                "comments_count": post.comments_count,
                "posted_at": post.posted_at.isoformat(),
                "is_author": post.student_id == current_user.id,
                "connection_status": connection_status,
                "author": {
                    "id": author.id,
                    "username": author.username,
                    "name": author.name,
                    "avatar": author.avatar,
                    "reputation_level": author.reputation_level
                } if author else None,
                "comments": comments_data,
                "user_interactions": {
                    "requested_thread": requested_thread.status if requested_thread else None,
                    "is_thread_member": is_member,
                    "user_followed": bool(user_followed),
                    "user_reacted": bool(user_reacted),
                    "reaction_type": user_reacted.reaction_type if user_reacted else None,
                    "bookmarked": bool(user_bookmarked)
                }
            })

        return jsonify({
            "status": "success",
            "data": {
                "posts": posts_data,
                "tag": tag,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": paginated.total,
                    "pages": paginated.pages,
                    "has_next": paginated.has_next,
                    "has_prev": paginated.has_prev
                }
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get posts by tag error: ", exc_info=True)
        return error_response("Failed to load posts by tag")



@posts_discovery_bp.route("/posts/tags", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def popular_tags(current_user):
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")

        # Load only the tags column for the current user's posts (not full Post objects)
        user_tags_rows = (
            Post.query
            .filter_by(student_id=user.id)
            .with_entities(Post.tags)
            .all()
        )
        user_tags = set()
        for (tags,) in user_tags_rows:
            if tags:
                for tag in tags:
                    user_tags.add(tag.lower().strip())

        # Load only the tags column for ALL posts — dramatically more memory-efficient
        # than Post.query.all() which loads every column for every post
        all_tags_rows = (
            Post.query
            .with_entities(Post.tags)
            .filter(Post.tags.isnot(None))
            .all()
        )

        tags_details = {}
        for (tags,) in all_tags_rows:
            if tags:
                for tag in tags:
                    tag_clean = tag.lower().strip()
                    if tag_clean:
                        tags_details[tag_clean] = tags_details.get(tag_clean, 0) + 1

        # Sort by count, prioritizing the current user's own tags first
        sorted_tags = sorted(
            tags_details.items(),
            key=lambda x: (x[0] not in user_tags, -x[1])
        )

        return jsonify({"status": "success", "data": dict(sorted_tags[:50])})
    except Exception as e:
        current_app.logger.error("Get tags error", exc_info=True)
        return error_response("Failed to load trending tags")
            

