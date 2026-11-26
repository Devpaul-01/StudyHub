"""Request logs must not contain tokens or email addresses (audit AUD-14)."""
import logging

import pytest

pytestmark = pytest.mark.integration

class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_post_log_uses_route_template_not_the_concrete_path(app, client, db_session):
    # app.logger has propagate=False (create_app attaches its own handlers),
    # so pytest's caplog on the root logger never sees its records: attach a
    # capturing handler to the app logger itself.
    handler = _Capture()
    app.logger.addHandler(handler)
    secret_token = "SECRET-TOKEN-VALUE-123"
    try:
        client.post(f"/student/verify-email/{secret_token}")
        client.post("/student/onboard/person@example.com", json={})
    finally:
        app.logger.removeHandler(handler)
    text = "\n".join(r.getMessage() for r in handler.records)
    assert secret_token not in text
    assert "person@example.com" not in text
    assert "/student/verify-email/<token>" in text
    assert "/student/onboard/<email>" in text
