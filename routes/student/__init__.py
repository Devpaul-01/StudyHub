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

Document 3 §2.2 (Phase 4, this pass): CSRF double-submit enforcement is
registered here via @student_bp.before_request rather than a per-route
decorator, specifically so every route under this blueprint — present
and future — is protected by default. See enforce_csrf() below.
--------------------------------------------------------------------------
"""

from flask import Blueprint, request, current_app,  g
from errors import ValidationError

# ============================================================================
# CREATE MAIN STUDENT BLUEPRINT
# ============================================================================

student_bp = Blueprint('student', __name__, url_prefix='/student')


# ============================================================================
# CSRF ENFORCEMENT (Document 3 §2) — double-submit cookie pattern
# ============================================================================
#
# Mechanism (Document 3 §2.1): on every response that sets/refreshes
# access_token, a parallel `csrf_token` cookie is also set — a random,
# unguessable value (secrets.token_urlsafe(32)), deliberately NOT a JWT
# (nothing to verify/decode; it's a shared-secret comparison, not a
# bearer credential). Nothing is stored server-side for this: the value
# is only ever compared cookie-vs-header, which is the entire point of
# double-submit — an attacker's cross-site form/fetch can make the
# browser attach the httponly auth cookies automatically, but can't read
# the non-httponly csrf_token cookie (blocked by same-origin policy) to
# also put it in the required header, so the two values won't match.
#
# CSRF_EXEMPT_PATHS covers routes that either predate having a csrf_token
# issued (login, register — no prior session) or are OAuth-callback-style
# entry points with no frontend fetch call to attach a header to.
# /auth/refresh-token is DELIBERATELY NOT exempted (confirmed): the
# csrf_token cookie is reissued alongside access_token on the same
# lifetime (see §1.2's table), so it's expected to still be valid/present
# whenever a refresh is needed, and protecting this endpoint too is
# strictly safer with no real downside once that's true.
# ============================================================================

CSRF_EXEMPT_PATHS = {
    "/student/login",
    "/student/register",
    "/student/onboard",  # onboarding auth is via Google session / token-in-URL, not a CSRF-protected cookie flow yet
    "/student/verify-email",
    "/student/verify-reset",
    "/student/complete-registration",
    "/student/set-password",
    "/student/validate-user",
    "/student/learnora/api/chat",
    "/student/clear-session",
    "/student/check-username",
    "/student/reset-password",
}

# Prefixes are checked separately from exact paths since several of the
# exempt routes above carry a <token> path segment
# (e.g. /student/verify-email/<token>).
CSRF_EXEMPT_PREFIXES = (
    "/student/verify-email/",
    "/student/verify-reset/",
    "/student/complete-registration/",
    "/student/clear-session",
    "/student/onboard/",
    "/google/",
)


def _is_csrf_exempt(path: str) -> bool:
    if path in CSRF_EXEMPT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in CSRF_EXEMPT_PREFIXES)


@student_bp.before_request
def enforce_csrf():
    """
    Document 3 §2.2: registered on the blueprint (not a per-route
    decorator) so new routes are protected by default instead of relying
    on every future route author remembering to add one.

    Only applies to mutating methods — GET/HEAD/OPTIONS are read-only by
    HTTP convention and carry no CSRF risk under the double-submit model.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    if _is_csrf_exempt(request.path):
        return

    cookie_value = request.cookies.get("csrf_token")
    header_value = request.headers.get("X-CSRF-Token")

    if not cookie_value or not header_value or cookie_value != header_value:

        current_app.logger.warning("no csrf  found")
        
        raise ValidationError("CSRF token missing or invalid", status_code=403)


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
