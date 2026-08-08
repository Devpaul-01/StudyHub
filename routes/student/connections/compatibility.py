"""
StudyHub - Connections: AI compatibility scoring + connection overview

Split from connections.py per Document 1 (Architecture Refactor) §2.1 as
part of Phase 2 (God-file splitting). This is a pure move — function
bodies, decorators, routes, and logic are unchanged from the original
connections.py. See routes/student/connections/__init__.py for the
sub-blueprint aggregation that re-exposes all routes under the same
paths as before.
"""

from flask import Blueprint, request, jsonify, current_app, Response, stream_with_context
from sqlalchemy import or_, and_, case
from datetime import timedelta
import datetime
import json
import logging
import random

from models import (
    User, StudentProfile, Connection, Notification,
    HelpRequest, Thread, ThreadMember,
    OnboardingDetails, Message
)
from extensions import db
from routes.student.helpers import (
    token_required, success_response, error_response,
    block_connection, unblock_connection,
)

from services.online_status_service import get_user_online_status
from services.ai_provider_service import provider_manager, StudyAssistant
from services.connection_service import (
    calculate_compatibility_score,
    calculate_schedule_overlap,
    gather_user_data,
    calculate_compatibility,
    get_recent_activity,
    get_mutual_connection_count,
    get_connection_health,
    get_user_onboarding_preview,
)
# Phase 5b (Document 4 §1): AI_EXPENSIVE — this is the AI-streaming
# compatibility overview, a real AI-provider call.
from services.rate_limit_service import limiter, RateLimitTier, user_or_ip_key

logger = logging.getLogger(__name__)

connections_compatibility_bp = Blueprint("connections_compatibility", __name__)
@connections_compatibility_bp.route('/connections/overview/<int:user_id>', methods=['GET'])
@limiter.limit(RateLimitTier.AI_EXPENSIVE, key_func=user_or_ip_key)
@token_required
def get_connection_overview(current_user, user_id):
    """
    Get AI-powered streaming overview of a potential connection
    
    Uses your multi-provider system from learnora.py:
    - Automatic provider rotation on failure
    - OpenRouter, Groq, Together AI support
    - Rate limit handling
    
    Returns Server-Sent Events stream with:
    - Compatibility score
    - AI-generated insights
    - Why you should connect
    - How you can help each other
    - Conversation starter
    """
    
    try:
        # Validate target user
        target_user = User.query.get(user_id)
        
        if not target_user:
            return error_response("User not found", 404)
        
        if target_user.id == current_user.id:
            return error_response("Cannot analyze connection with yourself", 400)
        
        # Check if already connected
        existing = Connection.query.filter(
            (
                ((Connection.requester_id == current_user.id) & (Connection.receiver_id == user_id)) |
                ((Connection.requester_id == user_id) & (Connection.receiver_id == current_user.id))
            )
        ).first()
        
        already_connected = existing and existing.status == 'accepted'
        
        # Gather data
        logger.info(f"💬 Connection overview: user={current_user.id} → target={user_id}")
        
        current_user_data = gather_user_data(current_user)
        target_user_data = gather_user_data(target_user)
        
        # Calculate compatibility
        compatibility_data = calculate_compatibility(current_user_data, target_user_data)
        
        # Calculate schedule overlap if onboarding data exists
        if current_user.onboarding_details and target_user.onboarding_details:
            compatibility_data['schedule_overlap'] = calculate_schedule_overlap(
                current_user.onboarding_details.study_schedule or {},
                target_user.onboarding_details.study_schedule or {}
            )
        
        # Get recent activity
        context_data = get_recent_activity(target_user.id)
        
        # Calculate compatibility score
        compatibility_score = calculate_compatibility_score(compatibility_data)
        
        # ========================================================================
        # Multi-provider AI system (services/ai_provider_service.py) —
        # provider_manager/StudyAssistant imported at module level now, see top of file.
        # ========================================================================

        # Get working provider (no vision needed for text chat)
        provider = provider_manager.get_working_provider(needs_vision=False)
        
        if not provider:
            # Fallback without AI
            logger.warning("⚠️ No AI provider available - returning fallback")
            fallback = generate_fallback_overview(
                compatibility_data,
                target_user_data,
                compatibility_score
            )
            
            return jsonify({
                "status": "success",
                "data": {
                    "target_user": {
                        "id": target_user.id,
                        "name": target_user.name,
                        "username": target_user.username,
                        "avatar": target_user.avatar,
                        "bio": target_user.bio,
                        "reputation_level": target_user.reputation_level,
                        "department": target_user_data['department']
                    },
                    "compatibility": {
                        "score": compatibility_score,
                        "shared_subjects": compatibility_data['shared_subjects'],
                        "mutual_help": compatibility_data['complementary_skills'],
                        "schedule_overlap": compatibility_data['schedule_overlap'],
                        "same_department": compatibility_data['department_match']
                    },
                    "ai_overview": fallback,
                    "activity": context_data,
                    "already_connected": already_connected,
                    "ai_available": False
                }
            })
        
        # Generate AI prompt
        prompt = generate_ai_overview_prompt(
            current_user_data,
            target_user_data,
            compatibility_data,
            context_data
        )
        
        # ✅ Create assistant using your learnora.py system
        assistant = StudyAssistant(provider, conversation_messages=[])
        assistant.select_model(has_images=False)  # Text-only chat
        
        # Build messages
        messages = [
            {
                "role": "system",
                "content": "You are a helpful study companion assistant specializing in helping students form meaningful learning connections. Be warm, specific, and encouraging."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
        
        # ✅ Stream response with provider rotation
        def generate():
            nonlocal provider
            full_response = ""
            error_occurred = False
            retries = 0
            max_retries = 2
            
            # Send initial data
            yield f"data: {json.dumps({'type': 'start', 'compatibility_score': compatibility_score})}\n\n"
            
            yield f"data: {json.dumps({'type': 'compatibility', 'data': {
                'score': compatibility_score,
                'shared_subjects': compatibility_data['shared_subjects'],
                'mutual_help': compatibility_data['complementary_skills'],
                'schedule_overlap': compatibility_data['schedule_overlap'],
                'same_department': compatibility_data['department_match']
            }})}\n\n"
            
            yield f"data: {json.dumps({'type': 'target_user', 'data': {
                'id': target_user.id,
                'name': target_user.name,
                'username': target_user.username,
                'avatar': target_user.avatar,
                'bio': target_user.bio,
                'reputation_level': target_user.reputation_level,
                'department': target_user_data['department']
            }})}\n\n"
            
            yield f"data: {json.dumps({'type': 'activity', 'data': context_data})}\n\n"
            
            yield f"data: {json.dumps({'type': 'ai_start', 'provider': provider['name']})}\n\n"
            
            # Stream AI response with retry logic
            while retries < max_retries:
                current_app.logger.error("Streaming response")
                error_in_stream = False
                
                for chunk in assistant.stream_response(messages):
                    yield chunk
                    
                    if chunk.startswith("data: "):
                        try:
                            chunk_data = json.loads(chunk[6:])
                            
                            if 'content' in chunk_data:
                                full_response += chunk_data['content']
                            elif 'error' in chunk_data:
                                error_occurred = True
                                
                                # Check if it's a rate limit/quota/timeout error
                                if chunk_data.get('rate_limit') or chunk_data.get('timeout') or chunk_data.get('http_error'):
                                    error_in_stream = True
                                    
                                    # Mark provider as failed
                                    provider_manager.mark_provider_failed(provider['name'])
                                    
                                    # Try next provider
                                    provider_manager.rotate()
                                    next_provider = provider_manager.get_working_provider(needs_vision=False)
                                    
                                    if next_provider and retries < max_retries - 1:
                                        logger.info(f"🔄 Switching from {provider['name']} to {next_provider['name']}")
                                        provider = next_provider
                                        assistant.provider = next_provider
                                        assistant.select_model(has_images=False)
                                        retries += 1
                                        
                                        # Notify frontend of provider switch
                                        yield f"data: {json.dumps({'type': 'retry', 'provider': provider['name']})}\n\n"
                                        break
                        except:
                            pass
                
                # Break out of retry loop if no error in stream
                if not error_in_stream:
                    break
            
            # Generate fallback if AI completely failed
            if error_occurred or not full_response:
                logger.warning("⚠️ AI failed - using fallback")
                fallback = generate_fallback_overview(
                    compatibility_data,
                    target_user_data,
                    compatibility_score
                )
                full_response = fallback
            
            # Send completion
            current_app.logger.error("Done streaming response")
            yield f"data: {json.dumps({
                'type': 'done',
                'success': not error_occurred,
                'already_connected': already_connected,
                'overview': full_response
            })}\n\n"
        
        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no'
            }
        )
        
    except Exception as e:
        logger.error(f"❌ Connection overview error: {str(e)}", exc_info=True)
        return error_response(f"Failed to generate overview: {str(e)}", 500)


# ============================================================================
# HELPER FUNCTIONS (Keep your existing ones, or use these refined versions)
# ============================================================================

def generate_fallback_overview(compatibility_data, target_user_data, score):
    """Generate simple overview if AI fails - KEEP THIS"""
    shared = compatibility_data['shared_subjects']
    can_help = compatibility_data['complementary_skills']['they_can_help_with']
    
    overview = f"**🎯 Connection Score:** {score}/100\n\n"
    
    if score >= 70:
        overview += "**💡 Why Connect?**\nThis is a strong match! "
    elif score >= 40:
        overview += "**💡 Why Connect?**\nThis could be a valuable connection! "
    else:
        overview += "**💡 Why Connect?**\nWhile you have different focuses, diverse perspectives can be valuable! "
    
    if shared:
        overview += f"You both share interests in {', '.join(shared[:2])}. "
    
    if can_help:
        overview += f"They can help you with {', '.join(can_help[:2])}. "
    
    overview += "\n\n**🤝 How You Can Help Each Other:**\n"
    if can_help:
        overview += f"They're strong in {', '.join(can_help[:2])}, which you're looking to learn. "
    
    you_can_help = compatibility_data['complementary_skills']['you_can_help_with']
    if you_can_help:
        overview += f"You can support them with {', '.join(you_can_help[:2])}. "
    
    if not can_help and not you_can_help:
        overview += "While your skills don't directly overlap, you can still learn from each other's different perspectives."
    
    overview += "\n\n**💬 Conversation Starter:**\n"
    if shared:
        overview += f"\"Hey! I noticed we're both interested in {shared[0]}. Would love to connect and maybe study together!\""
    elif can_help:
        overview += f"\"Hi! I saw you're experienced with {can_help[0]}. I'm working on that now – would you be open to connecting?\""
    else:
        overview += f"\"Hi! I'm a {target_user_data['department']} student too. Would love to connect and share study tips!\""
    
    return overview


def generate_ai_overview_prompt(current_user_data, target_user_data, compatibility_data, context_data):
    """Generate the prompt for AI connection overview - KEEP THIS"""
    
    prompt = f"""You are an intelligent study companion assistant helping students make meaningful connections.

**YOUR TASK:** Analyze this potential connection and provide a compelling, personalized overview.

**CURRENT USER:**
- Name: {current_user_data['name']}
- Department: {current_user_data['department']}
- Strong in: {', '.join(current_user_data['strong_subjects'][:3]) or 'Not specified'}
- Needs help with: {', '.join(current_user_data['help_subjects'][:3]) or 'Not specified'}
- Learning style: {current_user_data['learning_style']}

**POTENTIAL CONNECTION:**
- Name: {target_user_data['name']} (@{target_user_data['username']})
- Department: {target_user_data['department']} | Class: {target_user_data['class_name']}
- Reputation: {target_user_data['reputation']} ({target_user_data['reputation_level']})
- Strong in: {', '.join(target_user_data['strong_subjects'][:5]) or 'Not specified'}
- Can help with: {', '.join(target_user_data['help_subjects'][:5]) or 'Not specified'}
- Bio: {target_user_data['bio']}

**COMPATIBILITY:**
- Shared subjects: {', '.join(compatibility_data['shared_subjects']) if compatibility_data['shared_subjects'] else 'None'}
- They can help you with: {', '.join(compatibility_data['complementary_skills']['they_can_help_with'][:3]) if compatibility_data['complementary_skills']['they_can_help_with'] else 'No overlap'}
- You can help them with: {', '.join(compatibility_data['complementary_skills']['you_can_help_with'][:3]) if compatibility_data['complementary_skills']['you_can_help_with'] else 'No overlap'}
- Schedule compatibility: {compatibility_data['schedule_overlap']}%
- Same department: {'Yes' if compatibility_data['department_match'] else 'No'}

**RECENT ACTIVITY:**
- Posts this week: {context_data['recent_posts']}
- Helpful responses: {context_data['recent_helpful_comments']}
- Active in {context_data['active_threads']} study groups
- Top topics: {', '.join(context_data['popular_topics']) if context_data['popular_topics'] else 'None'}
- Badges: {', '.join(target_user_data['badges'][:3]) if target_user_data['badges'] else 'None'}

**RESPONSE FORMAT:**
Provide your analysis in exactly 4 sections (use emojis for visual appeal):

1. **🎯 Connection Score** (X/10)
   - One sentence explaining the overall match quality

2. **💡 Why Connect?**
   - 2-3 specific, compelling reasons based on the data above
   - Focus on mutual benefit and learning synergy
   - Be specific (use actual subject names, not generic terms)

3. **🤝 How You Can Help Each Other**
   - Clear examples of knowledge exchange
   - Mention specific subjects/skills from the data

4. **💬 Conversation Starter**
   - Suggest a personalized opening message (1-2 sentences)
   - Reference something specific from their profile or activity

**STYLE GUIDELINES:**
- Be warm, enthusiastic, and encouraging
- Use concrete examples from the data, not generic praise
- If compatibility is low, be honest but constructive
- Keep total response under 250 words
- Make it feel like advice from a knowledgeable friend
- NEVER make up information not provided above

Begin your response now:"""
    
    return prompt


"""
Online Connections Endpoint
Returns list of connected users who are currently online (active in last 30 minutes)
Matches the exact structure of your existing endpoints
"""

