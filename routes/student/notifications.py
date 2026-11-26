"""
Improved Notification Endpoints with Cursor Pagination
"""

from flask import request, jsonify, current_app, Blueprint
from sqlalchemy import or_, and_
from datetime import datetime

from models import User, Notification
from extensions import db
from routes.student.helpers import token_required, success_response, error_response
# Phase 5b (Document 4 §1): BURST_OK for mark-read/delete actions (low-risk,
# high-frequency as users clear notifications), WRITE_HEAVY for the settings
# update (less frequent, more consequential).
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key, ip_key
notifications_bp = Blueprint("student_notifications", __name__)


# ============================================================================
# GET NOTIFICATIONS WITH CURSOR PAGINATION
# ============================================================================
@notifications_bp.route("/profile/notifications/all", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def get_notifications(current_user):
    '''
    Get all notifications at once (no pagination)
    
    Query params:
    - type: Filter by notification type (optional)
    - unread_only: Boolean to fetch only unread notifications (default: false)
    
    Returns:
    - notifications: List of all notification objects
    - unread_count: Total unread notifications count
    '''
    
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found", 404)
        
        # Get user notification settings
        notification_settings = user.notification_settings or {}
        enable_notification_sound = notification_settings.get("enable_notification_sound", True)
        enable_notification = notification_settings.get("enable_notification", True)

        # The setting is enforced when notifications are CREATED (see
        # notification_service.notify). Existing history stays visible and the
        # unread count stays truthful (audit NOTIF-05).
        current_app.logger.info(f"Fetching all notifications for user {current_user.id}")
        
        # Base query - fetch ALL notifications
        query = Notification.query.filter_by(user_id=current_user.id)
        
        # Optional: filter by type if provided
        notif_type = request.args.get("type")
        if notif_type and notif_type != "all":
            query = query.filter(_build_type_filter(notif_type))
        
        # Optional: filter by unread only
        unread_only = request.args.get("unread_only", "false").lower() == "true"
        if unread_only:
            query = query.filter(Notification.is_read == False)
        
        # Order by created_at descending (newest first)
        query = query.order_by(Notification.created_at.desc())

        # Safety cap: never load more than 500 notifications at once to prevent OOM.
        notifications = query.limit(500).all()
        
        current_app.logger.info(f"Found {len(notifications)} total notifications for user {current_user.id}")
        
        # Format notifications
        notifications_data = []
        for notif in notifications:
            notifications_data.append({
                "id": notif.id,
                "title": notif.title,
                "body": notif.body,
                "type": notif.notification_type,
                "related_type": notif.related_type,
                "related_id": notif.related_id,
                "link": notif.link if hasattr(notif, "link") else None,
                "is_read": notif.is_read,
                "created_at": notif.created_at.isoformat(),
                "read_at": notif.read_at.isoformat() if notif.read_at else None
            })
        
        # Get total unread count (for badge display)
        unread_count = Notification.query.filter_by(
            user_id=current_user.id,
            is_read=False
        ).count()
        
        return jsonify({
            "status": "success",
            "data": {
                "notifications": notifications_data,
                'enable_notification': enable_notification,
                'enable_notification_sound': enable_notification_sound,
                "unread_count": unread_count,
                "count": len(notifications_data)
            }
        })
    
    except Exception as e:
        current_app.logger.error(f"Get notifications error: {str(e)}")
        return error_response("Failed to load notifications", 500)




       

# ============================================================================
# UNREAD COUNT (for badges)
# ============================================================================
@notifications_bp.route("/profile/notifications/unread-count", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
def get_unread_notification_count(current_user):
    """
    Just the number, for badges.

    Pages used to download up to 500 full notifications from /all on every load
    to read one integer (audit NOTIF-07). This is an indexed COUNT(*) that is
    always consistent with the rows.
    """
    try:
        unread_count = Notification.query.filter_by(
            user_id=current_user.id, is_read=False
        ).count()
        return jsonify({"status": "success", "data": {"unread_count": unread_count}})
    except Exception as e:
        current_app.logger.error(f"Unread notification count error: {e}")
        return error_response("Failed to load unread count", 500)


def _build_type_filter(notif_type):
    """Helper function to build notification type filters"""
    if notif_type == "badges":
        return or_(
            Notification.notification_type == "badge_earned",
            Notification.notification_type == "reputation_level_up"
        )
    elif notif_type == "engagements":
        return or_(
            Notification.notification_type == "comment",
            Notification.notification_type == "like",
            Notification.notification_type == "helpful",
            Notification.notification_type == "solution_accepted"
        )
    elif notif_type == "connections":
        return or_(
            Notification.notification_type == "connection_request",
            Notification.notification_type == "connection_accepted"
        )
    elif notif_type == "threads":
        return Notification.notification_type.like("thread_%")
    elif notif_type == "messages":
        return Notification.notification_type == "message"
    elif notif_type == "mentions":
        return Notification.notification_type == "mention"
    else:
        # Exact match for other types
        return Notification.notification_type == notif_type


# ============================================================================
# MARK ALL NOTIFICATIONS AS READ
# ============================================================================

@notifications_bp.route("/profile/notifications/mark-all-read", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def mark_all_notifications_read(current_user):
    """
    Mark all notifications as read for the current user
    
    Optional body params:
    - type: Mark only specific type as read (engagements, badges, etc.)
    - before_date: Mark all notifications before this date (ISO format)
    
    Returns:
    - marked_count: Number of notifications marked as read
    """
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found", 404)
        
        data = request.get_json(silent=True) or {}
        notif_type = data.get("type")
        before_date = data.get("before_date")
        
        # Base query for unread notifications
        query = Notification.query.filter(
            Notification.user_id == user.id,
            Notification.is_read == False
        )
        
        # Apply type filter if specified
        if notif_type:
            query = query.filter(_build_type_filter(notif_type))
        
        # Apply date filter if specified
        if before_date:
            try:
                date_obj = datetime.fromisoformat(before_date)
                query = query.filter(Notification.created_at <= date_obj)
            except ValueError:
                return error_response("Invalid date format. Use ISO 8601 format")
        
        # Bulk update
        marked_count = query.update(
            {
                Notification.is_read: True,
                Notification.read_at: datetime.utcnow()
            },
            synchronize_session=False
        )
        
        db.session.commit()

        return jsonify({
            "status": "success",
            "message": f"Marked {marked_count} notification(s) as read",
            "data": {
                "marked_count": marked_count,
                "type_filter": notif_type,
                "before_date": before_date
            }
        })
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Mark all read error: {str(e)}")
        return error_response("Error marking notifications as read", 500)


# ============================================================================
# MARK SINGLE NOTIFICATION AS READ
# ============================================================================

@notifications_bp.route("/profile/notifications/<int:notification_id>/mark-read", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def mark_notification_read(current_user, notification_id):
    """Mark a single notification as read"""
    try:
        owned = Notification.query.filter_by(
            id=notification_id, user_id=current_user.id
        ).first()
        if not owned:
            return error_response("Notification not found", 404)

        # One conditional UPDATE instead of read-then-write: marking an
        # already-read notification (double click, retry, second tab) is a
        # harmless no-op rather than a race (audit NOTIF-08/NOTIF-19).
        updated = Notification.query.filter_by(
            id=notification_id, user_id=current_user.id, is_read=False
        ).update(
            {"is_read": True, "read_at": datetime.utcnow()},
            synchronize_session=False,
        )
        db.session.commit()

        if not updated:
            return success_response("Notification already marked as read")

        return success_response("Notification marked as read")
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Mark notification read error: {str(e)}")
        return error_response("Error marking notification as read", 500)


# ============================================================================
# DELETE NOTIFICATION
# ============================================================================

@notifications_bp.route("/profile/notifications/<int:notification_id>", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def delete_notification(current_user, notification_id):
    """Delete a single notification"""
    try:
        # Single DELETE with a row count: two concurrent deletes of the same
        # notification yield one success and one clean 404, never a
        # StaleDataError 500 (audit NOTIF-08).
        deleted = Notification.query.filter_by(
            id=notification_id, user_id=current_user.id
        ).delete(synchronize_session=False)
        db.session.commit()

        if not deleted:
            return error_response("Notification not found", 404)

        return success_response("Notification deleted successfully")
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete notification error: {str(e)}")
        return error_response("Error deleting notification", 500)


# ============================================================================
# DELETE ALL NOTIFICATIONS
# ============================================================================

@notifications_bp.route("/profile/notifications/delete-all", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def delete_all_notifications(current_user):
    """
    Delete all notifications for the current user
    
    Optional query params:
    - type: Delete only specific type
    - read_only: Delete only read notifications (true/false)
    """
    try:
        notif_type = request.args.get("type")
        read_only = request.args.get("read_only", "false").lower() == "true"
        
        # Base query
        query = Notification.query.filter_by(user_id=current_user.id)
        
        # Apply filters
        if notif_type:
            query = query.filter(_build_type_filter(notif_type))
        
        if read_only:
            query = query.filter(Notification.is_read == True)
        
        # Count before deletion
        delete_count = query.count()

        # Delete
        query.delete(synchronize_session=False)
        db.session.commit()

        return jsonify({
            "status": "success",
            "message": f"Deleted {delete_count} notification(s)",
            "data": {
                "deleted_count": delete_count
            }
        })
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete all notifications error: {str(e)}")
        return error_response("Error deleting notifications", 500)


# ============================================================================
# NOTIFICATION SETTINGS
# ============================================================================

@notifications_bp.route("/profile/notifications/settings", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def update_notification_settings(current_user):
    """
    Change one of the user's notification settings.

    Body params:
    - setting: 'enable_notification' or 'enable_notification_sound'
    - value:   boolean, the state to set (idempotent). If omitted the setting
               is flipped, which keeps older clients working.

    Sending the desired value matters: a retry after a timeout, a double click
    or a second tab used to flip the setting back (audit NOTIF-19). The row is
    locked so concurrent updates of different keys cannot overwrite each other.

    (GET /profile/notifications/settings was removed: nothing called it, and it
    advertised category/email settings that were never implemented - audit
    NOTIF-20. GET /all still returns the two live settings.)
    """
    try:
        data = request.get_json(silent=True) or {}
        if not data:
            return error_response("No settings provided")

        setting_name = data.get("setting")
        if not setting_name:
            return error_response("Setting name is required")

        valid_settings = ["enable_notification", "enable_notification_sound"]
        if setting_name not in valid_settings:
            return error_response(f"Invalid setting name. Must be one of: {', '.join(valid_settings)}")

        if "value" in data and not isinstance(data["value"], bool):
            return error_response("value must be true or false")

        user = User.query.filter_by(id=current_user.id).with_for_update().first()
        if not user:
            return error_response("User not found", 404)

        notification_settings = dict(user.notification_settings or {})
        current_value = notification_settings.get(setting_name, True)
        new_value = data["value"] if "value" in data else not current_value
        notification_settings[setting_name] = new_value

        # Assign a new dict so the JSON column is always seen as changed.
        user.notification_settings = notification_settings
        db.session.commit()

        return jsonify({
            "status": "success",
            "message": f"{setting_name} updated successfully",
            "data": {
                "setting": setting_name,
                "previous_value": current_value,
                "new_value": new_value,
                "all_settings": notification_settings
            }
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update notification settings error: {str(e)}")
        return error_response("Error updating notification settings", 500)
