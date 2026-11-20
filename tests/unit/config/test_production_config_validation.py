"""ProductionConfig.validate() refuses unsafe settings at startup (audit AUD-12/13/19)."""
import pytest

import config


def _prod(**overrides):
    attrs = dict(
        RATE_LIMIT_STORAGE_URI="redis://redis:6379/0",
        RATE_LIMIT_ENABLED=True,
        PUBLIC_BASE_URL="https://studyhub.example",
        CORS_ALLOWED_ORIGINS=["https://studyhub.example"],
        SESSION_COOKIE_SECURE=True,
    )
    attrs.update(overrides)
    return type("P", (config.ProductionConfig,), attrs)


def test_safe_configuration_passes():
    _prod().validate()


@pytest.mark.parametrize("overrides,needle", [
    ({"RATE_LIMIT_STORAGE_URI": "memory://"}, "Redis"),
    ({"PUBLIC_BASE_URL": None}, "PUBLIC_BASE_URL"),
    ({"PUBLIC_BASE_URL": "http://studyhub.example"}, "PUBLIC_BASE_URL"),
    ({"CORS_ALLOWED_ORIGINS": ["*"]}, "CORS"),
    ({"SESSION_COOKIE_SECURE": False}, "SESSION_COOKIE_SECURE"),
])
def test_each_unsafe_setting_is_rejected(overrides, needle):
    with pytest.raises(config.ConfigurationError) as exc:
        _prod(**overrides).validate()
    assert needle in str(exc.value)


def test_all_problems_are_reported_together():
    bad = _prod(RATE_LIMIT_STORAGE_URI="memory://", PUBLIC_BASE_URL=None,
                CORS_ALLOWED_ORIGINS=["*"], SESSION_COOKIE_SECURE=False)
    with pytest.raises(config.ConfigurationError) as exc:
        bad.validate()
    assert str(exc.value).count("\n  - ") == 4


def test_memory_rate_limits_are_allowed_when_rate_limiting_is_disabled():
    _prod(RATE_LIMIT_STORAGE_URI="memory://", RATE_LIMIT_ENABLED=False).validate()


def test_create_app_refuses_to_start_with_an_unsafe_production_config():
    from app import create_app
    with pytest.raises(config.ConfigurationError):
        create_app(_prod(PUBLIC_BASE_URL=None))


def test_cors_is_closed_by_default_and_open_only_in_development():
    assert config.Config.CORS_ALLOWED_ORIGINS == [] or "*" not in config.Config.CORS_ALLOWED_ORIGINS
    assert config.DevelopmentConfig.CORS_ALLOWED_ORIGINS == ["*"]


def test_production_cookies_are_secure_by_default():
    assert config.ProductionConfig.SESSION_COOKIE_SECURE is True
