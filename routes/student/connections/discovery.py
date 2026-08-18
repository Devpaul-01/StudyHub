"""
StudyHub - Connections: Discovery: mutual connections, suggestions, search, availability

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
from services import search_service
# Phase 5b (Document 4 §1): PUBLIC_READ across the board — this file is
# entirely discovery/suggestion/search reads.
from services.rate_limit_service import limiter, RateLimitTier, ip_key

logger = logging.getLogger(__name__)

connections_discovery_bp = Blueprint("connections_discovery", __name__)
@connections_discovery_bp.route("/connections/mutual/<int:user_id>", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
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



@connections_discovery_bp.route("/connections/suggestions", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
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

@connections_discovery_bp.route("/connections/search", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def search_users(current_user):
    """
    Search for users, scoped to connections (excludes users you've
    blocked or who've blocked you).

    This is a thin wrapper around services/search_service.py::search_users
    (Document 1 section 6.1 / section 2.1) — the one implementation of user
    search. Block-exclusion is the only genuinely connections-specific
    behavior layered on top; everything else (text match, connection-status
    annotation, pagination) is the shared service logic.
    """
    try:
        search_term = request.args.get("search", "").strip()

        if not search_term or len(search_term) < 2:
            return error_response("Please provide at least 2 characters to search", 400)

        # Connections-specific: exclude users involved in a blocked
        # relationship with the viewer (in either direction).
        blocked_connections = Connection.query.filter(
            or_(
                and_(Connection.receiver_id == current_user.id, Connection.status == "blocked"),
                and_(Connection.requester_id == current_user.id, Connection.status == "blocked")
            )
        ).all()

        excluded_ids = [
            conn.requester_id if conn.receiver_id == current_user.id else conn.receiver_id
            for conn in blocked_connections
        ]

        data = search_service.search_users(
            search_term,
            exclude_ids=excluded_ids,
            per_page=50,
            viewer_id=current_user.id,
        )

        return jsonify({
            "status": "success",
            "data": {
                "users": data["users"],
                "total": data["pagination"]["total"],
                "search_term": search_term,
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

@connections_discovery_bp.route("/connections/mutuals/discover", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
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
        # AUDIT ENG-7a FIX: batch-load StudentProfile and every friend
        # AUDIT ENG-7a FIX (completed): get_user_onboarding_preview is now
        # also batched — services/connection_service.py has been supplied
        # and confirmed to be a thin, single-row OnboardingDetails lookup
        # with no cross-model joins or side effects, so its exact dict
        # shape is reproduced inline below from a batch-loaded
        # OnboardingDetails map, matching the profiles_map/onboarding_map
        # pattern already established elsewhere in this file (e.g. the
        # connection_suggestions_flat block). Output is byte-identical to
        # calling get_user_onboarding_preview(user_id) per user — same
        # 7-field shape, same [:3] truncation, same has_schedule bool
        # cast — just without the N+1.
        # ========================================
        profiles_map = {
            p.user_id: p
            for p in StudentProfile.query.filter(StudentProfile.user_id.in_(limited_ids)).all()
        } if limited_ids else {}

        onboarding_raw_map = {
            o.user_id: o
            for o in OnboardingDetails.query.filter(OnboardingDetails.user_id.in_(limited_ids)).all()
        } if limited_ids else {}

        all_friend_ids = list({
            fid for user_id in limited_ids for fid in mutual_friends.get(user_id, [])[:3]
        })
        friends_map = {
            u.id: u
            for u in User.query.filter(User.id.in_(all_friend_ids)).all()
        } if all_friend_ids else {}

        # ========================================
        # STEP 4: Build response with sample mutual friends
        # ========================================
        discoveries = []
        
        for user_id in limited_ids:
            potential_user = users_map.get(user_id)
            if not potential_user:
                continue
            
            profile = profiles_map.get(user_id)
            ob = onboarding_raw_map.get(user_id)
            onboarding = {
                "subjects": ob.subjects[:3] if ob.subjects else [],
                "strong_subjects": ob.strong_subjects[:3] if ob.strong_subjects else [],
                "help_subjects": ob.help_subjects[:3] if ob.help_subjects else [],
                "learning_style": ob.learning_style,
                "study_preferences": ob.study_preferences[:3] if ob.study_preferences else [],
                "session_length": ob.session_length,
                "has_schedule": bool(ob.study_schedule),
            } if ob else None
            online_status = get_user_online_status(user_id)
            
            # Get sample of mutual friends (up to 3)
            friend_ids = mutual_friends.get(user_id, [])[:3]
            sample_mutuals = []
            
            for friend_id in friend_ids:
                mutual_user = friends_map.get(friend_id)
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


@connections_discovery_bp.route("/connections/suggestions/flat", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
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

# PHASE-1 WIRING FIX: calculate_schedule_overlap, get_user_top_topics,
# calculate_compatibility_score, gather_user_data, calculate_compatibility,
# and get_recent_activity used to be defined here. They now live in
# services/connection_service.py (Document 2 §3.4) and are imported at the
# top of this file under the same names, so every call site below is
# unchanged.

# ============================================================================
# REPLACE THE ENTIRE /connections/overview/<int:user_id> ENDPOINT
# Starting around line 1117 in your connections.py
# ============================================================================

@connections_discovery_bp.route("/connections/available-now", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
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

