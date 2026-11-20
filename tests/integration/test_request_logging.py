"""Request logs must not contain tokens or email addresses (audit AUD-14)."""
import logging

import pytest

pytestmark = pytest.mark.integration


def test_post_log_uses_route_template_not_the_concrete_path(client, db_session, caplog):
    secret_token = "SECRET-TOKEN-VALUE-123"
    with caplog.at_level(logging.INFO):
        client.post(f"/student/verify-email/{secret_token}")
        client.post("/student/onboard/person@example.com", json={})
    text = caplog.text
    assert secret_token not in text
    assert "person@example.com" not in text
    assert "/student/verify-email/<token>" in text
    assert "/student/onboard/<email>" in text
