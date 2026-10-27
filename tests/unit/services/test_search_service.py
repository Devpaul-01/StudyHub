"""
Tests for services/search_service.py.

This module is the single consolidated search implementation (per the
module's own docstring) backing GET /search/users, /search/posts,
/search/threads and /search/unified. It's DB-backed (real queries
against the test SQLite DB via the app/db_session fixtures), not a pure
function module, but still has zero Flask/request coupling itself.

Previously zero tests existed for this module at all — a genuine gap
given it's the only search implementation in the codebase.
"""

import datetime

import pytest

from services.search_service import search_users, search_posts, search_threads, search_all


# ============================================================================
# search_users
# ============================================================================

def test_search_users_excludes_the_viewer_themselves(app, db_session, make_user):
    viewer = make_user(username="viewer")
    other = make_user(username="other_match")

    result = search_users("", viewer_id=viewer.id)

    ids = [u["id"] for u in result["users"]]
    assert viewer.id not in ids
    assert other.id in ids


def test_search_users_excludes_non_approved_status(app, db_session, make_user):
    viewer = make_user(username="viewer")
    make_user(username="pending_user", status="pending")
    approved = make_user(username="approved_user", status="approved")

    result = search_users("", viewer_id=viewer.id)

    ids = [u["id"] for u in result["users"]]
    assert approved.id in ids
    usernames = [u["username"] for u in result["users"]]
    assert "pending_user" not in usernames


def test_search_users_query_matches_username_case_insensitively(app, db_session, make_user):
    viewer = make_user(username="viewer")
    make_user(username="Johnathan")
    make_user(username="somebody_else")

    result = search_users("johna", viewer_id=viewer.id)

    usernames = [u["username"] for u in result["users"]]
    assert "Johnathan" in usernames
    assert "somebody_else" not in usernames


def test_search_users_exclude_ids_filters_out_blocked_users(app, db_session, make_user):
    viewer = make_user(username="viewer")
    blocked = make_user(username="blocked_user")
    visible = make_user(username="visible_user")

    result = search_users("", exclude_ids=[blocked.id], viewer_id=viewer.id)

    ids = [u["id"] for u in result["users"]]
    assert blocked.id not in ids
    assert visible.id in ids


def test_search_users_connected_only_restricts_to_accepted_connections(
    app, db_session, make_user, make_connection
):
    viewer = make_user(username="viewer")
    connected = make_user(username="connected_user")
    stranger = make_user(username="stranger_user")
    make_connection(viewer, connected, status="accepted")

    result = search_users("", connected_only=True, viewer_id=viewer.id)

    ids = [u["id"] for u in result["users"]]
    assert connected.id in ids
    assert stranger.id not in ids


def test_search_users_reputation_min_filters_below_threshold(app, db_session, make_user):
    viewer = make_user(username="viewer")
    low_rep = make_user(username="low_rep", reputation=5)
    high_rep = make_user(username="high_rep", reputation=500)

    result = search_users("", reputation_min=100, viewer_id=viewer.id)

    ids = [u["id"] for u in result["users"]]
    assert high_rep.id in ids
    assert low_rep.id not in ids


def test_search_users_skills_filter_matches_contained_skill(app, db_session, make_user):
    viewer = make_user(username="viewer")
    matching = make_user(username="matches", skills=["python", "calculus"])
    non_matching = make_user(username="no_match", skills=["chemistry"])

    result = search_users("", skills=["Python"], viewer_id=viewer.id)

    ids = [u["id"] for u in result["users"]]
    assert matching.id in ids
    assert non_matching.id not in ids


def test_search_users_sort_reputation_orders_descending(app, db_session, make_user):
    viewer = make_user(username="viewer")
    low = make_user(username="low_user", reputation=10)
    high = make_user(username="high_user", reputation=999)

    result = search_users("", sort="reputation", viewer_id=viewer.id)

    ids_in_order = [u["id"] for u in result["users"]]
    assert ids_in_order.index(high.id) < ids_in_order.index(low.id)


def test_search_users_private_profile_hides_reputation(app, db_session, make_user):
    viewer = make_user(username="viewer")
    private_user = make_user(
        username="private_one",
        reputation=777,
        privacy_settings={"set_profile_private": True},
    )

    result = search_users("private_one", viewer_id=viewer.id)

    match = next(u for u in result["users"] if u["id"] == private_user.id)
    assert match["private"] is True
    assert match["reputation"] is None
    assert match["reputation_level"] is None


def test_search_users_connection_status_reflects_pending_sent(
    app, db_session, make_user, make_connection
):
    viewer = make_user(username="viewer")
    target = make_user(username="target_user")
    make_connection(viewer, target, status="pending", responded_at=None)

    result = search_users("target_user", viewer_id=viewer.id)

    match = next(u for u in result["users"] if u["id"] == target.id)
    assert match["connection_status"] == "pending_sent"


def test_search_users_per_page_capped_at_50(app, db_session, make_user):
    viewer = make_user(username="viewer")
    for i in range(3):
        make_user(username=f"bulk_{i}")

    result = search_users("", per_page=500, viewer_id=viewer.id)

    assert result["pagination"]["per_page"] == 50


def test_search_users_filters_applied_echoed_back(app, db_session, make_user):
    viewer = make_user(username="viewer")

    result = search_users(
        "abc", department="CS", class_level="200L", skills=["python", "sql"],
        sort="name", viewer_id=viewer.id,
    )

    assert result["filters_applied"] == {
        "query": "abc", "department": "CS", "class_level": "200L",
        "skills": "python,sql", "sort": "name",
    }


# ============================================================================
# search_posts
# ============================================================================

def test_search_posts_query_matches_title(app, db_session, make_user, make_post):
    author = make_user()
    make_post(author, title="Calculus homework help")
    make_post(author, title="Chemistry notes")

    result = search_posts("calculus")

    titles = [p["title"] for p in result["posts"]]
    assert "Calculus homework help" in titles
    assert "Chemistry notes" not in titles


def test_search_posts_filters_by_post_type(app, db_session, make_user, make_post):
    author = make_user()
    make_post(author, title="Q1", post_type="question")
    make_post(author, title="R1", post_type="resource")

    result = search_posts("", post_type="question")

    types = {p["post_type"] for p in result["posts"]}
    assert types == {"question"}


def test_search_posts_filters_by_solved_status(app, db_session, make_user, make_post):
    author = make_user()
    solved = make_post(author, title="Solved one", is_solved=True)
    unsolved = make_post(author, title="Unsolved one", is_solved=False)

    result = search_posts("", solved=True)

    ids = [p["id"] for p in result["posts"]]
    assert solved.id in ids
    assert unsolved.id not in ids


def test_search_posts_tags_filter_matches_contained_tag(app, db_session, make_user, make_post):
    author = make_user()
    matching = make_post(author, title="Tagged", tags=["exams", "physics"])
    non_matching = make_post(author, title="Untagged", tags=["chemistry"])

    result = search_posts("", tags=["Exams"])

    ids = [p["id"] for p in result["posts"]]
    assert matching.id in ids
    assert non_matching.id not in ids


def test_search_posts_excerpt_truncated_to_200_chars(app, db_session, make_user, make_post):
    author = make_user()
    long_text = "x" * 500
    make_post(author, title="Long post", text_content=long_text)

    result = search_posts("Long post")

    assert len(result["posts"][0]["excerpt"]) == 200


def test_search_posts_invalid_date_from_is_silently_ignored(app, db_session, make_user, make_post):
    author = make_user()
    make_post(author, title="A post")

    # Malformed ISO date must not raise — the function swallows ValueError.
    result = search_posts("", date_from="not-a-date")

    assert len(result["posts"]) == 1


def test_search_posts_includes_author_summary(app, db_session, make_user, make_post):
    author = make_user(username="author_name")
    make_post(author, title="Authored post")

    result = search_posts("Authored post")

    assert result["posts"][0]["author"]["username"] == "author_name"


def test_search_posts_sort_recent_orders_newest_first(app, db_session, make_user, make_post):
    author = make_user()
    older = make_post(author, title="Older", posted_at=datetime.datetime.utcnow() - datetime.timedelta(days=5))
    newer = make_post(author, title="Newer", posted_at=datetime.datetime.utcnow())

    result = search_posts("", sort="recent")

    ids_in_order = [p["id"] for p in result["posts"]]
    assert ids_in_order.index(newer.id) < ids_in_order.index(older.id)


# ============================================================================
# search_threads
# ============================================================================

def test_search_threads_query_matches_title(app, db_session, make_user, make_thread):
    creator = make_user()
    make_thread(creator, title="Physics study group")
    make_thread(creator, title="Art club")

    result = search_threads("physics", viewer_id=creator.id)

    titles = [t["title"] for t in result["threads"]]
    assert "Physics study group" in titles
    assert "Art club" not in titles


def test_search_threads_has_space_filters_full_threads(app, db_session, make_user, make_thread):
    creator = make_user()
    full = make_thread(creator, title="Full thread", member_count=10, max_members=10)
    open_one = make_thread(creator, title="Open thread", member_count=2, max_members=10)

    result = search_threads("", has_space=True, viewer_id=creator.id)

    ids = [t["id"] for t in result["threads"]]
    assert open_one.id in ids
    assert full.id not in ids


def test_search_threads_summary_excludes_full_member_list(app, db_session, make_user, make_thread):
    """
    Per the module's documented redesign: thread search returns
    summaries only, not a members_data array — this is the specific
    behavior change the module's docstring calls out as intentional.
    """
    creator = make_user()
    make_thread(creator, title="Summary thread")

    result = search_threads("Summary thread", viewer_id=creator.id)

    assert "members_data" not in result["threads"][0]
    assert "member_count" in result["threads"][0]


def test_search_threads_is_member_true_when_viewer_has_membership(
    app, db_session, make_user, make_thread, make_thread_member
):
    creator = make_user()
    member = make_user()
    thread = make_thread(creator, title="Joined thread")
    make_thread_member(thread, member)

    result = search_threads("Joined thread", viewer_id=member.id)

    assert result["threads"][0]["is_member"] is True


def test_search_threads_is_creator_true_for_the_thread_creator(app, db_session, make_user, make_thread):
    creator = make_user()
    thread = make_thread(creator, title="Mine")

    result = search_threads("Mine", viewer_id=creator.id)

    assert result["threads"][0]["is_creator"] is True


# ============================================================================
# search_all
# ============================================================================

def test_search_all_combines_all_three_entity_types(
    app, db_session, make_user, make_post, make_thread
):
    viewer = make_user(username="viewer")
    make_user(username="alpha_match")
    make_post(viewer, title="alpha_match post")
    make_thread(viewer, title="alpha_match thread")

    result = search_all("alpha_match", viewer_id=viewer.id)

    assert result["counts"]["users"] >= 1
    assert result["counts"]["posts"] >= 1
    assert result["counts"]["threads"] >= 1
    assert result["counts"]["total"] == (
        result["counts"]["users"] + result["counts"]["posts"] + result["counts"]["threads"]
    )


def test_search_all_limit_per_type_capped_at_10(app, db_session, make_user):
    viewer = make_user(username="viewer")
    for i in range(15):
        make_user(username=f"many_{i}")

    result = search_all("", limit_per_type=100, viewer_id=viewer.id)

    assert len(result["all"]["users"]) <= 10


def test_search_all_echoes_query_back(app, db_session, make_user):
    viewer = make_user(username="viewer")

    result = search_all("some query", viewer_id=viewer.id)

    assert result["query"] == "some query"
