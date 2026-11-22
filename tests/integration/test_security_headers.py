"""CSP report-only header (audit F-01 defence in depth)."""
import pytest

pytestmark = pytest.mark.integration


def _get_home(client):
    resp = client.get("/")
    if resp.mimetype != "text/html" or resp.status_code != 200:
        pytest.skip("landing page is not a 200 HTML response in the test app")
    return resp


def test_html_response_has_report_only_csp_and_not_enforcing(client):
    resp = _get_home(client)

    assert "Content-Security-Policy" not in resp.headers
    policy = resp.headers["Content-Security-Policy-Report-Only"]
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


def test_json_response_has_no_csp(client, make_user, auth_headers):
    user = make_user()
    resp = client.get("/student/assignments", headers=auth_headers(user))
    assert resp.mimetype == "application/json"
    assert "Content-Security-Policy-Report-Only" not in resp.headers


def test_csp_can_be_disabled_via_config(app, client):
    app.config["CSP_REPORT_ONLY_POLICY"] = ""
    try:
        resp = _get_home(client)
        assert "Content-Security-Policy-Report-Only" not in resp.headers
    finally:
        app.config.pop("CSP_REPORT_ONLY_POLICY", None)
