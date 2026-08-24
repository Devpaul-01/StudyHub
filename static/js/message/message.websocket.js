/**
 * Message WebSocket Client — PRODUCTION
 * Real-time messaging events via Socket.IO.
 * Uses messageState.getCurrentUserId() — no window globals.
 * Deleted messages are soft-deleted (kept in DOM with placeholder).
 */

import { WS_EVENTS, RECONNECT_INTERVAL, TYPING_TIMEOUT } from './message.constants.js';
import * as messageState from './message.state.js';
import {
  renderNewMessage,
  updateMessageInUI,
  markMessageAsDeleted,
  showTypingIndicator,
  hideTypingIndicator,
  updatePartnerStatus,
} from './message.render.js';

const _toast = (msg, type) => { const fn = window.showToast || globalThis.showToast; fn?.(msg, type); };
import { socket } from "../core/socket.js";


// ============================================================================
// WEBSOCKET CLASS
// ============================================================================

class MessageWebSocket {
  constructor() {
    this.socket              = null;
    this.isConnected         = false;
    this.reconnectAttempts   = 0;
    this.maxReconnectAttempts = 10;
    this.typingTimer         = null;
    this.token               = null;
    this._handlersRegistered = false;  // ← guard against duplicate registration
  }

  // --------------------------------------------------------------------------
  // CONNECT / DISCONNECT
  // --------------------------------------------------------------------------

  connect() {
  
    
    this.socket = socket;

    this._registerHandlers();

    // ⚠️ The socket is a shared singleton and may already be connected by the
    // time connect() is called — in that case the 'connect' event already fired
    // and will never fire again, so isConnected would stay false forever.
    // Sync state immediately if the socket is already up.
    if (this.socket.connected) {
      console.log('✅ WS already connected — syncing state');
      this.isConnected = true;
      messageState.setWebSocketConnected(true);
      this._retryFailedMessages();
    }
    // Note: do NOT emit 'authenticate' here — the backend authenticates via
    // the Socket.IO handshake auth object, not a post-connect event.
    // Make sure your core/socket.js passes { auth: { token } } when connecting.
  }

  disconnect() {
    this.socket?.disconnect();
    this.socket      = null;
    this.isConnected = false;
    messageState.setWebSocketConnected(false);
  }

  // --------------------------------------------------------------------------
  // EVENT HANDLERS
  // --------------------------------------------------------------------------

  _registerHandlers() {
    // CRITICAL: Only register handlers once — calling connect() again (e.g.
    // when the user navigates back to Messages) must NOT stack duplicate listeners.
    if (this._handlersRegistered) return;
    this._handlersRegistered = true;

    const s = this.socket;

    // ── Connection lifecycle ──────────────────────────────────────────────

    s.on(WS_EVENTS.CONNECT, () => {
      console.log('✅ WS connected');
      
      this.reconnectAttempts   = 0;
      this.isConnected= true;
      messageState.setWebSocketConnected(true);
      

      s.emit(WS_EVENTS.AUTHENTICATE, { token: this.token });
      this._retryFailedMessages();

      // Hide reconnecting banner
      import('./message.render.js').then(r => r.hideReconnectingBanner());
    });

    s.on(WS_EVENTS.AUTHENTICATED, (data) => {
      console.log('✅ WS authenticated — user', data.user_id);
      

      // ⭐ Store current user ID in state (safe, no window globals)
      messageState.setCurrentUserId(data.user_id);

      this._requestOnlineStatus();
      this._requestUnreadCount();
    });

    s.on(WS_EVENTS.DISCONNECT, () => {
      console.log('❌ WS disconnected');
      this.isConnected = false;
      messageState.setWebSocketConnected(false);
      import('./message.render.js').then(r => r.showReconnectingBanner());
    });

    s.on('connect_error', () => {
      this.reconnectAttempts++;
      if (this.reconnectAttempts >= this.maxReconnectAttempts) {
        _toast('Connection failed — check your network', 'error');
      }
    });

    s.on(WS_EVENTS.AUTH_ERROR, () => {
      _toast('Authentication failed — please refresh', 'error');
    });

    // ── Messages ─────────────────────────────────────────────────────────

    s.on(WS_EVENTS.NEW_MESSAGE, (data) => {
      const currentPartnerId  = messageState.getCurrentPartnerId();
      const currentUserId     = messageState.getCurrentUserId();

      messageState.addMessage(data);

      if (currentPartnerId === data.sender_id) {
        // Viewing this conversation — render and auto-mark read
        renderNewMessage(data);
        this.markMessagesRead([data.id]);
      } else {
        messageState.incrementUnreadCount(data.sender_id);
        messageState.updateConversation(data.sender_id, {
          last_message:    data,
          last_message_at: data.sent_at,
        });
        // Lazy import to avoid circular reference
        import('./message.render.js').then(r => r.updateConversationItem(data.sender_id));

        const name    = data.sender?.name || 'Someone';
        const preview = data.body?.substring(0, 50) || '📎 Attachment';
        _toast(`${name}: ${preview}`, 'info');
      }

      this.updateUnreadBadge();
    });

    s.on(WS_EVENTS.MESSAGE_SENT, (data) => {
      if (data.client_temp_id) {
        messageState.updateMessageStatus(data.client_temp_id, data);
        updateMessageInUI(data.client_temp_id, data);
      }
      messageState.setSendingMessage(false);

      const pid = messageState.getCurrentPartnerId();
      if (pid) {
        messageState.updateConversation(pid, {
          last_message:    data,
          last_message_at: data.sent_at,
        });
        import('./message.render.js').then(r => r.updateConversationItem(pid));
      }
    });

    s.on(WS_EVENTS.MESSAGE_ERROR, (data) => {
      if (data.client_temp_id) {
        messageState.markMessageAsFailed(data.client_temp_id);
        const el = document.querySelector(`[data-temp-id="${data.client_temp_id}"]`);
        if (el) {
          el.classList.add('message-failed');
          // Retry button already in template — just ensure the failed class is set
        }
      }
      messageState.setSendingMessage(false);
      _toast(data.message || 'Failed to send message', 'error');
    });

    /**
     * ⭐ Soft-delete — keep message in DOM, show placeholder.
     * Do NOT call removeMessageFromUI / remove() on the element.
     */
    s.on(WS_EVENTS.MESSAGE_DELETED_FOR_YOU, (data) => {
      messageState.softDeleteMessage(data.message_id);
      markMessageAsDeleted(data.message_id);
    });

    s.on(WS_EVENTS.MESSAGE_DELETED_FOR_EVERYONE, (data) => {
      messageState.softDeleteMessage(data.message_id);
      markMessageAsDeleted(data.message_id);
    });

    // ── Typing ────────────────────────────────────────────────────────────

    s.on(WS_EVENTS.TYPING_STARTED, (data) => {
      messageState.setUserTyping(data.user_id, true);
      if (messageState.getCurrentPartnerId() === data.user_id) {
        showTypingIndicator();
      }
    });

    s.on(WS_EVENTS.TYPING_STOPPED, (data) => {
      messageState.setUserTyping(data.user_id, false);
      if (messageState.getCurrentPartnerId() === data.user_id) {
        hideTypingIndicator();
      }
    });

    // ── Reactions ─────────────────────────────────────────────────────────

    s.on(WS_EVENTS.REACTION_ADDED, (data) => {
      messageState.updateMessageReaction(data.message_id, data.reactions);
      this._updateReactionUI(data.message_id, data.reactions);
      messageState.confirmOptimisticReaction(data.message_id);
    });

    s.on(WS_EVENTS.REACTION_REMOVED, (data) => {
      messageState.updateMessageReaction(data.message_id, data.reactions);
      this._updateReactionUI(data.message_id, data.reactions);
    });

    // ── Online status ─────────────────────────────────────────────────────

    s.on(WS_EVENTS.USER_STATUS_CHANGED, (data) => {
      messageState.setUserOnline(data.user_id, data.is_online);
      updatePartnerStatus(data.user_id, data.is_online);
      this._updateConvListStatus(data.user_id, data.is_online);
    });

    s.on(WS_EVENTS.ONLINE_STATUSES, (data) => {
      // data: { user_id: boolean, … }
      Object.entries(data).forEach(([uid, online]) => {
        messageState.setUserOnline(parseInt(uid, 10), online);
      });
    });

    s.on(WS_EVENTS.UNREAD_COUNT, (data) => {
      messageState.setTotalUnreadCount(data.count ?? 0);
      this.updateUnreadBadge();
    });

    s.on(WS_EVENTS.PONG, () => { /* keep-alive */ });
  }

  // --------------------------------------------------------------------------
  // EMIT HELPERS
  // --------------------------------------------------------------------------

  sendMessage(receiverId, body, resources = [], clientTempId = null) {
    if (!this.isConnected) {
      _toast('Cannot send — reconnecting…', 'warning');
      return null;
    }
    const payload = {
      receiver_id:    receiverId,
      body,
      resources,
      client_temp_id: clientTempId || `temp_${Date.now()}_${Math.random().toString(36).slice(2)}`,
    };
    this.socket.emit(WS_EVENTS.SEND_MESSAGE, payload);
    return payload.client_temp_id;
  }

  sendTyping(partnerId, isTyping) {
    if (!this.isConnected) return;
    clearTimeout(this.typingTimer);
    this.socket.emit(WS_EVENTS.TYPING, { receiver_id: partnerId, is_typing: isTyping });
    if (isTyping) {
      this.typingTimer = setTimeout(() => this.sendTyping(partnerId, false), TYPING_TIMEOUT);
    }
  }

  markMessagesRead(messageIds) {
    if (!this.isConnected || !messageIds?.length) return;
    this.socket.emit(WS_EVENTS.MARK_READ, { message_ids: messageIds });
  }

  deleteMessage(messageId, deleteForEveryone = false) {
    if (!this.isConnected) return;
    const event = deleteForEveryone ? WS_EVENTS.DELETE_FOR_EVERYONE : WS_EVENTS.DELETE_FOR_ME;
    this.socket.emit(event, { message_id: messageId });
  }

  addReaction(messageId, reactionType) {
    if (!this.isConnected) return;
    this.socket.emit(WS_EVENTS.ADD_REACTION, { message_id: messageId, emoji: reactionType });
  }

  removeReaction(messageId) {
    if (!this.isConnected) return;
    this.socket.emit(WS_EVENTS.REMOVE_REACTION, { message_id: messageId });
  }

  updateUnreadBadge() {
    const badge = document.getElementById('message-badge');
    if (!badge) return;
    const count = messageState.getTotalUnreadCount();
    badge.textContent = count > 99 ? '99+' : String(count);
    badge.classList.toggle('hidden', count === 0);
  }

  // --------------------------------------------------------------------------
  // PRIVATE
  // --------------------------------------------------------------------------

  _requestOnlineStatus() {
    if (!this.isConnected) return;
    this.socket.emit(WS_EVENTS.GET_ONLINE_STATUS, {});
  }

  _requestUnreadCount() {
    if (!this.isConnected) return;
    this.socket.emit(WS_EVENTS.REQUEST_UNREAD_COUNT, {});
  }

  async _retryFailedMessages() {
    const failed = messageState.getFailedMessages();
    for (const msg of failed) {
      this.sendMessage(msg.receiver_id, msg.body, msg.resources || [], msg.client_temp_id);
      messageState.removeFromRetryQueue(msg.client_temp_id);
    }
  }

  _updateReactionUI(messageId, reactions) {
    const messageEl = document.querySelector(`[data-message-id="${messageId}"]`);
    if (!messageEl) return;

    const container = messageEl.querySelector('.message-reactions');
    if (!container) return;

    if (!reactions || Object.keys(reactions).length === 0) {
      container.innerHTML = '';
      return;
    }

    const isOwn = messageEl.dataset.isOwn === 'true';
    container.innerHTML = Object.entries(reactions).map(([type, data]) => `
      <span class="reaction-badge flex items-center gap-0.5 text-xs bg-white border border-gray-200
                   rounded-full px-2 py-0.5 shadow-sm cursor-pointer hover:bg-indigo-50 transition-colors"
            data-action="react-to-message"
            data-reaction="${type}"
            data-message-id="${messageId}">
        ${data.emoji} <span class="font-medium text-gray-700">${data.count}</span>
      </span>
    `).join('');
  }

  _updateConvListStatus(userId, isOnline) {
    const el = document.querySelector(`.conversation-item[data-partner-id="${userId}"]`);
    const dot = el?.querySelector('.rounded-full.border-2');
    if (!dot) return;
    dot.classList.toggle('bg-emerald-500', isOnline);
    dot.classList.toggle('bg-gray-300', !isOnline);
  }
}

// Export singleton
export const messageWS = new MessageWebSocket();
