/**
 * Message System Constants — PRODUCTION
 */

// ============================================================================
// API ENDPOINTS
// ============================================================================

export const API_ENDPOINTS = {
  // Auth (used to bootstrap current user)
  CURRENT_USER: '/auth/me',

  // Conversations
  CONVERSATIONS:  '/messages/conversations',
  HISTORY:        (partnerId) => `/messages/conversation/${partnerId}`,

  // Messages
  SEND:                  '/messages/send',
  MARK_READ:             '/messages/mark-read',
  MARK_ALL_READ:         (partnerId) => `/messages/mark-all-read/${partnerId}`,
  DELETE_FOR_ME:         (messageId) => `/messages/${messageId}/delete-for-me`,
  DELETE_FOR_EVERYONE:   (messageId) => `/messages/${messageId}/delete-for-everyone`,
  CLEAR_CHAT:            (partnerId) => `/messages/clear/${partnerId}`,

  // Reactions
  ADD_REACTION:    (messageId) => `/messages/${messageId}/react`,
  REMOVE_REACTION: (messageId) => `/messages/${messageId}/react`,

  // Resources (file / voice note upload)
  UPLOAD_RESOURCE: '/messages/resources/upload',
  SHARED_MEDIA:    (partnerId) => `/messages/shared-media/${partnerId}`,

  // Permissions / blocking
  CAN_MESSAGE:  (userId) => `/messages/can-message/${userId}`,
  BLOCK_USER:   (userId) => `/messages/block/${userId}`,
  UNBLOCK_USER: (userId) => `/messages/unblock/${userId}`,

  // Reporting
  REPORT_MESSAGE: (messageId) => `/messages/report/${messageId}`,

  // Utility
  UNREAD_COUNT:       '/messages/unread-count',
  CONNECTIONS_STATUS: '/connections/active-status',
  BATCH_STATUS:       '/connections/active-status/batch',
};

// ============================================================================
// WEBSOCKET EVENTS
// ============================================================================

export const WS_EVENTS = {
  // Connection lifecycle
  CONNECT:       'connect',
  DISCONNECT:    'disconnect',
  AUTHENTICATE:  'authenticate',
  AUTHENTICATED: 'authenticated',
  AUTH_ERROR:    'auth_error',
  ERROR:         'error',

  // Messaging — outgoing
  SEND_MESSAGE:          'send_message',
  TYPING:                'typing',
  MARK_READ:             'mark_read',
  DELETE_FOR_ME:         'delete_message_for_me',
  DELETE_FOR_EVERYONE:   'delete_message_for_everyone',

  // Messaging — incoming
  NEW_MESSAGE:                  'new_message',
  MESSAGE_SENT:                 'message_sent',
  MESSAGE_ERROR:                'message_error',
  MESSAGES_READ:                'messages_read',
  MESSAGE_DELETED_FOR_YOU:      'message_deleted_for_you',
  MESSAGE_DELETED_FOR_EVERYONE: 'message_deleted_for_everyone',
  TYPING_STARTED:               'typing_started',
  TYPING_STOPPED:               'typing_stopped',

  // Reactions
  ADD_REACTION:    'add_message_reaction',
  REMOVE_REACTION: 'remove_message_reaction',
  REACTION_ADDED:  'reaction_added',       // backend emits 'reaction_added'
  REACTION_REMOVED:'reaction_removed',     // backend emits 'reaction_removed'

  // Online status
  USER_STATUS_CHANGED:  'user_status_changed',
  ONLINE_CONNECTIONS:   'online_connections',
  GET_ONLINE_STATUS:    'get_online_status',
  ONLINE_STATUSES:      'online_statuses',
  REQUEST_UNREAD_COUNT: 'request_unread_count',
  UNREAD_COUNT:         'unread_count',

  // Keep-alive
  PING: 'ping',
  PONG: 'pong',
};

// ============================================================================
// REACTIONS
// ============================================================================

export const REACTIONS = {
  love:       '❤️',
  fire:       '🔥',
  laugh:      '😂',
  wow:        '😮',
  sad:        '😢',
  angry:      '😡',
  thumbs_up:  '👍',
  thumbs_down:'👎',
  clap:       '👏',
  pray:       '🙏',
  celebrate:  '🎉',
  think:      '🤔',
};

export function getReactionEmoji(type) {
  return REACTIONS[type] || '👍';
}

export function isValidReaction(type) {
  return type in REACTIONS;
}

// ============================================================================
// RESOURCE TYPES
// ============================================================================

export const RESOURCE_TYPES = {
  IMAGE:    'image',
  VIDEO:    'video',
  AUDIO:    'audio',
  VOICE:    'voice',     // voice notes (audio/webm blobs)
  DOCUMENT: 'document',
  FILE:     'file',
};

export const RESOURCE_ICONS = {
  image:    '🖼️',
  video:    '🎥',
  audio:    '🎵',
  voice:    '🎙️',
  document: '📄',
  pdf:      '📄',
  file:     '📎',
};

export function getResourceIcon(type) {
  return RESOURCE_ICONS[type] || '📎';
}

// ============================================================================
// MESSAGE STATUS
// ============================================================================

export const MESSAGE_STATUS = {
  PENDING:   'pending',
  SENT:      'sent',
  DELIVERED: 'delivered',
  READ:      'read',
  FAILED:    'failed',
};

// ============================================================================
// LIMITS & TIMING
// ============================================================================

export const MAX_FILE_SIZE               = 50 * 1024 * 1024; // 50 MB
export const MAX_VOICE_NOTE_DURATION_MS  = 5 * 60 * 1000;    // 5 min
export const MAX_RESOURCES_PER_MESSAGE   = 10;

export const TYPING_TIMEOUT    = 2000;  // ms before auto-stop
export const TYPING_DEBOUNCE   = 500;   // ms
export const DELETE_FOR_EVERYONE_WINDOW = 5 * 60 * 1000; // 5 min
export const RECONNECT_INTERVAL = 3000;
export const ONLINE_STATUS_CACHE = 30000;
export const MESSAGE_RETRY_ATTEMPTS = 3;
export const MESSAGE_RETRY_DELAY    = 2000;

// Long-press threshold (ms)
export const LONG_PRESS_DURATION = 500;
export const LONG_PRESS_MOVE_THRESHOLD = 12; // px

// ============================================================================
// UI
// ============================================================================

export const MAX_MESSAGE_LENGTH     = 5000;
export const DEFAULT_MESSAGE_PAGE_SIZE = 50;

// ============================================================================
// ERROR CODES
// ============================================================================

export const ERROR_CODES = {
  NOT_CONNECTED:     'not_connected',
  PERMISSION_DENIED: 'permission_denied',
  MESSAGE_TOO_LONG:  'message_too_long',
  FILE_TOO_LARGE:    'file_too_large',
  INVALID_FILE_TYPE: 'invalid_file_type',
  RATE_LIMITED:      'rate_limited',
  NETWORK_ERROR:     'network_error',
  SERVER_ERROR:      'server_error',
  USER_BLOCKED:      'user_blocked',
  USER_NOT_FOUND:    'user_not_found',
};
