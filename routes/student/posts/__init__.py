"""
StudyHub - Posts blueprint aggregation

Document 1 §2.3: posts.py (~3,300 lines) split into 5 files:
    crud.py       - post CRUD, feed, reactions, single-post detail endpoints
    comments.py   - comments and replies
    bookmarks.py  - bookmarks (verbatim move only — excluded from this
                    refactor phase's logic changes per project instructions)
    discovery.py  - tags, resources
    ai.py         - ask Learnora about a post, apply refinement

This __init__.py aggregates the five sub-blueprints so that wherever
blueprints are registered with the Flask app, the same import path
(`from routes.student.posts import posts_bp` / or the registration helper
below) continues to expose every route at the exact same path as the
pre-split posts.py.

This is a pure move (Document 5 Phase 2, principle #1): no route paths,
decorators, or function bodies were changed as part of this split.
"""

from flask import Blueprint

from routes.student.posts.crud import posts_crud_bp
from routes.student.posts.comments import posts_comments_bp
from routes.student.posts.bookmarks import posts_bookmarks_bp
from routes.student.posts.discovery import posts_discovery_bp
from routes.student.posts.ai import posts_ai_bp

# Aggregate blueprint — kept for any code that imports `posts_bp` by name.
posts_bp = Blueprint("student_posts", __name__)

_SUB_BLUEPRINTS = [
    posts_crud_bp,
    posts_comments_bp,
    posts_bookmarks_bp,
    posts_discovery_bp,
    posts_ai_bp,
]


def register_posts_blueprints(app_or_bp):
    """
    Register all five posts sub-blueprints directly on the given Flask app
    (or parent blueprint). Prefer this over registering the aggregate
    `posts_bp` above, since Flask blueprints can't be nested by simple
    attachment — each sub-blueprint needs its own `register_blueprint` call.

    Usage in app.py (replacing the old `app.register_blueprint(posts_bp)`):

        from routes.student.posts import register_posts_blueprints
        register_posts_blueprints(app)
    """
    for bp in _SUB_BLUEPRINTS:
        app_or_bp.register_blueprint(bp)
