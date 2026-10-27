"""
Tests for services/push_notifications.py.

Firebase Admin SDK calls are mocked throughout — these tests never touch
a real Firebase project. PushNotificationService._initialized is a
class-level flag, so every test resets it via the autouse
`reset_push_service_state` fixture to avoid state leaking between tests
(including from whatever order other test modules happen to run in).

Previously zero tests existed for this module at all.
"""

from unittest.mock import Mock, patch

import pytest

import services.push_notifications as push_notifications
from services.push_notifications import (
    PushNotificationService,
    notify_streak_updated,
    notify_reaction_received,
    notify_help_request,
    notify_became_champion,
    notify_streak_at_risk,
    notify_solution_submitted,
    notify_feedback_received,
    notify_assignment_due_soon,
)


@pytest.fixture(autouse=True)
def reset_push_service_state():
    """
    PushNotificationService._initialized is a class attribute shared
    across every test in the process — reset it before and after each
    test so one test's initialize()/failure doesn't leak into the next.
    """
    original = PushNotificationService._initialized
    PushNotificationService._initialized = False
    yield
    PushNotificationService._initialized = original


class _FakeUser:
    """Minimal stand-in for a User model instance — just needs .fcm_token."""
    def __init__(self, fcm_token=None):
        self.fcm_token = fcm_token


# ============================================================================
# PushNotificationService.initialize
# ============================================================================

def test_initialize_skips_when_credentials_file_missing():
    with patch.object(push_notifications.os.path, "exists", return_value=False):
        with patch.object(push_notifications.firebase_admin, "initialize_app") as mock_init:
            PushNotificationService.initialize()

    mock_init.assert_not_called()
    assert PushNotificationService._initialized is False


def test_initialize_succeeds_when_credentials_file_present():
    with patch.object(push_notifications.os.path, "exists", return_value=True), \
         patch.object(push_notifications.credentials, "Certificate", return_value=Mock()), \
         patch.object(push_notifications.firebase_admin, "initialize_app") as mock_init:
        PushNotificationService.initialize()

    mock_init.assert_called_once()
    assert PushNotificationService._initialized is True


def test_initialize_is_idempotent_once_already_initialized():
    PushNotificationService._initialized = True

    with patch.object(push_notifications.firebase_admin, "initialize_app") as mock_init:
        PushNotificationService.initialize()

    mock_init.assert_not_called()


def test_initialize_catches_exception_and_leaves_uninitialized():
    with patch.object(push_notifications.os.path, "exists", return_value=True), \
         patch.object(push_notifications.credentials, "Certificate", side_effect=RuntimeError("boom")):
        PushNotificationService.initialize()  # must not raise

    assert PushNotificationService._initialized is False


def test_initialize_logs_to_app_logger_when_app_instance_provided():
    fake_app = Mock()
    with patch.object(push_notifications.os.path, "exists", return_value=False):
        PushNotificationService.initialize(app=fake_app)

    fake_app.logger.warning.assert_called_once()


# ============================================================================
# PushNotificationService.send_notification
# ============================================================================

def test_send_notification_returns_false_when_not_initialized():
    PushNotificationService._initialized = False

    result = PushNotificationService.send_notification("some-token", "Title", "Body")

    assert result is False


def test_send_notification_returns_false_when_no_fcm_token():
    PushNotificationService._initialized = True

    result = PushNotificationService.send_notification(None, "Title", "Body")

    assert result is False


def test_send_notification_happy_path_calls_messaging_send_and_returns_true():
    PushNotificationService._initialized = True

    with patch.object(push_notifications.messaging, "send", return_value="message-id-123") as mock_send:
        result = PushNotificationService.send_notification(
            "device-token", "Hello", "World", data={"k": "v"}, badge_count=3
        )

    assert result is True
    mock_send.assert_called_once()
    sent_message = mock_send.call_args[0][0]
    assert sent_message.token == "device-token"
    assert sent_message.notification.title == "Hello"
    assert sent_message.notification.body == "World"
    assert sent_message.data == {"k": "v"}


def test_send_notification_unregistered_token_returns_false_without_raising():
    PushNotificationService._initialized = True

    with patch.object(
        push_notifications.messaging, "send",
        side_effect=push_notifications.messaging.UnregisteredError("gone"),
    ):
        result = PushNotificationService.send_notification("stale-token", "Title", "Body")

    assert result is False


def test_send_notification_generic_exception_returns_false_without_raising():
    PushNotificationService._initialized = True

    with patch.object(push_notifications.messaging, "send", side_effect=RuntimeError("network down")):
        result = PushNotificationService.send_notification("device-token", "Title", "Body")

    assert result is False


def test_send_notification_defaults_data_to_empty_dict_when_none():
    PushNotificationService._initialized = True

    with patch.object(push_notifications.messaging, "send", return_value="id") as mock_send:
        PushNotificationService.send_notification("device-token", "Title", "Body", data=None)

    sent_message = mock_send.call_args[0][0]
    assert sent_message.data == {}


# ============================================================================
# PushNotificationService.send_multicast
# ============================================================================

def test_send_multicast_returns_all_failures_when_not_initialized():
    PushNotificationService._initialized = False

    result = PushNotificationService.send_multicast(["t1", "t2", "t3"], "Title", "Body")

    assert result == {"success": 0, "failure": 3}


def test_send_multicast_empty_token_list_returns_zero_zero():
    PushNotificationService._initialized = True

    result = PushNotificationService.send_multicast([], "Title", "Body")

    assert result == {"success": 0, "failure": 0}


def test_send_multicast_happy_path_returns_counts_from_response():
    PushNotificationService._initialized = True
    fake_response = Mock(success_count=2, failure_count=1, responses=[
        Mock(success=True, exception=None),
        Mock(success=True, exception=None),
        Mock(success=False, exception=RuntimeError("bad token")),
    ])

    with patch.object(push_notifications.messaging, "send_each_for_multicast", return_value=fake_response):
        result = PushNotificationService.send_multicast(["t1", "t2", "t3"], "Title", "Body")

    assert result == {"success": 2, "failure": 1}


def test_send_multicast_exception_returns_all_failures_without_raising():
    """
    Unlike the happy-path test above, this one doesn't depend on
    messaging.send_multicast actually existing on the installed SDK —
    it patches the *service's* behavior indirectly by making the real
    call path fail, which happens unconditionally right now regardless
    of this test's own mocking (see the xfail'd test above). Patching
    PushNotificationService.send_multicast's inner call target directly
    via the messaging module would hit the same missing-attribute
    problem, so this exercises the documented fail-safe contract
    (never raise, always return a failure-shaped dict) through a
    MagicMock stand-in for the whole messaging module instead.
    """
    PushNotificationService._initialized = True

    with patch.object(push_notifications, "messaging") as mock_messaging:
        mock_messaging.send_each_for_multicast.side_effect = RuntimeError("down")
        result = PushNotificationService.send_multicast(["t1", "t2"], "Title", "Body")

    assert result == {"success": 0, "failure": 2}


# ============================================================================
# Notification helpers — no fcm_token short-circuits every one of them
# ============================================================================

@pytest.mark.parametrize(
    "helper,args",
    [
        (notify_streak_updated, (5,)),
        (notify_reaction_received, ("Alice", "🔥", "Homework")),
        (notify_help_request, ("Bob", "Calculus")),
        (notify_became_champion, ("Weekly",)),
        (notify_streak_at_risk, (3,)),
        (notify_solution_submitted, ("Helper", "Assignment")),
        (notify_feedback_received, ("Requester", "Assignment")),
        (notify_assignment_due_soon, ("Assignment", 2)),
    ],
)
def test_notification_helper_returns_false_without_fcm_token(helper, args):
    user = _FakeUser(fcm_token=None)

    with patch.object(PushNotificationService, "send_notification") as mock_send:
        result = helper(user, *args)

    assert result is False
    mock_send.assert_not_called()


# ============================================================================
# Notification helpers — happy paths delegate to send_notification
# ============================================================================

def test_notify_streak_updated_builds_expected_title_for_new_record():
    user = _FakeUser(fcm_token="device-token")

    with patch.object(PushNotificationService, "send_notification", return_value=True) as mock_send:
        result = notify_streak_updated(user, streak_count=10, is_new_record=True)

    assert result is True
    args, kwargs = mock_send.call_args
    token, title, body, data = args
    assert token == "device-token"
    assert "New Record" in title
    assert "10-day" in body
    assert data["type"] == "streak_updated"
    assert data["is_new_record"] == "True"


def test_notify_reaction_received_includes_emoji_and_assignment_in_body():
    user = _FakeUser(fcm_token="device-token")

    with patch.object(PushNotificationService, "send_notification", return_value=True) as mock_send:
        notify_reaction_received(user, "Alice", "🔥", "Homework 3")

    _, _, body, data = mock_send.call_args[0]
    assert "🔥" in body
    assert "Homework 3" in body
    assert data["from_user"] == "Alice"


def test_notify_became_champion_with_subject_uses_subject_title():
    user = _FakeUser(fcm_token="device-token")

    with patch.object(PushNotificationService, "send_notification", return_value=True) as mock_send:
        notify_became_champion(user, "Weekly Champion", subject="Physics")

    _, title, _, data = mock_send.call_args[0]
    assert "Physics" in title
    assert data["subject"] == "Physics"


def test_notify_became_champion_without_subject_uses_champion_type_title():
    user = _FakeUser(fcm_token="device-token")

    with patch.object(PushNotificationService, "send_notification", return_value=True) as mock_send:
        notify_became_champion(user, "Top Helper", subject=None)

    _, title, _, data = mock_send.call_args[0]
    assert "Top Helper" in title
    assert data["subject"] == ""


def test_notify_assignment_due_soon_includes_hours_remaining():
    user = _FakeUser(fcm_token="device-token")

    with patch.object(PushNotificationService, "send_notification", return_value=True) as mock_send:
        notify_assignment_due_soon(user, "Essay Draft", hours_remaining=4)

    _, _, body, data = mock_send.call_args[0]
    assert "4 hours" in body
    assert data["hours"] == "4"
