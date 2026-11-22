"""
StudyHub - Connections: Connection Request CRUD + Help Requests

Split from connections.py per Document 1 (Architecture Refactor) §2.1 as
part of Phase 2 (God-file splitting). This is a pure move — function
bodies, decorators, routes, and logic are unchanged from the original
connections.py. See routes/student/connections/__init__.py for the
sub-blueprint aggregation that re-exposes all routes under the same
paths as before.
"""

from flask import Blueprint, request, jsonify, current_app, Response, stream_with_context
from sqlalchemy import or_, and_, case
# AUDIT ENG-2 FIX: catches the new uq_connections_pair unique index
# (models.py) firing on a genuine reverse-duplicate race in
# send_connection_request below.
from sqlalchemy.exc import IntegrityError
from datetime import timedelta
import datetime
import json
import logging
import math
import random

from models import (
    User, StudentProfile, Connection,
    HelpRequest, HelpRequestVolunteer, Thread, ThreadMember,
    OnboardingDetails, Message
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response,
    block_connection, unblock_connection,
    is_request_authorized_for_email,
)

from services.online_status_service import get_user_online_status, get_online_status_batch
from services.ai_provider_service import provider_manager, StudyAssistant
from services.connection_service import (
    calculate_compatibility_score,
    calculate_schedule_overlap,
    gather_user_data,
    calculate_compatibility,
    get_recent_activity,
    get_mutual_connection_count,
    get_connection_health,
    get_connection_health_batch,
    get_user_onboarding_preview,
    notify_after_commit,
    clean_user_text,
    accept_pending_connection,
    reject_pending_connection,
    cancel_pending_connection,
    remove_accepted_connection,
    reopen_rejected_connection,
    get_accepted_partner_ids,
    invalidate_pair_caches,
)
# Plan §5.3/§17.6: every direct Notification(...) construction in this file
# is migrated to call notification_service.notify() (or a named template)
# instead — this is the prerequisite for the unread-notification-count
# Redis counter (services/counter_cache_service.py, incremented inside
# notify() itself) to be accurate for connection/help-request notifications.
# Before this migration, notifications created via direct construction here
# never triggered the increment, silently undercounting.
from services import notification_service
from services.activity_service import record_live_activity
# Phase 5b (Document 4 §1): send/accept/reject requests, help-broadcast/
# volunteer, block-adjacent actions -> WRITE_HEAVY; mark-seen/status checks
# -> BURST_OK; list/received/sent -> PUBLIC_READ; onboarding routes (no
# token yet, pre-auth) -> SENSITIVE_AUTH + ip_key since they mutate
# connection state before a session exists, same risk profile as auth.py.
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key, ip_key

logger = logging.getLogger(__name__)

connections_crud_bp = Blueprint("connections_crud", __name__)
CONNECTION_MESSAGE_MAX_LEN = 300   # audit CON-04
HELP_REQUEST_LINK = "/student/connections/help/{id}"   # real route (CON-17)
ONBOARDING_CONNECT_MAX_IDS = 25
_ONBOARDING_ACCOUNT_AGE = datetime.timedelta(hours=48)


def _resolve_onboarding_user(email):
    """
    Authorize + load the user for the onboarding connect routes (audit CON-01).

    Returns (user, None) or (None, error_response). Requirements:
      * the caller proves they own `email` (Google session or JWT cookie) -
        a raw email in a URL is NOT a credential;
      * the account is still onboarding: pending_onboarding/pending_verification,
        or brand new (<48h) so an immediately-approved Google signup works;
        established accounts must use the normal request/accept flow.
    """
    if not email or not is_request_authorized_for_email(email):
        return None, error_response("Unauthorized", 401)

    user = User.query.filter_by(email=email).first()
    if not user:
        return None, error_response("Unauthorized", 401)

    is_new = bool(user.joined_at and datetime.datetime.utcnow() - user.joined_at < _ONBOARDING_ACCOUNT_AGE)
    if user.status not in ("pending_onboarding", "pending_verification") and not is_new:
        return None, error_response("Onboarding is already complete", 403)

    return user, None


def _receiver_payload(user, with_reputation=False):
    payload = {
        "id": user.id,
        "name": user.name,
        "username": user.username,
        "avatar": user.avatar,
    }
    if with_reputation:
        payload["reputation_level"] = user.reputation_level
    return payload


def _pair_row(user_id, other_id):
    return Connection.query.filter(
        or_(
            and_(Connection.requester_id == user_id, Connection.receiver_id == other_id),
            and_(Connection.requester_id == other_id, Connection.receiver_id == user_id),
        )
    ).first()


@connections_crud_bp.route("/connections/suggestions-by-email/<email>", methods=["GET"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def connections_suggestions_by_email(email):
    """
    Onboarding suggestions for the (authenticated) onboarding user.
    Returns smart match list based on subjects, learning style, schedule.
    """
    try:
        user, err = _resolve_onboarding_user(email)
        if err:
            return err

        onboarding = OnboardingDetails.query.filter_by(user_id=user.id).first()

        # Never suggest anyone this user already has ANY relationship with
        # (blocked / rejected / pending / accepted).
        related_ids = {
            (c.receiver_id if c.requester_id == user.id else c.requester_id)
            for c in Connection.query.filter(
                or_(Connection.requester_id == user.id, Connection.receiver_id == user.id)
            ).all()
        }

        # OPTIMIZED: Single join query replaces O(N) per-user queries.
        # Fetches all candidates with their profiles and onboarding in one pass.
        candidates_data = (
            db.session.query(User, StudentProfile, OnboardingDetails)
            .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
            .outerjoin(OnboardingDetails, OnboardingDetails.user_id == User.id)
            .filter(User.id != user.id, User.status == "approved")
            .filter(~User.id.in_(related_ids) if related_ids else True)
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

@connections_crud_bp.route("/connections/onboard-connect/<email>/<int:target_user_id>", methods=["POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def onboard_connect_single(email, target_user_id):
    """
    Onboarding: connect with a single user and immediately set status=accepted.

    Audit CON-01: requires proof of email ownership and an account that is
    still onboarding, and is CREATE-ONLY: it never modifies an existing row,
    so it can no longer force-accept a pending/rejected request or lift a
    block. Idempotent: an already-connected pair returns success.
    """
    try:
        user, err = _resolve_onboarding_user(email)
        if err:
            return err

        if user.id == target_user_id:
            return error_response("Cannot connect with yourself", 400)

        target = User.query.get(target_user_id)
        if not target or target.status != "approved":
            return error_response("Target user not found", 404)

        existing = _pair_row(user.id, target_user_id)
        if existing:
            if existing.status == "accepted":
                return jsonify({"status": "success", "message": "Already connected",
                                "data": {"connection_id": existing.id}})
            # pending / rejected / blocked: leave untouched, never override.
            return error_response("Cannot connect with this user right now", 409)

        now = datetime.datetime.utcnow()
        connection = Connection(
            requester_id=user.id,
            receiver_id=target_user_id,
            status="accepted",
            requested_at=now,
            responded_at=now,
            requester_notes=None,
            requester_seen=True,
            receiver_seen=False,
        )
        db.session.add(connection)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing = _pair_row(user.id, target_user_id)
            if existing and existing.status == "accepted":
                return jsonify({"status": "success", "message": "Already connected",
                                "data": {"connection_id": existing.id}})
            return error_response("Cannot connect with this user right now", 409)

        connection_id = connection.id
        # Notify only AFTER the row is committed (audit CON-09).
        notify_after_commit(
            notification_service.notify,
            user_id=target_user_id,
            title=f"{user.name} connected with you!",
            body=f"{user.name} just joined StudyHub and connected with you.",
            notification_type="connection_accepted",
            related_type="user",
            related_id=user.id,
        )

        return jsonify({
            "status":  "success",
            "message": "Connected successfully",
            "data":    {"connection_id": connection_id, "status": "accepted"}
        }), 201

    except Exception:
        db.session.rollback()
        current_app.logger.error("onboard_connect_single error", exc_info=True)
        return error_response("Failed to create connection", 500)


# 3. BULK DIRECT-ACCEPT CONNECT (ONBOARDING "CONNECT ALL")

@connections_crud_bp.route("/connections/onboard-connect-all/<email>", methods=["POST"])
@limiter.limit(RateLimitTier.SENSITIVE_AUTH, key_func=ip_key)
def onboard_connect_all(email):
    """
    Onboarding: connect with all supplied user IDs at once (accepted).
    Body: { "ids": [1, 2, 3] }  (max ONBOARDING_CONNECT_MAX_IDS)

    Audit CON-01 / CON-15: authenticated, create-only, id list validated and
    capped, targets and existing rows loaded in two batched queries, and
    notifications sent only after the commit.
    """
    try:
        user, err = _resolve_onboarding_user(email)
        if err:
            return err

        data = request.get_json(silent=True) or {}
        raw_ids = data.get("ids", [])
        if not isinstance(raw_ids, list) or not raw_ids:
            return error_response("No user IDs provided", 400)
        if len(raw_ids) > ONBOARDING_CONNECT_MAX_IDS:
            return error_response(
                f"Too many users (max {ONBOARDING_CONNECT_MAX_IDS})", 400)

        ids = []
        for raw in raw_ids:
            if isinstance(raw, bool) or not isinstance(raw, int):
                return error_response("IDs must be integers", 400)
            if raw != user.id and raw not in ids:
                ids.append(raw)
        if not ids:
            return error_response("No valid user IDs provided", 400)

        approved = {
            u.id for u in User.query.filter(User.id.in_(ids), User.status == "approved").all()
        }
        existing_rows = Connection.query.filter(
            or_(
                and_(Connection.requester_id == user.id, Connection.receiver_id.in_(ids)),
                and_(Connection.receiver_id == user.id, Connection.requester_id.in_(ids)),
            )
        ).all()
        existing_by_other = {
            (c.receiver_id if c.requester_id == user.id else c.requester_id): c
            for c in existing_rows
        }

        connected, skipped, created = [], [], []
        now = datetime.datetime.utcnow()

        for target_id in ids:
            if target_id not in approved:
                skipped.append(target_id)
                continue

            existing = existing_by_other.get(target_id)
            if existing:
                # Already accepted counts as connected; anything else is left
                # untouched (never override pending/rejected/blocked).
                (connected if existing.status == "accepted" else skipped).append(target_id)
                continue

            connection = Connection(
                requester_id=user.id,
                receiver_id=target_id,
                status="accepted",
                requested_at=now,
                responded_at=now,
                requester_seen=True,
                receiver_seen=False,
            )
            # SAVEPOINT per row: a lost uq_connections_pair race only skips
            # this one target.
            try:
                with db.session.begin_nested():
                    db.session.add(connection)
                    db.session.flush()
            except IntegrityError:
                skipped.append(target_id)
                continue

            created.append(target_id)
            connected.append(target_id)

        db.session.commit()

        if created:
            def _notify_all():
                for target_id in created:
                    notification_service.notify(
                        user_id=target_id,
                        title=f"{user.name} connected with you!",
                        body=f"{user.name} just joined and connected with you on StudyHub.",
                        notification_type="connection_accepted",
                        related_type="user",
                        related_id=user.id,
                    )
            notify_after_commit(_notify_all)

        return jsonify({
            "status":  "success",
            "message": f"Connected with {len(connected)} user(s)",
            "data": {
                "connected_count": len(connected),
                "connected_ids":   connected,
                "skipped_ids":     skipped
            }
        })

    except Exception:
        db.session.rollback()
        current_app.logger.error("onboard_connect_all error", exc_info=True)
        return error_response("Failed to connect with all users", 500)

# ADD TO connections.py (after other endpoints)

HELP_REQUEST_SUBJECT_MAX_LEN = 100   # HelpRequest.subject is String(100)
HELP_REQUEST_MESSAGE_MAX_LEN = 300   # HelpRequest.message is String(300)
HELP_REQUEST_MAX_TARGETS = 10
HELP_REQUEST_TTL_HOURS = 2


def _score_help_targets(partner_ids, subject_lower):
    """
    Relevance of each connection for `subject_lower`, using batch-loaded users
    and onboarding rows (2 queries instead of 2 per connection - CON-15).
    Returns [(user, score)] for score > 0 only, best first, ties broken by id
    so the result is deterministic.
    """
    if not partner_ids:
        return []
    users = {u.id: u for u in User.query.filter(User.id.in_(partner_ids)).all()}
    onboarding = {
        o.user_id: o
        for o in OnboardingDetails.query.filter(OnboardingDetails.user_id.in_(partner_ids)).all()
    }
    scored = []
    for uid in partner_ids:
        user, ob = users.get(uid), onboarding.get(uid)
        if not user or not ob:
            continue
        score = 0
        if any(subject_lower in subj.lower() for subj in (ob.subjects or [])):
            score += 30
        if any(subject_lower in subj.lower() for subj in (ob.strong_subjects or [])):
            score += 50
        if score > 0:   # irrelevant connections are not pinged (CON-17)
            scored.append((user, score))
    scored.sort(key=lambda x: (-x[1], x[0].id))
    return scored


@connections_crud_bp.route("/connections/help/broadcast", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def broadcast_help_request(current_user):
    """
    Create a help request and notify the most relevant connections via push +
    in-app notification.

    Audit CON-17:
      * input is validated (type, length vs the String(100)/String(300)
        columns) and sanitized to plain text;
      * the database allows only ONE active request per user (partial unique
        index), so two concurrent broadcasts can't both stay active;
      * the request is COMMITTED first; push and in-app notifications run
        afterwards, so a failed commit can no longer leave irrevocable pushes;
      * only connections with relevance > 0 are notified (the old code padded
        the top 10 with irrelevant people);
      * users/onboarding rows are batch-loaded (CON-15).
    """
    try:
        data = request.get_json(silent=True) or {}
        try:
            subject = clean_user_text(data.get('subject'), HELP_REQUEST_SUBJECT_MAX_LEN, field="Subject")
            message = clean_user_text(data.get('message'), HELP_REQUEST_MESSAGE_MAX_LEN, field="Message")
        except ValueError as exc:
            return error_response(str(exc), 400)

        if not subject:
            return error_response("Subject is required", 400)

        # Expire the previous active request, then insert the new one. The
        # UPDATE is emitted immediately; the partial unique index settles any
        # race between two concurrent broadcasts.
        HelpRequest.query.filter_by(
            requester_id=current_user.id, status='active'
        ).update({"status": "expired"}, synchronize_session=False)

        help_request = HelpRequest(
            requester_id=current_user.id,
            subject=subject,
            message=message,
            status='active',
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=HELP_REQUEST_TTL_HOURS)
        )
        db.session.add(help_request)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return error_response("You already have an active help request, please retry", 409)

        help_request_id = help_request.id
        expires_at = help_request.expires_at

        partner_ids = sorted(get_accepted_partner_ids(current_user.id))
        targets = [u for u, _ in _score_help_targets(partner_ids, subject.lower())[:HELP_REQUEST_MAX_TARGETS]]

        if targets:
            notif_title = f"{current_user.name} needs help!"
            notif_body = f"Help needed with {subject}"
            fcm_tokens = [u.fcm_token for u in targets if u.fcm_token]
            target_ids = [u.id for u in targets]

            def _notify_targets():
                if fcm_tokens:
                    try:
                        from services.push_notifications import PushNotificationService
                        PushNotificationService.send_multicast(
                            fcm_tokens, notif_title, notif_body,
                            {
                                'type': 'help_request',
                                'help_request_id': str(help_request_id),
                                'requester_id': str(current_user.id),
                                'requester_name': current_user.name,
                                'subject': subject,
                            }
                        )
                    except Exception as push_err:
                        current_app.logger.warning(f"Push failed (non-critical): {push_err}")
                for uid in target_ids:
                    notification_service.notify(
                        user_id=uid,
                        title=notif_title,
                        body=notif_body,
                        notification_type='help_request',
                        related_type='help_request',
                        related_id=help_request_id,
                        link=HELP_REQUEST_LINK.format(id=help_request_id),
                    )

            notify_after_commit(_notify_targets)

        # The feed card carries the Volunteer button for every accepted
        # connection (volunteering requires an accepted connection - CON-06).
        record_live_activity(
            current_user.id,
            'help_request_broadcast',
            {
                'help_request_id': help_request_id,
                'subject': subject,
            }
        )

        payload = {
            "help_request_id": help_request_id,
            "notified_count": len(targets),
            "expires_at": expires_at.isoformat(),
        }
        if not partner_ids:
            payload["message"] = "Request created but you have no connections to notify"
        return jsonify({"status": "success", "data": payload})

    except Exception:
        db.session.rollback()
        current_app.logger.error("Broadcast help error", exc_info=True)
        return error_response("Failed to broadcast help request", 500)


def _help_request_visible_to(help_request, user_id):
    """The requester and their ACCEPTED connections may see/volunteer for a request."""
    if help_request.requester_id == user_id:
        return True
    row = _pair_row(user_id, help_request.requester_id)
    return bool(row and row.status == "accepted")


def _volunteer_payload(row, user):
    return {
        "user_id": user.id,
        "name": user.name,
        "username": user.username,
        "avatar": user.avatar,
        "volunteered_at": row.created_at.isoformat() if row.created_at else None,
    }


@connections_crud_bp.route("/connections/help/<int:request_id>", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def get_help_request(current_user, request_id):
    """
    View a help request. This is the target of the help-request
    notifications (they used to link to /help-request/<id>, a path nothing
    served - CON-17). Visible to the requester and their accepted connections.
    """
    try:
        help_request = HelpRequest.query.get(request_id)
        if not help_request or not _help_request_visible_to(help_request, current_user.id):
            return error_response("Help request not found", 404)

        requester = User.query.get(help_request.requester_id)
        effective_status = (
            "expired" if help_request.status == "active" and help_request.is_expired()
            else help_request.status
        )
        return jsonify({
            "status": "success",
            "data": {
                "id": help_request.id,
                "subject": help_request.subject,
                "message": help_request.message,
                "status": effective_status,
                "expires_at": help_request.expires_at.isoformat() if help_request.expires_at else None,
                "is_requester": help_request.requester_id == current_user.id,
                "requester": {
                    "id": requester.id, "name": requester.name,
                    "username": requester.username, "avatar": requester.avatar,
                } if requester else None,
                "total_volunteers": HelpRequestVolunteer.query.filter_by(
                    help_request_id=help_request.id).count(),
                "has_volunteered": HelpRequestVolunteer.query.filter_by(
                    help_request_id=help_request.id, user_id=current_user.id).first() is not None,
            }
        })
    except Exception:
        current_app.logger.error("Get help request error", exc_info=True)
        return error_response("Failed to load help request", 500)


@connections_crud_bp.route("/connections/help/<int:request_id>/volunteer", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def volunteer_for_help(current_user, request_id):
    """
    A connection volunteers to help; the requester is notified (push + in-app
    + WebSocket) AFTER the volunteer row is committed.

    Audit:
      CON-06  only the requester's ACCEPTED connections may volunteer. A
              stranger (or blocked user) gets the same 404 as a missing id, so
              sequential ids can't be used to probe or spam other users.
      CON-16  volunteers live in help_request_volunteers with a UNIQUE
              (help_request_id, user_id): the insert is atomic and idempotent
              across workers (no JSON read-modify-write lost updates).
      CON-17  side effects run after the commit.
    """
    try:
        help_request = HelpRequest.query.get(request_id)

        if not help_request or not _help_request_visible_to(help_request, current_user.id):
            return error_response("Help request not found", 404)

        if help_request.requester_id == current_user.id:
            return error_response("Cannot volunteer for your own request", 400)

        if help_request.status != 'active':
            return error_response("This help request is no longer active", 400)

        if help_request.is_expired():
            HelpRequest.query.filter(
                HelpRequest.id == request_id, HelpRequest.status == 'active'
            ).update({"status": "expired"}, synchronize_session=False)
            db.session.commit()
            return error_response("This help request has expired", 400)

        # Atomic + idempotent: the UNIQUE constraint decides, not a prior read.
        try:
            with db.session.begin_nested():
                db.session.add(HelpRequestVolunteer(
                    help_request_id=request_id, user_id=current_user.id))
                db.session.flush()
        except IntegrityError:
            db.session.rollback()
            return error_response("You have already volunteered", 400)

        db.session.commit()

        total = HelpRequestVolunteer.query.filter_by(help_request_id=request_id).count()
        requester_id = help_request.requester_id
        subject = help_request.subject

        notif_title = f"{current_user.name} can help!"
        notif_body = f"They volunteered to help with {subject}"

        def _after_commit():
            requester = User.query.get(requester_id)
            if requester and requester.fcm_token:
                try:
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
                except Exception as push_err:
                    current_app.logger.warning(f"Push failed (non-critical): {push_err}")

            notification_service.notify(
                user_id=requester_id,
                title=notif_title,
                body=notif_body,
                notification_type='help_volunteer',
                related_type='help_request',
                related_id=request_id,
                link=HELP_REQUEST_LINK.format(id=request_id),
            )

            # Real-time delivery across instances goes through
            # message_ws_manager (Socket.IO message_queue). Best effort.
            try:
                from services.websocket_messages import message_ws_manager
                message_ws_manager.emit_to_user(requester_id, 'help_volunteer_joined', {
                    'help_request_id': request_id,
                    'volunteer': {
                        'user_id': current_user.id,
                        'name': current_user.name,
                        'username': current_user.username,
                        'avatar': current_user.avatar,
                    },
                    'total_volunteers': total
                })
            except Exception as ws_err:
                current_app.logger.warning(f"WebSocket emit failed (non-critical): {ws_err}")

        notify_after_commit(_after_commit)

        return jsonify({
            "status": "success",
            "data": {
                "message": "You have volunteered to help",
                "total_volunteers": total
            }
        })

    except Exception:
        db.session.rollback()
        current_app.logger.error("Volunteer error", exc_info=True)
        return error_response("Failed to volunteer", 500)


@connections_crud_bp.route("/connections/help/<int:request_id>/volunteers", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def get_help_volunteers(current_user, request_id):
    """
    Requester fetches the current volunteer list for their help request.
    `status` reports "expired" for a request past its expiry even if the
    sweep job hasn't flipped the row yet (CON-17).
    """
    try:
        help_request = HelpRequest.query.get(request_id)

        if not help_request:
            return error_response("Help request not found", 404)

        if help_request.requester_id != current_user.id:
            return error_response("Not authorized", 403)

        rows = HelpRequestVolunteer.query.filter_by(
            help_request_id=request_id
        ).order_by(HelpRequestVolunteer.id).all()
        users = {
            u.id: u for u in User.query.filter(User.id.in_([r.user_id for r in rows])).all()
        } if rows else {}
        volunteers = [_volunteer_payload(r, users[r.user_id]) for r in rows if r.user_id in users]

        effective_status = (
            "expired" if help_request.status == "active" and help_request.is_expired()
            else help_request.status
        )

        return jsonify({
            "status": "success",
            "data": {
                "help_request_id": request_id,
                "subject": help_request.subject,
                "status": effective_status,
                "volunteers": volunteers,
                "total_volunteers": len(volunteers),
                "expires_at": help_request.expires_at.isoformat() if help_request.expires_at else None
            }
        })

    except Exception:
        current_app.logger.error("Get volunteers error", exc_info=True)
        return error_response("Failed to get volunteers", 500)


@connections_crud_bp.route("/connections/help/find", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def find_help_with_subject(current_user):
    """Find users who can help with a specific subject"""
    try:
        data = request.get_json(silent=True) or {}
        try:
            subject = clean_user_text(data.get('subject'), HELP_REQUEST_SUBJECT_MAX_LEN, field="Subject")
        except ValueError as exc:
            return error_response(str(exc), 400)

        if not subject:
            return error_response("Please provide a subject", 400)
        
        subject_lower = subject.lower()

        # Never surface users with a block between us (either direction).
        blocked_ids = {
            (c.receiver_id if c.requester_id == current_user.id else c.requester_id)
            for c in Connection.query.filter(
                or_(Connection.requester_id == current_user.id,
                    Connection.receiver_id == current_user.id),
                Connection.status == "blocked",
            ).all()
        }
        # AUDIT ENG-7c FIX: previously no .limit() at all — on a large
        # user base this loaded every approved user into memory and
        # issued 2 queries per user inside the loop below (StudentProfile
        # + OnboardingDetails), with zero ceiling. Capped at 200,
        # matching the same "scan then score" candidate-cap pattern
        # get_recommended_threads (threads/discovery.py) already uses for
        # the analogous case.
        # Audit CON-15: deterministic scan (most reputable first, id tiebreak)
        # instead of an arbitrary first-200 in physical row order.
        candidate_q = User.query.filter(
            User.id != current_user.id,
            User.status == "approved"
        )
        if blocked_ids:
            candidate_q = candidate_q.filter(~User.id.in_(blocked_ids))
        all_users = candidate_q.order_by(User.reputation.desc(), User.id.asc()).limit(200).all()

        # AUDIT ENG-7c FIX: batch-load StudentProfile/OnboardingDetails for
        # the whole capped candidate set — 2 queries total instead of 2
        # queries per user.
        candidate_ids = [u.id for u in all_users]
        profiles_map = {
            p.user_id: p
            for p in StudentProfile.query.filter(StudentProfile.user_id.in_(candidate_ids)).all()
        } if candidate_ids else {}
        onboarding_map = {
            o.user_id: o
            for o in OnboardingDetails.query.filter(OnboardingDetails.user_id.in_(candidate_ids)).all()
        } if candidate_ids else {}

        helpers = []
        
        for user in all_users:
            profile = profiles_map.get(user.id)
            onboarding = onboarding_map.get(user.id)
            
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
                helpers.append((user, profile, expertise_score, reasons))

        # AUDIT ENG-7c FIX: sort/limit BEFORE the online-status batch
        # lookup, so that lookup only ever runs for the up-to-10 helpers
        # actually returned, matching the same sort-then-batch ordering
        # already used elsewhere in this file (e.g. discover_mutual_connections).
        helpers.sort(key=lambda x: x[2], reverse=True)
        top_raw = helpers[:10]

        top_ids = [h[0].id for h in top_raw]
        online_status_map = get_online_status_batch(top_ids) if top_ids else {}

        top_helpers = []
        for user, profile, expertise_score, reasons in top_raw:
            online_status = online_status_map.get(user.id, {"is_online": False, "in_study_session": False, "last_active": "Unknown"})
            expertise_level = 4 if expertise_score >= 80 else 3 if expertise_score >= 60 else 2 if expertise_score >= 40 else 1
            top_helpers.append({
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
                    "is_online": online_status.get("is_online", False)
                },
                "expertise_score": expertise_score,
                "expertise_level": expertise_level,
                "reason": " • ".join(reasons[:3])
            })
        
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

MAX_PAGE_SIZE = 200


def _page_params():
    """
    Audit CON-15: pagination for the list endpoints.
    ?limit (1..200, default 200 = the old fixed cap, so existing clients keep
    working) and ?cursor (opaque offset returned as next_cursor).
    """
    try:
        limit = int(request.args.get("limit", MAX_PAGE_SIZE))
    except (TypeError, ValueError):
        limit = MAX_PAGE_SIZE
    try:
        offset = int(request.args.get("cursor", 0))
    except (TypeError, ValueError):
        offset = 0
    return max(1, min(limit, MAX_PAGE_SIZE)), max(0, offset)


def _page_meta(total, offset, returned):
    has_more = offset + returned < total
    return {
        "total": total,
        "has_more": has_more,
        "next_cursor": str(offset + returned) if has_more else None,
    }


@connections_crud_bp.route("/connections/requests/received", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def received_connection_requests(current_user):
    """Get ALL pending connection requests sent TO you (no pagination)"""
    try:
        # Get all received requests
        limit, offset = _page_params()
        base = Connection.query.filter(
            Connection.receiver_id == current_user.id,
            Connection.status == "pending"
        )
        total = base.count()
        requests = base.order_by(
            Connection.requested_at.desc(), Connection.id.desc()
        ).offset(offset).limit(limit).all()

        if not requests:
            return jsonify({"status": "success", "data": [], "total": total,
                            "has_more": False, "next_cursor": None})

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
                "message":            req.request_message,
                "requested_at":       req.requested_at.isoformat(),
                "time_ago":           time_ago_text,
                "mutuals_count":      mutual_count,
            })

        return jsonify({
            "status": "success",
            "data": requests_data,
            **_page_meta(total, offset, len(requests)),
        })

    except Exception as e:
        current_app.logger.error(f"Received requests error: ", exc_info=True)
        return error_response("Failed to load received requests")


# ============================================================================
# 2. SENT CONNECTION REQUESTS - NO PAGINATION
# ============================================================================

@connections_crud_bp.route("/connections/requests/sent", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def sent_connection_requests(current_user):
    """Get ALL pending connection requests YOU sent (no pagination)"""
    try:
        # Get all sent requests
        limit, offset = _page_params()
        base = Connection.query.filter(
            Connection.requester_id == current_user.id,
            Connection.status == "pending"
        )
        total = base.count()
        requests = base.order_by(
            Connection.requested_at.desc(), Connection.id.desc()
        ).offset(offset).limit(limit).all()

        if not requests:
            return jsonify({"status": "success", "data": [], "total": total,
                            "has_more": False, "next_cursor": None})

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
                "your_message":  req.request_message,
                "requested_at":  req.requested_at.isoformat(),
                "time_ago":      time_ago_text,
                "mutuals_count": mutual_count,
            })

        return jsonify({
            "status": "success",
            "data": requests_data,
            **_page_meta(total, offset, len(requests)),
        })

    except Exception as e:
        current_app.logger.error(f"Sent requests error: ", exc_info=True)
        return error_response("Failed to load sent requests")


# ============================================================================
# 3. CONNECTED USERS LIST - NO PAGINATION
# ============================================================================

@connections_crud_bp.route("/connections/list", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def list_connections(current_user):
    """
    List accepted connections.

    Audit CON-15: deterministic order (most recently connected first), real
    ?limit/?cursor pagination and a true `total`. It used to LIMIT 200 with no
    ORDER BY, so users with more connections silently got an arbitrary subset
    and a `total` that equalled the truncated count.
    """
    try:
        limit, offset = _page_params()
        base = Connection.query.filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id
            ),
            Connection.status == "accepted"
        )
        total = base.count()
        all_connections = base.order_by(
            Connection.responded_at.desc(), Connection.id.desc()
        ).offset(offset).limit(limit).all()

        if not all_connections:
            return jsonify({"status": "success", "data": [], "total": total,
                            "has_more": False, "next_cursor": None})

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

        # Audit CON-23: one shared implementation (service layer) instead of
        # an inline copy of the health formula that could drift.
        health_map = get_connection_health_batch(current_user.id, connected_ids)

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

            health = health_map.get(other_id) or {}
            health_score   = health.get("health_score", 0)
            suggestion     = health.get("suggestion", "")
            shared_threads = health.get("shared_threads", 0)

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
            **_page_meta(total, offset, len(all_connections)),
        })

    except Exception:
        current_app.logger.error("List connections error", exc_info=True)
        return error_response("Failed to load connections", 500)


# ============================================================================
# 4. MUTUAL CONNECTIONS - NO PAGINATION
# ============================================================================

# Audit CON-07: each party has its OWN seen flag.
#   receiver_seen  - the receiver has seen the incoming (pending) request
#   requester_seen - the requester has seen the response (accepted/rejected)
# They used to share one `is_seen` boolean, so a receiver opening the requests
# page before accepting silently suppressed the requester's "response" badge.

def _unseen_received_query(user_id):
    return Connection.query.filter(
        Connection.receiver_id == user_id,
        Connection.status == "pending",
        Connection.receiver_seen.is_(False),
    )


def _unseen_sent_query(user_id):
    return Connection.query.filter(
        Connection.requester_id == user_id,
        Connection.status.in_(["accepted", "rejected"]),
        Connection.requester_seen.is_(False),
    )


@connections_crud_bp.route("/connections/unseen/received", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def unseen_received_count(current_user):
    """
    Count of pending requests sent TO you that you haven't seen yet.
    """
    try:
        return jsonify({
            "status": "success",
            "data": {"count": _unseen_received_query(current_user.id).count()}
        })

    except Exception:
        current_app.logger.error("Unseen received connections error", exc_info=True)
        return error_response("Failed to get unseen received connections count", 500)


@connections_crud_bp.route("/connections/unseen/sent", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def unseen_sent_count(current_user):
    """
    Count of requests YOU sent that were accepted/rejected but whose response
    you haven't seen yet.
    """
    try:
        return jsonify({
            "status": "success",
            "data": {"count": _unseen_sent_query(current_user.id).count()}
        })

    except Exception:
        current_app.logger.error("Unseen sent connections error", exc_info=True)
        return error_response("Failed to get unseen sent connections count", 500)


@connections_crud_bp.route("/connections/mark-received-seen", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def mark_received_connections_seen(current_user):
    """
    Mark all received connection requests as seen.
    Call this when the user opens the received requests page. Only touches the
    receiver's own flag, so it can never hide the requester's badge.
    """
    try:
        updated = _unseen_received_query(current_user.id).update(
            {"receiver_seen": True}, synchronize_session=False
        )
        db.session.commit()

        return success_response(
            f"Marked {updated} received connections as seen",
            data={"updated_count": updated}
        )

    except Exception:
        db.session.rollback()
        current_app.logger.error("Mark received connections seen error", exc_info=True)
        return error_response("Failed to mark received connections as seen", 500)


@connections_crud_bp.route("/connections/mark-sent-seen", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def mark_sent_connections_seen(current_user):
    """
    Mark all sent-request responses as seen.
    Call this when the user opens the sent requests page. Only touches the
    requester's own flag.
    """
    try:
        updated = _unseen_sent_query(current_user.id).update(
            {"requester_seen": True}, synchronize_session=False
        )
        db.session.commit()

        return success_response(
            f"Marked {updated} sent connection responses as seen",
            data={"updated_count": updated}
        )

    except Exception:
        db.session.rollback()
        current_app.logger.error("Mark sent connections seen error", exc_info=True)
        return error_response("Failed to mark sent connections as seen", 500)


@connections_crud_bp.route("/connections/request/<int:user_id>", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def send_connection_request(current_user, user_id):
    """
    Smart connection request with auto-accept for high compatibility

    Body (optional): {
        "message": "Hi! Let's connect and study together"   (<= 300 chars, plain text)
    }

    Response includes:
    - is_instant: true if auto-connected (compatibility >= 70%)
    - status: "accepted" | "pending" | "already_connected"
    - compatibility_score: 0-100 match score

    Audit notes:
    - CON-04: message is validated/sanitized (plain text, <= 300 chars).
    - CON-03: re-request after a rejection works (and swaps direction when the
      original receiver is the one asking again).
    - CON-08: "they asked you first" acceptance is a compare-and-set.
    - CON-09: notifications are sent only AFTER the commit succeeds.
    - CON-19: the message lives in request_message, never in private notes.
    """
    try:
        # Validation
        if user_id == current_user.id:
            return error_response("Cannot connect with yourself")

        try:
            message = clean_user_text(
                (request.get_json(silent=True) or {}).get("message"),
                CONNECTION_MESSAGE_MAX_LEN,
                field="Message",
            )
        except ValueError as exc:
            return error_response(str(exc), 400)

        target_user = User.query.get(user_id)
        if not target_user:
            return error_response("User not found", 404)

        receiver_brief = _receiver_payload(target_user)

        # Check if connection already exists (either direction)
        existing = _pair_row(current_user.id, user_id)

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
                        "receiver": receiver_brief,
                    }
                }), 200

            elif existing.status == "pending":
                if existing.requester_id == current_user.id:
                    return jsonify({
                        "status": "success",
                        "message": "Connection request already pending",
                        "data": {
                            "connection_id": existing.id,
                            "is_instant": False,
                            "connection_status": "pending_sent",
                            "requested_at": existing.requested_at.isoformat(),
                            "receiver": receiver_brief,
                        }
                    }), 200

                # They sent you a request first - accept it instantly (atomic).
                connection_id = existing.id
                requester_id = existing.requester_id
                if not accept_pending_connection(connection_id, current_user.id):
                    db.session.rollback()
                    return error_response("This request is no longer pending", 409)
                db.session.commit()
                invalidate_pair_caches(current_user.id, user_id)
                notify_after_commit(
                    notification_service.notify_connection_accepted,
                    requester_id, current_user.name, current_user.id,
                )
                accepted_row = Connection.query.get(connection_id)

                return jsonify({
                    "status": "success",
                    "message": "Connection accepted! (They requested you first)",
                    "data": {
                        "connection_id": connection_id,
                        "is_instant": True,
                        "connection_status": "accepted",
                        "connected_at": accepted_row.responded_at.isoformat(),
                        "receiver": receiver_brief,
                    }
                }), 201

            elif existing.status == "blocked":
                return error_response("Cannot connect with this user", 403)

            elif existing.status == "rejected":
                # Cooldown period (24 hours)
                cooldown_hours = 24
                if existing.responded_at:
                    hours_since_rejection = (
                        datetime.datetime.utcnow() - existing.responded_at
                    ).total_seconds() / 3600
                    if hours_since_rejection < cooldown_hours:
                        remaining = max(1, math.ceil(cooldown_hours - hours_since_rejection))
                        return error_response(
                            f"Please wait {remaining} hours before requesting again",
                            429
                        )

                # Allow re-request after cooldown. Direction follows the NEW
                # requester so the receiver can actually accept (CON-03).
                connection_id = existing.id
                if not reopen_rejected_connection(connection_id, current_user.id, user_id, message):
                    db.session.rollback()
                    return error_response("Connection state changed, please retry", 409)
                db.session.commit()
                notify_after_commit(
                    notification_service.notify,
                    user_id=user_id,
                    title="New Connection Request",
                    body=f"{current_user.name} sent you a connection request again",
                    notification_type="connection_request",
                    related_type="user",
                    related_id=current_user.id,
                )
                reopened = Connection.query.get(connection_id)

                return jsonify({
                    "status": "success",
                    "message": "Connection request re-sent",
                    "data": {
                        "connection_id": connection_id,
                        "is_instant": False,
                        "connection_status": "pending",
                        "requested_at": reopened.requested_at.isoformat(),
                        "receiver": receiver_brief,
                    }
                }), 201

        # ============================================================================
        # Calculate compatibility for instant connect
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
        is_instant = compatibility_score >= 70
        now = datetime.datetime.utcnow()

        connection = Connection(
            requester_id=current_user.id,
            receiver_id=user_id,
            status="accepted" if is_instant else "pending",  # auto-accept at >= 70%
            requested_at=now,
            responded_at=now,
            request_message=message,
            requester_seen=True,   # the requester just saw the outcome
            receiver_seen=False,
        )
        db.session.add(connection)

        # uq_connections_pair can reject this INSERT if the other user's
        # request landed first. Report whatever state actually won the race,
        # and (CON-09) send nothing, because nothing was committed.
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            current_app.logger.info(
                f"Connection request race detected (uq_connections_pair): "
                f"{current_user.id} <-> {user_id}"
            )
            existing = _pair_row(current_user.id, user_id)
            state = existing.status if existing else "pending"
            return jsonify({
                "status": "success",
                "message": ("Connection request already pending" if state == "pending"
                            else "Already connected"),
                "data": {
                    "connection_id": existing.id if existing else None,
                    "is_instant": False,
                    "connection_status": "already_connected" if state == "accepted" else state,
                    "receiver": receiver_brief,
                }
            }), 200

        connection_id = connection.id
        requested_at = connection.requested_at
        invalidate_pair_caches(current_user.id, user_id)

        if is_instant:
            def _notify_instant():
                notification_service.notify_instant_connection(
                    user_id, current_user.name, current_user.id, compatibility_score,
                    is_receiver=True,
                )
                notification_service.notify_instant_connection(
                    current_user.id, target_user.name, user_id, compatibility_score,
                    is_receiver=False,
                )
            notify_after_commit(_notify_instant)

            return jsonify({
                "status": "success",
                "message": f"Instantly connected! ({compatibility_score}% compatibility)",
                "data": {
                    "connection_id": connection_id,
                    "is_instant": True,  # Frontend uses this
                    "connection_status": "accepted",
                    "connected_at": requested_at.isoformat(),
                    "compatibility": {
                        "score": compatibility_score,
                        "shared_subjects": compatibility_data['shared_subjects'],
                        "mutual_help": compatibility_data['complementary_skills'],
                        "schedule_overlap": compatibility_data.get('schedule_overlap', 0)
                    },
                    "receiver": _receiver_payload(target_user, with_reputation=True),
                }
            }), 201

        notify_after_commit(
            notification_service.notify_connection_request,
            user_id, current_user.name, current_user.id,
        )

        return jsonify({
            "status": "success",
            "message": "Connection request sent (awaiting approval)",
            "data": {
                "connection_id": connection_id,
                "is_instant": False,  # Frontend uses this
                "connection_status": "pending",
                "requested_at": requested_at.isoformat(),
                "compatibility": {
                    "score": compatibility_score,
                    "note": "Compatibility below 70% - requires approval"
                },
                "receiver": _receiver_payload(target_user, with_reputation=True),
            }
        }), 201

    except Exception:
        db.session.rollback()
        current_app.logger.error("Send connection request error", exc_info=True)
        return error_response("Failed to send connection request", 500)


@connections_crud_bp.route("/connections/accept/<int:request_id>", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def accept_connection(current_user, request_id):
    """
    Accept a connection request.

    Audit CON-08: the pending -> accepted transition is a single conditional
    UPDATE, so it can never resurrect a connection that was blocked/cancelled
    between the read and the write. CON-09: the notification is sent after
    the commit. CON-22: 404/403 like reject/cancel.
    """
    try:
        connection = Connection.query.get(request_id)

        if not connection:
            return error_response("Connection request not found", 404)

        # Verify user is the receiver
        if connection.receiver_id != current_user.id:
            return error_response("Not authorized to accept this request", 403)

        if connection.status != "pending":
            return error_response("Request is not pending", 400)

        requester_id = connection.requester_id
        connection_id = connection.id

        if not accept_pending_connection(connection_id, current_user.id):
            # Lost a race with cancel / block / another accept.
            db.session.rollback()
            return error_response("Request is no longer pending", 409)

        db.session.commit()
        invalidate_pair_caches(current_user.id, requester_id)

        notify_after_commit(
            notification_service.notify_connection_accepted,
            requester_id, current_user.name, current_user.id,
        )

        requester = User.query.get(requester_id)

        # Activity feed: single row from the acceptor's side (fans out to the
        # acceptor's own connections). record_live_activity swallows errors.
        record_live_activity(
            current_user.id,
            'connection_accepted',
            {
                'other_user_id': requester.id,
                'other_user_name': requester.name,
            }
        )

        return success_response(
            "Connection accepted",
            data={
                "connection_id": connection_id,
                "connected_user": {
                    "id": requester.id,
                    "name": requester.name,
                    "username": requester.username,
                    "avatar": requester.avatar
                }
            }
        )

    except Exception:
        db.session.rollback()
        current_app.logger.error("Accept connection error", exc_info=True)
        return error_response("Failed to accept connection", 500)


@connections_crud_bp.route("/connections/reject/<int:request_id>", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def reject_connection(current_user, request_id):
    """
    Reject a connection request (atomic pending -> rejected, audit CON-08).
    """
    try:
        connection = Connection.query.get(request_id)

        if not connection:
            return error_response("Connection request not found", 404)

        # Verify user is the receiver
        if connection.receiver_id != current_user.id:
            return error_response("Not authorized to reject this request", 403)

        if connection.status != "pending":
            return error_response("Request is not pending", 400)

        if not reject_pending_connection(connection.id, current_user.id):
            db.session.rollback()
            return error_response("Request is no longer pending", 409)

        db.session.commit()

        return success_response("Connection request rejected")

    except Exception:
        db.session.rollback()
        current_app.logger.error("Reject connection error", exc_info=True)
        return error_response("Failed to reject connection", 500)


@connections_crud_bp.route("/connections/cancel/<int:request_id>", methods=["DELETE"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def cancel_connection_request(current_user, request_id):
    """
    Cancel a pending connection request you sent.

    Audit CON-08: a conditional DELETE (status='pending'), so a cancel that
    races with the receiver's accept can no longer delete the now-accepted
    connection.
    """
    try:
        connection = Connection.query.get(request_id)

        if not connection:
            return error_response("Connection request not found", 404)

        # Verify user is the requester
        if connection.requester_id != current_user.id:
            return error_response("Not authorized to cancel this request", 403)

        if connection.status != "pending":
            return error_response("Request is not pending", 400)

        if not cancel_pending_connection(connection.id, current_user.id):
            db.session.rollback()
            return error_response("Request is no longer pending", 409)

        db.session.commit()

        return success_response("Connection request cancelled")

    except Exception:
        db.session.rollback()
        current_app.logger.error("Cancel connection error", exc_info=True)
        return error_response("Failed to cancel connection", 500)


@connections_crud_bp.route("/connections/remove/<int:user_id>", methods=["DELETE"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def remove_connection(current_user, user_id):
    """
    Remove/unfriend a connection (conditional DELETE of an ACCEPTED row).
    """
    try:
        if not remove_accepted_connection(current_user.id, user_id):
            db.session.rollback()
            return error_response("Connection not found", 404)

        db.session.commit()
        invalidate_pair_caches(current_user.id, user_id)

        return success_response("Connection removed")

    except Exception:
        db.session.rollback()
        current_app.logger.error("Remove connection error", exc_info=True)
        return error_response("Failed to remove connection", 500)

# PHASE-1 WIRING FIX: get_mutual_connection_count and get_connection_health
# used to be defined here. Both now live in services/connection_service.py
# (Document 2 §3.4) — get_connection_health is kept as a single-pair
# wrapper there; get_connection_health_batch is the new, N+1-safe batch
# form (Document 1 §2.1.1) available for any call site that loops over
# multiple pairs. Imported at the top of this file under the same names.

@connections_crud_bp.route("/connections/status/<int:user_id>", methods=["GET"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
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

@connections_crud_bp.route("/connections/settings", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
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
        

        

    

        

