"""total_posts is updated atomically in SQL, not by a Python read-modify-write."""
from sqlalchemy import event


def test_post_creation_increments_counter_in_a_single_sql_update(
    client, db_session, make_user, make_student_profile, auth_headers, csrf_headers
):
    from extensions import db
    user = make_user(total_posts=3)
    make_student_profile(user)
    statements = []

    def capture(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        resp = client.post(
            "/student/posts/create",
            json={"title": "Counter test post", "text_content": "Body of the post for the counter test.",
                  "post_type": "discussion", "tags": ["counter"]},
            headers={**auth_headers(user), **csrf_headers(client)},
        )
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)

    assert resp.status_code in (200, 201), resp.get_json()
    assert any("UPDATE users SET total_posts" in " ".join(s.split()) and "coalesce" in s.lower()
               for s in statements), "expected an in-SQL increment"
    db_session.refresh(user)
    assert user.total_posts == 4
