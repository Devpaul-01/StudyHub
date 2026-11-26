/**
 * thread.constants.js
 * Centralised endpoint paths, WebSocket event names, and UI constants.
 *
 * CHANGES:
 *  - Issue 6: THREAD_LIST_UPDATE, THREAD_UPDATED, THREAD_JOINED added.
 *  - NEW: THREAD_WS.AI_ACTION — client sends to trigger AI action on a message.
 *  - NEW: THREAD_API.MEETING_NOTES — AI meeting notes endpoints.
 */

// ─── API endpoint builders ───────────────────────────────────────────────────

export const THREAD_API = {
  OPEN:              "/threads/open",
  POPULAR:           "/threads/popular",
  RECOMMENDED:       "/threads/recommended",
  DEPARTMENTS:       "/threads/departments",

  MY_THREADS:        "/threads/my-threads",
  MY_REQUESTS:       "/threads/my-requests",
  MY_INVITES:        "/threads/invites",
  PENDING_REQUESTS:  "/threads/pending-requests",

  CREATE:            "/threads/create",
  CREATE_STANDALONE: "/threads/create-standalone",

  THREAD:   (id)            => `/threads/${id}`,
  AVATAR:   (id)            => `/threads/${id}/avatar`,
  SETTINGS: (id)            => `/threads/${id}/settings`,
  STATS:    (id)            => `/threads/${id}/stats`,
  CLOSE:    (id)            => `/threads/${id}/close`,
  REOPEN:   (id)            => `/threads/${id}/reopen`,

  /** AI meeting notes — POST to generate, GET to list past notes. */
  MEETING_NOTES: (id)       => `/threads/${id}/meeting-notes`,

  MEMBERS:       (tid)      => `/threads/${tid}/members`,
  ADD_MEMBERS:   (tid)      => `/threads/${tid}/members/add`,
  REMOVE_MEMBER: (tid, uid) => `/threads/${tid}/remove/${uid}`,
  LEAVE:         (tid)      => `/threads/${tid}/leave`,
  ROLE_UPDATE:   (tid, uid) => `/threads/${tid}/members/${uid}/role`,

  JOIN:            (id)         => `/threads/${id}/join`,
  CANCEL_REQUEST:  (reqId)      => `/threads/requests/${reqId}/cancel`,
  APPROVE_REQUEST: (tid, reqId) => `/threads/${tid}/requests/${reqId}/approve`,
  REJECT_REQUEST:  (tid, reqId) => `/threads/${tid}/requests/${reqId}/reject`,

  INVITE:         (tid, uid) => `/threads/${tid}/invite/${uid}`,
  ACCEPT_INVITE:  (invId)    => `/threads/invites/${invId}/accept`,
  DECLINE_INVITE: (invId)    => `/threads/invites/${invId}/decline`,

  MESSAGES: (tid) => `/threads/${tid}/messages`,
  UPLOAD:   (tid) => `/threads/${tid}/messages/upload`,
  PINNED:   (tid) => `/threads/${tid}/messages/pinned`,
  SEARCH:   (tid) => `/threads/${tid}/messages/search`,
};


// ─── WebSocket event names ────────────────────────────────────────────────────
// All names verified against @sio.on() decorators in websocket_threads.py

export const THREAD_WS = {
  // ── Client → Server ──────────────────────────────────────────────────────
  JOIN_ROOM:         "join_thread_room",
  LEAVE_ROOM:        "leave_thread_room",

  SEND:              "send_thread_message",
  TYPING_START:      "thread_typing",
  TYPING_STOP:       "thread_typing_stop",
  REACT:             "add_thread_reaction",
  MARK_READ:         "mark_thread_read",
  MESSAGE_DELIVERED: "message_delivered",
  PIN:               "pin_thread_message",
  UNPIN:             "unpin_thread_message",
  EDIT:              "edit_thread_message",
  DELETE:            "delete_thread_message",

  /** Trigger an AI action (explain/summarize/translate/to_code/fact_check) on a message. */
  AI_ACTION:         "thread_ai_action",

  // ── Server → Client (thread room) ────────────────────────────────────────
  ROOM_JOINED:       "thread_room_joined",
  NEW_MESSAGE:       "new_thread_message",
  MESSAGE_SENT:      "thread_message_sent",
  MESSAGE_EDITED:    "thread_message_edited",
  MESSAGE_DELETED:   "thread_message_deleted",
  MESSAGE_PINNED:    "thread_message_pinned",
  MESSAGE_UNPINNED:  "thread_message_unpinned",
  REACTION_UPDATED:  "thread_reactions_updated",
  USER_TYPING_START: "thread_typing_started",
  USER_TYPING_STOP:  "thread_typing_stopped",
  USER_ONLINE:       "user_online",
  USER_OFFLINE:      "user_offline",
  READ_ACK:          "thread_read_ack",
  MEMBER_JOINED:     "thread_member_joined",
  MEMBER_REMOVED:    "thread_member_removed",
  THREAD_DELETED:    "thread_deleted",
  MEMBER_LEFT:       "thread_member_left",
  ACCESS_REVOKED:    "thread_access_revoked",
  ROOM_RESYNC:       "thread_room_resync",
  ERROR:             "thread_error",
  MSG_ERROR:         "thread_message_error",
  LEARNORA_THINKING: "learnora_thinking",

  // ── Server → Client (personal room user_{id}) ────────────────────────────
  THREAD_LIST_UPDATE:     "thread_list_update",
  THREAD_UPDATED:         "thread_updated",
  THREAD_JOINED:          "thread_joined",
  MESSAGE_STATUS_UPDATED: "message_status_updated",
};


// ─── Message status values ────────────────────────────────────────────────────

export const MSG_STATUS = {
  PENDING:   "pending",
  SENT:      "sent",
  DELIVERED: "delivered",
  READ:      "read",
  FAILED:    "failed",
};


// ─── AI Personality keys (mirrors websocket_threads.py AI_PERSONALITIES) ─────

export const AI_BOT_TRIGGERS = [
  'learnora', 'teacherai', 'coderai', 'productai', 'funnyai',
];


// ─── UI constants ─────────────────────────────────────────────────────────────

export const THREAD_UI = {
  TYPING_TIMEOUT_MS:       3500,
  MESSAGES_PER_PAGE:       30,
  SCROLL_LOAD_THRESHOLD:   120,
  HIGHLIGHT_DURATION_MS:   2500,
  MAX_ATTACHMENT_MB:       25,
  MAX_ATTACHMENTS_PER_MSG: 5,
  RETRY_MAX_ATTEMPTS:      3,
  LONG_PRESS_DURATION_MS:  500,
  LONG_PRESS_THRESHOLD_PX: 10,
};

// Named exports used by legacy imports in thread_longpress.js
export const THREAD_LONG_PRESS_DURATION  = THREAD_UI.LONG_PRESS_DURATION_MS;
export const THREAD_LONG_PRESS_THRESHOLD = THREAD_UI.LONG_PRESS_THRESHOLD_PX;
