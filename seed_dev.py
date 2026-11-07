"""
Dev-only seed script: creates two approved users with profiles, a couple
posts, and a thread, then prints a ready-to-use access_token (JWT) for
each user so we can test authenticated pages with curl / a browser
without going through email verification.

Run with: FLASK_ENV=testing python3 seed_dev.py
(.env already sets FLASK_ENV=testing)
"""
import datetime
from app import app
from extensions import db
from models import User, StudentProfile, Post, Thread, ThreadMember
from routes.student.helpers import _build_access_token

with app.app_context():
    db.create_all()

    def get_or_create_user(username, email, name, department, class_name):
        user = User.query.filter_by(username=username).first()
        if user:
            return user
        user = User(
            username=username,
            email=email,
            pin="unused",
            name=name,
            role="student",
            status="approved",
            email_verified=True,
            reputation=120,
            reputation_level="Contributor",
            bio=f"Hi, I'm {name}! Studying {department}.",
            joined_at=datetime.datetime.utcnow(),
            last_active=datetime.datetime.utcnow(),
        )
        db.session.add(user)
        db.session.flush()

        profile = StudentProfile(
            user_id=user.id,
            pin="unused",
            username=username,
            full_name=name,
            department=department,
            class_name=class_name,
            status="active",
        )
        db.session.add(profile)
        db.session.commit()
        return user

    alice = get_or_create_user("alice_codes", "alice@test.com", "Alice Johnson", "Computer Science", "300L")
    bob = get_or_create_user("bob_math", "bob@test.com", "Bob Smith", "Mathematics", "200L")

    # Seed a couple posts by bob so alice can search for them / view bob's profile
    if Post.query.filter_by(student_id=bob.id).count() == 0:
        p1 = Post(
            student_id=bob.id,
            title="Need help with Linear Algebra eigenvalues",
            text_content="Can someone explain how to find eigenvalues for a 3x3 matrix? I keep getting confused with the characteristic polynomial.",
            post_type="question",
            department="Mathematics",
            tags=["linear-algebra", "eigenvalues", "math200"],
        )
        p2 = Post(
            student_id=bob.id,
            title="Study group for Calculus II final",
            text_content="Starting a study group for the Calculus II final exam next week. Anyone interested in joining?",
            post_type="discussion",
            department="Mathematics",
            tags=["calculus", "study-group"],
        )
        db.session.add_all([p1, p2])
        db.session.commit()

    if Post.query.filter_by(student_id=alice.id).count() == 0:
        p3 = Post(
            student_id=alice.id,
            title="Sharing my Data Structures notes",
            text_content="I made some detailed notes on binary trees and hash maps, happy to share if anyone needs them!",
            post_type="resource",
            department="Computer Science",
            tags=["data-structures", "cs201"],
        )
        db.session.add(p3)
        db.session.commit()

    # Seed a thread created by alice, with bob as a member
    if Thread.query.filter_by(creator_id=alice.id).count() == 0:
        t1 = Thread(
            creator_id=alice.id,
            title="CS Study Buddies",
            description="A thread for computer science students to collaborate on assignments and projects.",
            department="Computer Science",
            is_open=True,
            member_count=1,
        )
        db.session.add(t1)
        db.session.flush()
        db.session.add(ThreadMember(thread_id=t1.id, student_id=alice.id, role="admin"))
        db.session.commit()

    alice_token = _build_access_token(alice)
    bob_token = _build_access_token(bob)

    print("ALICE_TOKEN=" + alice_token)
    print("BOB_TOKEN=" + bob_token)
    print("alice username:", alice.username, "id:", alice.id)
    print("bob username:", bob.username, "id:", bob.id)
