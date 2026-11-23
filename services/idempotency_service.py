"""
services/idempotency_service.py — at-most-once guard for client-retried creates.

A client sends a random key with a create request. The first request claims
the key (Redis SET NX); a duplicate that arrives while the first is still
running is told so, and a duplicate that arrives afterwards is handed the
first request's stored response instead of creating a second row.

Fails OPEN: if Redis is unavailable the request proceeds as a normal create.
A missing guard is a (rare) duplicate row; a guard that blocks writes when its
store is down would be an outage.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import extensions

logger = logging.getLogger(__name__)


def _redis():
    """Resolved at call time so a replaced/unavailable client is honoured."""
    return extensions.redis_client

KEY_PREFIX = "sh:1:idem:"
DEFAULT_TTL_SECONDS = 600
_PENDING = "pending"
_VALID_KEY = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")

NEW = "new"
IN_PROGRESS = "in_progress"
DONE = "done"


def normalize_key(raw) -> Optional[str]:
    """Return a safe client key or None when absent/invalid."""
    if isinstance(raw, str) and _VALID_KEY.match(raw):
        return raw
    return None


def _redis_key(scope: str, user_id: int, client_key: str) -> str:
    return f"{KEY_PREFIX}{scope}:{user_id}:{client_key}"


def begin(scope: str, user_id: int, client_key: str, ttl_seconds: int = DEFAULT_TTL_SECONDS):
    """
    Claim the key. Returns (state, stored_response):
      (NEW, None)          — caller should perform the work
      (IN_PROGRESS, None)  — an identical request is still running
      (DONE, dict)         — it already finished; return the stored response
    """
    key = _redis_key(scope, user_id, client_key)
    try:
        if _redis() is None:
            return NEW, None
        if _redis().set(key, _PENDING, nx=True, ex=ttl_seconds):
            return NEW, None
        current = _redis().get(key)
        if isinstance(current, bytes):
            current = current.decode()
        if current is None:
            # expired between SET and GET: treat as a fresh request
            return NEW, None
        if current == _PENDING:
            return IN_PROGRESS, None
        return DONE, json.loads(current)
    except Exception:
        logger.warning("[IDEMPOTENCY] begin failed; proceeding without guard", exc_info=True)
        return NEW, None


def finish(scope: str, user_id: int, client_key: str, response: dict,
           ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
    """Store the successful response so duplicates can replay it."""
    try:
        if _redis() is not None:
            _redis().set(_redis_key(scope, user_id, client_key), json.dumps(response), ex=ttl_seconds)
    except Exception:
        logger.warning("[IDEMPOTENCY] finish failed", exc_info=True)


def abandon(scope: str, user_id: int, client_key: str) -> None:
    """Release the claim after a failed attempt so the client can retry."""
    try:
        if _redis() is not None:
            _redis().delete(_redis_key(scope, user_id, client_key))
    except Exception:
        logger.warning("[IDEMPOTENCY] abandon failed", exc_info=True)
