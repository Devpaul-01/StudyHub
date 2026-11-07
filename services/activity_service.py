"""
services/activity_service.py

Central write-path for the Live Activity (social) feed — the public,
cross-feature stream of "what your connections are doing right now"
backed by the ActivityFeed model.

Promoted from routes/student/homework_system.py::_create_activity, which
was the only place in the app that wrote to ActivityFeed, and only ever
called from one route (submit_solution). Behavior here is intentionally
a faithful port of that function, with one addition: a privacy check
(User.privacy_settings["show_in_activity_feed"]) that the original did
not have, since ActivityFeed had no opt-out at all before this.

NOT the same thing as services/auth_service.py::record_activity() or
services/post_service.py::update_user_activity() — both of those write
to the unrelated, private UserActivity table (a per-day engagement
counter feeding the analytics heatmap), not ActivityFeed. The name
record_live_activity is deliberately different from record_activity to
avoid a second, unrelated function sharing an identical name two files
apart in the same codebase.

Per Document 2 §2's layering rule, services generally don't call
db.session.commit() themselves. This module is a deliberate, documented
exception to that rule, same as badge_service.py's check_and_award_badge:
record_live_activity needs the row's generated id and created_at before
it can build the WebSocket payload, so the commit has to happen inside
the function rather than being left to the caller.

Consumers:
  - routes/student/homework_system.py::submit_solution — the existing
    caller, switched over from the old _create_activity in a follow-up
    commit.
  - Every other route listed in the Live Activity design doc's §5.4
    activity-type table, wired up incrementally.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import or_

from extensions import db
from models import ActivityFeed, Connection, User

logger = logging.getLogger(__name__)

ACTIVITY_EXPIRY_HOURS = 24


def record_live_activity(
    user_id: int,
    activity_type: str,
    data: dict,
) -> ActivityFeed | None:
    """Create an ActivityFeed entry and push it to the actor's accepted
    connections over WebSocket. Auto-expires after ACTIVITY_EXPIRY_HOURS.

    Returns None, without writing anything, if the user has opted out via
    privacy_settings["show_in_activity_feed"] (default True — opt-out,
    not opt-in). Checked here, once, so every call site gets this for
    free rather than needing to remember to check it itself.

    Broad except is intentional (same reasoning the original
    _create_activity documented): this is a best-effort side channel
    called from mutation routes whose primary operation must not fail or
    roll back just because the activity-feed write or WebSocket broadcast
    did. Every failure mode here is swallowed and logged, never raised.
    """
    try:
        user = User.query.get(user_id)
        if not user:
            return None
        if not (user.privacy_settings or {}).get("show_in_activity_feed", True):
            return None

        activity = ActivityFeed(
            user_id=user_id,
            activity_type=activity_type,
            activity_data=data,
            created_at=datetime.datetime.utcnow(),
            expires_at=datetime.datetime.utcnow()
            + datetime.timedelta(hours=ACTIVITY_EXPIRY_HOURS),
        )
        db.session.add(activity)
        db.session.commit()

        _broadcast_to_connections(user_id, activity)

        return activity
    except Exception:
        logger.error("record_live_activity failed", exc_info=True)
        db.session.rollback()
        return None


def _broadcast_to_connections(user_id: int, activity: ActivityFeed) -> None:
    """Push `activity` to every accepted connection of `user_id` over
    WebSocket, matching exactly what get_activity_feed's own query
    (ActivityFeed.user_id.in_(connection_ids)) already promises those
    same connections on their next poll/reload — this keeps the live and
    polled paths in agreement instead of inventing separate fan-out
    semantics for each.

    Imported locally (not at module level) to avoid a service-layer ->
    Flask-app-layer import at import time, matching how the original
    _create_activity imported message_ws_manager.
    """
    from services.websocket_messages import message_ws_manager

    connections = Connection.query.filter(
        or_(
            Connection.requester_id == user_id,
            Connection.receiver_id == user_id,
        ),
        Connection.status == "accepted",
    ).all()

    payload = {
        "id": activity.id,
        "type": activity.activity_type,
        "user_id": user_id,
        "data": activity.activity_data,
        "created_at": activity.created_at.isoformat(),
    }

    for conn in connections:
        other_id = (
            conn.receiver_id if conn.requester_id == user_id else conn.requester_id
        )
        message_ws_manager.emit_to_user(other_id, "activity_feed_update", payload)
