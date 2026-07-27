"""
services/online_status_service.py

Single source of truth for "is this user online" logic.

Extracted from utils.py::get_user_online_status (Document 1 §6.4 / Document 2
§3.7). That was the more complete of the pre-existing implementations
(handles the in_study_session special case), so it wins as the canonical
version — connections.py's several ad hoc `minutes_ago < 30` inline checks
(get_online_connections, get_online_connections_by_department,
get_available_connections) are expected to call is_user_online(...) instead
of re-deriving the threshold locally.

Per Document 2 §2's layering rule: no Flask imports, no `request`/`session`/`g`,
no route-level error_response()/jsonify(). Pure functions of User rows.
"""

from __future__ import annotations

import datetime
import logging

from models import User

logger = logging.getLogger(__name__)

# Default "online" threshold, matching the original utils.py behavior and
# connections.py's several hardcoded `30` defaults.
DEFAULT_ONLINE_WINDOW_MINUTES = 30


def is_user_online(user: User, window_minutes: int = DEFAULT_ONLINE_WINDOW_MINUTES) -> bool:
    """
    Return True if `user` counts as online right now.

    A user in an active study session is always considered online
    regardless of `last_active` staleness (matches the original
    get_user_online_status special case).

    `window_minutes` is configurable per call-site — connections.py's
    /connections/online, /connections/online/department, and
    /connections/available-now endpoints each accept a `time_window` query
    param today; this is the single place that threshold is applied.
    """
    if not user:
        return False

    if getattr(user, "in_study_session", False):
        return True

    if not user.last_active:
        return False

    minutes_ago = (datetime.datetime.utcnow() - user.last_active).total_seconds() / 60
    return minutes_ago < window_minutes


def _format_last_active(minutes_ago: float) -> str:
    """Format a minutes-ago float into the short strings the frontend expects."""
    if minutes_ago < 60:
        return f"{int(minutes_ago)}m"
    elif minutes_ago < 1440:  # < 24 hours
        return f"{int(minutes_ago // 60)}h"
    else:
        return f"{int(minutes_ago // 1440)}d"


def get_user_online_status(user_id: int) -> dict:
    """
    Get a user's online status and formatted last-active text.

    Returns:
        {
            "is_online": bool,
            "in_study_session": bool,
            "last_active": str | None   # None while online, else "Xm"/"Xh"/"Xd"/"Never"/"Unknown"
        }

    This is the exact response shape the original utils.py version returned —
    every existing caller (homework_system.py, connections.py, messages.py,
    websocket_events.py) can switch its import with zero changes downstream.
    """
    try:
        user = User.query.get(user_id)

        if not user or not user.last_active:
            return {
                "is_online": False,
                "in_study_session": False,
                "last_active": "Never",
            }

        if user.in_study_session:
            return {
                "is_online": True,
                "in_study_session": True,
                "last_active": None,
            }

        now = datetime.datetime.utcnow()
        minutes_ago = (now - user.last_active).total_seconds() / 60
        online = minutes_ago < DEFAULT_ONLINE_WINDOW_MINUTES

        if online:
            return {
                "is_online": True,
                "in_study_session": False,
                "last_active": None,
            }

        return {
            "is_online": False,
            "in_study_session": False,
            "last_active": _format_last_active(minutes_ago),
        }

    except Exception as e:
        logger.error(f"Online status error: {str(e)}")
        return {
            "is_online": False,
            "in_study_session": False,
            "last_active": "Unknown",
        }


def get_online_status_batch(user_ids: list[int]) -> dict[int, dict]:
    """
    Batch version of get_user_online_status — one query instead of N.

    New helper (not in the original utils.py) so callers that need online
    status for a list of users (e.g. a connections list, a search results
    page) don't need to call the single-user version in a loop. Returns
    {user_id: status_dict} using the exact same status shape as
    get_user_online_status.
    """
    if not user_ids:
        return {}

    users = User.query.filter(User.id.in_(user_ids)).all()
    now = datetime.datetime.utcnow()
    result = {}

    for user in users:
        if not user.last_active:
            result[user.id] = {
                "is_online": False,
                "in_study_session": False,
                "last_active": "Never",
            }
            continue

        if user.in_study_session:
            result[user.id] = {
                "is_online": True,
                "in_study_session": True,
                "last_active": None,
            }
            continue

        minutes_ago = (now - user.last_active).total_seconds() / 60
        online = minutes_ago < DEFAULT_ONLINE_WINDOW_MINUTES

        if online:
            result[user.id] = {
                "is_online": True,
                "in_study_session": False,
                "last_active": None,
            }
        else:
            result[user.id] = {
                "is_online": False,
                "in_study_session": False,
                "last_active": _format_last_active(minutes_ago),
            }

    return result
