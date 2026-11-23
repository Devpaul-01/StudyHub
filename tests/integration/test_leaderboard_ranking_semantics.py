"""Ranking semantics shared by every leaderboard surface.

Policy: competition ranking. Tied users share a rank and the next rank skips
("1, 2, 2, 4"). The same number must appear on the list row, in "your rank",
in the nearby window and in snapshots.
"""
import datetime

from models import LeaderboardSnapshot, ReputationHistory


def _hist(db_session, user, pts, days_ago=0.5, action="comment_marked_helpful"):
    db_session.add(ReputationHistory(
        user_id=user.id, action=action, points_change=pts,
        created_at=datetime.datetime.utcnow() - datetime.timedelta(days=days_ago),
        reputation_before=0, reputation_after=0))
    db_session.flush()


def _board(client, headers, query="period=all_time"):
    return client.get(f"/student/leaderboard/global?{query}", headers=headers).get_json()["data"]


class TestListRanks:
    def test_tied_users_share_a_rank_and_the_next_rank_skips(
        self, client, make_user, auth_headers, fakeredis_client
    ):
        top = make_user(reputation=100)
        a, b, c = make_user(reputation=50), make_user(reputation=50), make_user(reputation=50)
        low = make_user(reputation=10)

        data = _board(client, auth_headers(c))

        ranks = {e["user"]["id"]: e["rank"] for e in data["leaderboard"]}
        assert ranks == {top.id: 1, a.id: 2, b.id: 2, c.id: 2, low.id: 5}

    def test_your_rank_matches_your_row_even_when_you_are_last_of_a_tie(
        self, client, make_user, auth_headers, fakeredis_client
    ):
        make_user(reputation=100)
        make_user(reputation=50), make_user(reputation=50)
        viewer = make_user(reputation=50)

        data = _board(client, auth_headers(viewer))

        mine = next(e for e in data["leaderboard"] if e["is_you"])
        assert mine["rank"] == data["your_position"]["rank"] == 2

    def test_ties_spanning_a_page_boundary_keep_the_same_rank_on_both_pages(
        self, client, make_user, auth_headers, fakeredis_client
    ):
        viewer = make_user(reputation=100)
        tied = [make_user(reputation=50) for _ in range(3)]     # positions 2..4

        page1 = _board(client, auth_headers(viewer), "period=all_time&limit=2&page=1")
        page2 = _board(client, auth_headers(viewer), "period=all_time&limit=2&page=2")

        assert [e["rank"] for e in page1["leaderboard"]] == [1, 2]
        assert [e["rank"] for e in page2["leaderboard"]] == [2, 2]
        assert page1["pagination"]["has_more"] is True and page2["pagination"]["has_more"] is False
        assert {e["user"]["id"] for e in page1["leaderboard"] + page2["leaderboard"]} == {viewer.id, *[u.id for u in tied]}

    def test_period_board_ranks_by_ledger_sum_including_negative_scores(
        self, client, make_user, auth_headers, fakeredis_client, db_session
    ):
        a, b, c = make_user(), make_user(), make_user()
        _hist(db_session, a, 10); _hist(db_session, b, 10); _hist(db_session, c, -5)

        data = _board(client, auth_headers(a), "period=weekly")

        assert [(e["user"]["id"], e["score"], e["rank"]) for e in data["leaderboard"]] == [
            (a.id, 10, 1), (b.id, 10, 1), (c.id, -5, 3)]


class TestRankChangeScope:
    def _with_snapshots(self, db_session, users, days_ago=8):
        t = datetime.datetime.utcnow() - datetime.timedelta(days=days_ago)
        for rank, u in enumerate(users, start=1):
            db_session.add(LeaderboardSnapshot(
                user_id=u.id, snapshot_type="weekly", global_rank=rank, department_rank=None,
                score=u.reputation, created_at=t))
        db_session.flush()

    def test_weekly_board_does_not_compare_against_an_all_time_snapshot(
        self, client, make_user, auth_headers, fakeredis_client, db_session
    ):
        users = [make_user(reputation=1000 - i) for i in range(40)]
        self._with_snapshots(db_session, users)
        _hist(db_session, users[-1], 20)                    # weekly rank 1, all-time rank 40

        data = _board(client, auth_headers(users[0]), "period=weekly")

        assert data["leaderboard"][0]["rank"] == 1
        assert data["leaderboard"][0]["rank_change"] is None

    def test_all_time_board_still_reports_movement(
        self, client, make_user, auth_headers, fakeredis_client, db_session
    ):
        users = [make_user(reputation=1000 - i) for i in range(3)]
        self._with_snapshots(db_session, [users[1], users[0], users[2]])   # user[0] was rank 2, now rank 1

        data = _board(client, auth_headers(users[0]), "period=all_time")

        mover = next(e for e in data["leaderboard"] if e["user"]["id"] == users[0].id)
        assert mover["rank"] == 1 and mover["rank_change"] == 1

    def test_department_board_omits_rank_change(
        self, client, make_user, make_student_profile, auth_headers, fakeredis_client, db_session
    ):
        u = make_user(reputation=10, status="approved")
        make_student_profile(u, department="CS")
        self._with_snapshots(db_session, [u])

        data = _board(client, auth_headers(u), "period=all_time&department=CS")

        assert data["leaderboard"][0]["rank_change"] is None


class TestNearbyAndMyRank:
    def test_nearby_includes_tied_users_and_uses_list_ranks(
        self, client, make_user, auth_headers, fakeredis_client
    ):
        top = make_user(reputation=100)
        a, b, me = make_user(reputation=50), make_user(reputation=50), make_user(reputation=50)
        low = make_user(reputation=10)

        data = client.get("/student/leaderboard/nearby?period=all_time&range=5",
                          headers=auth_headers(me)).get_json()["data"]

        listed = {e["user"]["id"]: e["rank"] for e in _board(client, auth_headers(me))["leaderboard"]}
        got = {e["user"]["id"]: e["rank"] for e in data["nearby"]}
        assert set(got) == {top.id, a.id, b.id, me.id, low.id}      # tied users are not dropped
        assert got == listed                                         # same ranks as the list
        assert data["your_rank"] == listed[me.id] == 2

    def test_nearby_window_is_ordered_and_bounded(
        self, client, make_user, auth_headers, fakeredis_client
    ):
        users = [make_user(reputation=100 - i) for i in range(11)]   # scores 100..90
        me = users[5]

        data = client.get("/student/leaderboard/nearby?period=all_time&range=2",
                          headers=auth_headers(me)).get_json()["data"]

        assert [e["user"]["id"] for e in data["nearby"]] == [u.id for u in users[3:8]]
        assert [e["is_you"] for e in data["nearby"]] == [False, False, True, False, False]

    def test_nearby_for_a_viewer_with_no_weekly_activity_shows_where_they_would_sit(
        self, client, make_user, auth_headers, fakeredis_client, db_session
    ):
        a, b, quiet = make_user(), make_user(), make_user()
        _hist(db_session, a, 20); _hist(db_session, b, 10)

        data = client.get("/student/leaderboard/nearby?period=weekly&range=3",
                          headers=auth_headers(quiet)).get_json()["data"]

        assert [e["user"]["id"] for e in data["nearby"]] == [a.id, b.id, quiet.id]
        assert data["your_rank"] == 3

    def test_viewer_outside_the_department_gets_no_rank_in_it(
        self, client, make_user, make_student_profile, auth_headers, fakeredis_client
    ):
        mine, other = make_user(reputation=5, status="approved"), make_user(reputation=50, status="approved")
        make_student_profile(mine, department="Physics")
        make_student_profile(other, department="CS")

        board = _board(client, auth_headers(mine), "period=all_time&department=CS")
        nearby = client.get("/student/leaderboard/nearby?period=all_time&department=CS",
                            headers=auth_headers(mine)).get_json()["data"]

        assert [e["user"]["id"] for e in board["leaderboard"]] == [other.id]
        assert board["your_position"]["rank"] is None
        assert nearby["your_rank"] is None and nearby["nearby"] == []

    def test_my_rank_card_agrees_with_the_list_and_scopes_movement(
        self, client, make_user, auth_headers, fakeredis_client, db_session
    ):
        make_user(reputation=100)
        make_user(reputation=50)
        me = make_user(reputation=50)

        card = client.get("/student/leaderboard/me?period=all_time", headers=auth_headers(me)).get_json()["data"]
        weekly = client.get("/student/leaderboard/me?period=weekly", headers=auth_headers(me)).get_json()["data"]

        assert card["rank"]["global"] == 2
        assert [e["rank"] for e in card["nearby_users"]] == [1, 2, 2]
        assert weekly["rank"]["change"] is None


class TestConnectionsBoard:
    def test_zero_score_friend_ranks_above_a_negative_score_friend(
        self, client, make_user, make_connection, auth_headers, fakeredis_client, db_session
    ):
        me, neg, zero = make_user(), make_user(), make_user()
        make_connection(me, neg, status="accepted")
        make_connection(me, zero, status="accepted")
        _hist(db_session, me, 5)
        _hist(db_session, neg, -5, action="content_reported")

        data = client.get("/student/leaderboard/connections?period=weekly",
                          headers=auth_headers(me)).get_json()["data"]

        assert [(e["user"]["id"], e["score"], e["rank"]) for e in data["leaderboard"]] == [
            (me.id, 5, 1), (zero.id, 0, 2), (neg.id, -5, 3)]
        assert data["your_rank"] == 1

    def test_tied_friends_share_a_rank(
        self, client, make_user, make_connection, auth_headers, fakeredis_client
    ):
        me, a, b = make_user(reputation=50), make_user(reputation=50), make_user(reputation=10)
        make_connection(me, a, status="accepted")
        make_connection(me, b, status="accepted")

        data = client.get("/student/leaderboard/connections?period=all_time",
                          headers=auth_headers(me)).get_json()["data"]

        assert [e["rank"] for e in data["leaderboard"]] == [1, 1, 3]
