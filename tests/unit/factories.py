"""
tests/unit/factories.py

Plain factory functions (not factory_boy — see
UNIT_TEST_IMPLEMENTATION_PLAN.md §9's own note declining that
dependency for this suite's size). Each takes db_session plus keyword
overrides, applies sensible defaults verified directly against the real
models.py field names/nullability, adds+flushes, and returns the
instance.

Deviation from the plan's own pasted factory code (documented): the
plan's make_user used `id(overrides)` to build a "unique" email/username
suffix. Python object ids can be reused once an object is garbage
collected, so two calls without an explicit email could in principle
collide against User.email's unique constraint — a real, if rare,
source of flaky tests. Replaced with a monotonic counter, which cannot
collide. This is a test-infrastructure correctness improvement, not a
production change.
"""

import datetime
import itertools
import secrets

from werkzeug.security import generate_password_hash

_counter = itertools.count(1)


def _next_n():
    return next(_counter)


def make_user(db_session, **overrides):
    from models import User

    n = _next_n()
    defaults = dict(
        username=f"user{n}",
        email=f"user{n}@example.com",
        pin=generate_password_hash("testpassword123"),
        name="Test User",
        role="student",
        status="approved",
        email_verified=True,
        reputation=0,
        login_streak=0,
        joined_at=datetime.datetime.utcnow(),
    )
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


def make_student_profile(db_session, user, **overrides):
    from models import StudentProfile

    defaults = dict(
        user_id=user.id,
        pin=user.pin,
        full_name=user.name,
        department=overrides.pop("department", None),
        class_name=overrides.pop("class_name", None),
    )
    defaults.update(overrides)
    profile = StudentProfile(**defaults)
    db_session.add(profile)
    db_session.flush()
    return profile


def make_post(db_session, author, **overrides):
    from models import Post

    defaults = dict(
        student_id=author.id,
        title="Test Post",
        text_content="Body",
        post_type="discussion",
        positive_reactions_count=0,
        posted_at=datetime.datetime.utcnow(),
    )
    defaults.update(overrides)
    post = Post(**defaults)
    db_session.add(post)
    db_session.flush()
    return post


def make_comment(db_session, post, author, **overrides):
    from models import Comment

    defaults = dict(
        post_id=post.id,
        student_id=author.id,
        text_content="A comment",
        is_solution=False,
        is_deleted=False,
        posted_at=datetime.datetime.utcnow(),
    )
    defaults.update(overrides)
    comment = Comment(**defaults)
    db_session.add(comment)
    db_session.flush()
    return comment


def make_connection(db_session, requester, receiver, **overrides):
    from models import Connection

    now = datetime.datetime.utcnow()
    defaults = dict(
        requester_id=requester.id,
        receiver_id=receiver.id,
        status="accepted",
        requested_at=now,
        responded_at=now,
    )
    defaults.update(overrides)
    conn = Connection(**defaults)
    db_session.add(conn)
    db_session.flush()
    return conn


def make_thread(db_session, creator, **overrides):
    from models import Thread

    defaults = dict(
        creator_id=creator.id,
        title="Test Thread",
        member_count=1,
        max_members=10,
        is_open=True,
    )
    defaults.update(overrides)
    thread = Thread(**defaults)
    db_session.add(thread)
    db_session.flush()
    return thread


def make_thread_member(db_session, thread, user, **overrides):
    from models import ThreadMember

    defaults = dict(thread_id=thread.id, student_id=user.id, role="member")
    defaults.update(overrides)
    member = ThreadMember(**defaults)
    db_session.add(member)
    db_session.flush()
    return member


def make_activity_feed_row(db_session, user, **overrides):
    from models import ActivityFeed

    now = datetime.datetime.utcnow()
    defaults = dict(
        user_id=user.id,
        activity_type="submitted_solution",
        activity_data={},
        created_at=now,
        expires_at=now + datetime.timedelta(hours=24),
    )
    defaults.update(overrides)
    row = ActivityFeed(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def make_ai_conversation(db_session, user, **overrides):
    from models import AIConversation

    defaults = dict(user_id=user.id, is_archived=False, total_messages=0)
    defaults.update(overrides)
    conv = AIConversation(**defaults)
    db_session.add(conv)
    db_session.flush()
    return conv


def make_password_reset_token(db_session, user, **overrides):
    from models import PasswordResetToken

    n = _next_n()
    defaults = dict(
        user_id=user.id,
        token=f"prt_{n}_{secrets.token_urlsafe(16)}",
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        used=False,
    )
    defaults.update(overrides)
    row = PasswordResetToken(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def make_email_verification_token(db_session, user, **overrides):
    from models import EmailVerificationToken

    n = _next_n()
    defaults = dict(
        user_id=user.id,
        token=f"evt_{n}_{secrets.token_urlsafe(16)}",
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=5),
        used=False,
    )
    defaults.update(overrides)
    row = EmailVerificationToken(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def make_refresh_token(db_session, user, **overrides):
    from models import RefreshToken

    n = _next_n()
    defaults = dict(
        user_id=user.id,
        token_hash=f"hash_{n}_{secrets.token_hex(16)}",
        family_id=f"family_{n}_{secrets.token_hex(8)}",
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(days=7),
        revoked=False,
    )
    defaults.update(overrides)
    row = RefreshToken(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def make_assignment(db_session, user, **overrides):
    from models import Assignment

    defaults = dict(
        user_id=user.id,
        title="Test Assignment",
        due_date=datetime.datetime.utcnow() + datetime.timedelta(days=1),
        difficulty="medium",
        status="not_started",
        estimated_hours=None,
        is_shared_for_help=False,
    )
    defaults.update(overrides)
    a = Assignment(**defaults)
    db_session.add(a)
    db_session.flush()
    return a


def make_message(db_session, sender, receiver, **overrides):
    from models import Message

    defaults = dict(
        sender_id=sender.id,
        receiver_id=receiver.id,
        body="Hello",
        sent_at=datetime.datetime.utcnow(),
        is_read=False,
        deleted_by_sender=False,
        deleted_by_receiver=False,
    )
    defaults.update(overrides)
    msg = Message(**defaults)
    db_session.add(msg)
    db_session.flush()
    return msg


def make_thread_message(db_session, thread, sender, **overrides):
    from models import ThreadMessage

    defaults = dict(
        thread_id=thread.id,
        sender_id=sender.id,
        text_content="Hello",
        sent_at=datetime.datetime.utcnow(),
        is_deleted=False,
        is_ai_response=False,
    )
    defaults.update(overrides)
    msg = ThreadMessage(**defaults)
    db_session.add(msg)
    db_session.flush()
    return msg
