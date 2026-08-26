# routes/student/helpers.py
# Shared helper functions used across student routes

from flask import request, jsonify, current_app, g
from werkzeug.utils import secure_filename
from functools import wraps
import jwt                      # FIX: removed duplicate `import jwt` that appeared twice
import datetime
import os
import secrets

# FIX: removed `from flask_login import current_user` — was imported but never
#      used (token_required performs its own user lookup via the JWT payload).

from models import User
from extensions import db
from errors import AuthorizationError

# File upload settings
ALLOWED_IMAGE_EXT    = {"png", "jpg", "jpeg"}
ALLOWED_DOCUMENT_EXT = {"pdf", "doc", "docx", "txt", "ppt", "pptx"}


def _build_access_token(user) -> str:
    """
    Auth-flow-audit fix (Finding #7): single source of truth for the
    access-token JWT shape, used by both generate_tokens_for_user() (fresh
    login) and auth.py's refresh_token() route (routine rotation) — these
    two call sites previously hand-built near-identical payload dicts
    independently, which had already silently drifted once (a stale
    50-minute expiry in one path vs 30 minutes in the other, fixed
    separately) and could drift again since nothing enforced they stay in
    sync. refresh_token() intentionally does NOT call
    generate_tokens_for_user() wholesale, since that also mints a brand
    new refresh-token family — a routine access-token refresh must keep
    rotating within the SAME family (see rotate_refresh_token), not start
    a new one every 30 minutes.
    """
    secret = current_app.config["SECRET_KEY"]

    access_payload = {
        "user_id":  user.id,
        "username": user.username,
        "name":     user.name,
        "email":    user.email,
        "role":     user.role,
        "exp":      datetime.datetime.utcnow() + datetime.timedelta(minutes=30),
    }

    access_token = jwt.encode(access_payload, secret, algorithm="HS256")

    # PyJWT < 2.0 returns bytes; >= 2.0 returns str
    if isinstance(access_token, bytes):
        access_token = access_token.decode("utf-8")

    return access_token


def generate_tokens_for_user(user):
    """
    Generate an access token (short-lived JWT, unchanged) and a refresh
    token (opaque, DB-backed, rotatable — Auth-flow-audit Finding #6) for
    a fresh login.

    Auth-flow-audit fix: the refresh token used to be a stateless JWT
    with a 7-day `exp` and nothing else — reissued unchanged by
    /refresh-token every time, with no way to revoke a specific session
    or detect a stolen token being replayed. It's now minted via
    auth_service.issue_refresh_token(), which persists a DB row (hash
    only, never the raw value) that /refresh-token can rotate-on-use and
    logout can revoke. See models.RefreshToken's docstring for the full
    design.

    Commits internally at the point the refresh-token row is written (see
    the inline comment below for why) — unlike most token-issuance
    helpers in this codebase, callers do NOT need to commit afterward for
    the tokens themselves to be valid, though callers should still commit
    any of their OWN unrelated pending changes before calling this if
    those changes need to be durable too.
    """
    from services import auth_service  # local import: avoids a circular
                                        # import (auth_service imports
                                        # models, which does not import
                                        # helpers, so this is safe either
                                        # direction, but kept local to
                                        # match this module's existing
                                        # style of not importing service
                                        # modules at top level)

    access_token = _build_access_token(user)

    refresh_token = auth_service.issue_refresh_token(user)

    # Auth-flow-audit fix (regression caught during Finding #6
    # implementation): issue_refresh_token() adds a new RefreshToken row
    # to the session but does not commit (matching every other
    # token-issuance function's convention of leaving the commit to the
    # caller). generate_tokens_for_user() previously did ZERO database
    # writes (pure jwt.encode() calls), so none of its 4 existing call
    # sites (login(), onboard(), complete_registration(),
    # google_callback()'s existing-approved-user branch) commit
    # afterward — each already commits its OWN changes earlier in the
    # request, then calls this function and hands the resulting tokens
    # straight to the client without a further commit. Without committing
    # here, the refresh-token row would only exist in the SQLAlchemy
    # session, not the database — leaving the client holding a raw
    # refresh-token value that /refresh-token could never actually
    # validate later. Committing here, at the point of the write, is
    # simplest and safest: it doesn't require auditing/changing 4 call
    # sites and can't be missed by a future 5th caller either.
    db.session.commit()

    return access_token, refresh_token


def decode_token(token):
    """Decode and verify a JWT token. Raises jwt exceptions on failure."""
    secret = current_app.config["SECRET_KEY"]
    return jwt.decode(token, secret, algorithms=["HS256"])


def verify_token(token):
    """
    Decode a single-purpose verification JWT (email verification /
    password reset, created by utils.generate_verification_token) and
    return the email it was issued for.

    This is the single source of truth for that token shape — it lives
    here (rather than duplicated in utils.py) so there is exactly one
    place that knows how these tokens are signed and validated, matching
    generate_tokens_for_user/decode_token above.

    Returns:
        str: the email address, on success.
        dict: {"error": "<message>"} on any failure (expired, malformed,
              or missing "email" claim) — callers check for this shape
              via `isinstance(result, dict) and "error" in result`.
    """
    if not token:
        return {"error": "Token is required"}

    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        return {"error": "This link has expired. Please request a new one."}
    except jwt.InvalidTokenError:
        return {"error": "This link is invalid."}

    email = payload.get("email")
    if not email:
        return {"error": "This link is invalid."}

    return email


def role_required(*allowed_roles: str):
    """
    Document 3 §6.1: parameterized replacement for the old hardcoded
    "if user.role != 'student': 403" check inside token_required.

    This is why badges.py::award_badge_endpoint /
    reputation.py::award_reputation_endpoint's inline
    "if current_user.role not in ('admin', 'system'): 403" checks used to
    be unreachable dead code — token_required already rejected every
    non-student account before the route body ever ran, so no account
    could ever satisfy both gates. role_required("admin", "system") (see
    admin_required below) fixes that by making the role gate itself
    configurable per-route, rather than baking "student" into the one and
    only decorator.

    token_required = role_required("student") below is kept as a plain
    alias specifically so every existing `@token_required` usage across
    ~250 routes keeps working completely UNCHANGED — this is the single
    most important compatibility decision in this section: the fix is in
    how the decorator is DEFINED, not in rewriting every call site.

    Also sets g.current_user_id right after decoding (zero extra cost —
    the payload is already decoded), so Document 4's rate-limit service
    can key per-user without needing to decode the token a second time.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            token = None

            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]

            if not token:
                token = request.cookies.get("access_token")

            if not token:
                return jsonify({
                    "status":  "error",
                    "message": "Authentication required. Please login.",
                }), 401

            try:
                payload = decode_token(token)
                user    = User.query.get(payload.get("user_id"))

                if not user:
                    return jsonify({"status": "error", "message": "User not found."}), 401

                if user.role not in allowed_roles:
                    raise AuthorizationError("Access denied for this role")

                g.current_user_id = user.id
                try:
                    import sentry_sdk
                    sentry_sdk.set_user({"id": user.id})
                except Exception:
                    pass

            except jwt.ExpiredSignatureError:
                return jsonify({"status": "error", "message": "Token expired. Please refresh your session."}), 401
            except jwt.InvalidTokenError:
                return jsonify({"status": "error", "message": "Invalid token."}), 401

            return f(user, *args, **kwargs)
        return decorated
    return decorator


# Preserves every existing `@token_required` call site unchanged (Document 3 §6.1).
token_required = role_required("student")

# New in this phase: for the one surviving admin-gated endpoint
# (POST /leaderboard/snapshot, per Document 1 §6.3) and any future
# admin/system-only route. "system" is kept alongside "admin" since that
# was the original (dead) inline check's role set in badges.py/reputation.py.
admin_required = role_required("admin", "system")


# ============================================================================
# AUTH COOKIES  (Document 3 §1 — H-1 cookie redesign, §2 — CSRF cookie)
#
# Single place that sets/clears the three auth-related cookies, so every
# route that logs a user in (login, Google OAuth callback, onboarding,
# refresh-token) issues them identically instead of hand-rolling
# `response.set_cookie(...)` three times with slightly different flags at
# each call site (which is what let access_token's httponly=False survive
# unnoticed across several routes before this phase).
#
# ACCESS_TOKEN_HTTPONLY (config.py, defaults False) gates the actual
# behavior change: while False, this behaves exactly like the pre-Phase-4
# code (httponly=False on access_token, no csrf_token cookie) — while
# True, access_token becomes httponly and csrf_token is issued alongside
# it. This is a runtime config flag rather than a code branch removed
# after rollout specifically so the switch is instant and reversible
# (Document 5 Phase 4's rollback plan), not a redeploy.
# ============================================================================

def set_auth_cookies(response, access_token, refresh_token=None, *, secure=None):
    """
    Set access_token (+ optionally refresh_token) on `response`, plus the
    csrf_token cookie when ACCESS_TOKEN_HTTPONLY is enabled.

    `secure` defaults to the app's SESSION_COOKIE_SECURE setting if not
    explicitly passed, matching the existing pattern used across auth.py.
    """
    if secure is None:
        secure = current_app.config.get("SESSION_COOKIE_SECURE", False)

    access_httponly = current_app.config.get("ACCESS_TOKEN_HTTPONLY", False)

    response.set_cookie(
        "access_token", access_token,
        httponly=access_httponly, secure=secure, samesite="Lax", max_age=30 * 60,
    )

    if refresh_token is not None:
        response.set_cookie(
            "refresh_token", refresh_token,
            httponly=True, secure=secure, samesite="Lax", max_age=7 * 24 * 60 * 60,
        )

    if access_httponly:
        # Document 3 §2.1: csrf_token is deliberately NOT httponly (must be
        # JS-readable for the double-submit pattern) and matches
        # access_token's lifetime, reissued every time access_token is.
        csrf_token = secrets.token_urlsafe(32)
        response.set_cookie(
            "csrf_token", csrf_token,
            httponly=False, secure=secure, samesite="Lax", max_age=30 * 60,
        )

    return response


def clear_auth_cookies(response):
    """Clear all three auth cookies (used by /auth/logout)."""
    response.set_cookie("access_token", "", max_age=0)
    response.set_cookie("refresh_token", "", max_age=0)
    response.set_cookie("csrf_token", "", max_age=0)
    return response


def save_file(file, folder, allowed_extensions):
    """Securely save an uploaded file with a unique name."""
    if not file or not file.filename:
        return None

    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in allowed_extensions:
        raise ValueError(f"File type .{ext} not allowed")

    upload_folder = os.path.join(
        current_app.config.get("UPLOAD_FOLDER", "static/uploads"), folder
    )
    os.makedirs(upload_folder, exist_ok=True)

    unique_id      = secrets.token_hex(8)
    safe_filename  = secure_filename(file.filename)
    final_name     = f"{unique_id}_{safe_filename}"
    file_path      = os.path.join(upload_folder, final_name)
    file.save(file_path)

    return f"uploads/{folder}/{final_name}"


def is_ajax_request():
    """Check if the request was made via AJAX."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def success_response(message, data=None, redirect_url=None):
    """Standard success JSON response."""
    response = {"status": "success", "message": message}
    if data:
        response["data"] = data
    if redirect_url:
        response["redirect"] = redirect_url
    return jsonify(response)


def error_response(message, status_code=400, errors=None):
    """Standard error JSON response."""
    response = {"status": "error", "message": message}
    if errors:
        response["errors"] = errors
    return jsonify(response), status_code


# ============================================================================
# REACTION UTILITIES
# ============================================================================

REACTION_EMOJI_MAP = {
    "love":        "❤️",
    "fire":        "🔥",
    "laugh":       "😂",
    "wow":         "😮",
    "sad":         "😢",
    "angry":       "😡",
    "thumbs_up":   "👍",
    "thumbs_down": "👎",
    "clap":        "👏",
    "pray":        "🙏",
    "celebrate":   "🎉",
    "think":       "🤔",
}


def get_reaction_emoji(reaction_type):
    """
    Return the emoji for a reaction type string, or None if unrecognised.

    Example:
        get_reaction_emoji("love")  →  "❤️"
        get_reaction_emoji("fire")  →  "🔥"
    """
    return REACTION_EMOJI_MAP.get(reaction_type)


def get_reaction_summary(message_id):
    """
    Return a compact emoji-count string for all reactions on a message.

    Example output: "❤️ 3  🔥 1  👍 2"
    Returns an empty string when the message has no reactions.
    """
    from models import MessageReaction

    reactions = MessageReaction.query.filter_by(message_id=message_id).all()
    if not reactions:
        return ""

    counts = {}
    for r in reactions:
        counts[r.reaction_type] = counts.get(r.reaction_type, 0) + 1

    parts = []
    for reaction_type, count in counts.items():
        emoji = REACTION_EMOJI_MAP.get(reaction_type)
        if emoji:
            parts.append(f"{emoji} {count}" if count > 1 else emoji)

    return "  ".join(parts)


# ============================================================================
# CONNECTION BLOCKING (C-3 fix) — Document 2 §3.4 shim
#
# is_user_blocked / block_connection / unblock_connection used to be defined
# directly in this file. They have since moved VERBATIM to
# services/connection_service.py (Document 2 §3.4) — that is now the single
# source of truth for "who blocked whom" on the Connection table, since
# helpers.py is being repositioned as an HTTP-layer-only file (Document 2
# §2.1) and blocking is business logic, not HTTP plumbing.
#
# PHASE-1 CORRECTNESS FIX: this file previously still carried a full,
# independently-maintained COPY of these four functions alongside the new
# services/connection_service.py implementation — i.e. the "shim" described
# in Document 2 §3.4 was never actually wired up, so the two copies could
# silently drift apart. This import is that shim, finally in place: every
# existing call site that does
#   from routes.student.helpers import block_connection, unblock_connection
# (connections.py, messages.py) keeps working completely unchanged, but
# there is now exactly one implementation.
# ============================================================================

from services.connection_service import (  # noqa: E402  (kept below the rest of this file's own defs, matching original layout)
    is_user_blocked,
    block_connection,
    unblock_connection,
)
