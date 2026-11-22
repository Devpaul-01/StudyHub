from models import ReputationHistory
from services import reputation_cleanup


def _row(db_session, user, action, points, related_id):
    row = ReputationHistory(user_id=user.id, action=action, points_change=points,
                            related_type="comment", related_id=related_id,
                            reputation_before=0, reputation_after=0)
    db_session.add(row)
    db_session.flush()
    return row


def test_finds_only_surplus_rows_and_keeps_the_oldest(db_session, make_user):
    user = make_user(reputation=45)
    first = _row(db_session, user, "comment_marked_solution", 15, 7)
    dup1 = _row(db_session, user, "comment_marked_solution", 15, 7)
    dup2 = _row(db_session, user, "comment_marked_solution", 15, 7)
    _row(db_session, user, "comment_marked_solution", 15, 8)          # different comment: legitimate
    _row(db_session, user, "comment_marked_helpful", 3, 7)            # not a one-time action
    _row(db_session, user, "comment_marked_helpful", 3, 7)

    surplus = reputation_cleanup.find_surplus_awards()

    assert [r.id for r in surplus] == [dup1.id, dup2.id]
    assert first.id not in [r.id for r in surplus]
    assert reputation_cleanup.summarize(surplus) == {user.id: 30}


def test_apply_removes_rows_and_takes_points_back_with_floor(db_session, make_user):
    user = make_user(reputation=20)
    _row(db_session, user, "post_10_likes", 5, 1)
    _row(db_session, user, "comment_marked_solution", 15, 7)
    _row(db_session, user, "comment_marked_solution", 15, 7)
    _row(db_session, user, "comment_marked_solution", 15, 7)

    rows = reputation_cleanup.find_surplus_awards()
    reputation_cleanup.apply_cleanup(rows)
    db_session.flush()

    assert user.reputation == 0          # 20 - 30, floored
    assert db_session.query(ReputationHistory).filter_by(action="comment_marked_solution").count() == 1
    assert reputation_cleanup.find_surplus_awards() == []


def test_nothing_to_do_when_there_are_no_duplicates(db_session, make_user):
    user = make_user(reputation=5)
    _row(db_session, user, "post_10_likes", 5, 1)
    assert reputation_cleanup.find_surplus_awards() == []
