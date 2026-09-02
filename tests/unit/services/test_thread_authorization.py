"""
Tests for services/thread_authorization.py.
"""

import pytest

from services import thread_authorization
from errors import AuthorizationError


class _FakeMembership:
    """Minimal stand-in with just the .role attribute is_moderator_or_creator
    reads -- no DB row needed for the pure-predicate tests."""
    def __init__(self, role):
        self.role = role


def test_is_moderator_or_creator_true_for_creator():
    assert thread_authorization.is_moderator_or_creator(_FakeMembership("creator")) is True


def test_is_moderator_or_creator_true_for_moderator():
    assert thread_authorization.is_moderator_or_creator(_FakeMembership("moderator")) is True


def test_is_moderator_or_creator_false_for_plain_member():
    assert thread_authorization.is_moderator_or_creator(_FakeMembership("member")) is False


def test_is_moderator_or_creator_false_and_no_raise_for_none():
    assert thread_authorization.is_moderator_or_creator(None) is False


def test_require_moderator_or_creator_returns_row_for_creator(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    thread = make_thread(creator)
    membership = make_thread_member(thread, creator, role="creator")

    result = thread_authorization.require_moderator_or_creator(thread.id, creator.id)
    assert result.id == membership.id


def test_require_moderator_or_creator_returns_row_for_moderator(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    mod_user = make_user()
    thread = make_thread(creator)
    membership = make_thread_member(thread, mod_user, role="moderator")

    result = thread_authorization.require_moderator_or_creator(thread.id, mod_user.id)
    assert result.id == membership.id


def test_require_moderator_or_creator_raises_for_plain_member(db_session, make_user, make_thread, make_thread_member):
    creator = make_user()
    plain_user = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, plain_user, role="member")

    with pytest.raises(AuthorizationError):
        thread_authorization.require_moderator_or_creator(thread.id, plain_user.id)


def test_require_moderator_or_creator_raises_for_non_member(db_session, make_user, make_thread):
    creator = make_user()
    stranger = make_user()
    thread = make_thread(creator)
    # stranger has no ThreadMember row at all

    with pytest.raises(AuthorizationError):
        thread_authorization.require_moderator_or_creator(thread.id, stranger.id)


def test_require_moderator_or_creator_same_error_type_member_vs_nonmember(
    db_session, make_user, make_thread, make_thread_member
):
    """Locks in current behavior: 'member but wrong role' and 'not a member
    at all' raise the identical exception type (both AuthorizationError),
    matching the plan's own note to verify rather than assume this."""
    creator = make_user()
    plain_user = make_user()
    stranger = make_user()
    thread = make_thread(creator)
    make_thread_member(thread, plain_user, role="member")

    with pytest.raises(AuthorizationError) as exc_a:
        thread_authorization.require_moderator_or_creator(thread.id, plain_user.id)
    with pytest.raises(AuthorizationError) as exc_b:
        thread_authorization.require_moderator_or_creator(thread.id, stranger.id)

    assert type(exc_a.value) is type(exc_b.value)
