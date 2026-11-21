class TestSecurityHeaders:
    def test_baseline_csp_is_set(self, client):
        resp = client.get("/")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "object-src 'none'" in csp and "base-uri 'self'" in csp
