"""
Message-Only WebSocket Manager - PRODUCTION
Handles real-time messaging events (NO THREADS)

Features:
- Real-time message delivery
- Typing indicators with auto-expiration
- Online/offline status
- Read receipts
- Message reactions
- Reconnection handling
- Rate limiting removed (as requested)
"""

from flask_socketio import emit, join_room, leave_room
from flask import request, current_app
from datetime import datetime, timezone
import jwt
from models import User, Message, Connection, MessageReaction
from extensions import db
from sqlalchemy import or_, and_
import bleach

# H-10 fix: the typing-indicator tracker used to be a hand-rolled, unlocked
# class defined right here. Under async_mode='threading' that dict was being
# mutated from multiple real OS threads with no synchronization at all — a
# genuine data race. services.websocket_rate_limiter already ships a
# thread-safe (lock-protected) TypingStatusManager that does the same job;
# we use that single, shared implementation instead of maintaining a second,
# unsynchronized copy here.
from services.websocket_rate_limiter import TypingStatusManager

# ============================================================================
# MESSAGE WEBSOCKET MANAGER
# ============================================================================

class MessageWebSocketManager:
    """
    Production WebSocket manager for messaging ONLY
    Thread system is completely separate
    """
    
    def __init__(self):
        self.socketio = None
        self.online_users = {}      # {user_id: [socket_ids]}
        self.socket_to_user = {}    # {socket_id: user_id}
        self.typing_manager = TypingStatusManager(timeout=3)
    
    def init_app(self, app):
        """Initialize SocketIO with Flask app"""
        from flask_socketio import SocketIO
        
        self.socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',  # was 'threading' — must match your server
    logger=True,
    engineio_logger=False
)
        self.register_handlers()
        return self.socketio
    
    # ========================================================================
    # UTILITY FUNCTIONS
    # ========================================================================
    
    def create_conversation_key(self, user1_id, user2_id):
        """Create consistent conversation identifier"""
        return f"conv_{min(user1_id, user2_id)}_{max(user1_id, user2_id)}"
    
    def get_current_user(self):
        """Get user_id from current WebSocket session"""
        return self.socket_to_user.get(request.sid)
    
    def emit_error(self, message: str):
        """Emit error to client"""
        emit('error', {'message': message})
    
    def emit_to_user(self, user_id, event_name, data):
        """Emit event to specific user (all their sockets)"""
        socket_ids = self.online_users.get(user_id, [])
        for socket_id in socket_ids:
            self.socketio.emit(event_name, data, room=socket_id)

    def disconnect_user(self, user_id):
        """
        Document 3 §1.5: force-close every socket tracked for `user_id`.

        Called from auth.py::logout after clearing cookies — a small,
        deliberate addition since access_token is now the WebSocket
        credential too (once ACCESS_TOKEN_HTTPONLY is on), so logout
        should proactively end any live WebSocket session rather than
        leaving it connected until it naturally times out or the next
        reconnect attempt fails. Previously, logout didn't touch
        WebSocket state at all.

        Uses flask_socketio's disconnect(sid=...) — safe to call even if
        the manager has no socketio instance yet (e.g. during tests) or
        the user has no open sockets (silently a no-op).
        """
        if not self.socketio:
            return

        from flask_socketio import disconnect as _sio_disconnect

        socket_ids = list(self.online_users.get(user_id, []))
        for socket_id in socket_ids:
            try:
                _sio_disconnect(sid=socket_id, namespace='/')
            except Exception:
                pass
    
    def sanitize_content(self, text: str) -> str:
        """Sanitize message content"""
        if not text:
            return text
        clean_text = bleach.clean(text, tags=[], strip=True)
        return clean_text.strip()
    
    # ========================================================================
    # AUTHENTICATION DECORATOR
    # ========================================================================
    
    def auth_required(self, f):
        """Decorator to require authentication for WebSocket events"""
        def decorated_function(data):
            current_user_id = self.get_current_user()
            if not current_user_id:
                self.emit_error('Authenitication required')
                return
            return f(current_user_id, data)
        decorated_function.__name__ = f.__name__
        return decorated_function
    
    # ========================================================================
    # EVENT HANDLERS
    # ========================================================================
    
    def register_handlers(self):
        """Register all WebSocket event handlers"""
        
        # ====================================================================
        # CONNECTION EVENTS
        # ====================================================================
        @self.socketio.on('connect')
        def handle_connect(auth):
          try:
            # Document 3 §1.1: once access_token is httponly, the client can
            # no longer read it to put it in the `auth` handshake payload.
            # Flask-SocketIO's connect handler runs inside a real Flask
            # request context, so request.cookies is available here exactly
            # like in a normal HTTP route — and since the handshake is a
            # same-origin HTTP request, the browser attaches the httponly
            # cookie automatically. Confirmed (per your infrastructure) that
            # cookies reach this handshake, so no ws-token fallback is needed.
            #
            # While ACCESS_TOKEN_HTTPONLY is False (default, pre-rollout),
            # this falls back to the original auth-payload token exactly as
            # before, so nothing changes until the flag is flipped.
            if current_app.config.get('ACCESS_TOKEN_HTTPONLY', False):
                token = request.cookies.get('access_token')
            else:
                token = auth.get('token') if auth else None

            if not token:
              return False  # returning False disconnects the client
            payload = jwt.decode(
            token,
            current_app.config['SECRET_KEY'],
            algorithms=['HS256']
            )
            user_id = payload.get('user_id')
            
            if not user_id:
              return False
            self.socket_to_user[request.sid] = user_id
            if user_id not in self.online_users:
              self.online_users[user_id] = []
            self.online_users[user_id].append(request.sid)

            # ISSUE-6 FIX: Join personal room so thread_list_update events arrive
            # even before the user opens any thread.
            join_room(f"user_{user_id}")

            emit('authenticated', {'user_id': user_id})
            
            connections = Connection.query.filter(or_(
                Connection.requester_id == user_id,
                Connection.receiver_id == user_id
            ),
            Connection.status == 'accepted'
            ).all()
            
            for conn in connections:
              other_id = conn.receiver_id if conn.requester_id == user_id else conn.requester_id
              self.emit_to_user(other_id, 'user_status_changed', {
                'user_id': user_id,
                'is_online': True
            })
            
            print(f'User {user_id} authenticated on WebSocket (via handshake)')
          except jwt.ExpiredSignatureError:
            return False  # Disconnect
          except jwt.InvalidTokenError:
            return False  # Disconnec
          except Exception as e:
            current_app.logger.error(f'Connect auth error: {e}')
            return False
        

        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            """Client disconnected - cleanup"""
            user_id = self.socket_to_user.get(request.sid)
            
            if user_id:
                # Remove this socket from user's socket list
                if user_id in self.online_users:
                    self.online_users[user_id].remove(request.sid)
                    
                    # If user has no more sockets, mark as offline
                    if not self.online_users[user_id]:
                        del self.online_users[user_id]
                        
                        # Notify connections
                        connections = Connection.query.filter(
                            or_(
                                Connection.requester_id == user_id,
                                Connection.receiver_id == user_id
                            ),
                            Connection.status == 'accepted'
                        ).all()
                        
                        for conn in connections:
                            other_id = conn.receiver_id if conn.requester_id == user_id else conn.requester_id
                            self.emit_to_user(other_id, 'user_status_changed', {
                                'user_id': user_id,
                                'is_online': False
                            })
                
                del self.socket_to_user[request.sid]

                # FIX 3: clean up thread presence tracking on disconnect
                try:
                    from services.websocket_threads import thread_ws_manager
                    thread_ws_manager.user_active_thread.pop(user_id, None)
                except Exception:
                    pass
            
            print(f'WebSocket client disconnected: {request.sid}')
      
        @self.socketio.on('authenticate')
        def handle_authenticate(data):
            """Authenticate WebSocket connection (secondary/legacy path — 'connect' above is primary)."""
            try:
                # Document 3 §1.1: same cookie-preference logic as handle_connect.
                if current_app.config.get('ACCESS_TOKEN_HTTPONLY', False):
                    token = request.cookies.get('access_token') or (data.get('token') if data else None)
                else:
                    token = data.get('token') if data else None

                if not token:
                    emit('auth_error', {'message': 'Token required'})
                    print("Token not found")
                    return
                
                # Verify JWT token
                payload = jwt.decode(
                    token,
                    current_app.config['SECRET_KEY'],
                    algorithms=['HS256']
                )
                user_id = payload.get('user_id')
                
                if not user_id:
                    emit('auth_error', {'message': 'Invalid token'})
                    print("Invalid token")
                    current_app.logger.info("Invalid token")
                    return
                
                # Store mapping
                self.socket_to_user[request.sid] = user_id
                
                # Add to online users
                if user_id not in self.online_users:
                    self.online_users[user_id] = []
                self.online_users[user_id].append(request.sid)
                
                # ISSUE-6 FIX: Join personal room
                join_room(f"user_{user_id}")

                # Notify user
                emit('authenticated', {'user_id': user_id})
                
                # Notify connections
                connections = Connection.query.filter(
                    or_(
                        Connection.requester_id == user_id,
                        Connection.receiver_id == user_id
                    ),
                    Connection.status == 'accepted'
                ).all()
                
                for conn in connections:
                    other_id = conn.receiver_id if conn.requester_id == user_id else conn.requester_id
                    self.emit_to_user(other_id, 'user_status_changed', {
                        'user_id': user_id,
                        'is_online': True
                    })
                
                print(f'User {user_id} authenticated on WebSocket')
                
            except jwt.ExpiredSignatureError:
                emit('auth_error', {'message': 'Token expired'})
                print("Token  expired")
                
            except jwt.InvalidTokenError:
              print("Jwt invalid token")
              emit('auth_error', {'message': 'Invalid token'})
            except Exception as e:
                current_app.logger.error(f'Auth error: {e}')
                print(e)
                emit('auth_error', {'message': 'Authentication failed'})
              
        
        # ====================================================================
        # MESSAGING EVENTS
        # ====================================================================
        
        @self.socketio.on('send_message')
        @self.auth_required
        def handle_send_message(current_user_id, data):
            """Send message to another user"""
            try:
                receiver_id = data.get('receiver_id')
                body = data.get('body', '').strip()
                resources = data.get('resources', [])
                client_temp_id = data.get('client_temp_id')
                
                if not receiver_id:
                    self.emit_error('receiver_id required')
                    return
                if not body and not resources:
                  return
                  
                
                # Validate message length
                if len(body) > 5000:
                    self.emit_error('Message too long (max 5000 characters)')
                    return
                
                # Check if users can message each other
                connection = Connection.query.filter(
                    or_(
                        and_(Connection.requester_id == current_user_id, Connection.receiver_id == receiver_id),
                        and_(Connection.requester_id == receiver_id, Connection.receiver_id == current_user_id)
                    ),
                    Connection.status == 'accepted'
                ).first()
                
                if not connection:
                    self.emit_error('You must be connected to message this user')
                    return
                
                # Sanitize content
                body = self.sanitize_content(body)
                
                # Save message to database
                new_message = Message(
                    sender_id=current_user_id,
                    receiver_id=receiver_id,
                    body=body,
                    status='sent',
                    resources=resources if resources else None,
                    sent_at=datetime.now(timezone.utc),
                    is_read=False,
                    client_temp_id=client_temp_id
                )
                db.session.add(new_message)
                db.session.flush()
                
                db.session.commit()
                
                # Get sender info
                sender = User.query.get(current_user_id)
                
                message_data = {
                    'id': new_message.id,
                    'sender_id': current_user_id,
                    'receiver_id': receiver_id,
                    'body': body,
                    'status': 'sent',
                    
                    'resources': resources,
                    'sent_at': new_message.sent_at.isoformat().replace('+00:00', 'Z'),
                    'is_read': False,
                    'client_temp_id': client_temp_id,
                    'sender': {
                        'id': sender.id,
                        'username': sender.username,
                        'name': sender.name,
                        'avatar': sender.avatar
                    }
                }
                
                # Send to receiver
                self.emit_to_user(receiver_id, 'new_message', message_data)
                
                # Confirm to sender
                emit('message_sent', message_data)
                
            except Exception as e:
                current_app.logger.error(f'Send message error: {e}')
                db.session.rollback()
                emit('message_error', {
                    'message': 'Failed to send message',
                    'client_temp_id': client_temp_id
                })
        
        @self.socketio.on('typing')
        @self.auth_required
        def handle_typing(current_user_id, data):
            """Handle typing indicator"""
            try:
                receiver_id = data.get('receiver_id')
                is_typing = data.get('is_typing', True)
                
                if not receiver_id:
                    return
                
                user = User.query.get(current_user_id)
                
                conversation_key = self.create_conversation_key(current_user_id, receiver_id)

                if is_typing:
                    self.typing_manager.set_typing(conversation_key, current_user_id)
                    self.emit_to_user(receiver_id, 'typing_started', {
                        'user_id': current_user_id,
                        'user_name': user.name if user else 'Someone'
                    })
                else:
                    self.typing_manager.remove_typing(conversation_key, current_user_id)
                    self.emit_to_user(receiver_id, 'typing_stopped', {
                        'user_id': current_user_id
                    })
                
            except Exception as e:
                current_app.logger.error(f'Typing indicator error: {e}')
        
        @self.socketio.on('mark_read')
        @self.auth_required
        def handle_mark_read(current_user_id, data):
            """Mark messages as read"""
            try:
                message_ids = data.get('message_ids', [])
                
                if not message_ids:
                    return
                
                # Update messages
                Message.query.filter(
                    Message.id.in_(message_ids),
                    Message.receiver_id == current_user_id,
                    Message.is_read == False
                ).update({'is_read': True}, synchronize_session=False)
                
                db.session.commit()
                
                # Notify sender
                messages = Message.query.filter(Message.id.in_(message_ids)).all()
                for msg in messages:
                    self.emit_to_user(msg.sender_id, 'messages_read', {
                        'message_ids': message_ids,
                        'reader_id': current_user_id
                    })
                
            except Exception as e:
                current_app.logger.error(f'Mark read error: {e}')
                db.session.rollback()
        
        @self.socketio.on('delete_message_for_me')
        @self.auth_required
        def handle_delete_for_me(current_user_id, data):
            """Delete message for current user only"""
            try:
                message_id = data.get('message_id')
                
                if not message_id:
                    return
                
                message = Message.query.get(message_id)
                
                if not message:
                    self.emit_error('Message not found')
                    return
                
                # Mark as deleted for appropriate user
                if message.sender_id == current_user_id:
                    message.deleted_by_sender = True
                elif message.receiver_id == current_user_id:
                    message.deleted_by_receiver = True
                else:
                    self.emit_error('Unauthorized')
                    return
                
                db.session.commit()
                print("Emitting deleted for tou message")
                
                emit('message_deleted_for_you', {'message_id': message_id})
                
            except Exception as e:
                current_app.logger.error(f'Delete message error: {e}')
                db.session.rollback()
        
        @self.socketio.on('delete_message_for_everyone')
        @self.auth_required
        def handle_delete_for_everyone(current_user_id, data):
            """Delete message for everyone (within 5 min window)"""
            try:
                message_id = data.get('message_id')
                
                if not message_id:
                    return
                
                message = Message.query.get(message_id)
                
                if not message or message.sender_id != current_user_id:
                    self.emit_error('Unauthorized')
                    return
                
                # Check 5 minute window
                now = datetime.now(timezone.utc)
                diff = (now - message.sent_at).total_seconds()
                
                if diff > 300:  # 5 minutes
                    self.emit_error('Can only delete messages within 5 minutes')
                    return
                
                # Mark as deleted for both
                message.deleted_by_sender = True
                message.deleted_by_receiver = True
                message.body = '[Message deleted]'
                
                db.session.commit()
                print("Emitting deleted for everyone")
                
                # Notify both users
                emit('message_deleted_for_everyone', {'message_id': message_id})
                self.emit_to_user(message.receiver_id, 'message_deleted_for_everyone', {
                    'message_id': message_id
                })
                
            except Exception as e:
                current_app.logger.error(f'Delete for everyone error: {e}')
                db.session.rollback()
        
        # ====================================================================
        # REACTIONS
        # ====================================================================
        
        @self.socketio.on('add_message_reaction')
        @self.auth_required
        def handle_add_reaction(current_user_id, data):
            """Add reaction to message"""
            try:
                message_id = data.get('message_id')
                emoji = data.get('emoji', 'thumbs_up')
                
                if not message_id:
                    return
                
                # Check if reaction already exists
                existing = MessageReaction.query.filter_by(
                    message_id=message_id,
                    user_id=current_user_id
                ).first()
                
                if existing:
                    existing.reaction_type = emoji
                else:
                    reaction = MessageReaction(
                        message_id=message_id,
                        user_id=current_user_id,
                        reaction_type=emoji
                    )
                    db.session.add(reaction)
                
                db.session.commit()
                
                # Get all reactions for this message
                reactions = MessageReaction.query.filter_by(message_id=message_id).all()
                reaction_counts = {}
                
                for r in reactions:
                    if r.reaction_type not in reaction_counts:
                        reaction_counts[r.reaction_type] = {
                            'count': 0,
                            'emoji': r.reaction_type,
                            'users': []
                        }
                    reaction_counts[r.reaction_type]['count'] += 1
                    reaction_counts[r.reaction_type]['users'].append(r.user_id)
                
                # Notify both users
                message = Message.query.get(message_id)
                reaction_data = {
                    'message_id': message_id,
                    'reactions': reaction_counts
                }
                
                emit('reaction_added', reaction_data)
                self.emit_to_user(message.sender_id, 'reaction_added', reaction_data)
                self.emit_to_user(message.receiver_id, 'reaction_added', reaction_data)
                
            except Exception as e:
                current_app.logger.error(f'Add reaction error: {e}')
                db.session.rollback()
        
        @self.socketio.on('remove_message_reaction')
        @self.auth_required
        def handle_remove_reaction(current_user_id, data):
            """Remove reaction from message"""
            try:
                message_id = data.get('message_id')
                
                if not message_id:
                    return
                
                reaction = MessageReaction.query.filter_by(
                    message_id=message_id,
                    user_id=current_user_id
                ).first()
                
                if reaction:
                    db.session.delete(reaction)
                    db.session.commit()
                    
                    # Get remaining reactions
                    reactions = MessageReaction.query.filter_by(message_id=message_id).all()
                    reaction_counts = {}
                    
                    for r in reactions:
                        if r.reaction_type not in reaction_counts:
                            reaction_counts[r.reaction_type] = {
                                'count': 0,
                                'emoji': r.reaction_type,
                                'users': []
                            }
                        reaction_counts[r.reaction_type]['count'] += 1
                        reaction_counts[r.reaction_type]['users'].append(r.user_id)
                    
                    message = Message.query.get(message_id)
                    reaction_data = {
                        'message_id': message_id,
                        'reactions': reaction_counts
                    }
                    
                    emit('reaction_removed', reaction_data)
                    self.emit_to_user(message.sender_id, 'reaction_removed', reaction_data)
                    self.emit_to_user(message.receiver_id, 'reaction_removed', reaction_data)
                
            except Exception as e:
                current_app.logger.error(f'Remove reaction error: {e}')
                db.session.rollback()
        
        # ====================================================================
        # ONLINE STATUS
        # ====================================================================
        
        @self.socketio.on('get_online_status')
        @self.auth_required
        def handle_get_online_status(current_user_id, data):
            """Get online status of specific users or all connections"""
            try:
                user_ids = data.get('user_ids', [])
                
                if not user_ids:
                    # Get all connections
                    connections = Connection.query.filter(
                        or_(
                            Connection.requester_id == current_user_id,
                            Connection.receiver_id == current_user_id
                        ),
                        Connection.status == 'accepted'
                    ).all()
                    
                    user_ids = []
                    for conn in connections:
                        other_id = conn.receiver_id if conn.requester_id == current_user_id else conn.requester_id
                        user_ids.append(other_id)
                
                statuses = {uid: uid in self.online_users for uid in user_ids}
                emit('online_statuses', {'statuses': statuses})
                
            except Exception as e:
                current_app.logger.error(f'Get online status error: {e}')
        
        @self.socketio.on('request_unread_count')
        @self.auth_required
        def handle_request_unread_count(current_user_id, data):
            """Get unread message count"""
            try:
                unread_count = Message.query.filter(
                    Message.receiver_id == current_user_id,
                    Message.is_read == False,
                    Message.deleted_by_receiver == False
                ).count()
                
                emit('unread_count', {'count': unread_count})
                
            except Exception as e:
                current_app.logger.error(f'Unread count error: {e}')
        
        # ====================================================================
        # PING/PONG
        # ====================================================================
        
        @self.socketio.on('ping')
        def handle_ping():
            """Keep-alive ping"""
            emit('pong', {'timestamp': datetime.now(timezone.utc).isoformat()})


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

message_ws_manager = MessageWebSocketManager()


def init_message_websocket(app):
    """Initialize Message WebSocket manager with Flask app"""
    return message_ws_manager.init_app(app)
