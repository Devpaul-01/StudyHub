"""
tests/integration/test_admin_routes.py

routes/admin.py has four endpoints (health, scheduler status, AI
provider status, manual reconciliation trigger) and, per the module's
own docstring, is the first real consumer of admin_required
(role_required("admin", "system")) — previously untested at every
level: zero unit tests, zero integration tests, nothing exercising the
admin/system role gate at all.

admin_bp is registered as a sibling top-level blueprint, NOT nested
inside student_bp (see that module's docstring), so the student_bp-only
CSRF double-submit check (routes/student/__init__.py::enforce_csrf)
does not apply here — these tests intentionally don't use csrf_headers.

All four routes are GET except /reconciliation/run (POST).
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# ROLE GATE — admin_required = role_required("admin", "system")
# ============================================================================

class TestAdminRoleGate:
    def test_health_rejects_unauthenticated_request(self, client):
        resp = client.get("/admin/health")
        assert resp.status_code == 401

    def test_health_rejects_plain_student_role(self, client, make_user, auth_headers):
        student = make_user(role="student", status="approved")
        headers = auth_headers(student)

        resp = client.get("/admin/health", headers=headers)

        assert resp.status_code == 403

    def test_health_allows_admin_role(self, client, make_user, auth_headers):
        admin = make_user(role="admin", status="approved")
        headers = auth_headers(admin)

        resp = client.get("/admin/health", headers=headers)

        assert resp.status_code == 200

    def test_health_allows_system_role(self, client, make_user, auth_headers):
        """
        admin_required accepts both "admin" and "system" — per the
        module's own comment, "system" is kept alongside "admin" since
        that was the original (previously-dead) inline check's role set.
        """
        system_account = make_user(role="system", status="approved")
        headers = auth_headers(system_account)

        resp = client.get("/admin/health", headers=headers)

        assert resp.status_code == 200

    def test_scheduler_status_rejects_non_admin(self, client, make_user, auth_headers):
        student = make_user(role="student", status="approved")
        resp = client.get("/admin/scheduler", headers=auth_headers(student))
        assert resp.status_code == 403

    def test_ai_providers_rejects_non_admin(self, client, make_user, auth_headers):
        student = make_user(role="student", status="approved")
        resp = client.get("/admin/ai-providers", headers=auth_headers(student))
        assert resp.status_code == 403

    def test_reconciliation_run_rejects_non_admin(self, client, make_user, auth_headers):
        student = make_user(role="student", status="approved")
        resp = client.post("/admin/reconciliation/run", headers=auth_headers(student))
        assert resp.status_code == 403

    def test_reconciliation_run_is_not_csrf_gated(self, client, make_user, auth_headers):
        """
        admin_bp is a sibling top-level blueprint, not nested inside
        student_bp — the student_bp-only enforce_csrf before_request
        hook must not apply here. A POST with a valid admin bearer token
        and NO X-CSRF-Token header must not be rejected with 403/CSRF;
        it should reach the route (200) rather than being blocked at the
        CSRF layer.
        """
        admin = make_user(role="admin", status="approved")
        resp = client.post("/admin/reconciliation/run", headers=auth_headers(admin))

        assert resp.status_code == 200


# ============================================================================
# GET /admin/health
# ============================================================================

class TestAdminHealth:
    def test_returns_database_and_redis_connectivity(self, client, make_user, auth_headers):
        admin = make_user(role="admin", status="approved")

        resp = client.get("/admin/health", headers=auth_headers(admin))

        body = resp.get_json()
        assert body["status"] == "success"
        data = body["data"]
        assert "database" in data
        assert "connected" in data["database"]
        assert "redis" in data
        assert "connected" in data["redis"]

    def test_database_connected_true_against_real_test_db(self, client, make_user, auth_headers):
        """The route runs `SELECT 1` against the real (SQLite) session —
        against a healthy test DB this must report connected."""
        admin = make_user(role="admin", status="approved")

        resp = client.get("/admin/health", headers=auth_headers(admin))

        assert resp.get_json()["data"]["database"]["connected"] is True

    def test_includes_email_configured_rate_limiter_storage_and_environment_fields(
        self, client, make_user, auth_headers
    ):
        admin = make_user(role="admin", status="approved")

        resp = client.get("/admin/health", headers=auth_headers(admin))

        data = resp.get_json()["data"]
        assert "email_configured" in data
        assert data["rate_limiter_storage"] in ("redis", "memory")
        assert "environment" in data


# ============================================================================
# GET /admin/scheduler
# ============================================================================

class TestAdminSchedulerStatus:
    def test_reports_not_running_under_test_config(self, client, make_user, auth_headers):
        """
        The integration app fixture sets SCHEDULER_ENABLED=false (see
        this suite's conftest), so scheduler.running must be False and
        the jobs list empty — not an error.
        """
        admin = make_user(role="admin", status="approved")

        resp = client.get("/admin/scheduler", headers=auth_headers(admin))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["running"] is False
        assert data["jobs"] == []


# ============================================================================
# GET /admin/ai-providers
# ============================================================================

class TestAdminAiProviderStatus:
    def test_returns_provider_manager_stats_without_making_real_calls(
        self, client, make_user, auth_headers
    ):
        """
        Under TestingConfig, warm_model_discovery() is never called (see
        test_app_factory_ai_discovery_wiring.py), so this must return
        provider_manager's existing static stats without attempting any
        live network call.
        """
        admin = make_user(role="admin", status="approved")

        resp = client.get("/admin/ai-providers", headers=auth_headers(admin))

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert "data" in body


# ============================================================================
# POST /admin/reconciliation/run
# ============================================================================

class TestAdminTriggerReconciliation:
    def test_runs_against_empty_db_with_zero_drift(self, client, make_user, auth_headers):
        admin = make_user(role="admin", status="approved")

        resp = client.post("/admin/reconciliation/run", headers=auth_headers(admin))

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        data = body["data"]
        assert data["corrected_count"] == 0
        assert data["drifts_found"] == 0
        assert "checked_at" in data

    def test_detects_and_corrects_a_real_counter_drift(
        self, client, db_session, make_user, make_post, make_comment, auth_headers
    ):
        """
        Seed a genuine denormalized-counter drift (Post.comments_count
        out of sync with the actual comment row count) and confirm the
        admin-triggered run finds and corrects it, exercising the real
        reconciliation logic end-to-end through the route rather than
        just confirming the endpoint returns 200 against an empty DB.
        """
        admin = make_user(role="admin", status="approved")
        author = make_user(role="student", status="approved")
        post = make_post(author, title="Drift post", comments_count=5)
        make_comment(post, author)  # only 1 real comment, counter says 5

        resp = client.post("/admin/reconciliation/run", headers=auth_headers(admin))

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["counters_checked"] >= 1
        assert data["drifts_found"] >= 1
        assert data["corrected_count"] >= 1

        from models import Post
        refreshed = Post.query.get(post.id)
        assert refreshed.comments_count == 1
