/**
 * Message System API Layer — PRODUCTION
 * Uses the global `api` client present in the app.
 * uploadResource supports an optional progress callback for attachment uploads.
 */

import { API_ENDPOINTS } from './message.constants.js';

// ============================================================================
// CURRENT USER  (bootstrap — called once on init)
// ============================================================================

/**
 * Fetch the currently authenticated user profile.
 * @returns {Promise<object>} User object { id, name, avatar, username, … }
 */
export async function fetchCurrentUser() {
  try {
    const response = await api.get(API_ENDPOINTS.CURRENT_USER);
    return response.data.user ?? response.data;
  } catch (error) {
    console.error('Failed to fetch current user:', error);
    throw error;
  }
}

// ============================================================================
// CONVERSATIONS
// ============================================================================

/**
 * Fetch all conversations for the current user.
 * Expected response: { conversations: [ { partner, last_message, unread_count,
 *   partner_online, is_blocked_by_me, blocked_by_partner } ] }
 */
export async function fetchConversations() {
  try {
    const response = await api.get(API_ENDPOINTS.CONVERSATIONS);
    return response.data.conversations;
  } catch (error) {
    console.error('Failed to fetch conversations:', error);
    throw error;
  }
}

/**
 * Fetch message history with a specific partner.
 * @param {number} partnerId
 * @param {object} params  – e.g. { before, limit }
 */
export async function fetchMessageHistory(partnerId, params = {}) {
  try {
    const response = await api.get(API_ENDPOINTS.HISTORY(partnerId), params);
    return {
      messages:          response.data.messages,
      is_blocked_by_me:  response.data.is_blocked_by_me  ?? false,
      blocked_by_partner: response.data.blocked_by_partner ?? false,
    };
    
  } catch (error) {
    console.error('Failed to fetch message history:', error);
    throw error;
  }
}

// ============================================================================
// MESSAGES
// ============================================================================

// sendMessage() / markMessagesRead() were removed here (Batch 1, item 1):
// both called backend routes that don't exist (POST /messages/send and
// POST /messages/mark-read have no matching @messages_bp.route anywhere
// in routes/student/messages.py), and every real call site already used
// the WebSocket versions in message.websocket.js instead, so these were
// unused dead code rather than a working fallback.

export async function markAllRead(partnerId) {
  try {
    await api.post(API_ENDPOINTS.MARK_ALL_READ(partnerId));
  } catch (error) {
    console.error('Failed to mark all read:', error);
  }
}

export async function deleteMessageForMe(messageId) {
  try {
    await api.delete(API_ENDPOINTS.DELETE_FOR_ME(messageId));
  } catch (error) {
    console.error('Failed to delete message for me:', error);
    throw error;
  }
}

export async function deleteMessageForEveryone(messageId) {
  try {
    await api.delete(API_ENDPOINTS.DELETE_FOR_EVERYONE(messageId));
  } catch (error) {
    console.error('Failed to delete message for everyone:', error);
    throw error;
  }
}

export async function clearChat(partnerId) {
  try {
    await api.delete(API_ENDPOINTS.CLEAR_CHAT(partnerId));
  } catch (error) {
    console.error('Failed to clear chat:', error);
    throw error;
  }
}

// ============================================================================
// REACTIONS
// ============================================================================
//
// addReaction() / removeReaction() were removed here (Batch 1, item 1):
// POST/DELETE /messages/<id>/react has no matching backend route — only
// the WebSocket events 'add_message_reaction' / 'remove_message_reaction'
// exist (see services/websocket_events.py and websocket_messages.py).
// message.events.js's handleSelectReaction() was calling this REST
// version, so picking a reaction always 404'd; it now calls
// messageWS.addReaction() instead, which is the real, working path.
// There is still no UI action wired up for *removing* a reaction on
// either path — that's a separate gap, not a 404, so it's left for a
// later batch rather than built here.

// ============================================================================
// RESOURCES  (file / voice note upload)
// ============================================================================

/**
 * Upload a file resource.
 * Uses XMLHttpRequest so we can report upload progress.
 *
 * @param {File}     file
 * @param {Function} [onProgress]  – called with percent (0-100)
 * @returns {Promise<object>}  { id, url, type, filename, size }
 */
/**
 * Upload a file resource using the enhanced API client
 */
export async function uploadResource(file, onProgress = null) {
  try {
    const result = await api.uploadWithProgress(
      API_ENDPOINTS.UPLOAD_RESOURCE,
      file,
      onProgress
    );
    return result;
  } catch (error) {
    console.error('Failed to upload resource:', error);
    throw error;
  }
}

export async function getSharedMedia(partnerId, params = {}) {
  try {
    const response = await api.get(API_ENDPOINTS.SHARED_MEDIA(partnerId), params);
    return response.data;
  } catch (error) {
    console.error('Failed to fetch shared media:', error);
    throw error;
  }
}

// ============================================================================
// PERMISSIONS / BLOCKING
// ============================================================================

export async function canMessage(userId) {
  try {
    const response = await api.get(API_ENDPOINTS.CAN_MESSAGE(userId));
    return response.data;
  } catch (error) {
    console.error('Failed to check messaging permission:', error);
    throw error;
  }
}

export async function blockUser(userId) {
  try {
    await api.post(API_ENDPOINTS.BLOCK_USER(userId));
  } catch (error) {
    _toast(error.message, 'error');
    console.error('Failed to block user:', error);
    throw error;
  }
}

export async function unblockUser(userId) {
  try {
    await api.post(API_ENDPOINTS.UNBLOCK_USER(userId));
  } catch (error) {
    console.error('Failed to unblock user:', error);
    throw error;
  }
}

export async function reportMessage(messageId, reason, description = '') {
  try {
    await api.post(API_ENDPOINTS.REPORT_MESSAGE(messageId), { reason, description });
  } catch (error) {
    console.error('Failed to report message:', error);
    throw error;
  }
}

// ============================================================================
// UTILITY
// ============================================================================

export async function getUnreadCount() {
  try {
    const response = await api.get(API_ENDPOINTS.UNREAD_COUNT);
    return response.data.unread_count ?? 0;
  } catch {
    return 0;
  }
}

export async function getConnectionsActiveStatus() {
  try {
    const response = await api.get(API_ENDPOINTS.CONNECTIONS_STATUS);
    return response.data;
  } catch {
    return {};
  }
}

export async function getBatchActiveStatus(userIds) {
  try {
    const response = await api.post(API_ENDPOINTS.BATCH_STATUS, { user_ids: userIds });
    return response.data;
  } catch {
    return {};
  }
}
