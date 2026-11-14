"""
services/bookmark_service.py

All bookmark business logic. Single source of truth for:
  * Bookmark rows (folder_id + denormalised folder-name string kept in sync)
  * Post.bookmark_count and BookmarkFolder.bookmark_count (atomic SQL updates)
  * Folder CRUD
  * The feed-grade paginated list (hydrated via post_hydration_service)

No Flask imports (layering rule). Raises errors.AppError subclasses.
"""
import base64
import datetime
import logging
from collections import defaultdict

from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError

from errors import ConflictError, NotFoundError, ValidationError
from extensions import db
from models import Bookmark, BookmarkFolder, Post
from services.json_filters import json_array_contains
from services.post_hydration_service import hydrate_post_cards

logger = logging.getLogger(__name__)

DEFAULT_FOLDER = "Saved"
MAX_BATCH = 50          # post ids per write request
MAX_STATUS_BATCH = 100  # post ids per status lookup
MAX_NOTES = 1000
MAX_TAGS = 10
MAX_TAG_LEN = 30
MAX_FOLDERS_PER_USER = 50
MAX_FOLDER_NAME = 100
PAGE_DEFAULT = 12
PAGE_MAX = 20
VALID_POST_TYPES = {"question", "discussion", "announcement", "resource", "problem"}
_UNSET = object()


# ───────────────────────── validation helpers ─────────────────────────

def clean_post_ids(raw, *, cap=MAX_BATCH):
    """list[int-like] -> de-duplicated list[int] (order preserved)."""
    if not isinstance(raw, list) or not raw:
        raise ValidationError("post_ids must be a non-empty list")
    if len(raw) > cap:
        raise ValidationError(f"Maximum {cap} posts per request")
    out, seen = [], set()
    for v in raw:
        if isinstance(v, bool):
            raise ValidationError("post_ids must contain integers")
        try:
            i = int(v)
        except (TypeError, ValueError):
            raise ValidationError("post_ids must contain integers")
        if i > 0 and i not in seen:
            seen.add(i)
            out.append(i)
    if not out:
        raise ValidationError("post_ids must contain valid ids")
    return out


def clean_notes(notes):
    if notes is None:
        return None
    if not isinstance(notes, str):
        raise ValidationError("notes must be a string")
    notes = notes.strip()
    if len(notes) > MAX_NOTES:
        raise ValidationError(f"notes must be at most {MAX_NOTES} characters")
    return notes or None


def clean_tags(tags):
    if tags is None:
        return []
    if not isinstance(tags, list):
        raise ValidationError("tags must be a list")
    out, seen = [], set()
    for t in tags:
        if not isinstance(t, str):
            continue
        t = t.strip().lstrip("#")[:MAX_TAG_LEN]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
        if len(out) >= MAX_TAGS:
            break
    return out


def clean_folder_name(name):
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("Folder name is required")
    name = " ".join(name.split())
    if len(name) > MAX_FOLDER_NAME:
        raise ValidationError(f"Folder name must be at most {MAX_FOLDER_NAME} characters")
    return name


def _commit():
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


# ───────────────────────── atomic counters ─────────────────────────

def _bump_posts(post_ids, delta):
    """Post.bookmark_count += delta, clamped at 0, one UPDATE."""
    if not post_ids:
        return
    current = func.coalesce(Post.bookmark_count, 0)
    new_value = current + 1 if delta > 0 else case((current > 0, current - 1), else_=0)
    Post.query.filter(Post.id.in_(list(post_ids))).update(
        {Post.bookmark_count: new_value}, synchronize_session=False
    )


def _bump_folders(delta_by_folder):
    """{folder_id: signed_delta} -> one UPDATE per distinct folder, clamped at 0."""
    for folder_id, delta in delta_by_folder.items():
        if not delta or folder_id is None:
            continue
        current = func.coalesce(BookmarkFolder.bookmark_count, 0)
        new_value = case((current + delta > 0, current + delta), else_=0)
        BookmarkFolder.query.filter(BookmarkFolder.id == folder_id).update(
            {BookmarkFolder.bookmark_count: new_value}, synchronize_session=False
        )


def _post_counts(post_ids):
    if not post_ids:
        return {}
    rows = db.session.query(Post.id, Post.bookmark_count).filter(Post.id.in_(list(post_ids))).all()
    return {pid: int(c or 0) for pid, c in rows}


# ───────────────────────── folders ─────────────────────────

def serialize_folder(f):
    return {
        "id": f.id,
        "name": f.name,
        "description": f.description,
        "icon": f.icon or "📁",
        "color": f.color or "#6B7280",
        "is_default": bool(f.is_default),
        "position": f.position or 0,
        "count": f.bookmark_count or 0,
    }


def get_default_folder(user_id):
    """Return the user's 'Saved' folder, creating it race-safely."""
    folder = BookmarkFolder.query.filter_by(user_id=user_id, name=DEFAULT_FOLDER).first()
    if folder:
        if not folder.is_default:
            folder.is_default = True
        return folder
    try:
        with db.session.begin_nested():
            folder = BookmarkFolder(user_id=user_id, name=DEFAULT_FOLDER, is_default=True,
                                    position=0, bookmark_count=0)
            db.session.add(folder)
    except IntegrityError:           # concurrent request created it first
        folder = BookmarkFolder.query.filter_by(user_id=user_id, name=DEFAULT_FOLDER).first()
    return folder


def resolve_folder(user_id, *, folder_id=None, folder_name=None, create=True):
    """Folder the caller means: by id (must be theirs), by name, else default."""
    if folder_id is not None:
        try:
            folder_id = int(folder_id)
        except (TypeError, ValueError):
            raise ValidationError("folder_id must be an integer")
        folder = BookmarkFolder.query.filter_by(id=folder_id, user_id=user_id).first()
        if not folder:
            raise NotFoundError("Folder not found")
        return folder
    if folder_name:
        name = clean_folder_name(folder_name)
        if name == DEFAULT_FOLDER:
            return get_default_folder(user_id)
        folder = BookmarkFolder.query.filter_by(user_id=user_id, name=name).first()
        if folder or not create:
            if not folder:
                raise NotFoundError("Folder not found")
            return folder
        return _create_folder_row(user_id, name)
    return get_default_folder(user_id)


def _create_folder_row(user_id, name, *, description=None, icon=None, color=None):
    count = BookmarkFolder.query.filter_by(user_id=user_id).count()
    if count >= MAX_FOLDERS_PER_USER:
        raise ValidationError(f"You can have at most {MAX_FOLDERS_PER_USER} folders")
    try:
        with db.session.begin_nested():
            folder = BookmarkFolder(
                user_id=user_id, name=name, description=(description or None),
                icon=icon or "📁", color=color or "#6B7280",
                position=count, is_default=False, bookmark_count=0,
            )
            db.session.add(folder)
    except IntegrityError:
        folder = BookmarkFolder.query.filter_by(user_id=user_id, name=name).first()
        if folder is None:
            raise
    return folder


def list_folders(user_id, *, ensure_default=True):
    """ensure_default=False keeps GET /posts/bookmarked free of write side effects."""
    if ensure_default:
        get_default_folder(user_id)
        _commit()
    folders = (BookmarkFolder.query.filter_by(user_id=user_id)
               .order_by(BookmarkFolder.is_default.desc(), BookmarkFolder.position.asc(),
                         BookmarkFolder.name.asc()).all())
    return [serialize_folder(f) for f in folders]


def create_folder(user_id, *, name, description=None, icon=None, color=None):
    name = clean_folder_name(name)
    if name.lower() == DEFAULT_FOLDER.lower():
        raise ConflictError("That folder already exists")
    if BookmarkFolder.query.filter(
        BookmarkFolder.user_id == user_id, func.lower(BookmarkFolder.name) == name.lower()
    ).first():
        raise ConflictError("You already have a folder with that name")
    try:
        folder = _create_folder_row(user_id, name,
                                    description=(description or "")[:300],
                                    icon=(icon or "")[:50] or None,
                                    color=(color or "")[:20] or None)
        _commit()
    except Exception:
        db.session.rollback()
        raise
    return serialize_folder(folder)


def update_folder(user_id, folder_id, *, name=_UNSET, description=_UNSET, icon=_UNSET, color=_UNSET):
    folder = BookmarkFolder.query.filter_by(id=folder_id, user_id=user_id).first()
    if not folder:
        raise NotFoundError("Folder not found")
    try:
        if name is not _UNSET:
            if folder.is_default:
                raise ValidationError("The default folder can't be renamed")
            new_name = clean_folder_name(name)
            if new_name != folder.name:
                clash = BookmarkFolder.query.filter(
                    BookmarkFolder.user_id == user_id,
                    func.lower(BookmarkFolder.name) == new_name.lower(),
                    BookmarkFolder.id != folder.id).first()
                if clash:
                    raise ConflictError("You already have a folder with that name")
                folder.name = new_name
                # keep the denormalised string in sync
                Bookmark.query.filter_by(folder_id=folder.id).update(
                    {Bookmark.folder: new_name}, synchronize_session=False)
        if description is not _UNSET:
            folder.description = (description or "")[:300] or None
        if icon is not _UNSET:
            folder.icon = (icon or "📁")[:50]
        if color is not _UNSET:
            folder.color = (color or "#6B7280")[:20]
        _commit()
    except Exception:
        db.session.rollback()
        raise
    return serialize_folder(folder)


def delete_folder(user_id, folder_id, *, mode="move"):
    """
    mode='move'   -> bookmarks are moved into the default folder (safe default)
    mode='delete' -> bookmarks are deleted too (Post.bookmark_count is decremented)
    """
    if mode not in ("move", "delete"):
        raise ValidationError("mode must be 'move' or 'delete'")
    folder = BookmarkFolder.query.filter_by(id=folder_id, user_id=user_id).first()
    if not folder:
        raise NotFoundError("Folder not found")
    if folder.is_default:
        raise ValidationError("The default folder can't be deleted")
    try:
        affected = 0
        if mode == "move":
            default = get_default_folder(user_id)
            affected = Bookmark.query.filter_by(folder_id=folder.id, student_id=user_id).update(
                {Bookmark.folder_id: default.id, Bookmark.folder: default.name},
                synchronize_session=False)
            _bump_folders({default.id: affected})
        else:
            post_ids = [pid for (pid,) in db.session.query(Bookmark.post_id)
                        .filter_by(folder_id=folder.id, student_id=user_id).all()]
            _bump_posts(post_ids, -1)
            affected = Bookmark.query.filter_by(folder_id=folder.id, student_id=user_id).delete(
                synchronize_session=False)
        BookmarkFolder.query.filter_by(id=folder.id).delete(synchronize_session=False)
        _commit()
    except Exception:
        db.session.rollback()
        raise
    return {"deleted_folder_id": folder_id, "mode": mode, "bookmarks_affected": affected}


# ───────────────────────── bookmark writes ─────────────────────────

def add_bookmarks(user_id, post_ids, *, folder_id=None, folder_name=None, notes=None, tags=None):
    """
    Idempotent: bookmarking an already-bookmarked post is a no-op success
    (`already=True`). Never raises IntegrityError to the caller.
    """
    post_ids = clean_post_ids(post_ids)
    notes, tags = clean_notes(notes), clean_tags(tags)
    try:
        folder = resolve_folder(user_id, folder_id=folder_id, folder_name=folder_name)
        valid_ids = {pid for (pid,) in db.session.query(Post.id).filter(Post.id.in_(post_ids)).all()}
        existing = {b.post_id for b in Bookmark.query.filter(
            Bookmark.student_id == user_id, Bookmark.post_id.in_(post_ids)).all()}

        results, created = [], []
        for pid in post_ids:
            if pid not in valid_ids:
                results.append({"post_id": pid, "success": False, "bookmarked": False,
                                "error": "Post not found"})
            elif pid in existing:
                results.append({"post_id": pid, "success": True, "bookmarked": True, "already": True})
            else:
                try:
                    with db.session.begin_nested():     # savepoint: a race only loses this row
                        db.session.add(Bookmark(
                            post_id=pid, student_id=user_id, folder_id=folder.id,
                            folder=folder.name, notes=notes, tags=list(tags),
                            bookmarked_at=datetime.datetime.utcnow()))
                    created.append(pid)
                    results.append({"post_id": pid, "success": True, "bookmarked": True, "already": False})
                except IntegrityError:
                    results.append({"post_id": pid, "success": True, "bookmarked": True, "already": True})

        _bump_posts(created, +1)
        _bump_folders({folder.id: len(created)})
        _commit()
    except Exception:
        db.session.rollback()
        raise
    counts = _post_counts(post_ids)
    for r in results:
        r["bookmark_count"] = counts.get(r["post_id"], 0)
    return results, folder


def remove_bookmarks(user_id, post_ids):
    """Idempotent: removing a non-bookmarked post is a no-op success."""
    post_ids = clean_post_ids(post_ids)
    try:
        rows = Bookmark.query.filter(
            Bookmark.student_id == user_id, Bookmark.post_id.in_(post_ids)).all()
        by_post = {b.post_id: b for b in rows}
        folder_delta = defaultdict(int)
        for b in rows:
            folder_delta[b.folder_id] -= 1          # the bookmark's OWN folder (fixes audit B5)
        if rows:
            Bookmark.query.filter(Bookmark.id.in_([b.id for b in rows])).delete(
                synchronize_session=False)
            _bump_posts([b.post_id for b in rows], -1)
            _bump_folders(folder_delta)
        _commit()
    except Exception:
        db.session.rollback()
        raise
    counts = _post_counts(post_ids)
    return [{"post_id": pid, "success": True, "bookmarked": False,
             "removed": pid in by_post, "bookmark_count": counts.get(pid, 0)}
            for pid in post_ids]


def toggle_bookmarks(user_id, post_ids, *, folder_id=None, folder_name=None, notes=None, tags=None):
    """Legacy multi-toggle: bookmarked -> removed, not bookmarked -> added."""
    post_ids = clean_post_ids(post_ids)
    have = {pid for (pid,) in db.session.query(Bookmark.post_id).filter(
        Bookmark.student_id == user_id, Bookmark.post_id.in_(post_ids)).all()}
    to_remove = [p for p in post_ids if p in have]
    to_add = [p for p in post_ids if p not in have]
    out = {}
    folder = None
    if to_remove:
        for r in remove_bookmarks(user_id, to_remove):
            out[r["post_id"]] = r
    if to_add:
        added, folder = add_bookmarks(user_id, to_add, folder_id=folder_id,
                                      folder_name=folder_name, notes=notes, tags=tags)
        for r in added:
            out[r["post_id"]] = r
    if folder is None:
        folder = resolve_folder(user_id, folder_id=folder_id, folder_name=folder_name)
        _commit()
    return [out[p] for p in post_ids], folder


def update_bookmark(user_id, bookmark_id, *, notes=_UNSET, tags=_UNSET, folder_id=_UNSET):
    b = Bookmark.query.filter_by(id=bookmark_id, student_id=user_id).first()
    if not b:
        raise NotFoundError("Bookmark not found")
    try:
        if notes is not _UNSET:
            b.notes = clean_notes(notes)
        if tags is not _UNSET:
            b.tags = clean_tags(tags)
        if folder_id is not _UNSET and folder_id != b.folder_id:
            target = resolve_folder(user_id, folder_id=folder_id)
            _bump_folders({b.folder_id: -1, target.id: +1})
            b.folder_id, b.folder = target.id, target.name
        _commit()
    except Exception:
        db.session.rollback()
        raise
    return b


def move_bookmarks(user_id, post_ids, folder_id):
    post_ids = clean_post_ids(post_ids)
    try:
        target = resolve_folder(user_id, folder_id=folder_id)
        rows = Bookmark.query.filter(
            Bookmark.student_id == user_id, Bookmark.post_id.in_(post_ids),
            or_(Bookmark.folder_id != target.id, Bookmark.folder_id.is_(None))).all()
        delta = defaultdict(int)
        for b in rows:
            delta[b.folder_id] -= 1
            delta[target.id] += 1
        if rows:
            Bookmark.query.filter(Bookmark.id.in_([b.id for b in rows])).update(
                {Bookmark.folder_id: target.id, Bookmark.folder: target.name},
                synchronize_session=False)
            _bump_folders(delta)
        _commit()
    except Exception:
        db.session.rollback()
        raise
    return {"moved": len(rows), "folder": serialize_folder(target)}


# ───────────────────────── reads ─────────────────────────

def bookmark_status(user_id, post_ids):
    post_ids = clean_post_ids(post_ids, cap=MAX_STATUS_BATCH)
    rows = db.session.query(Bookmark.post_id, Bookmark.folder_id).filter(
        Bookmark.student_id == user_id, Bookmark.post_id.in_(post_ids)).all()
    got = {pid: fid for pid, fid in rows}
    return {str(pid): {"bookmarked": pid in got, "folder_id": got.get(pid)} for pid in post_ids}


def get_stats(user_id):
    folders = list_folders(user_id)
    by_type = dict(db.session.query(Post.post_type, func.count(Bookmark.id))
                   .join(Bookmark, Bookmark.post_id == Post.id)
                   .filter(Bookmark.student_id == user_id)
                   .group_by(Post.post_type).all())
    return {"total": sum(f["count"] for f in folders), "folders": folders, "by_type": by_type}


# ── cursor helpers: opaque base64("<iso>|<id>") over (bookmarked_at, id) ──

def encode_bookmark_cursor(b):
    return base64.urlsafe_b64encode(f"{b.bookmarked_at.isoformat()}|{b.id}".encode()).decode()


def decode_bookmark_cursor(cursor):
    try:
        ts, bid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.datetime.fromisoformat(ts), int(bid)
    except Exception:
        raise ValidationError("Invalid cursor")


def _escape_like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _apply_filters(query, user_id, *, folder_id, post_type, tag, q):
    query = query.join(Post, Post.id == Bookmark.post_id).filter(Bookmark.student_id == user_id)
    if folder_id is not None:
        query = query.filter(Bookmark.folder_id == folder_id)
    if post_type:
        if post_type not in VALID_POST_TYPES:
            raise ValidationError("Invalid post type")
        query = query.filter(Post.post_type == post_type)
    if tag:
        query = query.filter(json_array_contains(Post.tags, tag))
    if q:
        like = f"%{_escape_like(q[:100])}%"
        query = query.filter(or_(Post.title.ilike(like, escape="\\"),
                                 Post.text_content.ilike(like, escape="\\"),
                                 Bookmark.notes.ilike(like, escape="\\")))
    return query


def list_bookmarks_page(user_id, *, folder_id=None, post_type=None, tag=None, q=None,
                        sort="recent", cursor=None, limit=PAGE_DEFAULT):
    """
    Feed-grade page of bookmarked posts.

    Query budget (constant, independent of how many bookmarks exist):
      1 list query (index: idx_bookmarks_student_time / _student_folder_time)
      1 count query (first page only)
      ~9 batched IN-queries inside hydrate_post_cards (same set get_feed runs)
    """
    limit = max(1, min(int(limit or PAGE_DEFAULT), PAGE_MAX))
    q = (q or "").strip() or None
    tag = (tag or "").strip().lstrip("#") or None
    newest_first = sort != "oldest"

    base = _apply_filters(db.session.query(Bookmark, Post), user_id,
                          folder_id=folder_id, post_type=post_type, tag=tag, q=q)
    if cursor:
        ts, bid = decode_bookmark_cursor(cursor)
        if newest_first:
            base = base.filter(or_(Bookmark.bookmarked_at < ts,
                                   and_(Bookmark.bookmarked_at == ts, Bookmark.id < bid)))
        else:
            base = base.filter(or_(Bookmark.bookmarked_at > ts,
                                   and_(Bookmark.bookmarked_at == ts, Bookmark.id > bid)))
    order = ((Bookmark.bookmarked_at.desc(), Bookmark.id.desc()) if newest_first
             else (Bookmark.bookmarked_at.asc(), Bookmark.id.asc()))
    rows = base.order_by(*order).limit(limit + 1).all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_bookmark_cursor(rows[-1][0]) if has_more and rows else None

    total = None
    if not cursor:
        total = _apply_filters(db.session.query(func.count(Bookmark.id)), user_id,
                               folder_id=folder_id, post_type=post_type, tag=tag, q=q).scalar() or 0

    posts = [p for (_b, p) in rows]
    bookmarks_map = {b.post_id: b for (b, _p) in rows}
    cards = hydrate_post_cards(posts, user_id, bookmarks_map=bookmarks_map)
    card_by_id = {c["id"]: c for c in cards}

    items = []
    for b, p in rows:
        card = card_by_id.get(p.id)
        if not card:          # author deleted etc. — skipped, same as get_feed
            continue
        items.append({
            "bookmark_id": b.id,
            "folder_id": b.folder_id,
            "folder": b.folder,
            "notes": b.notes,
            "tags": b.tags or [],
            "bookmarked_at": b.bookmarked_at.isoformat() if b.bookmarked_at else None,
            "post": card,
        })
    return {"bookmarks": items, "next_cursor": next_cursor, "has_more": has_more, "total": total}
