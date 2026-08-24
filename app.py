# app.py - PRODUCTION READY VERSION WITH FLASK-MAIL + APScheduler
# ============================================================================
# IMPORTANT: Load environment variables FIRST before any other imports
# ============================================================================
from dotenv import load_dotenv
import os

# FIX: this MUST run before any local module is imported. Several modules
# (routes/student/auth.py in particular) read os.environ.get(...) at
# MODULE IMPORT TIME (to build the google_bp blueprint), not inside a
# function. If load_dotenv() runs after those imports, os.environ is still
# empty when auth.py executes, and GOOGLE_CLIENT_ID/GOOGLE_OAUTH_CLIENT_ID
# resolve to None -> Google OAuth blueprint gets client_id=None ->
# "Error 401: invalid_client" at the Google sign-in screen, even though
# the .env file itself is correct and Cloud Console is configured fine.
loaded = load_dotenv()

import random

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from flask_migrate import Migrate
from sqlalchemy import text
from services.websocket_messages import init_message_websocket
from services.websocket_threads import thread_ws_manager
from services.rate_limit_service import init_app as init_rate_limiter
from extensions import db, mail
from routes.student.helpers import (
    token_required, success_response, error_response
)
from config import get_config
from errors import AppError

from waitlist import waitlist_bp
import logging
from routes.student import student_bp
from routes.student.auth import google_bp
from logging.handlers import RotatingFileHandler


os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


# ============================================================================
# Configuration
# ============================================================================
# Phase 0 refactor: the Config class that used to be defined inline here now
# lives in config.py as a small hierarchy (Config / DevelopmentConfig /
# TestingConfig / ProductionConfig), selected via FLASK_ENV / APP_ENV. This
# is the fix for the previously-flagged "debug=True hardcoded in __main__
# contradicts Config.DEBUG=False" inconsistency (see the __main__ block
# below) — DEBUG now comes from whichever config tier is actually selected,
# not a hardcoded literal.
migrate = Migrate()


# ============================================================================
# Application Factory
# ============================================================================

def create_app(config_class=None):
    """Create and configure the Flask application"""
    config_class = config_class or get_config()
    app = Flask(__name__)
    app.config.from_object(config_class)

    # ========================================================================
    # CORS (AUDIT ENG-11)
    # ========================================================================
    # config.py already defined CORS_ALLOWED_ORIGINS but nothing actually
    # read it — flask-cors wasn't even a dependency. Confirmed with you
    # directly that no reverse proxy/CDN in front of this app handles CORS,
    # so it needs to be enforced here at the Flask layer.
    #
    # supports_credentials=True is REQUIRED, not optional: this app
    # authenticates via cookies (access_token/refresh_token/csrf_token, see
    # helpers.py::set_auth_cookies) rather than an Authorization header, so
    # a cross-origin frontend can't complete a request at all without the
    # browser being told it's allowed to send/receive those cookies.
    #
    # IMPORTANT — this makes CORS_ALLOWED_ORIGINS' default of ["*"]
    # actively broken for any deployment that isn't DevelopmentConfig's
    # explicit dev-only override: browsers reject the combination of
    # Access-Control-Allow-Credentials: true with a wildcard
    # Access-Control-Allow-Origin. supports_credentials=True + origins=["*"]
    # does not silently degrade to "CORS just doesn't work" — cross-origin
    # requests fail at the browser's own enforcement layer, before this
    # app's logic is even reached. CORS_ALLOWED_ORIGINS MUST be set to your
    # real frontend origin(s) (e.g. "https://app.studyhub.com") in any
    # environment where a cross-origin frontend needs to authenticate.
    #
    # NEW DEPENDENCY: flask-cors must be added to requirements.txt (or
    # whatever your dependency file is) — it was not previously a
    # dependency of this project, per the audit's own finding.
    CORS(
        app,
        origins=app.config.get("CORS_ALLOWED_ORIGINS", ["*"]),
        supports_credentials=True,
    )

    # Startup diagnostic (moved here from the old inline Config class body,
    # where it ran once per config subclass at import time rather than once
    # per actual app instance — same message, better-scoped side effect).
    #
    # AUDIT security-hygiene fix: no longer prints the actual MAIL_USERNAME
    # value. Low sensitivity as the audit itself notes (an email address,
    # not a secret), but this diagnostic runs on every create_app() call —
    # including under Gunicorn in production, not just direct `python
    # app.py` runs like the removed DATABASE_URL print above — so there's
    # no reason to print the value itself when confirming it's *set* is
    # all this diagnostic is actually for.
    if not app.config.get('MAIL_USERNAME') or not app.config.get('MAIL_PASSWORD'):
        print("⚠️  WARNING: Email credentials not configured!")
    else:
        print("✅ Email configured")
    
    # Initialize extensions
    db.init_app(app)
    mail.init_app(app)
    migrate.init_app(app, db)

    # Rate limiter — must init AFTER app.config is populated (config.from_object
    # above) since it reads RATE_LIMIT_STORAGE_URI/RATE_LIMIT_ENABLED from it.
    # Fails open if the storage backend (Redis) is unreachable at request time —
    # see services/rate_limit_service.py's module docstring.
    init_rate_limiter(app)
    
    # ========================================================================
    # HORIZONTAL SCALING (see 01-DESIGN-horizontal-scaling.md):
    # This app previously assumed a SINGLE process — WebSocket presence/
    # active-thread state, the per-user thread-message rate limit, and
    # scheduler job execution all lived in process-local memory with no
    # cross-instance coordination. That is no longer true for the three
    # items below, each now backed by Redis:
    #   - WebSocket presence (online/offline) and active-thread tracking
    #     -> services/presence_service.py
    #   - Thread-message rate limiting -> services/websocket_rate_limiter.py
    #     (RedisFixedWindowLimiter)
    #   - Scheduler job execution -> services/distributed_lock.py, wired
    #     into scheduler.py's three jobs (see that file's own docstring —
    #     the "Keep -w 1" constraint below is now obsolete as a result)
    # Two things are still explicitly OUT of this refactor's scope, left
    # exactly as in-process-only as before (see design doc §2 for why):
    #   - Typing-indicator dedup bookkeeping (TypingStatusManager) — stays
    #     local by design, not by omission; the actual broadcast already
    #     reaches every instance via message_queue regardless.
    #   - The Learnora multi-provider failover/cooldown state
    #     (services/ai_provider_service.py's MultiProviderManager) — out
    #     of the stated scope of this refactor, flagged in the design doc,
    #     not addressed here.
    # websocket_events.py is not part of any of the above — it's confirmed
    # unused (not imported by this file or anywhere else) and was
    # deliberately left untouched.
    # WebSocket Initialization (CRITICAL - must be in correct order)
    # ========================================================================
    # Step 1: Initialize base message WebSocket (creates socketio instance).
    #         Uses async_mode='threading' + simple-websocket (Python 3.13 safe).
    #         Install dep: pip install simple-websocket
    #         Also wires message_queue=REDIS_URL into the SocketIO
    #         constructor (see websocket_messages.py::init_app) — this is
    #         what makes emit(..., room=X) calls reach clients connected
    #         to OTHER application instances, not just this one.
    socketio = init_message_websocket(app)
    
    # Step 2: Initialize thread WebSocket handlers using the same socketio instance.
    #         This MUST happen BEFORE the server starts.
    thread_ws_manager.init_socketio(app, socketio)
    
    # ========================================================================
    # Logging Configuration
    # ========================================================================
    if not app.debug and not app.testing:
        # Create logs directory if itesn't exist
        if not os.path.exists('logs'):
            os.mkdir('logs')
        
        # File handler for error logs
        file_handler = RotatingFileHandler(
            'logs/studyhub.log',
            maxBytes=10240000,  # 10MB
            backupCount=10
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        
        app.logger.setLevel(logging.INFO)
        app.logger.info('StudyHub startup')
    
    # ========================================================================
    # Error Handlers
    # ========================================================================

    @app.errorhandler(AppError)
    def handle_app_error(err):
        """
        Centralized handler for the new typed-exception hierarchy (errors.py).
        Produces the exact same {"status": "error", "message": ...} response
        shape the rest of the app already returns via error_response(), so
        no API contract changes for any existing caller — this just gives
        services/routes a `raise SomeError(...)` alternative to manually
        building that dict inline.
        """
        if err.status_code >= 500:
            app.logger.error(f"{type(err).__name__}: {err}", exc_info=True)
        payload = {"status": "error", "message": str(err)}
        if err.details:
            payload["errors"] = err.details
        return jsonify(payload), err.status_code

    @app.errorhandler(400)
    def bad_request(error):
        app.logger.error(f"400 Bad Request: {error}")
        return jsonify({
            "status": "error",
            "message": "Bad request - Invalid data format"
        }), 400
    
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({
            "status": "error",
            "message": "Resource not found"
        }), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        app.logger.error(f"500 Internal Error: {error}")
        db.session.rollback()
        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500
    
    # ========================================================================
    # Security Headers
    # ========================================================================
    
    @app.after_request
    def set_security_headers(response):
        """Add security headers to all responses.

        FIX: Guard against WebSocket upgrade requests. When async_mode='threading'
        is used, the after_request hook fires on WebSocket connections too.
        Trying to set headers on an already-upgraded connection causes:
            AssertionError: write() before start_response
        """
        # Skip header injection for WebSocket upgrade connections
        if request.environ.get('wsgi.websocket'):
            return response

        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        
        # Only set HSTS in production with HTTPS
        if not app.debug and app.config.get('SESSION_COOKIE_SECURE'):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        
        return response
    
    # ========================================================================
    # Request Logging
    # ========================================================================
    
    @app.before_request
    def log_request():
        """Log important requests without exposing sensitive data"""
        if request.method in ['POST', 'PUT', 'DELETE']:
            app.logger.info(f"{request.method} {request.path} from {request.remote_addr}")
    
    # ========================================================================
    # Register Blueprints
    # ========================================================================
    app.register_blueprint(waitlist_bp)
    app.register_blueprint(google_bp, url_prefix='/google')
    app.register_blueprint(student_bp)
    
    # ========================================================================
    # Routes
    # ========================================================================
    
    @app.route("/")
    def home():
        """Landing page"""
        return render_template("index.html")
    
    @app.route("/health")
    def health_check():
        """Health check endpoint for monitoring"""
        try:
            # Check database connection
            db.session.execute(text('SELECT 1'))
            
            # Check email configuration
            email_status = bool(
                app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD')
            )

            # ── Scheduler status ───────────────────────────────────────────────
            from scheduler import scheduler
            scheduler_jobs = []
            if scheduler.running:
                for job in scheduler.get_jobs():
                    scheduler_jobs.append({
                        "id":            job.id,
                        "name":          job.name,
                        "next_run_time": (
                            job.next_run_time.isoformat()
                            if job.next_run_time else None
                        ),
                    })

            return jsonify({
                "status":           "healthy",
                "database":         "connected",
                "email_configured": email_status,
                "mail_server":      app.config.get('MAIL_SERVER'),
                "scheduler": {
                    "running": scheduler.running,
                    "jobs":    scheduler_jobs,
                },
            }), 200

        except Exception as e:
            app.logger.error(f"Health check failed: {e}")
            return jsonify({
                "status":   "unhealthy",
                "database": "disconnected",
            }), 500
    
    @app.route("/robots.txt")
    def robots():
        """Robots.txt for search engines"""
        return """User-agent: *
Allow: /
Disallow: /admin/
Disallow: /api/
Disallow: /student/profile/
""", 200, {'Content-Type': 'text/plain'}
    
    # ========================================================================
    # Shell Context
    # ========================================================================
    
    @app.shell_context_processor
    def make_shell_context():
        """Add database and models to Flask shell"""
        from models import User, WaitlistSignup, Post, Comment
        return {
            'db':             db,
            'User':           User,
            'WaitlistSignup': WaitlistSignup,
            'Post':           Post,
            'Comment':        Comment,
        }

    # ========================================================================
    # Scheduler Initialization
    # ========================================================================
    # Placed LAST in create_app() so the scheduler only starts after all
    # extensions, blueprints, and DB models are fully registered.
    #
    # Guard: SCHEDULER_ENABLED env var (default: true).
    # Set SCHEDULER_ENABLED=false in staging or multi-worker setups.
    # ========================================================================
    if app.config.get('SCHEDULER_ENABLED', True):
        from scheduler import init_scheduler
        init_scheduler(app)
    else:
        app.logger.info("[Scheduler] Disabled via SCHEDULER_ENABLED=false")

    return app, socketio  # Return both app and socketio for proper initialization


# ============================================================================
# Application Instance
# ============================================================================

# Create app and socketio using the factory
app, socketio = create_app()


# ============================================================================
# Run Application
# ============================================================================

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5001))
    host = "0.0.0.0"

    # ── Scheduler status line for startup banner ───────────────────────────────
    from scheduler import scheduler as _sched
    sched_status = "✅ Running" if _sched.running else "❌ Not started"
    sched_jobs   = len(_sched.get_jobs()) if _sched.running else 0
    next_runs    = ""
    if _sched.running:
        lines = [
            f"    • {j.name}: {j.next_run_time.strftime('%a %Y-%m-%d %H:%M UTC')}"
            for j in _sched.get_jobs()
            if j.next_run_time
        ]
        next_runs = "\n" + "\n".join(lines)
    print("Loaded:", loaded)
    # AUDIT security-hygiene fix: removed `print("DATABASE_URL:",
    # os.getenv("DATABASE_URL"))` — this printed the raw connection
    # string, including any embedded DB password, to stdout on every
    # `python app.py` direct run. Gunicorn (the actual production entry
    # point) never executes this __main__ block, so production risk was
    # already low, but this is exactly the kind of line that ends up in a
    # terminal recording, shell history, or CI log if a `python app.py`
    # smoke-test step is ever added — removed rather than redacted, since
    # `loaded` above already confirms .env loaded without needing to
    # expose any part of the connection string.



    print("\n" + "="*60)
    print("🚀 StudyHub Starting...")
    print("="*60)
    print(f"📧 Email:            {'✅ Configured' if os.environ.get('MAIL_USERNAME') else '❌ Not configured'}")
    # In auth.py, after google_bp is defined:
    # AUDIT security-hygiene fix (extending the same reasoning as the
    # removed DATABASE_URL print above to this line, which the audit's
    # single quoted example didn't name explicitly but is the identical
    # exposure — DATABASE_NEW_URL is the same underlying connection
    # string config.py's Config class reads via
    # DATABASE_URL = os.environ.get('DATABASE_NEW_URL'), potentially
    # including an embedded DB password): no longer prints the raw value.
    
    
    print(f"🗄️  Database:         {'✅ Configured' if os.environ.get('DATABASE_NEW_URL') else '❌ Not configured'}")
    print(f"🔑 Secret Key:       {'✅ Set' if os.environ.get('SECRET_KEY') else '❌ Missing'}")
    print(f"🌐 WebSocket:        threading + simple-websocket (Python 3.13 compatible)")
    print(f"💬 Thread WebSocket: {'✅ Initialized' if thread_ws_manager.socketio else '❌ Not initialized'}")
    print(f"⏰ Scheduler:        {sched_status} ({sched_jobs} job(s)){next_runs}")
    print("="*60)
    print(f"🔗 Server running on: http://{host}:{port}")
    print(f"🔗 Local access:      http://127.0.0.1:{port}")
    print(f"🔗 Network access:    http://localhost:{port}")
    print("="*60 + "\n")
    
    # Create database tables if they don't exist
    with app.app_context():
        db.create_all()
        print("✅ Database tables created/verified\n")
    
    # Run with SocketIO (socketio already has all handlers registered)
    # NOTE: use_reloader=False is required — prevents scheduler double-start
    #       and avoids the threading WebSocket handler being registered twice.
    # Phase 0 fix: debug is now sourced from the environment-tiered config
    # (config.py) instead of a hardcoded True that contradicted
    # Config.DEBUG=False in every other context.
    socketio.run(
        app,
        debug=app.config.get("DEBUG", False),
        host=host,
        port=port,
        use_reloader=False,
    )


# ============================================================================
# Production Entry Point (for Gunicorn)
# ============================================================================
# In production (Gunicorn), the 'app' and 'socketio' variables are already
# created at module level. The WebSocket handlers are already registered
# because create_app() ran when the module loaded.
#
# Run with threading mode (no special worker class needed):
#   gunicorn -w N app:app
#
# HORIZONTAL SCALING (see 01-DESIGN-horizontal-scaling.md §7.4): N can now
# be greater than 1, and SCHEDULER_ENABLED=true is safe to leave on for
# every worker/instance simultaneously. This was NOT true before this
# refactor — the warning below used to require -w 1 specifically because
# APScheduler's BackgroundScheduler had no cross-process coordination, so
# every worker independently running scheduler.py would fire the exact
# same job on the exact same cron tick, producing duplicate leaderboard
# snapshots (and, less dangerously but still wastefully, duplicate
# reconciliation scans). scheduler.py's three jobs are now each wrapped in
# a Redis distributed lock (services/distributed_lock.py) — on any given
# tick, every scheduler-enabled worker attempts the lock, exactly one
# wins and runs the job, the rest log a skip and return immediately. Cron
# schedules and job bodies are unchanged; only the "can more than one
# instance safely have SCHEDULER_ENABLED=true" answer changed, from no to
# yes.
#
# WebSocket cross-instance delivery for N > 1 additionally requires
# REDIS_URL to be set in the environment (see websocket_messages.py::
# init_app) — without it, WebSocket events won't cross worker/instance
# boundaries (a loud [WS_MESSAGE_QUEUE_DISABLED] warning is logged at
# startup if this is missed), even though the scheduler-lock behavior
# above works either way (it only ever needed Redis for the lock itself,
# which fails closed — see distributed_lock.py — rather than silently
# degrading).
#
# It remains fine to disable the scheduler on a specific dyno/container if
# you'd rather not have every instance participate in the lock race at all
# (e.g. a pure web-traffic replica with no interest in running background
# jobs):
#   SCHEDULER_ENABLED=false gunicorn -w N app:app
# This is now an operational choice, not a correctness requirement — with
# SCHEDULER_ENABLED=true everywhere, the lock still guarantees exactly one
# execution per tick regardless of how many instances are enabled.
