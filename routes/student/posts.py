"""
StudyHub - Complete Post System
Create, edit, interact with posts - the core of student collaboration
Includes: CRUD, mentions, reactions, comments, bookmarks, spam prevention
"""

from flask import Blueprint, request, jsonify, current_app,Response, stream_with_context
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
from routes.student.badges import check_and_award_badge

import datetime
import mimetypes
from datetime import date, timedelta
from collections import defaultdict
import logging


import random
logger = logging.getLogger(__name__)

# Add to model imports
from models import (
    User, StudentProfile, Post, Comment, Connection,PostReaction, PostReport,
    Bookmark, PostFollow, Mention, Notification, ReputationHistory, BookmarkFolder, Bookmark,ThreadMember,
    UserActivity, PostView,CommentHelpfulMark, Connection, CommentLike, ThreadJoinRequest,Thread  # ← Added Thread
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response,
    save_file, ALLOWED_IMAGE_EXT, ALLOWED_DOCUMENT_EXT
)

# Document 1 §2.3 / Document 2 §3.10: pure helper functions extracted to
# services/post_service.py — imported here at the same names so every
# existing call site in this file keeps working unchanged.
from services.post_service import (
    extract_public_id,
    update_post_reaction_count,
    detect_and_create_mentions,
    check_spam,
    update_user_activity,
)

posts_bp = Blueprint("student_posts", __name__)
import cloudinary

try:
    from routes.student.storage import cloudinary_storage, filename_service
    STORAGE_AVAILABLE = True
    logger.info("Storage module available")
except ImportError as e:
    STORAGE_AVAILABLE = False
    logger.warning(f"Storage module not available: {str(e)}")
except Exception as e:
    STORAGE_AVAILABLE = False
    logger.warning(f"Storage initialization failed: {str(e)}")

import re
import base64
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

@posts_bp.route("/posts/<int:post_id>/ask-learnora", methods=["POST", "GET"])
@token_required
def ask_learnora_about_post(current_user, post_id):
    """
    Ask Learnora AI a question about a specific post.
    Non-streaming: fetches the post, sends it + the question to the AI,
    and returns the full answer in a single JSON response.
 
    Body (optional): { "question": "..." }O
    If no question is provided, a sensible default is used.
    """
    try:
        post = Post.query.get(post_id)
        if not post:
            return error_response("Post not found", 404)
 
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
 
        if not question:
            question = "Can you explain this post, summarize the key points, and offer any helpful insight?"
 
        from routes.student.learnora import provider_manager, StudyAssistant
 
        provider = provider_manager.get_working_provider(needs_vision=False)
        if not provider:
            return error_response("AI service temporarily unavailable. Please try again later.", 503)
 
        assistant = StudyAssistant(provider, conversation_messages=[])
        assistant.select_model(has_images=False)
 
        post_context = f"""
**Post Title:** {post.title}
 
**Post Content:**
{post.text_content or '[No content]'}
"""
 
        messages = [
            {
                "role": "system",
                "content": "You are Learnora, a helpful study assistant. Use the post content below as context to answer the user's question clearly and concisely."
            },
            {
                "role": "user",
                "content": f"{post_context}\n\n**Question:** {question}"
            }
        ]
 
        # Consume the underlying stream_response generator fully so the
        # client gets one normal JSON response instead of SSE.
        full_response = ""
        error_occurred = False
        error_message = None
        retries = 0
        max_retries = 2
 
        while retries < max_retries:
            error_in_stream = False
 
            for chunk in assistant.stream_response(messages):
                if chunk.startswith("data: "):
                    try:
                        chunk_data = json.loads(chunk[6:])
 
                        if "content" in chunk_data:
                            full_response += chunk_data["content"]
                        elif "error" in chunk_data:
                            error_occurred = True
                            error_message = chunk_data.get("error")
 
                            if chunk_data.get("rate_limit") or chunk_data.get("timeout"):
                                error_in_stream = True
                                provider_manager.mark_provider_failed(provider["name"])
                                provider_manager.rotate()
                                next_provider = provider_manager.get_working_provider(needs_vision=False)
 
                                if next_provider and retries < max_retries - 1:
                                    provider = next_provider
                                    assistant.provider = next_provider
                                    assistant.select_model(has_images=False)
                                    retries += 1
                                    full_response = ""  # discard partial response before retry
                                    break
                    except Exception:
                        pass
 
            if not error_in_stream:
                break
 
        if error_occurred and not full_response:
            return error_response(error_message or "Failed to get a response from the AI service", 503)
 
        return jsonify({
            "status": "success",
            "data": {
                "post_id": post.id,
                "question": question,
                "answer": full_response
            }
        })
 
    except Exception as e:
        current_app.logger.error("Ask Learnora about post error: ", exc_info=True)
        return error_response("Failed to get AI response about this post")
 
# ─────────────────────────────────────────────────────────────────────────────
# HELPER: Build a post dict (shared between feed and tag endpoints)
# ─────────────────────────────────────────────────────────────────────────────
@posts_bp.route("/posts/feed", methods=["GET"])
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

@posts_bp.route("/posts/<int:post_id>/react", methods=["POST"])
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
@posts_bp.route("/posts/resource/upload", methods=["POST"])
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
# top of this file). check_helpful_milestones stays here for now — it
# calls check_and_award_badge, which currently lives in
# routes/student/badges.py (not yet a service), so moving it into
# post_service.py would make a service import from routes/*, violating
# Document 2 §2's layering rule. Revisit once services/badge_service.py
# exists.
# ============================================================================
def check_helpful_milestones(user_id):
    """Check if user reached helpful count milestones"""
    user = User.query.get(user_id)
    if not user:
        return
    
    helpful_count = PostReaction.query.filter_by(
        reaction_type="helpful"
    ).join(Post).filter(
        Post.student_id == user_id
    ).count()
    
    # Check badge criteria
    if helpful_count == 10:
        check_and_award_badge(user_id, "Helpful Csontributor")
    elif helpful_count == 50:
        check_and_award_badge(user_id, "Helpful Hero")
        


# ============================================================================
# POST CRUD OPERATIONS
# ============================================================================
# Add these endpoints to your posts.py file

@posts_bp.route("/posts/by-type", methods=["GET"])
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

@posts_bp.route("/posts/<int:post_id>/options-menu", methods=["GET"])
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


@posts_bp.route("/posts/by-status", methods=["GET"])
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
        
        
@posts_bp.route("/posts/bookmark/toggle", methods=["POST"])
@token_required
def toggle_bookmarks(current_user):
    """
    Toggle bookmark status for multiple posts.

    Body (JSON):
    - post_ids: list[int] (required)
    - folder_name: optional (default "Saved")
    - notes: optional
    - tags: optional list
    """
    try:
        data = request.get_json() or {}
        post_ids = data.get("post_ids", [])

        if not isinstance(post_ids, list) or not post_ids:
            return error_response("post_ids must be a non-empty list", 400)

        folder_name = data.get("folder_name", "Saved").strip()
        notes = (data.get("notes") or "").strip() or None
        tags = data.get("tags", [])
        tags = tags[:10] if isinstance(tags, list) else []

        results = []

        # 🔹 Find or create folder once
        folder = BookmarkFolder.query.filter_by(
            user_id=current_user.id,
            name=folder_name
        ).first()

        if not folder:
            max_position = db.session.query(
                func.max(BookmarkFolder.position)
            ).filter_by(user_id=current_user.id).scalar() or 0

            folder = BookmarkFolder(
                user_id=current_user.id,
                name=folder_name,
                icon="📁",
                color="#6B7280",
                position=max_position + 1,
                is_default=(folder_name == "Saved")
            )
            db.session.add(folder)
            db.session.flush()

        for post_id in post_ids:
            post = Post.query.get(post_id)

            if not post:
                results.append({
                    "post_id": post_id,
                    "success": False,
                    "error": "Post not found"
                })
                continue

            existing = Bookmark.query.filter_by(
                post_id=post_id,
                student_id=current_user.id
            ).first()

            # 🔻 UNBOOKMARK
            if existing:
                db.session.delete(existing)
                post.bookmark_count = max(0, post.bookmark_count - 1)
                folder.bookmark_count = max(0, folder.bookmark_count - 1)

                results.append({
                    "post_id": post_id,
                    "bookmarked": False
                })

            # 🔺 BOOKMARK
            else:
                bookmark = Bookmark(
                    post_id=post_id,
                    student_id=current_user.id,
                    folder_id=folder.id,
                    notes=notes,
                    tags=tags
                )

                db.session.add(bookmark)
                post.bookmark_count += 1
                folder.bookmark_count += 1

                results.append({
                    "post_id": post_id,
                    'bookmark_count': post.bookmark_count,
                    "bookmarked": True,
                    "bookmark_id": bookmark.id
                })

        db.session.commit()

        return success_response(
            "Bookmark toggle completed",
            data={
                "results": results,
                "folder": {
                    "id": folder.id,
                    "name": folder.name,
                    "icon": folder.icon,
                    "color": folder.color
                }
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Toggle bookmarks error: ", exc_info=True)
        return error_response("Failed to toggle bookmarks", 500)
        
        

        

        
        
@posts_bp.route("/comments/<int:comment_id>/resources", methods=["GET"])
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

@posts_bp.route("/posts/<int:post_id>/resources", methods=["GET"])
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
# refine_post/draft_post endpoints — their @posts_bp.route decorators
# had already been manually commented out, but the full function bodies
# were still shipping in this file). If AI-assisted post refinement is
# still wanted, re-implement it as a tracked feature using the same
# provider_manager/StudyAssistant pattern as ask_learnora_about_post()
# above, rather than restoring this block.
@posts_bp.route("/posts/<int:post_id>/apply-refinement", methods=["PATCH"])
@token_required
def apply_refinement(current_user, post_id):
    """
    Apply refined content to post after user approval
    """
    try:
        post = Post.query.get(post_id)
        
        if not post:
            return error_response("Post not found", 404)
        
        if post.student_id != current_user.id:
            return error_response("Only post author can update post", 403)
        
        data = request.get_json()
        
        if not data:
            return error_response("No data provided", 400)
        
        refined_title = data.get("title", "").strip()
        refined_content = data.get("content", "").strip()
        
        # Store original content for history (optional)
        original_content = {
            "title": post.title,
            "content": post.text_content,
            "refined_at": datetime.datetime.utcnow().isoformat()
        }
        
        # Update post
        post.title = refined_title
        post.text_content = refined_content
        post.edited_at = datetime.datetime.utcnow()
        
        # Re-detect mentions in refined content
        Mention.query.filter_by(
            mentioned_in_type="post",
            mentioned_in_id=post_id
        ).delete()
        
        detect_and_create_mentions(
            refined_content,
            current_user.id,
            "post",
            post_id
        )
        
        db.session.commit()
        
        return success_response(
            "Post refined successfully!",
            data={
                "post_id": post_id,
                "title": post.title,
                "content": post.text_content,
                "edited_at": post.edited_at.isoformat(),
                "original": original_content
            }
        )
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Apply refinement error: ", exc_info=True)
        return error_response("Failed to apply refinement")
        
@posts_bp.route("/posts/bulk/bookmark", methods=["POST"])
@token_required
def bulk_bookmark(current_user):
    """
    Bookmark/unbookmark multiple posts at once (toggle).
    Body: {"post_ids": [1, 2, 3], "folder": "Exam Prep"}
    """
    bookmark_info = []
    try:
        data = request.get_json()
        post_ids = data.get("post_ids")
        folder = data.get("folder", "Saved")

        if not post_ids or len(post_ids) > 50:
            return error_response("Please provide between 1 and 50 post ids")

        for post_id in post_ids:
            post = Post.query.get(post_id)
            if not post:
                bookmark_info.append({"post_id": post_id, "success": False, "error": "Post not found"})
                continue

            existing = Bookmark.query.filter_by(
                post_id=post_id,
                student_id=current_user.id
            ).first()

            if existing:
                # Unbookmark
                db.session.delete(existing)
                post.bookmark_count = max(0, post.bookmark_count - 1)
                bookmark_info.append({
                    "post_id": post_id,
                    "bookmarked": False,
                    "bookmark_count": post.bookmark_count
                })
            else:
                # Bookmark
                bookmark = Bookmark(
                    post_id=post_id,
                    student_id=current_user.id,
                    folder=folder
                )
                db.session.add(bookmark)
                post.bookmark_count += 1
                bookmark_info.append({
                    "post_id": post_id,
                    "bookmarked": True,
                    "bookmark_count": post.bookmark_count
                })

        db.session.commit()

        return success_response(
            f"Processed {len(bookmark_info)} posts",
            data={"bookmark_details": bookmark_info}
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error("Bulk bookmark error", exc_info=True)
        return error_response("Failed to bookmark posts")

@posts_bp.route("/posts/<int:post_id>/view", methods=["POST"])
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
        

@posts_bp.route("/posts/<int:post_id>/metrics", methods=["GET"])
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
        
@posts_bp.route("/posts/<int:post_id>/report", methods=["POST"])
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

@posts_bp.route("/posts/create", methods=["POST"])
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

@posts_bp.route("/posts/<int:post_id>/quick-view", methods=["GET"])
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
@posts_bp.route("/posts/<int:post_id>", methods=["GET"])
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

@posts_bp.route("/posts/<int:post_id>/edit", methods=["PATCH"])
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


@posts_bp.route("/posts/<int:post_id>", methods=["DELETE"])
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


@posts_bp.route("/posts/<int:post_id>/mark-solved", methods=["POST"])
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
        
@posts_bp.route("/posts/<int:post_id>/unmark-solved", methods=["POST"])
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

@posts_bp.route("/posts/<int:post_id>/unmark-solution", methods=["POST"])
@token_required
def unmark_solution(current_user, post_id):  # Changed from mark_solution
    """
    Unmark a specific comment as the solution
    """
    try:
        post = Post.query.get(post_id)
        
        if not post:
            return error_response("Post not found", 404)
        
        if post.student_id != current_user.id:
            return error_response("Only post author can unmark solution", 403)  # Fixed message
        
        if post.post_type not in ["question","problem"]:
            return error_response("Only questions and problems can be unmarked")
        
        data = request.get_json(silent=True) or {}
        comment_id = data.get("comment_id")
        
        if not comment_id:
            return error_response("comment_id is required")
        
        comment = Comment.query.get(comment_id)
        if not comment or comment.post_id != post_id:
            return error_response("Comment not found or doesn't belong to this post", 404)
        
        # Unmark solution
        comment.is_solution = False
        post.is_solved = False
        post.solved_at = None  # Add this
        
        db.session.commit()
        return success_response("Comment unmarked as solution successfully")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Unmark solution error: ", exc_info=True)
        return error_response("Failed to unmark comment as solution")
   
@posts_bp.route("/posts/<int:post_id>/mark-solution", methods=["POST"])
@token_required
def mark_solution(current_user, post_id):
    """
    Mark question/problem as solved
    Only ONE comment can be solution - auto-unmarks old one
    """
    try:
        post = Post.query.get(post_id)
        
        if not post:
            return error_response("Post not found", 404)
        
        if post.student_id != current_user.id:
            return error_response("Only post author can mark as solved", 403)
        
        if post.post_type not in ["question","problem"]:
            return error_response("Only questions and problems can be marked as solved")
        
        data = request.get_json(silent=True) or {}
        comment_id = data.get("comment_id")
        
        if not comment_id:
            return error_response("comment_id is required")
        
        comment = Comment.query.get(comment_id)
        if not comment or comment.post_id != post_id:
            return error_response("Comment not found or doesn't belong to this post", 404)
        
        # ✅ UNMARK old solution (if exists)
        old_solution = Comment.query.filter_by(
            post_id=post_id,
            is_solution=True
        ).first()
        
        if old_solution and old_solution.id != comment_id:
            old_solution.is_solution = False
            logger.info(f"Unmarked old solution: Comment {old_solution.id}")
        
        # Mark new solution
        comment.is_solution = True
        post.is_solved = True
        post.solved_at = datetime.datetime.utcnow()
        
        
        commenter = User.query.get(comment.student_id)
        if commenter and commenter.id != current_user.id:
            from routes.student.reputation import award_reputation
            # Document 2 §5: award_reputation() no longer commits internally.
            # Safe here without an immediate commit — this route's own
            # db.session.commit() a few lines below covers this change too,
            # as one transaction alongside the solution-marking and
            # notification writes.
            award_reputation(commenter.id, "comment_marked_solution", "comment", comment_id)
            
            # ✅ Check badge milestones
            from routes.student.badges import check_and_award_badge
            check_and_award_badge(commenter.id, "Problem Solver")
            check_and_award_badge(commenter.id, "Genius")
            
            # Notify commenter
            notification = Notification(
                user_id=commenter.id,
                title="Your answer was marked as the solution!",
                body=f'"{post.title}" (+15 reputation)',
                notification_type="solution_accepted",
                related_type="post",
                related_id=post_id
            )
            db.session.add(notification)
        
        db.session.commit()
        
        return success_response(
            "Post marked as solved",
            data={
                "solved_at": post.solved_at.isoformat(),
                "solution_comment_id": comment_id
            }
        )
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Mark solved error: ", exc_info=True)
        return error_response("Failed to mark as solved")

@posts_bp.route("/posts/<int:post_id>/bookmark", methods=["POST"])
@token_required
def bookmark_post(current_user, post_id):
    try:
        post = Post.query.get(post_id)
        if not post:
            return error_response("Post not found", 404)
        
        existing = Bookmark.query.filter_by(
            post_id=post_id,
            student_id=current_user.id
        ).first()
        
        if existing:
            db.session.delete(existing)
            post.bookmark_count = max(0, post.bookmark_count - 1)  # ✅ FIX: Decrement
            db.session.commit()
            return success_response("Post unbookmarked", data={
                "bookmarked": False, 
                "bookmark_count": post.bookmark_count
            })
        
        data = request.get_json(silent=True) or {}
        folder = data.get("folder", "Saved").strip()
        notes = data.get("notes", "").strip()
        
        bookmark = Bookmark(
            post_id=post_id,
            student_id=current_user.id,
            folder=folder,
            notes=notes if notes else None
        )
        db.session.add(bookmark)
        post.bookmark_count += 1
        
        db.session.commit()
        
        return success_response(
            "Post bookmarked",
            data={"bookmarked": True, "bookmark_count": post.bookmark_count}  # ✅ FIX: Return count
        ), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Bookmark post error: ", exc_info=True)
        return error_response("Failed to bookmark post")

@posts_bp.route("/posts/<int:post_id>/follow", methods=["POST"])
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

@posts_bp.route("/posts/<int:post_id>/unfollow", methods=["DELETE"])
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
@posts_bp.route("/comments/<int:comment_id>/like", methods=["POST"])
@token_required
def like_comment(current_user, comment_id):
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")
        comment = Comment.query.get(comment_id)
        if not comment:
            return error_response("Comment not found")
        if comment.is_deleted:
            return error_response("Comment has been deleted")
        post = Post.query.get(comment.post_id)
        if post and post.is_locked:
            return error_response("Post is locked")
        existing = CommentLike.query.filter_by(student_id=current_user.id, comment_id=comment_id).first()
        if existing:
            # Unlike
            db.session.delete(existing)
            comment.likes_count = max(0, comment.likes_count - 1)
            db.session.commit()
            return success_response("Comment unliked", data={"liked": False, "count": comment.likes_count})
            
        else:
            # Like
            new_like = CommentLike(
                comment_id=comment_id,
                student_id=current_user.id
            )
            db.session.add(new_like)
            comment.likes_count += 1
            
            # Notify comment author
            if comment.student_id != current_user.id:
                notification = Notification(
                    user_id=comment.student_id,
                    title=f"{current_user.name} liked your comment",
                    body="",
                    notification_type="like",
                    related_type="comment",
                    related_id=comment_id
                )
                db.session.add(notification)
        db.session.commit()
        return success_response("Comment liked", data={"liked": True, "count": comment.likes_count})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Comment like error: ", exc_info=True)
        return error_response("Failed to like comment")    

@posts_bp.route("/comments/<int:comment_id>/mark-helpful", methods=["POST"])
@token_required
def mark_comment_helpful(current_user, comment_id):
    """
    Mark a comment as helpful
    - User can mark multiple comments as helpful
    - Cannot mark own comment as helpful
    """
    try:
        comment = Comment.query.get(comment_id)
        if not comment:
            return error_response("Comment not found", 404)
        
        if comment.is_deleted:
            return error_response("Comment has been deleted", 400)
        
        # ✅ Cannot mark own comment
        if comment.student_id == current_user.id:
            return error_response("Cannot mark your own comment as helpful", 403)
        
        # Check if already marked
        existing = CommentHelpfulMark.query.filter_by(
            comment_id=comment_id,
            user_id=current_user.id
        ).first()
        
        
    
  
        
        if existing:
            db.session.delete(existing)
            if comment.helpful_count > 0:
                comment.helpful_count -= 1
            db.session.commit()  # ← this line is missing!
            return success_response("Comment unmarked helpful", data={"is_helpful": False, "count": comment.helpful_count})
        
        # Create mark
        helpful_mark = CommentHelpfulMark(
            comment_id=comment_id,
            user_id=current_user.id,
            marked_at=datetime.datetime.utcnow()
        )
        db.session.add(helpful_mark)
        
        # Increment count
        comment.helpful_count += 1
        
        db.session.commit()
        
        # ✅ Award reputation to commenter
        from routes.student.reputation import award_reputation
        award_reputation(comment.student_id, "comment_marked_helpful", "comment", comment_id)

        # Document 2 §5 fix: award_reputation() no longer commits internally
        # (moved to services/reputation_service.py, which follows the
        # "services don't commit" convention). This call site previously
        # relied entirely on that internal commit — added explicitly here so
        # the reputation change is actually persisted (this was a real,
        # silent bug: without this, the point award and any level-up
        # notification were computed in memory and never saved).
        db.session.commit()
        
        return success_response(
            "Comment marked as helpful",
            data={"is_helpful": True, "count": comment.helpful_count}
        ), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Mark helpful error: ", exc_info=True)
        return error_response("Failed to mark as helpful")

    
@posts_bp.route("/comments/<int:comment_id>", methods=["PATCH"])
@token_required
def edit_comment(current_user, comment_id):
    """
    Edit your own comment
    
    Body: {"text_content": "Updated text"}
    """
    try:
        comment = Comment.query.get(comment_id)
        
        if not comment:
            return error_response("Comment not found", 404)
        post = Post.query.get(comment.post_id)
        if not post:
            return error_response("Post not found or has been deleted")
        
        if comment.student_id != current_user.id:
            return error_response("You can only edit your own comments", 403)
        
        data = request.get_json()
        new_text = data.get("text_content", "").strip()
        
        if not new_text:
            return error_response("Comment text is required")
        
        if new_text == comment.text_content:
            return success_response("No changes made")
        
        comment.text_content = new_text
        comment.edited_at = datetime.datetime.utcnow()
        
        # Re-detect mentions
        Mention.query.filter_by(
            mentioned_in_type="comment",
            mentioned_in_id=comment_id
        ).delete()
        
        detect_and_create_mentions(
            new_text,
            current_user.id,
            "comment",
            comment_id
        )
        
        db.session.commit()
        
        return success_response(
            "Comment updated",
            data={"edited_at": comment.edited_at.isoformat()}
        )
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Edit comment error: ", exc_info=True)
        return error_response("Failed to edit comment")


@posts_bp.route("/comments/<int:comment_id>", methods=["DELETE"])
@token_required
def delete_comment(current_user, comment_id):
    """
    Delete your own comment (soft delete)
    """
    try:
        comment = Comment.query.get(comment_id)
        
        if not comment:
            return error_response("Comment not found", 404)
        
        if comment.student_id != current_user.id:
            return error_response("You can only delete your own comments", 403)
        
        # Soft delete (preserve structure for replies)
        comment.is_deleted = True
        comment.text_content = "[deleted]"
        
        # Update post comment count
        post = Post.query.get(comment.post_id)
        if post and post.comments_count > 0:
            post.comments_count -= 1
        
        db.session.commit()
        
        return success_response("Comment deleted")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete comment error: ", exc_info=True)
        return error_response("Failed to delete comment")


# ============================================================================
# FEED & DISCOVERY
# ===========================================================================
from models import ThreadMember

@posts_bp.route("/posts/tags/<tag>", methods=["GET"])
@token_required
def get_posts_by_tag(current_user, tag):
    """
    Get posts filtered by tag with PAGINATION
    """
    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        
        # PostgreSQL array contains operator
        query = Post.query.filter(Post.tags.contains([tag]))
        query = query.order_by(Post.posted_at.desc())
        
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Build response (same structure as feed)
        posts_data = []
        for post in paginated.items:
            comments_data = []
            comments = Comment.query.filter_by(
                post_id=post.id, 
                parent_id=None,
                is_deleted=False
            ).order_by(
                Comment.is_solution.desc(),
                Comment.likes_count.desc()
            ).limit(2).all()
            
            for comment in comments:
                user = User.query.get(comment.student_id)
                has_liked = CommentLike.query.filter_by(
                    student_id=current_user.id, 
                    comment_id=comment.id
                ).first() is not None
                
                comments_data.append({
                    'id': comment.id,
                    "likes_count": comment.likes_count,
                    "user_id": user.id,
                    "username": user.username,
                    "name": user.name,
                    "avatar": user.avatar,
                    "posted_at": comment.posted_at.isoformat(),
                    "is_solution": comment.is_solution,
                    "helpful_count": comment.helpful_count,
                    "resources": comment.resources,
                    "has_liked": has_liked,
                    "text_content": comment.text_content
                })
            
            # Get post author
            author = User.query.get(post.student_id)
            
            # Initialize default values
            connection_status = None
            is_solved = None
            is_pinned = None
            requested_thread = False
            is_member = False
            
            # Check connection status
            if author and author.id != current_user.id:
                connection = Connection.query.filter(
                    or_(
                        and_(Connection.requester_id == current_user.id, Connection.receiver_id == author.id),
                        and_(Connection.requester_id == author.id, Connection.receiver_id == current_user.id)
                    )
                ).first()
                
                if connection:
                    connection_status = connection.status
            
            # Check user reactions
            user_reacted = PostReaction.query.filter_by(
                post_id=post.id, 
                student_id=current_user.id
            ).first()
            
            user_bookmarked = Bookmark.query.filter_by(
                post_id=post.id, 
                student_id=current_user.id
            ).first()
            
            user_followed = PostFollow.query.filter_by(
                post_id=post.id, 
                student_id=current_user.id
            ).first()
            
            # Check thread request
            if post.thread_enabled:
                thread = Thread.query.filter_by(post_id=post.id).first()
                if thread:
                    requested_thread = ThreadJoinRequest.query.filter_by(
                        requester_id=current_user.id, 
                        thread_id=thread.id
                    ).first()
                    is_member = ThreadMember.query.filter_by(
                        thread_id=thread.id,
                        student_id=current_user.id
                    ).first() is not None
               
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

        # ✅ FIXED: Return with correct data structure (removed undefined filter_type)
        return jsonify({
            "status": "success",
            "data": {
                "posts": posts_data,
                "tag": tag,  # ✅ FIX: Use 'tag' instead of undefined 'filter_type'
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


@posts_bp.route("/posts/tags", methods=["GET"])
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
            

@posts_bp.route("/posts/my-posts", methods=["GET"])
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


@posts_bp.route("/posts/bookmarked", methods=["GET"])
@token_required
def get_bookmarked_posts(current_user):
    """
    Get all bookmarked posts organized by folder
    
    Query params:
    - folder: Filter by folder name
    """
    try:
        folder_filter = request.args.get("folder")
        
        query = Bookmark.query.filter_by(student_id=current_user.id)
        
        if folder_filter:
            query = query.filter_by(folder=folder_filter)
        
        bookmarks = query.order_by(Bookmark.bookmarked_at.desc()).all()
        
        bookmarks_data = []
        for bookmark in bookmarks:
            post = Post.query.get(bookmark.post_id)
            if post:
                author = User.query.get(post.student_id)
                bookmarks_data.append({
                    "bookmark_id": bookmark.id,
                    "folder": bookmark.folder,
                    "notes": bookmark.notes,
                    "bookmarked_at": bookmark.bookmarked_at.isoformat(),
                    "post": {
                        "id": post.id,
                        "title": post.title,
                        'content': post.text_content,
                        "post_type": post.post_type,
                        "posted_at": post.posted_at.isoformat(),
                        "author": {
                            "username": author.username,
                            "name": author.name,
                            "avatar": author.avatar,
                        } if author else None
                    }
                })
        
        # Get all unique folders
        folders = db.session.query(Bookmark.folder, func.count(Bookmark.id)).filter_by(
            student_id=current_user.id
        ).group_by(Bookmark.folder).all()
        
        folders_data = [{"name": f[0], "count": f[1]} for f in folders]
        
        return jsonify({
            "status": "success",
            "data": {
                "bookmarks": bookmarks_data,
                "folders": folders_data,
                "total": len(bookmarks_data)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get bookmarked posts error: ", exc_info=True)
        return error_response("Failed to load bookmark")



# ============================================================================
# COMMENTS & REPLIES - FIXED TO SUPPORT ONLY 2 LEVELS
# ============================================================================

@posts_bp.route("/comments/create", methods=["POST"])
@token_required
def create_comment(current_user):
    """
    Create comment/reply with file uploads
    
    **IMPORTANT:** Max depth = 1 (only 2 levels total)
    - Level 0: Top-level comments on posts
    - Level 1: Replies to top-level comments
    - Level 2+: NOT ALLOWED
    """
    try:
        is_spam, spam_reason = check_spam(current_user.id, "comment")
        if is_spam:
            return error_response(f"Rate limit exceeded: {spam_reason}", 429)
        
        # Get form data
        data = request.get_json()
        post_id = data.get("post_id")
        text_content = data.get("text_content", "").strip()
        parent_id = data.get("parent_id")
        resources = data.get("resources", [])
        
        if not isinstance(resources, list):
            resources = []
        
        # ✅ VALIDATE resource structure
        validated_resources = []
        for resource in resources:
            if isinstance(resource, dict) and "url" in resource:
                validated_resources.append({
                    "url": resource.get("url"),
                    "type": resource.get("type", "document"),
                    "filename": resource.get("filename", "file")
                })
            elif isinstance(resource, str):
                validated_resources.append({
                    "url": resource,
                    "type": "document",
                    "filename": "file"
                })
        
        # Validation
        if not post_id:
            return error_response("Post ID is required", 400)
        
        if not text_content:
            return error_response("Comment text cannot be empty", 400)
        
        if len(text_content) > 5000:
            return error_response("Comment too long (max 5000 characters)", 400)
        
        # Verify post exists
        post = Post.query.get(post_id)
        if not post:
            return error_response("Post not found", 404)
        
        if post.is_locked:
            return error_response("This post is locked", 403)
        
        # ✅ ENFORCE MAX DEPTH = 1 (only 2 levels)
        depth_level = 0
        parent_comment = None
        
        if parent_id:
            parent_comment = Comment.query.get(parent_id)
            if not parent_comment:
                return error_response("Parent comment not found", 404)
            
            if parent_comment.is_deleted:
                return error_response("Cannot reply to deleted comment", 400)
            
            # ✅ STRICT DEPTH CHECK - Block if parent is already level 1
            if parent_comment.depth_level >= 1:
                return error_response(
                    "Cannot reply to this comment. Maximum reply depth reached (2 levels only).",
                    400
                )
            
            depth_level = parent_comment.depth_level + 1
        
        # Create comment
        new_comment = Comment(
            post_id=post_id,
            student_id=current_user.id,
            parent_id=parent_id,
            text_content=text_content,
            depth_level=depth_level,
            resources=validated_resources 
        )
        
        db.session.add(new_comment)
        db.session.flush()
        
        # Update parent's reply count
        if parent_comment:
            parent_comment.replies_count += 1
        
        # Update post's comment count
        post.comments_count += 1
        
        # ✅ Detect mentions
        detect_and_create_mentions(
            text_content,
            current_user.id,
            "comment",
            new_comment.id
        )
        
        # ✅ Notify post author (if not self-comment)
        if post.student_id != current_user.id:
            notification = Notification(
                user_id=post.student_id,
                title=f"{current_user.name} commented on your post",
                body=f'"{post.title}"',
                notification_type="comment",
                related_type="post",
                related_id=post_id
            )
            db.session.add(notification)
        
        # Update activity
        update_user_activity(current_user.id, "comment")
        
        db.session.commit()
        
        # Fetch author data for response
        author = User.query.get(current_user.id)
        
        return jsonify({
            "status": "success",
            "message": "Comment posted successfully",
            "data": {
                "comment": {
                    "id": new_comment.id,
                    "post_id": new_comment.post_id,
                    "parent_id": new_comment.parent_id,
                    'comments_count': post.comments_count,
                    "text_content": new_comment.text_content,
                    "resources": new_comment.resources,
                    "likes_count": new_comment.likes_count,
                    "replies_count": new_comment.replies_count,
                    "depth_level": new_comment.depth_level, 
                    "helpful_count": new_comment.helpful_count,
                    "is_solution": new_comment.is_solution,
                    "posted_at": new_comment.posted_at.isoformat(),
                    "author": {
                        "id": author.id,
                        "name": author.name,
                        "username": author.username,
                        "avatar": author.avatar
                    },
                    "user_interactions": {
                        "liked": False,
                        "has_marked_helpful": False,
                        "is_author": True
                    },
                    # ✅ NEW: Tell frontend if this comment can receive replies
                    "can_reply": new_comment.depth_level < 1  # Only level 0 can receive replies
                }
            }
        }), 201
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Comment creation error: ", exc_info=True)
        return jsonify({
            "status": "error",
            "message": "Failed to post comment"
        }), 500


@posts_bp.route("/posts/<int:post_id>/comments", methods=["GET"])
@token_required
def post_comments(current_user, post_id):
    """
    Get all comments for a post
    Structure: Top-level comments (depth 0) with direct replies (depth 1) only
    """
    try:
        post = Post.query.get(post_id)
        if not post:
            return error_response("Post not found"), 404
        
        post_is_solved = post.is_solved

        # 1️⃣ Fetch Top-Level Comments (depth_level = 0)
        top_comments = Comment.query.filter_by(
            post_id=post_id,
            parent_id=None,
            is_deleted=False
        ).order_by(
            Comment.is_solution.desc(),
            Comment.likes_count.desc(),
            Comment.posted_at.desc()
        ).all()
        
        if not top_comments:
            return success_response("No comments yet for this post", data={
                "comments": []
            })

        comment_ids = [c.id for c in top_comments]

        # 2️⃣ Get direct replies (depth_level = 1 ONLY)
        all_replies = Comment.query.filter(
            Comment.parent_id.in_(comment_ids),
            Comment.is_deleted == False,
            Comment.depth_level == 1  # ✅ ENFORCE: Only level 1 replies
        ).order_by(
            Comment.parent_id,
            Comment.posted_at.asc()
        ).all()

        # 3️⃣ Map replies to parent comments
        reply_map = defaultdict(list)
        for r in all_replies:
            reply_map[r.parent_id].append(r)

        # 4️⃣ Build final response
        comments_data = []
        for c in top_comments:
            comment_author = User.query.get(c.student_id)
            
            comment_liked = CommentLike.query.filter_by(
                student_id=current_user.id,
                comment_id=c.id
            ).first() is not None
            
            comment_marked_helpful = CommentHelpfulMark.query.filter_by(
                user_id=current_user.id, 
                comment_id=c.id
            ).first() is not None

            replies_data = []
            for r in reply_map.get(c.id, []):
                reply_author = User.query.get(r.student_id)
                
                reply_liked = CommentLike.query.filter_by(
                    student_id=current_user.id,
                    comment_id=r.id
                ).first() is not None
                
                reply_marked_helpful = CommentHelpfulMark.query.filter_by(
                    user_id=current_user.id, 
                    comment_id=r.id
                ).first() is not None
                
                replies_data.append({
                    "id": r.id,
                    "text_content": r.text_content,
                    "likes_count": r.likes_count,
                    "post_is_solved": post_is_solved,
                    "is_author": post.student_id == r.student_id,
                    "replies_count": r.replies_count,
                    "helpful_count": r.helpful_count,
                    "resources": r.resources,
                    "is_you": reply_author.id == current_user.id,
                    "post_id": r.post_id,
                    "is_solution": r.is_solution,
                    "depth_level": r.depth_level,
                    "parent_id": r.parent_id,
                    "posted_at": r.posted_at.isoformat(),
                    "can_reply": False,  # ✅ Level 1 comments CANNOT receive replies
                    "author": {
                        "id": reply_author.id,
                        "name": reply_author.name,
                        "username": reply_author.username,
                        "avatar": reply_author.avatar
                    },
                    "user_interactions": {
                        "has_liked": reply_liked,
                        "has_marked_helpful": reply_marked_helpful,
                        "is_author": r.student_id == current_user.id
                    }
                })

            comments_data.append({
                "id": c.id,
                "text_content": c.text_content,
                "post_id": c.post_id,
                "likes_count": c.likes_count,
                "replies_count": c.replies_count,
                "helpful_count": c.helpful_count,
                "resources": c.resources,
                "is_solution": c.is_solution,
                "post_is_solved": post_is_solved,
                "is_author": post.student_id == c.student_id,

                "depth_level": c.depth_level,
                "is_you": comment_author.id == current_user.id,
                "posted_at": c.posted_at.isoformat(),
                "can_reply": True,  # ✅ Level 0 comments CAN receive replies
                "author": {
                    "id": comment_author.id,
                    "name": comment_author.name,
                    "username": comment_author.username,
                    "avatar": comment_author.avatar
                },
                "user_interactions": {
                    "has_liked": comment_liked,
                    "has_marked_helpful": comment_marked_helpful,
                    "is_author": c.student_id == current_user.id
                },
                "replies": replies_data
            })

        return jsonify({
            "status": "success",
            "data": {
                "comments": comments_data
            }
        })

    except Exception as e:
        current_app.logger.error(f"Comments load error: ", exc_info=True)
        return jsonify({
            "status": "error",
            "message": "Failed to load comments"
        }), 500


@posts_bp.route("/comments/<int:comment_id>/replies", methods=["GET"])
@token_required
def comment_replies(current_user, comment_id):
    """
    Get replies for a specific comment
    ✅ ONLY WORKS FOR LEVEL 0 COMMENTS (top-level)
    ✅ RETURNS LEVEL 1 REPLIES ONLY (no nested replies)
    """
    try:
        parent_comment = Comment.query.get(comment_id)
        if not parent_comment:
            return error_response("Comment not found", 404)

        # ✅ BLOCK: If this is already a level 1 comment, it has no replies
        if parent_comment.depth_level >= 1:
            return error_response(
                "This comment cannot have replies (maximum depth reached)",
                400
            )

        # Fetch only direct replies (depth_level = 1)
        replies = Comment.query.filter_by(
            parent_id=comment_id,
            is_deleted=False,
            depth_level=1  # ✅ ENFORCE: Only level 1 replies
        ).order_by(
            Comment.is_solution.desc(),
            Comment.likes_count.desc(),
            Comment.posted_at.desc()
        ).all()

        if not replies:
            return success_response("No replies found", data={
                "replies": [],
                "parent_comment": {
                    "id": parent_comment.id,
                    "depth_level": parent_comment.depth_level
                }
            })

        replies_data = []
        for reply in replies:
            reply_author = User.query.get(reply.student_id)

            reply_liked = CommentLike.query.filter_by(
                student_id=current_user.id,
                comment_id=reply.id
            ).first() is not None

            replies_data.append({
                "id": reply.id,
                "text_content": reply.text_content,
                "likes_count": reply.likes_count,
                "replies_count": 0,  # ✅ Always 0 (level 1 comments cannot have replies)
                "is_you": reply.student_id == current_user.id,
                "post_id": reply.post_id,
                "helpful_count": reply.helpful_count,
                "resources": reply.resources,
                "is_solution": reply.is_solution,
                "depth_level": reply.depth_level,
                "parent_id": reply.parent_id,
                "posted_at": reply.posted_at.isoformat(),
                "can_reply": False,  # ✅ Level 1 comments CANNOT receive replies
                "author": {
                    "id": reply_author.id,
                    "name": reply_author.name,
                    "username": reply_author.username,
                    "avatar": reply_author.avatar
                },
                "user_interactions": {
                    "liked": reply_liked,
                    "is_author": reply.student_id == current_user.id
                }
            })

        return jsonify({
            "status": "success",
            "data": {
                "replies": replies_data,
                "parent_comment": {
                    "id": parent_comment.id,
                    "depth_level": parent_comment.depth_level,
                    "can_receive_replies": True  # ✅ Only level 0 can receive replies
                }
            }
        })

    except Exception as e:
        current_app.logger.error(f"Replies load error: ", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to load replies"}), 500