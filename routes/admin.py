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

from flask import Blueprint, jsonify, current_app
from routes.student.helpers import admin_required
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


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
