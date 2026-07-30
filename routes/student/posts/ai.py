"""
StudyHub - Posts: AI (ask Learnora about a post, apply refinement)

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

posts_ai_bp = Blueprint("posts_ai", __name__)
@posts_ai_bp.route("/posts/<int:post_id>/ask-learnora", methods=["POST", "GET"])
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
 
        # Document 1 §2.4: use the consolidated call_ai_response() instead of
        # hand-rolling a provider-rotation/retry loop against
        # StudyAssistant.stream_response() directly — this is one of the four
        # duplicated call sites that consolidation was meant to replace.
        from services.ai_provider_service import call_ai_response

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

        answer, diagnostics = call_ai_response(
            messages,
            needs_vision=False,
            call_type="post_question",
        )

        if not answer:
            current_app.logger.warning(f"Ask Learnora about post failed: {diagnostics}")
            return error_response("Failed to get a response from the AI service", 503)

        return jsonify({
            "status": "success",
            "data": {
                "post_id": post.id,
                "question": question,
                "answer": answer
            }
        })
 
    except Exception as e:
        current_app.logger.error("Ask Learnora about post error: ", exc_info=True)
        return error_response("Failed to get AI response about this post")
 
# ─────────────────────────────────────────────────────────────────────────────
# HELPER: Build a post dict (shared between feed and tag endpoints)
# ─────────────────────────────────────────────────────────────────────────────

@posts_ai_bp.route("/posts/<int:post_id>/apply-refinement", methods=["PATCH"])
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
        

