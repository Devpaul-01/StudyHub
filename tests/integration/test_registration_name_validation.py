"""Names entering the system at registration are plain text."""
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("name", ['"><img src=x onerror=alert(1)>', "<script>alert(1)</script>", "a<b"])
def test_register_rejects_markup_in_the_name(client, monkeypatch, name):
    from models import User
    from services.job_queue import email_queue
    monkeypatch.setattr(email_queue, "enqueue", lambda *a, **k: type("J", (), {"id": "x"})())

    resp = client.post("/student/register",
                       json={"full_name": name, "email": "markup@example.com", "google_verified": False})

    assert resp.status_code == 400
    assert User.query.filter_by(email="markup@example.com").first() is None


@pytest.mark.parametrize("name", [12345, ["x"], {"a": 1}, "x" * 101])
def test_register_rejects_non_text_and_overlong_names(client, monkeypatch, name):
    from services.job_queue import email_queue
    monkeypatch.setattr(email_queue, "enqueue", lambda *a, **k: type("J", (), {"id": "x"})())

    resp = client.post("/student/register",
                       json={"full_name": name, "email": "bad@example.com", "google_verified": False})

    assert resp.status_code == 400


def test_register_normalises_whitespace_in_a_normal_name(client, monkeypatch):
    from models import User
    from services.job_queue import email_queue
    monkeypatch.setattr(email_queue, "enqueue", lambda *a, **k: type("J", (), {"id": "x"})())

    resp = client.post("/student/register",
                       json={"full_name": "  Ada   Lovelace ", "email": "ada@example.com", "google_verified": False})

    assert resp.status_code in (200, 201)
    assert User.query.filter_by(email="ada@example.com").first().name == "Ada Lovelace"
