"""
PRODUCTION-READY Email Integration with Background Sending
Optimized for limited memory environments like Render free tier
"""

import logging
import os
from datetime import datetime, timedelta
from threading import Thread

# Third-party imports
import jwt
from flask import current_app
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mail import Message

# Local imports
from extensions import mail
from models import User

# Initialize limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)




# ============================================================================
# Document 1 §6.4 / Document 2 §3.4 consolidation (PHASE-1 CORRECTNESS FIX):
#
# get_user_online_status and can_message used to be fully, independently
# reimplemented in this file — a second, silently-driftable copy of exactly
# the logic Document 1 §6.4 and Document 2 §3.4 name as needing ONE
# implementation apiece (services/online_status_service.py and
# services/connection_service.py). The service modules already existed, but
# this file was never actually converted into a shim onto them, so both
# copies were live and could disagree. They're now thin re-exports instead
# of duplicated bodies, so every existing `from utils import
# get_user_online_status` / `from utils import can_message` call site keeps
# working completely unchanged, but there is exactly one implementation.
# ============================================================================

from services.online_status_service import get_user_online_status  # noqa: F401
from services.connection_service import can_message  # noqa: F401

# Initialize limiter

logger = logging.getLogger(__name__)


def get_conversation_partner(conversation, current_user_id):
    """Get the other user in a conversation"""
    if conversation.get("user1_id") == current_user_id:
        return User.query.get(conversation.get("user2_id"))
    return User.query.get(conversation.get("user1_id"))


def create_conversation_key(user1_id, user2_id):
    sorted_ids = sorted([user1_id, user2_id])
    return f"{sorted_ids[0]}-{sorted_ids[1]}"


def get_app_url():
    """Get application URL"""
    url = os.environ.get('CURRENT_URL', 'http://127.0.0.1:5001/')
    if not url.endswith('/'):
        url += '/'
    return url



def generate_verification_token(email):
    """
    Generate a secure JWT token that expires in 5 hours.
    """
    try:
        secret_key = os.environ.get("SECRET_KEY")
        payload = {
            "email": email,
            "exp": datetime.utcnow() + timedelta(hours=5),
            "iat": datetime.utcnow()
        }
        token = jwt.encode(payload, secret_key, algorithm="HS256")
        return token
    except Exception as e:
        print("Token generation error:", e)
        return None



def send_password_reset(email, link):
    """Send password reset email"""
    with current_app.app_context():
        try:
            msg = Message(
                subject="🔐 Reset Your StudyHub Password",
                recipients=[email],
                html=f"""
                <html>
                <body style="font-family: Arial; padding: 40px; text-align: center;">
                    <h1 style="color: #667eea;">Reset Your Password</h1>
                    <p>Click the button below to reset your password:</p>
                    <a href="{link}" style="display: inline-block; margin: 20px 0; padding: 15px 30px; background: #667eea; color: white; text-decoration: none; border-radius: 5px;">Reset Password</a>
                    <p style="color: #999; font-size: 12px;">This link expires in 5 hours.</p>
                </body>
                </html>
                """,
                sender=os.environ.get('MAIL_DEFAULT_SENDER')
            )
            mail.send(msg)
            logger.info(f"✅ Password reset email sent to {email}")
        except Exception as e:
            logger.error(f"❌ Password reset email failed: {e}")

def send_verification_email(email, link):
    """Send email verification link"""
    with current_app.app_context():
        try:
            msg = Message(
                subject="✅ Verify Your StudyHub Email",
                recipients=[email],
                html=f"""
                <html>
                <body style="font-family: Arial; padding: 40px; text-align: center;">
                    <h1 style="color: #667eea;">Welcome to StudyHub!</h1>
                    <p>Click the button below to verify your email address:</p>
                    <a href="{link}" style="display: inline-block; margin: 20px 0; padding: 15px 30px; background: #667eea; color: white; text-decoration: none; border-radius: 5px;">Verify Email</a>
                    <p style="color: #999; font-size: 12px;">This link expires in 5 hours.</p>
                </body>
                </html>
                """,
                sender= os.environ.get('MAIL_DEFAULT_SENDER')
            )
            mail.send(msg)
            logger.info(f"✅ Verification email sent to {email}")
        except Exception as e:
            logger.error(f"❌ Verification email failed: {e}")


def send_async_email(app, msg):
    """Send email in background thread"""
    with app.app_context():
        try:
            mail.send(msg)
            logger.info(f"✅ Background email sent to {msg.recipients[0]}")
        except Exception as e:
            logger.error(f"❌ Background email failed: {e}")


def send_email_now(to_email, subject, html_content, async_send=True):
    """
    Send email with optional async mode for production
    
    Args:
        to_email: Recipient email
        subject: Email subject
        html_content: HTML content
        async_send: If True, send in background thread (default for production)
    
    Returns:
        bool: True if queued/sent, False if failed
    """
    print("\n" + "="*70)
    print("📧 SENDING EMAIL")
    print("="*70)
    print(f"To: {to_email}")
    print(f"Subject: {subject}")
    print(f"Async: {async_send}")
    print("="*70)
    
    try:
        msg = Message(
            subject=subject,
            recipients=[to_email],
            html=html_content,
            sender=current_app.config.get('MAIL_DEFAULT_SENDER')
        )
        
        if async_send:
            # Send in background thread to avoid blocking
            app = current_app._get_current_object()
            thread = Thread(target=send_async_email, args=(app, msg))
            thread.daemon = True  # Thread dies when main process exits
            thread.start()
            print("✅ EMAIL QUEUED FOR BACKGROUND SEND")
            print("="*70 + "\n")
            return True
        else:
            # Synchronous send (for testing only)
            mail.send(msg)
            print("✅ EMAIL SENT SYNCHRONOUSLY!")
            print("="*70 + "\n")
            return True
        
    except Exception as e:
        print(f"❌ FAILED: {e}")
        print("="*70 + "\n")
        logger.error(f"Email send error: {e}", exc_info=True)
        return False


def send_waitlist_welcome_email(to_email, referral_code, position, tier_info):
    """Send welcome email (async by default)"""
    
    print(f"\n🎯 send_waitlist_welcome_email() called for {to_email}")
    
    try:
        app_url = get_app_url()
        referral_link = f"{app_url}?ref={referral_code}"
        
        # Extract benefits (get first 3, or pad if less)
        benefits = tier_info["benefits"]
        benefit1 = benefits[0] if len(benefits) > 0 else "Early Access"
        benefit2 = benefits[1] if len(benefits) > 1 else "Premium Features"
        benefit3 = benefits[2] if len(benefits) > 2 else "Community Access"
        
        # Launch date
        launch_date = "January 2025"
        
        subject = f"🎉 You're #{position} on the StudyHub Waitlist!"
        
        html = f"""
<!DOCTYPE html><html>
<head>
    <meta charset="UTF-8">
</head>
<body style="font-family: 'Segoe UI', Arial, sans-serif; background-color: #0f172a; margin: 0; padding: 0;">
    <div style="max-width: 650px; margin: auto; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.15);">
    
    <!-- Header -->
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 40px 30px; text-align: center; color: white;">
        <h1 style="margin: 0; font-size: 32px; font-weight: 700;">🎉 Welcome to StudyHub!</h1>
        <div style="margin: 20px 0; font-size: 48px; font-weight: 900;">
            #{position}
        </div>
        <p style="margin: 0; font-size: 18px; opacity: 0.95;">You're on the waitlist!</p>
    </div>
    
    <!-- Content -->
    <div style="padding: 40px 30px;">
        
        <!-- Tier Badge -->
        <div style="background: linear-gradient(135deg, #667eea15 0%, #764ba215 100%); padding: 20px; border-radius: 12px; text-align: center; margin-bottom: 30px;">
            <h2 style="margin: 0 0 10px; color: #667eea; font-size: 24px;">{tier_info['tier']}</h2>
            <p style="margin: 0; color: #64748b; font-size: 14px;">Your exclusive tier benefits</p>
        </div>
        
        <!-- Benefits -->
        <div style="margin: 30px 0;">
            <h3 style="color: #1e293b; font-size: 20px; margin-bottom: 15px;">🎁 Your Benefits:</h3>
            <ul style="list-style: none; padding: 0; margin: 0;">
                <li style="margin: 12px 0; color: #1e293b; font-size: 15px; line-height: 1.6;">✅ {benefit1}</li>
                <li style="margin: 12px 0; color: #1e293b; font-size: 15px; line-height: 1.6;">✅ {benefit2}</li>
                <li style="margin: 12px 0; color: #1e293b; font-size: 15px; line-height: 1.6;">✅ {benefit3}</li>
            </ul>
        </div>
        
        <!-- Referral Section -->
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 12px; margin: 30px 0;">
            <h3 style="margin: 0 0 15px; font-size: 22px; text-align: center;">🚀 Skip the Line!</h3>
            <p style="margin: 0 0 20px; text-align: center; opacity: 0.95;">Share your unique link and climb the waitlist!</p>
            
            <div style="background: rgba(255,255,255,0.2); padding: 15px; border-radius: 8px; text-align: center; margin-bottom: 20px;">
                <p style="margin: 0 0 10px; font-size: 14px; opacity: 0.9;">Your Referral Link:</p>
                <a href="{referral_link}" 
                   style="color: white; font-size: 16px; font-weight: 600; text-decoration: underline; word-break: break-all;">
                    {referral_link}
                </a>
            </div>
            
            <div style="text-align: left;">
                <p style="margin: 10px 0; font-size: 15px;">✨ <strong>Refer 3 friends</strong> → Jump 100 spots</p>
                <p style="margin: 10px 0; font-size: 15px;">🚀 <strong>Refer 10 friends</strong> → Jump to Top 100</p>
                <p style="margin: 10px 0; font-size: 15px;">👑 <strong>Refer 20 friends</strong> → Lifetime Premium + Founder Badge</p>
            </div>
        </div>
        
        <!-- Launch Date -->
        <div style="background: #f1f5f9; padding: 20px; border-radius: 12px; text-align: center; margin: 30px 0;">
            <p style="margin: 0; color: #64748b; font-size: 14px; font-weight: 600;">🎯 LAUNCHING</p>
            <p style="margin: 8px 0 0; color: #1e293b; font-size: 28px; font-weight: 900;">
                {launch_date}
            </p>
            <p style="margin: 8px 0 0; color: #64748b; font-size: 14px;">
                Mark your calendar! 🎄
            </p>
        </div>
        
    </div>
    
    <!-- Footer -->
    <div style="background-color: #0f172a; padding: 30px; border-top: 2px solid #6366f1; text-align: center;">
        <p style="font-size: 1.2rem; margin-bottom: 15px; font-weight: 600; color: #ffffff;">
            📚 StudyHub - Learn Together, Grow Together
        </p>
        <p style="opacity: 0.7; color: #e2e8f0; margin-bottom: 10px;">
            Questions? Email us: 
            <a href="mailto:studyhubnoreply@gmail.com" 
               style="color: #6366f1; text-decoration: underline;">
                studyhubnoreply@gmail.com
            </a>
        </p>
        <p style="margin-top: 15px; opacity: 0.7; color: #e2e8f0; font-size: 0.9rem;">
            © 2025 StudyHub. Built with ❤️ by students, for students.
        </p>
    </div>
    
</div>

</body>
</html>
        """
        
        # Use async send in production (default)
        result = send_email_now(to_email, subject, html, async_send=True)
        
        if result:
            logger.info(f"✅ Welcome email queued for {to_email}")
        else:
            logger.error(f"❌ Welcome email failed for {to_email}")
        
        return result
        
    except Exception as e:
        logger.error(f"Exception in send_waitlist_welcome_email: {e}", exc_info=True)
        return False


def send_referral_milestone_email(to_email, referral_count, new_position, referral_code):
    """Send milestone email (async by default)"""
    
    print(f"\n🎯 send_referral_milestone_email() called for {to_email}")
    
    try:
        app_url = get_app_url()
        
        if referral_count == 3:
            milestone = "3 Referrals"
            reward = "Jumped 100 spots!"
            emoji = "✨"
            message = "You've unlocked your first milestone! Your position has jumped significantly on the waitlist."
        elif referral_count == 10:
            milestone = "10 Referrals"
            reward = "Jumped to Top 100!"
            emoji = "🚀"
            message = "Incredible! You're now in the top 100 and guaranteed early access with premium features."
        elif referral_count == 20:
            milestone = "20 Referrals"
            reward = "Lifetime Premium!"
            emoji = "👑"
            message = "You're a legend! You've earned Lifetime Premium Access and Founder Tier benefits. Welcome to the inner circle!"
        else:
            return False
        
        subject = f"{emoji} Milestone: {milestone}!"
        
        html = f"""
<!DOCTYPE html>
<html>
  <body style="font-family: 'Segoe UI', Arial, sans-serif; background-color: #0f172a; padding: 20px 0;">
    <div style="max-width: 600px; margin: auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 3px 10px rgba(0,0,0,0.1);">
        
        <!-- Header Section -->
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 40px; text-align: center; color: white;">
            <div style="font-size: 64px; margin-bottom: 10px;">{emoji}</div>
            <h1 style="margin: 0; font-size: 28px;">Milestone Reached!</h1>
            <p style="margin: 10px 0 0; font-size: 18px; opacity: 0.95;">{milestone}</p>
        </div>

        <!-- Main Content -->
        <div style="padding: 40px 30px; text-align: center;">
            <h2 style="color: #1e293b; font-size: 24px; margin-bottom: 20px;">{reward}</h2>
            <p style="color: #64748b; font-size: 16px; margin-bottom: 25px;">{message}</p>

            <div style="background: linear-gradient(135deg, #667eea15 0%, #764ba215 100%); padding: 25px; border-radius: 12px; margin: 30px 0;">
                <p style="margin: 0 0 15px; color: #64748b; font-size: 16px;">Your New Position:</p>
                <div style="font-size: 48px; font-weight: 900; color: #667eea;">#{new_position}</div>
            </div>

            <p style="color: #64748b; font-size: 16px; line-height: 1.6; margin: 25px 0;">
                Amazing work! You've helped <strong style="color: #667eea;">{referral_count} friends</strong> discover StudyHub.
                Keep sharing to climb even higher! 🎯
            </p>

            <a href="{app_url}#leaderboard-section" 
               style="display: inline-block; margin-top: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px 35px; border-radius: 50px; text-decoration: none; font-weight: 600;">
               View Leaderboard
            </a>
        </div>

        <!-- Footer -->
        <div style="background-color: #0f172a; padding: 30px; border-top: 2px solid #6366f1; text-align: center; margin-top: 40px;">
            <p style="font-size: 1.2rem; margin-bottom: 15px; font-weight: 600; color: #ffffff;">
                📚 StudyHub - Learn Together, Grow Together
            </p>
            <p style="opacity: 0.7; color: #e2e8f0; margin-bottom: 10px;">
                Questions? Email us:
                <a href="mailto:studyhubnoreply@gmail.com" 
                   style="color: #6366f1; text-decoration: underline;">
                    studyhubnoreply@gmail.com
                </a>
            </p>
            <p style="margin-top: 15px; opacity: 0.7; color: #e2e8f0; font-size: 0.9rem;">
                © 2025 StudyHub. Built with ❤️ by students, for students.
            </p>
        </div>

    </div>
  </body>
</html>
        """
        
        # Use async send in production (default)
        result = send_email_now(to_email, subject, html, async_send=True)
        
        if result:
            logger.info(f"✅ Milestone email queued for {to_email}")
        else:
            logger.error(f"❌ Milestone email failed for {to_email}")
        
        return result
        
    except Exception as e:
        logger.error(f"Exception in send_referral_milestone_email: {e}", exc_info=True)
        return False

          