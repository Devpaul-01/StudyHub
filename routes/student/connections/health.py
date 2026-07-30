"""
StudyHub - Connections: Connection health/detail, notes, online-connections listing

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

logger = logging.getLogger(__name__)

connections_health_bp = Blueprint("connections_health", __name__)
@connections_health_bp.route("/connections/<int:connection_id>/details", methods=["GET"])
@token_required
def get_connection_details(current_user, connection_id):
    """
    Get comprehensive connection details including:
    - Connection metadata (date connected, type, notes)
    - Shared context (threads, mutual connections)
    - Interaction metrics (messages, engagement)
    - Connection health score
    - Timeline of major events
    - Study session history
    
    Returns all important relationship information for the connection card/profile
    """
    try:
        # Get the connection
        connection = Connection.query.get(connection_id)
        
        if not connection:
            return error_response("Connection not found", 404)

        # Verify user is part of this connection
        if connection.requester_id != current_user.id and connection.receiver_id != current_user.id:
            return error_response("Not authorized to view this connection", 403)

        # Determine roles now that authorization is confirmed
        is_requester = connection.requester_id == current_user.id
        user_notes = (
            connection.requester_notes if is_requester
            else connection.receiver_notes
        ) or ""

        # Determine the other user (the connection partner)
        partner_id = (
            connection.receiver_id
            if connection.requester_id == current_user.id
            else connection.requester_id
        )

        partner = User.query.get(partner_id)
        if not partner:
            return error_response("Partner user not found", 404)

        # ============================================================================
        # 1. BASIC CONNECTION INFO
        # ============================================================================
        
        connection_info = {
            "id": connection.id,
            "status": connection.status,
            "connection_type": connection.connection_type,
            "connected_at": connection.responded_at.isoformat() if connection.responded_at else None,
            "requested_at": connection.requested_at.isoformat(),
            "days_connected": (
                (datetime.datetime.utcnow() - connection.responded_at).days 
                if connection.responded_at else 0
            ),
            "is_requester": is_requester,
            "notes": user_notes
        }
        
        # ============================================================================
        # 2. PARTNER USER INFO
        # ============================================================================
        
        partner_profile = StudentProfile.query.filter_by(user_id=partner_id).first()
        partner_onboarding = get_user_onboarding_preview(partner_id)
        partner_online = get_user_online_status(partner_id)
        
        partner_info = {
            "id": partner.id,
            "username": partner.username,
            "name": partner.name,
            "avatar": partner.avatar,
            "bio": partner.bio,
            "reputation": partner.reputation,
            "reputation_level": partner.reputation_level,
            "department": partner_profile.department if partner_profile else None,
            "class_level": partner_profile.class_name if partner_profile else None,
            "is_online": partner_online["is_online"],
            "last_active": partner_online["last_active"],
            "onboarding_details": partner_onboarding or {}
        }
        
        # ============================================================================
        # 3. MUTUAL CONNECTIONS
        # ============================================================================
        
        mutual_count = get_mutual_connection_count(current_user.id, partner_id)
        
        # Get sample of mutual connections (up to 5)
        mutual_connections_data = []
        if mutual_count > 0:
            # Get current user's connections
            user_connections = Connection.query.filter(
                or_(
                    Connection.requester_id == current_user.id,
                    Connection.receiver_id == current_user.id
                ),
                Connection.status == "accepted"
            ).all()
            
            user_connection_ids = set()
            for conn in user_connections:
                other_id = conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
                user_connection_ids.add(other_id)
            
            # Get partner's connections
            partner_connections = Connection.query.filter(
                or_(
                    Connection.requester_id == partner_id,
                    Connection.receiver_id == partner_id
                ),
                Connection.status == "accepted"
            ).all()
            
            partner_connection_ids = set()
            for conn in partner_connections:
                other_id = conn.receiver_id if conn.requester_id == partner_id else conn.requester_id
                partner_connection_ids.add(other_id)
            
            # Find mutual
            mutual_ids = user_connection_ids & partner_connection_ids
            
            # Get details for up to 5 mutuals
            mutual_users = User.query.filter(User.id.in_(list(mutual_ids)[:5])).all()
            
            for mutual_user in mutual_users:
                mutual_connections_data.append({
                    "id": mutual_user.id,
                    "username": mutual_user.username,
                    "name": mutual_user.name,
                    "avatar": mutual_user.avatar,
                    "reputation_level": mutual_user.reputation_level
                })
        
        # ============================================================================
        # 4. SHARED THREADS
        # ============================================================================
        
        # Get threads both users are members of
        user_threads = ThreadMember.query.filter_by(student_id=current_user.id).all()
        partner_threads = ThreadMember.query.filter_by(student_id=partner_id).all()
        
        user_thread_ids = set(t.thread_id for t in user_threads)
        partner_thread_ids = set(t.thread_id for t in partner_threads)
        
        shared_thread_ids = user_thread_ids & partner_thread_ids
        
        shared_threads_data = []
        if shared_thread_ids:
            shared_threads = Thread.query.filter(Thread.id.in_(shared_thread_ids)).all()
            
            for thread in shared_threads:
                shared_threads_data.append({
                    "id": thread.id,
                    "title": thread.title,
                    "avatar": thread.avatar,
                    "member_count": thread.member_count,
                    "message_count": thread.message_count,
                    "last_activity": thread.last_activity.isoformat(),
                    "created_at": thread.created_at.isoformat()
                })
        
        # ============================================================================
        # 5. MESSAGE COUNT & INTERACTION METRICS
        # ============================================================================
        
        # Total messages between the two users
        total_messages = Message.query.filter(
            or_(
                and_(Message.sender_id == current_user.id, Message.receiver_id == partner_id),
                and_(Message.sender_id == partner_id, Message.receiver_id == current_user.id)
            ),
            Message.deleted_by_sender == False,
            Message.deleted_by_receiver == False
        ).count()
        
        # Messages sent by current user
        messages_sent = Message.query.filter(
            Message.sender_id == current_user.id,
            Message.receiver_id == partner_id,
            Message.deleted_by_sender == False
        ).count()
        
        # Messages received from partner
        messages_received = Message.query.filter(
            Message.sender_id == partner_id,
            Message.receiver_id == current_user.id,
            Message.deleted_by_receiver == False
        ).count()
        
        # Get last message info
        last_message = Message.query.filter(
            or_(
                and_(Message.sender_id == current_user.id, Message.receiver_id == partner_id),
                and_(Message.sender_id == partner_id, Message.receiver_id == current_user.id)
            ),
            Message.deleted_by_sender == False,
            Message.deleted_by_receiver == False
        ).order_by(Message.sent_at.desc()).first()
        
        last_message_info = None
        if last_message:
            last_message_info = {
                "preview": last_message.body[:100],
                "sent_at": last_message.sent_at.isoformat(),
                "from_me": last_message.sender_id == current_user.id,
                "days_ago": (datetime.datetime.utcnow() - last_message.sent_at).days
            }
        
        interaction_metrics = {
            "total_messages": total_messages,
            "messages_sent": messages_sent,
            "messages_received": messages_received,
            "last_message": last_message_info
        }
        
        # ============================================================================
        # 6. CONNECTION HEALTH
        # ============================================================================
        
        health_data = get_connection_health(current_user.id, partner_id)
        
        
       
        
        thirty_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=30)
        
      
        
        # ============================================================================
        # 11. STUDY SESSION HISTORY
        # ============================================================================

        try:
            from models import StudySessions
            study_sessions = StudySessions.query.filter(
                or_(
                    and_(StudySessions.requester_id == current_user.id, StudySessions.receiver_id == partner_id),
                    and_(StudySessions.requester_id == partner_id, StudySessions.receiver_id == current_user.id)
                )
            ).order_by(StudySessions.schedule_date.desc()).limit(20).all()

            sessions_data = []
            completed = 0
            upcoming = 0
            now = datetime.datetime.utcnow()

            for session in study_sessions:
                is_completed = False
                if session.status == "accepted" and session.schedule_date:
                    if session.schedule_date < now:
                        completed += 1
                        is_completed = True
                    else:
                        upcoming += 1

                sessions_data.append({
                    "id": session.id,
                    "subject": session.subject,
                    "type": session.type,
                    "duration": session.duration,
                    "schedule_date": session.schedule_date.isoformat() if session.schedule_date else None,
                    "status": session.status,
                    "notes": session.notes,
                    "is_completed": is_completed,
                    "is_requester": session.requester_id == current_user.id,
                    "requested_at": session.requested_at.isoformat()
                })

            total_sessions = StudySessions.query.filter(
                or_(
                    and_(StudySessions.requester_id == current_user.id, StudySessions.receiver_id == partner_id),
                    and_(StudySessions.requester_id == partner_id, StudySessions.receiver_id == current_user.id)
                )
            ).count()

            study_history = {
                "sessions": sessions_data,
                "total_sessions": total_sessions,
                "completed_sessions": completed,
                "upcoming_sessions": upcoming,
                "last_session_date": (
                    sessions_data[0]["schedule_date"]
                    if sessions_data and sessions_data[0]["schedule_date"]
                    else None
                )
            }
        except Exception:
            study_history = {
                "sessions": [],
                "total_sessions": 0,
                "completed_sessions": 0,
                "upcoming_sessions": 0,
                "last_session_date": None
            }

        # ============================================================================
        # COMPILE FINAL RESPONSE
        # ============================================================================

        return jsonify({
            "status": "success",
            "data": {
                "connection": connection_info,
                "partner": partner_info,
                "mutual_connections": {
                    "count": mutual_count,
                    "sample": mutual_connections_data
                },
                "shared_threads": {
                    "count": len(shared_thread_ids),
                    "threads": shared_threads_data
                },
                "interaction_metrics": interaction_metrics,
                "health": health_data,
                "study_history": study_history
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get connection details error: ", exc_info=True)
        return error_response("Failed to load connection details")


        

@connections_health_bp.route("/connections/online", methods=["GET"])
@token_required
def get_online_connections(current_user):
    """
    Get ALL online connections (no pagination)

    Returns connections where user was active in the last 30 minutes
    Response structure matches /connections/list endpoint

    Query params:
    - time_window: Minutes to consider as "online" (default: 30, max: 120)
    """
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")

        # Get time window from query parameter (default 30, max 120)
        time_window = min(int(request.args.get("time_window", 30)), 120)
      
        
        cutoff_time = datetime.datetime.utcnow() - datetime.timedelta(minutes=time_window)
        
        # Get all connections
        all_connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()
        
        if not all_connections:
            return jsonify({
                "status": "success",
                "data": [],
                "total": 0,
                "time_window_minutes": time_window
            })
        
        # Extract connected user IDs
        connected_ids = []
        connection_map = {}
        
        for c in all_connections:
            other_id = c.receiver_id if c.requester_id == current_user.id else c.requester_id
            connected_ids.append(other_id)
            connection_map[other_id] = c
        
        # Filter users who are online (active within time window)
        online_users = User.query.filter(
            User.id.in_(connected_ids),
            User.last_active >= cutoff_time
        ).order_by(User.last_active.desc()).all()
        
        if not online_users:
            return jsonify({
                "status": "success",
                "data": [],
                "total": 0,
                "time_window_minutes": time_window,
                "message": "No connections are currently online"
            })
        
        # Build response data (same structure as /connections/list)
        connections_data = []
        
        for user_obj in online_users:
            profile = StudentProfile.query.filter_by(user_id=user_obj.id).first()
            onboarding = get_user_onboarding_preview(user_obj.id)
            connection = connection_map.get(user_obj.id)
            health_data = get_connection_health(current_user.id, user_obj.id)
            online_status = get_user_online_status(user_obj.id)
            
            # Calculate minutes since last active
            minutes_ago = (datetime.datetime.utcnow() - user_obj.last_active).total_seconds() / 60
            
            connection_data = {
                "id": connection.id,
                "user": {
                    "id": user_obj.id,
                    "username": user_obj.username,
                    "name": user_obj.name,
                    "avatar": user_obj.avatar,
                    "bio": user_obj.bio,
                    "department": profile.department if profile else None,
                    "class_level": profile.class_name if profile else None,
                    "reputation": user_obj.reputation,
                    "reputation_level": user_obj.reputation_level,
                    "is_online": True,  # All users in this list are online
                    "last_active": online_status["last_active"],
                    "last_active_minutes": int(minutes_ago)
                },
                "onboarding_details": onboarding or {},
                "connected_at": connection.responded_at.isoformat() if connection.responded_at else None,
                "health_score": health_data.get("health_score", 0) if health_data else 0,
                "suggestion": health_data.get("suggestion", "") if health_data else "",
                "shared_threads": health_data.get("shared_threads", 0) if health_data else 0
            }
            
            connections_data.append(connection_data)
        
        return jsonify({
            "status": "success",
            "data": connections_data,
            "total": len(connections_data),
            "time_window_minutes": time_window
        })
        
    except Exception as e:
        current_app.logger.error(f"Get online connections error: ", exc_info=True)
        return error_response("Failed to load online connections")


# ============================================================================
# BONUS: Online Count Endpoint (for badges/counters)
# ============================================================================

@connections_health_bp.route("/connections/online/count", methods=["GET"])
@token_required
def get_online_connections_count(current_user):
    """
    Get count of online connections
    Lightweight endpoint for updating UI badges/counters
    
    Query params:
    - time_window: Minutes to consider as "online" (default: 30, max: 120)
    """
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")
        
        # Get time window (default 30 minutes)
        time_window = min(int(request.args.get("time_window", 30)), 120)
        cutoff_time = datetime.datetime.utcnow() - datetime.timedelta(minutes=time_window)
        
        # Get all connections
        all_connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()
        
        if not all_connections:
            return jsonify({
                "status": "success",
                "data": {
                    "count": 0,
                    "time_window_minutes": time_window
                }
            })
        
        # Extract connected user IDs
        connected_ids = []
        for c in all_connections:
            other_id = c.receiver_id if c.requester_id == current_user.id else c.requester_id
            connected_ids.append(other_id)
        
        # Count online users
        online_count = User.query.filter(
            User.id.in_(connected_ids),
            User.last_active >= cutoff_time
        ).count()
        
        return jsonify({
            "status": "success",
            "data": {
                "count": online_count,
                "time_window_minutes": time_window,
                "total_connections": len(connected_ids)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get online connections count error: ", exc_info=True)
        return error_response("Failed to get online connections count")


# ============================================================================
# BONUS: Filter by Department (online connections in same department)
# ============================================================================

@connections_health_bp.route("/connections/online/department", methods=["GET"])
@token_required
def get_online_connections_by_department(current_user):
    """
    Get online connections from the same department
    Useful for the "My Department" filter in your UI
    
    Query params:
    - time_window: Minutes to consider as "online" (default: 30, max: 120)
    """
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")
        
        user_profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
        if not user_profile or not user_profile.department:
            return jsonify({
                "status": "success",
                "data": [],
                "total": 0,
                "message": "Your department is not set"
            })
        
        user_dept = user_profile.department
        
        # Get time window (default 30 minutes)
        time_window = min(int(request.args.get("time_window", 30)), 120)
        cutoff_time = datetime.datetime.utcnow() - datetime.timedelta(minutes=time_window)
        
        # Get all connections
        all_connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()
        
        if not all_connections:
            return jsonify({
                "status": "success",
                "data": [],
                "total": 0,
                "user_department": user_dept
            })
        
        # Extract connected user IDs
        connected_ids = []
        connection_map = {}
        
        for c in all_connections:
            other_id = c.receiver_id if c.requester_id == current_user.id else c.requester_id
            connected_ids.append(other_id)
            connection_map[other_id] = c
        
        # Get online users from same department
        online_dept_users = db.session.query(User).join(
            StudentProfile, StudentProfile.user_id == User.id
        ).filter(
            User.id.in_(connected_ids),
            User.last_active >= cutoff_time,
            StudentProfile.department == user_dept
        ).order_by(User.last_active.desc()).all()
        
        if not online_dept_users:
            return jsonify({
                "status": "success",
                "data": [],
                "total": 0,
                "user_department": user_dept,
                "time_window_minutes": time_window,
                "message": f"No online connections from {user_dept}"
            })
        
        # Build response data
        connections_data = []
        
        for user_obj in online_dept_users:
            profile = StudentProfile.query.filter_by(user_id=user_obj.id).first()
            onboarding = get_user_onboarding_preview(user_obj.id)
            connection = connection_map.get(user_obj.id)
            health_data = get_connection_health(current_user.id, user_obj.id)
            online_status = get_user_online_status(user_obj.id)
            
            minutes_ago = (datetime.datetime.utcnow() - user_obj.last_active).total_seconds() / 60
            
            connection_data = {
                "id": connection.id,
                "user": {
                    "id": user_obj.id,
                    "username": user_obj.username,
                    "name": user_obj.name,
                    "avatar": user_obj.avatar,
                    "bio": user_obj.bio,
                    "department": profile.department if profile else None,
                    "class_level": profile.class_name if profile else None,
                    "reputation": user_obj.reputation,
                    "reputation_level": user_obj.reputation_level,
                    "is_online": True,
                    "last_active": online_status["last_active"],
                    "last_active_minutes": int(minutes_ago)
                },
                "onboarding_details": onboarding or {},
                "connected_at": connection.responded_at.isoformat() if connection.responded_at else None,
                "health_score": health_data.get("health_score", 0) if health_data else 0,
                "suggestion": health_data.get("suggestion", "") if health_data else "",
                "shared_threads": health_data.get("shared_threads", 0) if health_data else 0,
                "same_department": True
            }
            
            connections_data.append(connection_data)
        
        return jsonify({
            "status": "success",
            "data": connections_data,
            "total": len(connections_data),
            "user_department": user_dept,
            "time_window_minutes": time_window
        })
        
    except Exception as e:
        current_app.logger.error(f"Get online dept connections error: ", exc_info=True)
        return error_response("Failed to load online connections from department")



@connections_health_bp.route("/connections/<int:connection_id>/notes", methods=["GET"])
@token_required
def get_connection_notes(current_user, connection_id):
    """
    Get the notes for a specific connection.
    Returns the notes that the current user wrote about the connection.
    
    Returns:
    - For requester: requester_notes (notes they wrote when sending request)
    - For receiver: receiver_notes (notes they wrote about the connection)
    """
    try:
        # Get the connection
        connection = Connection.query.get(connection_id)
        
        if not connection:
            return error_response("Connection not found", 404)
        
        # Verify user is part of this connection
        if connection.requester_id != current_user.id and connection.receiver_id != current_user.id:
            return error_response("Not authorized to view this connection", 403)
        
        # Determine if current user is requester or receiver
        is_requester = connection.requester_id == current_user.id
        
        # Get the appropriate notes
        user_notes = (
            connection.requester_notes if is_requester 
            else connection.receiver_notes
        ) or ""
        
        # Get the other user's info
        partner_id = (
            connection.receiver_id 
            if connection.requester_id == current_user.id 
            else connection.requester_id
        )
        
        partner = User.query.get(partner_id)
        if not partner:
            return error_response("Partner user not found", 404)
        
        return jsonify({
            "status": "success",
            "data": {
                "connection_id": connection.id,
                "notes": user_notes,
                "is_requester": is_requester,
                "partner": {
                    "id": partner.id,
                    "username": partner.username,
                    "name": partner.name,
                    "avatar": partner.avatar
                },
                "status": connection.status,
                "last_updated": connection.responded_at.isoformat() if connection.responded_at else connection.requested_at.isoformat()
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Get connection notes error: ", exc_info=True)
        return error_response("Failed to get connection notes")


@connections_health_bp.route("/connections/<int:connection_id>/notes/update", methods=["PUT", "POST"])
@token_required
def update_connection_notes(current_user, connection_id):
    """
    Update the notes for a specific connection.
    Users can only update their own notes about the connection.
    
    Body:
    {
        "notes": "Your notes about this connection"
    }
    """
    try:
        data = request.get_json()
        notes = data.get("notes", "").strip()
        
        # Validate notes length (optional)
        if len(notes) > 500:
            return error_response("Notes too long (max 500 characters)", 400)
        
        # Get the connection
        connection = Connection.query.get(connection_id)
        
        if not connection:
            return error_response("Connection not found", 404)
        
        # Verify user is part of this connection
        if connection.requester_id != current_user.id and connection.receiver_id != current_user.id:
            return error_response("Not authorized to update this connection", 403)
        
        # Determine if current user is requester or receiver and update appropriate notes
        is_requester = connection.requester_id == current_user.id
        
        if is_requester:
            connection.requester_notes = notes
        else:
            connection.receiver_notes = notes
        
        db.session.commit()
        
        return success_response(
            "Connection notes updated successfully",
            data={
                "connection_id": connection.id,
                "notes": notes,
                "is_requester": is_requester
            }
        )
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update connection notes error: ", exc_info=True)
        return error_response("Failed to update connection notes")



