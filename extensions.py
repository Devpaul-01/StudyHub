import os

from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
import redis

db = SQLAlchemy()
mail = Mail()

# Redis client — follows the exact same module-level-singleton pattern as
# db/mail above. Unlike db/mail, it does NOT need an .init_app(app) call:
# redis.Redis.from_url(...) doesn't open a connection at construction time
# (connections are opened lazily from an internal pool on first command),
# and it reads REDIS_URL directly from the environment rather than from
# Flask's app.config, so this stays importable with zero Flask app context.
# See studyhub-redis-caching-implementation-plan.md §6.1.
redis_client = redis.Redis.from_url(
    os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    retry_on_timeout=False,
)