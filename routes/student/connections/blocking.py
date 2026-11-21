"""
StudyHub - Connections: Block / Unblock endpoints

Split from connections.py per Document 1 (Architecture Refactor) §2.1.
See routes/student/connections/__init__.py for the sub-blueprint aggregation.

Production-readiness audit (FEATURE_PRODUCTION_READINESS_AUDIT.md):
  CON-10  blocking is idempotent and never transfers block ownership
  CON-02  unblocking always deletes the row
  CON-15  the blocked list is batch-loaded (no per-row queries)
  CON-18  authenticated routes are keyed per user, not per IP
  CON-22  404 for unknown users, 500 for unexpected failures
  CON-24  the duplicate GET /connections/blocked endpoint was removed
          (identical data to /connections/blocked/list; no consumers)
"""

from flask import Blueprint, current_app
import logging

from models import User, StudentProfile, Connection
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response,
    block_connection, unblock_connection,
)
from services.connection_service import invalidate_pair_caches
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key
from flask import jsonify

logger = logging.getLogger(__name__)

connections_blocking_bp = Blueprint("connections_blocking", __name__)


@connections_blocking_bp.route("/connections/blocked/list", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
def list_blocked_users_detailed(current_user):
    """
    List the users YOU have blocked, with the same card fields as the
    connections list.

    Response: {"data": {"blocked_users": [...], "total": N}}
    """
    try:
        # Filter on blocked_by_id: requester/receiver never imply "the blocker".
        blocked_connections = Connection.query.filter_by(
            blocked_by_id=current_user.id,
            status="blocked"
        ).order_by(Connection.responded_at.desc(), Connection.id.desc()).all()

        if not blocked_connections:
            return jsonify({
                "status": "success",
                "data": {"blocked_users": [], "total": 0}
            })

        def _other_user_id(conn):
            return conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id

        blocked_ids = [_other_user_id(conn) for conn in blocked_connections]

        # Batch-load users + profiles (2 queries instead of 2 per row).
        users_map = {u.id: u for u in User.query.filter(User.id.in_(blocked_ids)).all()}
        profiles_map = {
            p.user_id: p
            for p in StudentProfile.query.filter(StudentProfile.user_id.in_(blocked_ids)).all()
        }

        blocked_users_data = []
        for conn in blocked_connections:
            user = users_map.get(_other_user_id(conn))
            if not user:
                continue
            profile = profiles_map.get(user.id)
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
                "connection_id": conn.id,
                "blocked_at": conn.responded_at.isoformat() if conn.responded_at else None,
            })

        return jsonify({
            "status": "success",
            "data": {
                "blocked_users": blocked_users_data,
                "total": len(blocked_users_data)
            }
        })

    except Exception:
        current_app.logger.error("List blocked users error", exc_info=True)
        return error_response("Failed to load blocked users", 500)


@connections_blocking_bp.route("/connections/block/<int:user_id>", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def block_user(current_user, user_id):
    """
    Block a user - prevents them from sending connection requests and from
    messaging you; any existing connection becomes a block.

    Delegates to services.connection_service.block_connection(), which is
    idempotent and tracks the blocker via Connection.blocked_by_id. If the
    pair is already blocked (by either user) the existing block is kept as is,
    so the blocked user cannot take over ownership of the block (CON-10).
    """
    try:
        if user_id == current_user.id:
            return error_response("Cannot block yourself")

        target_user = User.query.get(user_id)
        if not target_user:
            return error_response("User not found", 404)

        block_connection(current_user.id, user_id)
        db.session.commit()
        invalidate_pair_caches(current_user.id, user_id)

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

    except Exception:
        db.session.rollback()
        current_app.logger.error("Block user error", exc_info=True)
        return error_response("Failed to block user", 500)


@connections_blocking_bp.route("/connections/unblock/<int:user_id>", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def unblock_user(current_user, user_id):
    """
    Unblock a previously blocked user (only the user who blocked can).

    Deletes the connection row: the two users must send a fresh request to
    reconnect. /messages/unblock behaves identically (CON-02).
    """
    try:
        success, error_message = unblock_connection(current_user.id, user_id)

        if not success:
            status_code = 403 if error_message == "Not authorized" else 404
            return error_response(error_message, status_code)

        db.session.commit()
        invalidate_pair_caches(current_user.id, user_id)
        return success_response("User unblocked successfully")

    except Exception:
        db.session.rollback()
        current_app.logger.error("Unblock user error", exc_info=True)
        return error_response("Failed to unblock user", 500)
