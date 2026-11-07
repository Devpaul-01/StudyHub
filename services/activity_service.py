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

record_live_activity is the only function in this module (or anywhere
else) that writes to ActivityFeed, and the only entry point for pushing
its events over WebSocket — but "pushing the WebSocket event" is now two
related paths, not one: _broadcast_to_connections (always, every call —
the original _create_activity's entire behavior) and, additionally when
scope="global", _broadcast_to_global_pulse, which sends an anonymized
version (anonymize_for_global) to anyone subscribed to the shared
activity_pulse:global room, not just the actor's known connections.

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


# Activity types whose feed card needs the actor's real identity even on
# the global/campus-pulse scope — e.g. user_signed_up's "Connect" button
# needs a real user_id to send a connection request to. Every other
# global-scope activity is anonymized (see anonymize_for_global). Kept as
# a small, explicit allowlist rather than a per-call flag, so adding a new
# un-anonymized type later is a one-line, reviewable change here rather
# than something a caller could opt into by accident.
GLOBAL_SCOPE_IDENTITY_ALLOWED = frozenset({"user_signed_up"})


def record_live_activity(
    user_id: int,
    activity_type: str,
    data: dict,
    scope: str = "connections",
) -> ActivityFeed | None:
    """Create an ActivityFeed entry and push it live over WebSocket.
    Auto-expires after ACTIVITY_EXPIRY_HOURS.

    scope="connections" (default): fan out via emit_to_user to the
        actor's accepted connections only — this is the entire behavior
        record_live_activity had before scope existed, unchanged here.
    scope="global": do the above, AND ALSO broadcast an anonymized
        version (see anonymize_for_global) to the shared
        "activity_pulse:global" room every subscribed client receives,
        regardless of whether they're connected to the actor.

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
        if scope == "global":
            _broadcast_to_global_pulse(user, activity)

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


def _broadcast_to_global_pulse(user: User, activity: ActivityFeed) -> None:
    """Broadcast an anonymized version of `activity` to every socket
    subscribed to the shared 'activity_pulse:global' room (see
    services/websocket_messages.py's activity_feed:subscribe handler).

    Imported locally for the same reason _broadcast_to_connections does.
    """
    from services.websocket_messages import message_ws_manager

    message_ws_manager.emit_to_room(
        "activity_pulse:global",
        "activity_pulse_update",
        anonymize_for_global(user, activity),
    )


def anonymize_for_global(user: User, activity: ActivityFeed) -> dict:
    """Strip the actor's identity before broadcasting to strangers.

    Public (no leading underscore), unlike _broadcast_to_connections and
    _broadcast_to_global_pulse above: GET /activity/pulse in
    routes/student/homework_system.py also needs this exact shaping logic
    for its initial-page-load response, so a client sees an identical
    payload shape whether a row arrived via that GET or over the
    activity_pulse_update WebSocket event this function also feeds.

    This is what makes the opt-out default (show_in_activity_feed=True)
    safe to ship: a user who never opts out is still only ever *named* to
    people they're actually connected with (_broadcast_to_connections,
    which targets specific known recipients) — the global/campus-pulse
    feed anyone can subscribe to gets department/subject-level aggregate
    info only, never a name, by default.

    The one deliberate exception is GLOBAL_SCOPE_IDENTITY_ALLOWED: a
    user_signed_up card needs a real user_id for its "Connect" button to
    send a connection request to, and a new member's name + department is
    already public via Discovery search today (see
    services/search_service.py's profile_private check, applied the same
    way here) — there's no privacy reason to strip it, and stripping it
    would leave the Connect button with nothing to act on. Still gated by
    set_profile_private, same as Discovery, in case a user has that on.
    """
    data = activity.activity_data or {}

    if activity.activity_type in GLOBAL_SCOPE_IDENTITY_ALLOWED:
        profile_private = (user.privacy_settings or {}).get(
            "set_profile_private", False
        )
        if not profile_private:
            return {
                "id": activity.id,
                "type": activity.activity_type,
                "user_id": user.id,
                "name": user.name,
                "data": data,
                "created_at": activity.created_at.isoformat(),
            }
        # profile_private wins even for an allowlisted type — fall through
        # to the standard anonymized shape below.

    return {
        "id": activity.id,
        "type": activity.activity_type,
        "subject": data.get("subject"),
        "department": data.get("department"),
        "created_at": activity.created_at.isoformat(),
    }
