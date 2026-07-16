"""
StudyHub - Enhanced Messaging System
WhatsApp/Messenger-level features with polling support

Features:
- Connection-based messaging (must be connected to message)
- Rich messages (text, files, code snippets, reactions)
- Read receipts and typing indicators
- Reply to specific messages
- Message actions (delete, forward, star)
- Conversation management (archive, mute, pin)
- Search and export
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import or_, and_, func, desc
from werkzeug.utils import secure_filename
import cloudinary.uploader
import uuid
import datetime
import os

from models import (
    User, Message, Connection, Notification, ThreadMember,
    MessageReaction
    
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response,
    save_file, ALLOWED_IMAGE_EXT, ALLOWED_DOCUMENT_EXT,
    get_reaction_emoji, get_reaction_summary
)

messages_bp = Blueprint("student_messages", __name__)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
def _utc_iso(dt):
    """Return ISO string guaranteed to end with Z so browsers parse it as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + 'Z'
    return dt.isoformat().replace('+00:00', 'Z')
def is_blocked_check(current_user_id, partner_id):
  """Returns (is_blocked_by_me, blocked_by_partner)"""
  from sqlalchemy import or_, and_
  block_by_me = Connection.query.filter(
    Connection.requester_id == current_user_id,
    Connection.receiver_id == partner_id,
    Connection.status == 'blocked'
  ).first()
  
  block_by_them = Connection.query.filter(
    Connection.requester_id == partner_id,
    Connection.receiver_id == current_user_id,
    Connection.status == 'blocked'
  ).first()
  
  return bool(block_by_me), bool(block_by_them)

def can_message(sender_id, receiver_id):
    """
    Check if sender can message receiver
    
    Rules:
    1. Must have accepted connection, OR
    2. System message exception
    
    Note: Thread members CANNOT DM - must connect first
    """
    if sender_id == receiver_id:
        return False
    
    # Check for accepted connection
    connection = Connection.query.filter(
        or_(
            and_(Connection.requester_id == sender_id, Connection.receiver_id == receiver_id),
            and_(Connection.requester_id == receiver_id, Connection.receiver_id == sender_id)
        ),
        Connection.status == "accepted"
    ).first()
    
    if connection:
        return True
    
    return False


def get_conversation_partner(conversation, current_user_id):
    """Get the other user in a conversation"""
    if conversation.get("user1_id") == current_user_id:
        return User.query.get(conversation.get("user2_id"))
    return User.query.get(conversation.get("user1_id"))


def create_conversation_key(user1_id, user2_id):
    sorted_ids = sorted([user1_id, user2_id])
    return f"{sorted_ids[0]}-{sorted_ids[1]}"


def _visible_to_me(msg, current_user_id):
    """Return True if the message should be visible to the given user."""
    if msg.is_deleted:
        return False
    if msg.sender_id == current_user_id and msg.deleted_by_sender:
        return False
    if msg.receiver_id == current_user_id and msg.deleted_by_receiver:
        return False
    return True

@messages_bp.route("/messages/resources/upload", methods=["POST"])
@token_required
def upload_message_resource(current_user):
    """
    Upload a resource to Cloudinary
    
    Request: multipart/form-data with 'file' field
    Response: {id, url, type, filename, size, cloudinary_public_id}
    """
    try:
        if 'file' not in request.files:
            return error_response("No file provided", 400)
        
        file = request.files['file']
        
        if file.filename == '':
            return error_response("No file selected", 400)
        
        # Validate file size (50MB max)
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > 50 * 1024 * 1024:  # 50MB
            return error_response("File too large (max 50MB)", 413)
        
        # Determine resource type
        filename = secure_filename(file.filename)
        file_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
        
        if file_ext in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
            resource_type = 'image'
        elif file_ext in ['mp4', 'mov', 'avi', 'webm']:
            resource_type = 'video'
        elif file_ext in ['mp3', 'wav', 'ogg', 'webm']:
            resource_type = 'audio'
        elif file_ext in ['pdf', 'doc', 'docx', 'txt', 'ppt', 'pptx']:
            resource_type = 'document'
        else:
            resource_type = 'file'
        
        # Upload to Cloudinary
        upload_result = cloudinary.uploader.upload(
            file,
            folder=f"messages/{current_user.id}",
            resource_type='auto'
        )
        
        # Generate unique ID
        resource_id = f"res_{uuid.uuid4().hex[:12]}"
        
        return success_response(
            "Resource uploaded successfully",
            data={
                "id": resource_id,
                "url": upload_result['secure_url'],
                "type": resource_type,
                "filename": filename,
                "size": file_size,
                "cloudinary_public_id": upload_result['public_id']
            }
        ), 201
        
    except Exception as e:
        current_app.logger.error(f"Resource upload error: {str(e)}")
        return error_response("Failed to upload resource", 500)





@messages_bp.route("/messages/analytics/<int:partner_id>", methods=["GET"])
@token_required
def get_conversation_analytics(current_user, partner_id):
    """
    Get analytics for conversation with a specific user
    
    Returns study patterns, subject breakdown, response times, etc.
    """
    try:
        if not can_message(current_user.id, partner_id):
            return error_response("Must be connected to view analytics", 403)
        
        # Create conversation key
        sorted_ids = sorted([current_user.id, partner_id])
        conv_key = f"{sorted_ids[0]}-{sorted_ids[1]}"
        
        # Get or compute analytics
        analytics = ConversationAnalytics.query.filter_by(
            conversation_key=conv_key
        ).first()
        
        if not analytics:
            # Trigger initial computation
            from routes.student.message_ai_helpers import update_conversation_analytics
            first_message = Message.query.filter(
                or_(
                    and_(Message.sender_id == current_user.id, Message.receiver_id == partner_id),
                    and_(Message.sender_id == partner_id, Message.receiver_id == current_user.id)
                )
            ).order_by(Message.sent_at.asc()).first()
            
            if first_message:
                update_conversation_analytics(current_user.id, partner_id, first_message)
                analytics = ConversationAnalytics.query.filter_by(conversation_key=conv_key).first()
        
        if not analytics:
            return jsonify({
                "status": "success",
                "data": {
                    "has_analytics": False,
                    "message": "No conversation history yet"
                }
            })
        
        # Get study sessions count
        study_sessions = LiveStudySession.query.filter(
            or_(
                and_(LiveStudySession.user1_id == current_user.id, LiveStudySession.user2_id == partner_id),
                and_(LiveStudySession.user1_id == partner_id, LiveStudySession.user2_id == current_user.id)
            )
        ).count()
        
        # Get partner info
        partner = User.query.get(partner_id)
        
        return jsonify({
            "status": "success",
            "data": {
                "has_analytics": True,
                "partner": {
                    "id": partner.id,
                    "username": partner.username,
                    "name": partner.name,
                    "avatar": partner.avatar
                } if partner else None,
                "stats": {
                    "total_messages": analytics.total_messages,
                    "messages_this_week": analytics.messages_this_week,
                    "messages_last_week": analytics.messages_last_week,
                    "trend": "up" if analytics.messages_this_week > analytics.messages_last_week else "down" if analytics.messages_this_week < analytics.messages_last_week else "stable"
                },
                "subjects": {
                    "top_subjects": analytics.top_subjects or [],
                    "all_subjects": analytics.subjects_discussed or {},
                    "most_discussed": analytics.top_subjects[0] if analytics.top_subjects else None
                },
                "timeline": {
                    "first_message": analytics.first_message_at.isoformat() if analytics.first_message_at else None,
                    "last_message": analytics.last_message_at.isoformat() if analytics.last_message_at else None,
                    "days_active": (analytics.last_message_at - analytics.first_message_at).days if analytics.first_message_at and analytics.last_message_at else 0,
                    "most_active_day": analytics.most_active_day,
                    "most_active_hour": analytics.most_active_hour
                },
                "study_sessions": {
                    "total_sessions": study_sessions,
                    "total_hours": analytics.total_study_time_hours
                },
                "engagement": {
                    "engagement_score": round(analytics.engagement_score, 2),
                    "learning_score": round(analytics.learning_score, 2),
                    "avg_response_time_minutes": round(analytics.avg_response_time_minutes, 1)
                },
                "last_updated": analytics.last_computed_at.isoformat()
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get analytics error: {str(e)}")
        return error_response("Failed to load analytics")

@messages_bp.route("/messages/analytics/summary", methods=["GET"])
@token_required
def get_all_analytics_summary(current_user):
    """
    Get summary analytics across ALL conversations
    Shows user's overall study patterns
    """
    try:
        # Get all analytics where user is involved
        all_analytics = ConversationAnalytics.query.filter(
            or_(
                ConversationAnalytics.user1_id == current_user.id,
                ConversationAnalytics.user2_id == current_user.id
            )
        ).all()
        
        if not all_analytics:
            return jsonify({
                "status": "success",
                "data": {
                    "has_data": False,
                    "message": "No conversation analytics yet"
                }
            })
        
        # Aggregate data
        total_messages = sum(a.total_messages for a in all_analytics)
        total_study_hours = sum(a.total_study_time_hours for a in all_analytics)
        
        # Aggregate subjects
        all_subjects = {}
        for analytics in all_analytics:
            if analytics.subjects_discussed:
                for subject, count in analytics.subjects_discussed.items():
                    all_subjects[subject] = all_subjects.get(subject, 0) + count
        
        top_subjects = sorted(all_subjects.items(), key=lambda x: x[1], reverse=True)[:10]
        
        # Get most active conversations
        most_active = sorted(all_analytics, key=lambda x: x.total_messages, reverse=True)[:5]
        most_active_data = []
        
        for analytics in most_active:
            other_user_id = analytics.user2_id if analytics.user1_id == current_user.id else analytics.user1_id
            other_user = User.query.get(other_user_id)
            
            if other_user:
                most_active_data.append({
                    "user": {
                        "id": other_user.id,
                        "username": other_user.username,
                        "name": other_user.name,
                        "avatar": other_user.avatar
                    },
                    "total_messages": analytics.total_messages,
                    "top_subject": analytics.top_subjects[0] if analytics.top_subjects else None
                })
        
        return jsonify({
            "status": "success",
            "data": {
                "has_data": True,
                "overview": {
                    "total_conversations": len(all_analytics),
                    "total_messages": total_messages,
                    "total_study_hours": round(total_study_hours, 1),
                    "avg_messages_per_conversation": round(total_messages / len(all_analytics), 1)
                },
                "subjects": {
                    "top_subjects": [{"subject": s[0], "count": s[1]} for s in top_subjects],
                    "total_subjects": len(all_subjects)
                },
                "most_active_conversations": most_active_data
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get summary analytics error: {str(e)}")
        return error_response("Failed to load analytics summary")



# ============================================================================
# HOMEWORK QUEUE
# ============================================================================

@messages_bp.route("/messages/shared-media/<int:partner_id>", methods=["GET"])
@token_required
def get_shared_media(current_user, partner_id):
    """
    Get all shared media (images, videos, files, links) between current user and partner
    
    Returns media organized by type:
    - images: All image files
    - videos: All video files
    - documents: PDFs, docs, etc.
    - links: External links shared
    - all: Everything chronologically
    
    Query params:
    - type: Filter by media type (images|videos|documents|links|all) - default: all
    - limit: Max items to return (default: 50, max: 200)
    - page: Page number for pagination
    """
    try:
        # Verify users are connected
        if not can_message(current_user.id, partner_id):
            return error_response("You must be connected to view shared media", 403)
        
        # Get filter parameters
        media_type = request.args.get("type", "all").lower()
        limit = min(int(request.args.get("limit", 50)), 200)
        page = int(request.args.get("page", 1))
        
        # Get all messages with media between these users
        messages_query = Message.query.filter(
            or_(
                and_(Message.sender_id == current_user.id, Message.receiver_id == partner_id),
                and_(Message.sender_id == partner_id, Message.receiver_id == current_user.id)
            ),
            Message.deleted_by_sender == False,
            Message.deleted_by_receiver == False,
            Message.is_deleted==False,
            Message.resources.isnot(None)
        ).order_by(Message.sent_at.desc())
        
        # Paginate
        paginated = messages_query.paginate(page=page, per_page=limit, error_out=False)
        
        # Process media by type
        images = []
        videos = []
        documents = []
        links = []
        all_media = []
        
        for message in paginated.items:
            if not message.resources:
                continue
            
            sender = User.query.get(message.sender_id)
            
            message_meta = {
                "message_id": message.id,
                "sent_at": message.sent_at.isoformat(),
                "from_me": message.sender_id == current_user.id,
                "sender": {
                    "id": sender.id,
                    "name": sender.name,
                    "username": sender.username,
                    "avatar": sender.avatar
                } if sender else None
            }
            
            # Process each resource in the message
            for resource in message.resources:
                # Resource can be a string (URL) or dict with metadata
                if isinstance(resource, str):
                    resource_url = resource
                    resource_type = detect_media_type(resource_url)
                    resource_name = resource_url.split('/')[-1]
                    resource_size = None
                elif isinstance(resource, dict):
                    resource_url = resource.get("url")
                    resource_type = resource.get("type", detect_media_type(resource_url))
                    resource_name = resource.get("name", resource_url.split('/')[-1] if resource_url else "Unknown")
                    resource_size = resource.get("size")
                else:
                    continue
                
                if not resource_url:
                    continue
                
                media_item = {
                    "url": resource_url,
                    "type": resource_type,
                    "name": resource_name,
                    "size": resource_size,
                    **message_meta
                }
                
                # Categorize
                if resource_type in ["image/jpeg", "image/png", "image/gif", "image/webp", "image"]:
                    images.append(media_item)
                elif resource_type in ["video/mp4", "video/webm", "video/quicktime", "video"]:
                    videos.append(media_item)
                elif resource_type in ["application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document"]:
                    documents.append(media_item)
                elif resource_type == "link":
                    links.append(media_item)
                
                all_media.append(media_item)
        
        # Filter by requested type
        if media_type == "images":
            filtered_media = images
        elif media_type == "videos":
            filtered_media = videos
        elif media_type == "documents":
            filtered_media = documents
        elif media_type == "links":
            filtered_media = links
        else:  # all
            filtered_media = all_media
        
        # Get partner info
        partner = User.query.get(partner_id)
        
        return jsonify({
            "status": "success",
            "data": {
                "partner": {
                    "id": partner.id,
                    "username": partner.username,
                    "name": partner.name,
                    "avatar": partner.avatar
                } if partner else None,
                "media": filtered_media,
                "counts": {
                    "total": len(all_media),
                    "images": len(images),
                    "videos": len(videos),
                    "documents": len(documents),
                    "links": len(links)
                },
                "pagination": {
                    "page": page,
                    "per_page": limit,
                    "total": paginated.total,
                    "pages": paginated.pages,
                    "has_next": paginated.has_next,
                    "has_prev": paginated.has_prev
                },
                "filter": {
                    "type": media_type,
                    "showing": len(filtered_media)
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get shared media error: {str(e)}")
        return error_response("Failed to load shared media")


def detect_media_type(url):
    """
    Detect media type from URL extension or Cloudinary metadata
    
    Returns: image, video, document, or link
    """
    if not url:
        return "unknown"
    
    url_lower = url.lower()
    
    # Image extensions
    if any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '/image/']):
        return "image"
    
    # Video extensions
    if any(ext in url_lower for ext in ['.mp4', '.webm', '.mov', '.avi', '.mkv', '/video/']):
        return "video"
    
    # Document extensions
    if any(ext in url_lower for ext in ['.pdf', '.doc', '.docx', '.txt', '.xls', '.xlsx', '.ppt', '.pptx', '/raw/']):
        return "document"
    
    # Check if it's a Cloudinary URL (typically contains 'cloudinary.com')
    if 'cloudinary.com' in url_lower:
        if '/image/' in url_lower:
            return "image"
        elif '/video/' in url_lower:
            return "video"
        elif '/raw/' in url_lower:
            return "document"
    
    # Default to link for external URLs
    if url.startswith('http://') or url.startswith('https://'):
        return "link"
    
    return "unknown"

@messages_bp.route("/messages/shared-media/<int:partner_id>/count", methods=["GET"])
@token_required
def get_shared_media_count(current_user, partner_id):
    """
    Get count of shared media items by type
    Lightweight endpoint for displaying badges/counts
    """
    try:
        if not can_message(current_user.id, partner_id):
            return error_response("Not authorized", 403)
        
        # Get all messages with media
        messages_with_media = Message.query.filter(
            or_(
                and_(Message.sender_id == current_user.id, Message.receiver_id == partner_id),
                and_(Message.sender_id == partner_id, Message.receiver_id == current_user.id)
            ),
            Message.deleted_by_sender == False,
            Message.deleted_by_receiver == False,
            Message.resources.isnot(None)
        ).all()
        
        # Count by type
        images = 0
        videos = 0
        documents = 0
        links = 0
        total = 0
        
        for message in messages_with_media:
            if not message.resources:
                continue
            
            for resource in message.resources:
                resource_url = resource if isinstance(resource, str) else resource.get("url")
                if not resource_url:
                    continue
                
                media_type = detect_media_type(resource_url)
                total += 1
                
                if "image" in media_type:
                    images += 1
                elif "video" in media_type:
                    videos += 1
                elif "document" in media_type:
                    documents += 1
                else:
                    links += 1
        
        return jsonify({
            "status": "success",
            "data": {
                "counts": {
                    "total": total,
                    "images": images,
                    "videos": videos,
                    "documents": documents,
                    "links": links
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get media count error: {str(e)}")
        return error_response("Failed to get media count")
    
@messages_bp.route("/messages/<int:message_id>/delete-for-everyone", methods=["DELETE"])
@token_required
def delete_message_for_everyone(current_user, message_id):
    try:
        message = Message.query.get(message_id)
        if not message:
            return error_response("Message not found")
        if message.sender_id != current_user.id and message.receiver_id != current_user.id:
            return error_response("Unauthorized")
        if message.sender_id != current_user.id:
            return error_response("Unauthorized")
        
        message.is_deleted = True
        message.body = '[Message deleted]'
        
        db.session.commit()
        return success_response("Message deleted succesfully")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete message error: {str(e)}")
        return error_response("Failed to delete message")
    
@messages_bp.route("/messages/<int:message_id>/delete-for-me", methods=["DELETE"])
@token_required
def delete_message(current_user, message_id):
    try:
        message = Message.query.get(message_id)
        if not message:
            return error_response("Message not found")
        if message.sender_id != current_user.id and message.receiver_id != current_user.id:
            return error_response("Unauthorized")
        is_sender = message.sender_id == current_user.id
        if is_sender:
            message.deleted_by_sender = True
        else:
            message.deleted_by_receiver = True
        db.session.commit()
        return success_response("Message deleted succesfully")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete message error: {str(e)}")
        return error_response("Failed to delete message")
        
        
        
@messages_bp.route("/messages/clear/<int:partner_id>", methods=["DELETE"])
@token_required
def clear_conversation(current_user, partner_id):
    try:
        # Messages I sent to partner — mark deleted_by_sender
        Message.query.filter(
            Message.sender_id == current_user.id,
            Message.receiver_id == partner_id
        ).update({"deleted_by_sender": True}, synchronize_session=False)

        # Messages partner sent me — mark deleted_by_receiver
        Message.query.filter(
            Message.sender_id == partner_id,
            Message.receiver_id == current_user.id
        ).update({"deleted_by_receiver": True}, synchronize_session=False)

        db.session.commit()
        return success_response("Chat history cleared")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Clear chat error: {str(e)}")
        return error_response("Failed to clear chat")
@messages_bp.route("/messages/conversations", methods=["GET"])
@token_required
def get_conversations(current_user):
    """
    Get all conversations for current user.
    Shows list like WhatsApp with last message preview.
    Includes all connections, even those with no messages yet.
    Last message preview respects per-user soft-deletes — a message
    deleted for me never shows as the preview on my end.
    """
    try:
        # Get all accepted connections for current user
        connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()

        # Extract all connected user IDs
        connected_user_ids = set()
        for conn in connections:
            partner_id = conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
            connected_user_ids.add(partner_id)

        # Get all messages where user is sender or receiver
        messages_query = Message.query.filter(
            or_(
                Message.sender_id == current_user.id,
                Message.receiver_id == current_user.id
            )
        ).order_by(Message.sent_at.desc()).all()

        # Group by conversation partner
        conversations = {}

        for message in messages_query:
            partner_id = message.receiver_id if message.sender_id == current_user.id else message.sender_id

            if partner_id not in connected_user_ids:
                continue

            if partner_id not in conversations:
                conversations[partner_id] = {
                    "partner_id": partner_id,
                    "messages": [],
                    "unread_count": 0
                }

            conversations[partner_id]["messages"].append(message)

            # Count unread: messages TO current user that are unread and not deleted
            if (message.receiver_id == current_user.id
                    and not message.is_read
                    and not message.is_deleted
                    and not message.deleted_by_receiver):
                conversations[partner_id]["unread_count"] += 1

        # Add connections with no messages
        for partner_id in connected_user_ids:
            if partner_id not in conversations:
                conversations[partner_id] = {
                    "partner_id": partner_id,
                    "messages": [],
                    "unread_count": 0
                }

        # ── Batch pre-loads (eliminates N+1 queries) ─────────────────────────

        all_partner_ids = list(conversations.keys())

        # 1. Batch load all partner User objects in one query
        partner_users = {}
        if all_partner_ids:
            partner_users = {
                u.id: u for u in User.query.filter(User.id.in_(all_partner_ids)).all()
            }

        # 2. Batch load all blocked connections in one query (replaces 2-per-partner loop)
        blocked_by_me_set = set()
        blocked_by_partner_set = set()
        if all_partner_ids:
            blocked_connections = Connection.query.filter(
                or_(
                    and_(
                        Connection.requester_id == current_user.id,
                        Connection.receiver_id.in_(all_partner_ids),
                        Connection.status == 'blocked'
                    ),
                    and_(
                        Connection.requester_id.in_(all_partner_ids),
                        Connection.receiver_id == current_user.id,
                        Connection.status == 'blocked'
                    )
                )
            ).all()
            for bc in blocked_connections:
                if bc.requester_id == current_user.id:
                    blocked_by_me_set.add(bc.receiver_id)
                else:
                    blocked_by_partner_set.add(bc.requester_id)

        # 3. First pass: find the last visible message per partner
        last_msg_by_partner = {}
        for partner_id, conv_data in conversations.items():
            visible = [m for m in conv_data["messages"] if _visible_to_me(m, current_user.id)]
            if visible:
                last_msg_by_partner[partner_id] = max(visible, key=lambda m: m.sent_at)

        # 4. Batch load reactions for all last messages in one query
        last_msg_ids = [msg.id for msg in last_msg_by_partner.values()]
        reactions_by_msg = {}
        if last_msg_ids:
            all_last_reactions = MessageReaction.query.filter(
                MessageReaction.message_id.in_(last_msg_ids)
            ).all()
            for r in all_last_reactions:
                # Keep only the first reaction per message (preview only needs one)
                if r.message_id not in reactions_by_msg:
                    reactions_by_msg[r.message_id] = r

        # ── Format conversations ──────────────────────────────────────────────

        conversations_list = []

        for partner_id, conv_data in conversations.items():
            partner = partner_users.get(partner_id)
            if not partner:
                continue

            conversation_obj = {
                "partner": {
                    "id": partner.id,
                    "username": partner.username,
                    "name": partner.name,
                    "avatar": partner.avatar,
                    "last_active": partner.last_active.isoformat() if partner.last_active else None
                },
                "unread_count": conv_data["unread_count"],
            }

            conversation_obj['is_blocked_by_me']   = partner_id in blocked_by_me_set
            conversation_obj['blocked_by_partner'] = partner_id in blocked_by_partner_set

            last_message = last_msg_by_partner.get(partner_id)

            if last_message:
                # Build reaction data using pre-loaded reactions
                reaction_data = None
                message_reaction = reactions_by_msg.get(last_message.id)

                if message_reaction:
                    emoji = get_reaction_emoji(message_reaction.reaction_type)
                    reaction_summary = get_reaction_summary(last_message.id)
                    reaction_data = {
                        'message_id': last_message.id,
                        'user_id': current_user.id,
                        'reaction_type': message_reaction.reaction_type,
                        'emoji': emoji,
                        "reaction_text": reaction_summary,
                        'reacted_at': message_reaction.reacted_at.isoformat() if message_reaction.reacted_at else None
                    }

                # Generate preview text
                last_message_text = ""
                if last_message.body:
                    last_message_text = last_message.body[:100]
                elif last_message.resources:
                    first_resource = last_message.resources[0]
                    if isinstance(first_resource, dict):
                        last_message_text = first_resource.get('resource_type', 'Media')
                    else:
                        last_message_text = detect_media_type(first_resource)

                conversation_obj["last_message"] = {
                    "id": last_message.id,
                    "preview": last_message_text,
                    "is_typing": False,
                    "sent_at": _utc_iso(last_message.sent_at),
                    "body": last_message.body,
                    "status": last_message.status if hasattr(last_message, 'status') else None,
                    "deleted_by_sender": last_message.deleted_by_sender,
                    "deleted_by_receiver": last_message.deleted_by_receiver,
                    'is_deleted': last_message.is_deleted,
                    "is_read": last_message.is_read,
                    "last_resource": last_message.resources[0] if last_message.resources else None,
                    "resources": last_message.resources,
                    "reaction_map": reaction_data,
                    "from_me": last_message.sender_id == current_user.id
                }
            else:
                # All messages deleted or no messages — show nothing
                conversation_obj["last_message"] = None

            conversations_list.append(conversation_obj)

        # Sort by last visible message time, conversations with no messages go to bottom
        def sort_key(conv):
            if conv.get("last_message"):
                return conv["last_message"]["sent_at"]
            return "0"  # no messages → sort to bottom

        conversations_list.sort(key=sort_key, reverse=True)

        return jsonify({
            "status": "success",
            "data": {
                "conversations": conversations_list,
                "total_unread": sum(c["unread_count"] for c in conversations_list),
                "total_conversations": len(conversations_list)
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get conversations error: {str(e)}")
        return error_response("Failed to load conversations")





@messages_bp.route("/messages/conversation/<int:partner_id>", methods=["GET"])
@token_required
def get_conversation_messages(current_user, partner_id):
    """
    Get messages in conversation with pagination and media support
    
    Query params:
    - page: Page number (default: 1)
    - per_page: Messages per page (default: 50, max: 100)
    - since: ISO timestamp (for polling - only get new messages)
    """
    try:
        is_blocked_by_me, blocked_by_partner = is_blocked_check(current_user.id, partner_id)
        is_either_blocked = is_blocked_by_me or blocked_by_partner

        # If not connected AND not blocked — truly unauthorized
        if not can_message(current_user.id, partner_id) and not is_either_blocked:
          return error_response("Must be connected to view messages", 403)
    
        
        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 50, type=int), 100)
        since = request.args.get("since")
        
        # Base query
        query = Message.query.filter(
            or_(
                and_(Message.sender_id == current_user.id, Message.receiver_id == partner_id),
                and_(Message.sender_id == partner_id, Message.receiver_id == current_user.id)
            ),
            # Exclude deleted
            or_(
                and_(Message.sender_id == current_user.id, Message.deleted_by_sender == False, Message.is_deleted == False),
                and_(Message.receiver_id == current_user.id, Message.deleted_by_receiver == False, Message.is_deleted == False)
            )
        )
        
        # Filter by timestamp if polling
        if since:
            try:
                since_dt = datetime.datetime.fromisoformat(since.replace('Z', '+00:00'))
                query = query.filter(Message.sent_at > since_dt)
            except ValueError:
                pass
        
        # Paginate
        paginated = query.order_by(Message.sent_at.asc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Format messages
        messages_data = []

        # ── Batch pre-loads (eliminates N+1 queries in the message loop) ─────
        # Only 2 unique users exist in any DM conversation
        partner_user = User.query.get(partner_id)
        users_cache = {
            current_user.id: current_user,
            partner_id: partner_user
        }

        # Batch load all partner reactions for this page of messages
        page_msg_ids = [msg.id for msg in paginated.items]
        reactions_map = {}
        if page_msg_ids:
            partner_reactions = MessageReaction.query.filter(
                MessageReaction.message_id.in_(page_msg_ids),
                MessageReaction.user_id == partner_id
            ).all()
            reactions_map = {r.message_id: r.reaction_type for r in partner_reactions}

        for msg in paginated.items:
            sender = users_cache.get(msg.sender_id)
            reaction_type = reactions_map.get(msg.id)
            messages_data.append({
                "id": msg.id,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "sender": {
                    "id": sender.id,
                    "username": sender.username,
                    "name": sender.name,
                    "avatar": sender.avatar
                } if sender else None,
                "subject": msg.subject,
                "body": msg.body,
                'status': msg.status,
                'reaction_type': reaction_type,
                "resources": msg.resources if msg.resources else [],
                "has_media": bool(msg.resources),
                "media_count": len(msg.resources) if msg.resources else 0,
                "sent_at": _utc_iso(msg.sent_at),
                'deleted_by_sender': msg.deleted_by_sender,
                "deleted_by_receiver": msg.deleted_by_receiver,
                "is_read": msg.is_read,
                "read_at": msg.read_at.isoformat() if msg.read_at else None,
                "from_me": msg.sender_id == current_user.id,
                "is_deleted": msg.is_deleted
            })
        
        # Mark messages as read (messages TO current user)
        Message.query.filter(
            Message.sender_id == partner_id,
            Message.receiver_id == current_user.id,
            Message.is_read == False
        ).update({
            "is_read": True,
            "read_at": datetime.datetime.utcnow()
        })
        db.session.commit()
        
        # Notify sender via WebSocket that messages were read
        from websocket_events import ws_manager
        if partner_id in ws_manager.online_users:
            unread_msg_ids = [m['id'] for m in messages_data if not m['is_read'] and not m['from_me']]
            if unread_msg_ids:
                ws_manager.socketio.emit(
                    'messages_read',
                    {
                        'reader_id': current_user.id,
                        'message_ids': unread_msg_ids,
                        'read_at': datetime.datetime.utcnow().isoformat()
                    },
                    room=f"user_{partner_id}"
                )
        
        return jsonify({
            "status": "success",
            "data": {
                "messages": messages_data,
                "is_blocked_by_me":   is_blocked_by_me,
                "blocked_by_partner": blocked_by_partner,
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
        current_app.logger.error(f"Get conversation error: {str(e)}")
        return error_response("Failed to load messages")



# ============================================================================
# MESSAGE ACTIONS
# ============================================================================



@messages_bp.route("/messages/<int:message_id>/mark-read", methods=["POST"])
@token_required
def mark_message_read(current_user, message_id):
    """
    Mark specific message as read
    """
    try:
        message = Message.query.get(message_id)
        
        if not message:
            return error_response("Message not found", 404)
        
        if message.receiver_id != current_user.id:
            return error_response("Can only mark received messages as read", 403)
        
        if not message.is_read:
            message.is_read = True
            message.read_at = datetime.datetime.utcnow()
            db.session.commit()
        
        return success_response("Message marked as read")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Mark read error: {str(e)}")
        return error_response("Failed to mark as read")


@messages_bp.route("/messages/mark-all-read/<int:partner_id>", methods=["POST"])
@token_required
def mark_all_read(current_user, partner_id):
    """
    Mark all messages from a user as read
    """
    try:
        Message.query.filter(
            Message.sender_id == partner_id,
            Message.receiver_id == current_user.id,
            Message.is_read == False
        ).update({
            "is_read": True,
            "read_at": datetime.datetime.utcnow()
        })
        
        db.session.commit()
        
        return success_response("All messages marked as read")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Mark all read error: {str(e)}")
        return error_response("Failed to mark messages as read")

@messages_bp.route("/messages/unread-count", methods=["GET"])
@token_required
def get_unread_count(current_user):
    """
    Get total unread message count (for badge)
    """
    try:
        unread_count = Message.query.filter(
            Message.receiver_id == current_user.id,
            Message.is_read == False,
            Message.is_deleted == False,
            Message.deleted_by_receiver == False
        ).count()
        
        return jsonify({
            "status": "success",
            "data": {
                "unread_count": unread_count
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Unread count error: {str(e)}")
        return error_response("Failed to get unread count")




@messages_bp.route("/messages/search", methods=["GET"])
@token_required
def search_messages(current_user):
    """
    Search messages by content or sender
    
    Query params:
    - q: Search query
    - partner_id: Filter by conversation (optional)
    """
    try:
        query_str = request.args.get("q", "").strip()
        partner_id = request.args.get("partner_id", type=int)
        
        if not query_str:
            return error_response("Search query required")
        
        # Base query
        query = Message.query.filter(
            or_(
                Message.sender_id == current_user.id,
                Message.receiver_id == current_user.id
            ),
            or_(
                Message.subject.ilike(f"%{query_str}%"),
                Message.body.ilike(f"%{query_str}%")
            )
        )
        
        # Filter by partner if specified
        if partner_id:
            query = query.filter(
                or_(
                    and_(Message.sender_id == current_user.id, Message.receiver_id == partner_id),
                    and_(Message.sender_id == partner_id, Message.receiver_id == current_user.id)
                )
            )
        
        results = query.order_by(Message.sent_at.desc()).limit(50).all()
        
        results_data = []
        for msg in results:
            partner = User.query.get(
                msg.receiver_id if msg.sender_id == current_user.id else msg.sender_id
            )
            
            results_data.append({
                "message_id": msg.id,
                "partner": {
                    "id": partner.id,
                    "username": partner.username,
                    "name": partner.name
                } if partner else None,
                "subject": msg.subject,
                "body": msg.body[:200],
                "sent_at": _utc_iso(msg.sent_at),
                "from_me": msg.sender_id == current_user.id
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "results": results_data,
                "count": len(results_data)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Search messages error: {str(e)}")
        return error_response("Failed to search messages")


@messages_bp.route("/messages/can-message/<int:user_id>", methods=["GET"])
@token_required
def check_can_message(current_user, user_id):
    """
    Check if current user can message another user
    Returns permission status and reason
    """
    try:
        if user_id == current_user.id:
            return jsonify({
                "status": "success",
                "data": {
                    "can_message": False,
                    "reason": "Cannot message yourself"
                }
            })
        
        target_user = User.query.get(user_id)
        if not target_user:
            return jsonify({
                "status": "success",
                "data": {
                    "can_message": False,
                    "reason": "User not found"
                }
            })
        
        # Check if blocked
        block = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == user_id),
                and_(Connection.requester_id == user_id, Connection.receiver_id == current_user.id)
            ),
            Connection.status == "blocked"
        ).first()
        
        if block:
            return jsonify({
                "status": "success",
                "data": {
                    "can_message": False,
                    "reason": "User is blocked"
                }
            })
        
        # Check if connected
        if can_message(current_user.id, user_id):
            return jsonify({
                "status": "success",
                "data": {
                    "can_message": True,
                    "reason": "Connected"
                }
            })
        
        # Not connected - check if pending connection
        pending = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == user_id),
                and_(Connection.requester_id == user_id, Connection.receiver_id == current_user.id)
            ),
            Connection.status == "pending"
        ).first()
        
        if pending:
            if pending.requester_id == current_user.id:
                reason = "Connection request pending - waiting for acceptance"
            else:
                reason = "User sent you a connection request - accept to message"
        else:
            reason = "Not connected - send connection request to message"
        
        return jsonify({
            "status": "success",
            "data": {
                "can_message": False,
                "reason": reason,
                "can_connect": not pending
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Check can message error: {str(e)}")
        return error_response("Failed to check messaging permission")


@messages_bp.route("/messages/block/<int:user_id>", methods=["POST"])
@token_required
def block_user_messaging(current_user, user_id):
    try:
        if user_id == current_user.id:
            return error_response("Cannot block yourself")

        # Find any existing connection between the two users regardless of direction
        connection = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == user_id),
                and_(Connection.requester_id == user_id, Connection.receiver_id == current_user.id)
            )
        ).first()

        if connection:
            # Re-own the record so is_blocked_check() correctly reads it as blocked_by_me
            connection.requester_id = current_user.id
            connection.receiver_id  = user_id
            connection.status       = "blocked"
        else:
            # No prior connection — create a fresh block record owned by the blocker
            connection = Connection(
                requester_id = current_user.id,
                receiver_id  = user_id,
                status       = "blocked"
            )
            db.session.add(connection)

        db.session.commit()
        return success_response("User blocked from messaging")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Block user error: {str(e)}")
        return error_response("Failed to block user")


@messages_bp.route("/messages/unblock/<int:user_id>", methods=["POST"])
@token_required
def unblock_user_messaging(current_user, user_id):
    try:
        connection = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == user_id),
                and_(Connection.requester_id == user_id, Connection.receiver_id == current_user.id)
            ),
            Connection.status == "blocked"
        ).first()

        if not connection:
            return error_response("User is not blocked", 404)

        # ⭐ Must be "accepted" — that is what can_message() checks for.
        # Setting to "connected" left can_message() returning False → 403.
        connection.status = "accepted"
        db.session.commit()
        return success_response("User unblocked")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Unblock user error: {str(e)}")
        return error_response("Failed to unblock user")


@messages_bp.route("/messages/report/<int:message_id>", methods=["POST"])
@token_required
def report_message(current_user, message_id):
    """
    Report inappropriate message
    
    Body: {
        "reason": "spam",
        "description": "Additional details"
    }
    """
    try:
        message = Message.query.get(message_id)
        
        if not message:
            return error_response("Message not found", 404)
        
        if message.receiver_id != current_user.id:
            return error_response("Can only report messages sent to you", 403)
        
        data = request.get_json()
        reason = data.get("reason", "").strip()
        description = data.get("description", "").strip()
        
        if not reason:
            return error_response("Reason required")
        
        # Create report (using existing PostReport model structure)
        # You may want to create a separate MessageReport model
        # For now, we'll log it
        
        current_app.logger.warning(
            f"Message reported - ID: {message_id}, "
            f"From: {message.sender_id}, "
            f"Reason: {reason}, "
            f"By: {current_user.id}"
        )
        
        return success_response("Message reported - we'll review it soon")
        
    except Exception as e:
        current_app.logger.error(f"Report message error: {str(e)}")
        return error_response("Failed to report message")