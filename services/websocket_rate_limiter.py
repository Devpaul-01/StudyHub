"""
Rate Limiter for WebSocket Events
Prevents spam and abuse with per-user rate limiting

HORIZONTAL SCALING NOTE (see 01-DESIGN-horizontal-scaling.md §6.5):
RateLimiter and TypingStatusManager below are UNCHANGED and remain
process-local in-memory structures. They are left exactly as they were —
per an explicit instruction, websocket_events.py (the only other consumer
of RateLimiter as a general-purpose limiter) is out of scope for this
refactor and must not be touched, so RateLimiter itself stays as-is to
avoid changing behavior out from under that file.

TypingStatusManager stays local by design, not by omission — see the
design doc §2/§6.1 for why typing-indicator dedup bookkeeping is
legitimately process-local state (it only suppresses a redundant re-emit
of an event the client already has; the actual broadcast already reaches
every instance once message_queue is wired in websocket_messages.py).

RedisFixedWindowLimiter (new, below) is what actually needed to move to
Redis: websocket_threads.py's per-user thread-message rate limit
(_thread_msg_rate_limiter) is a security control, and an in-memory bucket
means a user who reconnects to a different instance (or round-robins
across instances on every request) gets a fresh, unlinked limit each
time — the exact multi-instance correctness gap this refactor exists to
close. See design doc §6.5.
"""

from datetime import datetime, timezone
from typing import Dict, List
import logging
import threading
import time

from extensions import redis_client

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Thread-safe rate limiter for WebSocket events
    Uses sliding window algorithm
    """
    
    def __init__(self):
        self.limits: Dict[str, List[datetime]] = {}
        self.lock = threading.Lock()
    
    def check_rate_limit(
        self, 
        key: str, 
        limit: int, 
        window: int
    ) -> tuple[bool, int]:
        """
        Check if request is within rate limit
        
        Args:
            key: Unique identifier (e.g., f"user_{user_id}_messages")
            limit: Maximum number of requests allowed
            window: Time window in seconds
        
        Returns:
            (allowed: bool, remaining: int)
        """
        with self.lock:
            now = datetime.now(timezone.utc)
            
            # Initialize if first request
            if key not in self.limits:
                self.limits[key] = []
            
            # Remove timestamps outside window
            cutoff = now.timestamp() - window
            self.limits[key] = [
                ts for ts in self.limits[key]
                if ts.timestamp() > cutoff
            ]
            
            # Check if limit exceeded
            current_count = len(self.limits[key])
            remaining = max(0, limit - current_count)
            
            if current_count >= limit:
                return False, 0
            
            # Add current timestamp
            self.limits[key].append(now)
            return True, remaining - 1
    
    def cleanup_old_entries(self, max_age: int = 3600):
        """
        Remove entries older than max_age seconds
        Call this periodically to prevent memory growth
        
        Args:
            max_age: Maximum age in seconds (default 1 hour)
        """
        with self.lock:
            now = datetime.now(timezone.utc)
            cutoff = now.timestamp() - max_age
            
            # Remove old entries
            keys_to_delete = []
            for key, timestamps in self.limits.items():
                self.limits[key] = [
                    ts for ts in timestamps
                    if ts.timestamp() > cutoff
                ]
                
                # Mark empty entries for deletion
                if not self.limits[key]:
                    keys_to_delete.append(key)
            
            # Delete empty entries
            for key in keys_to_delete:
                del self.limits[key]
    
    def reset_user_limits(self, user_id: int):
        """
        Reset all rate limits for a specific user
        Useful when user disconnects
        """
        with self.lock:
            keys_to_delete = [
                key for key in self.limits.keys()
                if key.startswith(f"user_{user_id}_")
            ]
            
            for key in keys_to_delete:
                del self.limits[key]
    
    def get_stats(self) -> dict:
        """Get current rate limiter statistics"""
        with self.lock:
            return {
                'total_keys': len(self.limits),
                'total_timestamps': sum(len(v) for v in self.limits.values())
            }


class TypingStatusManager:
    """
    Manages typing indicators with automatic expiration
    Thread-safe implementation
    """
    
    def __init__(self, timeout: int = 10):
        self.typing_status: Dict[str, Dict[int, datetime]] = {}
        self.lock = threading.Lock()
        self.timeout = timeout  # seconds
    
    def set_typing(self, conversation_key: str, user_id: int):
        """Mark user as typing in conversation"""
        with self.lock:
            if conversation_key not in self.typing_status:
                self.typing_status[conversation_key] = {}
            
            self.typing_status[conversation_key][user_id] = datetime.now(timezone.utc)
    
    def remove_typing(self, conversation_key: str, user_id: int):
        """Remove typing indicator"""
        with self.lock:
            if conversation_key in self.typing_status:
                self.typing_status[conversation_key].pop(user_id, None)
                
                # Clean up empty conversations
                if not self.typing_status[conversation_key]:
                    del self.typing_status[conversation_key]
    
    def get_typing_users(self, conversation_key: str) -> List[int]:
        """Get list of currently typing users (excluding expired)"""
        with self.lock:
            if conversation_key not in self.typing_status:
                return []
            
            now = datetime.now(timezone.utc)
            valid_users = []
            
            for user_id, timestamp in list(self.typing_status[conversation_key].items()):
                age = (now - timestamp).total_seconds()
                
                if age <= self.timeout:
                    valid_users.append(user_id)
                else:
                    # Auto-remove expired
                    del self.typing_status[conversation_key][user_id]
            
            return valid_users
    
    def cleanup_expired(self):
        """Remove all expired typing indicators"""
        with self.lock:
            now = datetime.now(timezone.utc)
            conversations_to_delete = []
            
            for conv_key, users in list(self.typing_status.items()):
                users_to_delete = []
                
                for user_id, timestamp in users.items():
                    age = (now - timestamp).total_seconds()
                    if age > self.timeout:
                        users_to_delete.append(user_id)
                
                # Remove expired users
                for user_id in users_to_delete:
                    del self.typing_status[conv_key][user_id]
                
                # Mark empty conversations
                if not self.typing_status[conv_key]:
                    conversations_to_delete.append(conv_key)
            
            # Delete empty conversations
            for conv_key in conversations_to_delete:
                del self.typing_status[conv_key]
    
    def remove_user_from_all(self, user_id: int):
        """Remove user from all typing indicators (on disconnect)"""
        with self.lock:
            for conv_key in list(self.typing_status.keys()):
                self.typing_status[conv_key].pop(user_id, None)
                
                # Clean up empty conversations
                if not self.typing_status[conv_key]:
                    del self.typing_status[conv_key]


class RedisFixedWindowLimiter:
    """
    Distributed, cross-instance fixed-window rate limiter backed by Redis.

    Deliberately fixed-window (INCR + EXPIRE on a time-bucketed key), not a
    sliding-window Lua script, to match rate_limit_service.py's own
    established `strategy="fixed-window"` choice for the HTTP-layer
    limiter — one app, one boundary-behavior model for "rate limit",
    rather than two subtly different algorithms depending on which layer
    a given check happens to run in.

    Key shape: sh:1:ws:msgrate:{bucket_key}:{window_start}
    where window_start = int(time.time() // window_seconds) — every
    request within the same window_seconds-sized slice of wall-clock time
    increments the same key, which self-expires window_seconds after
    first write. bucket_key is caller-supplied (e.g. f"thread_msg_{user_id}")
    so this class stays reusable for any per-identity rate limit, not just
    thread messages.

    FAILS OPEN (matching rate_limit_service.py's explicit precedent: "a
    rate limiter that takes the whole app down when its backing store is
    down is worse than no rate limiter"): on any Redis error, check()
    returns (allowed=True, remaining=None) — the action is permitted
    rather than the request/event failing. This is the opposite of
    distributed_lock.py's deliberate fail-closed behavior; see that
    module's docstring for why scheduler locks need the opposite default.
    """

    def __init__(self, key_prefix: str = "sh:1:ws:msgrate"):
        self.key_prefix = key_prefix

    def check_rate_limit(self, key: str, limit: int, window: int) -> tuple[bool, int | None]:
        """
        Args mirror the in-memory RateLimiter.check_rate_limit signature
        above so call sites can swap between them with no other changes.

        Returns (allowed: bool, remaining: int | None). remaining is None
        on a Redis-error fail-open path, since an accurate remaining count
        isn't knowable in that case — callers that only branch on
        `allowed` (websocket_threads.py's _is_rate_limited does) are
        unaffected either way.
        """
        bucket = int(time.time() // window)
        redis_key = f"{self.key_prefix}:{key}:{bucket}"

        try:
            pipe = redis_client.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, window)
            count, _ = pipe.execute()

            allowed = count <= limit
            remaining = max(0, limit - count)

            if not allowed:
                logger.info(
                    "[WS_RATE_LIMITED] key=%s count=%s limit=%s window=%ss",
                    key, count, limit, window,
                )
            return allowed, remaining
        except Exception:
            logger.warning(
                "[WS_RATE_LIMIT_CHECK_FAILED] key=%s — failing OPEN (request allowed)",
                key, exc_info=True,
            )
            return True, None
