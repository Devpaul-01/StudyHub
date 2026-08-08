"""
StudyHub - Connections: Block / Unblock endpoints

Split from connections.py per Document 1 (Architecture Refactor) §2.1 as
part of Phase 2 (God-file splitting). This is a pure move — function
bodies, decorators, routes, and logic are unchanged from the original
connections.py. See routes/student/connections/__init__.py for the
sub-blueprint aggregation that re-exposes all routes under the same
paths as before.
"""

from flask import Blueprint, request, jsonify, current_app, Response, stream_with_context
from sqlalchemy import or_, and_, case
from datetime import timedelta
import datetime
import json
import logging
import random

from models import (
    User, StudentProfile, Connection, Notification,
    HelpRequest, Thread, ThreadMember,
    OnboardingDetails, Message
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response,
    block_connection, unblock_connection,
)

from services.online_status_service import get_user_online_status
from services.ai_provider_service import provider_manager, StudyAssistant
from services.connection_service import (
    calculate_compatibility_score,
    calculate_schedule_overlap,
    gather_user_data,
    calculate_compatibility,
    get_recent_activity,
    get_mutual_connection_count,
    get_connection_health,
    get_user_onboarding_preview,
)
# Phase 5b (Document 4 §1): block/unblock -> WRITE_HEAVY (security-sensitive
# per this file's own docstring, worth guarding against abuse); blocked-list
# reads -> PUBLIC_READ.
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key, ip_key

logger = logging.getLogger(__name__)

connections_blocking_bp = Blueprint("connections_blocking", __name__)
@connections_blocking_bp.route("/connections/blocked/list", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def list_blocked_users_detailed(current_user):
    """
    Get list of all blocked users (no pagination)
    Returns user details in the same format as connections list
    """
    try:
        # Get all blocked connections where current user is the blocker.
        # C-3 fix: filter on blocked_by_id — receiver_id no longer implies
        # "the blocker" (that assumption depended on block_user() swapping
        # IDs, which it no longer does).
        blocked_connections = Connection.query.filter_by(
            blocked_by_id=current_user.id,
            status="blocked"
        ).all()

        if not blocked_connections:
            return jsonify({
                "status": "success",
                "data": {
                    "blocked_users": [],
                    "total": 0
                }
            })

        # The blocked user is whichever side of the row isn't current_user.
        def _other_user_id(conn):
            return conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id

        blocked_ids = [_other_user_id(conn) for conn in blocked_connections]

        # Create a map of connection_id and blocked_at for later use
        connection_map = {
            _other_user_id(conn): {
                "connection_id": conn.id,
                "blocked_at": conn.responded_at
            }
            for conn in blocked_connections
        }
        
        # Get all blocked users
        blocked_users = User.query.filter(User.id.in_(blocked_ids)).all()
        
        # Prepare detailed user data
        blocked_users_data = []
        for user in blocked_users:
            profile = StudentProfile.query.filter_by(user_id=user.id).first()
            conn_info = connection_map.get(user.id, {})
            
            blocked_users_data.append({
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "avatar": user.avatar,
                "bio": user.bio,
                "department": profile.department if profile else None,
                "class_level": profile.class_name if profile else None,
                "reputation": user.reputation,
                "reputation_level": user.reputation_level,
                "connection_id": conn_info.get("connection_id"),
                "blocked_at": conn_info.get("blocked_at").isoformat() if conn_info.get("blocked_at") else None
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "blocked_users": blocked_users_data,
                "total": len(blocked_users_data)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"List blocked users detailed error: ", exc_info=True)
        return error_response("Failed to load blocked")
        

@connections_blocking_bp.route("/connections/block/<int:user_id>", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def block_user(current_user, user_id):
    """
    Block a user - prevents them from:
    - Sending connection requests
    - Viewing your profile (if private)
    - Messaging you

    This also removes any existing connection.

    C-3 fix: delegates to helpers.block_connection(), which tracks "who
    blocked whom" via Connection.blocked_by_id instead of swapping
    requester_id/receiver_id on the row (that used to corrupt the original
    connection-request history — see audit finding C-3).
    """
    try:
        if user_id == current_user.id:
            return error_response("Cannot block yourself")

        target_user = User.query.get(user_id)
        if not target_user:
            return error_response("User not found")

        block_connection(current_user.id, user_id)
        db.session.commit()

        return success_response(
            "User blocked successfully",
            data={
                "blocked_user": {
                    "id": target_user.id,
                    "username": target_user.username,
                    "name": target_user.name
                }
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Block user error: ", exc_info=True)
        return error_response("Failed to block user")


@connections_blocking_bp.route("/connections/unblock/<int:user_id>", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def unblock_user(current_user, user_id):
    """
    Unblock a previously blocked user.

    C-3 fix: delegates to helpers.unblock_connection(), which authorizes
    against Connection.blocked_by_id (unambiguous) instead of assuming the
    blocker is always receiver_id. Deletes the connection row — matches
    this endpoint's original behaviour, letting the two users reconnect
    from scratch with a fresh request.
    """
    try:
        success, error_message = unblock_connection(
            current_user.id, user_id, restore_to_accepted=False
        )

        if not success:
            status_code = 403 if error_message == "Not authorized" else 404
            return error_response(error_message, status_code)

        db.session.commit()
        return success_response("User unblocked successfully")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Unblock user error: ", exc_info=True)
        return error_response("Failed to unblock user")


@connections_blocking_bp.route("/connections/blocked", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def list_blocked_users(current_user):
    """
    Get list of all blocked users
    """
    try:
        # Find all users blocked by current user.
        # C-3 fix: filter on blocked_by_id (unambiguous) instead of assuming
        # receiver_id always identifies the blocker.
        blocked = Connection.query.filter(
            Connection.blocked_by_id == current_user.id,
            Connection.status == "blocked"
        ).all()
        
        blocked_data = []
        for block in blocked:
            other_user_id = block.receiver_id if block.requester_id == current_user.id else block.requester_id
            user = User.query.get(other_user_id)
            if user:
                profile = StudentProfile.query.filter_by(user_id=user.id).first()
                blocked_data.append({
                    "id": user.id,
                    "username": user.username,
                    "name": user.name,
                    "avatar": user.avatar,
                    "department": profile.department if profile else None,
                    "blocked_at": block.responded_at.isoformat() if block.responded_at else None
                })
        
        return jsonify({
            "status": "success",
            "data": {
                "blocked_users": blocked_data,
                "total": len(blocked_data)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"List blocked users error: ", exc_info=True)
        return error_response("Failed to load blocked users")
