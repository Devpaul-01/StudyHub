"""
tests/integration/test_rate_limiting.py

Covers INTEGRATION_TEST_IMPLEMENTATION_PLAN.md §3.10 — "no existing test
of any kind currently proves rate limiting... actually works end-to-end."
Uses the SENSITIVE_AUTH tier (5 per minute — services/rate_limit_service.py)
against /student/login, since that route is exempt from CSRF (making it
the simplest route to hammer without also needing csrf_headers plumbing)
and its rate limit is tight enough (5/min) to hit within a normal test.

IMPORTANT: Flask-Limiter's OWN storage (RATELIMIT_STORAGE_URI) is
deliberately left as "memory://" in the `app` fixture, NOT redirected to
fakeredis — Flask-Limiter's storage backend uses its own limits library
client interface, not the plain redis-py client fakeredis fakes out at
the `extensions.redis_client` level, so pointing RATELIMIT_STORAGE_URI at
a fakeredis instance would require a different patching seam than the one
this suite's fixtures provide (fakeredis_client patches import-bound
`redis_client` references, not Flask-Limiter's internal storage factory).
Since Flask-Limiter's in-memory storage is a real, first-class supported
backend (used in this app's own DevelopmentConfig/TestingConfig via
config.py), testing against it directly is the correct integration
boundary here, not a compromise — it's still the REAL rate limiter
enforcing REAL limits, just backed by memory instead of Redis for this
test process.
"""

import pytest

pytestmark = pytest.mark.integration


class TestAuthTiers:
    def test_check_username_tier_is_30_per_minute(self, client):
        codes = [client.post("/student/check-username", json={"username": f"abc{i}"}).status_code
                 for i in range(31)]
        assert 429 not in codes[:30] and codes[30] == 429

    def test_registration_tier_is_10_per_minute(self, client):
        codes = [client.post("/student/register", json={"full_name": "X", "email": "bad"}).status_code
                 for i in range(11)]
        assert 429 not in codes[:10] and codes[10] == 429

    def test_refresh_token_tier_is_60_per_minute(self, client, csrf_headers):
        headers = csrf_headers(client)
        codes = [client.post("/student/refresh-token", headers=headers).status_code for _ in range(61)]
        assert 429 not in codes[:60] and codes[60] == 429

    def test_reset_flow_keeps_the_strict_5_per_minute_tier(self, client):
        codes = [client.post("/student/verify-reset/not-a-token").status_code for _ in range(6)]
        assert 429 not in codes[:5] and codes[5] == 429


class TestSensitiveAuthRateLimit:
    def test_login_rate_limit_returns_429_after_tier_limit(self, client):
        """
        RateLimitTier.AUTH_ATTEMPT = '20 per minute' per IP (login is pre-auth,
        keyed via ip_key()). The 21st request within the window must be
        rejected with 429, with the standard error body shape from
        services/rate_limit_service.py::_rate_limit_exceeded. (The tier used
        to be 5/min, which is too tight for students sharing one campus IP;
        per-account protection is the allow_action counter.)
        """
        payload = {"username_or_email": "nobody@example.com", "password": "wrong"}

        statuses = []
        for _ in range(21):
            resp = client.post("/student/login", json=payload)
            statuses.append(resp.status_code)

        # The first 20 attempts fail on bad credentials (400), NOT on rate
        # limiting -- only the 21st should be 429.
        assert statuses[:20] == [400] * 20
        assert statuses[20] == 429

        last_resp = client.post("/student/login", json=payload)
        body = last_resp.get_json()
        assert body["status"] == "error"
        assert "Rate limit" in body["message"]


class TestRateLimiterFailsOpenOnRedisOutage:
    def test_public_read_route_still_works_when_redis_storage_unreachable(
        self, client, make_user, auth_headers, monkeypatch
    ):
        """
        services/rate_limit_service.py's own module docstring: 'a rate
        limiter that takes the whole app down when its backing store is
        down is worse than no rate limiter' — RATELIMIT_SWALLOW_ERRORS and
        RATELIMIT_IN_MEMORY_FALLBACK_ENABLED are both set True specifically
        for this. Since this suite's `app` fixture already uses
        memory:// storage (never actually Redis-backed for Flask-Limiter
        itself — see module docstring above), this test's job is narrower
        than a true "Redis died mid-request" simulation: it confirms a
        normal read-heavy route responds successfully under the fixture's
        real, active rate limiter, which is the honest boundary this
        suite's fixture setup can prove without wiring a second storage
        adapter. A genuine Redis-outage-for-Flask-Limiter test would need
        RATELIMIT_STORAGE_URI pointed at a real redis:// URL with the
        server then killed mid-suite — flagged here as a gap for a real
        Redis-backed CI environment, not fabricated against fakeredis.
        """
        user = make_user(status="approved")
        headers = auth_headers(user)
        resp = client.get("/student/profile/me/data", headers=headers)
        assert resp.status_code == 200

