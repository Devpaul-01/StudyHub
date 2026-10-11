"""
tests/integration/test_app_factory_ai_discovery_wiring.py

Regression coverage for a doc/code mismatch found during audit:
services/ai_provider_service.py's module docstring states "app.py's
create_app() must call provider_manager.warm_model_discovery() explicitly
once, after blueprint registration" -- and ARCHITECTURE.md §11.4 already
documented this as implemented ("It's now an explicit warm_model_discovery()
call made once from create_app(), after blueprint registration") -- but
app.py never actually contained that call. Model discovery therefore never
ran in any environment, and every provider silently stayed on its static,
hardcoded priority model list forever instead of being re-ranked against
each provider's live model catalogue.

These tests exist so that both halves of the fix stay true going forward:
the call is actually wired up in non-testing configs, AND it's correctly
skipped under TESTING (TestingConfig.TESTING = True, read via
app.config.from_object at the top of create_app()) so the integration
suite's own call to the real create_app() never spawns a background
thread making live HTTP calls to AI providers during a test run with no
network access -- which is exactly the problem the explicit-call design
was introduced to avoid in the first place (see that same module
docstring's "Import-time network call fix" paragraph).

Each test calls create_app() directly (not via the shared session-scoped
`app` fixture) so it can patch provider_manager.warm_model_discovery
before the call under test and avoid interfering with the shared fixture
used by the rest of this suite.
"""

from unittest.mock import patch

from config import TestingConfig, Config


def test_create_app_skips_model_discovery_under_testing_config():
    """TestingConfig.TESTING = True must suppress the call -- this is the
    behavior the rest of the integration suite silently depends on not
    spawning a live-network background thread on every test run."""
    with patch(
        "services.ai_provider_service.provider_manager.warm_model_discovery"
    ) as mock_warm:
        from app import create_app

        flask_app, _socketio = create_app(config_class=TestingConfig)

        assert flask_app.config["TESTING"] is True
        mock_warm.assert_not_called()


def test_create_app_calls_model_discovery_when_not_testing():
    """Outside TESTING, create_app() must call
    provider_manager.warm_model_discovery() exactly once -- the actual fix
    for the gap described in this file's module docstring. Uses the base
    Config class (TESTING = False) rather than a non-testing env/DB setup,
    so this only exercises the wiring itself, not a full non-test boot."""
    with patch(
        "services.ai_provider_service.provider_manager.warm_model_discovery"
    ) as mock_warm:
        from app import create_app

        flask_app, _socketio = create_app(config_class=Config)

        assert flask_app.config["TESTING"] is False
        mock_warm.assert_called_once()
