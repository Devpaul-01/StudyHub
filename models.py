"""
StudyHub - Complete Merged Database Models
Combines all model files with no duplicates.

Changes vs original (Alembic will auto-detect these):
  - ThreadMessage: added `status` VARCHAR(20) NOT NULL DEFAULT 'sent'
  - ThreadMessage: added `ai_personality` VARCHAR(50) NULL
  - ThreadMessageReadReceipt: new table for per-user read receipts
  - ThreadMeetingNote: new table for AI-generated meeting notes
  - Thread: avatar column was already present — no change needed
  - Thread: added `meeting_notes` relationship
"""

import datetime
from enum import Enum
from flask_login import UserMixin
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.dialects import postgresql
from extensions import db

# ============================================================================
# JSONB MIGRATION  (Document 4 §3.4)
#
# Post.tags / User.skills move from plain db.JSON to a dialect-conditional
# JSONB: postgresql.JSONB in production, ordinary db.JSON everywhere else
# (SQLite local dev in particular). db.JSON().with_variant(postgresql.JSONB,
# "postgresql") is what makes this work unchanged in both environments —
# SQLite keeps working exactly as it does today; Postgres gets binary
# JSONB storage plus the ability to add GIN indexes for containment
# queries (see the GIN indexes added in each model's __table_args__ below).
#
# NOTE: editing this file alone does not change any already-deployed
# database's actual on-disk column type or add any index — an Alembic
# autogenerate (or equivalent manual DDL) still has to run against a real
# database for any of this to take effect there. JSON -> JSONB in
# particular is a full column rewrite on Postgres and needs a maintenance
# window per Document 4 §3.4 / Document 5 §3 item 9, not a routine
# autogenerate-and-apply.
# ============================================================================

JSONB_VARIANT = db.JSON().with_variant(postgresql.JSONB, "postgresql")

# ============================================================================
# STATUS ENUMS  (Document 3 §5)
#
# db.Enum(..., native_enum=False) at the SQLAlchemy level rather than a raw
# CheckConstraint string — this gets Python-side validation for free
# (assigning an invalid value raises immediately in the application,
# before it ever reaches the database) in addition to the database-level
# CHECK constraint that native_enum=False still generates.
#
# native_enum=False is deliberate: stores as VARCHAR + CHECK constraint
# rather than a native Postgres ENUM type, since native Postgres enums
# require an awkward `ALTER TYPE ... ADD VALUE` migration every time a
# new status value is added — exactly the kind of migration friction that
# discourages evolving a status set later. A CHECK-constraint-backed enum
# is altered with an ordinary `ALTER TABLE ... DROP/ADD CONSTRAINT`.
#
# Per your instruction, the columns below are updated directly (no
# separate migration file) — running `db.create_all()` / an Alembic
# autogenerate against this file will pick up the new CHECK constraints
# on next migration, same as any other model change.
# ============================================================================

class MessageStatus(str, Enum):
    SENT      = "sent"
    DELIVERED = "delivered"
    READ      = "read"


class ConnectionStatus(str, Enum):
    PENDING  = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED  = "blocked"


class ThreadJoinRequestStatus(str, Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    INVITED  = "invited"


class HomeworkSubmissionStatus(str, Enum):
    PENDING    = "pending"
    ACCEPTED   = "accepted"
    COMPLETED  = "completed"
    DECLINED   = "declined"
    CANCELLED  = "cancelled"


class StudySessionCalendarStatus(str, Enum):
    PENDING    = "pending"
    CONFIRMED  = "confirmed"
    DECLINED   = "declined"
    CANCELLED  = "cancelled"
    COMPLETED  = "completed"


class ThreadMessageStatus(str, Enum):
    SENT      = "sent"
    DELIVERED = "delivered"
    READ      = "read"

# ============================================================================
# CORE USER MODELS
# ============================================================================

class LeaderboardSnapshot(db.Model):
    __tablename__ = "leaderboard_snapshots"

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    snapshot_type   = db.Column(db.String(20), nullable=False, default="weekly")  # "weekly" | "monthly"
    global_rank     = db.Column(db.Integer, nullable=True)
    department_rank = db.Column(db.Integer, nullable=True)
    score           = db.Column(db.Integer, nullable=False, default=0)  # User.reputation at snapshot time
    created_at      = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)

    user = db.relationship("User", backref="leaderboard_snapshots")

    __table_args__ = (
        db.Index("idx_lb_snap_user_id",        "user_id"),
        db.Index("idx_lb_snap_type_date",       "snapshot_type", "created_at"),
        db.Index("idx_lb_snap_rank",            "global_rank"),
        db.Index("idx_lb_snap_user_type_date",  "user_id", "snapshot_type", "created_at"),
    )

    def __repr__(self):
        return f"<LeaderboardSnapshot {self.id}: User {self.user_id} rank={self.global_rank} ({self.snapshot_type})>"

    def to_dict(self):
        return {
            "id":              self.id,
            "user_id":         self.user_id,
            "snapshot_type":   self.snapshot_type,
            "global_rank":     self.global_rank,
            "department_rank": self.department_rank,
            "score":           self.score,
            "created_at":      self.created_at.isoformat() if self.created_at else None,
        }


class ActivityFeed(db.Model):
    """
    Activity feed for homework-related activities.
    Stores recent activities from connections (expires after 24 hours).
    """
    __tablename__ = 'activity_feed'

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    activity_type = db.Column(db.String(50), nullable=False)
    activity_data = db.Column(db.JSON, nullable=True)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)
    expires_at    = db.Column(db.DateTime, nullable=False)

    user = db.relationship('User', backref='activities')

    __table_args__ = (
        db.Index('idx_activity_user_id',    'user_id'),
        db.Index('idx_activity_type',       'activity_type'),
        db.Index('idx_activity_created_at', 'created_at'),
        db.Index('idx_activity_expires_at', 'expires_at'),
    )

    def __repr__(self):
        return f'<ActivityFeed {self.id}: {self.activity_type} by User {self.user_id}>'

    def to_dict(self):
        return {
            'id':            self.id,
            'user_id':       self.user_id,
            'activity_type': self.activity_type,
            'activity_data': self.activity_data,
            'created_at':    self.created_at.isoformat() if self.created_at else None,
            'expires_at':    self.expires_at.isoformat() if self.expires_at else None,
        }


# ============================================================================
# WEEKLY CHAMPION MODEL
# ============================================================================

class WeeklyChampion(db.Model):
    """
    Weekly champions for homework help.
    Stores top helpers by subject, overall, and speed.
    """
    __tablename__ = 'weekly_champions'

    id                        = db.Column(db.Integer, primary_key=True)
    user_id                   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    subject                   = db.Column(db.String(100), nullable=True)
    help_count                = db.Column(db.Integer, nullable=False, default=0)
    week_start                = db.Column(db.Date, nullable=False)
    week_end                  = db.Column(db.Date, nullable=False)
    avg_response_time_minutes = db.Column(db.String(50), nullable=True)
    champion_type             = db.Column(db.String(50), nullable=False)
    created_at                = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)

    user = db.relationship('User', backref='championships')

    __table_args__ = (
        db.Index('idx_champion_user_id', 'user_id'),
        db.Index('idx_champion_week',    'week_start', 'week_end'),
        db.Index('idx_champion_type',    'champion_type'),
        db.Index('idx_champion_subject', 'subject'),
    )

    def __repr__(self):
        return f'<WeeklyChampion {self.id}: User {self.user_id} - {self.champion_type}>'

    def to_dict(self):
        return {
            'id':           self.id,
            'user_id':      self.user_id,
            'user_name':    self.user.name if self.user else None,
            'user_avatar':  self.user.avatar_url if self.user else None,
            'subject':      self.subject,
            'help_count':   self.help_count,
            'week_start':   self.week_start.isoformat() if self.week_start else None,
            'week_end':     self.week_end.isoformat() if self.week_end else None,
            'champion_type':self.champion_type,
            'created_at':   self.created_at.isoformat() if self.created_at else None,
        }


class HelpRequest(db.Model):
    __tablename__ = "help_requests"

    id             = db.Column(db.Integer, primary_key=True)
    requester_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    subject        = db.Column(db.String(100), nullable=False)
    message        = db.Column(db.String(300))
    status         = db.Column(db.String(20), default="active")
    broadcast_sent = db.Column(db.Boolean, default=False)
    volunteers     = db.Column(MutableList.as_mutable(db.JSON), default=list)
    created_at     = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    expires_at     = db.Column(db.DateTime)

    requester = db.relationship("User", foreign_keys=[requester_id])

    def is_expired(self):
        return datetime.datetime.utcnow() > self.expires_at if self.expires_at else False

    def __repr__(self):
        return f"<HelpRequest {self.id}: {self.subject} by User {self.requester_id}>"


class LiveStudySession(db.Model):
    __tablename__ = "live_study_sessions"

    id      = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    session_key = db.Column(db.String(100), unique=True, nullable=False)
    title       = db.Column(db.String(200), default="Study Session")
    subject     = db.Column(db.String(100))
    resources   = db.Column(MutableList.as_mutable(db.JSON), default=list)

    user1_timer_state = db.Column(db.JSON, default=dict)
    user2_timer_state = db.Column(db.JSON, default=dict)

    notepad_content      = db.Column(db.Text, default="# Study Notes\n\n")
    notepad_version      = db.Column(db.Integer, default=1)
    last_notepad_edit_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    last_notepad_edit_at = db.Column(db.DateTime)

    timer_started_at    = db.Column(db.DateTime)
    timer_paused_at     = db.Column(db.DateTime)
    timer_total_seconds = db.Column(db.Integer, default=0)
    timer_is_running    = db.Column(db.Boolean, default=False)
    timer_owner_id      = db.Column(db.Integer, db.ForeignKey('users.id'))

    ai_messages = db.Column(MutableList.as_mutable(db.JSON), default=list)

    status          = db.Column(db.String(20), default="active")
    session_log     = db.Column(db.JSON)
    started_at      = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    ended_at        = db.Column(db.DateTime)
    last_activity   = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    topics_covered  = db.Column(db.JSON, default=list)
    problems_solved = db.Column(db.Integer, default=0)

    # ── MIGRATION-001: missing columns ────────────────────────────────────────
    total_duration_seconds     = db.Column(db.Integer, nullable=False, default=0)
    session_goal               = db.Column(db.Text, nullable=True)
    target_count               = db.Column(db.Integer, nullable=False, default=0)
    completed_count            = db.Column(db.Integer, nullable=False, default=0)
    quick_notes                = db.Column(db.Text, nullable=True)
    assignment_id              = db.Column(db.Integer, db.ForeignKey("assignments.id"), nullable=True)
    pomodoro_cycles_completed  = db.Column(db.Integer, nullable=False, default=0)
    current_pomodoro_state     = db.Column(db.String(20), nullable=True)
    rating_user1               = db.Column(db.String(20), nullable=True)
    rating_user2               = db.Column(db.String(20), nullable=True)


class ConversationAnalytics(db.Model):
    """AI-powered analytics for conversations."""
    __tablename__ = 'conversation_analytics'

    id               = db.Column(db.Integer, primary_key=True)
    conversation_key = db.Column(db.String(100), unique=True, nullable=False)
    user1_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user2_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    total_messages      = db.Column(db.Integer, default=0)
    messages_this_week  = db.Column(db.Integer, default=0)
    messages_last_week  = db.Column(db.Integer, default=0)
    subjects_discussed  = db.Column(db.JSON)
    top_subjects        = db.Column(db.JSON)

    first_message_at  = db.Column(db.DateTime)
    last_message_at   = db.Column(db.DateTime)
    most_active_day   = db.Column(db.String(20))
    most_active_hour  = db.Column(db.Integer)

    total_study_sessions  = db.Column(db.Integer, default=0)
    total_study_time_hours= db.Column(db.Float, default=0)
    engagement_score      = db.Column(db.Float, default=0)
    learning_score        = db.Column(db.Float, default=0)
    avg_response_time_minutes = db.Column(db.Float)

    last_computed_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    needs_recompute  = db.Column(db.Boolean, default=False)

    user1 = db.relationship('User', foreign_keys=[user1_id])
    user2 = db.relationship('User', foreign_keys=[user2_id])

    def __repr__(self):
        return f'<ConversationAnalytics {self.conversation_key}>'


class StudySessionCalendar(db.Model):
    """Scheduled study sessions with confirmation workflow."""
    __tablename__ = 'study_session_calendar'

    id           = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    receiver_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    title           = db.Column(db.String(200), nullable=False)
    subject         = db.Column(db.String(100))
    description     = db.Column(db.Text)
    proposed_times  = db.Column(db.JSON)
    confirmed_time  = db.Column(db.DateTime)
    duration_minutes= db.Column(db.Integer, default=60)
    status          = db.Column(db.Enum(StudySessionCalendarStatus, native_enum=False, values_callable=lambda e: [m.value for m in e]), default=StudySessionCalendarStatus.PENDING.value)

    created_at   = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    confirmed_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    requester_notes     = db.Column(db.Text)
    requester_resources = db.Column(db.JSON)
    receiver_notes      = db.Column(db.Text)
    receiver_resources  = db.Column(db.JSON)
    decline_reason      = db.Column(db.Text)

    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'))

    reminder_15min_sent  = db.Column(db.Boolean, default=False)
    reminder_1hour_sent  = db.Column(db.Boolean, default=False)

    # ── MIGRATION-001: missing columns ────────────────────────────────────────
    cancelled_at  = db.Column(db.DateTime, nullable=True)
    cancelled_by  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    cancel_reason = db.Column(db.Text, nullable=True)
    template_used = db.Column(db.String(50), nullable=True)

    requester = db.relationship('User', foreign_keys=[requester_id])
    receiver  = db.relationship('User', foreign_keys=[receiver_id])

    def __repr__(self):
        return f'<StudySessionCalendar {self.id}: {self.title}>'


class Assignment(db.Model):
    """Personal assignment tracking with optional sharing for help."""
    __tablename__ = "assignments"

    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    resources = db.Column(db.JSON, default=list)

    title       = db.Column(db.String(200), nullable=False)
    subject     = db.Column(db.String(100), index=True)
    description = db.Column(db.Text)

    due_date           = db.Column(db.DateTime, nullable=False, index=True)
    estimated_hours    = db.Column(db.Float)
    time_spent_minutes = db.Column(db.Integer, default=0)

    difficulty     = db.Column(db.String(20), default="medium")
    status         = db.Column(db.String(20), default="not_started", index=True)
    priority_score = db.Column(db.Float, default=0)

    is_shared_for_help = db.Column(db.Boolean, default=False, index=True)

    created_at   = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    user = db.relationship("User", backref="assignments")

    def calculate_priority(self):
        now            = datetime.datetime.utcnow()
        hours_until_due = (self.due_date - now).total_seconds() / 3600

        if hours_until_due < 0:
            urgency_score = 100
        elif hours_until_due < 24:
            urgency_score = 90
        elif hours_until_due < 48:
            urgency_score = 70
        elif hours_until_due < 168:
            urgency_score = 50
        else:
            urgency_score = 30

        difficulty_multiplier = {"easy": 1.0, "medium": 1.3, "hard": 1.6}.get(self.difficulty, 1.3)
        status_multiplier     = {"not_started": 1.2, "in_progress": 1.0, "completed": 0.1}.get(self.status, 1.0)
        hours_bonus           = min((self.estimated_hours or 0) * 2, 20)
        self.priority_score   = (urgency_score * difficulty_multiplier * status_multiplier) + hours_bonus

    def __repr__(self):
        return f"<Assignment {self.id}: {self.title} - {self.status}>"


class HomeworkSubmission(db.Model):
    """Represents one person helping another with an assignment."""
    __tablename__ = "homework_submissions"

    id            = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignments.id"), nullable=True, index=True)
    requester_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    helper_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    title       = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    subject     = db.Column(db.String(100), index=True)
    difficulty  = db.Column(db.String(20))

    solution_text      = db.Column(db.Text)
    solution_resources = db.Column(MutableList.as_mutable(db.JSON), default=list)
    submitted_at       = db.Column(db.DateTime)

    feedback_text      = db.Column(db.Text)
    feedback_resources = db.Column(MutableList.as_mutable(db.JSON), default=list)
    feedback_at        = db.Column(db.DateTime)

    status               = db.Column(db.Enum(HomeworkSubmissionStatus, native_enum=False, values_callable=lambda e: [m.value for m in e]), default=HomeworkSubmissionStatus.PENDING.value, index=True)
    reaction_type        = db.Column(db.String(50))
    response_time_seconds= db.Column(db.Integer, nullable=True)
    reaction_at          = db.Column(db.DateTime, nullable=True)

    created_at        = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)
    is_marked_helpful = db.Column(db.Boolean, default=False)
    study_session_id  = db.Column(db.Integer, db.ForeignKey("live_study_sessions.id"))

    # ── MIGRATION-001: missing columns ────────────────────────────────────────
    feedback_rating = db.Column(db.Integer, nullable=True)

    requester  = db.relationship("User", foreign_keys=[requester_id])
    helper     = db.relationship("User", foreign_keys=[helper_id])
    assignment = db.relationship("Assignment", backref="help_submissions")

    def __repr__(self):
        return f"<HomeworkSubmission {self.id}: {self.title} - {self.status}>"


class User(UserMixin, db.Model):
    """Main user account - handles authentication and basic identity."""
    __tablename__ = "users"

    id                = db.Column(db.Integer, primary_key=True)
    in_study_session  = db.Column(db.Boolean(), default=False)
    username          = db.Column(db.String(50), unique=True, nullable=True, index=True)
    email             = db.Column(db.String(120), unique=True, nullable=False, index=True)
    pin               = db.Column(db.String(200), nullable=False)
    fcm_token         = db.Column(db.String(500), nullable=True)

    name = db.Column(db.String(100), nullable=False)
    bio  = db.Column(db.String(500))
    avatar = db.Column(db.String(200))

    role   = db.Column(db.String(20), default="student")
    status = db.Column(db.String(30), default="pending_verification")
    email_verified = db.Column(db.Boolean, default=False)

    reputation       = db.Column(db.Integer, default=0, index=True)
    reputation_level = db.Column(db.String(20), default="Newbie")

    last_active   = db.Column(db.DateTime)
    login_streak  = db.Column(db.Integer, default=0)
    total_posts   = db.Column(db.Integer, default=0)
    total_helpful = db.Column(db.Integer, default=0)

    # Document 4 §3.4: JSONB on Postgres (dialect-conditional), plain JSON
    # on SQLite. GIN index added below in __table_args__.
    skills         = db.Column(MutableList.as_mutable(JSONB_VARIANT), default=list)
    learning_goals = db.Column(MutableList.as_mutable(db.JSON), default=list)
    study_schedule = db.Column(MutableDict.as_mutable(db.JSON), default=dict)

    privacy_settings      = db.Column(MutableDict.as_mutable(db.JSON), default=dict)
    notification_settings = db.Column(MutableDict.as_mutable(db.JSON), default=dict)
    connection_settings   = db.Column(MutableDict.as_mutable(db.JSON), default=dict)

    help_streak_current      = db.Column(db.Integer, nullable=False, default=0)
    help_streak_longest      = db.Column(db.Integer, nullable=False, default=0)
    help_streak_last_updated = db.Column(db.DateTime, nullable=True)
    help_streak_frozen       = db.Column(db.Boolean, nullable=False, default=False)

    total_helps_given    = db.Column(db.Integer, nullable=False, default=0)
    total_helps_received = db.Column(db.Integer, nullable=False, default=0)
    first_responder_count= db.Column(db.Integer, nullable=False, default=0)

    weekly_helps_count      = db.Column(db.Integer, nullable=False, default=0)
    weekly_helps_last_reset = db.Column(db.Date, nullable=True)

    user_metadata = db.Column(
        'metadata',
        MutableDict.as_mutable(db.JSON),
        default=lambda: {
            "search_history": [],
            "recent_views": [],
            "feed_preferences": {
                "default_filter": "all",
                "posts_per_page": 20,
                "show_images_preview": True
            },
            "ai_usage": {
                "total_requests": 0,
                "last_request_at": None
            }
        }
    )
    bookmark_folders = db.Column(MutableList.as_mutable(db.JSON), default=list)

    joined_at  = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_login = db.Column(db.DateTime)

    # Relationships
    student_profile    = db.relationship('StudentProfile', backref='user', uselist=False, cascade="all, delete-orphan")
    waitlist_signup    = db.relationship('WaitlistSignup', backref='user', uselist=False, cascade="all, delete-orphan")
    ai_usage_quota     = db.relationship('AIUsageQuota', backref='user', uselist=False, cascade="all, delete-orphan")
    posts              = db.relationship("Post", backref="author", lazy="dynamic", cascade="all, delete-orphan")
    comments           = db.relationship("Comment", backref="author", lazy="dynamic", cascade="all, delete-orphan")
    onboarding_details = db.relationship('OnboardingDetails', backref='user', uselist=False, cascade="all, delete-orphan")
    threads_created    = db.relationship("Thread", foreign_keys="Thread.creator_id", backref="creator", lazy="dynamic")
    badges             = db.relationship("UserBadge", backref="user", lazy="dynamic", cascade="all, delete-orphan")
    bookmark_relations = db.relationship("Bookmark", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    __table_args__ = (
        # Document 4 §3.4: GIN index on skills for containment queries
        # (Postgres-specific — see the identical note on Post.__table_args__).
        db.Index("idx_users_skills_gin", "skills", postgresql_using="gin"),
    )

    @property
    def is_active(self):
        return (
            self.email_verified and
            self.status == "approved" and
            self.username is not None and
            self.pin != "PENDING_VERIFICATION"
        )

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

    def update_reputation_level(self):
        """
        Calculate and update reputation level.

        H-8 fix: previously reimplemented the reputation-tier boundaries
        independently of badges.py/leaderboard.py/reputation.py (all three
        of which are now consolidated into services.reputation_levels).
        This used strict "<" comparisons where the other three used an
        inclusive min/max range table, and they disagreed at exactly
        reputation == 1000 (this method said "Master"; the shared table says
        "Expert", since Expert's range is 501-1000 inclusive). Delegating to
        the shared table removes that discrepancy for good.

        Imported locally (not at module level) to avoid a circular import —
        services/*.py imports models.py, so models.py can't import
        services.reputation_levels at module scope.
        """
        from services.reputation_levels import get_reputation_level_name
        self.reputation_level = get_reputation_level_name(self.reputation)

    def __repr__(self):
        return f"<User @{self.username or self.email}>"


class StudentProfile(db.Model):
    """Extended profile info specific to students."""
    __tablename__ = "student_profiles"

    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    pin      = db.Column(db.String(200), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=True, index=True)

    full_name  = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(100), nullable=True, index=True)
    class_name = db.Column(db.String(50),  nullable=True, index=True)

    date_of_birth    = db.Column(db.Date, nullable=True)
    guardian_name    = db.Column(db.String(120))
    guardian_contact = db.Column(db.String(50))

    status        = db.Column(db.String(50), default="active")
    registered_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        db.Index("idx_student_profiles_dept_user", "department", "user_id"),
    )

    def __repr__(self):
        return f"<Student @{self.user.username if self.user else 'Unknown'} - {self.department}>"


class OnboardingDetails(db.Model):
    __tablename__ = "onboarding_details"

    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    email   = db.Column(db.String(120), unique=True, nullable=False)

    department   = db.Column(db.String(50))
    class_level  = db.Column(db.String(20))

    subjects          = db.Column(MutableList.as_mutable(db.JSON), default=list)
    learning_style    = db.Column(db.String(300))
    study_preferences = db.Column(MutableList.as_mutable(db.JSON), default=list)
    help_subjects     = db.Column(MutableList.as_mutable(db.JSON), default=list)
    strong_subjects   = db.Column(MutableList.as_mutable(db.JSON), default=list)
    study_schedule    = db.Column(MutableDict.as_mutable(db.JSON), default=dict)

    session_length = db.Column(db.String(100))
    last_updated   = db.Column(db.DateTime)


class WaitlistSignup(db.Model):
    __tablename__ = "waitlist_signups"

    id               = db.Column(db.Integer, primary_key=True)
    email            = db.Column(db.String(120), unique=True, nullable=False)
    user_id          = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True)
    referral_code    = db.Column(db.String(20), unique=True, nullable=False)
    referred_by      = db.Column(db.String(20))
    referral_count   = db.Column(db.Integer, default=0)
    waitlist_position= db.Column(db.Integer)
    signup_date      = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    status           = db.Column(db.String(20), default='waiting')


# ============================================================================
# CONTENT MODELS
# ============================================================================

class Post(db.Model):
    """Main content type - questions, discussions, resources."""
    __tablename__ = "posts"

    id          = db.Column(db.Integer, primary_key=True)
    student_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    title        = db.Column(db.String(200), nullable=False)
    text_content = db.Column(db.Text)
    post_type    = db.Column(db.String(50), nullable=False, default="discussion", index=True)

    resources  = db.Column(MutableList.as_mutable(db.JSON))
    department = db.Column(db.String(100), index=True)
    # Document 4 §3.4: JSONB on Postgres (dialect-conditional — see
    # JSONB_VARIANT at module scope), plain JSON on SQLite. GIN index
    # (below, in __table_args__) is the actual performance payoff — tag
    # containment queries go from a sequential scan to an index scan on
    # Postgres. GIN indexes are Postgres-specific; this is a no-op on
    # SQLite (see __table_args__ note).
    tags       = db.Column(MutableList.as_mutable(JSONB_VARIANT), default=list)

    positive_reactions_count = db.Column(db.Integer, default=0)
    dislikes_count           = db.Column(db.Integer, default=0)
    views_count              = db.Column(db.Integer, default=0)
    comments_count           = db.Column(db.Integer, default=0)
    bookmark_count           = db.Column(db.Integer, default=0)
    helpful_reactions_count  = db.Column(db.Integer, default=0)

    thread_enabled = db.Column(db.Boolean, default=False)

    is_solved = db.Column(db.Boolean, default=False)
    is_pinned = db.Column(db.Boolean, default=False)
    is_locked = db.Column(db.Boolean, default=False)

    posted_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)
    edited_at = db.Column(db.DateTime)
    solved_at = db.Column(db.DateTime)

    comments  = db.relationship("Comment",      backref="post", lazy="dynamic", cascade="all, delete-orphan")
    threads   = db.relationship("Thread",        backref="post", lazy="dynamic", cascade="all, delete-orphan")
    reactions = db.relationship("PostReaction",  backref="post", lazy="dynamic", cascade="all, delete-orphan")
    bookmarks = db.relationship("Bookmark",      backref="post", lazy="dynamic", cascade="all, delete-orphan")
    # H-3 fix: these were previously undeclared, so an ORM-level
    # db.session.delete(post) never cascaded to them and they were left as
    # orphaned rows referencing a deleted post_id. (routes/student/posts.py's
    # delete_post() also explicitly bulk-deletes these for the same reason —
    # that explicit cleanup remains in place as a safety net for any bulk
    # `.query.filter(...).delete()` code path that bypasses the ORM cascade
    # below entirely.)
    views     = db.relationship("PostView",      backref="post", lazy="dynamic", cascade="all, delete-orphan")
    follows   = db.relationship("PostFollow",     backref="post", lazy="dynamic", cascade="all, delete-orphan")

    __table_args__ = (
        # Document 4 §3.4: GIN index on tags for containment queries.
        # postgresql_using="gin" makes this Postgres-specific by
        # construction — Alembic/SQLAlchemy will skip it (or it must be
        # excluded from a SQLite create_all path) on other dialects,
        # since GIN is not a SQLite index type.
        db.Index("idx_posts_tags_gin", "tags", postgresql_using="gin"),
    )

    def __repr__(self):
        return f"<Post {self.id}: {self.title[:30]}>"


class PostView(db.Model):
    """Track post views by users."""
    __tablename__ = "post_views"

    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    post_id   = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    viewed_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'post_id', 'viewed_at', name='unique_daily_view'),
    )


class Comment(db.Model):
    """Comments on posts - supports nested replies."""
    __tablename__ = "comments"

    id         = db.Column(db.Integer, primary_key=True)
    post_id    = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # Document 4 §3.2: CASCADE — deleting a top-level comment should remove
    # its replies. This matches the existing ORM-level
    # cascade="all, delete-orphan" on Comment.replies below; adding
    # ondelete="CASCADE" here enforces the same behavior at the DB level
    # too, as defense-in-depth for any bulk delete that bypasses the ORM.
    parent_id  = db.Column(db.Integer, db.ForeignKey("comments.id", ondelete="CASCADE"), nullable=True)

    text_content = db.Column(db.Text, nullable=False)
    resources    = db.Column(MutableList.as_mutable(db.JSON))

    likes_count   = db.Column(db.Integer, default=0)
    helpful_count = db.Column(db.Integer, default=0)
    replies_count = db.Column(db.Integer, default=0)
    depth_level   = db.Column(db.Integer, default=0, index=True)

    is_solution = db.Column(db.Boolean, default=False)
    is_deleted  = db.Column(db.Boolean, default=False)

    posted_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    edited_at = db.Column(db.DateTime)

    replies = db.relationship(
        "Comment",
        backref=db.backref("parent", remote_side=[id]),
        cascade="all, delete-orphan",
        lazy="dynamic"
    )
    likes = db.relationship("CommentLike", backref="comment", lazy="dynamic", cascade="all, delete-orphan")

    @property
    def direct_replies(self):
        return Comment.query.filter_by(
            parent_id=self.id, is_deleted=False
        ).order_by(Comment.posted_at.asc()).all()

    def __repr__(self):
        return f"<Comment {self.id} on Post {self.post_id}>"


class CommentHelpfulMark(db.Model):
    """Track which users marked comments as helpful."""
    __tablename__ = "comment_helpful_marks"

    id         = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey("comments.id"), nullable=False, index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"),    nullable=False, index=True)
    marked_at  = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('comment_id', 'user_id', name='unique_helpful_mark'),
    )


# ============================================================================
# BOOKMARKS
# ============================================================================

class Bookmark(db.Model):
    """Save posts for later."""
    __tablename__ = "bookmarks"

    id         = db.Column(db.Integer, primary_key=True)
    post_id    = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    folder_id = db.Column(db.Integer, db.ForeignKey("bookmark_folders.id"), nullable=True, index=True)
    folder    = db.Column(db.String(100), default="Saved", index=True)

    notes          = db.Column(db.Text)
    tags           = db.Column(MutableList.as_mutable(db.JSON), default=list)
    bookmarked_at  = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_accessed_at = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint('post_id', 'student_id', name='unique_bookmark'),
    )

    def __repr__(self):
        return f"<Bookmark: User {self.student_id} -> Post {self.post_id}>"


class BookmarkFolder(db.Model):
    """Organized bookmark folders with metadata."""
    __tablename__ = "bookmark_folders"

    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    name        = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300))
    icon        = db.Column(db.String(50), default="📁")
    color       = db.Column(db.String(20), default="#6B7280")

    position   = db.Column(db.Integer, default=0)
    is_default = db.Column(db.Boolean, default=False)

    bookmark_count = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.datetime.utcnow)

    bookmarks = db.relationship("Bookmark", backref="folder_obj", lazy="dynamic", cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint('user_id', 'name', name='unique_folder_per_user'),
    )

    def __repr__(self):
        return f"<BookmarkFolder {self.id}: {self.name}>"


# ============================================================================
# THREADS
# ============================================================================

class Thread(db.Model):
    """Private collaboration groups."""
    __tablename__ = "threads"

    id         = db.Column(db.Integer, primary_key=True)
    post_id    = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=True, index=True)
    creator_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    title       = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    avatar      = db.Column(db.String(300), nullable=True)

    is_open           = db.Column(db.Boolean, default=True)
    max_members       = db.Column(db.Integer, default=10)
    requires_approval = db.Column(db.Boolean, default=True)

    department = db.Column(db.String(100), index=True)
    tags       = db.Column(MutableList.as_mutable(db.JSON), default=list)

    member_count  = db.Column(db.Integer, default=1)
    message_count = db.Column(db.Integer, default=0)

    created_at    = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_activity = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    members      = db.relationship("ThreadMember",      backref="thread", lazy="dynamic", cascade="all, delete-orphan")
    join_requests= db.relationship("ThreadJoinRequest", backref="thread", lazy="dynamic", cascade="all, delete-orphan")
    messages     = db.relationship("ThreadMessage",     backref="thread", lazy="dynamic", cascade="all, delete-orphan")
    meeting_notes= db.relationship("ThreadMeetingNote", backref="thread", lazy="dynamic", cascade="all, delete-orphan")

    __table_args__ = (
        # Supports listing open threads sorted by most recent activity
        db.Index("idx_threads_open_activity", "is_open", db.text("last_activity DESC")),
    )

    def __repr__(self):
        return f"<Thread {self.id}: {self.title}>"


class ThreadMember(db.Model):
    """Approved members of a thread."""
    __tablename__ = "thread_members"

    id        = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, db.ForeignKey("threads.id"), nullable=False, index=True)
    student_id= db.Column(db.Integer, db.ForeignKey("users.id"),   nullable=False, index=True)

    role = db.Column(db.String(20), default="member")

    joined_at     = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_read_at  = db.Column(db.DateTime)
    messages_sent = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.UniqueConstraint('thread_id', 'student_id', name='unique_thread_member'),
        # Supports unread-count queries: find messages after last_read_at for a member
        db.Index("idx_thread_member_last_read", "thread_id", "student_id", "last_read_at"),
    )

    def __repr__(self):
        return f"<ThreadMember: User {self.student_id} in Thread {self.thread_id}>"


class ThreadJoinRequest(db.Model):
    """Pending requests to join threads."""
    __tablename__ = "thread_join_requests"

    id           = db.Column(db.Integer, primary_key=True)
    thread_id    = db.Column(db.Integer, db.ForeignKey("threads.id"), nullable=False, index=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("users.id"),   nullable=False, index=True)

    message     = db.Column(db.Text)
    status      = db.Column(db.Enum(ThreadJoinRequestStatus, native_enum=False, values_callable=lambda e: [m.value for m in e]), default=ThreadJoinRequestStatus.PENDING.value, index=True)
    reviewed_at = db.Column(db.DateTime)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    __table_args__ = (
        db.UniqueConstraint('thread_id', 'requester_id', name='unique_join_request'),
    )

    def __repr__(self):
        return f"<JoinRequest: User {self.requester_id} -> Thread {self.thread_id} [{self.status}]>"


class ThreadMessage(db.Model):
    """Chat messages inside threads — includes replies, pins, and AI messages."""
    __tablename__ = "thread_messages"

    id        = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, db.ForeignKey("threads.id"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"),   nullable=False, index=True)

    text_content = db.Column(db.Text, nullable=False, default="")

    # Attachment (single file per message)
    # NOTE: old `attachment` column (String 255) is superseded by attachment_url (String 500)
    attachment      = db.Column(db.String(255), nullable=True)   # legacy — kept for migration safety
    attachment_url  = db.Column(db.String(500), nullable=True)
    attachment_name = db.Column(db.String(255), nullable=True)
    attachment_type = db.Column(db.String(50),  nullable=True)   # "image" | "video" | "document"
    attachment_size = db.Column(db.Integer,      nullable=True)  # bytes

    # Reply thread
    reply_to_id = db.Column(
        db.Integer,
        db.ForeignKey("thread_messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Pin state
    is_pinned    = db.Column(db.Boolean, default=False, nullable=False)
    pinned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Learnora / bot flag
    is_ai_response = db.Column(db.Boolean, default=False, nullable=False)
    # Which AI personality sent this (null for human messages or default Learnora)
    ai_personality = db.Column(db.String(50), nullable=True)

    # Edit / delete state
    is_edited  = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)

    # ── NEW: Delivery / read status ───────────────────────────────────────
    # Values: 'sent' | 'delivered' | 'read'
    # Alembic will generate: ALTER TABLE thread_messages ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'sent'
    status = db.Column(db.Enum(ThreadMessageStatus, native_enum=False, values_callable=lambda e: [m.value for m in e]), nullable=False, default=ThreadMessageStatus.SENT.value)

    # Timestamps
    sent_at   = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True, nullable=False)
    edited_at = db.Column(db.DateTime, nullable=True)

    # Relationships
    reactions = db.relationship(
        "ThreadMessageReaction",
        backref="message",
        lazy="dynamic",
        cascade="all, delete-orphan"
    )
    reply_to = db.relationship(
        "ThreadMessage",
        remote_side=[id],
        foreign_keys=[reply_to_id],
        backref=db.backref("replies", lazy="dynamic")
    )

    __table_args__ = (
        # Composite index for fetching active messages in a thread ordered by time
        db.Index("idx_tm_thread_active_time", "thread_id", "is_deleted", "sent_at"),
        # Composite index for fetching pinned messages in a thread
        db.Index("idx_tm_thread_pinned", "thread_id", "is_pinned", "is_deleted"),
        # Partial index: only index non-read messages (avoids bloat from fully-read history)
        db.Index("idx_tm_status", "status", postgresql_where=db.text("status != 'read'")),
        # Composite partial index for per-thread unread-count queries (MIGRATION-03)
        # Speeds up the COUNT queries in get_my_threads; only indexes non-deleted rows.
        db.Index("idx_tm_thread_unread", "thread_id", "sender_id", "is_deleted", "sent_at",
                 postgresql_where=db.text("is_deleted = FALSE")),
        # Partial index: only index messages with a non-null AI personality
        db.Index("idx_tm_ai_personality", "ai_personality",
                 postgresql_where=db.text("ai_personality IS NOT NULL")),
    )

    def __repr__(self):
        return f"<ThreadMessage {self.id} in Thread {self.thread_id}>"


# ============================================================================
# [ADD] ThreadMessageReaction — mirrors MessageReaction from the DM system.
# One reaction per user per message (UniqueConstraint).
# Sending the same emoji again toggles it off (handled in WS manager).
# ============================================================================

class ThreadMessageReaction(db.Model):
    """Emoji reactions on thread messages."""
    __tablename__ = "thread_message_reactions"

    id         = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer,
        db.ForeignKey("thread_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    emoji      = db.Column(db.String(10), nullable=False)
    reacted_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    user = db.relationship("User", backref="thread_reactions")

    __table_args__ = (
        db.UniqueConstraint("message_id", "user_id", name="unique_thread_msg_reaction"),
        db.Index("idx_thread_rxn_message", "message_id"),
    )

    def __repr__(self):
        return f"<ThreadMessageReaction {self.emoji} by User {self.user_id} on Msg {self.message_id}>"


# ============================================================================
# NEW: ThreadMessageReadReceipt
# Per-user read receipts — powers delivered/read double ticks.
# Alembic will generate CREATE TABLE thread_message_read_receipts …
# ============================================================================

class ThreadMessageReadReceipt(db.Model):
    """Per-user read receipts for thread messages. Powers delivered/read ticks."""
    __tablename__ = "thread_message_read_receipts"

    id         = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer,
        db.ForeignKey("thread_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    read_at    = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("message_id", "user_id", name="unique_thread_read_receipt"),
        db.Index("idx_tread_receipt_msg",  "message_id"),
        db.Index("idx_tread_receipt_user", "user_id"),
    )

    def __repr__(self):
        return f"<ThreadMessageReadReceipt: User {self.user_id} read Msg {self.message_id}>"


# ============================================================================
# ThreadMessageAttachment — MIGRATION-01
# Dedicated child table for multiple attachments per thread message.
# The legacy single-attachment columns on ThreadMessage are preserved as
# nullable for backward compatibility; they will be dropped in a later release
# once all reads go through this table (Phase 3 of migration-01).
# ============================================================================

class ThreadMessageAttachment(db.Model):
    """Multiple attachments per thread message. Replaces single-attachment columns on ThreadMessage."""
    __tablename__ = "thread_message_attachments"

    id              = db.Column(db.Integer, primary_key=True)
    message_id      = db.Column(
        db.Integer,
        db.ForeignKey("thread_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    attachment_url  = db.Column(db.String(500), nullable=False)
    attachment_name = db.Column(db.String(255), nullable=True)
    attachment_type = db.Column(db.String(50),  nullable=True)   # 'image' | 'video' | 'document'
    attachment_size = db.Column(db.Integer,      nullable=True)  # bytes
    sort_order      = db.Column(db.Integer,      nullable=False, default=0)
    created_at      = db.Column(db.DateTime,     nullable=False, default=datetime.datetime.utcnow)

    message = db.relationship(
        "ThreadMessage",
        backref=db.backref("attachments", lazy="dynamic", cascade="all, delete-orphan")
    )

    __table_args__ = (
        db.Index("idx_tma_message_id",   "message_id"),
        db.Index("idx_tma_message_sort", "message_id", "sort_order"),
    )

    def __repr__(self):
        return f"<ThreadMessageAttachment {self.id}: '{self.attachment_name}' on Msg {self.message_id}>"

    def to_dict(self):
        return {
            "id":              self.id,
            "message_id":      self.message_id,
            "attachment_url":  self.attachment_url,
            "attachment_name": self.attachment_name,
            "attachment_type": self.attachment_type,
            "attachment_size": self.attachment_size,
            "sort_order":      self.sort_order,
            "created_at":      self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================================
# NEW: ThreadMeetingNote
# AI-generated structured meeting notes for a thread conversation.
# Alembic will generate CREATE TABLE thread_meeting_notes …
# ============================================================================

class ThreadMeetingNote(db.Model):
    """AI-generated structured meeting notes for a thread conversation."""
    __tablename__ = "thread_meeting_notes"

    id            = db.Column(db.Integer, primary_key=True)
    thread_id     = db.Column(db.Integer, db.ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    message_range = db.Column(db.Integer, nullable=False)   # requested window (50 | 100 | 500)
    message_count = db.Column(db.Integer, nullable=False)   # actual messages analysed
    notes_json    = db.Column(db.JSON,    nullable=False)   # structured output from AI
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)

    __table_args__ = (
        db.Index("idx_tmn_thread_id",      "thread_id"),
        db.Index("idx_tmn_created_at",     "created_at"),
        db.Index("idx_tmn_thread_created", "thread_id", "created_at"),
    )

    def __repr__(self):
        return f"<ThreadMeetingNote {self.id}: Thread {self.thread_id}>"


# ============================================================================
# ============================================================================

class Connection(db.Model):
    """Friend/connection system."""
    __tablename__ = "connections"

    id           = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    receiver_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    status       = db.Column(db.Enum(ConnectionStatus, native_enum=False, values_callable=lambda e: [m.value for m in e]), default=ConnectionStatus.PENDING.value, index=True)

    connection_type  = db.Column(db.String(30), default="connection")
    requester_notes  = db.Column(db.Text)
    receiver_notes   = db.Column(db.Text)

    # C-3 fix: explicit "who blocked whom" column. Previously block_user()
    # swapped requester_id/receiver_id on an existing row so that
    # "receiver_id" would always mean "the blocker" — that corrupted the
    # original connection-request history and disagreed with at least two
    # other independently-written "is this blocked" checks elsewhere in the
    # codebase. blocked_by_id is the single, unambiguous source of truth for
    # that question; requester_id/receiver_id are never mutated to express
    # blocking anymore. NULL unless status == "blocked".
    blocked_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    __table_args__ = (
        db.UniqueConstraint('requester_id', 'receiver_id', name='unique_connection'),
        db.CheckConstraint('requester_id != receiver_id', name='no_self_connection'),
    )

    def __repr__(self):
        return f"<Connection: {self.requester_id} -> {self.receiver_id} [{self.status}]>"


# ----------------------------------------------------------------------------
# Document 4 §3.1: functional (expression) index on the normalized/unordered
# connection pair plus status, serving every or_(and_(...), and_(...))
# bidirectional connection lookup across connections.py/messages.py/search.py.
#
# NOTE — this is a *lookup-speed* index only, and is deliberately NOT unique
# and NOT the same thing as the reverse-duplicate-prevention index specced in
# Document 1 §5 / Document 4 §3.1's own "or, more efficiently, a single
# expression index..." aside. That one is a separate, UNIQUE functional index
# on just (LEAST(requester_id,receiver_id), GREATEST(requester_id,receiver_id))
# with no status column, whose job is to make a reverse-direction duplicate
# connection row impossible to insert. Implementing both here would conflate
# a constraint with a performance index; only the lookup-speed index
# requested for this phase is added. The uniqueness guarantee remains a
# separate, not-yet-implemented item — flag if you want it added too.
#
# LEAST/GREATEST are Postgres functions with no SQLite equivalent. Unlike
# postgresql_using=... on a plain db.Index() (which only changes the index
# *type* on Postgres but still emits the same DDL, including the LEAST/
# GREATEST expressions, on every dialect — this was tested and confirmed to
# break `db.create_all()` on SQLite), a raw DDL() gated with
# .execute_if(dialect="postgresql") is skipped entirely on non-Postgres
# dialects, which is what actually makes this Postgres-only.
# ----------------------------------------------------------------------------
from sqlalchemy import event, DDL  # noqa: E402

_connections_pair_status_index = DDL(
    "CREATE INDEX IF NOT EXISTS idx_connections_pair_status "
    "ON connections (LEAST(requester_id, receiver_id), GREATEST(requester_id, receiver_id), status)"
)
event.listen(
    Connection.__table__,
    "after_create",
    _connections_pair_status_index.execute_if(dialect="postgresql"),
)


class Mention(db.Model):
    """Track @username mentions."""
    __tablename__ = "mentions"

    id                   = db.Column(db.Integer, primary_key=True)
    mentioned_in_type    = db.Column(db.String(20), nullable=False, index=True)
    mentioned_in_id      = db.Column(db.Integer,    nullable=False, index=True)
    mentioned_user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    mentioned_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    is_read              = db.Column(db.Boolean, default=False)
    mentioned_at         = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def __repr__(self):
        return f"<Mention: @User{self.mentioned_user_id} in {self.mentioned_in_type} {self.mentioned_in_id}>"


class PostFollow(db.Model):
    """Follow posts for notifications."""
    __tablename__ = "post_follows"

    id         = db.Column(db.Integer, primary_key=True)
    post_id    = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    followed_at        = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    notify_on_comment  = db.Column(db.Boolean, default=True)
    notify_on_solution = db.Column(db.Boolean, default=True)

    __table_args__ = (
        db.UniqueConstraint('post_id', 'student_id', name='unique_post_follow'),
    )

    def __repr__(self):
        return f"<Follow: User {self.student_id} -> Post {self.post_id}>"


class Message(db.Model):
    """Private messaging between connected users."""
    __tablename__ = "messages"

    id          = db.Column(db.Integer, primary_key=True)
    # Document 4 §3.2: deliberately left as RESTRICT (Postgres/SQLAlchemy's
    # default when no ondelete= is given) rather than CASCADE — cascading a
    # user deletion into these would silently delete the *other* user's
    # message history too, which is not the intended behavior. Confirmed
    # with the user (2026-08-04). If account deletion is ever built as a
    # feature, the right model is a soft-delete/anonymization flow on User,
    # not a hard DELETE — that's a separate product decision, not addressed
    # here.
    sender_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    resources   = db.Column(MutableList.as_mutable(db.JSON), default=list)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    subject        = db.Column(db.String(200), nullable=True)
    body           = db.Column(db.Text, nullable=False)
    status         = db.Column(db.Enum(MessageStatus, native_enum=False, values_callable=lambda e: [m.value for m in e]), default=MessageStatus.SENT.value)
    client_temp_id = db.Column(db.String(300))

    sent_at    = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)
    is_read    = db.Column(db.Boolean, default=False, index=True)
    read_at    = db.Column(db.DateTime)
    is_deleted = db.Column(db.Boolean, default=False, index=True)

    deleted_by_sender   = db.Column(db.Boolean, default=False)
    deleted_by_receiver = db.Column(db.Boolean, default=False)
    parent_message_id   = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=True, index=True)
    has_thread          = db.Column(db.Boolean, default=False)
    thread_reply_count  = db.Column(db.Integer, default=0)
    related_post_id     = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=True)
    related_session_id  = db.Column(db.Integer, db.ForeignKey("study_session_calendar.id"), nullable=True)

    def __repr__(self):
        return f"<Message {self.id}: {self.sender_id} -> {self.receiver_id}>"


class MessageReaction(db.Model):
    """Emoji reactions to direct messages."""
    __tablename__ = 'message_reactions'

    id            = db.Column(db.Integer, primary_key=True)
    message_id    = db.Column(db.Integer, db.ForeignKey('messages.id'), nullable=False)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'),    nullable=False)
    reaction_type = db.Column(db.String(50), nullable=False)
    emoji         = db.Column(db.String(10), nullable=False)
    reacted_at    = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    message = db.relationship('Message', backref='reactions')
    user    = db.relationship('User',    backref='message_reactions')

    __table_args__ = (
        db.UniqueConstraint('message_id', 'user_id', name='unique_user_reaction'),
    )

    def __repr__(self):
        return f'<MessageReaction {self.emoji} on message {self.message_id}>'


# ============================================================================
# STUDY SESSIONS & PEER TEACHING
# ============================================================================

class PeerTeachingRelationship(db.Model):
    """Formal mentor/mentee or peer teaching partnerships."""
    __tablename__ = 'peer_teaching_relationships'

    id          = db.Column(db.Integer, primary_key=True)
    teacher_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    learner_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    subject      = db.Column(db.String(100), nullable=False)
    teaching_type= db.Column(db.String(50), default='peer')

    sessions_completed = db.Column(db.Integer, default=0)
    total_hours        = db.Column(db.Float, default=0.0)
    topics_covered     = db.Column(MutableList.as_mutable(db.JSON), default=list)

    initial_skill_level = db.Column(db.Integer)
    current_skill_level = db.Column(db.Integer)
    skill_improvement   = db.Column(db.Float, default=0.0)

    learner_rating   = db.Column(db.Float)
    teacher_rating   = db.Column(db.Float)
    learner_feedback = db.Column(db.Text)
    teacher_feedback = db.Column(db.Text)

    resources_shared           = db.Column(MutableList.as_mutable(db.JSON), default=list)
    problems_solved_together   = db.Column(db.Integer, default=0)

    status    = db.Column(db.String(20), default='active')
    is_active = db.Column(db.Boolean, default=True)

    learning_goals  = db.Column(MutableList.as_mutable(db.JSON), default=list)
    goals_achieved  = db.Column(MutableList.as_mutable(db.JSON), default=list)
    achievements    = db.Column(MutableList.as_mutable(db.JSON), default=list)

    started_at      = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    completed_at    = db.Column(db.DateTime)
    last_session_at = db.Column(db.DateTime)
    created_at      = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint('teacher_id != learner_id', name='check_different_users'),
        db.CheckConstraint('learner_rating >= 1 AND learner_rating <= 5', name='check_learner_rating'),
        db.CheckConstraint('teacher_rating >= 1 AND teacher_rating <= 5', name='check_teacher_rating'),
    )


class StudyBuddyRequest(db.Model):
    """Study partnership requests with matching criteria."""
    __tablename__ = "study_buddy_requests"

    id           = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    receiver_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    subjects     = db.Column(MutableList.as_mutable(db.JSON), default=list)
    availability = db.Column(MutableDict.as_mutable(db.JSON), default=dict)
    message      = db.Column(db.Text)

    status    = db.Column(db.String(20), default="pending", index=True)
    thread_id = db.Column(db.Integer, db.ForeignKey("threads.id"), nullable=True)

    requested_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    responded_at = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint('requester_id', 'receiver_id', name='unique_study_buddy_request'),
    )

    def __repr__(self):
        return f"<StudyBuddy: {self.requester_id} -> {self.receiver_id} [{self.status}]>"


class StudyBuddyMatch(db.Model):
    """Active study buddy partnerships."""
    __tablename__ = "study_buddy_matches"

    id       = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    user2_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    subjects       = db.Column(MutableList.as_mutable(db.JSON), default=list)
    thread_id      = db.Column(db.Integer, db.ForeignKey("threads.id"))
    sessions_count = db.Column(db.Integer, default=0)
    is_active      = db.Column(db.Boolean, default=True)

    matched_at    = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_activity = db.Column(db.DateTime)
    ended_at      = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint('user1_id', 'user2_id', name='unique_study_match'),
    )

    def __repr__(self):
        return f"<StudyMatch: {self.user1_id} <-> {self.user2_id}>"


# ============================================================================
# ENGAGEMENT & REACTIONS
# ============================================================================

class CommentLike(db.Model):
    """Like tracking for comments."""
    __tablename__ = "comment_likes"

    id         = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey("comments.id"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"),    nullable=False, index=True)
    liked_at   = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('comment_id', 'student_id', name='unique_comment_like'),
        # Document 4 §3.1: serves batch "did I like any of these comments"
        # reverse lookups (WHERE student_id = X AND comment_id IN (...)).
        db.Index("idx_comment_likes_student_comment", "student_id", "comment_id"),
    )

    def __repr__(self):
        return f"<CommentLike: User {self.student_id} -> Comment {self.comment_id}>"


class PostReaction(db.Model):
    """Emoji reactions for posts."""
    __tablename__ = "post_reactions"

    id            = db.Column(db.Integer, primary_key=True)
    post_id       = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    student_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    reaction_type = db.Column(db.String(20), nullable=False)
    reacted_at    = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('post_id', 'student_id', name='unique_post_reaction'),
        # Document 4 §3.1: serves batch "did I react to any of these posts"
        # reverse lookups (WHERE student_id = X AND post_id IN (...)).
        db.Index("idx_post_reactions_student_post", "student_id", "post_id"),
    )

    def __repr__(self):
        return f"<Reaction: {self.reaction_type} on Post {self.post_id}>"


class PostEvent(db.Model):
    """Track post events for badges and analytics."""
    __tablename__ = "post_events"

    id              = db.Column(db.Integer, primary_key=True)
    post_id         = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    event_type      = db.Column(db.String(50), nullable=False, index=True)
    event_data      = db.Column(MutableDict.as_mutable(db.JSON), default=dict)
    triggered_at    = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    processed       = db.Column(db.Boolean, default=False, index=True)
    awarded_badge_id= db.Column(db.Integer, db.ForeignKey("badges.id"))

    __table_args__ = (
        db.UniqueConstraint('post_id', 'event_type', name='unique_post_event'),
    )


# ============================================================================
# GAMIFICATION
# ============================================================================

class Badge(db.Model):
    """Achievable badges."""
    __tablename__ = "badges"

    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(100), unique=True, nullable=False)
    description  = db.Column(db.Text)
    icon         = db.Column(db.String(100))
    category     = db.Column(db.String(50), index=True)
    criteria     = db.Column(MutableDict.as_mutable(db.JSON))
    rarity       = db.Column(db.String(20), default="common")
    awarded_count= db.Column(db.Integer, default=0)
    is_active    = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def __repr__(self):
        return f"<Badge: {self.name} [{self.rarity}]>"


class UserBadge(db.Model):
    """Badges earned by users."""
    __tablename__ = "user_badges"

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"),  nullable=False, index=True)
    badge_id    = db.Column(db.Integer, db.ForeignKey("badges.id"), nullable=False, index=True)
    earned_at   = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    is_featured = db.Column(db.Boolean, default=False)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'badge_id', name='unique_user_badge'),
    )

    badge = db.relationship("Badge", backref="user_badges")

    def __repr__(self):
        return f"<UserBadge: User {self.user_id} earned Badge {self.badge_id}>"


class ReputationHistory(db.Model):
    """Log of all reputation changes."""
    __tablename__ = "reputation_history"

    id             = db.Column(db.Integer, primary_key=True)
    # Document 4 §3.2: CASCADE — meaningless without the user.
    user_id        = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    action         = db.Column(db.String(100), nullable=False)
    points_change  = db.Column(db.Integer, nullable=False)
    related_type   = db.Column(db.String(20))
    related_id     = db.Column(db.Integer)
    created_at     = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)
    reputation_before = db.Column(db.Integer)
    reputation_after  = db.Column(db.Integer)

    __table_args__ = (
        db.Index("idx_rep_history_date_user", "created_at", "user_id"),
        db.Index("idx_rep_history_user_date", "user_id", "created_at"),
    )

    def __repr__(self):
        return f"<RepHistory: User {self.user_id} {self.points_change:+d} pts for {self.action}>"


# ============================================================================
# AI FEATURES
# ============================================================================

class AIConversation(db.Model):
    """AI chat conversations."""
    __tablename__ = "ai_conversations"

    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    title    = db.Column(db.String(200), default="New Conversation")
    messages = db.Column(MutableList.as_mutable(db.JSON), default=list)

    attachment         = db.Column(db.String(200))
    related_post_id    = db.Column(db.Integer, db.ForeignKey("posts.id"))
    related_comment_id = db.Column(db.Integer, db.ForeignKey("comments.id"))

    last_incomplete_message   = db.Column(db.Text, nullable=True)
    is_last_message_complete  = db.Column(db.Boolean, default=True)
    error_count               = db.Column(db.Integer, default=0)

    total_messages = db.Column(db.Integer, default=0)
    tokens_used    = db.Column(db.Integer, default=0)
    is_archived    = db.Column(db.Boolean, default=False)

    created_at      = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_message_at = db.Column(db.DateTime)


class AIUsageQuota(db.Model):
    """Track AI usage limits."""
    __tablename__ = "ai_usage_quotas"

    id                   = db.Column(db.Integer, primary_key=True)
    user_id              = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True)
    daily_messages_limit = db.Column(db.Integer, default=50)
    daily_messages_used  = db.Column(db.Integer, default=0)
    last_reset_date      = db.Column(db.Date, default=datetime.date.today)
    last_message_time    = db.Column(db.DateTime, default=datetime.datetime.utcnow)


# ============================================================================
# ANALYTICS & TRACKING
# ============================================================================

class UserActivity(db.Model):
    """Track daily user activity for heatmap and streaks."""
    __tablename__ = "user_activity"

    id            = db.Column(db.Integer, primary_key=True)
    # Document 4 §3.2: CASCADE — meaningless without the user.
    user_id       = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    activity_date = db.Column(db.Date, default=datetime.date.today, nullable=False, index=True)

    posts_created   = db.Column(db.Integer, default=0)
    comments_created= db.Column(db.Integer, default=0)
    threads_joined  = db.Column(db.Integer, default=0)
    messages_sent   = db.Column(db.Integer, default=0)
    helpful_count   = db.Column(db.Integer, default=0)
    activity_score  = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'activity_date', name='unique_daily_activity'),
        db.Index("idx_user_activity_user_date_score", "user_id", "activity_date"),
    )

    def __repr__(self):
        return f"<Activity: User {self.user_id} on {self.activity_date}>"


class SearchIndex(db.Model):
    """
    Full-text search index for faster queries.

    H-6 note: this table is currently NEVER populated or queried anywhere
    in the codebase — every search in routes/student/search.py uses
    unindexed `ILIKE '%term%'` against the live tables instead. Left in
    place (not dropped) for this pass, since removing it is a destructive
    schema change that needs a real migration and a product decision on
    whether full-text search is still planned — flagged in the
    implementation summary rather than silently deleted or silently wired
    up as a guess.
    """
    __tablename__ = "search_index"

    id              = db.Column(db.Integer, primary_key=True)
    post_id         = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, unique=True, index=True)
    searchable_text = db.Column(db.Text)
    department      = db.Column(db.String(100), index=True)
    post_type       = db.Column(db.String(50),  index=True)
    tags_text       = db.Column(db.String(500), index=True)
    indexed_at      = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def __repr__(self):
        return f"<SearchIndex: Post {self.post_id}>"


# ============================================================================
# UTILITY & MODERATION
# ============================================================================

class Notification(db.Model):
    """In-app notifications."""
    __tablename__ = "notifications"

    id                = db.Column(db.Integer, primary_key=True)
    # Document 4 §3.2: CASCADE — a notification is meaningless without its
    # user; if a user account is ever hard-deleted, their notifications
    # should go with it.
    user_id           = db.Column(db.Integer, db.ForeignKey('users.id', ondelete="CASCADE"), nullable=False, index=True)
    title             = db.Column(db.String(200), nullable=False)
    body              = db.Column(db.Text, nullable=False)
    link              = db.Column(db.String(200))
    notification_type = db.Column(db.String(50), index=True)
    related_type      = db.Column(db.String(20))
    related_id        = db.Column(db.Integer)
    is_read           = db.Column(db.Boolean, default=False, index=True)
    created_at        = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)
    read_at           = db.Column(db.DateTime)

    def __repr__(self):
        return f"<Notification {self.id}: {self.notification_type} for User {self.user_id}>"


class PostReport(db.Model):
    """Content moderation."""
    __tablename__ = "post_reports"

    id          = db.Column(db.Integer, primary_key=True)
    post_id     = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    reported_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    reason       = db.Column(db.String(100), nullable=False)
    description  = db.Column(db.Text)
    status       = db.Column(db.String(20), default="pending", index=True)
    reviewed_by  = db.Column(db.Integer, db.ForeignKey("users.id"))
    review_notes = db.Column(db.Text)
    action_taken = db.Column(db.String(100))

    reported_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    reviewed_at = db.Column(db.DateTime)

    def __repr__(self):
        return f"<Report {self.id}: Post {self.post_id} - {self.reason} [{self.status}]>"


class UserWarning(db.Model):
    """Track warnings for policy violations."""
    __tablename__ = "user_warnings"

    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    reason      = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    severity    = db.Column(db.String(20), default="low")
    issued_by   = db.Column(db.Integer, db.ForeignKey("users.id"))

    related_type = db.Column(db.String(20))
    related_id   = db.Column(db.Integer)

    is_active  = db.Column(db.Boolean, default=True)
    expires_at = db.Column(db.DateTime)
    issued_at  = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def __repr__(self):
        return f"<Warning: User {self.user_id} - {self.severity} [{self.reason}]>"


class ProfileChangeHistory(db.Model):
    """Audit trail for profile changes."""
    __tablename__ = "profile_change_history"

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    field_changed = db.Column(db.String(100), nullable=False)
    old_value     = db.Column(db.String(500))
    new_value     = db.Column(db.String(500))
    change_type   = db.Column(db.String(50), index=True)
    ip_address    = db.Column(db.String(50))
    user_agent    = db.Column(db.String(200))
    changed_at    = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def __repr__(self):
        return f"<ProfileChange: User {self.user_id} - {self.field_changed} [{self.change_type}]>"


class PasswordResetToken(db.Model):
    """Secure password reset tokens."""
    __tablename__ = "password_reset_tokens"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    token      = db.Column(db.String(500), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used       = db.Column(db.Boolean, default=False)
    used_at    = db.Column(db.DateTime)

    def is_valid(self):
        return not self.used and datetime.datetime.utcnow() < self.expires_at

    def __repr__(self):
        return f"<PasswordResetToken for User {self.user_id}>"
