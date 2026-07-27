# ============================================================================
# STUDY SESSION SCHEDULING
# ============================================================================
from flask import Blueprint, request, jsonify, current_app, Response, stream_with_context
from sqlalchemy import or_, and_
import datetime
import os
import uuid

from models import (
    User, Message, Connection, Notification, ThreadMember,
    StudySessionCalendar, LiveStudySession, ConversationAnalytics, Assignment
)
from extensions import db
from utils import limiter, can_message
from routes.student.helpers import (
    token_required, success_response, error_response,
    save_file, ALLOWED_IMAGE_EXT, ALLOWED_DOCUMENT_EXT
)
from services import study_session_service

study_sessions_bp = Blueprint("study_sessions", __name__)


# ============================================================================
# HELPERS
#
# Template catalogue and proposed-time validation now live in
# services/study_session_service.py (pure functions, Document 2 §2). The
# WebSocket-emit helpers below stay here — they're Flask-SocketIO-context
# concerns, not business logic.
# ============================================================================

VALID_TEMPLATE_IDS = study_session_service.VALID_TEMPLATE_IDS
TEMPLATE_DEFAULTS = study_session_service.TEMPLATE_DEFAULTS


def _emit(event, data, room):
    """Emit a WebSocket event safely — always after a commit."""
    try:
        from services.websocket_events import ws_manager
        ws_manager.socketio.emit(event, data, room=room)
    except Exception as exc:
        current_app.logger.warning(f"WS emit '{event}' failed: {exc}")


def _partner_online(session_obj, current_user_id):
    """Return partner_id if they are online, else None."""
    try:
        from services.websocket_events import ws_manager
        partner_id = (
            session_obj.user2_id
            if session_obj.user1_id == current_user_id
            else session_obj.user1_id
        )
        if partner_id in ws_manager.online_users:
            return partner_id
        return None
    except Exception:
        return None


def _validate_proposed_times(times_list, max_times=10):
    """Route-layer wrapper over study_session_service.validate_proposed_times."""
    return study_session_service.validate_proposed_times(times_list, max_times=max_times)


# ============================================================================
# SESSION GOALS & PROGRESS TRACKING
# ============================================================================

@study_sessions_bp.route("/live-session/<int:session_id>/set-goal", methods=["POST"])
@token_required
def set_session_goal(current_user, session_id):
    """Set goal for study session BEFORE or DURING session."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)

        data = request.get_json() or {}
        session.session_goal = data.get("session_goal", "").strip()
        session.target_count = int(data.get("target_count", 0))
        session.assignment_id = data.get("assignment_id")

        db.session.commit()

        # Emit AFTER commit
        partner_id = session.user2_id if session.user1_id == current_user.id else session.user1_id
        _emit(
            'session_goal_set',
            {
                'session_id': session_id,
                'session_goal': session.session_goal,
                'target_count': session.target_count,
                'set_by': current_user.name,
            },
            room=f"user_{partner_id}",
        )

        return success_response("Session goal set!")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Set session goal error: {e}")
        return error_response("Failed to set goal")


@study_sessions_bp.route("/live-session/<int:session_id>/update-progress", methods=["POST"])
@token_required
def update_session_progress(current_user, session_id):
    """Update progress during session (increment completed count)."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)
        if session.status != "active":
            return error_response("Session is not active", 400)

        data = request.get_json() or {}
        session.completed_count = int(data.get("completed_count", session.completed_count or 0))

        if data.get("quick_note"):
            current_notes = session.quick_notes or ""
            timestamp = datetime.datetime.utcnow().strftime("%H:%M")
            session.quick_notes = current_notes + f"[{timestamp}] {data['quick_note']}\n"

        db.session.commit()

        # Emit AFTER commit
        target = session.target_count or 0
        _emit(
            'session_progress_updated',
            {
                'session_id': session_id,
                'completed_count': session.completed_count,
                'target_count': target,
                'progress_percentage': int(session.completed_count / target * 100) if target else 0,
                'updated_by': current_user.name,
            },
            room=f"live_session_{session_id}",
        )

        return success_response("Progress updated!")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update progress error: {e}")
        return error_response("Failed to update progress")


@study_sessions_bp.route("/live-session/<int:session_id>/rate", methods=["POST"])
@token_required
def rate_session(current_user, session_id):
    """Simple thumbs up/down rating after session."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)
        if session.status != "ended":
            return error_response("Can only rate completed sessions", 400)

        data = request.get_json() or {}
        rating = data.get("rating")
        if rating not in ["thumbs_up", "thumbs_down"]:
            return error_response("Invalid rating — use 'thumbs_up' or 'thumbs_down'")

        if session.user1_id == current_user.id:
            session.rating_user1 = rating
        else:
            session.rating_user2 = rating

        db.session.commit()
        return success_response("Thanks for rating!")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Rate session error: {e}")
        return error_response("Failed to rate session")


# ============================================================================
# POMODORO TIMER CONTROL
# ============================================================================

@study_sessions_bp.route("/live-session/<int:session_id>/pomodoro/start", methods=["POST"])
@token_required
def start_pomodoro(current_user, session_id):
    """Start a Pomodoro focus cycle (25 min)."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)
        if session.status != "active":
            return error_response("Session is not active", 400)

        session.current_pomodoro_state = "focus"
        session.timer_started_at = datetime.datetime.utcnow()
        session.timer_is_running = True

        db.session.commit()

        # Emit AFTER commit
        _emit(
            'pomodoro_started',
            {
                'session_id': session_id,
                'started_by': current_user.name,
                'started_at': session.timer_started_at.isoformat(),
                'duration_minutes': 25,
            },
            room=f"live_session_{session_id}",
        )

        return success_response("Pomodoro started! Focus for 25 minutes 🎯")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Start pomodoro error: {e}")
        return error_response("Failed to start pomodoro")


@study_sessions_bp.route("/live-session/<int:session_id>/pomodoro/break", methods=["POST"])
@token_required
def start_break(current_user, session_id):
    """Start a 5-minute break. Requires an active focus cycle."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)
        if session.status != "active":
            return error_response("Session is not active", 400)
        # Guard: must have started a focus cycle first
        if session.current_pomodoro_state != "focus":
            return error_response("Start a focus cycle before taking a break", 400)

        session.pomodoro_cycles_completed = (session.pomodoro_cycles_completed or 0) + 1
        session.current_pomodoro_state = "break"
        session.timer_started_at = datetime.datetime.utcnow()

        db.session.commit()

        # Emit AFTER commit
        _emit(
            'pomodoro_break_started',
            {
                'session_id': session_id,
                'cycles_completed': session.pomodoro_cycles_completed,
                'break_duration_minutes': 5,
            },
            room=f"live_session_{session_id}",
        )

        return success_response("Take a 5 minute break! ☕")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Start break error: {e}")
        return error_response("Failed to start break")


# ============================================================================
# SESSION TEMPLATES
# ============================================================================

@study_sessions_bp.route("/study-session/templates", methods=["GET"])
@token_required
def get_session_templates(current_user):
    """Get pre-made session templates."""
    return jsonify({
        "status": "success",
        "data": {"templates": study_session_service.SESSION_TEMPLATES}
    })


@study_sessions_bp.route("/study-session/schedule-with-template", methods=["POST"])
@token_required
def schedule_session_with_template(current_user):
    """Schedule session using a template."""
    try:
        data = request.get_json() or {}
        template_id = data.get("template_id", "")
        partner_id = data.get("partner_id")

        if not template_id or not partner_id:
            return error_response("template_id and partner_id are required")

        if template_id not in VALID_TEMPLATE_IDS:
            return error_response(f"Invalid template_id. Choose from: {', '.join(VALID_TEMPLATE_IDS)}")

        if not can_message(current_user.id, partner_id):
            return error_response("Must be connected to schedule a session", 403)

        proposed_times_raw = data.get("proposed_times", [])
        validated_times, err = _validate_proposed_times(proposed_times_raw)
        if err:
            return error_response(err)

        defaults = TEMPLATE_DEFAULTS[template_id]

        session = StudySessionCalendar(
            requester_id=current_user.id,
            receiver_id=partner_id,
            title=data.get("title", f"{template_id.replace('_', ' ').title()} Session"),
            subject=data.get("subject", ""),
            description=defaults["goal"],
            duration_minutes=defaults["duration"],
            proposed_times=[t.isoformat() for t in validated_times],
            template_used=template_id,
        )
        db.session.add(session)
        db.session.commit()

        # Notification record
        from services import notification_service
        notification_service.notify(
            user_id=partner_id,
            title="Study Session Request",
            body=f"{current_user.name} wants to schedule a {template_id.replace('_', ' ')} session",
            notification_type="study_session_request",
            related_type="study_session",
            related_id=session.id,
        )
        db.session.commit()

        # Emit AFTER commit
        _emit(
            'study_session_requested',
            {
                'session_id': session.id,
                'from': current_user.name,
                'title': session.title,
                'template': template_id,
            },
            room=f"user_{partner_id}",
        )

        return success_response("Session request sent!", data={"session_id": session.id}), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Schedule with template error: {e}")
        return error_response("Failed to schedule session")


# ============================================================================
# ASSIGNMENT INTEGRATION
# ============================================================================

@study_sessions_bp.route("/live-session/<int:session_id>/link-assignment", methods=["POST"])
@token_required
def link_assignment_to_session(current_user, session_id):
    """Link an assignment to an active study session."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)

        data = request.get_json() or {}
        assignment_id = data.get("assignment_id")
        if not assignment_id:
            return error_response("assignment_id required")

        assignment = Assignment.query.get(assignment_id)
        if not assignment:
            return error_response("Assignment not found", 404)
        if assignment.user_id != current_user.id:
            return error_response("Not your assignment", 403)

        session.assignment_id = assignment_id
        db.session.commit()

        return success_response("Assignment linked to session")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Link assignment error: {e}")
        return error_response("Failed to link assignment")


# ============================================================================
# STUDY SESSION CALENDAR — DETAILS / RESCHEDULE / CANCEL / DECLINE / REQUEST
# ============================================================================

@study_sessions_bp.route("/study-session/<int:session_id>/details", methods=["GET"])
@token_required
def get_study_session_details(current_user, session_id):
    """Get single study session details."""
    try:
        session = StudySessionCalendar.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)

        # Access control: only participants may view
        if session.requester_id != current_user.id and session.receiver_id != current_user.id:
            return error_response("Not authorized", 403)

        other_user_id = session.receiver_id if session.requester_id == current_user.id else session.requester_id
        other_user = User.query.get(other_user_id)

        minutes_until = None
        if session.confirmed_time:
            delta = session.confirmed_time - datetime.datetime.utcnow()
            minutes_until = int(delta.total_seconds() / 60)

        am_requester = session.requester_id == current_user.id

        return jsonify({
            "status": "success",
            "data": {
                "id": session.id,
                "title": session.title,
                "subject": session.subject,
                "description": session.description,
                "status": session.status,
                "duration_minutes": session.duration_minutes,
                "proposed_times": session.proposed_times,
                "confirmed_time": session.confirmed_time.isoformat() if session.confirmed_time else None,
                "requester_notes": session.requester_notes,
                "receiver_notes": session.receiver_notes,
                "requester_resources": session.requester_resources,
                "receiver_resources": session.receiver_resources,
                "created_at": session.created_at.isoformat(),
                "confirmed_at": session.confirmed_at.isoformat() if session.confirmed_at else None,
                "minutes_until": minutes_until,
                "is_upcoming": minutes_until > 0 if minutes_until is not None else False,
                "is_soon": minutes_until <= 60 if minutes_until is not None else False,
                "am_requester": am_requester,
                "partner": {
                    "id": other_user.id,
                    "username": other_user.username,
                    "name": other_user.name,
                    "avatar": other_user.avatar,
                } if other_user else None,
                "can_confirm": session.receiver_id == current_user.id and session.status in ["pending", "rescheduled"],
                "can_reschedule": session.status in ["pending", "confirmed", "rescheduled"] and am_requester,
                "can_cancel": session.status in ["pending", "confirmed", "rescheduled"] and am_requester,
                "can_decline": not am_requester and session.status in ["pending", "rescheduled", "confirmed"],
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get study session details error: {e}")
        return error_response("Failed to load session details")


@study_sessions_bp.route("/study-session/<int:session_id>/reschedule", methods=["POST"])
@token_required
def edit_study_session(current_user, session_id):
    """Edit / reschedule a study session (requester only)."""
    try:
        session = StudySessionCalendar.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.requester_id != current_user.id:
            return error_response("Only requester can edit session", 403)
        if session.status not in ["pending", "confirmed", "rescheduled"]:
            return error_response("Cannot edit this session", 400)

        data = request.get_json() or {}
        times_changed = False

        if "title" in data:
            session.title = data["title"].strip() or "Study Session"
        if "subject" in data:
            session.subject = data["subject"].strip()
        if "description" in data:
            session.description = data["description"].strip()
        if "duration_minutes" in data:
            session.duration_minutes = int(data["duration_minutes"]) or 30
        if "requester_notes" in data:
            session.requester_notes = data["requester_notes"]
        if "requester_resources" in data:
            session.requester_resources = data["requester_resources"] or []
        if "receiver_resources" in data:
            session.receiver_resources = data["receiver_resources"] or []

        if "proposed_times" in data:
            validated_times, err = _validate_proposed_times(data["proposed_times"])
            if err:
                return error_response(err)

            new_time_strs = [t.isoformat() for t in validated_times]

            if study_session_service.times_changed(session.proposed_times, new_time_strs):
                times_changed = True
                session.proposed_times = new_time_strs
                if session.status == "confirmed":
                    session.status = "rescheduled"
                    session.confirmed_time = None

        db.session.commit()

        # Emit AFTER commit
        _emit(
            'study_session_updated',
            {
                'session_id': session.id,
                'title': session.title,
                'subject': session.subject,
                'proposed_times': session.proposed_times,
                'times_changed': times_changed,
                'status': session.status,
                'updated_by': current_user.name,
            },
            room=f"user_{session.receiver_id}",
        )

        msg = "Study session updated - needs re-confirmation" if times_changed else "Study session updated"
        return success_response(msg, data={"session_id": session.id, "times_changed": times_changed, "status": session.status})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Edit study session error: {e}")
        return error_response("Failed to edit session")


@study_sessions_bp.route("/study-session/<int:session_id>/cancel", methods=["POST"])
@token_required
def cancel_study_session(current_user, session_id):
    """Cancel a scheduled study session (requester only)."""
    try:
        session = StudySessionCalendar.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.requester_id != current_user.id:
            return error_response("Only requester can cancel", 403)
        if session.status not in ["pending", "confirmed", "rescheduled"]:
            return error_response("Cannot cancel this session", 400)

        data = request.get_json() or {}
        cancel_reason = data.get("reason", "").strip()

        session.status = "cancelled"
        session.cancelled_at = datetime.datetime.utcnow()
        session.cancelled_by = current_user.id
        session.cancel_reason = cancel_reason

        db.session.commit()

        # Emit AFTER commit
        _emit(
            'study_session_cancelled',
            {
                'session_id': session.id,
                'cancelled_by': current_user.name,
                'reason': cancel_reason,
                'title': session.title,
            },
            room=f"user_{session.receiver_id}",
        )

        return success_response("Study session cancelled")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Cancel study session error: {e}")
        return error_response("Failed to cancel session")


@study_sessions_bp.route("/study-session/<int:session_id>/decline", methods=["POST"])
@token_required
def decline_study_session(current_user, session_id):
    """Decline or withdraw from a study session (receiver only)."""
    try:
        session = StudySessionCalendar.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.receiver_id != current_user.id:
            return error_response("Only receiver can decline or withdraw", 403)
        if session.status not in ["pending", "rescheduled", "confirmed"]:
            return error_response("Cannot decline or withdraw from this session", 400)

        data = request.get_json() or {}
        decline_reason = data.get("reason", "").strip()
        is_withdrawal = session.status == "confirmed"

        session.status = "declined"
        session.cancelled_at = datetime.datetime.utcnow()
        session.cancelled_by = current_user.id
        session.decline_reason = decline_reason

        db.session.commit()

        # Emit AFTER commit
        _emit(
            'study_session_declined',
            {
                'session_id': session.id,
                'declined_by': current_user.name,
                'is_withdrawal': is_withdrawal,
                'reason': decline_reason,
                'title': session.title,
            },
            room=f"user_{session.requester_id}",
        )

        action_text = "withdrew from" if is_withdrawal else "declined"
        return success_response(f"Study session {action_text}")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Decline study session error: {e}")
        return error_response("Failed to decline session")


@study_sessions_bp.route("/study-session/request", methods=["POST"])
@token_required
def request_study_session(current_user):
    """Request a study session with proposed times."""
    try:
        data = request.get_json() or {}
        receiver_id = data.get("receiver_id")
        title = data.get("title", "Study Session").strip()
        subject = data.get("subject", "").strip()
        description = data.get("description", "").strip()
        duration_minutes = int(data.get("duration_minutes", 30))
        resources = data.get("resources", [])
        notes = data.get("notes", "")

        if not receiver_id:
            return error_response("receiver_id required")
        if not can_message(current_user.id, receiver_id):
            return error_response("Must be connected to request session", 403)

        validated_times, err = _validate_proposed_times(data.get("proposed_times", []))
        if err:
            return error_response(err)

        session_request = StudySessionCalendar(
            requester_id=current_user.id,
            receiver_id=receiver_id,
            title=title,
            subject=subject,
            description=description,
            proposed_times=[t.isoformat() for t in validated_times],
            duration_minutes=duration_minutes,
            requester_resources=resources,
            requester_notes=notes,
        )
        db.session.add(session_request)
        db.session.flush()

        message = Message(
            sender_id=current_user.id,
            receiver_id=receiver_id,
            subject="Study Session Request",
            body=f"📅 {current_user.name} invited you to a study session: {title}",
            related_session_id=session_request.id,
            sent_at=datetime.datetime.utcnow(),
        )
        db.session.add(message)
        session_request.message_id = message.id

        db.session.commit()

        # Emit AFTER commit
        _emit(
            'study_session_request',
            {
                'session_id': session_request.id,
                'title': title,
                'subject': subject,
                'proposed_times': [t.isoformat() for t in validated_times],
                'duration_minutes': duration_minutes,
                'requester': {
                    'id': current_user.id,
                    'name': current_user.name,
                    'avatar': current_user.avatar,
                },
            },
            room=f"user_{receiver_id}",
        )

        return success_response(
            "Study session requested",
            data={"session_id": session_request.id, "message_id": message.id},
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Request study session error: {e}")
        return error_response("Failed to request session")


@study_sessions_bp.route("/study-session/<int:session_id>/confirm", methods=["POST"])
@token_required
def confirm_study_session(current_user, session_id):
    """Confirm study session by choosing one of the proposed times."""
    try:
        session = StudySessionCalendar.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.receiver_id != current_user.id:
            return error_response("Only receiver can confirm", 403)
        if session.status not in ["pending", "rescheduled"]:
            return error_response("Session is not pending or rescheduled", 400)

        data = request.get_json() or {}
        confirmed_time_str = data.get("confirmed_time")
        if not confirmed_time_str:
            return error_response("confirmed_time required")

        try:
            confirmed_time = datetime.datetime.fromisoformat(confirmed_time_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return error_response("Invalid time format — use ISO 8601")

        # Validate that the chosen time is one of the proposed times
        if not study_session_service.confirmed_time_matches_proposed(
            confirmed_time, session.proposed_times or []
        ):
            return error_response("confirmed_time must be one of the proposed times")

        session.confirmed_time = confirmed_time
        session.status = "confirmed"
        session.confirmed_at = datetime.datetime.utcnow()
        session.receiver_notes = data.get("receiver_notes", "")

        db.session.commit()

        # Emit AFTER commit
        _emit(
            'study_session_confirmed',
            {
                'session_id': session.id,
                'confirmed_time': confirmed_time.isoformat(),
                'receiver_name': current_user.name,
            },
            room=f"user_{session.requester_id}",
        )

        return success_response("Study session confirmed")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Confirm session error: {e}")
        return error_response("Failed to confirm session")


@study_sessions_bp.route("/study-session/upcoming", methods=["GET"])
@token_required
def get_upcoming_study_sessions(current_user):
    """Get all upcoming confirmed study sessions."""
    try:
        now = datetime.datetime.utcnow()
        sessions = StudySessionCalendar.query.filter(
            or_(
                StudySessionCalendar.requester_id == current_user.id,
                StudySessionCalendar.receiver_id == current_user.id,
            ),
            StudySessionCalendar.status == "confirmed",
            StudySessionCalendar.confirmed_time >= now,
        ).order_by(StudySessionCalendar.confirmed_time.asc()).all()

        # Gather partner IDs and batch-fetch users
        partner_ids = {
            s.receiver_id if s.requester_id == current_user.id else s.requester_id
            for s in sessions
        }
        users_map = {u.id: u for u in User.query.filter(User.id.in_(partner_ids)).all()}

        sessions_data = []
        for session in sessions:
            other_user_id = session.receiver_id if session.requester_id == current_user.id else session.requester_id
            other_user = users_map.get(other_user_id)
            minutes_until = int((session.confirmed_time - now).total_seconds() / 60)

            sessions_data.append({
                "id": session.id,
                "title": session.title,
                "subject": session.subject,
                "confirmed_time": session.confirmed_time.isoformat(),
                "duration_minutes": session.duration_minutes,
                "requester_notes": session.requester_notes,
                "requester_resources": session.requester_resources,
                "receiver_notes": session.receiver_notes,
                "receiver_resources": session.receiver_resources,
                "cancel_reason": session.cancel_reason,
                "decline_reason": session.decline_reason,
                "minutes_until": minutes_until,
                "is_soon": minutes_until <= 60,
                "partner": {
                    "id": other_user.id,
                    "username": other_user.username,
                    "name": other_user.name,
                    "avatar": other_user.avatar,
                } if other_user else None,
                "am_requester": session.requester_id == current_user.id,
            })

        return jsonify({"status": "success", "data": {"upcoming_sessions": sessions_data, "total": len(sessions_data)}})

    except Exception as e:
        current_app.logger.error(f"Get upcoming sessions error: {e}")
        return error_response("Failed to load upcoming sessions")


@study_sessions_bp.route("/study-session/all", methods=["GET"])
@token_required
def get_all_study_sessions(current_user):
    """Get all study sessions (pending, confirmed, completed, cancelled)."""
    try:
        partner_id = request.args.get("partner_id", type=int)
        status_filter = request.args.get("status")

        query = StudySessionCalendar.query.filter(
            or_(
                StudySessionCalendar.requester_id == current_user.id,
                StudySessionCalendar.receiver_id == current_user.id,
            )
        )

        if partner_id:
            if not can_message(current_user.id, partner_id):
                return error_response("Must be connected to view sessions with this user", 403)
            query = query.filter(
                or_(
                    and_(
                        StudySessionCalendar.requester_id == current_user.id,
                        StudySessionCalendar.receiver_id == partner_id,
                    ),
                    and_(
                        StudySessionCalendar.requester_id == partner_id,
                        StudySessionCalendar.receiver_id == current_user.id,
                    ),
                )
            )

        if status_filter and status_filter != "all":
            query = query.filter(StudySessionCalendar.status == status_filter)

        sessions = query.order_by(StudySessionCalendar.created_at.desc()).all()

        # Batch-fetch partner users
        partner_ids = {
            s.receiver_id if s.requester_id == current_user.id else s.requester_id
            for s in sessions
        }
        users_map = {u.id: u for u in User.query.filter(User.id.in_(partner_ids)).all()}

        now = datetime.datetime.utcnow()
        sessions_data = []
        for session in sessions:
            other_user_id = session.receiver_id if session.requester_id == current_user.id else session.requester_id
            other_user = users_map.get(other_user_id)
            am_requester = session.requester_id == current_user.id

            minutes_until = None
            if session.confirmed_time:
                minutes_until = int((session.confirmed_time - now).total_seconds() / 60)

            sessions_data.append({
                "id": session.id,
                "title": session.title,
                "subject": session.subject,
                "description": session.description,
                "status": session.status,
                "duration_minutes": session.duration_minutes,
                "proposed_times": session.proposed_times,
                "confirmed_time": session.confirmed_time.isoformat() if session.confirmed_time else None,
                "requester_notes": session.requester_notes,
                "receiver_notes": session.receiver_notes,
                "requester_resources": session.requester_resources,
                "receiver_resources": session.receiver_resources,
                "created_at": session.created_at.isoformat(),
                "confirmed_at": session.confirmed_at.isoformat() if session.confirmed_at else None,
                "minutes_until": minutes_until,
                "is_upcoming": minutes_until > 0 if minutes_until is not None else False,
                "is_soon": minutes_until <= 60 if minutes_until is not None else False,
                "am_requester": am_requester,
                "partner": {
                    "id": other_user.id,
                    "username": other_user.username,
                    "name": other_user.name,
                    "avatar": other_user.avatar,
                } if other_user else None,
                "can_confirm": session.receiver_id == current_user.id and session.status in ["pending", "rescheduled"],
                "can_reschedule": session.status in ["pending", "rescheduled", "confirmed"] and am_requester,
                "can_decline": not am_requester and session.status in ["pending", "rescheduled", "confirmed"],
                "can_cancel": session.status in ["pending", "confirmed", "rescheduled"] and am_requester,
            })

        partner_info = None
        if partner_id:
            p = users_map.get(partner_id) or User.query.get(partner_id)
            if p:
                partner_info = {"id": p.id, "username": p.username, "name": p.name, "avatar": p.avatar}

        return jsonify({
            "status": "success",
            "data": {
                "sessions": sessions_data,
                "total": len(sessions_data),
                "filter": {"partner_id": partner_id, "status": status_filter},
                "partner_info": partner_info,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get all study sessions error: {e}")
        return error_response("Failed to load study sessions")


# ============================================================================
# LIVE STUDY SESSIONS
# ============================================================================

@study_sessions_bp.route("/live-session/start", methods=["POST"])
@limiter.limit("20 per day")
@token_required
def start_live_study_session(current_user):
    """Start a live study session with a partner."""
    try:
        data = request.get_json() or {}
        partner_id = data.get("partner_id")
        title = data.get("title", "Study Session").strip()
        subject = data.get("subject", "").strip()

        if not partner_id:
            return error_response("partner_id required")
        if not can_message(current_user.id, partner_id):
            return error_response("Must be connected to start session", 403)

        existing = LiveStudySession.query.filter(
            or_(
                and_(LiveStudySession.user1_id == current_user.id, LiveStudySession.user2_id == partner_id),
                and_(LiveStudySession.user1_id == partner_id, LiveStudySession.user2_id == current_user.id),
            ),
            LiveStudySession.status == "active",
        ).first()

        if existing:
            return error_response("Active session already exists", 409)

        sorted_ids = sorted([current_user.id, partner_id])
        session_key = f"live_{sorted_ids[0]}_{sorted_ids[1]}_{datetime.datetime.utcnow().timestamp()}"

        session = LiveStudySession(
            user1_id=sorted_ids[0],
            user2_id=sorted_ids[1],
            session_key=session_key,
            title=title,
            subject=subject,
            notepad_content="# Study Notes\n\n",
            user1_timer_state={"is_running": False, "elapsed": 0},
            user2_timer_state={"is_running": False, "elapsed": 0},
        )
        db.session.add(session)
        db.session.commit()

        # Emit AFTER commit
        _emit(
            'live_session_started',
            {
                'session_id': session.id,
                'session_key': session_key,
                'title': title,
                'subject': subject,
                'starter': {'id': current_user.id, 'name': current_user.name, 'avatar': current_user.avatar},
            },
            room=f"user_{partner_id}",
        )

        return success_response("Live session started", data={"session_id": session.id, "session_key": session_key}), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Start live session error: {e}")
        return error_response("Failed to start session")


@study_sessions_bp.route("/live-session/<int:session_id>", methods=["GET"])
@token_required
def get_live_study_session(current_user, session_id):
    """Get live session details."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)

        partner_id = session.user2_id if session.user1_id == current_user.id else session.user1_id
        partner = User.query.get(partner_id)

        # total_duration_seconds is now a real column (migration 001)
        duration_seconds = session.total_duration_seconds or 0
        if session.status == "active":
            duration_seconds += int((datetime.datetime.utcnow() - session.started_at).total_seconds())

        return jsonify({
            "status": "success",
            "data": {
                "session_id": session.id,
                "session_key": session.session_key,
                "title": session.title,
                "subject": session.subject,
                "status": session.status,
                "notepad_content": session.notepad_content,
                "topics_covered": session.topics_covered or [],
                "problems_solved": session.problems_solved,
                "duration_seconds": duration_seconds,
                "started_at": session.started_at.isoformat(),
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                "partner": {
                    "id": partner.id,
                    "username": partner.username,
                    "name": partner.name,
                    "avatar": partner.avatar,
                } if partner else None,
                "your_timer": session.user1_timer_state if session.user1_id == current_user.id else session.user2_timer_state,
                "partner_timer": session.user2_timer_state if session.user1_id == current_user.id else session.user1_timer_state,
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get live session error: {e}")
        return error_response("Failed to load session")


@study_sessions_bp.route("/live-session/<int:session_id>/end", methods=["POST"])
@token_required
def end_live_study_session(current_user, session_id):
    """End live study session and generate summary."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)
        if session.status != "active":
            return error_response("Session is not active", 400)

        data = request.get_json() or {}
        topics_covered = data.get("topics_covered", [])
        problems_solved = int(data.get("problems_solved", 0))

        session.status = "ended"
        session.ended_at = datetime.datetime.utcnow()
        session.topics_covered = topics_covered
        session.problems_solved = problems_solved

        total_seconds = int((session.ended_at - session.started_at).total_seconds())
        session.total_duration_seconds = total_seconds

        session.session_log = {
            "started_at": session.started_at.isoformat(),
            "ended_at": session.ended_at.isoformat(),
            "duration_minutes": round(total_seconds / 60, 1),
            "topics_covered": topics_covered,
            "problems_solved": problems_solved,
            "notepad_final": session.notepad_content,
            "ended_by": current_user.id,
        }

        db.session.commit()

        # Update conversation analytics
        partner_id = session.user2_id if session.user1_id == current_user.id else session.user1_id
        sorted_ids = sorted([current_user.id, partner_id])
        conv_key = f"{sorted_ids[0]}-{sorted_ids[1]}"
        analytics = ConversationAnalytics.query.filter_by(conversation_key=conv_key).first()
        if analytics:
            analytics.total_study_sessions += 1
            analytics.total_study_time_hours += round(total_seconds / 3600, 2)
            db.session.commit()

        # Emit AFTER commit
        _emit(
            'live_session_ended',
            {'session_id': session.id, 'session_log': session.session_log, 'ended_by': current_user.name},
            room=f"user_{partner_id}",
        )

        return success_response("Session ended", data={"session_log": session.session_log})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"End live session error: {e}")
        return error_response("Failed to end session")


@study_sessions_bp.route("/live-session/<int:session_id>/cancel", methods=["POST"])
@token_required
def cancel_live_study_session(current_user, session_id):
    """Cancel an active live study session."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)
        if session.status != "active":
            return error_response("Session is not active", 400)

        data = request.get_json() or {}
        reason = data.get("reason", "Session cancelled")

        session.status = "cancelled"
        session.ended_at = datetime.datetime.utcnow()
        total_seconds = int((session.ended_at - session.started_at).total_seconds())
        session.total_duration_seconds = total_seconds

        session.session_log = {
            "started_at": session.started_at.isoformat(),
            "ended_at": session.ended_at.isoformat(),
            "duration_minutes": round(total_seconds / 60, 1),
            "topics_covered": session.topics_covered or [],
            "problems_solved": session.problems_solved,
            "cancelled_by": current_user.id,
            "cancel_reason": reason,
            "notepad_final": session.notepad_content,
            "resources_count": len(session.resources or []),
        }

        db.session.commit()

        # Emit AFTER commit
        _emit(
            'session_cancelled',
            {
                'session_id': session_id,
                'cancelled_by': current_user.name,
                'reason': reason,
                'session_log': session.session_log,
            },
            room=f"live_session_{session_id}",
        )

        return success_response("Session cancelled", data={"session_log": session.session_log})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Cancel session error: {e}")
        return error_response("Failed to cancel session")


@study_sessions_bp.route("/live-session/<int:partner_id>/history", methods=["GET"])
@token_required
def get_live_session_history(current_user, partner_id):
    """Get completed live session history with a specific partner."""
    try:
        if not can_message(current_user.id, partner_id):
            return error_response("Must be connected to view history", 403)

        sessions = LiveStudySession.query.filter(
            or_(
                and_(LiveStudySession.user1_id == current_user.id, LiveStudySession.user2_id == partner_id),
                and_(LiveStudySession.user1_id == partner_id, LiveStudySession.user2_id == current_user.id),
            ),
            LiveStudySession.status == "ended",
        ).order_by(LiveStudySession.ended_at.desc()).limit(20).all()

        history_data = [
            {
                "id": s.id,
                "title": s.title,
                "subject": s.subject,
                "started_at": s.started_at.isoformat(),
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "duration_minutes": round((s.total_duration_seconds or 0) / 60, 1),
                "topics_covered": s.topics_covered or [],
                "problems_solved": s.problems_solved,
                "session_log": s.session_log,
            }
            for s in sessions
        ]

        partner = User.query.get(partner_id)
        return jsonify({
            "status": "success",
            "data": {
                "partner": {
                    "id": partner.id,
                    "username": partner.username,
                    "name": partner.name,
                    "avatar": partner.avatar,
                } if partner else None,
                "sessions": history_data,
                "total_sessions": len(history_data),
                "total_hours": round(sum(s["duration_minutes"] for s in history_data) / 60, 1),
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get session history error: {e}")
        return error_response("Failed to load session history")


# ============================================================================
# LIVE SESSION AI ASSISTANT
# ============================================================================

@study_sessions_bp.route("/live-session/<int:session_id>/ai/ask", methods=["POST"])
@limiter.limit("30 per hour")
@token_required
def ai_ask_in_session(current_user, session_id):
    """Ask the AI study assistant a question within a live session (streaming)."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)

        data = request.get_json() or {}
        question = data.get("question", "").strip()
        if not question:
            return error_response("Question required")
        if len(question) > 2000:
            return error_response("Question too long (max 2000 characters)")

        api_key = os.getenv("OPENROUTER_API_KEY_1")
        if not api_key:
            return error_response("AI service not configured", 503)

        ai_messages = list(session.ai_messages or [])
        ai_messages.append({
            "role": "user",
            "content": question,
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "asked_by": current_user.id,
        })

        system_prompt = (
            f"You are an AI study assistant helping students learn "
            f"{session.subject or 'various topics'}.\n"
            f'Current study session: "{session.title}"\n'
            f"Notepad content (for context):\n{(session.notepad_content or '')[:1000]}\n\n"
            f"Provide clear, educational explanations. Use examples when helpful."
        )

        messages_for_api = [{"role": "system", "content": system_prompt}]
        for msg in ai_messages[-20:]:
            messages_for_api.append({"role": msg["role"], "content": msg["content"]})

        partner_id = session.user2_id if session.user1_id == current_user.id else session.user1_id

        def generate():
            import requests as _requests
            import json as _json

            full_response = ""
            try:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://learnora-study.com",
                    "X-Title": "Learnora Study Assistant",
                }
                payload = {
                    "model": "google/gemini-2.0-flash-exp:free",
                    "messages": messages_for_api,
                    "stream": True,
                }
                resp = _requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=60,
                )

                for line in resp.iter_lines():
                    if not line:
                        continue
                    text = line.decode("utf-8")
                    if text.startswith("data: "):
                        text = text[6:]
                    if text == "[DONE]":
                        break
                    try:
                        chunk = _json.loads(text)
                        content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content:
                            full_response += content
                            yield f"data: {_json.dumps({'content': content})}\n\n"
                    except _json.JSONDecodeError:
                        continue

            except Exception:
                # Do NOT log exception details that could include the API key
                current_app.logger.error("AI stream request failed")
                yield f"data: {_json.dumps({'error': 'AI request failed'})}\n\n"
                return

            # Persist AI reply
            try:
                ai_messages.append({
                    "role": "assistant",
                    "content": full_response,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                })
                # Re-fetch session inside generator to avoid stale state
                from extensions import db as _db
                live_s = LiveStudySession.query.get(session_id)
                if live_s:
                    live_s.ai_messages = ai_messages
                    _db.session.commit()
            except Exception as persist_err:
                current_app.logger.error(f"AI message persist error: {persist_err}")

            yield f"data: {_json.dumps({'type': 'done'})}\n\n"

            # Notify partner AFTER everything is done
            _emit(
                'ai_used_in_session',
                {'session_id': session_id, 'question': question[:100], 'asked_by': current_user.name},
                room=f"user_{partner_id}",
            )

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    except Exception as e:
        current_app.logger.error(f"AI ask error: {e}")
        return error_response("Failed to process AI request")


@study_sessions_bp.route("/live-session/<int:session_id>/ai/history", methods=["GET"])
@token_required
def get_ai_history(current_user, session_id):
    """Get AI conversation history for this session."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)

        msgs = session.ai_messages or []
        return jsonify({"status": "success", "data": {"messages": msgs, "total": len(msgs)}})

    except Exception as e:
        current_app.logger.error(f"Get AI history error: {e}")
        return error_response("Failed to load AI history")


# ============================================================================
# SESSION RESOURCES MANAGEMENT
# ============================================================================

@study_sessions_bp.route("/live-session/<int:session_id>/resource/add", methods=["POST"])
@token_required
def add_session_resource(current_user, session_id):
    """Add a resource to a live study session."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)

        data = request.get_json() or {}
        resource_name = data.get("name")
        resource_url = data.get("url")
        if not resource_url:
            return error_response("url is required")

        resource_id = str(uuid.uuid4())
        resource = {
            "id": resource_id,
            "name": resource_name,
            "url": resource_url,
            "type": data.get("type"),
            "size_bytes": data.get("size_bytes"),
            "uploaded_by": current_user.id,
            "uploaded_at": datetime.datetime.utcnow().isoformat(),
        }

        resources = list(session.resources or [])
        resources.append(resource)
        session.resources = resources
        db.session.commit()

        # Emit AFTER commit
        _emit('resource_added', resource, room=f"live_session_{session_id}")

        return success_response("Resource added", data={"resource_id": resource_id})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Add resource error: {e}")
        return error_response("Failed to add resource")


@study_sessions_bp.route("/live-session/<int:session_id>/resource/<resource_id>", methods=["DELETE"])
@token_required
def remove_session_resource(current_user, session_id, resource_id):
    """Remove a resource from a live study session."""
    try:
        session = LiveStudySession.query.get(session_id)
        if not session:
            return error_response("Session not found", 404)
        if session.user1_id != current_user.id and session.user2_id != current_user.id:
            return error_response("Not authorized", 403)

        resources = list(session.resources or [])
        updated = [r for r in resources if r.get("id") != resource_id]
        if len(updated) == len(resources):
            return error_response("Resource not found", 404)

        session.resources = updated
        db.session.commit()

        # Emit AFTER commit
        _emit('resource_removed', {"resource_id": resource_id}, room=f"live_session_{session_id}")

        return success_response("Resource removed")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Remove resource error: {e}")
        return error_response("Failed to remove session resource")
