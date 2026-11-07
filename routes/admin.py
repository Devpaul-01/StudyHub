"""
routes/admin.py

Operational/admin endpoints. Four routes: health, scheduler status,
AI provider status, and a manual reconciliation trigger. Each is a thin
wrapper over an already-built subsystem — see
ADMIN_ENDPOINTS_IMPLEMENTATION_PLAN.md for why these four and not more.

Registered as a sibling top-level blueprint (same pattern as
routes/student/auth.py's google_bp in app.py), not nested inside
student_bp — see the implementation plan's "Blueprint placement and
CSRF" section for why: student_bp's CSRF double-submit enforcement is
scoped to that blueprint and is designed around browser-session,
cookie-based end-user flows, which isn't the right model for
operator-driven admin/ops endpoints.

Auth: every route uses admin_required (routes/student/helpers.py),
which is role_required("admin", "system") — this is the FIRST real
consumer of that decorator; see the implementation plan for the
discovered admin/moderator role-vocabulary inconsistency this surfaces.
"""

from flask import Blueprint, jsonify, current_app, render_template, request
from routes.student.helpers import admin_required, moderator_required, success_response, error_response
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# Batch 5: admin panel UI. The four routes below (health, scheduler,
# ai-providers, reconciliation) already existed as a working JSON API
# with no UI anywhere calling any of them — this page is the first
# frontend for them. Same admin_required gate as the API routes, so
# non-admin/system accounts get the same 403 hitting this page that they
# already get hitting the API directly.
@admin_bp.route("", methods=["GET"])
@admin_required
def admin_panel(current_user):
    return render_template("admin.html")


@admin_bp.route("/health", methods=["GET"])
@admin_required
@limiter.limit(RateLimitTier.ADMIN, key_func=user_or_ip_key)
def admin_health(current_user):
    from extensions import db, redis_client
    from sqlalchemy import text
    import time

    data = {}

    try:
        db.session.execute(text("SELECT 1"))
        data["database"] = {"connected": True}
    except Exception as e:
        data["database"] = {"connected": False, "error": str(e)}

    try:
        start = time.monotonic()
        redis_client.ping()
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        data["redis"] = {"connected": True, "latency_ms": latency_ms}
    except Exception as e:
        data["redis"] = {"connected": False, "error": str(e)}

    data["email_configured"] = bool(
        current_app.config.get("MAIL_USERNAME") and current_app.config.get("MAIL_PASSWORD")
    )
    data["rate_limiter_storage"] = (
        "redis" if str(current_app.config.get("RATE_LIMIT_STORAGE_URI", "")).startswith("redis")
        else "memory"
    )
    data["environment"] = current_app.config.get("FLASK_ENV", "unknown")

    return jsonify({"status": "success", "data": data})


@admin_bp.route("/scheduler", methods=["GET"])
@admin_required
@limiter.limit(RateLimitTier.ADMIN, key_func=user_or_ip_key)
def admin_scheduler_status(current_user):
    from scheduler import scheduler

    jobs = []
    if scheduler.running:
        for job in scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            })

    return jsonify({
        "status": "success",
        "data": {
            "running": scheduler.running,
            "jobs": jobs,
        }
    })


@admin_bp.route("/ai-providers", methods=["GET"])
@admin_required
@limiter.limit(RateLimitTier.ADMIN, key_func=user_or_ip_key)
def admin_ai_provider_status(current_user):
    from services.ai_provider_service import provider_manager

    return jsonify({
        "status": "success",
        "data": provider_manager.get_stats(),
    })


@admin_bp.route("/reconciliation/run", methods=["POST"])
@admin_required
@limiter.limit(RateLimitTier.ADMIN, key_func=user_or_ip_key)
def admin_trigger_reconciliation(current_user):
    from services.reconciliation_service import reconcile_denormalized_counts

    report = reconcile_denormalized_counts()

    return jsonify({
        "status": "success",
        "message": "Reconciliation complete",
        "data": {
            "counters_checked": report.counters_checked,
            "drifts_found": len(report.drifts_found),
            "corrected_count": report.corrected_count,
            "alerted_only_count": report.alerted_only_count,
            "checked_at": report.checked_at.isoformat(),
        }
    })


# ============================================================================
# MODERATION QUEUE (Batch 5)
# ============================================================================
#
# PostReport rows were being written (routes/student/posts/crud.py's
# report route) ever since that model existed, but nothing anywhere
# ever read them back — no endpoint listed pending reports, resolved
# one, or issued a warning, even though UserWarning (models.py) existed
# for exactly that purpose and was never used by any route either.
# MessageReport is new this batch (see models.py's MessageReport
# docstring for why — message reports were never even persisted before
# this).
#
# Uses moderator_required (routes/student/helpers.py), not
# admin_required — a moderator reviewing reports is this feature's
# primary user, not a secondary admin capability. See the matching note
# on moderator_required's definition for the precedent this follows.

from models import db, PostReport, MessageReport, UserWarning, User, Post, Message
from datetime import datetime


def _report_to_dict(report, content_type):
    reporter = User.query.get(report.reported_by)
    reviewer = User.query.get(report.reviewed_by) if report.reviewed_by else None
    data = {
        "id": report.id,
        "type": content_type,
        "reason": report.reason,
        "description": report.description,
        "status": report.status,
        "reported_at": report.reported_at.isoformat() if report.reported_at else None,
        "reviewed_at": report.reviewed_at.isoformat() if report.reviewed_at else None,
        "action_taken": report.action_taken,
        "review_notes": report.review_notes,
        "reporter": {
            "id": reporter.id, "username": reporter.username, "name": reporter.name,
        } if reporter else None,
        "reviewer": {
            "id": reviewer.id, "username": reviewer.username, "name": reviewer.name,
        } if reviewer else None,
    }
    if content_type == "post":
        post = Post.query.get(report.post_id)
        data["content_id"] = report.post_id
        data["content_preview"] = {
            "title": post.title,
            "excerpt": (post.text_content or "")[:200],
            "author_id": post.student_id,
        } if post else None
    else:  # "message"
        message = Message.query.get(report.message_id)
        data["content_id"] = report.message_id
        data["content_preview"] = {
            "body": (message.body or "")[:200],
            "sender_id": message.sender_id,
        } if message else None
    return data


@admin_bp.route("/moderation/reports", methods=["GET"])
@moderator_required
@limiter.limit(RateLimitTier.ADMIN, key_func=user_or_ip_key)
def list_moderation_reports(current_user):
    """
    List content reports (posts and messages together, newest first),
    for the moderation queue.

    Query params:
      status: "pending" (default) | "reviewed" | "all"
      type:   "post" | "message" | "all" (default)
      page, per_page: pagination (default 1, 20; max per_page 100)
    """
    status = request.args.get("status", "pending")
    content_type = request.args.get("type", "all")
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))

    items = []

    if content_type in ("post", "all"):
        q = PostReport.query
        if status != "all":
            q = q.filter(PostReport.status == status)
        for r in q.all():
            items.append((r.reported_at, _report_to_dict(r, "post")))

    if content_type in ("message", "all"):
        q = MessageReport.query
        if status != "all":
            q = q.filter(MessageReport.status == status)
        for r in q.all():
            items.append((r.reported_at, _report_to_dict(r, "message")))

    # Both report types are pulled and merged in Python rather than with
    # a SQL UNION, since they're two separate tables/models with no
    # shared base — fine at moderation-queue volumes (this is an
    # operator tool, not a high-traffic endpoint), but would need
    # revisiting if report volume ever grew large enough for this to be
    # a real cost.
    items.sort(key=lambda pair: pair[0] or datetime.min, reverse=True)
    total = len(items)
    start = (page - 1) * per_page
    page_items = [d for _, d in items[start:start + per_page]]

    return success_response("Reports loaded", data={
        "reports": page_items,
        "pagination": {
            "page": page, "per_page": per_page, "total": total,
            "pages": (total + per_page - 1) // per_page if per_page else 0,
        },
    })


@admin_bp.route("/moderation/reports/<report_type>/<int:report_id>/resolve", methods=["POST"])
@moderator_required
@limiter.limit(RateLimitTier.ADMIN, key_func=user_or_ip_key)
def resolve_moderation_report(current_user, report_type, report_id):
    """
    Mark a report reviewed.

    report_type: "post" | "message" (matches the "type" field each
    report in GET .../reports already carries, so the frontend never
    has to guess which table a given report id belongs to).

    Body: { "action_taken": "content_removed" | "warning_issued" |
            "no_action" | "...", "review_notes": "optional free text" }
    """
    if report_type not in ("post", "message"):
        return error_response("report_type must be 'post' or 'message'", 400)

    model = PostReport if report_type == "post" else MessageReport
    report = model.query.get(report_id)
    if not report:
        return error_response("Report not found", 404)

    try:
        data = request.get_json(silent=True) or {}
        action_taken = (data.get("action_taken") or "").strip()
        if not action_taken:
            return error_response("action_taken is required")

        report.status = "reviewed"
        report.reviewed_by = current_user.id
        report.review_notes = (data.get("review_notes") or "").strip() or None
        report.action_taken = action_taken
        report.reviewed_at = datetime.utcnow()
        db.session.commit()

        return success_response("Report resolved", data=_report_to_dict(report, report_type))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Resolve moderation report error: {str(e)}")
        return error_response("Failed to resolve report")


@admin_bp.route("/moderation/warnings", methods=["POST"])
@moderator_required
@limiter.limit(RateLimitTier.ADMIN, key_func=user_or_ip_key)
def issue_warning(current_user):
    """
    Issue a warning to a user. UserWarning (models.py) existed with no
    route anywhere ever writing to it.

    Body: { "user_id": int, "reason": "...", "severity": "low" | "medium" | "high",
            "description": "optional longer explanation",
            "related_type": "post" | "message" (optional),
            "related_id": int (optional) }

    related_type/related_id let a warning point back at the specific
    report, post, or message that prompted it — both columns already
    existed on UserWarning with nothing populating them.
    """
    try:
        data = request.get_json(silent=True) or {}
        user_id = data.get("user_id")
        reason = (data.get("reason") or "").strip()
        severity = (data.get("severity") or "low").strip()
        description = (data.get("description") or "").strip() or None
        related_type = (data.get("related_type") or "").strip() or None
        related_id = data.get("related_id")

        if not user_id or not reason:
            return error_response("user_id and reason are required")
        if len(reason) > 200:  # matches UserWarning.reason's db.String(200)
            return error_response("reason is too long (200 characters max)")
        if severity not in ("low", "medium", "high"):
            return error_response("severity must be low, medium, or high")

        user = User.query.get(user_id)
        if not user:
            return error_response("User not found", 404)

        warning = UserWarning(
            user_id=user_id,
            issued_by=current_user.id,
            reason=reason,
            severity=severity,
            description=description,
            related_type=related_type,
            related_id=related_id,
        )
        db.session.add(warning)
        db.session.commit()

        return success_response("Warning issued", data={
            "id": warning.id, "user_id": user_id, "severity": severity,
            "issued_at": warning.issued_at.isoformat(),
        }), 201
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Issue warning error: {str(e)}")
        return error_response("Failed to issue warning")


@admin_bp.route("/moderation/warnings/<int:user_id>", methods=["GET"])
@moderator_required
@limiter.limit(RateLimitTier.ADMIN, key_func=user_or_ip_key)
def get_user_warnings(current_user, user_id):
    """A user's warning history — useful context before issuing a new one."""
    user = User.query.get(user_id)
    if not user:
        return error_response("User not found", 404)

    warnings = UserWarning.query.filter_by(user_id=user_id).order_by(
        UserWarning.issued_at.desc()
    ).all()

    return success_response("Warnings loaded", data={
        "user": {"id": user.id, "username": user.username, "name": user.name},
        "warnings": [{
            "id": w.id, "reason": w.reason, "description": w.description,
            "severity": w.severity, "issued_by": w.issued_by,
            "related_type": w.related_type, "related_id": w.related_id,
            "is_active": w.is_active, "issued_at": w.issued_at.isoformat(),
        } for w in warnings],
        "total": len(warnings),
    })
