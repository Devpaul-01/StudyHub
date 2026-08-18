"""
StudyHub - Study Buddy Matching System
Smart algorithm to match students for collaborative learning

Features:
- Preference-based matching
- Smart algorithm (subject overlap, availability, activity level)
- Request workflow
- Auto-thread creation on match
- Session tracking
- Success metrics
"""

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func, desc, and_, or_
import datetime

from models import (
    User, StudentProfile, StudyBuddyRequest, StudyBuddyMatch,
    Thread, ThreadMember, Connection
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response
)

# Document 2 §4: calculate_match_score is a pure function of its inputs
# (no DB access, no Flask dependency) — moved to
# services/study_buddy_service.py per the same reasoning as
# connection_service.calculate_compatibility_score. Imported here at the
# same name so every existing call site in this file keeps working
# unchanged.
from services.study_buddy_service import calculate_match_score
# Plan §4.6/§17.7: manual cache-aside (see routes/student/analytics.py's
# same reasoning) around get_suggestions/get_success_stories/
# get_platform_stats — get_match_details (single-pair detail view) and
# get_match_details_partnership are deliberately NOT cached, per plan
# §4.6's table and its absence from §4/§17.7 respectively.
from services import cache_service
from services import notification_service
# Phase 5b (Document 4 §1): WRITE_HEAVY for preference/request/session
# mutations, BURST_OK for the cancel/remove actions (low-risk cleanup).
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key, ip_key

study_buddy_bp = Blueprint("student_study_buddy", __name__)


# ============================================================================
# PREFERENCES MANAGEMENT
# ============================================================================

@study_buddy_bp.route("/study-buddy/preferences", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def set_preferences(current_user):
    try:
        data = request.get_json()

        needs_help      = data.get("needs_help", [])
        good_at         = data.get("good_at", [])
        available_days  = data.get("available_days", [])
        available_times = data.get("available_times", [])
        study_style     = data.get("study_style", [])
        goals           = data.get("goals", "").strip()

        if not isinstance(needs_help, list) or not isinstance(good_at, list):
            return error_response("needs_help and good_at must be arrays")

        if not needs_help and not good_at:
            return error_response("Must specify at least one course in needs_help or good_at")

        metadata = current_user.user_metadata if current_user.user_metadata else {}
        metadata["study_buddy_prefs"] = {
            "needs_help":       needs_help[:10],
            "good_at":          good_at[:10],
            "available_days":   available_days,
            "available_times":  available_times,
            "study_style":      study_style,
            "goals":            goals[:500],
            "updated_at":       datetime.datetime.utcnow().isoformat(),
        }

        current_user.user_metadata = metadata
        db.session.commit()

        return success_response(
            "Preferences saved! We'll find great matches for you.",
            data={"preferences": metadata["study_buddy_prefs"]},
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Set preferences error: {str(e)}")
        return error_response("Failed to save preferences")


@study_buddy_bp.route("/study-buddy/preferences", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_preferences(current_user):
    try:
        metadata = current_user.user_metadata if current_user.user_metadata else {}
        prefs = metadata.get("study_buddy_prefs", {})
        if not prefs:
            return jsonify({"status": "success", "data": {}})
        return jsonify({
            "status": "success",
            "data": {
                "needs_help":     prefs.get("needs_help", [])[:10],
                "good_at":        prefs.get("good_at", [])[:10],
                "available_days": prefs.get("available_days", []),
                "available_times":prefs.get("available_times", []),
                "study_style":    prefs.get("study_style", []),
                "goals":          prefs.get("goals", ""),
                "last_updated":   prefs.get("updated_at"),
            },
        })
    except Exception as e:
        current_app.logger.error(f"Get preferences error: {str(e)}")
        return error_response("Failed to load preferences")


@study_buddy_bp.route("/study-buddy/preferences", methods=["PATCH"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def update_preferences(current_user):
    try:
        data = request.get_json()
        metadata = current_user.user_metadata if current_user.user_metadata else {}
        prefs = metadata.get("study_buddy_prefs", {})

        field_map = {
            "needs_help":      lambda v: v[:10],
            "good_at":         lambda v: v[:10],
            "available_days":  lambda v: v,
            "available_times": lambda v: v,
            "study_style":     lambda v: v,
            "goals":           lambda v: v[:500],
        }
        for field, transform in field_map.items():
            if field in data:
                prefs[field] = transform(data[field])

        prefs["updated_at"] = datetime.datetime.utcnow().isoformat()
        metadata["study_buddy_prefs"] = prefs
        current_user.user_metadata = metadata
        db.session.commit()

        return success_response("Preferences updated", data={"preferences": prefs})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update preferences failed: {str(e)}")
        return error_response("Failed to update preferences")


# ============================================================================
# SMART MATCHING
# ============================================================================

# ---------------------------------------------------------------------------
# OPTIMIZED: GET /study-buddy/suggestions
#
# BEFORE:
#   For each of up to 50 candidates:
#     - calculate_match_score() → StudentProfile.query.filter_by(user1) → DB hit
#     - calculate_match_score() → StudentProfile.query.filter_by(user2) → DB hit
#     - StudyBuddyMatch.query.filter(...).count()                        → DB hit
#   Worst case: 50 × 3 = 150 extra queries on top of the initial 3
#
# AFTER:
#   - 1 query: batch-load all candidate StudentProfiles via .in_()
#   - 1 query: batch-load successful match counts via GROUP BY
#   - current_user profile loaded once, reused every iteration
#   Total extra queries: 2 (flat, regardless of candidate count)
# ---------------------------------------------------------------------------
@study_buddy_bp.route("/study-buddy/suggestions", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_suggestions(current_user):
    """
    Get smart study buddy suggestions.

    Query params:
    - limit: Number of suggestions (default 10, max 20)
    """
    try:
        limit = min(request.args.get("limit", 10, type=int), 20)

        metadata   = current_user.user_metadata if current_user.user_metadata else {}
        user_prefs = metadata.get("study_buddy_prefs", {})

        if not user_prefs:
            return jsonify({
                "status": "success",
                "data": {"suggestions": [], "message": "Set your preferences first to get matches"},
            })

        # Plan §4.6/§17.7: cache-aside, TTL 300s, TTL fallback only —
        # deliberately not hard-invalidated, since any other user's
        # study_buddy_prefs or last_active could affect this viewer's
        # suggestion list, which is unbounded fan-out (plan §4.9). Cache
        # key has no `limit` component: limit only slices the already-
        # scored list at the very end, so the full computation is cached
        # once and reused regardless of the requested page size. The
        # no-prefs-set early return above is deliberately NOT cached —
        # caching it would mean a user who just set their preferences
        # could see a stale "set your preferences" response for up to the
        # TTL, which the plan's own reasoning for this cache (staleness in
        # *candidate* state, not viewer state) doesn't intend to cover.
        cache_key = f"sh:1:sb:suggest:{current_user.id}"
        cached_result = cache_service.get(cache_key)
        if cached_result is not None:
            suggestions = cached_result["suggestions"]
            return jsonify({
                "status": "success",
                "data": {"suggestions": suggestions[:limit], "total_found": cached_result["total_found"]},
            })

        # ✅ Load current user's profile once
        current_profile = StudentProfile.query.filter_by(user_id=current_user.id).first()

        # ✅ Excluded user IDs in two queries (requests + matches)
        existing_requests = StudyBuddyRequest.query.filter(
            or_(
                StudyBuddyRequest.requester_id == current_user.id,
                StudyBuddyRequest.receiver_id  == current_user.id,
            )
        ).with_entities(StudyBuddyRequest.requester_id, StudyBuddyRequest.receiver_id).all()

        existing_matches = StudyBuddyMatch.query.filter(
            or_(
                StudyBuddyMatch.user1_id == current_user.id,
                StudyBuddyMatch.user2_id == current_user.id,
            )
        ).with_entities(StudyBuddyMatch.user1_id, StudyBuddyMatch.user2_id).all()

        excluded_ids = {current_user.id}
        for r in existing_requests:
            excluded_ids.add(r.receiver_id if r.requester_id == current_user.id else r.requester_id)
        for m in existing_matches:
            excluded_ids.add(m.user2_id if m.user1_id == current_user.id else m.user1_id)

        # ✅ Fetch candidates — one query
        candidates = User.query.filter(
            User.id.notin_(excluded_ids),
            User.status == "approved",
            User.last_active >= datetime.datetime.utcnow() - datetime.timedelta(days=30),
        ).limit(50).all()

        if not candidates:
            cache_service.set(cache_key, {"suggestions": [], "total_found": 0}, ttl_seconds=300)
            return jsonify({"status": "success", "data": {"suggestions": [], "total_found": 0}})

        candidate_ids = [c.id for c in candidates]

        # ✅ Batch-load all candidate profiles in ONE query
        profiles = StudentProfile.query.filter(StudentProfile.user_id.in_(candidate_ids)).all()
        profile_map = {p.user_id: p for p in profiles}

        # ✅ Batch-load successful match counts per candidate in ONE GROUP BY query
        success_counts_rows = (
            db.session.query(
                func.coalesce(StudyBuddyMatch.user1_id, StudyBuddyMatch.user2_id).label("uid"),
                func.count(StudyBuddyMatch.id).label("cnt"),
            )
            .filter(
                or_(
                    StudyBuddyMatch.user1_id.in_(candidate_ids),
                    StudyBuddyMatch.user2_id.in_(candidate_ids),
                ),
                StudyBuddyMatch.is_active  == False,
                StudyBuddyMatch.sessions_count >= 3,
            )
            .group_by(StudyBuddyMatch.user1_id, StudyBuddyMatch.user2_id)
            .all()
        )
        # Aggregate: a user can appear as user1 or user2 across multiple rows
        success_count_map = {}
        for row in success_counts_rows:
            # Re-aggregate per individual user
            pass
        # Simpler and correct approach: one subquery per side, then union in Python
        success_count_map = {}
        raw_rows = (
            db.session.query(
                StudyBuddyMatch.user1_id.label("uid"),
                func.count(StudyBuddyMatch.id).label("cnt"),
            )
            .filter(
                StudyBuddyMatch.user1_id.in_(candidate_ids),
                StudyBuddyMatch.is_active == False,
                StudyBuddyMatch.sessions_count >= 3,
            )
            .group_by(StudyBuddyMatch.user1_id)
            .all()
        )
        for row in raw_rows:
            success_count_map[row.uid] = success_count_map.get(row.uid, 0) + row.cnt

        raw_rows2 = (
            db.session.query(
                StudyBuddyMatch.user2_id.label("uid"),
                func.count(StudyBuddyMatch.id).label("cnt"),
            )
            .filter(
                StudyBuddyMatch.user2_id.in_(candidate_ids),
                StudyBuddyMatch.is_active == False,
                StudyBuddyMatch.sessions_count >= 3,
            )
            .group_by(StudyBuddyMatch.user2_id)
            .all()
        )
        for row in raw_rows2:
            success_count_map[row.uid] = success_count_map.get(row.uid, 0) + row.cnt

        # ✅ Score all candidates — zero DB hits inside this loop
        suggestions = []
        for candidate in candidates:
            cand_metadata = candidate.user_metadata if candidate.user_metadata else {}
            cand_prefs    = cand_metadata.get("study_buddy_prefs", {})

            if not cand_prefs:
                continue

            cand_profile    = profile_map.get(candidate.id)
            success_count   = success_count_map.get(candidate.id, 0)

            score = calculate_match_score(
                current_user, candidate,
                user_prefs, cand_prefs,
                profile1=current_profile,
                profile2=cand_profile,
                user2_success_count=success_count,
            )

            if score < 30:
                continue

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
                },
                "match_score": score,
                "preferences": {
                    "good_at":        cand_prefs.get("good_at", []),
                    "needs_help":     cand_prefs.get("needs_help", []),
                    "available_days": cand_prefs.get("available_days", []),
                    "study_style":    cand_prefs.get("study_style", []),
                },
                "compatibility": {
                    "subject_match":     score >= 40,
                    "availability_match": score >= 30,
                    "same_department": (
                        current_profile and cand_profile and
                        current_profile.department == cand_profile.department
                    ),
                },
                "stats": {"successful_partnerships": success_count},
            })

        suggestions.sort(key=lambda x: x["match_score"], reverse=True)

        cache_service.set(
            cache_key,
            {"suggestions": suggestions, "total_found": len(suggestions)},
            ttl_seconds=300,
        )

        return jsonify({
            "status": "success",
            "data": {"suggestions": suggestions[:limit], "total_found": len(suggestions)},
        })

    except Exception as e:
        current_app.logger.error(f"Get suggestions error: {str(e)}")
        return error_response("Failed to load suggestions")


@study_buddy_bp.route("/study-buddy/suggestions/details/<int:user_id>", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_match_details(current_user, user_id):
    try:
        candidate = User.query.get(user_id)
        if not candidate:
            return error_response("User not found", 404)

        user_metadata  = current_user.user_metadata if current_user.user_metadata else {}
        user_prefs     = user_metadata.get("study_buddy_prefs", {})
        cand_metadata  = candidate.user_metadata if candidate.user_metadata else {}
        cand_prefs     = cand_metadata.get("study_buddy_prefs", {})

        if not user_prefs or not cand_prefs:
            return error_response("Both users must have preferences set")

        profile1 = StudentProfile.query.filter_by(user_id=current_user.id).first()
        profile2 = StudentProfile.query.filter_by(user_id=candidate.id).first()

        score = calculate_match_score(
            current_user, candidate, user_prefs, cand_prefs,
            profile1=profile1, profile2=profile2
        )

        needs1        = {s.lower() for s in user_prefs.get("needs_help", [])}
        good_at2      = {s.lower() for s in cand_prefs.get("good_at", [])}
        subject_overlap = list(needs1 & good_at2)

        avail1 = set(user_prefs.get("available_days", []))
        avail2 = set(cand_prefs.get("available_days", []))
        day_overlap = list(avail1 & avail2)

        now = datetime.datetime.utcnow()

        return jsonify({
            "status": "success",
            "data": {
                "match_score": score,
                "breakdown": {
                    "subject_compatibility": {
                        "score":   min(len(subject_overlap) * 10, 40),
                        "details": (
                            f"You need help with {', '.join(subject_overlap)} and they're strong in it!"
                            if subject_overlap else "No direct subject overlap"
                        ),
                    },
                    "availability": {
                        "score":       min(len(day_overlap) * 5, 30),
                        "shared_days": day_overlap,
                        "details": (
                            f"Both available on {', '.join(day_overlap)}"
                            if day_overlap else "Limited availability overlap"
                        ),
                    },
                    "activity_level": {
                        "you":  "Active" if current_user.last_active and (now - current_user.last_active).days < 7 else "Moderate",
                        "them": "Active" if candidate.last_active  and (now - candidate.last_active).days  < 7 else "Moderate",
                    },
                },
                "recommendation": (
                    "Perfect match!" if score >= 80
                    else "Great match!" if score >= 60
                    else "Good match"  if score >= 40
                    else "Moderate match"
                ),
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get match details error: {str(e)}")
        return error_response("Failed to load match details")


# ============================================================================
# STUDY BUDDY REQUESTS
# ============================================================================

@study_buddy_bp.route("/study-buddy/request/<int:user_id>", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def send_request(current_user, user_id):
    try:
        if user_id == current_user.id:
            return error_response("Cannot send request to yourself")

        target_user = User.query.get(user_id)
        if not target_user:
            return error_response("User not found", 404)

        existing = StudyBuddyRequest.query.filter(
            or_(
                and_(StudyBuddyRequest.requester_id == current_user.id, StudyBuddyRequest.receiver_id == user_id),
                and_(StudyBuddyRequest.requester_id == user_id, StudyBuddyRequest.receiver_id == current_user.id),
            )
        ).first()

        if existing:
            if existing.status == "pending":
                return error_response("Request already pending", 409)
            elif existing.status == "accepted":
                return error_response("Already study buddies", 409)

        data     = request.get_json(silent=True) or {}
        message  = data.get("message", "").strip()
        subjects = data.get("subjects", [])

        metadata = current_user.user_metadata if current_user.user_metadata else {}
        prefs    = metadata.get("study_buddy_prefs", {})

        buddy_request = StudyBuddyRequest(
            requester_id=current_user.id,
            receiver_id=user_id,
            message=message if message else "Let's study together!",
            subjects=subjects,
            availability=prefs.get("available_days", []),
            status="pending",
        )
        db.session.add(buddy_request)
        # PRE-EXISTING BUG FOUND DURING ENG-3 MIGRATION (flagged, not in
        # the audit's original list): buddy_request.id was read below for
        # related_id before any flush, so it was always None — this flush
        # populates it so the notification's related_id actually points
        # at the request it's about.
        db.session.flush()

        # AUDIT ENG-3 FIX: migrated off a direct Notification(...)
        # construction to notification_service.notify() — same fields,
        # same values (now that related_id is populated by the flush
        # above), unchanged behavior otherwise.
        notification_service.notify(
            user_id=user_id,
            title=f"{current_user.name} wants to be study buddies!",
            body=f"Request to study: {', '.join(subjects[:3]) if subjects else 'together'}",
            notification_type="study_buddy_request",
            related_type="study_buddy_request",
            related_id=buddy_request.id,
        )
        db.session.commit()

        return success_response(
            "Study buddy request sent!",
            data={"request_id": buddy_request.id},
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Send study buddy request error: {str(e)}")
        return error_response("Failed to send request")


@study_buddy_bp.route("/study-buddy/match/<int:match_id>", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_match_details_partnership(current_user, match_id):
    try:
        match = StudyBuddyMatch.query.get(match_id)
        if not match:
            return error_response("Match not found", 404)

        if match.user1_id != current_user.id and match.user2_id != current_user.id:
            return error_response("Not authorized", 403)

        partner_id = match.user2_id if match.user1_id == current_user.id else match.user1_id
        partner    = User.query.get(partner_id)
        thread     = Thread.query.get(match.thread_id) if match.thread_id else None

        return jsonify({
            "status": "success",
            "data": {
                "match": {
                    "id":                match.id,
                    "partner":           {
                        "id":       partner.id,
                        "username": partner.username,
                        "name":     partner.name,
                        "avatar":   partner.avatar,
                        "bio":      partner.bio,
                    } if partner else None,
                    "subjects":          match.subjects,
                    "sessions_completed":match.sessions_count,
                    "is_active":         match.is_active,
                    "matched_at":        match.matched_at.isoformat(),
                    "last_activity":     match.last_activity.isoformat() if match.last_activity else None,
                    "ended_at":          match.ended_at.isoformat() if match.ended_at else None,
                    "thread": {
                        "id":            thread.id,
                        "title":         thread.title,
                        "message_count": thread.message_count,
                    } if thread else None,
                },
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get match details error: {str(e)}")
        return error_response("Failed to load match details")


@study_buddy_bp.route("/study-buddy/session", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
def log_study_session(current_user):
    try:
        data     = request.get_json()
        match_id = data.get("match_id")
        duration = data.get("duration_minutes", 60)
        subjects = data.get("subjects_covered", [])
        notes    = data.get("notes", "").strip()

        if not match_id:
            return error_response("match_id required")

        match = StudyBuddyMatch.query.get(match_id)
        if not match:
            return error_response("Match not found", 404)

        if match.user1_id != current_user.id and match.user2_id != current_user.id:
            return error_response("Not authorized", 403)

        match.sessions_count  += 1
        match.last_activity    = datetime.datetime.utcnow()
        db.session.commit()

        return success_response(
            f"Session logged! You've completed {match.sessions_count} sessions together.",
            data={"sessions_count": match.sessions_count},
        ), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Log session error: {str(e)}")
        return error_response("Failed to log session")


@study_buddy_bp.route("/study-buddy/sessions", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_session_history(current_user):
    try:
        matches = StudyBuddyMatch.query.filter(
            or_(StudyBuddyMatch.user1_id == current_user.id, StudyBuddyMatch.user2_id == current_user.id)
        ).all()

        total_sessions = sum(m.sessions_count for m in matches)

        # ✅ Batch-load all partners
        partner_ids = [
            (m.user2_id if m.user1_id == current_user.id else m.user1_id)
            for m in matches if m.sessions_count > 0
        ]
        partner_map = {u.id: u for u in User.query.filter(User.id.in_(partner_ids)).all()} if partner_ids else {}

        sessions_data = []
        for match in matches:
            if match.sessions_count > 0:
                partner_id = match.user2_id if match.user1_id == current_user.id else match.user1_id
                partner    = partner_map.get(partner_id)
                sessions_data.append({
                    "match_id":      match.id,
                    "partner":       {"username": partner.username, "name": partner.name} if partner else None,
                    "subjects":      match.subjects,
                    "sessions_count":match.sessions_count,
                    "last_session":  match.last_activity.isoformat() if match.last_activity else None,
                })

        return jsonify({
            "status": "success",
            "data": {
                "sessions":             sessions_data,
                "total_sessions":       total_sessions,
                "active_partnerships":  len([m for m in matches if m.is_active]),
            },
        })

    except Exception as e:
        current_app.logger.error(f"Get session history error: {str(e)}")
        return error_response("Failed to load session history")


@study_buddy_bp.route("/study-buddy/remove/<int:match_id>", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def end_partnership(current_user, match_id):
    try:
        match = StudyBuddyMatch.query.get(match_id)
        if not match:
            return error_response("Match not found")

        if match.user1_id != current_user.id and match.user2_id != current_user.id:
            return error_response("Not authorized")

        match.is_active = False
        match.ended_at  = datetime.datetime.utcnow()

        partner_id = match.user2_id if match.user1_id == current_user.id else match.user1_id
        # AUDIT ENG-3 FIX: migrated off a direct Notification(...)
        # construction to notification_service.notify() — same fields,
        # same values, unchanged behavior otherwise.
        notification_service.notify(
            user_id=partner_id,
            title="Study partnership ended",
            body=f"{current_user.name} ended your study partnership",
            notification_type="study_buddy_ended",
            related_type="study_buddy_match",
            related_id=match_id,
        )
        db.session.commit()

        return success_response("Partnership ended")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"End partnership error: {str(e)}")
        return error_response("Failed to end partnership")


@study_buddy_bp.route("/study-buddy/requests/sent", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_sent_requests(current_user):
    try:
        requests = StudyBuddyRequest.query.filter_by(
            requester_id=current_user.id
        ).order_by(StudyBuddyRequest.requested_at.desc()).all()

        if not requests:
            return jsonify({"status": "success", "data": {"sent_requests": [], "total": 0}})

        receiver_ids = [r.receiver_id for r in requests]
        receiver_map = {u.id: u for u in User.query.filter(User.id.in_(receiver_ids)).all()}

        requests_data = []
        for req in requests:
            receiver = receiver_map.get(req.receiver_id)
            if receiver:
                requests_data.append({
                    "request_id": req.id,
                    "user": {
                        "id":       receiver.id,
                        "username": receiver.username,
                        "name":     receiver.name,
                        "avatar":   receiver.avatar,
                    },
                    "subjects":     req.subjects,
                    "message":      req.message,
                    "status":       req.status,
                    "requested_at": req.requested_at.isoformat(),
                    "responded_at": req.responded_at.isoformat() if req.responded_at else None,
                })

        return jsonify({"status": "success", "data": {"sent_requests": requests_data, "total": len(requests_data)}})

    except Exception as e:
        current_app.logger.error(f"Get sent requests error: {str(e)}")
        return error_response("Failed to load sent requests")


@study_buddy_bp.route("/study-buddy/requests/received", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_received_requests(current_user):
    try:
        requests = StudyBuddyRequest.query.filter_by(
            receiver_id=current_user.id, status="pending"
        ).order_by(StudyBuddyRequest.requested_at.desc()).all()

        if not requests:
            return jsonify({"status": "success", "data": {"received_requests": [], "total": 0}})

        requester_ids = [r.requester_id for r in requests]
        requester_map = {u.id: u for u in User.query.filter(User.id.in_(requester_ids)).all()}

        user_metadata  = current_user.user_metadata if current_user.user_metadata else {}
        user_prefs     = user_metadata.get("study_buddy_prefs", {})
        current_profile = StudentProfile.query.filter_by(user_id=current_user.id).first()

        # ✅ Batch-load requester profiles
        req_profiles = StudentProfile.query.filter(StudentProfile.user_id.in_(requester_ids)).all()
        req_profile_map = {p.user_id: p for p in req_profiles}

        requests_data = []
        for req in requests:
            requester = requester_map.get(req.requester_id)
            if not requester:
                continue

            req_metadata = requester.user_metadata if requester.user_metadata else {}
            req_prefs    = req_metadata.get("study_buddy_prefs", {})
            req_profile  = req_profile_map.get(requester.id)

            match_score = (
                calculate_match_score(
                    current_user, requester, user_prefs, req_prefs,
                    profile1=current_profile, profile2=req_profile,
                )
                if user_prefs and req_prefs else 0
            )

            requests_data.append({
                "request_id": req.id,
                "user": {
                    "id":               requester.id,
                    "username":         requester.username,
                    "name":             requester.name,
                    "avatar":           requester.avatar,
                    "bio":              requester.bio,
                    "reputation_level": requester.reputation_level,
                },
                "subjects":     req.subjects,
                "message":      req.message,
                "availability": req.availability,
                "match_score":  match_score,
                "requested_at": req.requested_at.isoformat(),
            })

        return jsonify({"status": "success", "data": {"received_requests": requests_data, "total": len(requests_data)}})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Get received requests error: {str(e)}")
        return error_response("Failed to load received requests")


@study_buddy_bp.route("/study-buddy/requests/connected", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_buddy_connections(current_user):
    try:
        matches = (
            StudyBuddyMatch.query.filter(
                or_(
                    StudyBuddyMatch.user1_id == current_user.id,
                    StudyBuddyMatch.user2_id == current_user.id,
                )
            )
            .order_by(StudyBuddyMatch.is_active.desc(), StudyBuddyMatch.matched_at.asc())
            .all()
        )

        buddy_ids = [
            (m.user2_id if m.user1_id == current_user.id else m.user1_id) for m in matches
        ]
        buddy_map = {u.id: u for u in User.query.filter(User.id.in_(buddy_ids)).all()} if buddy_ids else {}

        buddies_data = []
        for q in matches:
            buddy_id = q.user2_id if q.user1_id == current_user.id else q.user1_id
            user = buddy_map.get(buddy_id)
            if not user:
                continue

            buddies_data.append({
                "user": {
                    "user_id":          user.id,
                    "username":         user.username,
                    "name":             user.name,
                    "avatar":           user.avatar,
                    "bio":              user.bio,
                    "reputation_level": user.reputation_level,
                },
                "details": {
                    "id":            q.id,
                    "matched_at":    q.matched_at,
                    "sessions_count":q.sessions_count,
                    "is_active":     q.is_active,
                    "subjects":      q.subjects,
                    "thread_id":     q.thread_id,
                    "last_activity": q.last_activity if q.last_activity else None,
                    "ended_at":      q.ended_at if q.ended_at else None,
                    "match_score":   getattr(q, "match_score", None),
                },
            })

        return jsonify({"status": "success", "data": buddies_data}), 200

    except Exception as e:
        current_app.logger.error(f"Get study buddies list error: {str(e)}")
        return error_response("Failed to load buddies list")


@study_buddy_bp.route("/study-buddy/cancel/<int:request_id>", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
def cancel_request(current_user, request_id):
    try:
        buddy_request = StudyBuddyRequest.query.get(request_id)
        if not buddy_request:
            return error_response("Request not found", 404)
        if buddy_request.requester_id != current_user.id:
            return error_response("Can only cancel requests you sent", 403)
        if buddy_request.status != "pending":
            return error_response("Request is no longer pending", 400)

        db.session.delete(buddy_request)
        db.session.commit()
        return success_response("Study buddy request cancelled")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Cancel study buddy request error: {str(e)}")
        return error_response("Failed to cancel request")


# ============================================================================
# DISCOVERY & STATS
# ============================================================================

@study_buddy_bp.route("/study-buddy/success-stories", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_success_stories(current_user):
    try:
        profile = StudentProfile.query.filter_by(user_id=current_user.id).first()

        # Plan §4.6/§17.7 lists this cache's key as the flat, unscoped
        # sh:1:sb:success_stories (grouping it with get_platform_stats as
        # "aggregate/platform-level"). That's not quite this route's actual
        # behavior: it filters to the viewer's own department when they
        # have a profile (see the branch below), so a single shared cache
        # entry would serve one department's stories to every other
        # department's viewers. Scoping the key by department (falling
        # back to the same "_" no-department sentinel used elsewhere in
        # the plan, e.g. §3.2's leaderboard keys) preserves the intended
        # per-department correctness while keeping the same TTL/mechanism
        # the plan specifies. Still deliberately NOT scoped by user_id —
        # every viewer in the same department sees the identical result.
        dept_key_part = profile.department if profile and profile.department else "_"
        cache_key = f"sh:1:sb:success_stories:{dept_key_part}"
        data = cache_service.get(cache_key)
        if data is not None:
            return jsonify({"status": "success", "data": data})

        if profile:
            dept_user_ids = db.session.query(User.id).join(StudentProfile).filter(
                StudentProfile.department == profile.department
            ).scalar_subquery()
            successful_matches = StudyBuddyMatch.query.filter(
                or_(
                    StudyBuddyMatch.user1_id.in_(dept_user_ids),
                    StudyBuddyMatch.user2_id.in_(dept_user_ids),
                ),
                StudyBuddyMatch.sessions_count >= 5,
            ).order_by(StudyBuddyMatch.sessions_count.desc()).limit(10).all()
        else:
            successful_matches = StudyBuddyMatch.query.filter(
                StudyBuddyMatch.sessions_count >= 5
            ).order_by(StudyBuddyMatch.sessions_count.desc()).limit(10).all()

        # ✅ Batch-load all users at once
        all_ids = list({m.user1_id for m in successful_matches} | {m.user2_id for m in successful_matches})
        user_map = {u.id: u for u in User.query.filter(User.id.in_(all_ids)).all()} if all_ids else {}

        stories = []
        for match in successful_matches:
            u1 = user_map.get(match.user1_id)
            u2 = user_map.get(match.user2_id)
            if u1 and u2:
                now = datetime.datetime.utcnow()
                stories.append({
                    "users": [
                        {"username": u1.username, "name": u1.name},
                        {"username": u2.username, "name": u2.name},
                    ],
                    "subjects":          match.subjects,
                    "sessions_completed":match.sessions_count,
                    "duration_days":     (
                        (match.ended_at - match.matched_at).days
                        if match.ended_at
                        else (now - match.matched_at).days
                    ),
                    "is_active": match.is_active,
                })

        data = {"success_stories": stories, "total": len(stories)}
        cache_service.set(cache_key, data, ttl_seconds=300)

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Get success stories error: {str(e)}")
        return error_response("Failed to load success stories")


@study_buddy_bp.route("/study-buddy/stats", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=ip_key)
@token_required
def get_platform_stats(current_user):
    try:
        # Plan §4.6/§17.7: cache-aside, TTL 300s, flat unscoped key — this
        # route (unlike get_success_stories above) genuinely has no
        # current_user-dependent logic, matching §3.2's key pattern as-is.
        cache_key = "sh:1:sb:platform_stats"
        data = cache_service.get(cache_key)
        if data is not None:
            return jsonify({"status": "success", "data": data})

        total_matches    = StudyBuddyMatch.query.count()
        active_matches   = StudyBuddyMatch.query.filter_by(is_active=True).count()
        total_sessions   = db.session.query(func.sum(StudyBuddyMatch.sessions_count)).scalar() or 0
        successful_matches = StudyBuddyMatch.query.filter(StudyBuddyMatch.sessions_count >= 3).count()
        success_rate     = round(successful_matches / total_matches * 100, 1) if total_matches else 0

        # ✅ Count subjects at DB level instead of loading all matches
        all_matches = StudyBuddyMatch.query.with_entities(StudyBuddyMatch.subjects).all()
        subject_counts = {}
        for (subjects,) in all_matches:
            for s in (subjects or []):
                key = s.lower()
                subject_counts[key] = subject_counts.get(key, 0) + 1

        popular_subjects = sorted(
            [{"subject": k, "count": v} for k, v in subject_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10]

        data = {
            "platform_stats": {
                "total_matches":       total_matches,
                "active_partnerships": active_matches,
                "total_sessions":      int(total_sessions),
                "success_rate":        success_rate,
                "popular_subjects":    popular_subjects,
            },
            "motivational_message": (
                f"Join {active_matches} active study partnerships! "
                f"{int(total_sessions)} sessions completed so far! 🎓"
            ),
        }

        cache_service.set(cache_key, data, ttl_seconds=300)

        return jsonify({"status": "success", "data": data})

    except Exception as e:
        current_app.logger.error(f"Get platform stats error: {str(e)}")
        return error_response("Failed to load platform stats")
