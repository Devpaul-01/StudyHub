"""
StudyHub - Student Routes Package
Combines all student-related sub-blueprints into main student blueprint

Structure:
- Auth: Registration, login, verification
- Dashboard: Overview, stats
- Posts: Create, view, interact with posts
- Comments: Add, edit comments
- Threads: Collaboration groups
- Connections: Friend requests
- Messages: Private messaging
- Profile: View/edit profile
- Badges: Achievement system
- Reputation: Points and leaderboards
- Analytics: Activity tracking
- Search: Find users/posts/threads
- Study Buddy: Find study partners

--------------------------------------------------------------------------
Document 1 (Architecture Refactor) §2 / Document 5 Phase 2 note:

connections.py, threads.py, and posts.py were each split into a
sub-package of 5 files (routes/student/<name>/{crud,...}.py) — see the
refactor/split-<name>-py branches. Each split file defines its OWN
Blueprint object (e.g. connections_crud_bp, connections_blocking_bp, ...)
rather than sharing one; there is no single blueprint object per
sub-package that carries all its routes anymore.

Because of that, `student_bp.register_blueprint(connections_bp)` style
one-liners silently stopped working for those three packages — the
`connections_bp` re-exported from connections/__init__.py is just an
empty aggregate Blueprint with nothing registered on it (Flask blueprints
can't be "nested" onto another blueprint by simple attribute access; each
sub-blueprint needs its own register_blueprint call). Registering only
the empty aggregate would silently drop every route in that package.

Fix: use the register_<name>_blueprints(...) helper each split package
exports instead of importing/registering a single blueprint object for
connections, threads, and posts. learnora.py was NOT split into multiple
blueprints (only its FileHandler class moved to a separate file within
the package) — learnora_bp still carries all its own routes directly, so
it keeps the original single-blueprint import/register pattern unchanged.
--------------------------------------------------------------------------
"""

from flask import Blueprint

# ============================================================================
# CREATE MAIN STUDENT BLUEPRINT
# ============================================================================

student_bp = Blueprint('student', __name__, url_prefix='/student')


# ============================================================================
# IMPORT ALL SUB-BLUEPRINTS
# ============================================================================

from .auth import auth_bp
from .messages import messages_bp
from .homework_system import homework_bp
from .notifications import notifications_bp
from .profile import profile_bp
from .study_sessions import study_sessions_bp

from .leaderboard import leaderboard_bp

from .badges import badges_bp
from .reputation import reputation_bp
from .analytics import analytics_bp
from .search import search_bp
from .learnora import learnora_bp          # unchanged: single blueprint, not split
from .study_buddy import study_buddy_bp

# Document 1 §2 / Document 5 Phase 2: these three packages were split into
# multiple blueprints each — import the registration helper, not a single
# blueprint object.
from .connections import register_connections_blueprints
from .threads import register_threads_blueprints
from .posts import register_posts_blueprints

# Optional routes (uncomment if you have them)
# from .assignments import assignments_bp
# from .grades import grades_bp
# from .attendance import attendance_bp
# from .fees import fees_bp
# from .account import account_bp
# from .notifications import notifications_bp
# from .resources import resources_bp
# from .extras import extras_bp
# from .password_reset import password_reset_bp


# ============================================================================
# REGISTER ALL SUB-BLUEPRINTS
# ============================================================================

# Core features
student_bp.register_blueprint(notifications_bp)
student_bp.register_blueprint(homework_bp)
student_bp.register_blueprint(auth_bp)
student_bp.register_blueprint(study_sessions_bp)
register_posts_blueprints(student_bp)          # was: student_bp.register_blueprint(posts_bp)
student_bp.register_blueprint(learnora_bp)
student_bp.register_blueprint(profile_bp)
student_bp.register_blueprint(leaderboard_bp)

# Social features
student_bp.register_blueprint(messages_bp)
register_connections_blueprints(student_bp)    # was: student_bp.register_blueprint(connections_bp)
register_threads_blueprints(student_bp)        # was: student_bp.register_blueprint(threads_bp)
student_bp.register_blueprint(study_buddy_bp)

# Gamification
student_bp.register_blueprint(badges_bp)
student_bp.register_blueprint(reputation_bp)

# Discovery & Analytics
student_bp.register_blueprint(search_bp)
student_bp.register_blueprint(analytics_bp)
