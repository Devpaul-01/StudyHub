"""
tests/integration/test_track_visit_rate_limit.py

Regression coverage for an audit finding: POST /api/track-visit
(app.py::track_visit) is a public, unauthenticated endpoint with no auth
gate of any kind, and its own docstring used to flag that it had no rate
limiting at all -- "consider wiring it in before relying on this in
production". Since it's reachable by anyone with no login required, an
unlimited version of this endpoint is a cheap way to flood the
visitor_logs table (or, before a store existed, just hammer the DB
connection pool) from a single IP with no friction at all.

Follows the same approach as test_rate_limiting.py's existing
TestSensitiveAuthRateLimit: hammer the real route behind the real
Flask-Limiter instance (backed by the app fixture's memory:// storage,
same rationale as that file's module docstring) and assert the Nth
request gets a 429, not a 200/500.

This does NOT exercise track_visit's actual visitor_logs INSERT/UPSERT
logic -- that SQL uses `on conflict` and `now()`, both PostgreSQL-only
syntax, and the test DB here is SQLite (see this suite's other
Postgres-only skips, e.g. test_connections_flow.py). The rate limiter
decorator runs and can reject a request before the view body's DB call
ever executes, so testing 429 behavior specifically doesn't require a
Postgres-compatible visitor_logs table -- only testing the *success*
path's actual row-upsert behavior would.
"""

import pytest

pytestmark = pytest.mark.integration


class TestTrackVisitRateLimit:
    def test_track_visit_rate_limited_after_tier_limit(self, client):
        """
        RateLimitTier.BURST_OK = '60 per minute'. The 61st request within
        the window from the same key (IP, since this route is
        unauthenticated and keyed via ip_key()) must be rejected with 429
        rather than reaching the (possibly-500-ing, Postgres-only) view
        body.
        """
        statuses = []
        for _ in range(61):
            resp = client.post("/api/track-visit")
            statuses.append(resp.status_code)

        # None of the first 60 should be 429 -- whatever they return
        # (200 on a real Postgres backend, 500 here against SQLite,
        # since visitor_logs doesn't exist as an ORM table in this test
        # DB) is irrelevant to this test; only that the limiter doesn't
        # fire early.
        assert 429 not in statuses[:60]
        assert statuses[60] == 429

        body = client.post("/api/track-visit").get_json()
        assert body["status"] == "error"
        assert "Rate limit" in body["message"]
