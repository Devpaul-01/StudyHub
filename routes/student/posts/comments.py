"""
StudyHub - Posts: Comments and replies

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

posts_comments_bp = Blueprint("posts_comments", __name__)
@posts_comments_bp.route("/posts/<int:post_id>/unmark-solution", methods=["POST"])
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
   

@posts_comments_bp.route("/posts/<int:post_id>/mark-solution", methods=["POST"])
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

@posts_comments_bp.route("/comments/<int:comment_id>/like", methods=["POST"])
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

@posts_comments_bp.route("/comments/<int:comment_id>/mark-helpful", methods=["POST"])
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

    

@posts_comments_bp.route("/comments/<int:comment_id>", methods=["PATCH"])
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


@posts_comments_bp.route("/comments/<int:comment_id>", methods=["DELETE"])
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

@posts_comments_bp.route("/comments/create", methods=["POST"])
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


@posts_comments_bp.route("/posts/<int:post_id>/comments", methods=["GET"])
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


@posts_comments_bp.route("/comments/<int:comment_id>/replies", methods=["GET"])
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
