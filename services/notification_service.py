"""
services/notification_service.py

Thin wrapper around Notification model writes, so the many call sites
across the codebase that each independently did:

    notification = Notification(user_id=..., title=..., body=..., ...)
    db.session.add(notification)

now call a named function instead — one place owns the field list per
notification "type," which is what actually prevents the kind of drift
that caused the badges.py link-format bug (missing f-string prefix) to
ship in the first place.

Per Document 2 §5's convention: these functions call db.session.add(...)
but do NOT commit — the caller's existing transaction (award a badge,
accept a connection, etc.) stays in charge of the commit, exactly like
every other service in this migration.

WebSocket broadcast integration:
  services/websocket_events.py (the manager you supplied) is the LEGACY
  real-time manager — messages.py already imports a NEWER, messaging-
  specific manager from `services.websocket_messages` (not supplied) for
  DM real-time events, per the comment in messages.py::get_conversation_messages:
    "this is messaging functionality, so it now goes through the active
    message_ws_manager ... that manager is being kept for non-messaging
    functionality only (e.g. Homework activity tracking)."

  So broadcasting a notification has two possible transports depending on
  notification type, and I only have one of them. This module calls
  websocket_events.ws_manager for the general case (badge/reputation/etc.)
  and leaves a single, clearly marked seam (_broadcast_via_message_ws) for
  wiring in services.websocket_messages once that file is supplied — it is
  NOT guessed at or stubbed with fake behavior.
"""

from __future__ import annotations

import logging

from extensions import db
from models import Notification, Badge
from services import counter_cache_service

logger = logging.getLogger(__name__)


# ============================================================================
# WEBSOCKET BROADCAST (best-effort — never blocks or fails the caller)
# ============================================================================

def _broadcast(user_id: int, notification: Notification) -> None:
    """
    Best-effort real-time push of a newly created notification.

    Uses services.websocket_events.ws_manager (supplied). Failures are
    swallowed and logged — a notification that fails to push in real time
    still exists as a row and will show up next time the client polls/
    fetches /profile/notifications/all, so this must never raise and
    break the caller's actual (already-committed-elsewhere) business
    operation.
    """
    try:
        from services.websocket_events import ws_manager

        payload = {
            "id": notification.id,
            "title": notification.title,
            "body": notification.body,
            "type": notification.notification_type,
            "link": notification.link,
            "related_type": notification.related_type,
            "related_id": notification.related_id,
            "created_at": (
                notification.created_at.isoformat() if notification.created_at else None
            ),
        }

        if hasattr(ws_manager, "emit_to_user"):
            ws_manager.emit_to_user(user_id, "new_notification", payload)
        elif getattr(ws_manager, "socketio", None) and user_id in getattr(ws_manager, "online_users", {}):
            ws_manager.socketio.emit("new_notification", payload, room=f"user_{user_id}")

    except Exception as exc:
        logger.warning(f"Notification broadcast failed (non-critical): {exc}")


# ============================================================================
# GENERIC ENTRY POINT
# ============================================================================

def notify(
    user_id: int,
    title: str,
    body: str,
    notification_type: str,
    *,
    related_type: str | None = None,
    related_id: int | None = None,
    link: str | None = None,
) -> Notification:
    """
    Create (and best-effort broadcast) a notification. Does not commit —
    caller's transaction owns that, same as every other service function.

    Use this directly for one-off notifications; use the named
    notify_*() functions below for the notification "templates" that are
    repeated across many call sites, so the field list for each type only
    exists in one place.

    Unread-count cache (plan §4.7/§5.6): every notification created here
    increments the Redis counter at sh:1:notif:unread:{user_id} via a
    native atomic INCR (counter_cache_service.increment_unread_notification_count),
    never a read-modify-write — see plan §7.1 for why that distinction
    matters under concurrent notify() calls for the same user. This call
    is deliberately placed alongside db.session.flush()/_broadcast(),
    which are also both best-effort/non-transactional — like _broadcast(),
    a Redis hiccup here must never surface as an error to notify()'s
    caller (counter_cache_service already fails open internally, per its
    own module docstring, so no try/except is needed at this call site).

    This function being the single funnel point for the increment is only
    correct once every direct `Notification(...)` construction elsewhere
    in the codebase has been migrated to call notify() instead — see plan
    §5.3/§17.6 for the enumerated migration this depends on.
    """
    notification = Notification(
        user_id=user_id,
        title=title,
        body=body,
        notification_type=notification_type,
        related_type=related_type,
        related_id=related_id,
        link=link,
    )
    db.session.add(notification)
    db.session.flush()  # populate notification.id/created_at for the broadcast payload

    counter_cache_service.increment_unread_notification_count(user_id)

    _broadcast(user_id, notification)

    return notification


# ============================================================================
# NAMED TEMPLATES  (one per genuinely-repeated notification shape)
# ============================================================================

def notify_badge_earned(user_id: int, badge: Badge) -> Notification:
    """
    Badge-earned notification. Extracted from badges.py::check_and_award_badge.

    FIX (carried over from badges.py): link uses an f-string, not the
    literal string "#badge-{badge.id}" that shipped from a missing f-prefix
    in the original code.
    """
    return notify(
        user_id=user_id,
        title=f"Badge Earned: {badge.name}!",
        body=f"{badge.icon} {badge.description}",
        notification_type="badge_earned",
        related_type="badge",
        related_id=badge.id,
        link=f"/student/badges/#badge-{badge.id}",
    )


def notify_level_up(user_id: int, new_level: dict) -> Notification:
    """
    Reputation level-up notification.

    Not yet called from any of the currently-migrated route files (none
    of badges.py/leaderboard.py/analytics.py currently construct this
    notification inline), but specified here per Document 2 §3.1 /§3.9 so
    reputation_service.award_reputation has a stable named function to
    call once that service is wired in.
    """
    level_name = new_level.get("name", "a new level")
    icon = new_level.get("icon", "🎉")
    return notify(
        user_id=user_id,
        title=f"{icon} Level Up: {level_name}!",
        body=f"You've reached the {level_name} reputation level. Keep it up!",
        notification_type="reputation_level_up",
        related_type="user",
        related_id=user_id,
    )


def notify_connection_request(user_id: int, requester_name: str, requester_id: int) -> Notification:
    """New connection request received. Extracted from connections.py."""
    return notify(
        user_id=user_id,
        title="New Connection Request",
        body=f"{requester_name} wants to connect with you",
        notification_type="connection_request",
        related_type="user",
        related_id=requester_id,
    )


def notify_connection_accepted(user_id: int, accepter_name: str, accepter_id: int) -> Notification:
    """Connection request accepted. Extracted from connections.py (several call sites)."""
    return notify(
        user_id=user_id,
        title="Connection Accepted",
        body=f"{accepter_name} accepted your connection request",
        notification_type="connection_accepted",
        related_type="user",
        related_id=accepter_id,
    )


def notify_instant_connection(user_id: int, other_name: str, other_id: int, compatibility_score: int, *, is_receiver: bool) -> Notification:
    """
    Instant (auto-accepted, >=70% compatibility) connection notification.
    Extracted from connections.py::send_connection_request. Body text
    differs slightly between the two parties in the original code, so
    `is_receiver` picks the matching copy.
    """
    if is_receiver:
        title = f"🎉 Instant Connection with {other_name}"
        body = f"You're {compatibility_score}% compatible! Start chatting now."
    else:
        title = f"🎉 Instantly Connected with {other_name}"
        body = f"High compatibility match ({compatibility_score}%)! Start chatting now."

    return notify(
        user_id=user_id,
        title=title,
        body=body,
        notification_type="instant_connection",
        related_type="user",
        related_id=other_id,
    )


def notify_homework_help_offer(user_id: int, helper_name: str, assignment_title: str, submission_id: int) -> Notification:
    """Someone offered to help with an assignment. Extracted from homework_system.py."""
    return notify(
        user_id=user_id,
        title="Someone wants to help! 🎓",
        body=f"{helper_name} offered to help with '{assignment_title}'",
        notification_type="homework_help_offer",
        related_type="homework_submission",
        related_id=submission_id,
    )


def notify_homework_solution_submitted(user_id: int, helper_name: str, assignment_title: str, submission_id: int) -> Notification:
    """Solution submitted for review. Extracted from homework_system.py."""
    return notify(
        user_id=user_id,
        title="Solution received! 📝",
        body=f"{helper_name} submitted a solution for '{assignment_title}'",
        notification_type="homework_solution_submitted",
        related_type="homework_submission",
        related_id=submission_id,
    )


def notify_homework_feedback_received(user_id: int, requester_name: str, assignment_title: str) -> Notification:
    """Feedback given on a submitted solution. Extracted from homework_system.py."""
    return notify(
        user_id=user_id,
        title="Feedback received! 🎉",
        body=f"{requester_name} reviewed your solution for '{assignment_title}'",
        notification_type="homework_feedback_received",
    )


def notify_homework_help_cancelled(user_id: int, canceller_name: str, assignment_title: str, *, canceller_is_requester: bool) -> Notification:
    """
    Help request cancelled/withdrawn. Extracted from
    homework_system.py::cancel_help_request — body text differs depending
    on which side cancelled.
    """
    if canceller_is_requester:
        body = f"{canceller_name} cancelled the help request for '{assignment_title}'"
    else:
        body = f"{canceller_name} can no longer help with '{assignment_title}'"

    return notify(
        user_id=user_id,
        title="Help request cancelled",
        body=body,
        notification_type="homework_help_cancelled",
    )


def notify_welcome(user_id: int, name: str, *, features_link: str, rich: bool = False) -> Notification:
    """
    Welcome notification on registration. Extracted from auth.py (register()
    and google_callback() each construct a slightly different body — `rich`
    picks the fuller feature-list copy used by register(); Google signup
    uses the short version).
    """
    if rich:
        body = (
            f"Welcome @{name}! 🎓\n\n"
            "Discover what makes StudyHub special:\n\n"
            "📚 Smart Q&A - Get help from peers and experts\n"
            "🧵 Study Threads - Join private study groups\n"
            "🤝 Study Buddy - Find your perfect study partner\n"
            "🏆 Earn Badges - Showcase your achievements\n"
            "📊 Track Progress - GitHub-style activity heatmaps\n\n"
            "Ready to start? Complete your profile and ask your first question!\n\n"
            "💡 Pro tip: Be helpful to earn reputation points and unlock badges!"
        )
    else:
        body = f"Welcome {name}! 🎓 Complete your profile to find the perfect study partners."

    return notify(
        user_id=user_id,
        title="🎉 Welcome to StudyHub!",
        body=body,
        notification_type="welcome",
        related_type="user",
        related_id=user_id,
        link=features_link,
    )


