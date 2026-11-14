"""
StudyHub - Posts: Bookmarks (routes only)

Thin HTTP layer. All logic lives in services/bookmark_service.py.
Routes -> services -> models (see ARCHITECTURE.md).
"""
import logging
from functools import wraps

from flask import Blueprint, request

from errors import AppError, ValidationError
from routes.student.helpers import token_required, success_response, error_response
from services import bookmark_service as bm
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key
from extensions import db

logger = logging.getLogger(__name__)
posts_bookmarks_bp = Blueprint("posts_bookmarks", __name__)

_UNSET = bm._UNSET


def _guard(failure_message):
    """Typed AppErrors propagate to the central handler; anything else -> logged 500."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except AppError:
                raise
            except Exception:
                db.session.rollback()
                logger.exception("bookmarks: %s", failure_message)
                return error_response(failure_message, 500)
        return wrapper
    return deco


def _body():
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    return data


def _opt_int(raw, name):
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be an integer")


# ───────────────────────── reads ─────────────────────────

@posts_bookmarks_bp.route("/posts/bookmarked", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
@_guard("Failed to load bookmarks")
def get_bookmarked_posts(current_user):
    """
    Feed-grade, cursor-paginated list of the viewer's bookmarks.

    Query params: cursor, limit (<=20, default 12), folder_id | folder (name, legacy),
                  type (post_type), tag, q (search), sort=recent|oldest.
    Response data: {bookmarks:[{bookmark_id, folder_id, folder, notes, tags,
                    bookmarked_at, post:{...same card payload as /posts/feed...}}],
                    next_cursor, has_more, total (first page only), folders (first page only)}
    """
    a = request.args
    folder_id = _opt_int(a.get("folder_id"), "folder_id")
    if folder_id is None and a.get("folder"):
        # legacy: filter by folder NAME
        f = next((x for x in bm.list_folders(current_user.id, ensure_default=False)
                  if x["name"] == a.get("folder")), None)
        if f is None:
            return success_response("Bookmarks retrieved", {
                "bookmarks": [], "next_cursor": None, "has_more": False,
                "total": 0, "folders": bm.list_folders(current_user.id, ensure_default=False)})
        folder_id = f["id"]

    page = bm.list_bookmarks_page(
        current_user.id,
        folder_id=folder_id,
        post_type=(a.get("type") or "").strip() or None,
        tag=a.get("tag"), q=a.get("q"),
        sort=a.get("sort", "recent"),
        cursor=a.get("cursor") or None,
        limit=a.get("limit", bm.PAGE_DEFAULT, type=int),
    )
    if not a.get("cursor"):                      # first page also carries the folder rail
        page["folders"] = bm.list_folders(current_user.id, ensure_default=False)
    return success_response("Bookmarks retrieved", page)


@posts_bookmarks_bp.route("/posts/bookmarks/status", methods=["POST"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
@_guard("Failed to check bookmark status")
def bookmark_status(current_user):
    """POST {post_ids:[..<=100]} -> {statuses:{"12":{bookmarked,folder_id}, ...}}"""
    return success_response("Status retrieved", {
        "statuses": bm.bookmark_status(current_user.id, _body().get("post_ids"))})


@posts_bookmarks_bp.route("/posts/bookmarks/stats", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
@_guard("Failed to load bookmark stats")
def bookmark_stats(current_user):
    return success_response("Stats retrieved", bm.get_stats(current_user.id))


# ───────────────────────── idempotent single-post API (new) ─────────────────────────

@posts_bookmarks_bp.route("/posts/<int:post_id>/bookmark", methods=["PUT"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
@_guard("Failed to bookmark post")
def put_bookmark(current_user, post_id):
    """Idempotent 'bookmark this'. Body (all optional): folder_id | folder_name, notes, tags."""
    d = _body()
    results, folder = bm.add_bookmarks(
        current_user.id, [post_id],
        folder_id=_opt_int(d.get("folder_id"), "folder_id"),
        folder_name=d.get("folder_name"), notes=d.get("notes"), tags=d.get("tags"))
    r = results[0]
    if not r["success"]:
        return error_response(r.get("error", "Post not found"), 404)
    return success_response("Bookmarked", {
        "post_id": post_id, "bookmarked": True, "already": r["already"],
        "bookmark_count": r["bookmark_count"], "folder": bm.serialize_folder(folder)})


@posts_bookmarks_bp.route("/posts/<int:post_id>/bookmark", methods=["DELETE"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
@_guard("Failed to remove bookmark")
def delete_bookmark(current_user, post_id):
    r = bm.remove_bookmarks(current_user.id, [post_id])[0]
    return success_response("Bookmark removed", {
        "post_id": post_id, "bookmarked": False, "bookmark_count": r["bookmark_count"]})


# ───────────────────────── legacy toggle endpoints (compat wrappers) ─────────────────────────

@posts_bookmarks_bp.route("/posts/bookmark/toggle", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
@_guard("Failed to update bookmarks")
def toggle_bookmarks(current_user):
    d = _body()
    results, folder = bm.toggle_bookmarks(
        current_user.id, d.get("post_ids"),
        folder_id=_opt_int(d.get("folder_id"), "folder_id"),
        folder_name=d.get("folder_name"), notes=d.get("notes"), tags=d.get("tags"))
    return success_response("Bookmarks updated", {
        "results": results, "folder": bm.serialize_folder(folder)})


@posts_bookmarks_bp.route("/posts/bulk/bookmark", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
@_guard("Failed to bookmark posts")
def bulk_bookmark(current_user):
    """DEPRECATED: use PUT /posts/<id>/bookmark or POST /posts/bookmark/toggle."""
    d = _body()
    results, folder = bm.add_bookmarks(
        current_user.id, d.get("post_ids"),
        folder_name=d.get("folder"), notes=d.get("notes"), tags=d.get("tags"))
    return success_response("Posts bookmarked", {
        "bookmark_details": [{"post_id": r["post_id"], "bookmarked": r["success"]} for r in results],
        "folder": bm.serialize_folder(folder)})


@posts_bookmarks_bp.route("/posts/<int:post_id>/bookmark", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
@_guard("Failed to toggle bookmark")
def bookmark_post(current_user, post_id):
    """Legacy single toggle (post-view.js). 201 when created, 200 when removed."""
    d = _body()
    results, _folder = bm.toggle_bookmarks(
        current_user.id, [post_id], folder_name=d.get("folder"),
        notes=d.get("notes"), tags=d.get("tags"))
    r = results[0]
    if not r["success"]:
        return error_response("Post not found", 404)
    msg = "Post bookmarked" if r["bookmarked"] else "Bookmark removed"
    resp = success_response(msg, {"post_id": post_id, "bookmarked": r["bookmarked"],
                                  "bookmark_count": r["bookmark_count"]})
    return (resp, 201) if r["bookmarked"] and not r.get("already") else resp


# ───────────────────────── edit / move / bulk (new) ─────────────────────────

@posts_bookmarks_bp.route("/posts/bookmarks/<int:bookmark_id>", methods=["PATCH"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
@_guard("Failed to update bookmark")
def patch_bookmark(current_user, bookmark_id):
    """Body: any of {notes, tags, folder_id}. Missing keys are left unchanged."""
    d = _body()
    b = bm.update_bookmark(
        current_user.id, bookmark_id,
        notes=d["notes"] if "notes" in d else _UNSET,
        tags=d["tags"] if "tags" in d else _UNSET,
        folder_id=_opt_int(d["folder_id"], "folder_id") if "folder_id" in d else _UNSET)
    return success_response("Bookmark updated", {
        "bookmark_id": b.id, "post_id": b.post_id, "folder_id": b.folder_id,
        "folder": b.folder, "notes": b.notes, "tags": b.tags or []})


@posts_bookmarks_bp.route("/posts/bookmarks/move", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
@_guard("Failed to move bookmarks")
def move_bookmarks(current_user):
    d = _body()
    return success_response("Bookmarks moved", bm.move_bookmarks(
        current_user.id, d.get("post_ids"), _opt_int(d.get("folder_id"), "folder_id")))


@posts_bookmarks_bp.route("/posts/bookmarks/bulk-remove", methods=["POST"])
@limiter.limit(RateLimitTier.BURST_OK, key_func=user_or_ip_key)
@token_required
@_guard("Failed to remove bookmarks")
def bulk_remove_bookmarks(current_user):
    results = bm.remove_bookmarks(current_user.id, _body().get("post_ids"))
    return success_response("Bookmarks removed", {
        "removed": sum(1 for r in results if r["removed"]), "results": results})


# ───────────────────────── folders (new) ─────────────────────────

@posts_bookmarks_bp.route("/posts/bookmarks/folders", methods=["GET"])
@limiter.limit(RateLimitTier.PUBLIC_READ, key_func=user_or_ip_key)
@token_required
@_guard("Failed to load folders")
def get_folders(current_user):
    return success_response("Folders retrieved", {"folders": bm.list_folders(current_user.id)})


@posts_bookmarks_bp.route("/posts/bookmarks/folders", methods=["POST"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
@_guard("Failed to create folder")
def create_folder(current_user):
    d = _body()
    folder = bm.create_folder(current_user.id, name=d.get("name"),
                              description=d.get("description"),
                              icon=d.get("icon"), color=d.get("color"))
    return success_response("Folder created", {"folder": folder}), 201


@posts_bookmarks_bp.route("/posts/bookmarks/folders/<int:folder_id>", methods=["PATCH"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
@_guard("Failed to update folder")
def patch_folder(current_user, folder_id):
    d = _body()
    folder = bm.update_folder(
        current_user.id, folder_id,
        **{k: d[k] for k in ("name", "description", "icon", "color") if k in d})
    return success_response("Folder updated", {"folder": folder})


@posts_bookmarks_bp.route("/posts/bookmarks/folders/<int:folder_id>", methods=["DELETE"])
@limiter.limit(RateLimitTier.WRITE_HEAVY, key_func=user_or_ip_key)
@token_required
@_guard("Failed to delete folder")
def remove_folder(current_user, folder_id):
    """?mode=move (default; contents go to 'Saved') | ?mode=delete (contents deleted)."""
    return success_response("Folder deleted", bm.delete_folder(
        current_user.id, folder_id, mode=request.args.get("mode", "move")))
