// ============================================================================
// CONNECTION SYSTEM CONSTANTS
// ============================================================================

export const connectionContainer = document.querySelector('section#connections');

export const CONNECTION_TABS = {
  CONNECTED: 'connected',
  RECEIVED: 'received',
  SENT: 'sent',
  SUGGESTIONS: 'suggestions',
  DISCOVERY: 'discovery'
};

export const CONNECTION_ENDPOINTS = {
  RECEIVED: '/connections/requests/received',
  SENT: '/connections/requests/sent',
  CONNECTED: '/connections/list',
  SUGGESTIONS: '/connections/suggestions',
  DISCOVERY: '/connections/mutuals/discover',
  ONLINE: '/connections/online',
  SEARCH: '/connections/search',
  OVERVIEW: '/connections/overview',
  MUTUALS: '/connections/mutual',
  ACCEPT: '/connections/accept',
  REJECT: '/connections/reject',
  CANCEL: '/connections/cancel',
  CONNECT: '/connections/request',
  BLOCK: '/connections/block',
  UNBLOCK: '/connections/unblock',
  // Batch 2: this existed as a working backend route
  // (DELETE /connections/remove/<user_id>) with no frontend endpoint
  // constant and nothing calling it at all.
  REMOVE: '/connections/remove',
  BLOCKED_LIST: '/connections/blocked/list',
  MARK_RECEIVED_SEEN: '/connections/mark-received-seen',
  MARK_SENT_SEEN: '/connections/mark-sent-seen',
  UNSEEN_RECEIVED: '/connections/unseen/received',
  UNSEEN_SENT: '/connections/unseen/sent'
};

export const REPUTATION_ICONS = {
  'Beginner': '🌱',
  'Contributor': '⭐',
  'Expert': '💎',
  'Master': '👑',
  'Legend': '🏆'
};

export const HEALTH_COLORS = {
  HIGH: '#10b981', // green
  MEDIUM: '#f59e0b', // yellow
  LOW: '#ef4444' // red
};

export const POLLING_INTERVAL = 180000; // 3 minutes