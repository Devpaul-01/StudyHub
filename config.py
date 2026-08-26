"""
StudyHub Configuration - environment-tiered config classes
Phase 0 foundation piece of the backend refactor.

Replaces the Config class that used to live inline in app.py. Every field
below is copied verbatim from that inline class (no behavior change) except
where a comment explains a deliberate addition bundled into this move.

Selection is via FLASK_ENV / APP_ENV (checked in that order), defaulting to
"production" - matching the previous inline Config class's own comment
("FLASK_ENV = ... default 'production'"), so an unset env var in an existing
deployment continues to behave exactly as it does today.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the same directory as this file
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)



class Config:
    """Shared defaults - every environment inherits from this."""

    # Flask Core
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable is not set!")

    FLASK_ENV = os.environ.get('FLASK_ENV', 'production')
    DEBUG = False
    TESTING = False
    LEARNORA_BOT_USER_ID = os.environ.get("LEARNORA_BOT_USER_ID")

    # Database
    DATABASE_URL = os.environ.get('DATABASE_NEW_URL')
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set!")

    # Fix for Heroku/Railway postgres:// vs postgresql://
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Flask-Mail Configuration (Gmail with App Password)
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'False').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')

    MAIL_MAX_EMAILS = 50
    MAIL_TIMEOUT = 5
    MAIL_DEBUG = False
    MAIL_SUPPRESS_SEND = False
    MAIL_ASCII_ATTACHMENTS = False

    # Application Settings
    CURRENT_URL = os.environ.get('CURRENT_URL', 'http://127.0.0.1:5001/')
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'static/upload')

    # Mailchimp (Optional)
    MAILCHIMP_API_KEY = os.environ.get('MAILCHIMP_API_KEY')
    MAILCHIMP_LIST_ID = os.environ.get('MAILCHIMP_LIST_ID')

    # Security Settings
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload
    JSON_SORT_KEYS = False
    JSONIFY_PRETTYPRINT_REGULAR = False

    # Session Security
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour

    LEARNORA_BOT_USER_ID = int(os.environ.get('LEARNORA_BOT_USER_ID', 0))

    SCHEDULER_ENABLED = os.environ.get('SCHEDULER_ENABLED', 'true').lower() == 'true'

    # NEW in this phase - infrastructure the rest of the refactor plan
    # (rate limiting, Redis, CSRF/cookie work) will read from. Every one
    # of these degrades gracefully when unset, so introducing them here
    # now, ahead of the code that will actually use them, is zero-risk:
    # nothing reads these yet.
    REDIS_URL = os.environ.get("REDIS_URL")  # None if unset
    RATE_LIMIT_STORAGE_URI = os.environ.get("RATE_LIMIT_STORAGE_URI", "memory://")
    RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() == "true"
    CORS_ALLOWED_ORIGINS = [
        origin.strip() for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "*").split(",")
    ]
    # Feature flag for the H-1 cookie redesign (Security & Authorization
    # phase) - defaults False so the flip is a config change, not a code
    # deploy, and is instantly reversible. Not used by any code yet.
    ACCESS_TOKEN_HTTPONLY = os.environ.get("ACCESS_TOKEN_HTTPONLY", "true").lower() == "true"

    # Sentry error tracking — None/unset means disabled (see
    # services/error_tracking.py). No behavior change to any existing
    # config tier; DevelopmentConfig/TestingConfig/ProductionConfig all
    # inherit this unchanged unless you want per-tier sampling later.
    SENTRY_DSN = os.environ.get("SENTRY_DSN")


class DevelopmentConfig(Config):
    DEBUG = True
    CORS_ALLOWED_ORIGINS = ["*"]  # explicit override - only safe in dev


class TestingConfig(Config):
    TESTING = True
    RATE_LIMIT_STORAGE_URI = "memory://"


class ProductionConfig(Config):
    DEBUG = False
    # Reuse REDIS_URL for rate-limit storage when it's set, so provisioning
    # one Redis instance is enough (Document 4 §2.5's "one Redis, logically
    # namespaced by key prefix" — Flask-Limiter prefixes its own keys).
    # Falls back to memory:// (single-process only) if REDIS_URL is unset,
    # same as every other Redis-optional piece in this codebase.
    RATE_LIMIT_STORAGE_URI = os.environ.get("RATE_LIMIT_STORAGE_URI") or os.environ.get("REDIS_URL") or "memory://"


_ENV_TO_CONFIG = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config():
    """
    Resolve which Config subclass to use from FLASK_ENV (checked first,
    matching Config.FLASK_ENV's own precedent) or APP_ENV, defaulting to
    "production" - so an existing deployment with neither var set keeps
    getting exactly the production-safe defaults it gets today.
    """
    env = os.environ.get("FLASK_ENV") or os.environ.get("APP_ENV") or "production"
    return _ENV_TO_CONFIG.get(env.lower(), ProductionConfig)
