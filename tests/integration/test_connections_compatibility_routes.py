"""
tests/integration/test_connections_compatibility_routes.py

routes/student/connections/compatibility.py has a single endpoint,
GET /connections/overview/<user_id>, previously untested. It calls out
to a multi-provider AI system (services/ai_provider_service.py); with
no provider API keys configured in this test environment,
provider_manager.get_working_provider() always returns None, so every
request here exercises the documented fallback path (a plain JSON
response built by generate_fallback_overview(), not the SSE stream).
That's a real, env-driven production code path -- not a test-only
shortcut -- so it's exercised here directly rather than mocked.
"""

import pytest

pytestmark = pytest.mark.integration


class TestGetConnectionOverview:
    def test_nonexistent_user_returns_404(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get("/student/connections/overview/999999", headers=auth_headers(user))

        assert resp.status_code == 404

    def test_self_overview_rejected(self, client, make_user, auth_headers):
        user = make_user()

        resp = client.get(f"/student/connections/overview/{user.id}", headers=auth_headers(user))

        assert resp.status_code == 400

    def test_no_ai_provider_returns_fallback_overview(
        self, client, make_user, auth_headers
    ):
        me = make_user()
        target = make_user()

        resp = client.get(
            f"/student/connections/overview/{target.id}", headers=auth_headers(me)
        )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["ai_available"] is False
        assert data["target_user"]["id"] == target.id
        assert "ai_overview" in data
        assert "Connection Score" in data["ai_overview"]
        assert "compatibility" in data
        assert not data["already_connected"]

    def test_shared_subject_reflected_in_compatibility_score(
        self, client, db_session, make_user, auth_headers
    ):
        from models import OnboardingDetails

        me = make_user()
        db_session.add(
            OnboardingDetails(
                user_id=me.id, email=me.email,
                strong_subjects=["python"], help_subjects=[],
            )
        )
        target = make_user()
        db_session.add(
            OnboardingDetails(
                user_id=target.id, email=target.email,
                strong_subjects=["python"], help_subjects=[],
            )
        )
        db_session.flush()

        resp = client.get(
            f"/student/connections/overview/{target.id}", headers=auth_headers(me)
        )

        data = resp.get_json()["data"]
        assert "python" in data["compatibility"]["shared_subjects"]
        assert data["compatibility"]["score"] > 0

    def test_already_connected_flag_reflects_accepted_connection(
        self, client, make_user, make_connection, auth_headers
    ):
        me = make_user()
        target = make_user()
        make_connection(me, target, status="accepted")

        resp = client.get(
            f"/student/connections/overview/{target.id}", headers=auth_headers(me)
        )

        data = resp.get_json()["data"]
        assert data["already_connected"] is True


# ============================================================================
# Audit CON-21: block check, caching, no error leakage
# ============================================================================

class TestOverviewAudit:
    def test_blocked_pair_gets_404_either_direction(self, client, make_user, make_connection, auth_headers):
        a, b = make_user(), make_user()
        make_connection(a, b, status="blocked", blocked_by_id=a.id)

        blocker = client.get(f"/student/connections/overview/{b.id}", headers=auth_headers(a))
        blocked = client.get(f"/student/connections/overview/{a.id}", headers=auth_headers(b))

        assert blocker.status_code == 404 and blocked.status_code == 404

    def test_cached_overview_skips_the_ai_provider(self, client, make_user, auth_headers, monkeypatch):
        import routes.student.connections.compatibility as comp
        me, target = make_user(), make_user()
        store = {}
        monkeypatch.setattr(comp.cache_service, "get", lambda k: store.get(k))
        monkeypatch.setattr(comp.cache_service, "set", lambda k, v, ttl_seconds: store.__setitem__(k, v))
        store[f"sh:1:conn:overview:{me.id}:{target.id}"] = "Cached overview text"

        def boom(*a, **k):
            raise AssertionError("AI provider must not be called on a cache hit")
        monkeypatch.setattr(comp.provider_manager, "get_working_provider", boom)

        resp = client.get(f"/student/connections/overview/{target.id}", headers=auth_headers(me))

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Cached overview text" in body and '"type": "done"' in body

    def test_internal_errors_are_not_echoed_to_the_client(self, client, make_user, auth_headers, monkeypatch):
        import routes.student.connections.compatibility as comp
        me, target = make_user(), make_user()
        monkeypatch.setattr(comp, "gather_user_data", lambda u: (_ for _ in ()).throw(RuntimeError("secret internals")))

        resp = client.get(f"/student/connections/overview/{target.id}", headers=auth_headers(me))

        assert resp.status_code == 500
        assert "secret internals" not in resp.get_data(as_text=True)
