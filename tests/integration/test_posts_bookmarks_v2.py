import pytest
pytestmark = pytest.mark.integration


def _h(client, auth_headers, csrf_headers, user):
    return {**auth_headers(user), **csrf_headers(client)}


class TestIdempotentPutDelete:
    def test_put_twice_counts_once(self, client, make_user, make_post, auth_headers, csrf_headers):
        author, user = make_user(), make_user()
        post = make_post(author)
        h = _h(client, auth_headers, csrf_headers, user)
        r1 = client.put(f"/student/posts/{post.id}/bookmark", json={}, headers=h)
        r2 = client.put(f"/student/posts/{post.id}/bookmark", json={}, headers=h)
        assert r1.status_code == r2.status_code == 200
        assert r2.get_json()["data"]["already"] is True
        assert r2.get_json()["data"]["bookmark_count"] == 1

    def test_delete_when_not_bookmarked_is_a_noop(self, client, make_user, make_post, auth_headers, csrf_headers):
        author, user = make_user(), make_user()
        post = make_post(author)
        r = client.delete(f"/student/posts/{post.id}/bookmark", headers=_h(client, auth_headers, csrf_headers, user))
        assert r.status_code == 200
        assert r.get_json()["data"]["bookmark_count"] == 0

    def test_put_unknown_post_404(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        r = client.put("/student/posts/999999/bookmark", json={}, headers=_h(client, auth_headers, csrf_headers, user))
        assert r.status_code == 404

    def test_counter_never_goes_negative(self, client, make_user, make_post, auth_headers, csrf_headers):
        from models import Post
        author, user = make_user(), make_user()
        post = make_post(author)
        h = _h(client, auth_headers, csrf_headers, user)
        client.delete(f"/student/posts/{post.id}/bookmark", headers=h)
        client.delete(f"/student/posts/{post.id}/bookmark", headers=h)
        assert Post.query.get(post.id).bookmark_count == 0


class TestFolderCounters:
    def test_unbookmark_decrements_the_bookmarks_own_folder(self, client, make_user, make_post, auth_headers, csrf_headers):
        """Regression for audit B5."""
        from models import BookmarkFolder
        author, user = make_user(), make_user()
        post = make_post(author)
        h = _h(client, auth_headers, csrf_headers, user)
        client.put(f"/student/posts/{post.id}/bookmark", json={"folder_name": "Exam Prep"}, headers=h)
        # toggle off while naming a DIFFERENT folder in the request
        client.post("/student/posts/bookmark/toggle",
                    json={"post_ids": [post.id], "folder_name": "Other"}, headers=h)
        counts = {f.name: f.bookmark_count for f in BookmarkFolder.query.filter_by(user_id=user.id)}
        assert counts.get("Exam Prep", 0) == 0
        assert all(v >= 0 for v in counts.values())

    def test_legacy_single_route_updates_folder_count(self, client, make_user, make_post, auth_headers, csrf_headers):
        """Regression for audit B6 (legacy routes used to skip folder_id/count)."""
        from models import BookmarkFolder, Bookmark
        author, user = make_user(), make_user()
        post = make_post(author)
        client.post(f"/student/posts/{post.id}/bookmark", headers=_h(client, auth_headers, csrf_headers, user))
        b = Bookmark.query.filter_by(post_id=post.id, student_id=user.id).one()
        assert b.folder_id is not None
        assert BookmarkFolder.query.get(b.folder_id).bookmark_count == 1


class TestFolders:
    def test_crud_and_delete_modes(self, client, make_user, make_post, auth_headers, csrf_headers):
        from models import Post
        author, user = make_user(), make_user()
        post = make_post(author)
        h = _h(client, auth_headers, csrf_headers, user)

        r = client.post("/student/posts/bookmarks/folders", json={"name": "Exam Prep"}, headers=h)
        assert r.status_code == 201
        fid = r.get_json()["data"]["folder"]["id"]

        assert client.post("/student/posts/bookmarks/folders", json={"name": "exam prep"}, headers=h).status_code == 409

        client.put(f"/student/posts/{post.id}/bookmark", json={"folder_id": fid}, headers=h)
        r = client.patch(f"/student/posts/bookmarks/folders/{fid}", json={"name": "Finals"}, headers=h)
        assert r.get_json()["data"]["folder"]["name"] == "Finals"
        listed = client.get("/student/posts/bookmarked?folder=Finals", headers=auth_headers(user)).get_json()["data"]
        assert listed["total"] == 1            # denormalised folder string was renamed too

        r = client.delete(f"/student/posts/bookmarks/folders/{fid}?mode=delete", headers=h)
        assert r.status_code == 200
        assert Post.query.get(post.id).bookmark_count == 0

    def test_default_folder_protected(self, client, make_user, auth_headers, csrf_headers):
        user = make_user()
        h = _h(client, auth_headers, csrf_headers, user)
        folders = client.get("/student/posts/bookmarks/folders", headers=auth_headers(user)).get_json()["data"]["folders"]
        default = next(f for f in folders if f["is_default"])
        assert client.delete(f"/student/posts/bookmarks/folders/{default['id']}", headers=h).status_code == 400

    def test_cannot_touch_another_users_folder(self, client, make_user, auth_headers, csrf_headers):
        a, b = make_user(), make_user()
        fid = client.post("/student/posts/bookmarks/folders", json={"name": "Mine"},
                          headers=_h(client, auth_headers, csrf_headers, a)).get_json()["data"]["folder"]["id"]
        r = client.patch(f"/student/posts/bookmarks/folders/{fid}", json={"name": "Hax"},
                         headers=_h(client, auth_headers, csrf_headers, b))
        assert r.status_code == 404


class TestListPagination:
    def test_cursor_pages_without_overlap(self, client, make_user, make_post, auth_headers, csrf_headers):
        author, user = make_user(), make_user()
        posts = [make_post(author, title=f"P{i}") for i in range(5)]
        h = _h(client, auth_headers, csrf_headers, user)
        for p in posts:
            client.put(f"/student/posts/{p.id}/bookmark", json={}, headers=h)

        seen, cursor = [], None
        for _ in range(5):
            url = "/student/posts/bookmarked?limit=2" + (f"&cursor={cursor}" if cursor else "")
            d = client.get(url, headers=auth_headers(user)).get_json()["data"]
            seen += [b["post"]["id"] for b in d["bookmarks"]]
            cursor = d["next_cursor"]
            if not d["has_more"]:
                break
        assert sorted(seen) == sorted(p.id for p in posts)
        assert len(seen) == len(set(seen))

    def test_invalid_cursor_400(self, client, make_user, auth_headers):
        user = make_user()
        assert client.get("/student/posts/bookmarked?cursor=garbage", headers=auth_headers(user)).status_code == 400

    def test_limit_is_capped(self, client, make_user, auth_headers):
        user = make_user()
        assert client.get("/student/posts/bookmarked?limit=9999", headers=auth_headers(user)).status_code == 200

    def test_filter_by_type_and_search(self, client, make_user, make_post, auth_headers, csrf_headers):
        author, user = make_user(), make_user()
        q = make_post(author, title="Calculus help", post_type="question")
        d = make_post(author, title="Weekend plans", post_type="discussion")
        h = _h(client, auth_headers, csrf_headers, user)
        for p in (q, d):
            client.put(f"/student/posts/{p.id}/bookmark", json={}, headers=h)
        r = client.get("/student/posts/bookmarked?type=question", headers=auth_headers(user)).get_json()["data"]
        assert [b["post"]["id"] for b in r["bookmarks"]] == [q.id]
        r = client.get("/student/posts/bookmarked?q=weekend", headers=auth_headers(user)).get_json()["data"]
        assert [b["post"]["id"] for b in r["bookmarks"]] == [d.id]


class TestQueryBudget:
    def test_list_query_count_is_constant(self, client, make_user, make_post, auth_headers, csrf_headers):
        """The whole point of this rewrite: N bookmarks must not mean N+1 queries."""
        from sqlalchemy import event
        from extensions import db
        author, user = make_user(), make_user()
        h = _h(client, auth_headers, csrf_headers, user)

        def count_queries():
            n = {"c": 0}
            def _inc(*a, **k): n["c"] += 1
            engine = db.engine
            event.listen(engine, "before_cursor_execute", _inc)
            try:
                client.get("/student/posts/bookmarked?limit=20", headers=auth_headers(user))
            finally:
                event.remove(engine, "before_cursor_execute", _inc)
            return n["c"]

        for i in range(2):
            client.put(f"/student/posts/{make_post(author).id}/bookmark", json={}, headers=h)
        few = count_queries()
        for i in range(12):
            client.put(f"/student/posts/{make_post(author).id}/bookmark", json={}, headers=h)
        many = count_queries()
        assert many <= few + 2          # allow tiny variance (e.g. thread/comment branches)


class TestFeedBookmarkFlag:
    def test_feed_reports_bookmarked(self, client, make_user, make_post, auth_headers, csrf_headers):
        author, user = make_user(), make_user()
        post = make_post(author)
        client.put(f"/student/posts/{post.id}/bookmark", json={}, headers=_h(client, auth_headers, csrf_headers, user))
        feed = client.get("/student/posts/feed", headers=auth_headers(user)).get_json()["data"]["posts"]
        card = next(p for p in feed if p["id"] == post.id)
        assert card["user_interactions"]["bookmarked"] is True
        assert card["bookmark_count"] == 1


class TestBulkAndStatus:
    def test_cap_and_status(self, client, make_user, make_post, auth_headers, csrf_headers):
        author, user = make_user(), make_user()
        post = make_post(author)
        h = _h(client, auth_headers, csrf_headers, user)
        assert client.post("/student/posts/bookmark/toggle",
                           json={"post_ids": list(range(1, 52))}, headers=h).status_code == 400   # audit B4
        client.put(f"/student/posts/{post.id}/bookmark", json={}, headers=h)
        s = client.post("/student/posts/bookmarks/status", json={"post_ids": [post.id, 999999]}, headers=h)
        st = s.get_json()["data"]["statuses"]
        assert st[str(post.id)]["bookmarked"] is True and st["999999"]["bookmarked"] is False

    def test_none_body_is_400_not_500(self, client, make_user, auth_headers, csrf_headers):    # audit B8
        user = make_user()
        r = client.post("/student/posts/bulk/bookmark", data="", headers=_h(client, auth_headers, csrf_headers, user))
        assert r.status_code == 400
