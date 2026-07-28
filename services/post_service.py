"""
services/post_service.py

Document 1 §2.3 / Document 2 §3.10. Pure/business-logic helper functions
extracted from posts.py's "HELPER FUNCTIONS" section — mentions detection,
spam-check, activity tracking, and reaction-count bookkeeping. These have
no Flask/route dependency and are reused (or reusable) beyond a single
route body, which per Document 2 §4's decision test puts them squarely in
the service layer.

Moved verbatim (no behavior change) from posts.py:
    - extract_public_id
    - update_post_reaction_count
    - detect_and_create_mentions
    - check_spam
    - update_user_activity
    - check_helpful_milestones (moved in the Phase 1 remediation pass, now
      that services/badge_service.py exists — it previously stayed in
      posts.py because moving it here would have made this service import
      from routes/*, violating Document 2 §2's dependency rule. Now calls
      badge_service.check_and_award_badge(...) instead of
      routes.student.badges.check_and_award_badge(...).)

Per Document 2 §5's transaction-boundary convention: these functions
mutate the session (db.session.add / attribute assignment) but do NOT
call db.session.commit() — that stays the calling route's responsibility,
same as their original behavior in posts.py.

Per Document 2 §2's layering rule: no Flask imports, no `request`/`session`/`g`.
"""

import re
import datetime

from models import User, Post, Comment, Mention, Notification, UserActivity, PostReaction
from extensions import db
from services import badge_service


def extract_public_id(url):
    """Extract a Cloudinary public_id from a delivery URL."""
    # remove query params
    url = url.split("?")[0]

    # remove extension (.jpg, .png, .mp4, etc)
    public_id = re.sub(r'\.[^.]+$', '', url)

    # get everything after /upload/v123456/
    public_id = re.split(r'/upload/v\d+/', public_id)[-1]

    return public_id


def update_post_reaction_count(post, reaction_type, delta):
    """Update denormalized reaction counts on post"""
    if reaction_type in ["like", "love", "helpful", "insightful", "fire", "wow", "celebrate"]:
        post.positive_reactions_count = max(0, post.positive_reactions_count + delta)
    if reaction_type == "helpful":
        post.helpful_count += 1


def detect_and_create_mentions(text_content, created_by_id, content_type, content_id):
    """
    Detect @username mentions in text and create Mention records
    Also creates notifications for mentioned users

    Args:
        text_content: Text to scan for mentions
        created_by_id: ID of user who created the content
        content_type: "post", "comment", or "thread_message"
        content_id: ID of the content (post_id, comment_id, etc)
    """
    if not text_content:
        return []

    # Regex pattern to match @username (alphanumeric + underscore)
    mention_pattern = r'@([a-zA-Z0-9_]{3,20})'
    matches = re.finditer(mention_pattern, text_content)

    mentioned_users = []
    creator = User.query.get(created_by_id)

    for match in matches:
        username = match.group(1).lower()

        # Find user
        mentioned_user = User.query.filter_by(username=username).first()

        if mentioned_user and mentioned_user.id != created_by_id:
            # Check if mention already exists (prevent duplicates)
            existing_mention = Mention.query.filter_by(
                mentioned_in_type=content_type,
                mentioned_in_id=content_id,
                mentioned_user_id=mentioned_user.id,
                mentioned_by_user_id=created_by_id
            ).first()

            if not existing_mention:
                # Create mention record
                mention = Mention(
                    mentioned_in_type=content_type,
                    mentioned_in_id=content_id,
                    mentioned_user_id=mentioned_user.id,
                    mentioned_by_user_id=created_by_id
                )
                db.session.add(mention)

                # Create notification
                content_link = f"{content_type}/{content_id}"
                notification = Notification(
                    user_id=mentioned_user.id,
                    title=f"{creator.name} mentioned you",
                    body=f"{creator.name} mentioned you in a {content_type}",
                    notification_type="mention",
                    related_type=content_type,
                    related_id=content_id
                )
                db.session.add(notification)

                mentioned_users.append(mentioned_user.id)

    return mentioned_users


def check_spam(user_id, content_type="post"):
    """
    Simple spam detection - rate limiting

    Returns: (is_spam: bool, reason: str)
    """
    now = datetime.datetime.utcnow()
    hour_ago = now - datetime.timedelta(hours=1)

    # Check posts in last hour
    if content_type == "post":
        recent_posts = Post.query.filter(
            Post.student_id == user_id,
            Post.posted_at >= hour_ago
        ).count()

        if recent_posts >= 10:  # Max 10 posts per hour
            return True, "Too many posts in short time"

    # Check comments in last hour
    elif content_type == "comment":
        recent_comments = Comment.query.filter(
            Comment.student_id == user_id,
            Comment.posted_at >= hour_ago
        ).count()

        if recent_comments >= 30:  # Max 30 comments per hour
            return True, "Too many comments in short time"

    return False, None


def update_user_activity(user_id, activity_type):
    """
    Update or create daily activity record for user
    Used for activity heatmap and streak tracking
    """
    today = datetime.date.today()

    activity = UserActivity.query.filter_by(
        user_id=user_id,
        activity_date=today
    ).first()

    if not activity:
        activity = UserActivity(
            user_id=user_id,
            activity_date=today,
            posts_created=0,
            comments_created=0,
            threads_joined=0,
            messages_sent=0,
            helpful_count=0,
            activity_score=0
        )
        db.session.add(activity)

    # Increment counters (now safe because we initialized them)
    if activity_type == "post":
        activity.posts_created = (activity.posts_created or 0) + 1
        activity.activity_score = (activity.activity_score or 0) + 5
    elif activity_type == "comment":
        activity.comments_created = (activity.comments_created or 0) + 1
        activity.activity_score = (activity.activity_score or 0) + 2

    return activity


def check_helpful_milestones(user_id):
    """
    Check if user reached helpful count milestones and award the
    corresponding badge if so.

    Moved from posts.py in the Phase 1 remediation pass now that
    services/badge_service.py exists — calls badge_service directly
    instead of importing routes.student.badges.
    """
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
        badge_service.check_and_award_badge(user_id, "Helpful Contributor")
    elif helpful_count == 50:
        badge_service.check_and_award_badge(user_id, "Helpful Hero")
