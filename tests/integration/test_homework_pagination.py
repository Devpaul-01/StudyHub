"""
Regression tests for audit finding F-20 (stable cursor pagination).
"""
import datetime
from types import SimpleNamespace

import pytest

from services import homework_service

pytestmark = pytest.mark.integration


def _items(n):
    return [SimpleNamespace(id=i, key=i) for i in range(1, n + 1)]


class TestSliceByKeyset:
    ARGS = dict(encode=str, decode=int)

    def test_walks_the_whole_list_ascending(self):
        items = _items(7)
        seen, cursor = [], None
        while True:
            page, more, cursor = homework_service.slice_by_keyset(
                items, lambda x: x.key, cursor, 3, descending=False, **self.ARGS)
            seen += [i.id for i in page]
            if not more:
                break
        assert seen == list(range(1, 8))

    def test_cursor_row_removed_does_not_restart_from_the_top(self):
        items = _items(6)
        _, _, cursor = homework_service.slice_by_keyset(
            items, lambda x: x.key, None, 3, descending=False, **self.ARGS)
        assert cursor == "3:3"
        remaining = [i for i in items if i.id != 3]        # row 3 was completed meanwhile

        page, _, _ = homework_service.slice_by_keyset(
            remaining, lambda x: x.key, cursor, 3, descending=False, **self.ARGS)

        assert [i.id for i in page] == [4, 5, 6]            # old behaviour returned [1, 2, 4]

    def test_descending_order(self):
        items = list(reversed(_items(5)))
        page, more, cursor = homework_service.slice_by_keyset(
            items, lambda x: x.key, None, 2, descending=True, **self.ARGS)
        page2, _, _ = homework_service.slice_by_keyset(
            items, lambda x: x.key, cursor, 2, descending=True, **self.ARGS)
        assert [i.id for i in page] == [5, 4] and [i.id for i in page2] == [3, 2]

    def test_legacy_bare_id_cursor_still_works(self):
        page, _, _ = homework_service.slice_by_keyset(
            _items(5), lambda x: x.key, "2", 2, descending=False, **self.ARGS)
        assert [i.id for i in page] == [3, 4]

    def test_garbage_cursor_starts_from_the_top(self):
        page, _, _ = homework_service.slice_by_keyset(
            _items(5), lambda x: x.key, "abc:def", 2, descending=False, **self.ARGS)
        assert [i.id for i in page] == [1, 2]

    def test_iso_datetime_keys_contain_colons_and_still_round_trip(self):
        base = datetime.datetime(2030, 1, 1, 10, 30, 15)
        items = [SimpleNamespace(id=i, key=base + datetime.timedelta(hours=i)) for i in range(1, 5)]
        iso = lambda v: v.isoformat()  # noqa: E731
        page, _, cursor = homework_service.slice_by_keyset(
            items, lambda x: x.key, None, 2, descending=False,
            encode=iso, decode=datetime.datetime.fromisoformat)
        page2, _, _ = homework_service.slice_by_keyset(
            items, lambda x: x.key, cursor, 2, descending=False,
            encode=iso, decode=datetime.datetime.fromisoformat)
        assert [i.id for i in page2] == [3, 4]


class TestRoutesPaginateStably:
    def _walk(self, client, headers, url, list_key, mutate=None):
        seen, cursor, first = [], None, True
        for _ in range(20):
            sep = "&" if "?" in url else "?"
            resp = client.get(f"{url}{sep}limit=3" + (f"&cursor={cursor}" if cursor else ""), headers=headers)
            data = resp.get_json()["data"]
            seen += [x["id"] for x in data[list_key]]
            if first and mutate:
                mutate(data[list_key][-1]["id"])
                first = False
            cursor = data["next_cursor"]
            if not data["has_more"]:
                break
        return seen

    @pytest.mark.parametrize("sort", ["priority", "due_date", "created_at"])
    def test_each_sort_returns_every_row_exactly_once(
        self, sort, client, make_user, make_assignment, auth_headers
    ):
        user = make_user()
        now = datetime.datetime.utcnow()
        made = [make_assignment(user, due_date=now + datetime.timedelta(hours=5 + i * 7)) for i in range(8)]

        seen = self._walk(client, auth_headers(user), f"/student/assignments?sort={sort}", "assignments")

        assert sorted(seen) == sorted(a.id for a in made)
        assert len(seen) == len(set(seen))

    def test_completing_the_cursor_row_between_pages_does_not_repeat_rows(
        self, client, db_session, make_user, make_assignment, auth_headers
    ):
        from models import Assignment

        user = make_user()
        now = datetime.datetime.utcnow()
        for i in range(8):
            make_assignment(user, due_date=now + datetime.timedelta(hours=5 + i * 7))

        def complete(assignment_id):
            Assignment.query.get(assignment_id).status = "completed"
            db_session.flush()

        seen = self._walk(client, auth_headers(user), "/student/assignments?sort=due_date",
                          "assignments", mutate=complete)

        assert len(seen) == len(set(seen)), f"repeated rows: {seen}"

    def test_feed_pages_cover_everything(
        self, client, make_user, make_assignment, make_connection, auth_headers
    ):
        me, friend = make_user(), make_user()
        make_connection(me, friend)
        made = [make_assignment(friend, is_shared_for_help=True) for _ in range(7)]

        seen = self._walk(client, auth_headers(me), "/student/homework/feed", "homework")

        assert sorted(seen) == sorted(a.id for a in made)
