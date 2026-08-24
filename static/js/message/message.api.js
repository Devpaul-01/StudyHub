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

/**
 * Send a message (REST fallback — primary path is WebSocket).
 */
export async function sendMessage(receiverId, body, resources = []) {
  try {
    const response = await api.post(API_ENDPOINTS.SEND, { receiver_id: receiverId, body, resources });
    return response.data;
  } catch (error) {
    console.error('Failed to send message:', error);
    throw error;
  }
}

export async function markMessagesRead(messageIds) {
  try {
    await api.post(API_ENDPOINTS.MARK_READ, { message_ids: messageIds });
  } catch (error) {
    console.error('Failed to mark messages as read:', error);
  }
}

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

export async function addReaction(messageId, reactionType) {
  try {
    await api.post(API_ENDPOINTS.ADD_REACTION(messageId), { emoji: reactionType });
  } catch (error) {
    console.error('Failed to add reaction:', error);
    throw error;
  }
}

export async function removeReaction(messageId) {
  try {
    await api.delete(API_ENDPOINTS.REMOVE_REACTION(messageId));
  } catch (error) {
    console.error('Failed to remove reaction:', error);
    throw error;
  }
}

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
