"""
services/post_hydration_service.py

Turn a page of Post rows into feed-card payloads using constant-count,
batched IN-queries (no per-post queries). Extracted from routes/student/posts/
crud.py::get_feed. No Flask imports.
"""
from collections import defaultdict

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import aliased

from extensions import db
from models import (
    Bookmark, Comment, CommentLike, Connection, PostFollow, PostReaction,
    Thread, ThreadJoinRequest, ThreadMember, User,
)


def hydrate_post_cards(posts, viewer_id, *, bookmarks_map=None, comments_preview_limit=2):
    """
    posts          : list[Post] in display order
    viewer_id      : current user's id
    bookmarks_map  : optional {post_id: Bookmark} if the caller already has the
                     viewer's bookmark rows (bookmarks list). Otherwise one
                     batched query loads them.
    Returns list[dict] (same order; posts whose author row is missing are skipped).
    """
    if not posts:
        return []

    post_ids = [p.id for p in posts]
    author_ids = list({p.student_id for p in posts})

    # 1. authors
    authors_map = {u.id: u for u in User.query.filter(User.id.in_(author_ids)).all()}

    # 2. viewer reactions
    reactions_map = {r.post_id: r for r in PostReaction.query.filter(
        PostReaction.post_id.in_(post_ids), PostReaction.student_id == viewer_id).all()}

    # 3. viewer follows
    follows_map = {f.post_id: f for f in PostFollow.query.filter(
        PostFollow.post_id.in_(post_ids), PostFollow.student_id == viewer_id).all()}

    # 3b. viewer bookmarks  (NEW — the root cause of "feed can't show bookmarked state")
    if bookmarks_map is None:
        bookmarks_map = {b.post_id: b for b in Bookmark.query.filter(
            Bookmark.post_id.in_(post_ids), Bookmark.student_id == viewer_id).all()}

    # 4. connections with authors
    other_author_ids = [a for a in author_ids if a != viewer_id]
    connections_map = {}
    if other_author_ids:
        conns = Connection.query.filter(or_(
            and_(Connection.requester_id == viewer_id, Connection.receiver_id.in_(other_author_ids)),
            and_(Connection.receiver_id == viewer_id, Connection.requester_id.in_(other_author_ids)),
        )).all()
        for c in conns:
            other = c.receiver_id if c.requester_id == viewer_id else c.requester_id
            connections_map[other] = c.status

    # 5. threads (+ viewer's join requests / membership)
    threads_map, thread_join_map, thread_member_set = {}, {}, set()
    thread_post_ids = [p.id for p in posts if p.thread_enabled]
    if thread_post_ids:
        threads = Thread.query.filter(Thread.post_id.in_(thread_post_ids)).all()
        threads_map = {t.post_id: t for t in threads}
        thread_ids = [t.id for t in threads]
        if thread_ids:
            thread_join_map = {jr.thread_id: jr.status for jr in ThreadJoinRequest.query.filter(
                ThreadJoinRequest.thread_id.in_(thread_ids),
                ThreadJoinRequest.requester_id == viewer_id).all()}
            thread_member_set = {m.thread_id for m in ThreadMember.query.filter(
                ThreadMember.thread_id.in_(thread_ids),
                ThreadMember.student_id == viewer_id).all()}

    # 6. top-N comments per post (window function) + authors + viewer likes
    rank_col = func.row_number().over(
        partition_by=Comment.post_id,
        order_by=[Comment.is_solution.desc(), Comment.likes_count.desc()]).label("rn")
    ranked = (db.session.query(Comment, rank_col)
              .filter(Comment.post_id.in_(post_ids), Comment.parent_id == None,  # noqa: E711
                      Comment.is_deleted == False)                                # noqa: E712
              .subquery())
    CommentAlias = aliased(Comment, ranked)
    top_comments = (db.session.query(CommentAlias)
                    .filter(ranked.c.rn <= comments_preview_limit).all())
    comments_by_post = defaultdict(list)
    for c in top_comments:
        comments_by_post[c.post_id].append(c)

    c_author_ids = list({c.student_id for c in top_comments})
    c_authors = {u.id: u for u in User.query.filter(User.id.in_(c_author_ids)).all()} if c_author_ids else {}
    liked = set()
    if top_comments:
        liked = {lk.comment_id for lk in CommentLike.query.filter(
            CommentLike.student_id == viewer_id,
            CommentLike.comment_id.in_([c.id for c in top_comments])).all()}

    # ── assemble ──
    out = []
    for post in posts:
        author = authors_map.get(post.student_id)
        if not author:
            continue
        reacted = reactions_map.get(post.id)
        followed = follows_map.get(post.id)
        bm = bookmarks_map.get(post.id)

        thread_id = requested = None
        is_member = False
        if post.thread_enabled:
            t = threads_map.get(post.id)
            if t:
                thread_id = t.id
                requested = thread_join_map.get(t.id)
                is_member = t.id in thread_member_set

        preview = []
        for c in comments_by_post.get(post.id, []):
            ca = c_authors.get(c.student_id)
            preview.append({
                "id": c.id, "text_content": c.text_content, "likes_count": c.likes_count,
                "helpful_count": c.helpful_count, "is_solution": c.is_solution,
                "resources": c.resources or [], "has_liked": c.id in liked,
                "posted_at": c.posted_at.isoformat(),
                "author": {"id": ca.id, "username": ca.username, "name": ca.name,
                           "avatar": ca.avatar} if ca else None,
            })

        out.append({
            "id": post.id,
            "title": post.title,
            "excerpt": post.text_content,
            "post_type": post.post_type,
            "department": post.department,
            "tags": post.tags or [],
            "resources": post.resources or [],
            "thread_enabled": post.thread_enabled,
            "thread_id": thread_id,
            "is_solved": post.is_solved if post.post_type in ["question", "problem"] else None,
            "is_pinned": post.is_pinned if post.student_id == viewer_id else None,
            "reactions_count": post.positive_reactions_count or 0,
            "comments_count": post.comments_count or 0,
            "views_count": post.views_count or 0,
            "bookmark_count": post.bookmark_count or 0,                      # NEW
            "posted_at": post.posted_at.isoformat(),
            "is_author": post.student_id == viewer_id,
            "connection_status": connections_map.get(author.id) if author.id != viewer_id else None,
            "author": {"id": author.id, "username": author.username, "name": author.name,
                       "avatar": author.avatar, "reputation": author.reputation,
                       "reputation_level": author.reputation_level},
            "comments_preview": preview,
            "user_interactions": {
                "user_reacted": bool(reacted),
                "reaction_type": reacted.reaction_type if reacted else None,
                "user_followed": bool(followed),
                "requested_thread": requested,
                "is_thread_member": is_member,
                "bookmarked": bm is not None,                                # NEW
                "bookmark_folder_id": bm.folder_id if bm else None,          # NEW
            },
        })
    return out
