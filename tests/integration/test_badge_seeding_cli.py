"""`flask seed-badges` and the startup seeding gate (audit F-04)."""

import pytest

from models import Badge
from services.badge_service import BADGE_DEFINITIONS

pytestmark = pytest.mark.integration


def test_startup_does_not_seed_under_testing(app):
    # (runs first: the app fixture's database persists across this module)
    # TestingConfig sets TESTING=True, so create_app() must not have seeded.
    assert app.config["TESTING"] is True
    assert Badge.query.count() == 0


def test_seed_badges_cli_creates_all_badges_idempotently(app):
    runner = app.test_cli_runner()
    first = runner.invoke(args=["seed-badges"])
    assert first.exit_code == 0, first.output
    assert f"'created': {len(BADGE_DEFINITIONS)}" in first.output
    assert Badge.query.count() == len(BADGE_DEFINITIONS)

    second = runner.invoke(args=["seed-badges"])
    assert second.exit_code == 0
    assert "'created': 0" in second.output
    assert Badge.query.count() == len(BADGE_DEFINITIONS)
