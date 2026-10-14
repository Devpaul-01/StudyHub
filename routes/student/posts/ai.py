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
    
    detect_and_create_mentions,
    check_spam,
    update_user_activity,
)
# Phase 5b (Document 4 §1): AI_EXPENSIVE for both routes here — ask-learnora
# is a real AI-provider call; apply-refinement persists AI-refined content
# generated from a prior AI call in the same user flow, so it's grouped
# with the AI tier rather than a plain WRITE_HEAVY edit.
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key

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
@limiter.limit(RateLimitTier.AI_EXPENSIVE, key_func=user_or_ip_key)
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
# AI REFINE (Batch 1, item: refinePostStream 404 fix)
# ─────────────────────────────────────────────────────────────────────────────
#
# static/js/feed/feed.api.js's refinePostStream() has always called this
# path (POST /posts/<id>/refine), but no matching route existed anywhere in
# the backend, so refining a post's title/content with AI always 404'd.
# This route exists to generate the refined {title, content} pair that
# apply-refinement (right below) then persists once the user approves it —
# the two routes are a matched pair: this one proposes, that one applies.
#
# static/js/feed/feed.modals.js's startRefinement() reads the response as a
# Server-Sent Events stream with a specific, already-hardcoded contract:
#   data: {"type": "start"}\n\n
#   data: {"content": "<fragment of the raw AI text, possibly many chunks>"}\n\n   (repeated)
#   data: {"type": "done", "success": true, "refined": {"title": "...", "content": "..."}}\n\n
#   data: {"error": "..."}\n\n   (on failure, in place of the chunks above)
# This route is built to produce exactly that, reusing the same
# StudyAssistant / provider_manager primitives as the Learnora chat
# endpoint (routes/student/learnora/__init__.py), rather than hand-rolling
# a second AI call path. Unlike that chat endpoint, this is a single
# one-shot generation with no conversation history to persist and no
# multi-provider rotation loop — if the first provider fails, the error is
# surfaced to the user (who can just hit "refine" again) rather than
# silently retried across every configured provider.
def _extract_refined_json(text):
    """
    Pull a {"title": ..., "content": ...} object out of an AI response.

    refine_post()'s system prompt asks for ONLY a JSON object, but models
    occasionally comply loosely anyway — a markdown fence, a line of
    commentary, or an unrelated '{' earlier in the reply. A single greedy
    regex from the first '{' to the last '}' breaks on that last case (it
    spans across the earlier, unrelated brace). This instead tries the
    whole response first, then every individually-balanced {...} block in
    it, and returns the first one that actually parses with both keys
    present. Returns None if nothing in the text qualifies.
    """
    candidates = [text]
    starts = [m.start() for m in re.finditer(r"\{", text)]
    for start in starts:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict) and "title" in parsed and "content" in parsed:
            return {"title": str(parsed["title"]), "content": str(parsed["content"])}
    return None


@posts_ai_bp.route("/posts/<int:post_id>/refine", methods=["POST"])
@limiter.limit(RateLimitTier.AI_EXPENSIVE, key_func=user_or_ip_key)
@token_required
def refine_post(current_user, post_id):
    """
    Stream an AI-refined {title, content} pair for a post, for the user to
    review and (via apply-refinement) approve.

    Body (optional JSON): { "instructions": "make it more concise" }
    """
    post = Post.query.get(post_id)
    if not post:
        return error_response("Post not found", 404)

    if post.student_id != current_user.id:
        return error_response("Only the post author can refine this post", 403)

    data = request.get_json(silent=True) or {}
    instructions = (data.get("instructions") or "").strip()

    from services.ai_provider_service import StudyAssistant, provider_manager

    system_prompt = (
        "You are Learnora, a helpful study assistant. You improve the "
        "clarity, grammar, and structure of a student's post without "
        "changing its meaning or the facts/claims it makes. "
        "Respond with ONLY a single JSON object of the exact shape "
        '{"title": "<refined title>", "content": "<refined body>"} '
        "and nothing else — no markdown fences, no commentary before or "
        "after it."
    )
    user_prompt = (
        f'Original title: "{post.title}"\n\n'
        f"Original content:\n{post.text_content or '[No content]'}\n\n"
        + (f"Additional instructions from the author: {instructions}\n\n" if instructions else "")
        + "Return the refined {\"title\": ..., \"content\": ...} JSON object now."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    def generate():
        full_response = ""
        stream_failed = False

        yield f"data: {json.dumps({'type': 'start'})}\n\n"

        try:
            provider = provider_manager.get_working_provider(needs_vision=False)
            if not provider:
                yield f"data: {json.dumps({'error': 'No AI provider is currently available'})}\n\n"
                return

            assistant = StudyAssistant(provider)
            assistant.select_model(has_images=False)

            for chunk in assistant.stream_response(messages, has_images=False):
                # stream_response() already yields fully-formed SSE lines
                # ("data: {...}\n\n") whose JSON body uses a 'content' key
                # for each text fragment — exactly the shape
                # feed.modals.js's startRefinement() already parses, so
                # these are relayed to the client as-is.
                yield chunk

                if chunk.startswith("data: "):
                    try:
                        parsed_chunk = json.loads(chunk[6:])
                    except Exception:
                        continue
                    if "content" in parsed_chunk:
                        full_response += parsed_chunk["content"]
                    elif "error" in parsed_chunk:
                        stream_failed = True

        except Exception as e:
            current_app.logger.error("Refine post stream error: ", exc_info=True)
            yield f"data: {json.dumps({'error': 'Failed to generate a refinement'})}\n\n"
            return

        if stream_failed or not full_response.strip():
            yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
            return

        from services.ai_provider_service import clean_ai_response
        cleaned = clean_ai_response(full_response)
        refined = _extract_refined_json(cleaned)

        if not refined:
            yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'done', 'success': True, 'refined': refined})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: Build a post dict (shared between feed and tag endpoints)
# ─────────────────────────────────────────────────────────────────────────────

@posts_ai_bp.route("/posts/<int:post_id>/apply-refinement", methods=["PATCH"])
@limiter.limit(RateLimitTier.AI_EXPENSIVE, key_func=user_or_ip_key)
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
        

