"""
StudyHub - Connection Request System
Users must connect before messaging - prevents spam and creates safer community
"""

from flask import Blueprint, request, jsonify, current_app, Response, stream_with_context
from sqlalchemy import or_, and_, func, case
from datetime import timedelta
import datetime
import json
import logging
import random
from collections import Counter

from models import (
    User, StudentProfile, Connection, Notification,
    HelpRequest, Post, Comment, Thread, ThreadMember,
    OnboardingDetails, PostReaction, CommentHelpfulMark, Message
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)
from utils import get_user_online_status

logger = logging.getLogger(__name__)

connections_bp = Blueprint('connections', __name__)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
# ============================================================================
# CORRECTED ENDPOINTS - Replace in your connections.py
# ============================================================================

# Helper function for online status



# ============================================================================
# 1. RECEIVED CONNECTION REQUESTS - NO PAGINATION
# ============================================================================
# ============================================================================
# CONVERSATION MANAGEMENT
# ============================================================================
# =============================================================================
# ADD THESE THREE ENDPOINTS TO connections.py
# Place at the bottom of connections.py (before the last line).
# They mirror the auth.py onboard endpoints but live in connections.py,
# and the connect endpoints always create ACCEPTED (not pending) connections.
# =============================================================================
#
# Required imports already present in connections.py - no re-import needed:
#   from models import User, StudentProfile, Connection, OnboardingDetails, Notification
#   from extensions import db
#   import datetime, random
#   from sqlalchemy import or_, and_
#
# =============================================================================


# 1. SUGGESTIONS ENDPOINT
# Replaces /student/onboard/suggestions-by-email/<email> from auth.py
@connections_bp.route("/connections/suggestions-by-email/<email>", methods=["GET"])
def connections_suggestions_by_email(email):
    """
    Onboarding suggestions using email. No token required.
    Returns smart match list based on subjects, learning style, schedule.
    """
    try:
        if not email:
            return error_response("Email required", 400)

        user = User.query.filter_by(email=email).first()
        if not user:
            return error_response("User not found", 404)

        onboarding = OnboardingDetails.query.filter_by(user_id=user.id).first()

        # OPTIMIZED: Single join query replaces O(N) per-user queries.
        # Fetches all candidates with their profiles and onboarding in one pass.
        candidates_data = (
            db.session.query(User, StudentProfile, OnboardingDetails)
            .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
            .outerjoin(OnboardingDetails, OnboardingDetails.user_id == User.id)
            .filter(User.id != user.id, User.status == "approved")
            .all()
        )

        matches = []

        for candidate, profile, c_onboard in candidates_data:
            score   = 0
            reasons = []

            # Department match
            if profile and onboarding:
                my_dept = getattr(onboarding, 'department', None)
                c_dept  = profile.department if profile else None
                if my_dept and c_dept and my_dept == c_dept:
                    score += 40
                    reasons.append("Same department")

            if c_onboard and onboarding:
                # Shared subjects
                my_subs  = set(s.lower() for s in (onboarding.subjects or []))
                c_subs   = set(s.lower() for s in (c_onboard.subjects or []))
                shared   = my_subs & c_subs
                if shared:
                    score += len(shared) * 10
                    top_shared = list(shared)[:2]
                    reasons.append(f"Studies {', '.join(top_shared)}")

                # Complementary: I need help where they are strong
                my_help  = set(s.lower() for s in (onboarding.help_subjects or []))
                c_strong = set(s.lower() for s in (c_onboard.strong_subjects or []))
                comp     = my_help & c_strong
                if comp:
                    score += len(comp) * 15
                    top_comp = list(comp)[:2]
                    reasons.append(f"Can help with {', '.join(top_comp)}")

                # Same learning style
                if (onboarding.learning_style and c_onboard.learning_style
                        and onboarding.learning_style == c_onboard.learning_style):
                    score += 10
                    reasons.append("Same learning style")

                # Schedule overlap
                my_sched = onboarding.study_schedule or {}
                c_sched  = c_onboard.study_schedule or {}
                overlap  = set(my_sched.keys()) & set(c_sched.keys())
                if overlap:
                    score += len(overlap) * 3
                    reasons.append("Overlapping schedule")

            # Reputation bonus
            if candidate.reputation >= 500:
                score += 10
                reasons.append(candidate.reputation_level or "High reputation")

            if score >= 10:
                matches.append({
                    "user": {
                        "id":               candidate.id,
                        "username":         candidate.username,
                        "name":             candidate.name,
                        "avatar":           candidate.avatar or "/static/default-avatar.png",
                        "reputation":       candidate.reputation,
                        "reputation_level": candidate.reputation_level,
                        "department":       profile.department if profile else None,
                        "class_level":      profile.class_name if profile else None,
                    },
                    "match_score": min(score, 99),
                    "reasons":     reasons[:4]
                })

        matches.sort(key=lambda x: x["match_score"], reverse=True)
        top_matches = matches[:10]

        # Fallback: top reputation users if no algorithmic matches
        if not top_matches:
            top_users = User.query.filter(
                User.id != user.id, User.status == "approved"
            ).order_by(User.reputation.desc()).limit(6).all()

            # OPTIMIZED: Batch-load fallback profiles in one query
            top_user_ids = [tu.id for tu in top_users]
            top_profiles_map = {
                p.user_id: p
                for p in StudentProfile.query.filter(
                    StudentProfile.user_id.in_(top_user_ids)
                ).all()
            }

            for tu in top_users:
                tu_profile = top_profiles_map.get(tu.id)
                top_matches.append({
                    "user": {
                        "id":               tu.id,
                        "username":         tu.username,
                        "name":             tu.name,
                        "avatar":           tu.avatar or "/static/default-avatar.png",
                        "reputation":       tu.reputation,
                        "reputation_level": tu.reputation_level,
                        "department":       tu_profile.department if tu_profile else None,
                        "class_level":      tu_profile.class_name if tu_profile else None,
                    },
                    "match_score": random.randint(50, 70),
                    "reasons":     ["Top contributor", "Active member"]
                })

        return jsonify({"status": "success", "data": {"matches": top_matches}})

    except Exception as e:
        current_app.logger.error(f"connections_suggestions_by_email error: ", exc_info=True)
        return error_response("Failed to generate suggestions")


# 2. SINGLE DIRECT-ACCEPT CONNECT (ONBOARDING)
@connections_bp.route("/connections/onboard-connect/<email>/<int:target_user_id>", methods=["POST"])
def onboard_connect_single(email, target_user_id):
    """
    Onboarding: connect with a single user and immediately set status=accepted.
    No pending step. No token required - uses email from URL path.
    Idempotent: already-connected pairs return success without duplication.
    """
    try:
        user = User.query.filter_by(email=email).first()
        if not user:
            return error_response("User not found", 404)

        if user.id == target_user_id:
            return error_response("Cannot connect with yourself", 400)

        target = User.query.get(target_user_id)
        if not target:
            return error_response("Target user not found", 404)

        existing = Connection.query.filter(
            or_(
                and_(Connection.requester_id == user.id, Connection.receiver_id == target_user_id),
                and_(Connection.requester_id == target_user_id, Connection.receiver_id == user.id)
            )
        ).first()

        if existing:
            if existing.status == "accepted":
                return jsonify({"status": "success", "message": "Already connected",
                                "data": {"connection_id": existing.id}})
            existing.status = "accepted"
            existing.responded_at = datetime.datetime.utcnow()
            db.session.commit()
            return jsonify({"status": "success", "message": "Connection accepted",
                            "data": {"connection_id": existing.id}})

        connection = Connection(
            requester_id    = user.id,
            receiver_id     = target_user_id,
            status          = "accepted",
            requested_at    = datetime.datetime.utcnow(),
            responded_at    = datetime.datetime.utcnow(),
            requester_notes = "Connected during onboarding"
        )
        db.session.add(connection)

        notification = Notification(
            user_id           = target_user_id,
            title             = f"{user.name} connected with you!",
            body              = f"{user.name} just joined StudyHub and connected with you.",
            notification_type = "connection_accepted",
            related_type      = "user",
            related_id        = user.id
        )
        db.session.add(notification)
        db.session.commit()

        return jsonify({
            "status":  "success",
            "message": "Connected successfully",
            "data":    {"connection_id": connection.id, "status": "accepted"}
        }), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"onboard_connect_single error: ", exc_info=True)
        return error_response("Failed to create connection")


# 3. BULK DIRECT-ACCEPT CONNECT (ONBOARDING "CONNECT ALL")
@connections_bp.route("/connections/onboard-connect-all/<email>", methods=["POST"])
def onboard_connect_all(email):
    """
    Onboarding: connect with all supplied user IDs at once, all accepted immediately.
    No token required - uses email from URL path.
    Body: { "ids": [1, 2, 3] }
    """
    try:
        user = User.query.filter_by(email=email).first()
        if not user:
            return error_response("User not found", 404)

        data = request.get_json()
        ids  = data.get("ids", [])

        if not ids:
            return error_response("No user IDs provided", 400)

        connected = []
        skipped   = []

        for target_id in ids:
            if target_id == user.id:
                continue

            target = User.query.get(target_id)
            if not target:
                skipped.append(target_id)
                continue

            existing = Connection.query.filter(
                or_(
                    and_(Connection.requester_id == user.id,   Connection.receiver_id == target_id),
                    and_(Connection.requester_id == target_id, Connection.receiver_id == user.id)
                )
            ).first()

            if existing:
                if existing.status != "accepted":
                    existing.status       = "accepted"
                    existing.responded_at = datetime.datetime.utcnow()
                connected.append(target_id)
                continue

            connection = Connection(
                requester_id    = user.id,
                receiver_id     = target_id,
                status          = "accepted",
                requested_at    = datetime.datetime.utcnow(),
                responded_at    = datetime.datetime.utcnow(),
                requester_notes = "Connected during onboarding (bulk)"
            )
            db.session.add(connection)

            notification = Notification(
                user_id           = target_id,
                title             = f"{user.name} connected with you!",
                body              = f"{user.name} just joined and connected with you on StudyHub.",
                notification_type = "connection_accepted",
                related_type      = "user",
                related_id        = user.id
            )
            db.session.add(notification)
            connected.append(target_id)

        db.session.commit()

        return jsonify({
            "status":  "success",
            "message": f"Connected with {len(connected)} user(s)",
            "data": {
                "connected_count": len(connected),
                "connected_ids":   connected,
                "skipped_ids":     skipped
            }
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"onboard_connect_all error: ", exc_info=True)
        return error_response("Failed to connect with all users")

# ADD TO connections.py (after other endpoints)
@connections_bp.route("/connections/help/broadcast", methods=["POST"])
@token_required
def broadcast_help_request(current_user):
    """
    Create a help request and broadcast to relevant connections via
    push notification + in-app notification.
    """
    try:
        data = request.get_json()
        subject = data.get('subject', '').strip()
        message = data.get('message', '').strip()

        if not subject:
            return error_response("Subject is required", 400)

        # Expire any previous active request from this user
        old_requests = HelpRequest.query.filter_by(
            requester_id=current_user.id,
            status='active'
        ).all()
        for old in old_requests:
            old.status = 'expired'

        # Create the new help request
        help_request = HelpRequest(
            requester_id=current_user.id,
            subject=subject,
            message=message or None,
            status='active',
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=2)
        )
        db.session.add(help_request)
        db.session.flush()  # Get the ID before commit

        # Get user's accepted connections
        connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == 'accepted'
        ).all()

        connection_ids = [
            c.receiver_id if c.requester_id == current_user.id else c.requester_id
            for c in connections
        ]

        if not connection_ids:
            db.session.commit()
            return jsonify({
                "status": "success",
                "data": {
                    "help_request_id": help_request.id,
                    "notified_count": 0,
                    "message": "Request created but you have no connections to notify"
                }
            })

        # Score connections by subject relevance (reuse your existing logic)
        subject_lower = subject.lower()
        scored = []

        for user_id in connection_ids:
            user = User.query.get(user_id)
            if not user:
                continue
            onboarding = OnboardingDetails.query.filter_by(user_id=user_id).first()
            if not onboarding:
                continue

            score = 0
            for subj in (onboarding.subjects or []):
                if subject_lower in subj.lower():
                    score += 30
                    break
            for subj in (onboarding.strong_subjects or []):
                if subject_lower in subj.lower():
                    score += 50
                    break

            # Include everyone who has any relevance, plus a small group of others
            scored.append((user, score))

        # Sort by relevance — top 10 most relevant connections get notified
        scored.sort(key=lambda x: x[1], reverse=True)
        targets = [u for u, s in scored[:10]]

        # Collect FCM tokens for multicast push
        fcm_tokens = [u.fcm_token for u in targets if u.fcm_token]

        notif_title = f"{current_user.name} needs help!"
        notif_body = f"Help needed with {subject}"
        notif_data = {
            'type': 'help_request',
            'help_request_id': str(help_request.id),
            'requester_id': str(current_user.id),
            'requester_name': current_user.name,
            'subject': subject
        }

        # Send push notifications
        if fcm_tokens:
            from services.push_notifications import PushNotificationService
            PushNotificationService.send_multicast(
                fcm_tokens,
                notif_title,
                notif_body,
                notif_data
            )

        # Create in-app notifications
        for user in targets:
            notif = Notification(
                user_id=user.id,
                title=notif_title,
                body=notif_body,
                notification_type='help_request',
                related_type='help_request',
                related_id=help_request.id,
                link=f'/help-request/{help_request.id}'
            )
            db.session.add(notif)

        help_request.broadcast_sent = True
        db.session.commit()

        return jsonify({
            "status": "success",
            "data": {
                "help_request_id": help_request.id,
                "notified_count": len(targets),
                "expires_at": help_request.expires_at.isoformat()
            }
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Broadcast help error: ", exc_info=True)
        return error_response("Failed to broadcast help request")

  
@connections_bp.route("/connections/help/<int:request_id>/volunteer", methods=["POST"])
@token_required
def volunteer_for_help(current_user, request_id):
    """
    A connection volunteers to help. Adds them to the volunteer list
    and notifies the requester via push + in-app notification.
    """
    try:
        help_request = HelpRequest.query.get(request_id)

        if not help_request:
            return error_response("Help request not found", 404)

        if help_request.status != 'active':
            return error_response("This help request is no longer active", 400)

        if help_request.is_expired():
            help_request.status = 'expired'
            db.session.commit()
            return error_response("This help request has expired", 400)

        if help_request.requester_id == current_user.id:
            return error_response("Cannot volunteer for your own request", 400)

        # Check they're already volunteered
        volunteers = help_request.volunteers or []
        already = any(v['user_id'] == current_user.id for v in volunteers)
        if already:
            return error_response("You have already volunteered", 400)

        # Append volunteer
        volunteers.append({
            'user_id': current_user.id,
            'name': current_user.name,
            'username': current_user.username,
            'avatar': current_user.avatar,
            'volunteered_at': datetime.datetime.utcnow().isoformat()
        })
        help_request.volunteers = volunteers

        requester = User.query.get(help_request.requester_id)

        # Notify the requester
        notif_title = f"{current_user.name} can help!"
        notif_body = f"They volunteered to help with {help_request.subject}"

        if requester and requester.fcm_token:
            from services.push_notifications import PushNotificationService
            PushNotificationService.send_notification(
                requester.fcm_token,
                notif_title,
                notif_body,
                data={
                    'type': 'help_volunteer',
                    'help_request_id': str(request_id),
                    'volunteer_id': str(current_user.id),
                    'volunteer_name': current_user.name
                }
            )

        # In-app notification to requester
        notif = Notification(
            user_id=requester.id,
            title=notif_title,
            body=notif_body,
            notification_type='help_volunteer',
            related_type='help_request',
            related_id=request_id,
            link=f'/help-request/{request_id}'
        )
        db.session.add(notif)

        # Emit websocket event to requester if they're online
        try:
            from websocket_events import ws_manager
            ws_manager.emit_to_user(requester.id, 'help_volunteer_joined', {
                'help_request_id': request_id,
                'volunteer': {
                    'user_id': current_user.id,
                    'name': current_user.name,
                    'username': current_user.username,
                    'avatar': current_user.avatar,
                },
                'total_volunteers': len(volunteers)
            })
        except Exception as ws_err:
            current_app.logger.warning(f"WebSocket emit failed (non-critical): {ws_err}")

        db.session.commit()

        return jsonify({
            "status": "success",
            "data": {
                "message": "You have volunteered to help",
                "total_volunteers": len(volunteers)
            }
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Volunteer error: ", exc_info=True)
        return error_response("Failed to volunteer")
@connections_bp.route("/connections/help/<int:request_id>/volunteers", methods=["GET"])
@token_required
def get_help_volunteers(current_user, request_id):
    """
    Requester fetches the current volunteer list for their help request.
    """
    try:
        help_request = HelpRequest.query.get(request_id)

        if not help_request:
            return error_response("Help request not found", 404)

        if help_request.requester_id != current_user.id:
            return error_response("Not authorized", 403)

        return jsonify({
            "status": "success",
            "data": {
                "help_request_id": request_id,
                "subject": help_request.subject,
                "status": help_request.status,
                "volunteers": help_request.volunteers or [],
                "total_volunteers": len(help_request.volunteers or []),
                "expires_at": help_request.expires_at.isoformat() if help_request.expires_at else None
            }
        })

    except Exception as e:
        current_app.logger.error(f"Get volunteers error: ", exc_info=True)
        return error_response("Failed to get volunteers")


@connections_bp.route("/connections/help/find", methods=["POST"])
@token_required
def find_help_with_subject(current_user):
    """Find users who can help with a specific subject"""
    try:
        data = request.get_json()
        subject = data.get('subject', '').strip()
        
        if not subject:
            return error_response("Please provide a subject", 400)
        
        subject_lower = subject.lower()
        all_users = User.query.filter(
            User.id != current_user.id,
            User.status == "approved"
        ).all()
        
        helpers = []
        
        for user in all_users:
            profile = StudentProfile.query.filter_by(user_id=user.id).first()
            onboarding = OnboardingDetails.query.filter_by(user_id=user.id).first()
            
            if not onboarding:
                continue
            
            expertise_score = 0
            reasons = []
            
            # Check subjects
            subjects_list = onboarding.subjects or []
            strong_subjects_list = onboarding.strong_subjects or []
            
            for subj in subjects_list:
                if subject_lower in subj.lower():
                    expertise_score += 30
                    reasons.append(f"Studying {subj}")
                    break
            
            for subj in strong_subjects_list:
                if subject_lower in subj.lower():
                    expertise_score += 50
                    reasons.append(f"Strong in {subj}")
                    break
            
            if expertise_score == 0:
                continue
            
            # Bonuses
            if profile and profile.class_name:
                level = {"Freshman": 1, "Sophomore": 2, "Junior": 3, "Senior": 4}.get(profile.class_name, 0)
                expertise_score += level * 5
                if level >= 3:
                    reasons.append("Upper-level")
            
            if user.reputation >= 500:
                expertise_score += 15
                reasons.append(user.reputation_level)
            
            if expertise_score >= 30:
                online_status = get_user_online_status(user.id)
                expertise_level = 4 if expertise_score >= 80 else 3 if expertise_score >= 60 else 2 if expertise_score >= 40 else 1
                
                helpers.append({
                    "user": {
                        "id": user.id,
                        "username": user.username,
                        "name": user.name,
                        "avatar": user.avatar,
                        "bio": user.bio,
                        "department": profile.department if profile else None,
                        "class_level": profile.class_name if profile else None,
                        "reputation": user.reputation,
                        "reputation_level": user.reputation_level,
                        "is_online": online_status["is_online"]
                    },
                    "expertise_score": expertise_score,
                    "expertise_level": expertise_level,
                    "reason": " • ".join(reasons[:3])
                })
        
        helpers.sort(key=lambda x: x['expertise_score'], reverse=True)
        top_helpers = helpers[:10]
        
        return jsonify({
            "status": "success",
            "data": {
                "helpers": top_helpers,
                "subject": subject,
                "total": len(top_helpers)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Find help error: ", exc_info=True)
        return error_response("Failed to find help")

@connections_bp.route("/connections/<int:connection_id>/details", methods=["GET"])
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


        
@connections_bp.route("/connections/requests/received", methods=["GET"])
@token_required
def received_connection_requests(current_user):
    """Get ALL pending connection requests sent TO you (no pagination)"""
    try:
        # Get all received requests
        requests = Connection.query.filter(
            Connection.receiver_id == current_user.id,
            Connection.status == "pending"
        ).order_by(Connection.requested_at.desc()).limit(100).all()

        if not requests:
            return jsonify({"status": "success", "data": [], "total": 0})

        # OPTIMIZED: Batch-load all requesters, profiles, and onboarding in 3 queries
        # instead of 3 queries * N requests.
        requester_ids = [req.requester_id for req in requests]

        users_map = {
            u.id: u
            for u in User.query.filter(User.id.in_(requester_ids)).all()
        }
        profiles_map = {
            p.user_id: p
            for p in StudentProfile.query.filter(
                StudentProfile.user_id.in_(requester_ids)
            ).all()
        }
        onboarding_raw_map = {
            o.user_id: o
            for o in OnboardingDetails.query.filter(
                OnboardingDetails.user_id.in_(requester_ids)
            ).all()
        }

        # OPTIMIZED: Compute all mutual counts in 2 queries (vs 4 * N).
        # Load current user's connections once.
        my_conns = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()
        my_conn_ids = {
            c.receiver_id if c.requester_id == current_user.id else c.requester_id
            for c in my_conns
        }

        # Load all connections for all requesters at once.
        all_req_conns = Connection.query.filter(
            or_(
                Connection.requester_id.in_(requester_ids),
                Connection.receiver_id.in_(requester_ids)
            ),
            Connection.status == "accepted"
        ).all()

        req_conn_ids_map = {}  # {requester_id: set of their connected user_ids}
        for conn in all_req_conns:
            for uid in (conn.requester_id, conn.receiver_id):
                if uid in requester_ids:
                    other = conn.receiver_id if conn.requester_id == uid else conn.requester_id
                    req_conn_ids_map.setdefault(uid, set()).add(other)

        now = datetime.datetime.utcnow()
        requests_data = []

        for req in requests:
            requester = users_map.get(req.requester_id)
            if not requester:
                continue

            profile = profiles_map.get(requester.id)
            ob      = onboarding_raw_map.get(requester.id)

            # Build onboarding preview from pre-loaded data (no extra query)
            onboarding_preview = None
            if ob:
                onboarding_preview = {
                    "subjects":          ob.subjects[:3] if ob.subjects else [],
                    "strong_subjects":   ob.strong_subjects[:3] if ob.strong_subjects else [],
                    "help_subjects":     ob.help_subjects[:3] if ob.help_subjects else [],
                    "learning_style":    ob.learning_style,
                    "study_preferences": ob.study_preferences[:3] if ob.study_preferences else [],
                    "session_length":    ob.session_length,
                    "has_schedule":      bool(ob.study_schedule),
                }

            online_status = get_user_online_status(requester.id)

            # Time-ago calculation
            time_ago = now - req.requested_at
            if time_ago.days > 0:
                time_ago_text = f"{time_ago.days}d ago"
            elif time_ago.seconds >= 3600:
                time_ago_text = f"{time_ago.seconds // 3600}h ago"
            elif time_ago.seconds >= 60:
                time_ago_text = f"{time_ago.seconds // 60}m ago"
            else:
                time_ago_text = "Just now"

            # Mutual count via set intersection on pre-loaded data
            their_ids    = req_conn_ids_map.get(requester.id, set())
            mutual_count = len(my_conn_ids & their_ids)

            requests_data.append({
                "request_id": req.id,
                "user": {
                    "id":               requester.id,
                    "username":         requester.username,
                    "name":             requester.name,
                    "avatar":           requester.avatar,
                    "bio":              requester.bio,
                    "reputation":       requester.reputation,
                    "reputation_level": requester.reputation_level,
                    "department":       profile.department if profile else None,
                    "class_level":      profile.class_name if profile else None,
                    "is_online":        online_status["is_online"],
                    "last_active":      online_status["last_active"],
                },
                "onboarding_details": onboarding_preview,
                "message":            req.requester_notes,
                "requested_at":       req.requested_at.isoformat(),
                "time_ago":           time_ago_text,
                "mutuals_count":      mutual_count,
            })

        return jsonify({
            "status": "success",
            "data": requests_data,
            "total": len(requests_data),
        })

    except Exception as e:
        current_app.logger.error(f"Received requests error: ", exc_info=True)
        return error_response("Failed to load received requests")


# ============================================================================
# 2. SENT CONNECTION REQUESTS - NO PAGINATION
# ============================================================================
@connections_bp.route("/connections/requests/sent", methods=["GET"])
@token_required
def sent_connection_requests(current_user):
    """Get ALL pending connection requests YOU sent (no pagination)"""
    try:
        # Get all sent requests
        requests = Connection.query.filter(
            Connection.requester_id == current_user.id,
            Connection.status == "pending"
        ).order_by(Connection.requested_at.desc()).limit(100).all()

        if not requests:
            return jsonify({"status": "success", "data": [], "total": 0})

        # OPTIMIZED: Batch-load all receivers, profiles, and onboarding in 3 queries
        # instead of 3 queries * N requests.
        receiver_ids = [req.receiver_id for req in requests]

        users_map = {
            u.id: u
            for u in User.query.filter(User.id.in_(receiver_ids)).all()
        }
        profiles_map = {
            p.user_id: p
            for p in StudentProfile.query.filter(
                StudentProfile.user_id.in_(receiver_ids)
            ).all()
        }
        onboarding_raw_map = {
            o.user_id: o
            for o in OnboardingDetails.query.filter(
                OnboardingDetails.user_id.in_(receiver_ids)
            ).all()
        }

        # OPTIMIZED: Compute all mutual counts in 2 queries (vs 4 * N).
        # Load current user's connections once.
        my_conns = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()
        my_conn_ids = {
            c.receiver_id if c.requester_id == current_user.id else c.requester_id
            for c in my_conns
        }

        # Load all connections for all receivers at once.
        all_recv_conns = Connection.query.filter(
            or_(
                Connection.requester_id.in_(receiver_ids),
                Connection.receiver_id.in_(receiver_ids)
            ),
            Connection.status == "accepted"
        ).all()

        recv_conn_ids_map = {}  # {receiver_id: set of their connected user_ids}
        for conn in all_recv_conns:
            for uid in (conn.requester_id, conn.receiver_id):
                if uid in receiver_ids:
                    other = conn.receiver_id if conn.requester_id == uid else conn.requester_id
                    recv_conn_ids_map.setdefault(uid, set()).add(other)

        now = datetime.datetime.utcnow()
        requests_data = []

        for req in requests:
            receiver = users_map.get(req.receiver_id)
            if not receiver:
                continue

            profile = profiles_map.get(receiver.id)
            ob      = onboarding_raw_map.get(receiver.id)

            # Build onboarding preview from pre-loaded data (no extra query)
            onboarding_preview = None
            if ob:
                onboarding_preview = {
                    "subjects":          ob.subjects[:3] if ob.subjects else [],
                    "strong_subjects":   ob.strong_subjects[:3] if ob.strong_subjects else [],
                    "help_subjects":     ob.help_subjects[:3] if ob.help_subjects else [],
                    "learning_style":    ob.learning_style,
                    "study_preferences": ob.study_preferences[:3] if ob.study_preferences else [],
                    "session_length":    ob.session_length,
                    "has_schedule":      bool(ob.study_schedule),
                }

            online_status = get_user_online_status(receiver.id)

            # Time-ago calculation
            time_ago = now - req.requested_at
            if time_ago.days > 0:
                time_ago_text = f"{time_ago.days}d"
            elif time_ago.seconds >= 3600:
                time_ago_text = f"{time_ago.seconds // 3600}h"
            elif time_ago.seconds >= 60:
                time_ago_text = f"{time_ago.seconds // 60}m"
            else:
                time_ago_text = "Just now"

            # Mutual count via set intersection on pre-loaded data
            their_ids    = recv_conn_ids_map.get(receiver.id, set())
            mutual_count = len(my_conn_ids & their_ids)

            requests_data.append({
                "request_id": req.id,
                "user": {
                    "id":               receiver.id,
                    "username":         receiver.username,
                    "name":             receiver.name,
                    "avatar":           receiver.avatar,
                    "bio":              receiver.bio,
                    "reputation":       receiver.reputation,
                    "reputation_level": receiver.reputation_level,
                    "department":       profile.department if profile else None,
                    "class_level":      profile.class_name if profile else None,
                    "is_online":        online_status["is_online"],
                    "last_active":      online_status["last_active"],
                },
                "onboarding_details": onboarding_preview,
                "your_message":  req.requester_notes,
                "requested_at":  req.requested_at.isoformat(),
                "time_ago":      time_ago_text,
                "mutuals_count": mutual_count,
            })

        return jsonify({
            "status": "success",
            "data": requests_data,
            "total": len(requests_data),
        })

    except Exception as e:
        current_app.logger.error(f"Sent requests error: ", exc_info=True)
        return error_response("Failed to load sent requests")


# ============================================================================
# 3. CONNECTED USERS LIST - NO PAGINATION
# ============================================================================
@connections_bp.route("/connections/list", methods=["GET"])
@token_required
def list_connections(current_user):
    """List ALL connections (no pagination, max 200)"""
    try:
        all_connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).limit(200).all()

        if not all_connections:
            return jsonify({"status": "success", "data": [], "total": 0})

        connected_ids  = []
        connection_map = {}

        for c in all_connections:
            other_id = c.receiver_id if c.requester_id == current_user.id else c.requester_id
            connected_ids.append(other_id)
            connection_map[other_id] = c

        # OPTIMIZED: Batch-load all connected users, profiles, and onboarding in 3 queries
        # instead of 2 queries * N connections.
        users_map = {
            u.id: u
            for u in User.query.filter(User.id.in_(connected_ids)).all()
        }
        profiles_map = {
            p.user_id: p
            for p in StudentProfile.query.filter(
                StudentProfile.user_id.in_(connected_ids)
            ).all()
        }
        onboarding_raw_map = {
            o.user_id: o
            for o in OnboardingDetails.query.filter(
                OnboardingDetails.user_id.in_(connected_ids)
            ).all()
        }

        # OPTIMIZED: Pre-load current user's ThreadMembers once for health scoring.
        my_thread_ids = {
            t.thread_id
            for t in ThreadMember.query.filter_by(student_id=current_user.id).all()
        }

        # Batch-load ThreadMembers for all connected users at once.
        all_partner_thread_memberships = ThreadMember.query.filter(
            ThreadMember.student_id.in_(connected_ids)
        ).all()

        partner_thread_ids_map = {}  # {user_id: set of thread_ids}
        for tm in all_partner_thread_memberships:
            partner_thread_ids_map.setdefault(tm.student_id, set()).add(tm.thread_id)

        now = datetime.datetime.utcnow()
        connections_data = []

        for other_id in connected_ids:
            user_obj   = users_map.get(other_id)
            connection = connection_map.get(other_id)
            if not user_obj or not connection:
                continue

            profile = profiles_map.get(other_id)

            # Build onboarding preview from pre-loaded data (no extra query)
            ob = onboarding_raw_map.get(other_id)
            onboarding_preview = {}
            if ob:
                onboarding_preview = {
                    "subjects":          ob.subjects[:3] if ob.subjects else [],
                    "strong_subjects":   ob.strong_subjects[:3] if ob.strong_subjects else [],
                    "help_subjects":     ob.help_subjects[:3] if ob.help_subjects else [],
                    "learning_style":    ob.learning_style,
                    "study_preferences": ob.study_preferences[:3] if ob.study_preferences else [],
                    "session_length":    ob.session_length,
                    "has_schedule":      bool(ob.study_schedule),
                }

            online_status = get_user_online_status(other_id)

            # OPTIMIZED: Inline health score using pre-loaded thread memberships.
            their_thread_ids = partner_thread_ids_map.get(other_id, set())
            shared_threads   = len(my_thread_ids & their_thread_ids)

            last_interaction = connection.responded_at or connection.requested_at
            days_since       = (now - last_interaction).days if last_interaction else 999

            health_score = 100
            if days_since > 30:
                health_score -= 40
            elif days_since > 14:
                health_score -= 20
            elif days_since > 7:
                health_score -= 10
            health_score += min(shared_threads * 10, 30)
            health_score = max(0, min(100, health_score))

            if health_score < 40:
                suggestion = "💤 Haven't connected in a while. Send them a message!"
            elif health_score < 70:
                suggestion = "👍 Good connection. Schedule a study session?"
            else:
                suggestion = "🔥 Strong connection! Keep it up."

            connections_data.append({
                "id": connection.id,
                "user": {
                    "id":               user_obj.id,
                    "username":         user_obj.username,
                    "name":             user_obj.name,
                    "avatar":           user_obj.avatar,
                    "bio":              user_obj.bio,
                    "department":       profile.department if profile else None,
                    "class_level":      profile.class_name if profile else None,
                    "reputation":       user_obj.reputation,
                    "reputation_level": user_obj.reputation_level,
                    "is_online":        online_status["is_online"],
                    "last_active":      online_status["last_active"],
                },
                "onboarding_details": onboarding_preview,
                "connected_at":  connection.responded_at.isoformat() if connection.responded_at else None,
                "health_score":  health_score,
                "suggestion":    suggestion,
                "shared_threads": shared_threads,
            })

        return jsonify({
            "status": "success",
            "data": connections_data,
            "total": len(connections_data),
        })

    except Exception as e:
        current_app.logger.error(f"List connections error: ", exc_info=True)
        return error_response("Failed to load connections")


# ============================================================================
# 4. MUTUAL CONNECTIONS - NO PAGINATION
# ============================================================================
@connections_bp.route("/connections/mutual/<int:user_id>", methods=["GET"])
@token_required
def get_mutual_connections(current_user, user_id):
    """Get ALL mutual connections with another user (no pagination)"""
    try:
        other_user = User.query.get(user_id)
        if not other_user:
            return error_response("User not found")

        if user_id == current_user.id:
            return error_response("Cannot get mutual connections with yourself")

        # Get YOUR connections
        your_connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()

        # Map also preserves the Connection object for connected_at
        your_conn_map = {}  # {other_user_id: Connection}
        for conn in your_connections:
            other_id = conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
            your_conn_map[other_id] = conn

        your_connection_ids = set(your_conn_map.keys())

        # Get THEIR connections
        their_connections = Connection.query.filter(
            or_(
                Connection.requester_id == user_id,
                Connection.receiver_id == user_id
            ),
            Connection.status == "accepted"
        ).all()

        their_connection_ids = {
            conn.receiver_id if conn.requester_id == user_id else conn.requester_id
            for conn in their_connections
        }

        # Mutual IDs
        mutual_ids = your_connection_ids & their_connection_ids

        if not mutual_ids:
            return jsonify({
                "status": "success",
                "data": {
                    "mutual_connections": [],
                    "count": 0,
                    "other_user": {
                        "id":       other_user.id,
                        "username": other_user.username,
                        "name":     other_user.name,
                    },
                },
            })

        # OPTIMIZED: Batch-load mutual users, profiles, onboarding in 3 queries
        # instead of 3 queries * N mutuals.
        mutual_users = (
            User.query.filter(User.id.in_(mutual_ids))
            .order_by(User.reputation.desc())
            .limit(50)
            .all()
        )
        limited_mutual_ids = [u.id for u in mutual_users]

        profiles_map = {
            p.user_id: p
            for p in StudentProfile.query.filter(
                StudentProfile.user_id.in_(limited_mutual_ids)
            ).all()
        }
        onboarding_raw_map = {
            o.user_id: o
            for o in OnboardingDetails.query.filter(
                OnboardingDetails.user_id.in_(limited_mutual_ids)
            ).all()
        }

        mutual_data = []

        for mutual_user in mutual_users:
            profile = profiles_map.get(mutual_user.id)
            ob      = onboarding_raw_map.get(mutual_user.id)

            # Build onboarding preview from pre-loaded data (no extra query)
            onboarding_preview = {}
            if ob:
                onboarding_preview = {
                    "subjects":          ob.subjects[:3] if ob.subjects else [],
                    "strong_subjects":   ob.strong_subjects[:3] if ob.strong_subjects else [],
                    "help_subjects":     ob.help_subjects[:3] if ob.help_subjects else [],
                    "learning_style":    ob.learning_style,
                    "study_preferences": ob.study_preferences[:3] if ob.study_preferences else [],
                    "session_length":    ob.session_length,
                    "has_schedule":      bool(ob.study_schedule),
                }

            online_status = get_user_online_status(mutual_user.id)

            # Use the already-loaded connection object (no extra query)
            your_connection = your_conn_map.get(mutual_user.id)

            mutual_data.append({
                "id": your_connection.id if your_connection else None,
                "user": {
                    "id":               mutual_user.id,
                    "username":         mutual_user.username,
                    "name":             mutual_user.name,
                    "avatar":           mutual_user.avatar,
                    "bio":              mutual_user.bio,
                    "reputation":       mutual_user.reputation,
                    "reputation_level": mutual_user.reputation_level,
                    "department":       profile.department if profile else None,
                    "class_level":      profile.class_name if profile else None,
                    "is_online":        online_status["is_online"],
                    "last_active":      online_status["last_active"],
                },
                "onboarding_details": onboarding_preview,
                "connected_at": (
                    your_connection.responded_at.isoformat()
                    if your_connection and your_connection.responded_at
                    else None
                ),
            })

        return jsonify({
            "status": "success",
            "data": {
                "mutual_connections": mutual_data,
                "count":    len(mutual_ids),
                "showing":  len(mutual_data),
                "other_user": {
                    "id":       other_user.id,
                    "username": other_user.username,
                    "name":     other_user.name,
                    "avatar":   other_user.avatar,
                },
            },
        })

    except Exception as e:
        current_app.logger.error(f"Mutual connections error: ", exc_info=True)
        return error_response("Failed to get mutual connections")



@connections_bp.route("/connections/suggestions", methods=["GET"])
@token_required
def connection_suggestions(current_user):
    """Get connection suggestions with study partners and mentors"""
    logger.info(f"[connection_suggestions] start user_id={current_user.id}")

    try:
        profile    = StudentProfile.query.filter_by(user_id=current_user.id).first()
        onboarding = OnboardingDetails.query.filter_by(user_id=current_user.id).first()

        if not profile:
            logger.warning(f"[connection_suggestions] no profile found for user_id={current_user.id}")
            return error_response("Profile not found", 404)

        if not onboarding:
            logger.info(f"[connection_suggestions] user_id={current_user.id} has no onboarding details — "
                        f"scoring will be skipped for both categories")

        # Build excluded IDs from existing connections
        existing_connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            )
        ).all()
        logger.debug(f"[connection_suggestions] user_id={current_user.id} "
                     f"existing_connections_count={len(existing_connections)}")

        excluded_ids = {current_user.id}
        for conn in existing_connections:
            other_id = conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
            excluded_ids.add(other_id)

        logger.debug(f"[connection_suggestions] user_id={current_user.id} excluded_ids_count={len(excluded_ids)}")

        # OPTIMIZED: Single 3-way join fetches all candidates with profiles and
        # onboarding at once — replaces 2 per-candidate queries inside the loop.
        candidates_data = (
            db.session.query(User, StudentProfile, OnboardingDetails)
            .join(StudentProfile, StudentProfile.user_id == User.id)
            .join(OnboardingDetails, OnboardingDetails.user_id == User.id)
            .filter(
                User.id.notin_(excluded_ids),
                User.status == "approved",
            )
            .limit(100)
            .all()
        )

        logger.info(f"[connection_suggestions] user_id={current_user.id} "
                    f"candidates_fetched={len(candidates_data)}")

        if not candidates_data:
            logger.info(f"[connection_suggestions] user_id={current_user.id} no candidates found, returning empty result")
            return jsonify({
                "status": "success",
                "data": {"study_partners": [], "mentors": [], "total": 0},
            })

        # OPTIMIZED: Pre-load current user's connections for mutual count computation.
        # We compute mutuals in batch (2 queries total) for all qualifying candidates
        # instead of 4 queries per candidate.
        my_conns = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()
        my_conn_ids = {
            c.receiver_id if c.requester_id == current_user.id else c.requester_id
            for c in my_conns
        }
        logger.debug(f"[connection_suggestions] user_id={current_user.id} "
                     f"accepted_connections_count={len(my_conn_ids)}")

        class_hierarchy = {
            "Freshman": 1, "Sophomore": 2, "Junior": 3, "Senior": 4,
            "100 Level": 1, "200 Level": 2, "300 Level": 3, "400 Level": 4, "500 Level": 5,
        }
        current_level = class_hierarchy.get(profile.class_name, 0) if profile.class_name else 0
        logger.debug(f"[connection_suggestions] user_id={current_user.id} "
                     f"class_name={profile.class_name!r} current_level={current_level}")

        # First pass: score all candidates (no DB hits)
        study_partners_raw = []
        mentors_raw        = []

        for candidate, cand_profile, cand_onboarding in candidates_data:
            # ---- STUDY PARTNER SCORING ----
            if onboarding:
                sp_score   = 0
                sp_reasons = []

                if profile.department and cand_profile.department == profile.department:
                    sp_score += 30
                    sp_reasons.append(f"Same major: {profile.department}")

                if profile.class_name and cand_profile.class_name == profile.class_name:
                    sp_score += 10
                    sp_reasons.append(f"Same class: {profile.class_name}")

                if onboarding.subjects and cand_onboarding.subjects:
                    common = (
                        set(s.lower().strip() for s in onboarding.subjects)
                        & set(s.lower().strip() for s in cand_onboarding.subjects)
                    )
                    if common:
                        sp_score += min(len(common) * 8, 25)
                        sp_reasons.append(f"Studying: {', '.join(list(common)[:2])}")

                if (
                    onboarding.learning_style
                    and cand_onboarding.learning_style
                    and onboarding.learning_style == cand_onboarding.learning_style
                ):
                    sp_score += 10
                    sp_reasons.append("Similar learning style")

                logger.debug(f"[connection_suggestions] sp_score candidate_id={candidate.id} "
                             f"score={sp_score} reasons={sp_reasons}")

                if sp_score >= 30:
                    study_partners_raw.append((candidate, cand_profile, cand_onboarding, sp_score, sp_reasons))

            # ---- MENTOR SCORING ----
            if onboarding and current_level > 0:
                cand_level = class_hierarchy.get(cand_profile.class_name, 0)
                if (
                    cand_level > current_level
                    and cand_profile.department == profile.department
                ):
                    m_score   = 20
                    m_reasons = [f"Same major: {profile.department}"]

                    level_diff = cand_level - current_level
                    m_score += min(level_diff * 15, 30)
                    m_reasons.append(f"Higher class level: {cand_profile.class_name}")

                    if onboarding.help_subjects and cand_onboarding.strong_subjects:
                        helpful = (
                            set(s.lower().strip() for s in onboarding.help_subjects)
                            & set(s.lower().strip() for s in cand_onboarding.strong_subjects)
                        )
                        if helpful:
                            m_score += min(len(helpful) * 10, 25)
                            m_reasons.append(f"Can help with: {', '.join(list(helpful)[:2])}")

                    if candidate.reputation >= 500:
                        m_score += 10
                        m_reasons.append("Highly rated")

                    logger.debug(f"[connection_suggestions] mentor_score candidate_id={candidate.id} "
                                 f"score={m_score} reasons={m_reasons}")

                    if m_score >= 40:
                        mentors_raw.append((candidate, cand_profile, cand_onboarding, m_score, m_reasons))

        logger.info(f"[connection_suggestions] user_id={current_user.id} "
                    f"study_partners_qualified={len(study_partners_raw)} "
                    f"mentors_qualified={len(mentors_raw)}")

        # Sort early and limit before batch mutual-count lookup
        study_partners_raw.sort(key=lambda x: x[3], reverse=True)
        study_partners_raw = study_partners_raw[:10]

        mentors_raw.sort(key=lambda x: x[3], reverse=True)
        mentors_raw = mentors_raw[:10]

        logger.debug(f"[connection_suggestions] user_id={current_user.id} "
                     f"study_partners_after_limit={len(study_partners_raw)} "
                     f"mentors_after_limit={len(mentors_raw)}")

        qualifying_ids = list({r[0].id for r in study_partners_raw + mentors_raw})
        logger.debug(f"[connection_suggestions] user_id={current_user.id} "
                     f"qualifying_ids_count={len(qualifying_ids)}")

        # OPTIMIZED: One query to get all connections for qualifying candidates
        if qualifying_ids:
            all_cand_conns = Connection.query.filter(
                or_(
                    Connection.requester_id.in_(qualifying_ids),
                    Connection.receiver_id.in_(qualifying_ids)
                ),
                Connection.status == "accepted"
            ).all()
            logger.debug(f"[connection_suggestions] user_id={current_user.id} "
                         f"candidate_connections_fetched={len(all_cand_conns)}")

            cand_conn_ids_map = {}
            for conn in all_cand_conns:
                for uid in (conn.requester_id, conn.receiver_id):
                    if uid in qualifying_ids:
                        other = conn.receiver_id if conn.requester_id == uid else conn.requester_id
                        cand_conn_ids_map.setdefault(uid, set()).add(other)
        else:
            cand_conn_ids_map = {}

        def build_entry(candidate, cand_profile, cand_onboarding, score, reasons, category):
            online_status = get_user_online_status(candidate.id)
            their_ids     = cand_conn_ids_map.get(candidate.id, set())
            mutual_count  = len(my_conn_ids & their_ids)
            logger.debug(f"[connection_suggestions] build_entry category={category} "
                         f"candidate_id={candidate.id} score={score} mutual_count={mutual_count} "
                         f"is_online={online_status['is_online']}")
            return {
                "category": category,
                "user": {
                    "id":               candidate.id,
                    "username":         candidate.username,
                    "name":             candidate.name,
                    "avatar":           candidate.avatar,
                    "bio":              candidate.bio,
                    "department":       cand_profile.department,
                    "class_level":      cand_profile.class_name,
                    "reputation":       candidate.reputation,
                    "reputation_level": candidate.reputation_level,
                    "is_online":        online_status["is_online"],
                    "last_active":      online_status["last_active"],
                },
                "onboarding_details": {
                    "subjects":    cand_onboarding.subjects[:5] if cand_onboarding.subjects else [],
                    "study_style": cand_onboarding.learning_style,
                },
                "mutuals_count": mutual_count,
                "match_score":   min(score, 100),
                "reasons":       reasons[:4],
            }

        study_partners = [build_entry(*r, "study_partner") for r in study_partners_raw]
        mentors        = [build_entry(*r, "mentor")        for r in mentors_raw]

        logger.info(f"[connection_suggestions] user_id={current_user.id} completed successfully "
                    f"study_partners_returned={len(study_partners)} mentors_returned={len(mentors)}")

        return jsonify({
            "status": "success",
            "data": {
                "study_partners": study_partners,
                "mentors":        mentors,
                "total":          len(study_partners) + len(mentors),
            },
        })

    except Exception as e:
        logger.error(f"[connection_suggestions] user_id={getattr(current_user, 'id', 'unknown')} "
                     f"failed with error: {e}", exc_info=True)
        return error_response("Failed to load suggestions")

# ============================================================================
# 6. SEARCH USERS - NO PAGINATION
# ============================================================================
@connections_bp.route("/connections/search", methods=["GET"])
@token_required
def search_users(current_user):
    """Search for users (no pagination, max 50 results)"""
    try:
        search_term = request.args.get("search", "").strip()
        
        if not search_term or len(search_term) < 2:
            return error_response("Please provide at least 2 characters to search", 400)
        
        # Get blocked user IDs
        blocked_connections = Connection.query.filter(
            or_(
                and_(Connection.receiver_id == current_user.id, Connection.status == "blocked"),
                and_(Connection.requester_id == current_user.id, Connection.status == "blocked")
            )
        ).all()
        
        excluded_ids = [current_user.id]
        for conn in blocked_connections:
            blocked_id = conn.requester_id if conn.receiver_id == current_user.id else conn.receiver_id
            excluded_ids.append(blocked_id)
        
        # Search query
        users = User.query.filter(
            User.id.notin_(excluded_ids),
            User.status == "approved",
            or_(
                User.name.ilike(f"%{search_term}%"),
                User.username.ilike(f"%{search_term}%")
            )
        ).limit(50).all()
        
        # Get connection statuses
        user_ids = [user.id for user in users]
        connections = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id.in_(user_ids)),
                and_(Connection.requester_id.in_(user_ids), Connection.receiver_id == current_user.id)
            )
        ).all()
        
        connection_map = {}
        for conn in connections:
            other_id = conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
            connection_map[other_id] = {
                "connection_id": conn.id,
                "status": conn.status,
                "is_requester": conn.requester_id == current_user.id
            }
        
        users_data = []
        for user in users:
            profile = StudentProfile.query.filter_by(user_id=user.id).first()
            conn_info = connection_map.get(user.id, {})
            online_status = get_user_online_status(user.id)
            
            # Determine connection status
            if not conn_info:
                connection_status = "none"
                can_connect = True
            elif conn_info["status"] == "accepted":
                connection_status = "connected"
                can_connect = False
            elif conn_info["status"] == "pending":
                connection_status = "pending_sent" if conn_info["is_requester"] else "pending_received"
                can_connect = False
            elif conn_info["status"] == "blocked":
                connection_status = "blocked"
                can_connect = False
            else:
                connection_status = "rejected"
                can_connect = True
            
            users_data.append({
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "avatar": user.avatar,
                "bio": user.bio,
                "department": profile.department if profile else None,
                "class_level": profile.class_name if profile else None,
                "reputation": user.reputation,
                "reputation_level": user.reputation_level,
                "is_online": online_status["is_online"],
                "last_active": online_status["last_active"],
                "connection_status": connection_status,
                "connection_id": conn_info.get("connection_id"),
                "can_connect": can_connect
            })
        
        return jsonify({
            "status": "success",
            "data": {
                "users": users_data,
                "total": len(users_data),
                "search_term": search_term
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Search users error: ", exc_info=True)
        return error_response("Failed to search users")

# ============================================================================
def _get_generic_user_suggestions(current_user, limit=20, message=None):
    """
    Fallback function to suggest high-quality generic users when no mutual connections exist
    
    Prioritizes:
    1. Same department
    2. High reputation
    3. Active users
    4. Similar subjects/interests
    """
    try:
        user_profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
        user_onboarding = OnboardingDetails.query.filter_by(user_id=current_user.id).first()
        
        # Get existing connections to exclude
        existing_connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            )
        ).all()
        
        excluded_ids = [current_user.id]
        for conn in existing_connections:
            other_id = conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
            excluded_ids.append(other_id)
        
        # ========================================
        # QUERY: Get potential candidates
        # ========================================
        
        # Start with base query: approved users not in excluded list
        candidates_query = User.query.filter(
            User.id.notin_(excluded_ids),
            User.status == "approved"
        )
        
        # Join with student profile for department filtering
        candidates_query = candidates_query.join(StudentProfile, StudentProfile.user_id == User.id, isouter=True)
        
        # Prioritize: same department, high reputation, recent activity
        if user_profile and user_profile.department:
            # Same department users first
            candidates_query = candidates_query.order_by(
                case(
                    (StudentProfile.department == user_profile.department, 1),
                    else_=2
                ),
                User.reputation.desc(),
                User.last_active.desc().nullslast()
            )
        else:
            # No department, just use reputation and activity
            candidates_query = candidates_query.order_by(
                User.reputation.desc(),
                User.last_active.desc().nullslast()
            )
        
        # Get top candidates (more than needed for scoring)
        candidates = candidates_query.limit(limit * 3).all()
        
        if not candidates:
            return jsonify({
                "status": "success",
                "data": {
                    "discoveries": [],
                    "total": 0,
                    "showing": 0,
                    "discovery_type": "generic",
                    "message": message or "No users available for suggestions at this time"
                }
            })

        # ========================================
        # BATCH LOAD profiles and onboarding (eliminates N+1)
        # ========================================
        candidate_ids = [c.id for c in candidates]

        profiles_map = {
            p.user_id: p
            for p in StudentProfile.query.filter(
                StudentProfile.user_id.in_(candidate_ids)
            ).all()
        }
        onboarding_map = {
            o.user_id: o
            for o in OnboardingDetails.query.filter(
                OnboardingDetails.user_id.in_(candidate_ids)
            ).all()
        }

        now = datetime.datetime.utcnow()

        # ========================================
        # SCORE CANDIDATES (zero DB hits)
        # ========================================

        scored = []

        for candidate in candidates:
            candidate_profile = profiles_map.get(candidate.id)
            candidate_onboarding = onboarding_map.get(candidate.id)

            score = 0
            match_reasons = []

            # 1. Same department (40 points)
            if user_profile and candidate_profile:
                if user_profile.department and candidate_profile.department == user_profile.department:
                    score += 40
                    match_reasons.append(f"Same department: {user_profile.department}")

            # 2. Similar subjects (30 points)
            if user_onboarding and candidate_onboarding:
                if user_onboarding.subjects and candidate_onboarding.subjects:
                    common = (
                        set(s.lower().strip() for s in user_onboarding.subjects)
                        & set(s.lower().strip() for s in candidate_onboarding.subjects)
                    )
                    if common:
                        score += min(len(common) * 10, 30)
                        match_reasons.append(f"Common interests: {', '.join(list(common)[:2])}")

            # 3. High reputation (20 points)
            if candidate.reputation > 500:
                score += 20
                match_reasons.append(f"Experienced: {candidate.reputation_level}")
            elif candidate.reputation > 200:
                score += 10

            # 4. Active user (10 points)
            if candidate.last_active:
                days_ago = (now - candidate.last_active).days
                if days_ago < 1:
                    score += 10
                    match_reasons.append("Active today")
                elif days_ago < 7:
                    score += 5

            if score > 0:
                scored.append((candidate, candidate_profile, candidate_onboarding, score, match_reasons))

        # Sort and limit before batch connection lookup
        scored.sort(key=lambda x: x[3], reverse=True)
        scored = scored[:limit]

        # ========================================
        # BATCH LOAD connection statuses for qualifying candidates
        # ========================================
        qualifying_ids = [s[0].id for s in scored]
        existing_requests_map = {}

        if qualifying_ids:
            existing_reqs = Connection.query.filter(
                or_(
                    and_(Connection.requester_id == current_user.id,
                         Connection.receiver_id.in_(qualifying_ids)),
                    and_(Connection.requester_id.in_(qualifying_ids),
                         Connection.receiver_id == current_user.id)
                )
            ).all()
            for req in existing_reqs:
                other_id = req.receiver_id if req.requester_id == current_user.id else req.requester_id
                existing_requests_map[other_id] = req

        suggestions = []

        for candidate, candidate_profile, candidate_onboarding, score, match_reasons in scored:
            existing_request = existing_requests_map.get(candidate.id)
            online_status = get_user_online_status(candidate.id)

            if existing_request:
                if existing_request.status == "pending":
                    request_status = "pending_sent" if existing_request.requester_id == current_user.id else "pending_received"
                    can_connect = False
                elif existing_request.status == "blocked":
                    continue  # Skip blocked users
                elif existing_request.status == "rejected":
                    request_status = "rejected"
                    can_connect = True
                else:
                    request_status = existing_request.status
                    can_connect = False
                connection_id = existing_request.id
            else:
                request_status = "none"
                can_connect = True
                connection_id = None

            # Build onboarding preview from already-loaded data (no extra DB call)
            ob_preview = {}
            if candidate_onboarding:
                ob_preview = {
                    "subjects": candidate_onboarding.subjects[:3] if candidate_onboarding.subjects else [],
                    "strong_subjects": candidate_onboarding.strong_subjects[:3] if candidate_onboarding.strong_subjects else [],
                    "help_subjects": candidate_onboarding.help_subjects[:3] if candidate_onboarding.help_subjects else [],
                    "learning_style": candidate_onboarding.learning_style,
                    "study_preferences": candidate_onboarding.study_preferences[:3] if candidate_onboarding.study_preferences else [],
                    "session_length": candidate_onboarding.session_length,
                    "has_schedule": bool(candidate_onboarding.study_schedule),
                }

            suggestions.append({
                "user": {
                    "id": candidate.id,
                    "username": candidate.username,
                    "name": candidate.name,
                    "avatar": candidate.avatar,
                    "bio": candidate.bio,
                    "reputation": candidate.reputation,
                    "reputation_level": candidate.reputation_level,
                    "department": candidate_profile.department if candidate_profile else None,
                    "class_level": candidate_profile.class_name if candidate_profile else None,
                    "is_online": online_status["is_online"],
                    "last_active": online_status["last_active"]
                },
                "onboarding_details": ob_preview,
                "mutuals_count": 0,
                "sample_mutuals": [],
                "match_score": score,
                "match_reasons": match_reasons[:3],
                "request_status": request_status,
                "can_connect": can_connect,
                "connection_id": connection_id,
                "discovery_type": "generic"
            })
        
        # Sort by score and limit
        suggestions.sort(key=lambda x: x["match_score"], reverse=True)
        suggestions = suggestions[:limit]
        
        return jsonify({
            "status": "success",
            "data": {
                "discoveries": suggestions,
                "total": len(suggestions),
                "showing": len(suggestions),
                "discovery_type": "generic",
                "message": message or "Here are some suggested connections based on your profile"
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Generic suggestions error: ", exc_info=True)
        return jsonify({
            "status": "success",
            "data": {
                "discoveries": [],
                "total": 0,
                "showing": 0,
                "discovery_type": "generic",
                "message": "Unable to load suggestions at this time"
            }
        })
@connections_bp.route("/connections/mutuals/discover", methods=["GET"])
@token_required
def discover_mutual_connections(current_user):
    """
    Discover people in your extended network (friends of friends)
    Returns ALL qualified users you're NOT yet connected with
    No pagination - returns up to 100 results sorted by mutual connection count
    
    FALLBACK: If no mutual connections found, returns high-quality generic users
    
    Query params:
    - min_mutuals: Minimum mutual connections required (default: 1)
    """
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")
        
        min_mutuals = request.args.get("min_mutuals", 1, type=int)
        
        # ========================================
        # STEP 1: Get YOUR direct connections
        # ========================================
        your_connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()
        
        your_connection_ids = set()
        for conn in your_connections:
            other_id = conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
            your_connection_ids.add(other_id)
        
        # ========================================
        # FALLBACK: If user has no connections, suggest generic users
        # ========================================
        if not your_connection_ids:
            return _get_generic_user_suggestions(current_user, limit=20)
        
        # ========================================
        # STEP 2: Get connections of YOUR connections
        # ========================================
        their_connections = Connection.query.filter(
            or_(
                Connection.requester_id.in_(your_connection_ids),
                Connection.receiver_id.in_(your_connection_ids)
            ),
            Connection.status == "accepted"
        ).all()
        
        # Count mutual connections for each potential user
        mutual_counts = {}  # {user_id: count}
        mutual_friends = {}  # {user_id: [friend_ids]}
        
        for conn in their_connections:
            # Determine which of your friends is in this connection
            if conn.requester_id in your_connection_ids:
                your_friend_id = conn.requester_id
                potential_user_id = conn.receiver_id
            else:
                your_friend_id = conn.receiver_id
                potential_user_id = conn.requester_id
            
            # Skip if it's you or already your connection
            if potential_user_id == current_user.id or potential_user_id in your_connection_ids:
                continue
            
            # Increment mutual count and track which friends
            mutual_counts[potential_user_id] = mutual_counts.get(potential_user_id, 0) + 1
            
            if potential_user_id not in mutual_friends:
                mutual_friends[potential_user_id] = []
            mutual_friends[potential_user_id].append(your_friend_id)
        
        # Filter by minimum mutual connections
        qualified_ids = [
            user_id for user_id, count in mutual_counts.items()
            if count >= min_mutuals
        ]
        
        # ========================================
        # FALLBACK 2: If no mutual connections found, suggest generic users
        # ========================================
        if not qualified_ids:
            return _get_generic_user_suggestions(
                current_user, 
                limit=20,
                message=f"No users found with at least {min_mutuals} mutual connection(s). Here are some suggestions based on your interests:"
            )
        
        # Sort by mutual count (highest first)
        sorted_ids = sorted(qualified_ids, key=lambda x: mutual_counts[x], reverse=True)
        
        # Apply hard limit of 100 results
        limited_ids = sorted_ids[:100]
        
        # ========================================
        # STEP 3: Get full user details
        # ========================================
        potential_users = User.query.filter(User.id.in_(limited_ids)).all()
        
        # Create a map for quick lookup
        users_map = {u.id: u for u in potential_users}
        
        # Check for existing requests with these users
        existing_requests = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id.in_(limited_ids)),
                and_(Connection.requester_id.in_(limited_ids), Connection.receiver_id == current_user.id)
            )
        ).all()
        
        request_map = {}
        for req in existing_requests:
            other_id = req.receiver_id if req.requester_id == current_user.id else req.requester_id
            request_map[other_id] = {
                "status": req.status,
                "is_requester": req.requester_id == current_user.id,
                "connection_id": req.id
            }
        
        # ========================================
        # STEP 4: Build response with sample mutual friends
        # ========================================
        discoveries = []
        
        for user_id in limited_ids:
            potential_user = users_map.get(user_id)
            if not potential_user:
                continue
            
            profile = StudentProfile.query.filter_by(user_id=user_id).first()
            onboarding = get_user_onboarding_preview(user_id)
            online_status = get_user_online_status(user_id)
            
            # Get sample of mutual friends (up to 3)
            friend_ids = mutual_friends.get(user_id, [])[:3]
            sample_mutuals = []
            
            for friend_id in friend_ids:
                mutual_user = User.query.get(friend_id)
                if mutual_user:
                    sample_mutuals.append({
                        "id": mutual_user.id,
                        "username": mutual_user.username,
                        "name": mutual_user.name,
                        "avatar": mutual_user.avatar
                    })
            
            # Determine connection/request status
            request_info = request_map.get(user_id, {})
            
            if not request_info:
                request_status = "none"
                can_connect = True
            elif request_info["status"] == "pending":
                request_status = "pending_sent" if request_info["is_requester"] else "pending_received"
                can_connect = False
            elif request_info["status"] == "rejected":
                request_status = "rejected"
                can_connect = True
            elif request_info["status"] == "blocked":
                request_status = "blocked"
                can_connect = False
            else:
                request_status = "unknown"
                can_connect = True
            
            discovery_data = {
                "user": {
                    "id": potential_user.id,
                    "username": potential_user.username,
                    "name": potential_user.name,
                    "avatar": potential_user.avatar,
                    "bio": potential_user.bio,
                    "reputation": potential_user.reputation,
                    "reputation_level": potential_user.reputation_level,
                    "department": profile.department if profile else None,
                    "class_level": profile.class_name if profile else None,
                    "is_online": online_status["is_online"],
                    "last_active": online_status["last_active"]
                },
                "onboarding_details": onboarding or {},
                "mutuals_count": mutual_counts[user_id],
                "sample_mutuals": sample_mutuals,
                "request_status": request_status,
                "can_connect": can_connect,
                "connection_id": request_info.get("connection_id"),
                "discovery_type": "mutual"  # Indicator this is from mutual connections
            }
            
            discoveries.append(discovery_data)
        
        # ========================================
        # RETURN RESPONSE
        # ========================================
        return jsonify({
            "status": "success",
            "data": {
                "discoveries": discoveries,
                "total": len(discoveries),
                "showing": len(discoveries),
                "min_mutuals_filter": min_mutuals,
                "discovery_type": "mutual"
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Discover mutuals error: ", exc_info=True)
        return error_response("Failed to discover mutual connections")


@connections_bp.route("/connections/suggestions/flat", methods=["GET"])
@token_required
def connection_suggestions_flat(current_user):
    """
    Get connection suggestions as a flat list (no grouping)
    Returns top 20 suggestions sorted by match score

    Query params:
    - limit: Maximum results (default: 20, max: 50)
    """
    try:
        profile    = StudentProfile.query.filter_by(user_id=current_user.id).first()
        onboarding = OnboardingDetails.query.filter_by(user_id=current_user.id).first()

        if not profile:
            return error_response("Profile not found", 404)

        limit = min(int(request.args.get("limit", 20)), 50)

        # Build excluded IDs
        existing_connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            )
        ).all()

        excluded_ids = {current_user.id}
        for conn in existing_connections:
            other_id = conn.receiver_id if conn.requester_id == current_user.id else conn.requester_id
            excluded_ids.add(other_id)

        # OPTIMIZED: Single 3-way join fetches all candidates with profiles and
        # onboarding at once — replaces 2 per-candidate queries inside the loop.
        candidates_data = (
            db.session.query(User, StudentProfile, OnboardingDetails)
            .join(StudentProfile, StudentProfile.user_id == User.id)
            .outerjoin(OnboardingDetails, OnboardingDetails.user_id == User.id)
            .filter(
                User.id.notin_(excluded_ids),
                User.status == "approved",
            )
            .limit(100)
            .all()
        )

        now = datetime.datetime.utcnow()

        # OPTIMIZED: Pre-load current user's connections for batch mutual counting.
        my_conns = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()
        my_conn_ids = {
            c.receiver_id if c.requester_id == current_user.id else c.requester_id
            for c in my_conns
        }

        # First pass: score all candidates (zero extra DB hits)
        scored_raw = []

        for candidate, cand_profile, cand_onboarding in candidates_data:
            score    = 0
            reasons  = []
            category = "peer"

            if profile.department and cand_profile.department == profile.department:
                score += 30
                reasons.append(f"Same major: {profile.department}")

            if profile.class_name and cand_profile.class_name == profile.class_name:
                score += 10
                reasons.append(f"Same class: {profile.class_name}")

            if onboarding and cand_onboarding:
                if onboarding.subjects and cand_onboarding.subjects:
                    common = (
                        set(s.lower().strip() for s in onboarding.subjects)
                        & set(s.lower().strip() for s in cand_onboarding.subjects)
                    )
                    if common:
                        score += min(len(common) * 8, 25)
                        reasons.append(f"Studying: {', '.join(list(common)[:2])}")

                if onboarding.help_subjects and cand_onboarding.strong_subjects:
                    can_help = (
                        set(s.lower().strip() for s in onboarding.help_subjects)
                        & set(s.lower().strip() for s in cand_onboarding.strong_subjects)
                    )
                    if can_help:
                        score   += 20
                        category = "mentor"
                        reasons.append(f"Can help with: {', '.join(list(can_help)[:2])}")

                if onboarding.strong_subjects and cand_onboarding.help_subjects:
                    you_help = (
                        set(s.lower().strip() for s in onboarding.strong_subjects)
                        & set(s.lower().strip() for s in cand_onboarding.help_subjects)
                    )
                    if you_help:
                        score += 15
                        reasons.append(f"You can help with: {', '.join(list(you_help)[:2])}")

                if (
                    onboarding.learning_style
                    and cand_onboarding
                    and cand_onboarding.learning_style
                    and onboarding.learning_style.lower() == cand_onboarding.learning_style.lower()
                ):
                    score += 10
                    reasons.append("Similar learning style")

                if onboarding.study_schedule and cand_onboarding and cand_onboarding.study_schedule:
                    overlap = calculate_schedule_overlap(
                        onboarding.study_schedule, cand_onboarding.study_schedule
                    )
                    if overlap > 0:
                        score += min(int(overlap * 0.15), 15)
                        if overlap > 30:
                            reasons.append(f"{overlap}% schedule overlap")

            if candidate.reputation > 500:
                score += 5

            if candidate.last_active:
                days_ago = (now - candidate.last_active).days
                if days_ago < 7:
                    score += 5

            if score >= 30:
                scored_raw.append((candidate, cand_profile, cand_onboarding, score, reasons, category))

        # Sort and limit before the mutual-count batch lookup
        scored_raw.sort(key=lambda x: x[3], reverse=True)
        scored_raw = scored_raw[:limit]

        qualifying_ids = [r[0].id for r in scored_raw]

        # OPTIMIZED: One query for all qualifying candidates' connections
        if qualifying_ids:
            all_cand_conns = Connection.query.filter(
                or_(
                    Connection.requester_id.in_(qualifying_ids),
                    Connection.receiver_id.in_(qualifying_ids)
                ),
                Connection.status == "accepted"
            ).all()

            cand_conn_ids_map = {}
            for conn in all_cand_conns:
                for uid in (conn.requester_id, conn.receiver_id):
                    if uid in qualifying_ids:
                        other = conn.receiver_id if conn.requester_id == uid else conn.requester_id
                        cand_conn_ids_map.setdefault(uid, set()).add(other)
        else:
            cand_conn_ids_map = {}

        suggestions = []

        for candidate, cand_profile, cand_onboarding, score, reasons, category in scored_raw:
            online_status = get_user_online_status(candidate.id)
            their_ids     = cand_conn_ids_map.get(candidate.id, set())
            mutual_count  = len(my_conn_ids & their_ids)

            ob_preview = {}
            if cand_onboarding:
                ob_preview = {
                    "subjects":          cand_onboarding.subjects[:3] if cand_onboarding.subjects else [],
                    "strong_subjects":   cand_onboarding.strong_subjects[:3] if cand_onboarding.strong_subjects else [],
                    "help_subjects":     cand_onboarding.help_subjects[:3] if cand_onboarding.help_subjects else [],
                    "learning_style":    cand_onboarding.learning_style,
                    "study_preferences": cand_onboarding.study_preferences[:3] if cand_onboarding.study_preferences else [],
                    "session_length":    cand_onboarding.session_length,
                    "has_schedule":      bool(cand_onboarding.study_schedule),
                }

            suggestions.append({
                "user": {
                    "id":               candidate.id,
                    "username":         candidate.username,
                    "name":             candidate.name,
                    "avatar":           candidate.avatar,
                    "bio":              candidate.bio,
                    "department":       cand_profile.department if cand_profile else None,
                    "class_level":      cand_profile.class_name if cand_profile else None,
                    "reputation":       candidate.reputation,
                    "reputation_level": candidate.reputation_level,
                    "is_online":        online_status["is_online"],
                    "last_active":      online_status["last_active"],
                },
                "onboarding_details": ob_preview,
                "category":     category,
                "match_score":  min(score, 100),
                "reasons":      reasons[:4],
                "mutuals_count": mutual_count,
            })

        return jsonify({
            "status": "success",
            "data":  suggestions,
            "total": len(suggestions),
        })

    except Exception as e:
        current_app.logger.error(f"Flat connection suggestions error: ", exc_info=True)
        return error_response("Failed to load suggestions")
              
def calculate_schedule_overlap(schedule1, schedule2):
    """Calculate percentage of overlapping study times"""
    if not schedule1 or not schedule2:
        return 0
    
    overlap_count = 0
    total_slots = 0
    
    # Match the casing from HTML
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    times = ['morning', 'afternoon', 'evening']
    
    for day in days:
        if day in schedule1 and day in schedule2:
            # Get time slots for each user
            user1_times = set(schedule1[day])  # e.g., ['morning', 'afternoon']
            user2_times = set(schedule2[day])  # e.g., ['afternoon', 'evening']
            
            # Count total possible slots
            total_slots += len(times)
            
            # Count overlapping time slots
            overlaps = user1_times & user2_times  # Intersection
            overlap_count += len(overlaps)
    
    # Return percentage
    return int((overlap_count / max(total_slots, 1)) * 100) if total_slots > 0 else 0


def get_user_top_topics(user_id, limit=3):
    """Get user's most discussed topics from posts and activity"""
    try:
        # Get tags from user's posts
        posts = Post.query.filter_by(student_id=user_id).limit(30).all()
        
        all_tags = []
        for post in posts:
            if post.tags:
                all_tags.extend(post.tags)
        
        if not all_tags:
            return []
        
        topic_counts = Counter(all_tags)
        return [topic for topic, _ in topic_counts.most_common(limit)]
    except Exception as e:
        logger.error(f"Error getting top topics: ", exc_info=True)
        return []


def calculate_compatibility_score(compatibility_data):
    """Calculate numerical compatibility score (0-100)"""
    score = 0
    
    # Shared subjects (30 points max)
    shared_count = len(compatibility_data.get('shared_subjects', []))
    score += min(shared_count * 10, 30)
    
    # They can help you (40 points max)
    help_count = len(compatibility_data['complementary_skills'].get('they_can_help_with', []))
    score += min(help_count * 20, 40)
    
    # Schedule overlap (20 points max)
    schedule_overlap = compatibility_data.get('schedule_overlap', 0)
    score += min(schedule_overlap * 0.2, 20)
    
    # Department match (10 points)
    if compatibility_data.get('department_match', False):
        score += 10
    
    return min(int(score), 100)


def gather_user_data(user):
    """Gather all relevant data about a user for AI analysis"""
    try:
        profile = user.student_profile
        onboarding = user.onboarding_details
        
        return {
            "name": user.name,
            "username": user.username,
            "bio": user.bio or "No bio yet",
            "department": profile.department if profile else "Unknown",
            "class_name": profile.class_name if profile else "Unknown",
            "reputation": user.reputation,
            "reputation_level": user.reputation_level,
            "strong_subjects": onboarding.strong_subjects if onboarding else [],
            "help_subjects": onboarding.help_subjects if onboarding else [],
            "learning_style": onboarding.learning_style if onboarding else "Not specified",
            "study_preferences": onboarding.study_preferences if onboarding else [],
            "badges": [ub.badge.name for ub in user.badges.limit(3)] if user.badges else []
        }
    except Exception as e:
        logger.error(f"Error gathering user data: ", exc_info=True)
        return {}


def calculate_compatibility(current_user_data, target_user_data):
    """Calculate compatibility metrics between two users"""
    try:
        current_subjects = set(
            current_user_data.get('strong_subjects', []) + 
            current_user_data.get('help_subjects', [])
        )
        target_subjects = set(
            target_user_data.get('strong_subjects', []) + 
            target_user_data.get('help_subjects', [])
        )
        
        shared_subjects = list(current_subjects & target_subjects)
        
        they_can_help = list(
            set(target_user_data.get('strong_subjects', [])) & 
            set(current_user_data.get('help_subjects', []))
        )
        
        you_can_help = list(
            set(current_user_data.get('strong_subjects', [])) & 
            set(target_user_data.get('help_subjects', []))
        )
        
        return {
            "shared_subjects": shared_subjects,
            "complementary_skills": {
                "they_can_help_with": they_can_help,
                "you_can_help_with": you_can_help
            },
            "schedule_overlap": 0,  # Will be calculated below
            "department_match": current_user_data.get('department') == target_user_data.get('department')
        }
    except Exception as e:
        logger.error(f"Error calculating compatibility: ", exc_info=True)
        return {
            "shared_subjects": [],
            "complementary_skills": {"they_can_help_with": [], "you_can_help_with": []},
            "schedule_overlap": 0,
            "department_match": False
        }


def get_recent_activity(user_id):
    """Get user's recent activity metrics"""
    try:
        seven_days_ago = datetime.datetime.utcnow() - timedelta(days=7)
        
        recent_posts = Post.query.filter_by(student_id=user_id)\
            .filter(Post.posted_at >= seven_days_ago).count()
        
        recent_helpful = Comment.query.filter_by(student_id=user_id)\
            .filter(Comment.helpful_count > 0)\
            .filter(Comment.posted_at >= seven_days_ago).count()
        
        active_threads = ThreadMember.query.filter_by(student_id=user_id).count()
        
        popular_topics = get_user_top_topics(user_id, limit=3)
        
        return {
            "recent_posts": recent_posts,
            "recent_helpful_comments": recent_helpful,
            "active_threads": active_threads,
            "popular_topics": popular_topics
        }
    except Exception as e:
        logger.error(f"Error getting recent activity: ", exc_info=True)
        return {
            "recent_posts": 0,
            "recent_helpful_comments": 0,
            "active_threads": 0,
            "popular_topics": []
        }


# ============================================================================
# REPLACE THE ENTIRE /connections/overview/<int:user_id> ENDPOINT
# Starting around line 1117 in your connections.py
# ============================================================================

@connections_bp.route('/connections/overview/<int:user_id>', methods=['GET'])
@token_required
def get_connection_overview(current_user, user_id):
    """
    Get AI-powered streaming overview of a potential connection
    
    Uses your multi-provider system from learnora.py:
    - Automatic provider rotation on failure
    - OpenRouter, Groq, Together AI support
    - Rate limit handling
    
    Returns Server-Sent Events stream with:
    - Compatibility score
    - AI-generated insights
    - Why you should connect
    - How you can help each other
    - Conversation starter
    """
    
    try:
        # Validate target user
        target_user = User.query.get(user_id)
        
        if not target_user:
            return error_response("User not found", 404)
        
        if target_user.id == current_user.id:
            return error_response("Cannot analyze connection with yourself", 400)
        
        # Check if already connected
        existing = Connection.query.filter(
            (
                ((Connection.requester_id == current_user.id) & (Connection.receiver_id == user_id)) |
                ((Connection.requester_id == user_id) & (Connection.receiver_id == current_user.id))
            )
        ).first()
        
        already_connected = existing and existing.status == 'accepted'
        
        # Gather data
        logger.info(f"💬 Connection overview: user={current_user.id} → target={user_id}")
        
        current_user_data = gather_user_data(current_user)
        target_user_data = gather_user_data(target_user)
        
        # Calculate compatibility
        compatibility_data = calculate_compatibility(current_user_data, target_user_data)
        
        # Calculate schedule overlap if onboarding data exists
        if current_user.onboarding_details and target_user.onboarding_details:
            compatibility_data['schedule_overlap'] = calculate_schedule_overlap(
                current_user.onboarding_details.study_schedule or {},
                target_user.onboarding_details.study_schedule or {}
            )
        
        # Get recent activity
        context_data = get_recent_activity(target_user.id)
        
        # Calculate compatibility score
        compatibility_score = calculate_compatibility_score(compatibility_data)
        
        # ========================================================================
        # ✅ FIX: Use your multi-provider system from learnora.py
        # ========================================================================
        
        # Get working provider (no vision needed for text chat)
        provider = provider_manager.get_working_provider(needs_vision=False)
        
        if not provider:
            # Fallback without AI
            logger.warning("⚠️ No AI provider available - returning fallback")
            fallback = generate_fallback_overview(
                compatibility_data,
                target_user_data,
                compatibility_score
            )
            
            return jsonify({
                "status": "success",
                "data": {
                    "target_user": {
                        "id": target_user.id,
                        "name": target_user.name,
                        "username": target_user.username,
                        "avatar": target_user.avatar,
                        "bio": target_user.bio,
                        "reputation_level": target_user.reputation_level,
                        "department": target_user_data['department']
                    },
                    "compatibility": {
                        "score": compatibility_score,
                        "shared_subjects": compatibility_data['shared_subjects'],
                        "mutual_help": compatibility_data['complementary_skills'],
                        "schedule_overlap": compatibility_data['schedule_overlap'],
                        "same_department": compatibility_data['department_match']
                    },
                    "ai_overview": fallback,
                    "activity": context_data,
                    "already_connected": already_connected,
                    "ai_available": False
                }
            })
        
        # Generate AI prompt
        prompt = generate_ai_overview_prompt(
            current_user_data,
            target_user_data,
            compatibility_data,
            context_data
        )
        
        # ✅ Create assistant using your learnora.py system
        assistant = StudyAssistant(provider, conversation_messages=[])
        assistant.select_model(has_images=False)  # Text-only chat
        
        # Build messages
        messages = [
            {
                "role": "system",
                "content": "You are a helpful study companion assistant specializing in helping students form meaningful learning connections. Be warm, specific, and encouraging."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
        
        # ✅ Stream response with provider rotation
        def generate():
            nonlocal provider
            full_response = ""
            error_occurred = False
            retries = 0
            max_retries = 2
            
            # Send initial data
            yield f"data: {json.dumps({'type': 'start', 'compatibility_score': compatibility_score})}\n\n"
            
            yield f"data: {json.dumps({'type': 'compatibility', 'data': {
                'score': compatibility_score,
                'shared_subjects': compatibility_data['shared_subjects'],
                'mutual_help': compatibility_data['complementary_skills'],
                'schedule_overlap': compatibility_data['schedule_overlap'],
                'same_department': compatibility_data['department_match']
            }})}\n\n"
            
            yield f"data: {json.dumps({'type': 'target_user', 'data': {
                'id': target_user.id,
                'name': target_user.name,
                'username': target_user.username,
                'avatar': target_user.avatar,
                'bio': target_user.bio,
                'reputation_level': target_user.reputation_level,
                'department': target_user_data['department']
            }})}\n\n"
            
            yield f"data: {json.dumps({'type': 'activity', 'data': context_data})}\n\n"
            
            yield f"data: {json.dumps({'type': 'ai_start', 'provider': provider['name']})}\n\n"
            
            # Stream AI response with retry logic
            while retries < max_retries:
                current_app.logger.error("Streaming response")
                error_in_stream = False
                
                for chunk in assistant.stream_response(messages):
                    yield chunk
                    
                    if chunk.startswith("data: "):
                        try:
                            chunk_data = json.loads(chunk[6:])
                            
                            if 'content' in chunk_data:
                                full_response += chunk_data['content']
                            elif 'error' in chunk_data:
                                error_occurred = True
                                
                                # Check if it's a rate limit/quota/timeout error
                                if chunk_data.get('rate_limit') or chunk_data.get('timeout') or chunk_data.get('http_error'):
                                    error_in_stream = True
                                    
                                    # Mark provider as failed
                                    provider_manager.mark_provider_failed(provider['name'])
                                    
                                    # Try next provider
                                    provider_manager.rotate()
                                    next_provider = provider_manager.get_working_provider(needs_vision=False)
                                    
                                    if next_provider and retries < max_retries - 1:
                                        logger.info(f"🔄 Switching from {provider['name']} to {next_provider['name']}")
                                        provider = next_provider
                                        assistant.provider = next_provider
                                        assistant.select_model(has_images=False)
                                        retries += 1
                                        
                                        # Notify frontend of provider switch
                                        yield f"data: {json.dumps({'type': 'retry', 'provider': provider['name']})}\n\n"
                                        break
                        except:
                            pass
                
                # Break out of retry loop if no error in stream
                if not error_in_stream:
                    break
            
            # Generate fallback if AI completely failed
            if error_occurred or not full_response:
                logger.warning("⚠️ AI failed - using fallback")
                fallback = generate_fallback_overview(
                    compatibility_data,
                    target_user_data,
                    compatibility_score
                )
                full_response = fallback
            
            # Send completion
            current_app.logger.error("Done streaming response")
            yield f"data: {json.dumps({
                'type': 'done',
                'success': not error_occurred,
                'already_connected': already_connected,
                'overview': full_response
            })}\n\n"
        
        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no'
            }
        )
        
    except Exception as e:
        logger.error(f"❌ Connection overview error: {str(e)}", exc_info=True)
        return error_response(f"Failed to generate overview: {str(e)}", 500)


# ============================================================================
# HELPER FUNCTIONS (Keep your existing ones, or use these refined versions)
# ============================================================================

def generate_fallback_overview(compatibility_data, target_user_data, score):
    """Generate simple overview if AI fails - KEEP THIS"""
    shared = compatibility_data['shared_subjects']
    can_help = compatibility_data['complementary_skills']['they_can_help_with']
    
    overview = f"**🎯 Connection Score:** {score}/100\n\n"
    
    if score >= 70:
        overview += "**💡 Why Connect?**\nThis is a strong match! "
    elif score >= 40:
        overview += "**💡 Why Connect?**\nThis could be a valuable connection! "
    else:
        overview += "**💡 Why Connect?**\nWhile you have different focuses, diverse perspectives can be valuable! "
    
    if shared:
        overview += f"You both share interests in {', '.join(shared[:2])}. "
    
    if can_help:
        overview += f"They can help you with {', '.join(can_help[:2])}. "
    
    overview += "\n\n**🤝 How You Can Help Each Other:**\n"
    if can_help:
        overview += f"They're strong in {', '.join(can_help[:2])}, which you're looking to learn. "
    
    you_can_help = compatibility_data['complementary_skills']['you_can_help_with']
    if you_can_help:
        overview += f"You can support them with {', '.join(you_can_help[:2])}. "
    
    if not can_help and not you_can_help:
        overview += "While your skills don't directly overlap, you can still learn from each other's different perspectives."
    
    overview += "\n\n**💬 Conversation Starter:**\n"
    if shared:
        overview += f"\"Hey! I noticed we're both interested in {shared[0]}. Would love to connect and maybe study together!\""
    elif can_help:
        overview += f"\"Hi! I saw you're experienced with {can_help[0]}. I'm working on that now – would you be open to connecting?\""
    else:
        overview += f"\"Hi! I'm a {target_user_data['department']} student too. Would love to connect and share study tips!\""
    
    return overview


def generate_ai_overview_prompt(current_user_data, target_user_data, compatibility_data, context_data):
    """Generate the prompt for AI connection overview - KEEP THIS"""
    
    prompt = f"""You are an intelligent study companion assistant helping students make meaningful connections.

**YOUR TASK:** Analyze this potential connection and provide a compelling, personalized overview.

**CURRENT USER:**
- Name: {current_user_data['name']}
- Department: {current_user_data['department']}
- Strong in: {', '.join(current_user_data['strong_subjects'][:3]) or 'Not specified'}
- Needs help with: {', '.join(current_user_data['help_subjects'][:3]) or 'Not specified'}
- Learning style: {current_user_data['learning_style']}

**POTENTIAL CONNECTION:**
- Name: {target_user_data['name']} (@{target_user_data['username']})
- Department: {target_user_data['department']} | Class: {target_user_data['class_name']}
- Reputation: {target_user_data['reputation']} ({target_user_data['reputation_level']})
- Strong in: {', '.join(target_user_data['strong_subjects'][:5]) or 'Not specified'}
- Can help with: {', '.join(target_user_data['help_subjects'][:5]) or 'Not specified'}
- Bio: {target_user_data['bio']}

**COMPATIBILITY:**
- Shared subjects: {', '.join(compatibility_data['shared_subjects']) if compatibility_data['shared_subjects'] else 'None'}
- They can help you with: {', '.join(compatibility_data['complementary_skills']['they_can_help_with'][:3]) if compatibility_data['complementary_skills']['they_can_help_with'] else 'No overlap'}
- You can help them with: {', '.join(compatibility_data['complementary_skills']['you_can_help_with'][:3]) if compatibility_data['complementary_skills']['you_can_help_with'] else 'No overlap'}
- Schedule compatibility: {compatibility_data['schedule_overlap']}%
- Same department: {'Yes' if compatibility_data['department_match'] else 'No'}

**RECENT ACTIVITY:**
- Posts this week: {context_data['recent_posts']}
- Helpful responses: {context_data['recent_helpful_comments']}
- Active in {context_data['active_threads']} study groups
- Top topics: {', '.join(context_data['popular_topics']) if context_data['popular_topics'] else 'None'}
- Badges: {', '.join(target_user_data['badges'][:3]) if target_user_data['badges'] else 'None'}

**RESPONSE FORMAT:**
Provide your analysis in exactly 4 sections (use emojis for visual appeal):

1. **🎯 Connection Score** (X/10)
   - One sentence explaining the overall match quality

2. **💡 Why Connect?**
   - 2-3 specific, compelling reasons based on the data above
   - Focus on mutual benefit and learning synergy
   - Be specific (use actual subject names, not generic terms)

3. **🤝 How You Can Help Each Other**
   - Clear examples of knowledge exchange
   - Mention specific subjects/skills from the data

4. **💬 Conversation Starter**
   - Suggest a personalized opening message (1-2 sentences)
   - Reference something specific from their profile or activity

**STYLE GUIDELINES:**
- Be warm, enthusiastic, and encouraging
- Use concrete examples from the data, not generic praise
- If compatibility is low, be honest but constructive
- Keep total response under 250 words
- Make it feel like advice from a knowledgeable friend
- NEVER make up information not provided above

Begin your response now:"""
    
    return prompt


"""
Online Connections Endpoint
Returns list of connected users who are currently online (active in last 30 minutes)
Matches the exact structure of your existing endpoints
"""

@connections_bp.route("/connections/online", methods=["GET"])
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

@connections_bp.route("/connections/online/count", methods=["GET"])
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

@connections_bp.route("/connections/online/department", methods=["GET"])
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



@connections_bp.route("/connections/unseen/received", methods=["GET"])
@token_required
def unseen_received_count(current_user):
    """
    Get count of unseen received connection requests
    Returns the number of pending requests sent TO you that you haven't seen yet
    """
    try:
        count = Connection.query.filter_by(
            receiver_id=current_user.id,
            status="pending",
            is_seen=False
        ).count()
        
        return jsonify({
            "status": "success",
            "data": {
                "count": count
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Unseen received connections error: ", exc_info=True)
        return error_response("Failed to get unseen received connections count")


@connections_bp.route("/connections/unseen/sent", methods=["GET"])
@token_required
def unseen_sent_count(current_user):
    """
    Get count of unseen sent connection requests that were responded to
    Returns requests YOU sent that have been accepted/rejected but you haven't seen the response
    """
    try:
        count = Connection.query.filter_by(
            requester_id=current_user.id,
            is_seen=False
        ).filter(
            Connection.status.in_(["accepted", "rejected"])
        ).count()
        
        return jsonify({
            "status": "success",
            "data": {
                "count": count
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Unseen sent connections error: ", exc_info=True)
        return error_response("Failed to get unseen sent connections count")


@connections_bp.route("/connections/unseen/all", methods=["GET"])
@token_required
def unseen_all_count(current_user):
    """
    Get combined count of all unseen connection activities
    Useful for a single notification badge showing total unseen items
    """
    try:
        # Unseen received requests
        received_count = Connection.query.filter_by(
            receiver_id=current_user.id,
            status="pending",
            is_seen=False
        ).count()
        
        # Unseen responses to sent requests
        sent_count = Connection.query.filter_by(
            requester_id=current_user.id,
            is_seen=False
        ).filter(
            Connection.status.in_(["accepted", "rejected"])
        ).count()
        
        total = received_count + sent_count
        
        return jsonify({
            "status": "success",
            "data": {
                "total": total,
                "received": received_count,
                "sent_responses": sent_count
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Unseen all connections error: ", exc_info=True)
        return error_response("Failed to get unseen connections count")


@connections_bp.route("/study-sessions/unseen", methods=["GET"])
@token_required
def unseen_study_sessions_count(current_user):
    """
    Get count of unseen study session requests
    Returns sessions sent TO you that you haven't seen yet
    """
    try:
        try:
            from models import StudySessions
            count = StudySessions.query.filter_by(
                receiver_id=current_user.id,
                is_seen=False,
                status="pending"
            ).count()
        except Exception:
            count = 0

        return jsonify({
            "status": "success",
            "data": {
                "count": count
            }
        })

    except Exception as e:
        current_app.logger.error("Unseen study sessions error", exc_info=True)
        return error_response("Failed to get unseen study sessions count")


# ============================================================================
# MARK AS SEEN - Helper Endpoints
# ============================================================================

@connections_bp.route("/connections/mark-seen/<int:connection_id>", methods=["POST"])
@token_required
def mark_connection_seen(current_user, connection_id):
    """
    Mark a specific connection request as seen
    Can be called when user views the connection request
    """
    try:
        connection = Connection.query.get(connection_id)
        
        if not connection:
            return error_response("Connection not found", 404)
        
        # Verify user is involved in this connection
        if connection.receiver_id != current_user.id and connection.requester_id != current_user.id:
            return error_response("Not authorized", 403)
        
        connection.is_seen = True
        db.session.commit()
        
        return success_response("Connection marked as seen")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Mark connection seen error: ", exc_info=True)
        return error_response("Failed to mark connection as seen")


@connections_bp.route("/connections/mark-received-seen", methods=["POST"])
@token_required
def mark_received_connections_seen(current_user):
    """
    Mark all received connection requests as seen
    Call this when user opens the received requests page
    """
    try:
        updated = Connection.query.filter_by(
            receiver_id=current_user.id,
            status="pending",
            is_seen=False
        ).update({"is_seen": True})
        
        db.session.commit()
        
        return success_response(
            f"Marked {updated} received connections as seen",
            data={"updated_count": updated}
        )
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Mark received connections seen error: ", exc_info=True)
        return error_response("Failed to mark received connections as seen")


@connections_bp.route("/connections/mark-sent-seen", methods=["POST"])
@token_required
def mark_sent_connections_seen(current_user):
    """
    Mark all sent connection request responses as seen
    Call this when user opens the sent requests page
    """
    try:
        updated = Connection.query.filter_by(
            requester_id=current_user.id,
            is_seen=False
        ).filter(
            Connection.status.in_(["accepted", "rejected"])
        ).update({"is_seen": True}, synchronize_session=False)
        
        db.session.commit()
        
        return success_response(
            f"Marked {updated} sent connection responses as seen",
            data={"updated_count": updated}
        )
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Mark sent connections seen error: ", exc_info=True)
        return error_response("Failed to mark sent connections as seen")


@connections_bp.route("/connections/mark-all-seen", methods=["POST"])
@token_required
def mark_all_connections_seen(current_user):
    """
    Mark ALL unseen connections as seen (both received and sent)
    Useful when user opens main connections page
    """
    try:
        # Mark all received pending requests as seen
        received_updated = Connection.query.filter_by(
            receiver_id=current_user.id,
            status="pending",
            is_seen=False
        ).update({"is_seen": True})
        
        # Mark all responded sent requests as seen
        sent_updated = Connection.query.filter_by(
            requester_id=current_user.id,
            is_seen=False
        ).filter(
            Connection.status.in_(["accepted", "rejected"])
        ).update({"is_seen": True}, synchronize_session=False)
        
        db.session.commit()
        
        total = received_updated + sent_updated
        
        return success_response(
            f"Marked {total} connections as seen",
            data={
                "total_updated": total,
                "received_updated": received_updated,
                "sent_updated": sent_updated
            }
        )
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Mark all connections seen error: ", exc_info=True)
        return error_response("Failed to mark connections as seen")



def get_user_onboarding_preview(user_id):
    
    try:
        from models import OnboardingDetails
        
        onboarding = OnboardingDetails.query.filter_by(user_id=user_id).first()
        
        if not onboarding:
            return None
        
        return {
            "subjects": onboarding.subjects[:3] if onboarding.subjects else [],
            "strong_subjects": onboarding.strong_subjects[:3] if onboarding.strong_subjects else [],
            "help_subjects": onboarding.help_subjects[:3] if onboarding.help_subjects else [],
            "learning_style": onboarding.learning_style,
            "study_preferences": onboarding.study_preferences[:3] if onboarding.study_preferences else [],
            "session_length": onboarding.session_length,
            "has_schedule": bool(onboarding.study_schedule)
        }
    except Exception as e:
        current_app.logger.error(f"Available connections error: ", exc_info=True)
        return error_response("Failed to find available connections")

            
@connections_bp.route("/connections/available-now", methods=["GET"])
@token_required
def get_available_connections(current_user):
    try:
        subject = request.args.get("subject", "").strip()

        # Get all accepted connections
        connections = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        ).all()

        if not connections:
            return jsonify({
                "status": "success",
                "data": {"subject": subject, "available_now": [], "total": 0}
            })

        # Extract partner IDs
        other_ids = [
            c.receiver_id if c.requester_id == current_user.id else c.requester_id
            for c in connections
        ]

        # BATCH LOAD users, profiles, onboarding (eliminates N+1)
        users_map = {
            u.id: u for u in User.query.filter(User.id.in_(other_ids)).all()
        }
        profiles_map = {
            p.user_id: p
            for p in StudentProfile.query.filter(
                StudentProfile.user_id.in_(other_ids)
            ).all()
        }
        onboarding_map = {
            o.user_id: o
            for o in OnboardingDetails.query.filter(
                OnboardingDetails.user_id.in_(other_ids)
            ).all()
        }

        now = datetime.datetime.utcnow()
        day_name = now.strftime("%A").lower()
        current_hour = now.hour

        if 6 <= current_hour < 12:
            time_slot = "morning"
        elif 12 <= current_hour < 18:
            time_slot = "afternoon"
        elif 18 <= current_hour < 22:
            time_slot = "evening"
        else:
            time_slot = "night"

        available_users = []

        for other_id in other_ids:
            user = users_map.get(other_id)
            if not user:
                continue

            profile = profiles_map.get(other_id)
            onboarding = onboarding_map.get(other_id)

            # Build onboarding preview from already-loaded data
            if onboarding:
                onboarding_details = {
                    "subjects": onboarding.subjects[:3] if onboarding.subjects else [],
                    "strong_subjects": onboarding.strong_subjects[:3] if onboarding.strong_subjects else [],
                    "help_subjects": onboarding.help_subjects[:3] if onboarding.help_subjects else [],
                    "learning_style": onboarding.learning_style,
                    "study_preferences": onboarding.study_preferences[:3] if onboarding.study_preferences else [],
                    "session_length": onboarding.session_length,
                    "has_schedule": bool(onboarding.study_schedule),
                }
            else:
                onboarding_details = {}

            # Check 1: Active recently (last 30 minutes)
            minutes_ago = None
            if user.last_active:
                minutes_ago = (now - user.last_active).total_seconds() / 60
                is_online = minutes_ago < 30
            else:
                is_online = False

            # Check 2: Can help with subject
            can_help = False
            if onboarding and onboarding.strong_subjects and subject:
                can_help = any(
                    subject.lower() in strong.lower()
                    for strong in onboarding.strong_subjects
                )

            # Check 3: Available now according to schedule
            schedule_available = False
            if onboarding and onboarding.study_schedule:
                day_slots = onboarding.study_schedule.get(day_name, [])
                schedule_available = time_slot in day_slots

            # Calculate availability score
            availability_score = 0
            if is_online:
                availability_score += 50
            if can_help:
                availability_score += 30
            if schedule_available:
                availability_score += 20

            if availability_score > 0:
                available_users.append({
                    "user": {
                        "id": user.id,
                        "name": user.name,
                        "username": user.username,
                        "avatar": user.avatar,
                        "department": profile.department if profile else None,
                        "reputation_level": user.reputation_level
                    },
                    "onboarding_details": onboarding_details,
                    "availability": {
                        "is_online": is_online,
                        "can_help_with_subject": can_help,
                        "schedule_available": schedule_available,
                        "score": availability_score
                    },
                    "last_active_minutes": int(minutes_ago) if minutes_ago is not None else None
                })

        available_users.sort(key=lambda x: x["availability"]["score"], reverse=True)

        return jsonify({
            "status": "success",
            "data": {
                "subject": subject,
                "available_now": available_users,
                "total": len(available_users)
            }
        })

    except Exception as e:
        current_app.logger.error("Available connections error", exc_info=True)
        return error_response("Failed to find available connections")
        

"""
Endpoint for getting connection notes
Add this to your connections.py file
"""

@connections_bp.route("/connections/<int:connection_id>/notes", methods=["GET"])
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


@connections_bp.route("/connections/<int:connection_id>/notes/update", methods=["PUT", "POST"])
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



@connections_bp.route("/connections/request/<int:user_id>", methods=["POST"])
@token_required
def send_connection_request(current_user, user_id):
    """
    Smart connection request with auto-accept for high compatibility
    
    Body (optional): {
        "message": "Hi! Let's connect and study together"
    }
    
    Response includes:
    - is_instant: true if auto-connected (compatibility >= 70%)
    - status: "accepted" | "pending" | "already_connected"
    - compatibility_score: 0-100 match score
    """
    try:
        # Validation
        if user_id == current_user.id:
            return error_response("Cannot connect with yourself")
        
        target_user = User.query.get(user_id)
        if not target_user:
            return error_response("User not found", 404)
        
        # Check if connection already exists (either direction)
        existing = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == user_id),
                and_(Connection.requester_id == user_id, Connection.receiver_id == current_user.id)
            )
        ).first()
        
        if existing:
            if existing.status == "accepted":
                return jsonify({
                    "status": "success",
                    "message": "Already connected",
                    "data": {
                        "connection_id": existing.id,
                        "is_instant": False,
                        "connection_status": "already_connected",
                        "connected_at": existing.responded_at.isoformat() if existing.responded_at else None,
                        "receiver": {
                            "id": target_user.id,
                            "name": target_user.name,
                            "username": target_user.username,
                            "avatar": target_user.avatar
                        }
                    }
                }), 200
                
            elif existing.status == "pending":
                # Check who sent the original request
                if existing.requester_id == current_user.id:
                    return jsonify({
                        "status": "success",
                        "message": "Connection request already pending",
                        "data": {
                            "connection_id": existing.id,
                            "is_instant": False,
                            "connection_status": "pending_sent",
                            "requested_at": existing.requested_at.isoformat(),
                            "receiver": {
                                "id": target_user.id,
                                "name": target_user.name,
                                "username": target_user.username,
                                "avatar": target_user.avatar
                            }
                        }
                    }), 200
                else:
                    # They sent you a request - accept it instantly!
                    existing.status = "accepted"
                    existing.responded_at = datetime.datetime.utcnow()
                    
                    # Notify original requester
                    notification = Notification(
                        user_id=existing.requester_id,
                        title="Connection Accepted",
                        body=f"{current_user.name} accepted your connection request",
                        notification_type="connection_accepted",
                        related_type="user",
                        related_id=current_user.id
                    )
                    db.session.add(notification)
                    db.session.commit()
                    
                    return jsonify({
                        "status": "success",
                        "message": "Connection accepted! (They requested you first)",
                        "data": {
                            "connection_id": existing.id,
                            "is_instant": True,
                            "connection_status": "accepted",
                            "connected_at": existing.responded_at.isoformat(),
                            "receiver": {
                                "id": target_user.id,
                                "name": target_user.name,
                                "username": target_user.username,
                                "avatar": target_user.avatar
                            }
                        }
                    }), 201
                    
            elif existing.status == "blocked":
                return error_response("Cannot connect with this user", 403)
                
            elif existing.status == "rejected":
                # Check cooldown period (24 hours)
                cooldown_hours = 24
                if existing.responded_at:
                    hours_since_rejection = (datetime.datetime.utcnow() - existing.responded_at).total_seconds() / 3600
                    if hours_since_rejection < cooldown_hours:
                        remaining = int(cooldown_hours - hours_since_rejection)
                        return error_response(
                            f"Please wait {remaining} hours before requesting again",
                            429
                        )
                
                # Allow re-request after cooldown
                existing.status = "pending"
                existing.requested_at = datetime.datetime.utcnow()
                existing.responded_at = None
                
                notification = Notification(
                    user_id=user_id,
                    title="New Connection Request",
                    body=f"{current_user.name} sent you a connection request again",
                    notification_type="connection_request",
                    related_type="user",
                    related_id=current_user.id
                )
                db.session.add(notification)
                db.session.commit()
                
                return jsonify({
                    "status": "success",
                    "message": "Connection request re-sent",
                    "data": {
                        "connection_id": existing.id,
                        "is_instant": False,
                        "connection_status": "pending",
                        "requested_at": existing.requested_at.isoformat(),
                        "receiver": {
                            "id": target_user.id,
                            "name": target_user.name,
                            "username": target_user.username,
                            "avatar": target_user.avatar
                        }
                    }
                }), 201
        
        # ============================================================================
        # NEW: Calculate compatibility for instant connect
        # ============================================================================
        
        current_user_data = gather_user_data(current_user)
        target_user_data = gather_user_data(target_user)
        
        compatibility_data = calculate_compatibility(current_user_data, target_user_data)
        
        # Calculate schedule overlap if onboarding data exists
        if current_user.onboarding_details and target_user.onboarding_details:
            compatibility_data['schedule_overlap'] = calculate_schedule_overlap(
                current_user.onboarding_details.study_schedule or {},
                target_user.onboarding_details.study_schedule or {}
            )
        
        compatibility_score = calculate_compatibility_score(compatibility_data)
        
        # Get custom message
        data = request.get_json(silent=True) or {}
        message = data.get("message", "").strip()
        
        # ============================================================================
        # INSTANT CONNECT: Auto-accept if compatibility >= 70%
        # ============================================================================
        
        if compatibility_score >= 70:
            connection = Connection(
                requester_id=current_user.id,
                receiver_id=user_id,
                status="accepted",  # ← AUTO-ACCEPT
                requested_at=datetime.datetime.utcnow(),
                responded_at=datetime.datetime.utcnow(),
                requester_notes=message if message else f"Instant connection (compatibility: {compatibility_score}%)"
            )
            db.session.add(connection)
            
            # Notify both users about instant connection
            notification_receiver = Notification(
                user_id=user_id,
                title=f"🎉 Instant Connection with {current_user.name}",
                body=f"You're {compatibility_score}% compatible! Start chatting now.",
                notification_type="instant_connection",
                related_type="user",
                related_id=current_user.id
            )
            db.session.add(notification_receiver)
            
            notification_sender = Notification(
                user_id=current_user.id,
                title=f"🎉 Instantly Connected with {target_user.name}",
                body=f"High compatibility match ({compatibility_score}%)! Start chatting now.",
                notification_type="instant_connection",
                related_type="user",
                related_id=user_id
            )
            db.session.add(notification_sender)
            
            db.session.commit()
            
            return jsonify({
                "status": "success",
                "message": f"Instantly connected! ({compatibility_score}% compatibility)",
                "data": {
                    "connection_id": connection.id,
                    "is_instant": True,  # ← Frontend uses this
                    "connection_status": "accepted",
                    "connected_at": connection.responded_at.isoformat(),
                    "compatibility": {
                        "score": compatibility_score,
                        "shared_subjects": compatibility_data['shared_subjects'],
                        "mutual_help": compatibility_data['complementary_skills'],
                        "schedule_overlap": compatibility_data.get('schedule_overlap', 0)
                    },
                    "receiver": {
                        "id": target_user.id,
                        "name": target_user.name,
                        "username": target_user.username,
                        "avatar": target_user.avatar,
                        "reputation_level": target_user.reputation_level
                    }
                }
            }), 201
        
        # ============================================================================
        # REGULAR FLOW: Create pending request (compatibility < 70%)
        # ============================================================================
        
        else:
            connection = Connection(
                requester_id=current_user.id,
                receiver_id=user_id,
                status="pending",
                requested_at=datetime.datetime.utcnow(),
                requester_notes=message if message else None
            )
            db.session.add(connection)
            
            # Create notification
            notification = Notification(
                user_id=user_id,
                title="New Connection Request",
                body=f"{current_user.name} wants to connect with you",
                notification_type="connection_request",
                related_type="user",
                related_id=current_user.id
            )
            db.session.add(notification)
            
            db.session.commit()
            
            return jsonify({
                "status": "success",
                "message": "Connection request sent (awaiting approval)",
                "data": {
                    "connection_id": connection.id,
                    "is_instant": False,  # ← Frontend uses this
                    "connection_status": "pending",
                    "requested_at": connection.requested_at.isoformat(),
                    "compatibility": {
                        "score": compatibility_score,
                        "note": "Compatibility below 70% - requires approval"
                    },
                    "receiver": {
                        "id": target_user.id,
                        "name": target_user.name,
                        "username": target_user.username,
                        "avatar": target_user.avatar,
                        "reputation_level": target_user.reputation_level
                    }
                }
            }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Send connection request error: ", exc_info=True)
        return error_response("Failed to send connection request")

@connections_bp.route("/connections/accept/<int:request_id>", methods=["POST"])
@token_required
def accept_connection(current_user, request_id):
    """
    Accept a connection request
    """
    try:
        connection = Connection.query.get(request_id)
        
        if not connection:
            return error_response("Connection request not found")
        
        # Verify user is the receiver
        if connection.receiver_id != current_user.id:
            return error_response("Not authorized to accept this request")
        
        if connection.status != "pending":
            return error_response("Request is not pending")
        
        # Accept connection
        connection.status = "accepted"
        connection.responded_at = datetime.datetime.utcnow()
        
        # Create notification for requester
        notification = Notification(
            user_id=connection.requester_id,
            title="Connection Accepted",
            body=f"{current_user.name} accepted your connection request",
            notification_type="connection_accepted",
            related_type="user",
            related_id=current_user.id
        )
        db.session.add(notification)
        
        db.session.commit()
        
        # Get requester info
        requester = User.query.get(connection.requester_id)
        
        return success_response(
            "Connection accepted",
            data={
                "connection_id": connection.id,
                "connected_user": {
                    "id": requester.id,
                    "name": requester.name,
                    "username": requester.username,
                    "avatar": requester.avatar
                }
            }
        )
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Accept connection error: ", exc_info=True)
        return error_response("Failed to accept connection")


@connections_bp.route("/connections/blocked/list", methods=["GET"])
@token_required
def list_blocked_users_detailed(current_user):
    """
    Get list of all blocked users (no pagination)
    Returns user details in the same format as connections list
    """
    try:
        # Get all blocked connections where current user is the blocker
        blocked_connections = Connection.query.filter_by(
            receiver_id=current_user.id,
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
        
        # Extract blocked user IDs
        blocked_ids = [conn.requester_id for conn in blocked_connections]
        
        # Create a map of connection_id and blocked_at for later use
        connection_map = {
            conn.requester_id: {
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
        
@connections_bp.route("/connections/reject/<int:request_id>", methods=["POST"])
@token_required
def reject_connection(current_user, request_id):
    """
    Reject a connection request
    """
    try:
        connection = Connection.query.get(request_id)
        
        if not connection:
            return error_response("Connection request not found")
        
        # Verify user is the receiver
        if connection.receiver_id != current_user.id:
            return error_response("Not authorized to reject this request", 403)
        
        if connection.status != "pending":
            return error_response("Request is not pending", 400)
        
        # Reject connection
        connection.status = "rejected"
        connection.responded_at = datetime.datetime.utcnow()
        
        db.session.commit()
        
        return success_response("Connection request rejected")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Reject connection error: ", exc_info=True)
        return error_response("Failed to reject connection")


@connections_bp.route("/connections/cancel/<int:request_id>", methods=["DELETE"])
@token_required
def cancel_connection_request(current_user, request_id):
    """
    Cancel a pending connection request you sent
    """
    try:
        connection = Connection.query.get(request_id)
        
        if not connection:
            return error_response("Connection request not found", 404)
        
        # Verify user is the requester
        if connection.requester_id !=     current_user.id:
            return error_response("Not authorized to cancel this request", 403)
        
        if connection.status != "pending":
            return error_response("Request is not pending", 400)
        
        # Delete the request
        db.session.delete(connection)
        db.session.commit()
        
        return success_response("Connection request cancelled")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Cancel connection error: ", exc_info=True)
        return error_response("Failed to cancel connection")


@connections_bp.route("/connections/remove/<int:user_id>", methods=["DELETE"])
@token_required
def remove_connection(current_user, user_id):
    """
    Remove/unfriend a connection
    """
    try:
        # Find connection (either direction)
        connection = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == user_id),
                and_(Connection.requester_id == user_id, Connection.receiver_id == current_user.id)
            ),
            Connection.status == "accepted"
        ).first()
        
        if not connection:
            return error_response("Connection not found", 404)
        
        # Delete the connection
        db.session.delete(connection)
        db.session.commit()
        
        return success_response("Connection removed")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Remove connection error: ", exc_info=True)
        return error_response("Failed to remove connection")

def get_mutual_connection_count(user1_id, user2_id):
    """
    Get count of mutual connections between two users
    """
    try:
        # Get user1's connections
        user1_connections = Connection.query.filter(
            or_(
                Connection.requester_id == user1_id,
                Connection.receiver_id == user1_id
            ),
            Connection.status == "accepted"
        ).all()
        
        user1_ids = set()
        for conn in user1_connections:
            other_id = conn.receiver_id if conn.requester_id == user1_id else conn.requester_id
            user1_ids.add(other_id)
        
        # Get user2's connections
        user2_connections = Connection.query.filter(
            or_(
                Connection.requester_id == user2_id,
                Connection.receiver_id == user2_id
            ),
            Connection.status == "accepted"
        ).all()
        
        user2_ids = set()
        for conn in user2_connections:
            other_id = conn.receiver_id if conn.requester_id == user2_id else conn.requester_id
            user2_ids.add(other_id)
        
        # Count mutual connections
        return len(user1_ids & user2_ids)
        
    except Exception as e:
        current_app.logger.error(f"Get mutual count error: ", exc_info=True)
        return 0
        




# ========================================
# HELPER FUNCTION: Get Connection Health
# ========================================

        
def get_connection_health(user_id, other_user_id):
    """
    Calculate connection health score based on:
    - Time since last interaction
    - Shared thread participation
    - Study sessions completed
    
    Returns dict with score, suggestion, and metrics
    """
    try:
        # Find the connection
        connection = Connection.query.filter(
            or_(
                and_(Connection.requester_id == user_id, Connection.receiver_id == other_user_id),
                and_(Connection.requester_id == other_user_id, Connection.receiver_id == user_id)
            ),
            Connection.status == "accepted"
        ).first()
        
        if not connection:
            return None
        
        score = 100  # Start at perfect
        
        # 1. Check last interaction time
        last_interaction = connection.responded_at or connection.requested_at
        days_since = (datetime.datetime.utcnow() - last_interaction).days
        
        if days_since > 30:
            score -= 40
        elif days_since > 14:
            score -= 20
        elif days_since > 7:
            score -= 10
        
        # 2. Shared thread activity
        user_threads = ThreadMember.query.filter_by(student_id=user_id).all()
        other_threads = ThreadMember.query.filter_by(student_id=other_user_id).all()
        
        user_thread_ids = set(t.thread_id for t in user_threads)
        other_thread_ids = set(t.thread_id for t in other_threads)
        
        shared_threads = len(user_thread_ids & other_thread_ids)
        score += min(shared_threads * 10, 30)
        
        # 3. Study buddy bonus
        
       
        
        # Cap score
        score = max(0, min(100, score))
        
        # Generate suggestion
        if score < 40:
            suggestion = "💤 Haven't connected in a while. Send them a message!"
        elif score < 70:
            suggestion = "👍 Good connection. Schedule a study session?"
        else:
            suggestion = "🔥 Strong connection! Keep it up."
        health_percent = score/100  *100
        return {
            "health_score": score,
            "health_percent": health_percent,
            "suggestion": suggestion,
            "last_interaction_days": days_since,
            "shared_threads": shared_threads
        }
        
    except Exception as e:
        current_app.logger.error(f"Connection health error: ", exc_info=True)
        return None
                
@connections_bp.route("/connections/status/<int:user_id>", methods=["GET"])
@token_required
def connection_status(current_user, user_id):
    """
    Check connection status with a specific user
    
    Returns: none, pending_sent, pending_received, connected, blocked
    """
    try:
        if user_id == current_user.id:
            return jsonify({
                "status": "success",
                "data": {
                    "status": "self",
                    "can_message": False,
                    "can_connect": False
                }
            })
        
        # Check for connection
        connection = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == user_id),
                and_(Connection.requester_id == user_id, Connection.receiver_id == current_user.id)
            )
        ).first()
        
        if not connection:
            return jsonify({
                "status": "success",
                "data": {
                    "status": "none",
                    "can_message": False,
                    "can_connect": True
                }
            })
        
        # Determine status
        if connection.status == "accepted":
            conn_status = "connected"
            can_message = True
        elif connection.status == "pending":
            if connection.requester_id == current_user.id:
                conn_status = "pending_sent"
            else:
                conn_status = "pending_received"
            can_message = False
        elif connection.status == "blocked":
            conn_status = "blocked"
            can_message = False
        else:
            conn_status = "rejected"
            can_message = False
        
        return jsonify({
            "status": "success",
            "data": {
                "status": conn_status,
                "can_message": can_message,
                "can_connect": conn_status in ["none", "rejected"],
                "connection_id": connection.id if connection else None,
                "connected_at": connection.responded_at.isoformat() if connection and connection.status == "accepted" else None
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"Connection status error: ", exc_info=True)
        return error_response("Failed to check connection status")




# ============================================================================
# MUTUAL CONNECTIONS
# =========================================================================
@connections_bp.route("/connections/settings", methods=["POST"])
@token_required
def connections_settings(current_user):
    try:
        user = User.query.get(current_user.id)
        if not user:
            return error_response("User not found")
        data = request.get_json()
        enable_sound = data.get("enable_sound", True)
        connection_setting = user.connection_settings
        connection_setting["enable_sound"] = enable_sound
        user.connection_settings = connection_setting
        db.session.commit()
        return success_response("Settings updated successfully")
    except Exception as e:
        current_app.logger.error(f"Connections settings error: ", exc_info=True)
        return error_response("Failed to update connection settings")
        

        

    

        
@connections_bp.route("/connections/block/<int:user_id>", methods=["POST"])
@token_required
def block_user(current_user, user_id):
    """
    Block a user - prevents them from:
    - Sending connection requests
    - Viewing your profile (if private)
    - Messaging you
    
    This also removes any existing connection
    """
    try:
        if user_id == current_user.id:
            return error_response("Cannot block yourself")
        
        target_user = User.query.get(user_id)
        if not target_user:
            return error_response("User not found")
        
        # Check if connection exists
        existing = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == user_id),
                and_(Connection.requester_id == user_id, Connection.receiver_id == current_user.id)
            )
        ).first()
        
        if existing:
            # Update existing connection to blocked
            existing.status = "blocked"
            existing.responded_at = datetime.datetime.utcnow()
            # Make sure current user is always the blocker
            if existing.receiver_id != current_user.id:
                # Swap to make current user the "receiver" (blocker)
                existing.requester_id, existing.receiver_id = existing.receiver_id, existing.requester_id
        else:
            # Create new block record
            block = Connection(
                requester_id=user_id,  # The blocked user
                receiver_id=current_user.id,  # The blocker
                status="blocked"
            )
            db.session.add(block)
        
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


@connections_bp.route("/connections/unblock/<int:user_id>", methods=["POST"])
@token_required
def unblock_user(current_user, user_id):
    """
    Unblock a previously blocked user
    """
    try:
        # Find block record
        block = Connection.query.filter(
            or_(
                and_(Connection.requester_id == current_user.id, Connection.receiver_id == user_id),
                and_(Connection.requester_id == user_id, Connection.receiver_id == current_user.id)
            ),
            Connection.status == "blocked"
        ).first()
        
        if not block:
            return error_response("User is not blocked", 404)
        
        # Verify current user is the blocker
        if block.receiver_id != current_user.id and block.requester_id != current_user.id:
            return error_response("Not authorized", 403)
        
        # Remove block — delete the record so both users can reconnect freely
        db.session.delete(block)
        db.session.commit()
        
        return success_response("User unblocked successfully")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Unblock user error: ", exc_info=True)
        return error_response("Failed to unblock user")


@connections_bp.route("/connections/blocked", methods=["GET"])
@token_required
def list_blocked_users(current_user):
    """
    Get list of all blocked users
    """
    try:
        # Find all users blocked by current user    
        blocked = Connection.query.filter(
            Connection.receiver_id == current_user.id,
            Connection.status == "blocked"
        ).all()
        
        blocked_data = []
        for block in blocked:
            user = User.query.get(block.requester_id)
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