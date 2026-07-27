"""
services/search_service.py

Consolidated search implementation — the ONE search implementation per
Document 1 §6.1 / §A-3. Replaces search.py's dedicated-vs-unified pairs
(search_users vs _search_users_unified, search_posts vs
_search_posts_unified, search_threads vs _search_threads_unified, plus
_search_all_unified's independent third re-implementation of filtering)
with one function per entity type. search.py's routes become thin callers:
`GET /search/users` calls search_users(...) directly; `GET /search/unified
?type=users` calls the exact same function and wraps the result in the
unified envelope. There is no second code path to drift out of sync.

Since you confirmed the frontend isn't using these endpoints yet, this is
a genuine redesign, not a careful-preserve-every-field migration. Notably:

  - search_threads() returns thread SUMMARIES ONLY (id, title, member_count,
    tags, creator) — NOT the full members_data array the old
    _search_threads_unified returned (every member, with a per-member
    connection-status lookup, for every thread on the results page). That
    was an N×M-query problem for what should be a lightweight search
    endpoint. Full member lists remain available via the existing
    GET /threads/<id>/members endpoint, which is the correct place for
    that payload. This matches search_posts/search_users, which already
    only return summaries.

  - Skills/tags filtering uses the already-fixed `.contains()`-based
    portable filter (H-7), not the Postgres-JSONB-only `?|` operator.

Per Document 2 §2's layering rule: no Flask imports, no request/session/g,
no jsonify(). Functions accept plain Python values (query string, filter
dict, page/per_page ints, viewer_id) and return plain dicts/dataclasses;
routes/student/search.py does the request-arg parsing and jsonify(...)
wrapping.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, and_, func

from models import (
    User, StudentProfile, Post, Thread, ThreadMember, Connection,
)
from extensions import db


# ─────────────────────────────────────────────────────────────────────────────
# RESULT DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PaginatedResult:
    items: list
    page: int
    per_page: int
    total: int
    pages: int
    has_next: bool
    has_prev: bool

    def to_dict(self, items_key: str) -> dict:
        return {
            items_key: self.items,
            "pagination": {
                "page": self.page,
                "per_page": self.per_page,
                "total": self.total,
                "pages": self.pages,
                "has_next": self.has_next,
                "has_prev": self.has_prev,
            },
        }

    @classmethod
    def from_paginate(cls, paginated, formatted_items: list) -> "PaginatedResult":
        return cls(
            items=formatted_items,
            page=paginated.page,
            per_page=paginated.per_page,
            total=paginated.total,
            pages=paginated.pages,
            has_next=paginated.has_next,
            has_prev=paginated.has_prev,
        )


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _connection_status_map(viewer_id: int, user_ids: list[int]) -> dict[int, str]:
    """Batch {user_id -> connection_status string} relative to viewer_id."""
    if not user_ids:
        return {}
    connections = Connection.query.filter(
        or_(
            and_(Connection.requester_id == viewer_id, Connection.receiver_id.in_(user_ids)),
            and_(Connection.requester_id.in_(user_ids), Connection.receiver_id == viewer_id),
        )
    ).all()

    result = {}
    for conn in connections:
        other_id = conn.receiver_id if conn.requester_id == viewer_id else conn.requester_id
        if conn.status == "accepted":
            result[other_id] = "connected"
        elif conn.status == "pending":
            result[other_id] = "pending_sent" if conn.requester_id == viewer_id else "pending_received"
    return result


def _profile_map(user_ids: list[int]) -> dict:
    if not user_ids:
        return {}
    return {p.user_id: p for p in StudentProfile.query.filter(StudentProfile.user_id.in_(user_ids)).all()}


# ─────────────────────────────────────────────────────────────────────────────
# USER SEARCH
# ─────────────────────────────────────────────────────────────────────────────

def search_users(
    query: str,
    *,
    department: str | None = None,
    class_level: str | None = None,
    skills: list[str] | None = None,
    reputation_min: int | None = None,
    connected_only: bool = False,
    sort: str = "relevance",
    page: int = 1,
    per_page: int = 20,
    viewer_id: int,
) -> dict:
    """
    Search users with filters. Single implementation for both
    GET /search/users and GET /search/unified?type=users.
    """
    per_page = min(per_page, 50)

    q = User.query.filter(User.id != viewer_id, User.status == "approved")

    if query:
        pattern = f"%{query}%"
        q = q.filter(or_(User.username.ilike(pattern), User.name.ilike(pattern)))

    joined_profile = False
    if department:
        q = q.join(StudentProfile)
        joined_profile = True
        q = q.filter(StudentProfile.department == department)

    if class_level:
        if not joined_profile:
            q = q.join(StudentProfile)
            joined_profile = True
        q = q.filter(StudentProfile.class_name == class_level)

    if connected_only:
        connected_rows = Connection.query.filter(
            or_(
                and_(Connection.requester_id == viewer_id, Connection.status == "accepted"),
                and_(Connection.receiver_id == viewer_id, Connection.status == "accepted"),
            )
        ).all()
        connected_ids = [
            c.requester_id if c.receiver_id == viewer_id else c.receiver_id
            for c in connected_rows
        ]
        q = q.filter(User.id.in_(connected_ids))

    if skills:
        skills_list = [s.strip().lower() for s in skills]
        # H-7: .contains() is dialect-portable; the Postgres-JSONB-only
        # '?|' operator is not valid on a plain db.JSON column / SQLite.
        q = q.filter(or_(*[User.skills.contains([s]) for s in skills_list]))

    if reputation_min:
        q = q.filter(User.reputation >= reputation_min)

    if sort == "reputation":
        q = q.order_by(User.reputation.desc())
    elif sort == "name":
        q = q.order_by(User.name.asc())
    elif sort == "recent":
        q = q.order_by(User.joined_at.desc())
    else:  # relevance
        if query:
            q = q.order_by(User.username.ilike(f"{query}%").desc(), User.reputation.desc())
        else:
            q = q.order_by(User.reputation.desc())

    paginated = q.paginate(page=page, per_page=per_page, error_out=False)

    user_ids = [u.id for u in paginated.items]
    pmap = _profile_map(user_ids)
    cmap = _connection_status_map(viewer_id, user_ids)

    users_data = []
    for user in paginated.items:
        privacy_settings = user.privacy_settings or {}
        profile_private = privacy_settings.get("set_profile_private", False)
        profile = pmap.get(user.id)

        users_data.append({
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "avatar": user.avatar,
            "bio": user.bio,
            "private": profile_private,
            "department": profile.department if profile else None,
            "class_level": profile.class_name if profile else None,
            "reputation": user.reputation if not profile_private else None,
            "reputation_level": user.reputation_level if not profile_private else None,
            "skills": user.skills[:5] if user.skills else [],
            "connection_status": cmap.get(user.id, "none"),
        })

    result = PaginatedResult.from_paginate(paginated, users_data).to_dict("users")
    result["filters_applied"] = {
        "query": query, "department": department, "class_level": class_level,
        "skills": ",".join(skills) if skills else "", "sort": sort,
    }
    return result


# ─────────────────────────────────────────────────────────────────────────────
# POST SEARCH
# ─────────────────────────────────────────────────────────────────────────────

def search_posts(
    query: str,
    *,
    post_type: str | None = None,
    department: str | None = None,
    tags: list[str] | None = None,
    solved: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = "recent",
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """Search posts with filters. Single implementation for both
    GET /search/posts and GET /search/unified?type=posts."""
    per_page = min(per_page, 50)

    q = Post.query

    if query:
        pattern = f"%{query}%"
        q = q.filter(or_(Post.title.ilike(pattern), Post.text_content.ilike(pattern)))

    if post_type:
        q = q.filter(Post.post_type == post_type)

    if department:
        q = q.filter(Post.department == department)

    if tags:
        tags_list = [t.strip().lower() for t in tags]
        # H-7: portable .contains() filter, not the Postgres-only '?|' operator.
        q = q.filter(or_(*[Post.tags.contains([t]) for t in tags_list]))

    if solved is not None:
        q = q.filter(Post.is_solved == solved)

    if date_from:
        try:
            q = q.filter(Post.posted_at >= datetime.datetime.fromisoformat(date_from))
        except ValueError:
            pass

    if date_to:
        try:
            q = q.filter(Post.posted_at <= datetime.datetime.fromisoformat(date_to))
        except ValueError:
            pass

    if sort == "popular":
        q = q.order_by((Post.positive_reactions_count + Post.comments_count).desc())
    elif sort == "trending":
        week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        q = q.filter(Post.posted_at >= week_ago).order_by(
            (Post.positive_reactions_count * 2 + Post.comments_count).desc()
        )
    elif sort == "recent":
        q = q.order_by(Post.posted_at.desc())
    else:  # relevance
        if query:
            q = q.order_by(Post.title.ilike(f"%{query}%").desc(), Post.posted_at.desc())
        else:
            q = q.order_by(Post.posted_at.desc())

    paginated = q.paginate(page=page, per_page=per_page, error_out=False)

    author_ids = {p.student_id for p in paginated.items}
    authors_map = {u.id: u for u in User.query.filter(User.id.in_(author_ids)).all()} if author_ids else {}

    posts_data = []
    for post in paginated.items:
        author = authors_map.get(post.student_id)
        posts_data.append({
            "id": post.id,
            "title": post.title,
            "post_type": post.post_type,
            "department": post.department,
            "tags": post.tags,
            "excerpt": post.text_content[:200] if post.text_content else None,
            "reactions_count": post.positive_reactions_count,
            "comments_count": post.comments_count,
            "views": post.views_count,
            "is_solved": post.is_solved,
            "thread_enabled": post.thread_enabled,
            "posted_at": post.posted_at.isoformat(),
            "author": {
                "id": author.id, "username": author.username, "name": author.name,
                "avatar": author.avatar, "reputation_level": author.reputation_level,
            } if author else None,
        })

    result = PaginatedResult.from_paginate(paginated, posts_data).to_dict("posts")
    result["filters_applied"] = {
        "query": query, "post_type": post_type, "department": department,
        "tags": ",".join(tags) if tags else "", "solved": solved, "sort": sort,
    }
    return result


# ─────────────────────────────────────────────────────────────────────────────
# THREAD SEARCH  (redesigned — summaries only, per Doc 1 §6.1)
# ─────────────────────────────────────────────────────────────────────────────

def search_threads(
    query: str,
    *,
    department: str | None = None,
    is_open: bool | None = None,
    has_space: bool | None = None,
    page: int = 1,
    per_page: int = 20,
    viewer_id: int,
) -> dict:
    """
    Search threads. Returns SUMMARIES only (id, title, member_count, tags,
    creator) — no per-member enrichment. This is the redesign from Document
    1 §6.1: the old _search_threads_unified returned a full members_data
    array (every member + a per-member connection-status lookup) for every
    thread on the page, which is an N×M-query cost that doesn't belong on
    a search endpoint. Fetch GET /threads/<id>/members for full member
    lists.
    """
    per_page = min(per_page, 50)

    q = Thread.query

    if query:
        pattern = f"%{query}%"
        q = q.filter(or_(Thread.title.ilike(pattern), Thread.description.ilike(pattern)))

    if department:
        q = q.filter(Thread.department == department)

    if is_open is not None:
        q = q.filter(Thread.is_open == is_open)

    if has_space:
        q = q.filter(Thread.member_count < Thread.max_members)

    q = q.order_by(Thread.last_activity.desc())

    paginated = q.paginate(page=page, per_page=per_page, error_out=False)

    thread_ids = [t.id for t in paginated.items]
    creator_ids = {t.creator_id for t in paginated.items}
    creators_map = {u.id: u for u in User.query.filter(User.id.in_(creator_ids)).all()} if creator_ids else {}

    memberships = ThreadMember.query.filter(
        ThreadMember.thread_id.in_(thread_ids), ThreadMember.student_id == viewer_id
    ).all() if thread_ids else []
    member_thread_ids = {m.thread_id for m in memberships}

    threads_data = []
    for thread in paginated.items:
        creator = creators_map.get(thread.creator_id)
        threads_data.append({
            "id": thread.id,
            "title": thread.title,
            "description": thread.description,
            "avatar": thread.avatar,
            "department": thread.department,
            "tags": thread.tags or [],
            "is_open": thread.is_open,
            "member_count": thread.member_count,
            "max_members": thread.max_members,
            "has_space": thread.member_count < thread.max_members,
            "requires_approval": thread.requires_approval,
            "is_member": thread.id in member_thread_ids,
            "is_creator": thread.creator_id == viewer_id,
            "last_activity": thread.last_activity.isoformat(),
            "created_at": thread.created_at.isoformat(),
            "creator": {
                "id": creator.id, "username": creator.username,
                "name": creator.name, "avatar": creator.avatar,
                "reputation_level": creator.reputation_level,
            } if creator else None,
        })

    result = PaginatedResult.from_paginate(paginated, threads_data).to_dict("threads")
    result["filters_applied"] = {"query": query, "department": department, "is_open": is_open, "has_space": has_space}
    return result


# ─────────────────────────────────────────────────────────────────────────────
# UNIFIED SEARCH  (calls the three type-specific functions — no reimplementation)
# ─────────────────────────────────────────────────────────────────────────────

def search_all(query: str, *, limit_per_type: int = 5, viewer_id: int) -> dict:
    """
    Search across all entity types. Internally calls the exact same
    search_users/search_posts/search_threads functions with
    limit_per_type as per_page — no independent third filtering
    implementation (the old _search_all_unified re-derived basic
    filtering on its own).
    """
    limit_per_type = min(limit_per_type, 10)

    users_result = search_users(query, page=1, per_page=limit_per_type, viewer_id=viewer_id)
    posts_result = search_posts(query, page=1, per_page=limit_per_type)
    threads_result = search_threads(query, page=1, per_page=limit_per_type, viewer_id=viewer_id)

    users_data = users_result["users"]
    posts_data = posts_result["posts"]
    threads_data = threads_result["threads"]

    total = len(users_data) + len(posts_data) + len(threads_data)

    return {
        "query": query,
        "all": {"users": users_data, "posts": posts_data, "threads": threads_data},
        "counts": {
            "users": len(users_data), "posts": len(posts_data),
            "threads": len(threads_data), "total": total,
        },
    }
