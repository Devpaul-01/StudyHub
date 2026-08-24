import os
import logging

from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
import redis

# ── NOTE: logger is replaced with print for debugging ──
# logger = logging.getLogger(__name__)

db = SQLAlchemy()
mail = Mail()

# ── Redis client — module-level singleton ──────────────────────────────────
# Follows the exact same pattern as db/mail above. Unlike db/mail, it does
# NOT need an .init_app(app) call: redis.Redis.from_url(...) doesn't open
# a connection at construction time (connections are opened lazily from an
# internal pool on first command), and it reads REDIS_URL directly from
# the environment rather than from Flask's app.config.
#
# See studyhub-redis-caching-implementation-plan.md §6.1.
#
# DEBUG: Added connection testing and detailed logging to diagnose
# TimeoutError issues with Upstash Redis (SSL/TLS).
# ──────────────────────────────────────────────────────────────────────────

def _create_redis_client():
    """Create Redis client with error handling and logging."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    print("redis url : ")
    print(redis_url)
    
    # Log URL with password redacted
    safe_url = redis_url
    if '@' in redis_url:
        # Redact password for logging
        import re
        safe_url = re.sub(r':([^@]+)@', ':***@', redis_url)
    
    print(f"[Redis] Initializing with URL: {safe_url}")
    
    # Determine if TLS is needed
    is_tls = redis_url.startswith("rediss://")
    print(f"[Redis] TLS enabled: {is_tls}")
    
    try:
        # Create Redis client with explicit settings.
        #
        # IMPORTANT: from_url() infers the connection class (TLS or not)
        # from the URL scheme itself (rediss:// vs redis://). Passing an
        # explicit `ssl=` kwarg alongside from_url() is invalid — it gets
        # forwarded straight into the connection class's __init__, and
        # the plain (non-TLS) Connection class has no `ssl` parameter,
        # which raises:
        #   TypeError: AbstractConnection.__init__() got an unexpected
        #   keyword argument 'ssl'
        # So: never pass `ssl=`. Only pass ssl_cert_reqs (and other
        # ssl_* kwargs) when the URL is actually rediss://, since those
        # are only accepted by the TLS connection class.
        #
        # NOTE on cert verification: providers like LayerBase send proper
        # TLS SNI using the real hostname from the URL, and redis-py's
        # from_url() sets that SNI automatically — so a normal signed
        # cert should verify fine with NO extra ssl_* kwargs at all.
        # Only disable verification (ssl_cert_reqs=None) if you actually
        # hit a certificate error, e.g. with a self-signed cert behind a
        # proxy that breaks hostname matching. Gate it behind an env var
        # so it's an explicit, visible opt-out rather than a silent default.
        extra_kwargs = {}
        if is_tls and os.environ.get("REDIS_SSL_INSECURE", "false").lower() == "true":
            print("[Redis] REDIS_SSL_INSECURE=true — disabling TLS cert verification")
            extra_kwargs["ssl_cert_reqs"] = None

        client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=10,      # Increased from 2s to 5s
            socket_timeout=10,              # Increased from 2s to 5s
            retry_on_timeout=False,
            **extra_kwargs,
        )
        
        # Test connection immediately
        print("[Redis] Testing connection with PING...")
        result = client.ping()
        print(f"[Redis] PING result: {result}")
        
        # Get server info (without exposing sensitive data)
        try:
            info = client.info()
            print(f"[Redis] Server version: {info.get('redis_version', 'unknown')}")
            print(f"[Redis] Connected clients: {info.get('connected_clients', 'unknown')}")
            print(f"[Redis] Used memory: {info.get('used_memory_human', 'unknown')}")
        except Exception as info_err:
            print(f"[Redis] Could not get server info: {info_err}")
        
        print("[Redis] ✅ Connection successful!")
        return client
        
    except redis.ConnectionError as e:
        print(f"[Redis] ❌ ConnectionError: {e}")
        print("[Redis] Please check:")
        print("  1. REDIS_URL is correct in .env")
        print("  2. Redis server is running")
        print("  3. Network/firewall allows access")
        if 'upstash' in redis_url.lower():
            print("  4. For Upstash, ensure you're using rediss:// (not redis://)")
        return None
        
    except redis.TimeoutError as e:
        print(f"[Redis] ❌ TimeoutError: {e}")
        print("[Redis] Connection timed out. This could be:")
        print("  1. Redis server is not reachable")
        print("  2. Firewall blocking the connection")
        print("  3. SSL/TLS handshake issue (check certificate)")
        return None
        
    except Exception as e:
        print(f"[Redis] ❌ Unexpected error: {e}")
        print(f"[Redis] Error type: {type(e).__name__}")
        return None


# Create the Redis client
redis_client = _create_redis_client()

# Log final status
if redis_client:
    print("[Redis] ✅ Redis client ready")
else:
    print("[Redis] ⚠️ Redis client is None — caching will be disabled")
    print("[Redis] All cache_service calls will treat Redis as a cache miss (fail-open behavior)")