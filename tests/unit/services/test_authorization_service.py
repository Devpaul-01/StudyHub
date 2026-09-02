"""
Tests for services/authorization_service.py.

Per UNIT_TEST_IMPLEMENTATION_PLAN.md §7.5: pure logic + simple ORM
attribute access, no dependencies to mock.
"""

import pytest

from services import authorization_service
from errors import AuthorizationError


def test_require_owner_passes_silently_when_owner_matches(db_session, make_user, make_post):
    owner = make_user()
    post = make_post(owner)

    # Should not raise.
    authorization_service.require_owner(post, "student_id", owner.id)


def test_require_owner_raises_on_mismatch(db_session, make_user, make_post):
    owner = make_user()
    other = make_user()
    post = make_post(owner)

    with pytest.raises(AuthorizationError):
        authorization_service.require_owner(post, "student_id", other.id)


def test_require_owner_default_message(db_session, make_user, make_post):
    owner = make_user()
    other = make_user()
    post = make_post(owner)

    with pytest.raises(AuthorizationError) as exc_info:
        authorization_service.require_owner(post, "student_id", other.id)
    assert exc_info.value.message == "Not authorized"


def test_require_owner_custom_message(db_session, make_user, make_post):
    owner = make_user()
    other = make_user()
    post = make_post(owner)

    with pytest.raises(AuthorizationError) as exc_info:
        authorization_service.require_owner(
            post, "student_id", other.id, message="You can only edit your own posts"
        )
    assert exc_info.value.message == "You can only edit your own posts"


def test_is_owner_true_on_match(db_session, make_user, make_post):
    owner = make_user()
    post = make_post(owner)
    assert authorization_service.is_owner(post, "student_id", owner.id) is True


def test_is_owner_false_on_mismatch_never_raises(db_session, make_user, make_post):
    owner = make_user()
    other = make_user()
    post = make_post(owner)
    # Must never raise, even on mismatch -- callers branch on this directly.
    assert authorization_service.is_owner(post, "student_id", other.id) is False
