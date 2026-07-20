"""
Firebase Cloud Messaging Service
Handles push notifications for web app

Setup:
1. Get service account key from Firebase Console
2. Save as firebase-credentials.json in project root
3. Add to .env: FIREBASE_CREDENTIALS_PATH=firebase-credentials.json

FIXED: Works outside Flask application context
"""

import os
import firebase_admin
from firebase_admin import credentials, messaging
import logging

# Setup basic logging (doesn't require Flask app context)
logger = logging.getLogger(__name__)


class PushNotificationService:
    """Firebase Cloud Messaging wrapper"""
    
    _initialized = False
    
    @classmethod
    def initialize(cls, app=None):
        """
        Initialize Firebase Admin SDK (call once on app startup)
        
        Args:
            app: Optional Flask app instance (if available)
        """
        if cls._initialized:
            return
        
        try:
            # Get credentials path from environment
            cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH', 'firebase-credentials.json')
            
            if not os.path.exists(cred_path):
                msg = f"Firebase credentials not found at {cred_path}"
                logger.warning(msg)
                if app:
                    app.logger.warning(msg)
                return
            
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            cls._initialized = True
            
            msg = "✅ Firebase initialized successfully"
            logger.info(msg)
            if app:
                app.logger.info(msg)
            
        except Exception as e:
            msg = f"Firebase initialization error: {e}"
            logger.error(msg)
            if app:
                app.logger.error(msg)
    
    @staticmethod
    def send_notification(fcm_token, title, body, data=None, badge_count=None):
        """
        Send push notification to single device
        
        Args:
            fcm_token: User's FCM device token
            title: Notification title
            body: Notification body text
            data: Optional dict of custom data
            badge_count: Optional badge count for app icon
        
        Returns:
            bool: True if sent successfully, False otherwise
        """
        if not PushNotificationService._initialized:
            logger.warning("Firebase not initialized, skipping notification")
            return False
        
        if not fcm_token:
            return False
        
        try:
            # Build notification
            notification = messaging.Notification(
                title=title,
                body=body
            )
            
            # Build web config with icon and badge
            web_config = messaging.WebpushConfig(
                notification=messaging.WebpushNotification(
                    title=title,
                    body=body,
                    icon='/static/images/logo.png',
                    badge='/static/images/badge.png'
                ),
                fcm_options=messaging.WebpushFCMOptions(
                    link='/'  # Where to navigate on click
                )
            )
            
            # Build message
            message = messaging.Message(
                notification=notification,
                data=data or {},
                token=fcm_token,
                webpush=web_config
            )
            
            # Send
            response = messaging.send(message)
            logger.info(f"✅ Push notification sent: {response}")
            return True
            
        except messaging.UnregisteredError:
            # Token is invalid/expired, should remove from database
            logger.warning(f"Invalid FCM token: {fcm_token[:20]}...")
            return False
            
        except Exception as e:
            logger.error(f"Push notification error: {e}")
            return False
    
    @staticmethod
    def send_multicast(fcm_tokens, title, body, data=None):
        """
        Send notification to multiple devices at once
        
        Args:
            fcm_tokens: List of FCM device tokens
            title: Notification title
            body: Notification body
            data: Optional custom data
        
        Returns:
            dict: Success and failure counts
        """
        if not PushNotificationService._initialized:
            return {'success': 0, 'failure': len(fcm_tokens)}
        
        if not fcm_tokens:
            return {'success': 0, 'failure': 0}
        
        try:
            # Build notification
            notification = messaging.Notification(title=title, body=body)
            
            # Build multicast message
            message = messaging.MulticastMessage(
                notification=notification,
                data=data or {},
                tokens=fcm_tokens
            )
            
            # Send
            response = messaging.send_multicast(message)
            
            logger.info(
                f"✅ Multicast sent: {response.success_count} success, {response.failure_count} failures"
            )
            
            # Log any failures
            if response.failure_count > 0:
                for idx, resp in enumerate(response.responses):
                    if not resp.success:
                        logger.warning(
                            f"Failed to send to token {idx}: {resp.exception}"
                        )
            
            return {
                'success': response.success_count,
                'failure': response.failure_count
            }
            
        except Exception as e:
            logger.error(f"Multicast error: {e}")
            return {'success': 0, 'failure': len(fcm_tokens)}


# ============================================================================
# NOTIFICATION HELPERS
# ============================================================================

def notify_streak_updated(user, streak_count, is_new_record=False):
    """Send notification when user's streak is updated"""
    if not user.fcm_token:
        return False
    
    title = "🔥 Streak Updated!" if not is_new_record else "🎉 New Record!"
    body = f"You're on a {streak_count}-day helping streak!"
    
    data = {
        'type': 'streak_updated',
        'streak_count': str(streak_count),
        'is_new_record': str(is_new_record)
    }
    
    return PushNotificationService.send_notification(
        user.fcm_token, title, body, data
    )


def notify_reaction_received(user, from_user_name, reaction_emoji, assignment_title):
    """Send notification when someone reacts to your help"""
    if not user.fcm_token:
        return False
    
    title = f"{from_user_name} reacted to your help!"
    body = f"{reaction_emoji} on '{assignment_title}'"
    
    data = {
        'type': 'reaction_received',
        'from_user': from_user_name,
        'reaction': reaction_emoji
    }
    
    return PushNotificationService.send_notification(
        user.fcm_token, title, body, data
    )


def notify_help_request(user, requester_name, subject):
    """Notify user when someone in their network needs help"""
    if not user.fcm_token:
        return False
    
    title = f"{requester_name} needs help!"
    body = f"Help needed with {subject}"
    
    data = {
        'type': 'help_request',
        'requester': requester_name,
        'subject': subject
    }
    
    return PushNotificationService.send_notification(
        user.fcm_token, title, body, data
    )


def notify_became_champion(user, champion_type, subject=None):
    """Notify user they became a weekly champion"""
    if not user.fcm_token:
        return False
    
    if subject:
        title = f"🏆 You're the {subject} Champion!"
        body = "You helped the most people in this subject this week!"
    else:
        title = f"👑 {champion_type}!"
        body = "Congratulations on your achievement!"
    
    data = {
        'type': 'became_champion',
        'champion_type': champion_type,
        'subject': subject or ''
    }
    
    return PushNotificationService.send_notification(
        user.fcm_token, title, body, data
    )


def notify_streak_at_risk(user, streak_count):
    """Remind user their streak is at risk"""
    if not user.fcm_token:
        return False
    
    title = "⚠️ Streak at Risk!"
    body = f"Help someone today to keep your {streak_count}-day streak alive!"
    
    data = {
        'type': 'streak_at_risk',
        'streak_count': str(streak_count)
    }
    
    return PushNotificationService.send_notification(
        user.fcm_token, title, body, data
    )


# ============================================================================
# ADDITIONAL NOTIFICATION TYPES FOR HOMEWORK SYSTEM
# ============================================================================1

def notify_solution_submitted(user, helper_name, assignment_title):
    """Notify when helper submits solution"""
    if not user.fcm_token:
        return False
    
    title = f"{helper_name} submitted a solution!"
    body = f"Check their solution for '{assignment_title}'"
    
    data = {
        'type': 'solution_submitted',
        'helper': helper_name,
        'assignment': assignment_title
    }
    
    return PushNotificationService.send_notification(
        user.fcm_token, title, body, data
    )


def notify_feedback_received(user, requester_name, assignment_title):
    """Notify when requester gives feedback"""
    if not user.fcm_token:
        return False
    
    title = f"{requester_name} left feedback!"
    body = f"See their response to your help on '{assignment_title}'"
    
    data = {
        'type': 'feedback_received',
        'requester': requester_name,
        'assignment': assignment_title
    }
    
    return PushNotificationService.send_notification(
        user.fcm_token, title, body, data
    )


def notify_assignment_due_soon(user, assignment_title, hours_remaining):
    """Notify when assignment is due soon"""
    if not user.fcm_token:
        return False
    
    title = "⏰ Assignment Due Soon!"
    body = f"'{assignment_title}' is due in {hours_remaining} hours"
    
    data = {
        'type': 'assignment_due_soon',
        'assignment': assignment_title,
        'hours': str(hours_remaining)
    }
    
    return PushNotificationService.send_notification(
        user.fcm_token, title, body, data
    )
