"""
StudyHub - Posts: Bookmarks

Split from posts.py per Document 1 (Architecture Refactor) §2.3 as part of
Phase 2 (God-file splitting). This is a pure move — function bodies,
decorators, routes, and logic are unchanged from the original posts.py.
See routes/student/posts/__init__.py for the sub-blueprint aggregation
that re-exposes all routes under the same paths as before.

Per Document 1 §2.3: this file is a VERBATIM copy-paste move only —
no logic changes, no error-handling changes, no folder/tag audit
fixes bundled in. Bookmark functionality is explicitly excluded from
this refactor phase per the project instructions.
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
    
    detect_and_create_mentions,
    check_spam,
    update_user_activity,
)
# Phase 5b (Document 4 §1): rate limiting is infrastructure, not bookmark
# business logic, so it's applied here despite this file's own "verbatim
# move only, no logic changes" scope note (Document 1 §2.3) — that note
# covers bookmark behavior/error-handling, not cross-cutting HTTP-layer
# concerns like rate limits. Toggle/bulk/single-bookmark -> BURST_OK
# (low-risk, frequent); bookmarked-list GET -> PUBLIC_READ.
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key, ip_key

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

posts_bookmarks_bp = Blueprint("posts_bookmarks", __name__)
@posts_bookmarks_bp.route("/posts/bookmark/toggle", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
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
        
        

        

        
        

@posts_bookmarks_bp.route("/posts/bulk/bookmark", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
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

@posts_bookmarks_bp.route("/posts/<int:post_id>/bookmark", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
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

@posts_bookmarks_bp.route("/posts/bookmarked", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
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

