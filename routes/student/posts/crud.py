"""
StudyHub - Posts: Post CRUD, feed, reactions, and single-post detail endpoints

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

posts_crud_bp = Blueprint("posts_crud", __name__)
def encode_cursor(dt: datetime.datetime) -> str:
    """Encode a datetime into a URL-safe cursor string."""
    return base64.urlsafe_b64encode(dt.isoformat().encode()).decode()


def decode_cursor(cursor: str):
    """Decode cursor back to datetime. Returns None on any failure."""
    try:
        return datetime.datetime.fromisoformat(
            base64.urlsafe_b64decode(cursor).decode()
        )
    except Exception:
        return None

@posts_crud_bp.route("/posts/feed", methods=["GET"])
@token_required
def get_feed(current_user):
    start_time = time.time()
    request_id = request.headers.get("X-Request-Id") or f"feed_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000, 9999)}"
    
    current_app.logger.info(f"[FEED] {request_id} ⚡ START", extra={
        "user_id": current_user.id,
        "user_email": current_user.email,
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
        "user_agent": request.headers.get("User-Agent"),
        "request_id": request_id,
    })

    try:
        # ── Step 1: Parse request parameters ──────────────────────────────────
        filter_type = request.args.get("filter", "all")
        cursor_str  = request.args.get("cursor")
        limit       = min(request.args.get("limit", 10, type=int), 20)
        post_type   = request.args.get("post_type", "").strip()

        current_app.logger.info(f"[FEED] {request_id} Parameters parsed", extra={
            "filter_type": filter_type,
            "cursor_str": cursor_str,
            "limit": limit,
            "post_type": post_type or "None",
            "request_id": request_id,
        })

        # ── Step 2: Decode cursor ──────────────────────────────────────────────
        cursor_date = decode_cursor(cursor_str) if cursor_str else None
        current_app.logger.debug(f"[FEED] {request_id} Cursor decoded", extra={
            "cursor_date": cursor_date.isoformat() if cursor_date else None,
            "request_id": request_id,
        })

        # ── Step 3: Get user's department ──────────────────────────────────────
        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
        user_dept = profile.department if profile else None
        current_app.logger.debug(f"[FEED] {request_id} User profile", extra={
            "user_dept": user_dept,
            "has_profile": bool(profile),
            "request_id": request_id,
        })

        # ── Step 4: Build base query ───────────────────────────────────────────
        if filter_type == "connections":
            current_app.logger.info(f"[FEED] {request_id} Filter: connections", extra={"request_id": request_id})
            
            conns = Connection.query.filter(
                or_(
                    Connection.requester_id == current_user.id,
                    Connection.receiver_id == current_user.id
                ),
                Connection.status == "accepted"
            ).all()

            current_app.logger.debug(f"[FEED] {request_id} Connections found", extra={
                "conn_count": len(conns),
                "request_id": request_id,
            })

            conn_ids = [
                c.receiver_id if c.requester_id == current_user.id else c.requester_id
                for c in conns
            ]

            if not conn_ids:
                current_app.logger.info(f"[FEED] {request_id} No connections, returning empty", extra={"request_id": request_id})
                return jsonify({
                    "status": "success",
                    "data": {
                        "posts": [],
                        "filter": filter_type,
                        "next_cursor": None,
                        "has_more": False,
                        "debug": {"request_id": request_id}
                    }
                })
            query = Post.query.filter(Post.student_id.in_(conn_ids))

        elif filter_type == "department":
            current_app.logger.info(f"[FEED] {request_id} Filter: department", extra={"request_id": request_id})
            query = Post.query.filter(Post.department == user_dept)

        elif filter_type == "trending":
            current_app.logger.info(f"[FEED] {request_id} Filter: trending", extra={"request_id": request_id})
            week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
            query = Post.query.filter(Post.posted_at >= week_ago)

        elif filter_type == "unsolved":
            current_app.logger.info(f"[FEED] {request_id} Filter: unsolved", extra={"request_id": request_id})
            query = Post.query.filter(
                Post.post_type.in_(["question", "problem"]),
                Post.is_solved == False
            )
        else:
            current_app.logger.info(f"[FEED] {request_id} Filter: all", extra={"request_id": request_id})
            query = Post.query

        # ── Step 5: Apply post_type filter ─────────────────────────────────────
        valid_types = ["question", "discussion", "announcement", "resource", "problem"]
        if post_type and post_type in valid_types:
            current_app.logger.debug(f"[FEED] {request_id} Filtering by post_type: {post_type}", extra={"request_id": request_id})
            query = query.filter(Post.post_type == post_type)
        elif post_type:
            current_app.logger.warning(f"[FEED] {request_id} Invalid post_type: {post_type}", extra={"request_id": request_id})

        # ── Step 6: Apply ordering ─────────────────────────────────────────────
        if filter_type == "trending":
            query = query.order_by(
                desc(Post.positive_reactions_count * 2 + Post.comments_count * 1.5 + Post.views_count / 10),
                Post.posted_at.desc()
            )
        else:
            query = query.order_by(Post.posted_at.desc())

        if cursor_date:
            query = query.filter(Post.posted_at < cursor_date)
            current_app.logger.debug(f"[FEED] {request_id} Applied cursor filter", extra={
                "cursor_date": cursor_date.isoformat(),
                "request_id": request_id,
            })

        # ── Step 7: Execute query ──────────────────────────────────────────────
        query_start = time.time()
        posts_raw = query.limit(limit + 1).all()
        query_elapsed = (time.time() - query_start) * 1000

        current_app.logger.info(f"[FEED] {request_id} Query executed", extra={
            "posts_found": len(posts_raw),
            "limit": limit,
            "query_elapsed_ms": round(query_elapsed, 2),
            "request_id": request_id,
        })

        has_more = len(posts_raw) > limit
        posts_page = posts_raw[:limit]
        next_cursor = encode_cursor(posts_page[-1].posted_at) if has_more and posts_page else None

        if not posts_page:
            current_app.logger.info(f"[FEED] {request_id} No posts found, returning empty", extra={"request_id": request_id})
            return jsonify({
                "status": "success",
                "data": {
                    "posts": [],
                    "filter": filter_type,
                    "next_cursor": None,
                    "has_more": False,
                    "debug": {"request_id": request_id}
                }
            })

        # ════════════════════════════════════════════════════════════════════
        # BATCH LOAD EVERYTHING
        # ════════════════════════════════════════════════════════════════════
        batch_start = time.time()
        post_ids = [p.id for p in posts_page]
        author_ids = list({p.student_id for p in posts_page})

        current_app.logger.debug(f"[FEED] {request_id} Batch loading", extra={
            "post_count": len(post_ids),
            "author_count": len(author_ids),
            "request_id": request_id,
        })

        # 1. Authors
        authors_map = {u.id: u for u in User.query.filter(User.id.in_(author_ids)).all()}

        # 2. Current-user reactions
        reactions_map = {
            r.post_id: r
            for r in PostReaction.query.filter(
                PostReaction.post_id.in_(post_ids),
                PostReaction.student_id == current_user.id
            ).all()
        }

        # 3. Current-user follows
        follows_map = {
            f.post_id: f
            for f in PostFollow.query.filter(
                PostFollow.post_id.in_(post_ids),
                PostFollow.student_id == current_user.id
            ).all()
        }

        # 4. Connections
        other_author_ids = [aid for aid in author_ids if aid != current_user.id]
        connections_map = {}
        if other_author_ids:
            conns = Connection.query.filter(
                or_(
                    and_(Connection.requester_id == current_user.id,
                         Connection.receiver_id.in_(other_author_ids)),
                    and_(Connection.receiver_id == current_user.id,
                         Connection.requester_id.in_(other_author_ids))
                )
            ).all()
            for c in conns:
                other = c.receiver_id if c.requester_id == current_user.id else c.requester_id
                connections_map[other] = c.status

        # 5. Threads
        thread_enabled_ids = [p.id for p in posts_page if p.thread_enabled]
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
                thread_join_map = {jr.thread_id: jr.status for jr in join_reqs}

                members = ThreadMember.query.filter(
                    ThreadMember.thread_id.in_(thread_ids),
                    ThreadMember.student_id == current_user.id
                ).all()
                thread_member_set = {m.thread_id for m in members}

        # 6. Top-2 comments per post
        rank_col = func.row_number().over(
            partition_by=Comment.post_id,
            order_by=[Comment.is_solution.desc(), Comment.likes_count.desc()]
        ).label("rn")

        ranked_subq = (
            db.session.query(Comment, rank_col)
            .filter(
                Comment.post_id.in_(post_ids),
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

        comments_by_post = defaultdict(list)
        for c in top_comments_all:
            comments_by_post[c.post_id].append(c)

        # Batch-load comment authors
        comment_author_ids = list({c.student_id for c in top_comments_all})
        comment_authors_map = {
            u.id: u for u in User.query.filter(User.id.in_(comment_author_ids)).all()
        } if comment_author_ids else {}

        # Batch-load comment likes
        all_comment_ids = [c.id for c in top_comments_all]
        comment_liked_set = set()
        if all_comment_ids:
            liked_rows = CommentLike.query.filter(
                CommentLike.student_id == current_user.id,
                CommentLike.comment_id.in_(all_comment_ids)
            ).all()
            comment_liked_set = {lk.comment_id for lk in liked_rows}

        batch_elapsed = (time.time() - batch_start) * 1000
        current_app.logger.debug(f"[FEED] {request_id} Batch load complete", extra={
            "batch_elapsed_ms": round(batch_elapsed, 2),
            "top_comments": len(top_comments_all),
            "request_id": request_id,
        })

        # ════════════════════════════════════════════════════════════════════
        # ASSEMBLE PAYLOADS
        # ════════════════════════════════════════════════════════════════════
        posts_data = []
        for post in posts_page:
            author = authors_map.get(post.student_id)
            if not author:
                current_app.logger.warning(f"[FEED] {request_id} Author not found for post", extra={
                    "post_id": post.id,
                    "student_id": post.student_id,
                    "request_id": request_id,
                })
                continue

            user_reacted = reactions_map.get(post.id)
            user_followed = follows_map.get(post.id)
            connection_status = connections_map.get(author.id) if author.id != current_user.id else None

            thread_id = None
            requested_thread = None
            is_member = False
            if post.thread_enabled:
                thread = threads_map.get(post.id)
                if thread:
                    thread_id = thread.id
                    requested_thread = thread_join_map.get(thread.id)
                    is_member = thread.id in thread_member_set

            comments_preview = []
            for c in comments_by_post.get(post.id, []):
                c_author = comment_authors_map.get(c.student_id)
                comments_preview.append({
                    "id": c.id,
                    "text_content": c.text_content,
                    "likes_count": c.likes_count,
                    "helpful_count": c.helpful_count,
                    "is_solution": c.is_solution,
                    "resources": c.resources or [],
                    "has_liked": c.id in comment_liked_set,
                    "posted_at": c.posted_at.isoformat(),
                    "author": {
                        "id": c_author.id,
                        "username": c_author.username,
                        "name": c_author.name,
                        "avatar": c_author.avatar
                    } if c_author else None
                })

            posts_data.append({
                "id": post.id,
                "title": post.title,
                "excerpt": post.text_content,
                "post_type": post.post_type,
                "department": post.department,
                "tags": post.tags or [],
                "resources": post.resources or [],
                "thread_enabled": post.thread_enabled,
                "thread_id": thread_id,
                "is_solved": post.is_solved if post.post_type in ["question", "problem"] else None,
                "is_pinned": post.is_pinned if post.student_id == current_user.id else None,
                "reactions_count": post.positive_reactions_count or 0,
                "comments_count": post.comments_count or 0,
                "views_count": post.views_count or 0,
                "posted_at": post.posted_at.isoformat(),
                "is_author": post.student_id == current_user.id,
                "connection_status": connection_status,
                "author": {
                    "id": author.id,
                    "username": author.username,
                    "name": author.name,
                    "avatar": author.avatar,
                    "reputation": author.reputation,
                    "reputation_level": author.reputation_level
                },
                "comments_preview": comments_preview,
                "user_interactions": {
                    "user_reacted": bool(user_reacted),
                    "reaction_type": user_reacted.reaction_type if user_reacted else None,
                    "user_followed": bool(user_followed),
                    "requested_thread": requested_thread,
                    "is_thread_member": is_member
                }
            })

        total_elapsed = (time.time() - start_time) * 1000
        current_app.logger.info(f"[FEED] {request_id} ✅ SUCCESS", extra={
            "posts_returned": len(posts_data),
            "has_more": has_more,
            "total_elapsed_ms": round(total_elapsed, 2),
            "filter_type": filter_type,
            "request_id": request_id,
        })

        return jsonify({
            "status": "success",
            "data": {
                "posts": posts_data,
                "filter": filter_type,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "debug": {
                    "request_id": request_id,
                    "response_time_ms": round(total_elapsed, 2)
                }
            }
        })

    except Exception as e:
        total_elapsed = (time.time() - start_time) * 1000
        current_app.logger.error(f"[FEED] {request_id} ❌ ERROR", extra={
            "error": str(e),
            "error_type": type(e).__name__,
            "traceback": traceback.format_exc(),
            "total_elapsed_ms": round(total_elapsed, 2),
            "request_id": request_id,
            "args": request.args.to_dict(),
        })
        return error_response("Failed to load feed")


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 2: Like-only toggle  (replaces the multi-reaction endpoint)
# ─────────────────────────────────────────────────────────────────────────────

@posts_crud_bp.route("/posts/<int:post_id>/react", methods=["POST"])
@token_required
def react_to_post(current_user, post_id):
    """
    Simple like toggle. Body: {} (no reaction field needed).
    Returns:
      { status, data: { liked: bool, count: int } }
    """
    try:
        post = Post.query.get(post_id)
        if not post:
            return error_response("Post not found", 404)

        existing = PostReaction.query.filter_by(
            post_id=post_id,
            student_id=current_user.id
        ).first()

        if existing:
            # ── UNLIKE ────────────────────────────────────────────────────
            db.session.delete(existing)
            post.positive_reactions_count = max(0, post.positive_reactions_count - 1)
            db.session.commit()
            return jsonify({
                "status": "success",
                "message": "Post unliked",
                "data": {
                    "liked": False,
                    "count": post.positive_reactions_count
                }
            })
        else:
            # ── LIKE ──────────────────────────────────────────────────────
            new_reaction = PostReaction(
                post_id=post_id,
                student_id=current_user.id,
                reaction_type="like"
            )
            db.session.add(new_reaction)
            post.positive_reactions_count += 1
            db.session.commit()

            # Reputation award (not on self-like)
            if post.student_id != current_user.id:
                try:
                    from routes.student.reputation import check_and_award_milestone
                    check_and_award_milestone(post.student_id, post_id=post_id)
                    # Document 2 §5 fix: award_reputation() (called internally
                    # by check_and_award_milestone) no longer commits on its
                    # own — added explicitly here so a milestone-triggered
                    # reputation award is actually persisted, not just
                    # computed in memory and discarded.
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            return jsonify({
                "status": "success",
                "message": "Post liked",
                "data": {
                    "liked": True,
                    "count": post.positive_reactions_count
                }
            }), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Like toggle error: ", exc_info=True)
        return error_response("Failed to toggle like")

@posts_crud_bp.route("/posts/resource/upload", methods=["POST"])
@token_required
def upload_post_resource(current_user):
    request_id = f"upload_{current_user.id}_{int(time.time())}"
    current_app.logger.info(f"[{request_id}] Upload request initiated | user_id={current_user.id}")

    try:
        # ── User validation ──────────────────────────────────────────────
        user = User.query.get(current_user.id)
        if not user:
            current_app.logger.warning(f"[{request_id}] User not found in DB | user_id={current_user.id}")
            return error_response("User not found")

        current_app.logger.debug(f"[{request_id}] User validated | username={user.username}")

        # ── Storage availability check ───────────────────────────────────
        if not STORAGE_AVAILABLE:
            current_app.logger.error(f"[{request_id}] Storage unavailable | STORAGE_AVAILABLE=False")
            return error_response("File uploads are temporarily unavailable")

        # ── File presence check ──────────────────────────────────────────
        if 'file' not in request.files:
            current_app.logger.warning(f"[{request_id}] No file key in request | form_keys={list(request.files.keys())}")
            return error_response("No file provided")

        file = request.files['file']

        if not file or not file.filename:
            current_app.logger.warning(f"[{request_id}] File object invalid or missing filename | file={file}")
            return error_response("Invalid file")

        raw_filename = file.filename
        filename = secure_filename(raw_filename)
        current_app.logger.info(f"[{request_id}] File received | raw_filename={raw_filename!r} | secured_filename={filename!r}")

        # ── File categorisation ──────────────────────────────────────────
        file_type = filename_service.get_file_category(filename)
        current_app.logger.debug(f"[{request_id}] File categorised | file_type={file_type}")

        # ── Path generation ──────────────────────────────────────────────
        folder, generated_filename = filename_service.get_post_file_path(
            current_user.id,
            filename,
            file_type
        )
        current_app.logger.debug(
            f"[{request_id}] Storage path resolved | folder={folder!r} | generated_filename={generated_filename!r}"
        )

        # ── Resource type mapping ────────────────────────────────────────
        resource_type_map = {"image": "image", "video": "video", "document": "raw"}
        resource_type = resource_type_map.get(file_type, "auto")
        current_app.logger.debug(
            f"[{request_id}] Cloudinary resource type mapped | file_type={file_type} → resource_type={resource_type}"
        )

        # ── Cloudinary upload ────────────────────────────────────────────
        current_app.logger.info(
            f"[{request_id}] Starting Cloudinary upload | folder={folder!r} | resource_type={resource_type}"
        )
        result = cloudinary_storage.upload_file(
            file,
            folder,
            generated_filename,
            resource_type=resource_type
        )

        if not result["success"]:
            current_app.logger.error(
                f"[{request_id}] Cloudinary upload failed | error={result['error']!r} | "
                f"folder={folder!r} | resource_type={resource_type}"
            )
            return error_response(f"Upload failed: {result['error']}")

        url = result["url"]
        current_app.logger.info(
            f"[{request_id}] Upload successful | url={url!r} | file_type={file_type} | filename={filename!r}"
        )

        # ── Build response ───────────────────────────────────────────────
        resource = {"url": url, "type": file_type, "filename": filename}
        current_app.logger.debug(f"[{request_id}] Response payload built | resource={resource}")

        return jsonify({"status": "success", "data": resource})

    except Exception as e:
        current_app.logger.error(
            f"[{request_id}] Unhandled exception during upload | user_id={current_user.id} | error={e}",
            exc_info=True
        )
        return error_response("Failed to upload file")

# ============================================================================
# HELPER FUNCTIONS
#
# Document 1 §2.3 / Document 2 §3.10: extract_public_id,
# update_post_reaction_count, detect_and_create_mentions, check_spam, and
# update_user_activity moved to services/post_service.py (imported at the
# top of this file). check_helpful_milestones has now ALSO moved to
# services/post_service.py, since services/badge_service.py exists now —
# the layering blocker that used to keep it here is resolved. Import it
# from services.post_service at call sites instead.
# ============================================================================


# ============================================================================
# POST CRUD OPERATIONS
# ============================================================================
# Add these endpoints to your posts.py file

@posts_crud_bp.route("/posts/by-type", methods=["GET"])
@token_required
def get_posts_by_type(current_user):
    """
    Get posts filtered by post_type
    
    Query params:
    - post_type: question, discussion, announcement, resource, problem (required)
    - page: Page number (default: 1)
    - per_page: Posts per page (default: 20)
    - department: Optional filter by department
    - tags: Optional comma-separated tags
    """
    try:
        # Get query parameters
        post_type = request.args.get("post_type", "").strip().lower()
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        department = request.args.get("department", "").strip()
        tags = request.args.get("tags", "").strip()
        
        # Validate post_type
        valid_types = ["question", "discussion", "announcement", "resource", "problem"]
        if not post_type:
            return error_response("post_type is required", 400)
        
        if post_type not in valid_types:
            return error_response(
                f"Invalid post_type. Must be one of: {', '.join(valid_types)}", 
                400
            )
        
        # Build query
        query = Post.query.filter_by(post_type=post_type)
        
        # Apply optional filters
        if department:
            query = query.filter_by(department=department)
        
        if tags:
            tag_list = [t.strip() for t in tags.split(",")]
            # Filter posts that have ANY of the specified tags.
            # H-7 fix: '&&' is a PostgreSQL-array-only operator that doesn't
            # exist for a plain db.JSON column (and would raise on SQLite).
            # .contains() on each tag — ORed together — is dialect-portable
            # and matches the pattern already used by get_posts_by_tag()
            # elsewhere in this file.
            query = query.filter(
                or_(*[Post.tags.contains([t]) for t in tag_list])
            )
        
        # Order by most recent
        query = query.order_by(Post.posted_at.desc())
        
        # Paginate
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Build response
        posts_data = []
        for post in paginated.items:
            author = User.query.get(post.student_id)
            
            # Check user interactions
            user_reacted = PostReaction.query.filter_by(
                post_id=post.id, 
                student_id=current_user.id
            ).first()
            
            user_bookmarked = Bookmark.query.filter_by(
                post_id=post.id, 
                student_id=current_user.id
            ).first()
            
            # Check connection status
            connection_status = None
            if author and author.id != current_user.id:
                connection = Connection.query.filter(
                    or_(
                        and_(Connection.requester_id == current_user.id, Connection.receiver_id == author.id),
                        and_(Connection.requester_id == author.id, Connection.receiver_id == current_user.id)
                    )
                ).first()
                
                if connection:
                    connection_status = connection.status
            
            posts_data.append({
                "id": post.id,
                "title": post.title,
                "excerpt": post.text_content[:200] if post.text_content else None,
                "post_type": post.post_type,
                "department": post.department,
                "tags": post.tags,
                "resources": post.resources,
                "thread_enabled": post.thread_enabled,
                "is_solved": post.is_solved if post.post_type in ["question", "problem"] else None,
                "is_pinned": post.is_pinned,
                "reactions_count": post.positive_reactions_count or 0,
                "comments_count": post.comments_count,
                "views_count": post.views_count,
                "bookmarks_count": post.bookmark_count,
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
                "user_interactions": {
                    "user_reacted": bool(user_reacted),
                    "reaction_type": user_reacted.reaction_type if user_reacted else None,
                    "bookmarked": bool(user_bookmarked)
                }
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "posts": posts_data,
                "post_type": post_type,
                "filters": {
                    "department": department or None,
                    "tags": tag_list if tags else None
                },
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
        current_app.logger.error(f"Get posts by type error: ", exc_info=True)
        return error_response("Failed to load posts")

@posts_crud_bp.route("/posts/<int:post_id>/options-menu", methods=["GET"])
@token_required
def get_post_options_menu(current_user, post_id):
    """
    Get fresh data for post options menu
    Returns current state of all interactions for accurate UI rendering
    """
    try:
        post = Post.query.get(post_id)
        
        if not post:
            return error_response("Post not found", 404)
        
        author = User.query.get(post.student_id)
        
        # Check user's current interactions
        user_followed = PostFollow.query.filter_by(
            post_id=post_id,
            student_id=current_user.id
        ).first() is not None
        
       
        # Check connection status with author
        connection_status = None
        has_connection  = False
        
        if author and author.id != current_user.id:
            connection = Connection.query.filter(
                or_(
                    and_(Connection.requester_id == current_user.id, Connection.receiver_id == author.id),
                    and_(Connection.requester_id == author.id, Connection.receiver_id == current_user.id)
                )
            ).first()
            
            if connection:
              has_connection = True
                
        
        # Check thread status
        thread_data = None
        if post.thread_enabled:
            thread = Thread.query.filter_by(post_id=post_id).first()
            if thread:
                is_member = ThreadMember.query.filter_by(
                    thread_id=thread.id,
                    student_id=current_user.id
                ).first() is not None
                
                join_request = ThreadJoinRequest.query.filter_by(
                    thread_id=thread.id,
                    requester_id=current_user.id
                ).first()
                
                thread_data = {
                    "thread_id": thread.id,
                    "is_member": is_member,
                    "request_status": join_request.status if join_request else None,
                    "requires_approval": thread.requires_approval
                }
        
        # Determine if post can be marked solved
        can_solve = post.post_type in ["question", "discussion", "problem"] and post.student_id == current_user.id
        
        return jsonify({
            "status": "success",
            "data": {
                "post_id": post_id,
                "is_author": post.student_id == current_user.id,
                "post_type": post.post_type,
                "is_solved": post.is_solved,
                "is_locked": post.is_locked,
                "interactions": {
                    "followed": user_followed
                },
                "author": {
                    "id": author.id,
                    "name": author.name,
                    'connection': has_connection,
                    "username": author.username,
                    "connection_status": connection_status
                } if author else None,
                "thread": thread_data,
                "permissions": {
                    "can_edit": post.student_id == current_user.id,
                    "can_delete": post.student_id == current_user.id,
                    "can_solve": can_solve,
                    "can_mark_solution": can_solve
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get post options menu error: ", exc_info=True)
        return error_response("Failed to load post options")


@posts_crud_bp.route("/posts/by-status", methods=["GET"])
@token_required
def get_posts_by_status(current_user):
    """
    Get posts grouped by solved/unsolved status
    Only applicable to 'question' and 'problem' post types
    
    Query params:
    - status: solved, unsolved, all (default: all)
    - post_type: Optional filter (question, problem, or both)
    - page: Page number (default: 1)
    - per_page: Posts per page (default: 20)
    - department: Optional filter by department
    """
    try:
        # Get query parameters
        status = request.args.get("status", "all").strip().lower()
        post_type = request.args.get("post_type", "").strip().lower()
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        department = request.args.get("department", "").strip()
        
        # Validate status
        valid_statuses = ["solved", "unsolved", "all"]
        if status not in valid_statuses:
            return error_response(
                f"Invalid status. Must be one of: {', '.join(valid_statuses)}", 
                400
            )
        
        # Build base query for solvable post types
        if post_type and post_type in ["question", "problem"]:
            query = Post.query.filter_by(post_type=post_type)
        else:
            # Both question and problem types
            query = Post.query.filter(
                Post.post_type.in_(["question", "problem"])
            )
        
        # Apply status filter
        if status == "solved":
            query = query.filter_by(is_solved=True)
        elif status == "unsolved":
            query = query.filter_by(is_solved=False)
        # if status == "all", don't filter by is_solved
        
        # Apply optional department filter
        if department:
            query = query.filter_by(department=department)
        
        # Order: unsolved first (if status=all), then by most recent
        if status == "all":
            query = query.order_by(
                Post.is_solved.asc(),  # False (unsolved) comes before True (solved)
                Post.posted_at.desc()
            )
        else:
            query = query.order_by(Post.posted_at.desc())
        
        # Paginate
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Get counts for summary
        total_query = Post.query.filter(
            Post.post_type.in_(["question", "problem"])
        )
        if department:
            total_query = total_query.filter_by(department=department)
        if post_type and post_type in ["question", "problem"]:
            total_query = total_query.filter_by(post_type=post_type)
        
        solved_count = total_query.filter_by(is_solved=True).count()
        unsolved_count = total_query.filter_by(is_solved=False).count()
        
        # Build response
        posts_data = []
        for post in paginated.items:
            author = User.query.get(post.student_id)
            
            # Check user interactions
            user_reacted = PostReaction.query.filter_by(
                post_id=post.id, 
                student_id=current_user.id
            ).first()
            
            user_bookmarked = Bookmark.query.filter_by(
                post_id=post.id, 
                student_id=current_user.id
            ).first()
            
            # Get solution comment if solved
            solution_comment = None
            if post.is_solved:
                solution = Comment.query.filter_by(
                    post_id=post.id,
                    is_solution=True
                ).first()
                
                if solution:
                    solution_author = User.query.get(solution.student_id)
                    solution_comment = {
                        "id": solution.id,
                        "text_preview": solution.text_content[:100] + "..." if len(solution.text_content) > 100 else solution.text_content,
                        "author": {
                            "id": solution_author.id,
                            "username": solution_author.username,
                            "name": solution_author.name,
                            "avatar": solution_author.avatar
                        } if solution_author else None
                    }
            
            posts_data.append({
                "id": post.id,
                "title": post.title,
                "excerpt": post.text_content[:200] if post.text_content else None,
                "post_type": post.post_type,
                "department": post.department,
                "tags": post.tags,
                "is_solved": post.is_solved,
                "solved_at": post.solved_at.isoformat() if post.solved_at else None,
                "solution_comment": solution_comment,
                "reactions_count": post.positive_reactions_count or 0,
                "comments_count": post.comments_count,
                "views_count": post.views_count,
                "posted_at": post.posted_at.isoformat(),
                "is_author": post.student_id == current_user.id,
                "author": {
                    "id": author.id,
                    "username": author.username,
                    "name": author.name,
                    "avatar": author.avatar,
                    "reputation_level": author.reputation_level
                } if author else None,
                "user_interactions": {
                    "user_reacted": bool(user_reacted),
                    "reaction_type": user_reacted.reaction_type if user_reacted else None,
                    "bookmarked": bool(user_bookmarked)
                }
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "posts": posts_data,
                "summary": {
                    "total": solved_count + unsolved_count,
                    "solved": solved_count,
                    "unsolved": unsolved_count,
                    "solved_percentage": round((solved_count / (solved_count + unsolved_count) * 100), 1) if (solved_count + unsolved_count) > 0 else 0
                },
                "filters": {
                    "status": status,
                    "post_type": post_type or "all",
                    "department": department or None
                },
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
        current_app.logger.error(f"Get posts by status error: ", exc_info=True)
        return error_response("Failed to load posts")
        
        

@posts_crud_bp.route("/posts/<int:post_id>/view", methods=["POST"])
@token_required
def view_post(current_user, post_id):
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")
        post = Post.query.get(post_id)
        if not post:
            return error_response("Post not found")
        existing = PostView.query.filter_by(user_id=user.id, post_id=post_id).first()
        if existing:
            return success_response("Already viewed")
        post_view = PostView(user_id=user.id,post_id=post_id)
        db.session.add(post_view)
        post.views_count += 1  # Increment on Post model
        db.session.commit()
        
        return success_response("Post viewed")
    except Exception as e:
        current_app.logger.error(f"View post error: ", exc_info=True)
        return error_response("Failed to view posts")
        

@posts_crud_bp.route("/posts/<int:post_id>/metrics", methods=["GET"])
@token_required
def get_post_metrics(current_user, post_id):
    """
    Get detailed engagement metrics for a post
    Useful for analytics dashboard
    """
    try:
        post = Post.query.get(post_id)
        if not post:
            return error_response("Post not found", 404)
        
        # Get reaction breakdown
        reactions = db.session.query(
            PostReaction.reaction_type,
            func.count(PostReaction.id).label('count')
        ).filter(
            PostReaction.post_id == post_id
        ).group_by(PostReaction.reaction_type).all()
        
        # Get activity timeline (last 7 days)
        week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        daily_views = db.session.query(
            func.date(PostView.viewed_at).label('date'),
            func.count(PostView.id).label('views')
        ).filter(
            PostView.post_id == post_id,
            PostView.viewed_at >= week_ago
        ).group_by(func.date(PostView.viewed_at)).all()
        
        # Get comment rate (comments per hour)
        time_since_post = (datetime.datetime.utcnow() - post.posted_at).total_seconds() / 3600
        comment_rate = post.comments_count / max(time_since_post, 1)
        
        return jsonify({
            "status": "success",
            "data": {
                "post_id": post_id,
                "total_views": post.views_count,
                "total_reactions": post.positive_reactions_count,
                "total_comments": post.comments_count,
                "total_bookmarks": post.bookmark_count,
                "reaction_breakdown": {r[0]: r[1] for r in reactions},
                "engagement_rate": (post.positive_reactions_count + post.comments_count) / max(post.views_count, 1),
                "comment_rate_per_hour": round(comment_rate, 2),
                "daily_views": [{"date": str(d[0]), "views": d[1]} for d in daily_views],
                "is_trending": post.views_count > 100 and comment_rate > 5
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get metrics error: ", exc_info=True)
        return error_response("Failed to load metrics")
        

@posts_crud_bp.route("/posts/<int:post_id>/report", methods=["POST"])
@token_required
def report_post(current_user, post_id):
    """
    Report post for moderation
    Body: {"reason": "spam", "description": "Details..."}
    """
    try:
        post = Post.query.get(post_id)
        if not post:
            return error_response("Post not found", 404)
        
        data = request.get_json()
        reason = data.get("reason", "").strip()
        description = data.get("description", "").strip()
        
        valid_reasons = ["spam", "harassment", "inappropriate", "misinformation", "other"]
        if reason not in valid_reasons:
            return error_response(f"Reason must be one of: {', '.join(valid_reasons)}")
        
        # Check if already reported by this user
        existing = PostReport.query.filter_by(
            post_id=post_id,
            reported_by=current_user.id,
            status="pending"
        ).first()
        
        if existing:
            return error_response("You've already reported this post", 409)
        
        report = PostReport(
            post_id=post_id,
            reported_by=current_user.id,
            reason=reason,
            description=description
        )
        db.session.add(report)
        db.session.commit()
        
        return success_response("Report submitted. Our team will review it.")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Report error: ", exc_info=True)
        return error_response("Failed to submit report")

@posts_crud_bp.route("/posts/create", methods=["POST"])
@token_required
def create_post(current_user):
    """
    Create a new post
    
    Supports:
    - Text content
    - File attachments (images, videos, documents)
    - Tags for discovery
    - Thread collaboration toggle
    - @mentions detection
    
    Body (JSON):
    - title: Post title (required)
    - text_content: Post body
    - post_type: question, discussion, announcement, resource, problem
    - department: Department tag
    - tags: Array of tags
    - thread_enabled: Boolean
    - resources: Array of uploaded file URLs
    """
    try:
        # Spam check
        is_spam, spam_reason = check_spam(current_user.id, "post")
        if is_spam:
            return error_response(f"Rate limit exceeded: {spam_reason}", 429)
        
        # Get JSON data
        data = request.get_json()
        
        if not data:
            return error_response("No data provided")
        
        # Validation
        title = data.get("title", "").strip()
        
        if not title:
            return error_response("Title is required")
        
        if len(title) < 5:
            return error_response("Title too short (minimum 5 characters)")
        
        if len(title) > 200:
            return error_response("Title too long (maximum 200 characters)")
        
        text_content = data.get("text_content", "").strip()
        post_type = data.get("post_type", "discussion")
        
        # Validate post type
        valid_types = ["question", "discussion", "announcement", "resource", "problem"]
        if post_type not in valid_types:
            return error_response(f"Invalid post type. Must be one of: {', '.join(valid_types)}")
        
        # Get department from profile
        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
        department = data.get("department", profile.department if profile else None)
        
        # Parse tags
        tags = data.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        
        # Limit tags
        tags = tags[:5]
        
        # Get resources (file URLs)
        resources = data.get("resources", [])
        if not isinstance(resources, list):
            resources = []
        
        # ✅ VALIDATE resource structure
        validated_resources = []
        for resource in resources:
            if isinstance(resource, dict) and "url" in resource:
                # Ensure all required fields exist
                validated_resources.append({
                    "url": resource.get("url"),
                    "type": resource.get("type", "document"),
                    "filename": resource.get("filename", "file")
                })
            elif isinstance(resource, str):
                # Legacy support: if just URL, convert to object
                validated_resources.append({
                    "url": resource,
                    "type": "document",
                    "filename": "file"
                })
        
       
        
        # Thread settings
        thread_enabled = data.get("thread_enabled", False)
        
        
        # Create post
        new_post = Post(
            student_id=current_user.id,
            title=title,
            text_content=text_content,
            post_type=post_type,
            department=department,
            tags=tags,
            posted_at = datetime.datetime.utcnow(),
            thread_enabled=thread_enabled,
            resources=validated_resources  # Array of resource URLs
        )
        
        db.session.add(new_post)
        db.session.flush()  # Get post ID
        
        # Create thread if enabled
        if thread_enabled:
            thread_title = data.get("thread_title", title)
            thread_description = data.get("thread_description", "Study Discussion")
            max_members = data.get("max_members")
            requires_approval = data.get("requires_approval", False)
            
            thread = Thread(
                creator_id=current_user.id,
                post_id=new_post.id,
                title=thread_title,
                description=thread_description,
                max_members=max_members,
                requires_approval=requires_approval
            )
            db.session.add(thread)
        
        
        # Detect mentions
        mentioned_users = detect_and_create_mentions(
            text_content,
            current_user.id,
            "post",
            new_post.id
        )
        
        # Update user stats
        current_user.total_posts = (current_user.total_posts or 0) + 1
        
        # Update activity
        update_user_activity(current_user.id, "post")
        
        db.session.commit()
        
        return success_response(
            "Post created successfully!",
            data={
                "post": {
                    "id": new_post.id,
                    "title": new_post.title,
                    "post_type": new_post.post_type,
                    "thread_enabled": new_post.thread_enabled,
                    "posted_at": new_post.posted_at.isoformat()
                },
                "mentioned_users": mentioned_users
            }
        ), 201
        
    except ValueError as e:
        db.session.rollback()
        return error_response(str(e))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create post error: ", exc_info=True)
        return error_response("Failed to create post")

@posts_crud_bp.route("/posts/<int:post_id>/quick-view", methods=["GET"])
@token_required
def quick_view_post(current_user, post_id):
    """Get single post with full details"""
    try:
        post = Post.query.get(post_id)
        
        if not post:
            return error_response("Post not found", 404)
        # ✅ FIXED: Proper return statement (was missing try/except indent)
        return jsonify({
            "status": "success", 
            "data": {
                "id": post.id, 
                "title": post.title, 
                "content": post.text_content
            }
        })
    
    except Exception as e:  # ✅ FIXED: This was incorrectly indented
        db.session.rollback()
        current_app.logger.error(f"Get post error: ", exc_info=True)
        return error_response("Failed to get post")

@posts_crud_bp.route("/posts/<int:post_id>", methods=["GET"])
@token_required
def get_post(current_user, post_id):
    """Get single post with full details"""
    try:
        post = Post.query.get(post_id)
        
        if not post:
            return error_response("Post not found", 404)
        
        post.views_count += 1
        db.session.commit()
        
        # Get author info
        author = User.query.get(post.student_id)
        author_profile = StudentProfile.query.filter_by(user_id=author.id).first() if author else None
        
        # Check user's interactions
        user_reaction = PostReaction.query.filter_by(
            post_id=post_id,
            student_id=current_user.id
        ).first()
        
        user_bookmark = Bookmark.query.filter_by(
            post_id=post_id,
            student_id=current_user.id
        ).first()
        
        user_following = PostFollow.query.filter_by(
            post_id=post_id,
            student_id=current_user.id
        ).first()
        
        # Get reaction breakdown
        reactions = db.session.query(
            PostReaction.reaction_type,
            func.count(PostReaction.id).label('count')
        ).filter(
            PostReaction.post_id == post_id
        ).group_by(PostReaction.reaction_type).all()
        
        reaction_counts = {r[0]: r[1] for r in reactions}
        
        # Check if user is author
        is_author = post.student_id == current_user.id
        
        # Check connection with author
        connection_status = "none"
        if author and author.id != current_user.id:
            connection = Connection.query.filter(
                or_(
                    and_(Connection.requester_id == current_user.id, Connection.receiver_id == author.id),
                    and_(Connection.requester_id == author.id, Connection.receiver_id == current_user.id)
                ),
                Connection.status == "accepted"
            ).first()
            if connection:
                connection_status = "connected"
        
        return jsonify({
            "status": "success",
            "data": {
                "post": {
                    "id": post.id,
                    "title": post.title,
                    "text_content": post.text_content,
                    "post_type": post.post_type,
                    "department": post.department,
                    "tags": post.tags,
                    "resources": post.resources,
                    "thread_enabled": post.thread_enabled,
                    "is_solved": post.is_solved,
                    "is_pinned": post.is_pinned,
                    "is_locked": post.is_locked,
                    "posted_at": post.posted_at.isoformat(),
                    "edited_at": post.edited_at.isoformat() if post.edited_at else None,
                    "solved_at": post.solved_at.isoformat() if post.solved_at else None
                },
                "stats": {
                    "reactions_count": post.positive_reactions_count,
                    "comments_count": post.comments_count,
                    "views": post.views_count,
                    "reactions": reaction_counts
                },
                "author": {
                    "id": author.id,
                    "username": author.username,
                    "name": author.name,
                    "avatar": author.avatar,
                    "reputation": author.reputation,
                    "reputation_level": author.reputation_level,
                    "department": author_profile.department if author_profile else None
                } if author else None,
                "user_interaction": {
                    "reaction": user_reaction.reaction_type if user_reaction else None,
                    "bookmarked": bool(user_bookmark),
                    "following": bool(user_following),
                    "is_author": is_author
                },
                "permissions": {
                    "can_edit": is_author,
                    "can_delete": is_author,
                    "can_mark_solved": is_author and post.post_type in ["question", "problem"],
                    "can_comment": not post.is_locked,
                    "connection_with_author": connection_status
                }
            }
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Get post error: ", exc_info=True)
        return error_response("Failed to load post")

@posts_crud_bp.route("/posts/<int:post_id>/edit", methods=["PATCH"])
@token_required
def edit_post(current_user, post_id):
    """
    Edit your own post
    
    Can update: title, text_content, tags, thread_enabled
    Cannot change: post_type, department (for integrity)
    """
    try:
        post = Post.query.get(post_id)
        
        if not post:
            return error_response("Post not found", 404)
        
        # Verify ownership
        if post.student_id != current_user.id:
            return error_response("You can only edit your own posts", 403)
        
        data = request.get_json()
        changes = []
        
        # Update title
        if "title" in data:
            new_title = data["title"].strip()
            if len(new_title) < 5:
                return error_response("Title too short")
            if new_title != post.title:
                post.title = new_title
                changes.append("title")
        
        # Update content
        if "text_content" in data:
            new_content = data["text_content"].strip()
            if new_content != post.text_content:
                post.text_content = new_content
                changes.append("content")
                
                # Re-detect mentions (delete old, create new)
                Mention.query.filter_by(
                    mentioned_in_type="post",
                    mentioned_in_id=post_id
                ).delete()
                
                detect_and_create_mentions(
                    new_content,
                    current_user.id,
                    "post",
                    post_id
                )
        
        # Update tags
        if "tags" in data:
            new_tags = data["tags"]
            if isinstance(new_tags, list):
                post.tags = new_tags[:5]
                changes.append("tags")
        
        # Update thread enabled (only if no threads exist yet)
        if "thread_enabled" in data:
            if post.threads.count() == 0:  # No threads created yet
                post.thread_enabled = bool(data["thread_enabled"])
                changes.append("thread_enabled")
        
        if changes:
            post.edited_at = datetime.datetime.utcnow()
            db.session.commit()
            
            return success_response(
                "Post updated successfully",
                data={
                    "changes": changes,
                    "edited_at": post.edited_at.isoformat()
                }
            )
        else:
            return success_response("No changes made")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Edit post error: ", exc_info=True)
        return error_response("Failed to update post")


@posts_crud_bp.route("/posts/<int:post_id>", methods=["DELETE"])
@token_required
def delete_post(current_user, post_id):
    """
    Delete your own post

    Cascade deletes (via SQLAlchemy relationship cascade="all, delete-orphan"):
    - All comments
    - All reactions/likes
    - All bookmarks
    - Associated threads

    Manually cleaned up here (H-3 fix — these are NOT covered by the ORM
    cascades above, so they used to be left behind as orphaned rows
    referencing a post_id that no longer exists):
    - PostView / PostFollow: real foreign keys to posts.id, but neither
      relationship on Post declares a cascade.
    - Mention: mentioned_in_id is a plain Integer (it's polymorphic — a
      mention can point at a post, a comment, or a thread message), so it
      can never be a real ForeignKey and can never be cleaned up by the
      database automatically. We remove mentions for the post itself AND
      for every comment that's about to be cascade-deleted with it.
    """
    try:
        post = Post.query.get(post_id)
        
        if not post:
            return error_response("Post not found", 404)
        
        # Verify ownership
        if post.student_id != current_user.id:
            return error_response("You can only delete your own posts", 403)

        # H-3 fix: clean up rows that aren't covered by ORM/FK cascades.
        comment_ids = [
            row[0] for row in
            db.session.query(Comment.id).filter_by(post_id=post_id).all()
        ]

        PostView.query.filter_by(post_id=post_id).delete(synchronize_session=False)
        PostFollow.query.filter_by(post_id=post_id).delete(synchronize_session=False)

        mention_clauses = [
            and_(Mention.mentioned_in_type == "post", Mention.mentioned_in_id == post_id)
        ]
        if comment_ids:
            mention_clauses.append(
                and_(Mention.mentioned_in_type == "comment", Mention.mentioned_in_id.in_(comment_ids))
            )
        Mention.query.filter(or_(*mention_clauses)).delete(synchronize_session=False)

        db.session.delete(post)
        
        # Update user stats
        if current_user.total_posts > 0:
            current_user.total_posts -= 1
        
        db.session.commit()
        
        return success_response("Post deleted successfully")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete post error: ", exc_info=True)
        return error_response("Failed to delete post")


# ============================================================================
# POST INTERACTIONS - Likes, Reactions, Bookmarks
# ============================================================================


@posts_crud_bp.route("/posts/<int:post_id>/mark-solved", methods=["POST"])
@token_required
def mark_solved(current_user, post_id):
    try:
        post = Post.query.get(post_id)
        
        if not post:
            return error_response("Post not found", 404)
        
        if post.student_id != current_user.id:
            return error_response("Only post author can mark as solved", 403)
        
        if post.post_type not in ["question","problem"]:
            return error_response("Only questions and problems can be marked as solved")
        
        post.is_solved = True
        post.solved_at = datetime.datetime.utcnow()  # Add this
        
        db.session.commit()  # ADD THIS LINE
        
        return success_response("Post marked as solved successfully")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Mark solved error: ", exc_info=True)
        return error_response("Failed to mark post as solved")
        

@posts_crud_bp.route("/posts/<int:post_id>/unmark-solved", methods=["POST"])
@token_required
def unmark_solved(current_user, post_id):
    try:
        post = Post.query.get(post_id)
        
        if not post:
            return error_response("Post not found", 404)
        
        if post.student_id != current_user.id:
            return error_response("Only post author can unmark as solved", 403)
        
        if post.post_type not in ["question","problem"]:
            return error_response("Only questions and problems can be unmarked as solved")
        post.is_solved = False
        post_comments = Comment.query.filter_by(post_id=post.id).all()
        for comment in post_comments:
            if comment.is_solution:
                comment.is_solution = False
        db.session.commit()  # ADD THIS
        return success_response("Post unmarked as solved successfully")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"UnMark solved error: ", exc_info=True)
        return error_response("Failed to unmark post as solved")

@posts_crud_bp.route("/posts/<int:post_id>/follow", methods=["POST"])
@token_required
def follow_post(current_user, post_id):
    """
    Follow post to get notifications of new activity
    """
    try:
        post = Post.query.get(post_id)
        if not post:
            return error_response("Post not found", 404)
        
        existing = PostFollow.query.filter_by(
            post_id=post_id,
            student_id=current_user.id
        ).first()
        
        if existing:
            return error_response("Already following this post", 409)
        
        follow = PostFollow(
            post_id=post_id,
            student_id=current_user.id
        )
        db.session.add(follow)
        db.session.commit()
        
        return success_response("Now following post"), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Follow post error: ", exc_info=True)
        return error_response("Failed to follow post")

@posts_crud_bp.route("/posts/<int:post_id>/unfollow", methods=["DELETE"])
@token_required
def unfollow_post(current_user, post_id):
    """
    Unfollow post (stop notifications)
    """
    try:
        follow = PostFollow.query.filter_by(
            post_id=post_id,
            student_id=current_user.id
        ).first()
        
        if not follow:
            return error_response("Not following this post", 404)
        
        db.session.delete(follow)
        db.session.commit()
        
        return success_response("Unfollowed post")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Unfollow post error: ", exc_info=True)
        return error_response("Failed to unfollow post")


# ============================================================================
# COMMENTS & REPLIES
# ============================================================================

@posts_crud_bp.route("/posts/my-posts", methods=["GET"])
@token_required
def get_my_posts(current_user):
    """
    Get all posts created by current user
    """
    try:
        page = request.args.get("page", 1, type=int)
        per_page = 20
        
        paginated = Post.query.filter_by(
            student_id=current_user.id
        ).order_by(Post.posted_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        posts_data = []
        for post in paginated.items:
            posts_data.append({
                "id": post.id,
                "title": post.title,
                "post_type": post.post_type,
                "is_solved": post.is_solved,
                "reactions_count": post.positive_reactions_count,
                "comments_count": post.comments_count,
                "is_pinned": post.is_pinned,
                "views_count": post.views_count,
                "posted_at": post.posted_at.isoformat()
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "posts": posts_data,
                "pagination": {
                    "page": page,
                    "total": paginated.total,
                    "pages": paginated.pages
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get my posts error: ", exc_info=True)
        return error_response("Failed to load your posts")


