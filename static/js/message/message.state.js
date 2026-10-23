/**
 * Message System State Management — PRODUCTION
 * Single source of truth for messaging state.
 * currentUserId is populated from the WS 'authenticated' event
 * (and optionally from a REST /auth/me call) — never from window globals.
 */

import { MESSAGE_STATUS } from './message.constants.js';

// ============================================================================
// CENTRAL STATE
// ============================================================================

export const messageState = {
  // ── Current authenticated user ──────────────────────────────────────────
  currentUserId: null,
  currentUser: null,          // full user object {id, name, avatar, …}

  // ── Conversations ────────────────────────────────────────────────────────
  conversations: [],
  conversationsLoaded: false,

  // ── Active conversation ──────────────────────────────────────────────────
  currentConversation: null,
  currentPartnerId: null,

  // ── Messages ─────────────────────────────────────────────────────────────
  messages: [],
  messagesLoaded: false,
  hasMoreMessages: true,
  oldestMessageId: null,

  // ── Online / typing ──────────────────────────────────────────────────────
  onlineUsers: new Set(),
  typingUsers: new Set(),

  // ── Loading flags ────────────────────────────────────────────────────────
  isLoadingConversations: false,
  isLoadingMessages: false,
  isSendingMessage: false,
  isUploadingFile: false,

  // ── Network ──────────────────────────────────────────────────────────────
  isOnline: navigator.onLine,
  isConnectedToWebSocket: false,
  lastSyncTime: null,

  // Batch 2: the ISO timestamp of the most recent message actually
  // received over the socket (any conversation, not just the one
  // currently open) — set in message.websocket.js's 'new_message'
  // handler. This is deliberately separate from lastSyncTime above:
  // lastSyncTime gets overwritten to "now" on every single connect (see
  // setWebSocketConnected()), including the very reconnect where
  // sync_state needs the *previous* known-good timestamp to ask "what did
  // I miss since then?" — reading lastSyncTime at that moment would
  // always yield a ~0-second gap and defeat the point of syncing.
  lastMessageReceivedAt: null,

  // ── Failed / retry ───────────────────────────────────────────────────────
  failedMessages: [],
  retryQueue: [],

  // ── Unread ───────────────────────────────────────────────────────────────
  totalUnreadCount: 0,
  unreadByConversation: {},

  // ── UI state ─────────────────────────────────────────────────────────────
  showReactionPicker: false,
  reactionPickerMessageId: null,
  showMessageOptions: false,
  messageOptionsMessageId: null,
  activeMessageOptionsTarget: null, // DOM element that triggered long-press

  // ── Optimistic tracking ──────────────────────────────────────────────────
  pendingMessages: new Map(),   // client_temp_id → message data
  pendingReactions: new Map(),  // message_id → reaction

  // ── Pending attachments (staged before send) ─────────────────────────────
  pendingAttachments: [],       // [{id, url, type, filename, size, localUrl}]

  // ── File upload progress ─────────────────────────────────────────────────
  uploadProgress: new Map(),    // file_id → 0-100
};

// ============================================================================
// CURRENT USER
// ============================================================================

export function setCurrentUser(user) {
  messageState.currentUser = user;
  messageState.currentUserId = user?.id ?? null;
}

export function setCurrentUserId(id) {
  messageState.currentUserId = id;
}

export function getCurrentUserId() {
  return messageState.currentUserId;
}

export function getCurrentUser() {
  return messageState.currentUser;
}

// ============================================================================
// CONVERSATIONS
// ============================================================================

export function setConversations(conversations) {
  messageState.conversations = conversations;
  messageState.conversationsLoaded = true;
  messageState.isLoadingConversations = false;

  messageState.unreadByConversation = {};
  let totalUnread = 0;

  conversations.forEach(conv => {
    if (conv.unread_count > 0) {
      messageState.unreadByConversation[conv.partner.id] = conv.unread_count;
      totalUnread += conv.unread_count;
    }
  });

  messageState.totalUnreadCount = totalUnread;
}

export function updateConversation(partnerId, updates) {
  const index = messageState.conversations.findIndex(c => c.partner.id === partnerId);
  if (index !== -1) {
    messageState.conversations[index] = { ...messageState.conversations[index], ...updates };
    _sortConversations();
  }
}

export function addOrUpdateConversation(conversation) {
  const index = messageState.conversations.findIndex(c => c.partner.id === conversation.partner.id);
  if (index !== -1) {
    messageState.conversations[index] = conversation;
  } else {
    messageState.conversations.unshift(conversation);
  }
  _sortConversations();
}

function _sortConversations() {
  messageState.conversations.sort((a, b) => {
    const tA = new Date(a.last_message?.sent_at || 0);
    const tB = new Date(b.last_message?.sent_at || 0);
    return tB - tA;
  });
}

export function setLoadingConversations(loading) {
  messageState.isLoadingConversations = loading;
}

// ============================================================================
// CURRENT CONVERSATION
// ============================================================================

export function setCurrentConversation(partnerId) {
  messageState.currentPartnerId = partnerId;
  messageState.currentConversation = messageState.conversations.find(c => c.partner.id === partnerId) || null;

  // Reset message state for new conversation
  messageState.messages = [];
  messageState.messagesLoaded = false;
  messageState.hasMoreMessages = true;
  messageState.oldestMessageId = null;
  messageState.pendingAttachments = [];
}

export function clearCurrentConversation() {
  messageState.currentPartnerId = null;
  messageState.currentConversation = null;
  messageState.messages = [];
  messageState.messagesLoaded = false;
  messageState.pendingAttachments = [];
}

export function getCurrentConversation() {
  return messageState.currentConversation;
}

export function getCurrentPartner() {
  return messageState.currentConversation?.partner ?? null;
}

export function getCurrentPartnerId() {
  return messageState.currentPartnerId;
}

/** Returns true if the current conversation partner has been blocked by me */
export function isCurrentPartnerBlockedByMe() {
  return messageState.currentConversation?.is_blocked_by_me === true;
}

/** Returns true if the current conversation partner has blocked me */
export function isBlockedByCurrentPartner() {
  return messageState.currentConversation?.blocked_by_partner === true;
}

// ============================================================================
// MESSAGES
// ============================================================================

export function setMessages(messages, append = false) {
  if (append) {
    messageState.messages = [...messages, ...messageState.messages];
  } else {
    messageState.messages = messages;
  }

  messageState.messagesLoaded = true;
  messageState.isLoadingMessages = false;

  if (messages.length > 0) {
    messageState.oldestMessageId = messages[0].id;
  }

  // No pagination — if fewer than max, mark as done
  if (messages.length < 50) {
    messageState.hasMoreMessages = false;
  }
}

export function addMessage(message) {
  const exists = messageState.messages.some(m => m.id === message.id);
  if (exists) return;

  messageState.messages.push(message);

  if (message.client_temp_id) {
    messageState.pendingMessages.delete(message.client_temp_id);
  }

  // Batch 2: track the newest message timestamp seen from the server, for
  // sync_state on reconnect — see the field's own comment above for why
  // this is kept separate from lastSyncTime.
  if (message.sent_at) {
    const ts = new Date(message.sent_at);
    if (!messageState.lastMessageReceivedAt || ts > new Date(messageState.lastMessageReceivedAt)) {
      messageState.lastMessageReceivedAt = message.sent_at;
    }
  }
}

export function addOptimisticMessage(message) {
  messageState.messages.push(message);
  messageState.pendingMessages.set(message.client_temp_id, message);
}

export function updateMessageStatus(clientTempId, serverMessage) {
  const index = messageState.messages.findIndex(m => m.client_temp_id === clientTempId);
  if (index !== -1) {
    messageState.messages[index] = {
      ...messageState.messages[index],
      ...serverMessage,
      status: MESSAGE_STATUS.SENT,
    };
  }
  messageState.pendingMessages.delete(clientTempId);
}

export function markMessageAsFailed(clientTempId) {
  const index = messageState.messages.findIndex(m => m.client_temp_id === clientTempId);
  if (index !== -1) {
    messageState.messages[index].status = MESSAGE_STATUS.FAILED;
    messageState.failedMessages.push(messageState.messages[index]);
  }
  messageState.pendingMessages.delete(clientTempId);
}

/**
 * Soft-delete a message in state — keeps the record but marks is_deleted = true.
 * This is used for both "delete for me" and "delete for everyone".
 */
export function softDeleteMessage(messageId) {
  const message = messageState.messages.find(m => m.id === messageId);
  if (message) {
    message.is_deleted = true;
    message.body = '';
    message.resources = [];
  }
}

/** Hard-remove (used only for "delete for me" where we truly hide it from the user) */
export function removeMessage(messageId) {
  messageState.messages = messageState.messages.filter(m => m.id !== messageId);
}

export function updateMessageReaction(messageId, reactions) {
  const message = messageState.messages.find(m => m.id === messageId);
  if (message) {
    message.reactions = reactions;
  }
}

export function setLoadingMessages(loading) {
  messageState.isLoadingMessages = loading;
}

// ============================================================================
// PENDING ATTACHMENTS
// ============================================================================

export function addPendingAttachment(attachment) {
  messageState.pendingAttachments.push(attachment);
}

export function removePendingAttachment(id) {
  messageState.pendingAttachments = messageState.pendingAttachments.filter(a => a.id !== id);
}

export function clearPendingAttachments() {
  // Revoke any local object URLs
  messageState.pendingAttachments.forEach(a => {
    if (a.localUrl) URL.revokeObjectURL(a.localUrl);
  });
  messageState.pendingAttachments = [];
}

export function getPendingAttachments() {
  return messageState.pendingAttachments;
}

// ============================================================================
// SEND STATE
// ============================================================================

export function setSendingMessage(sending) {
  messageState.isSendingMessage = sending;
}

export function setUploadingFile(uploading) {
  messageState.isUploadingFile = uploading;
}

export function setUploadProgress(fileId, progress) {
  messageState.uploadProgress.set(fileId, progress);
}

export function clearUploadProgress(fileId) {
  messageState.uploadProgress.delete(fileId);
}

// ============================================================================
// ONLINE / TYPING
// ============================================================================

export function setOnlineStatus(isOnline) {
  messageState.isOnline = isOnline;
}

export function setWebSocketConnected(connected) {
  messageState.isConnectedToWebSocket = connected;
  if (connected) messageState.lastSyncTime = new Date();
}

export function setUserOnline(userId, isOnline) {
  if (isOnline) {
    messageState.onlineUsers.add(userId);
  } else {
    messageState.onlineUsers.delete(userId);
  }
}

export function isUserOnline(userId) {
  return messageState.onlineUsers.has(userId);
}

export function setUserTyping(userId, isTyping) {
  if (isTyping) {
    messageState.typingUsers.add(userId);
  } else {
    messageState.typingUsers.delete(userId);
  }
}

export function isUserTyping(userId) {
  return messageState.typingUsers.has(userId);
}

// ============================================================================
// UNREAD COUNTS
// ============================================================================

export function setTotalUnreadCount(count) {
  messageState.totalUnreadCount = count;
}

export function incrementUnreadCount(partnerId) {
  if (!messageState.unreadByConversation[partnerId]) {
    messageState.unreadByConversation[partnerId] = 0;
  }
  messageState.unreadByConversation[partnerId]++;
  messageState.totalUnreadCount++;
  updateConversation(partnerId, {
    unread_count: messageState.unreadByConversation[partnerId],
  });
}

export function clearUnreadCount(partnerId) {
  const prev = messageState.unreadByConversation[partnerId] || 0;
  messageState.unreadByConversation[partnerId] = 0;
  messageState.totalUnreadCount = Math.max(0, messageState.totalUnreadCount - prev);
  updateConversation(partnerId, { unread_count: 0 });
}

// ============================================================================
// UI: REACTION PICKER
// ============================================================================

export function showReactionPicker(messageId) {
  messageState.showReactionPicker = true;
  messageState.reactionPickerMessageId = messageId;
}

export function hideReactionPicker() {
  messageState.showReactionPicker = false;
  messageState.reactionPickerMessageId = null;
}

// ============================================================================
// UI: MESSAGE OPTIONS
// ============================================================================

export function showMessageOptions(messageId, targetEl = null) {
  messageState.showMessageOptions = true;
  messageState.messageOptionsMessageId = messageId;
  messageState.activeMessageOptionsTarget = targetEl;
}

export function hideMessageOptions() {
  messageState.showMessageOptions = false;
  messageState.messageOptionsMessageId = null;
  messageState.activeMessageOptionsTarget = null;
}

// ============================================================================
// RETRY QUEUE
// ============================================================================

export function addToRetryQueue(message) {
  messageState.retryQueue.push(message);
}

export function removeFromRetryQueue(clientTempId) {
  messageState.retryQueue = messageState.retryQueue.filter(m => m.client_temp_id !== clientTempId);
}

export function clearRetryQueue() {
  messageState.retryQueue = [];
}

// ============================================================================
// OPTIMISTIC REACTIONS
// ============================================================================

export function addOptimisticReaction(messageId, reactionType) {
  messageState.pendingReactions.set(messageId, reactionType);
}

export function confirmOptimisticReaction(messageId) {
  messageState.pendingReactions.delete(messageId);
}

export function revertOptimisticReaction(messageId) {
  messageState.pendingReactions.delete(messageId);
}

// ============================================================================
// GETTERS
// ============================================================================

export function getMessages() {
  return messageState.messages;
}

export function getConversations() {
  return messageState.conversations;
}

export function getOnlineUsers() {
  return Array.from(messageState.onlineUsers);
}

export function getTotalUnreadCount() {
  return messageState.totalUnreadCount;
}

export function getUnreadCountForPartner(partnerId) {
  return messageState.unreadByConversation[partnerId] || 0;
}

export function isLoading() {
  return (
    messageState.isLoadingConversations ||
    messageState.isLoadingMessages ||
    messageState.isSendingMessage
  );
}

export function canLoadMoreMessages() {
  return messageState.hasMoreMessages && !messageState.isLoadingMessages;
}

export function getPendingMessages() {
  return Array.from(messageState.pendingMessages.values());
}

export function getFailedMessages() {
  return messageState.failedMessages;
}
