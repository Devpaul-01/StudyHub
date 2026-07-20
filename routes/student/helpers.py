# routes/student/helpers.py
# Shared helper functions used across student routes

from flask import request, jsonify, current_app
from werkzeug.utils import secure_filename
from functools import wraps
import jwt                      # FIX: removed duplicate `import jwt` that appeared twice
import datetime
import os
import secrets

# FIX: removed `from flask_login import current_user` — was imported but never
#      used (token_required performs its own user lookup via the JWT payload).

from models import User, Connection
from extensions import db
from sqlalchemy import or_, and_

# File upload settings
ALLOWED_IMAGE_EXT    = {"png", "jpg", "jpeg"}
ALLOWED_DOCUMENT_EXT = {"pdf", "doc", "docx", "txt", "ppt", "pptx"}


def generate_tokens_for_user(user):
    """Generate JWT access and refresh tokens for a user."""
    secret = current_app.config["SECRET_KEY"]

    access_payload = {
        "user_id":  user.id,
        "username": user.username,
        "name":     user.name,
        "email":    user.email,
        "role":     user.role,
        "exp":      datetime.datetime.utcnow() + datetime.timedelta(minutes=30),
    }

    refresh_payload = {
        "user_id": user.id,
        "email":   user.email,
        "exp":     datetime.datetime.utcnow() + datetime.timedelta(days=7),
    }

    access_token  = jwt.encode(access_payload,  secret, algorithm="HS256")
    refresh_token = jwt.encode(refresh_payload, secret, algorithm="HS256")

    # PyJWT < 2.0 returns bytes; >= 2.0 returns str
    if isinstance(access_token,  bytes):
        access_token  = access_token.decode("utf-8")
    if isinstance(refresh_token, bytes):
        refresh_token = refresh_token.decode("utf-8")

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


def token_required(f):
    """JWT authentication decorator for student routes."""
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

            if user.role != "student":
                return jsonify({"status": "error", "message": "Access denied. Students only."}), 403

        except jwt.ExpiredSignatureError:
            return jsonify({"status": "error", "message": "Token expired. Please refresh your session."}), 401
        except jwt.InvalidTokenError:
            return jsonify({"status": "error", "message": "Invalid token."}), 401

        return f(user, *args, **kwargs)
    return decorated


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
# CONNECTION BLOCKING (C-3 fix)
#
# Both connections.py and messages.py used to implement their own, mutually
# inconsistent notion of "who blocked whom" on the same Connection table:
#   - connections.py::block_user swapped requester_id/receiver_id on an
#     existing row so "receiver_id" always meant "the blocker" — which
#     corrupted the original connection-request history.
#   - messages.py::is_blocked_check and messages.py::get_conversations each
#     read plain requester_id/receiver_id direction to guess the blocker,
#     which doesn't actually match the swap convention above.
#   - messages.py::block_user_messaging re-owned the row a third, different
#     way.
#
# These three functions are now the single source of truth for blocking,
# built on the explicit Connection.blocked_by_id column. requester_id and
# receiver_id are never mutated by blocking; they keep meaning exactly what
# they meant when the connection/request was first created.
# ============================================================================

def _find_connection_between(user_a_id, user_b_id, status=None):
    """Find the Connection row between two users, in either direction."""
    query = Connection.query.filter(
        or_(
            and_(Connection.requester_id == user_a_id, Connection.receiver_id == user_b_id),
            and_(Connection.requester_id == user_b_id, Connection.receiver_id == user_a_id),
        )
    )
    if status:
        query = query.filter(Connection.status == status)
    return query.first()


def is_user_blocked(user_a_id, user_b_id):
    """
    Return (blocked_by_a, blocked_by_b): whether user_a has blocked user_b,
    and whether user_b has blocked user_a. At most one of these can be True
    at a time under the current one-row-per-pair model.

    This is the single "is this pair blocked" check — use it everywhere
    instead of re-deriving blocking direction from requester_id/receiver_id.
    """
    connection = _find_connection_between(user_a_id, user_b_id, status="blocked")

    if not connection or not connection.blocked_by_id:
        return False, False

    return connection.blocked_by_id == user_a_id, connection.blocked_by_id == user_b_id


def block_connection(blocker_id, blocked_id):
    """
    Block `blocked_id` on behalf of `blocker_id`.

    Reuses the existing Connection row between the two users if one exists
    (whatever its prior status — pending, accepted, rejected), setting
    status="blocked" and blocked_by_id=blocker_id, WITHOUT touching
    requester_id/receiver_id. Creates a fresh row if no connection existed
    yet. Does not commit — the caller's existing try/except/commit block
    stays in charge of the transaction, same as before this fix.

    Returns the (session-pending) Connection object.
    """
    connection = _find_connection_between(blocker_id, blocked_id)

    now = datetime.datetime.utcnow()

    if connection:
        connection.status = "blocked"
        connection.blocked_by_id = blocker_id
        connection.responded_at = now
    else:
        connection = Connection(
            requester_id=blocker_id,
            receiver_id=blocked_id,
            status="blocked",
            blocked_by_id=blocker_id,
            requested_at=now,
            responded_at=now,
        )
        db.session.add(connection)

    return connection


def unblock_connection(unblocker_id, other_id, restore_to_accepted=False):
    """
    Remove a block between `unblocker_id` and `other_id`. Only the user who
    created the block (Connection.blocked_by_id) may remove it.

    restore_to_accepted:
        False (default) — delete the connection row entirely; the two users
            are no longer connected and would need a fresh connection
            request to reconnect. Matches the original
            connections.py /connections/unblock behaviour.
        True — keep the row and set status back to "accepted" instead of
            deleting it, so messaging/connection status is restored
            immediately with no new request needed. Matches the original
            messages.py /messages/unblock behaviour.

    Does not commit — same convention as block_connection() above.

    Returns (success: bool, error_message: str | None).
    """
    connection = _find_connection_between(unblocker_id, other_id, status="blocked")

    if not connection:
        return False, "User is not blocked"

    if connection.blocked_by_id != unblocker_id:
        return False, "Not authorized"

    if restore_to_accepted:
        connection.status = "accepted"
        connection.blocked_by_id = None
        connection.responded_at = datetime.datetime.utcnow()
    else:
        db.session.delete(connection)

    return True, None
