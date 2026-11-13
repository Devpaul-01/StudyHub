/**
 * thread.websocket.js
 * Frontend WebSocket client for the thread real-time layer.
 *
 * CHANGES:
 *  - BUG FIX: MEMBER_JOINED handler now stores `username` in memberMap
 *    so @mention autocomplete works for newly joined members.
 *  - Issue 6: _registerPersonalHandlers registers thread_list_update,
 *    thread_updated, thread_joined handlers.
 *  - LEARNORA_THINKING handler passes personality name to showLearnoraBotTyping.
 *  - Issue 1: sendMessage() and _buildOptimistic() carry attachments[] array.
 *  - All previously applied fixes retained (WS-01..06, ARCH-01..02, BUG-C2, BUG-C5).
 */

import {
  threadState,
  addMessage,
  addPendingMessage,
  confirmOptimisticMessage,
  failPendingMessage,
  updateMessageStatus,
  setUserOnline,
  setUserTyping,
  addOrUpdateThreadInList,
  removeThreadFromList,
  setMember,
  setUserActiveThread,
} from './thread.state.js';

import { THREAD_WS, MSG_STATUS } from './thread.constants.js';

// ─── Module state ─────────────────────────────────────────────────────────────

let _socket   = null;
let _threadId = null;

const _threadHandlers   = {};
const _personalHandlers = {};
let _personalHandlersSocket = null;


// ─── Public: lifecycle ────────────────────────────────────────────────────────

/**
 * Register the personal-room handlers (thread_list_update, thread_updated,
 * thread_joined, message_status_updated). They do not depend on an open
 * thread, so threadInit calls this up front; otherwise the list would get no
 * live updates until the first thread was opened. Idempotent per socket.
 */
export function registerThreadPersonalHandlers(socket) {
  if (_personalHandlersSocket === socket) return;
  _registerPersonalHandlers(socket);
  _personalHandlersSocket = socket;
}

export function unregisterThreadPersonalHandlers() {
  if (!_personalHandlersSocket) return;
  for (const [event, handler] of Object.entries(_personalHandlers)) {
    _personalHandlersSocket.off(event, handler);
    delete _personalHandlers[event];
  }
  _personalHandlersSocket = null;
}

export function initThreadWebSocket(socket, threadId) {
  _socket   = socket;
  _threadId = threadId;

  const token = api.getToken();

  registerThreadPersonalHandlers(socket);

  _registerThreadHandlers(socket, threadId, token);

  if (threadState.currentUser?.id) {
    setUserActiveThread(threadState.currentUser.id, threadId);
  }

  socket.emit(THREAD_WS.JOIN_ROOM, { token, thread_id: threadId });
}

export function disconnectThreadWebSocket(threadId) {
  if (!_socket) return;

  const token = api.getToken();
  _socket.emit(THREAD_WS.LEAVE_ROOM, { token, thread_id: threadId });

  for (const [event, handler] of Object.entries(_threadHandlers)) {
    _socket.off(event, handler);
    delete _threadHandlers[event];
  }

  if (threadState.currentUser?.id) {
    setUserActiveThread(threadState.currentUser.id, null);
  }

  _socket   = null;
  _threadId = null;
}


// ─── Public: outbound helpers ─────────────────────────────────────────────────

export function sendMessage(payload) {
  if (!_socket || !_socket.connected) {
    const clientTempId = `tmp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const { currentUser, activeThreadId } = threadState;

    const optimistic = _buildOptimistic(payload, clientTempId, currentUser, activeThreadId);
    optimistic.status = MSG_STATUS.FAILED;

    addPendingMessage(optimistic);
    import('./thread.render.js').then(({ renderNewMessage }) => {
      renderNewMessage(optimistic);
    });

    if (typeof window.showToast === 'function') {
      window.showToast('You appear to be offline. Message could not be sent.', 'error');
    }
    return null;
  }

  const clientTempId = `tmp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const { currentUser, activeThreadId } = threadState;
  const token = api.getToken();

  const optimistic = _buildOptimistic(payload, clientTempId, currentUser, activeThreadId);
  addPendingMessage(optimistic);

  import('./thread.render.js').then(({ renderNewMessage }) => {
    renderNewMessage(optimistic);
  });

  _socket.emit(THREAD_WS.SEND, {
    token,
    thread_id:       activeThreadId,
    text_content:    payload.text_content    ?? '',
    client_temp_id:  clientTempId,
    reply_to_id:     payload.reply_to_id     ?? null,
    attachments:     payload.attachments     ?? [],
    attachment_url:  payload.attachments?.[0]?.attachment_url  ?? null,
    attachment_name: payload.attachments?.[0]?.attachment_name ?? null,
    attachment_type: payload.attachments?.[0]?.attachment_type ?? null,
    attachment_size: payload.attachments?.[0]?.attachment_size ?? null,
  });

  return clientTempId;
}

export function emitTypingStart(threadId) {
  threadState.typingThreadId = threadId;
  _socket?.emit(THREAD_WS.TYPING_START, { token: api.getToken(), thread_id: threadId });
}

export function emitTypingStop(threadId) {
  _socket?.emit(THREAD_WS.TYPING_STOP, { token: api.getToken(), thread_id: threadId });
  threadState.typingThreadId = null;
}

export function emitMarkRead(threadId) {
  _socket?.emit(THREAD_WS.MARK_READ, { token: api.getToken(), thread_id: threadId });
}

export function emitDelivered(messageId) {
  _socket?.emit(THREAD_WS.MESSAGE_DELIVERED, {
    token:      api.getToken(),
    message_id: messageId,
  });
}


// ─── Private: optimistic message builder ─────────────────────────────────────

function _buildOptimistic(payload, clientTempId, currentUser, activeThreadId) {
  return {
    id:              null,
    client_temp_id:  clientTempId,
    thread_id:       activeThreadId,
    sender_id:       currentUser?.id,
    sender: {
      id:       currentUser?.id,
      name:     currentUser?.name,
      username: currentUser?.username,
      avatar:   currentUser?.avatar,
    },
    text_content:    payload.text_content    ?? '',
    attachments:     payload.attachments     ?? [],
    attachment_url:  payload.attachments?.[0]?.attachment_url  ?? null,
    attachment_name: payload.attachments?.[0]?.attachment_name ?? null,
    attachment_type: payload.attachments?.[0]?.attachment_type ?? null,
    attachment_size: payload.attachments?.[0]?.attachment_size ?? null,
    reply_to_id:     payload.reply_to_id     ?? null,
    reply_to:        payload.reply_to        ?? null,
    is_pinned:       false,
    is_edited:       false,
    is_ai_response:  false,
    ai_personality:  null,
    reactions:       {},
    status:          MSG_STATUS.PENDING,
    sent_at:         new Date().toISOString(),
  };
}


// ─── Private: personal-room handlers (survive thread switches) ────────────────

function _registerPersonalHandlers(socket) {

  // 1. Message status ticks
  _personalHandlers[THREAD_WS.MESSAGE_STATUS_UPDATED] = (data) => {
    const { message_ids, status } = data;
    if (!Array.isArray(message_ids) || !status) return;
    updateMessageStatus(message_ids, status);
    import('./thread.render.js').then(({ updateStatusIcons }) => {
      updateStatusIcons(message_ids, status);
    });
  };
  socket.on(THREAD_WS.MESSAGE_STATUS_UPDATED, _personalHandlers[THREAD_WS.MESSAGE_STATUS_UPDATED]);

  // 2. thread_list_update — new message arrived in a background thread
  _personalHandlers[THREAD_WS.THREAD_LIST_UPDATE] = (data) => {
    const { thread_id, last_message, last_activity } = data;
    const existingThread = threadState.threadList.get(thread_id);
    if (!existingThread) return;

    const isActiveThread = thread_id === threadState.activeThreadId;

    addOrUpdateThreadInList({ id: thread_id, last_message, last_activity });

    if (!isActiveThread && last_message?.sender_id !== threadState.currentUser?.id) {
      addOrUpdateThreadInList({
        id:           thread_id,
        unread_count: (existingThread.unread_count ?? 0) + 1,
      });
    }

    if (threadState.activeTab !== 'invites') {
      import('./thread.render.js').then(({ moveThreadToTop, rerenderThreadListItem }) => {
        moveThreadToTop(thread_id);
        rerenderThreadListItem(thread_id);
      });
    }
  };
  socket.on(THREAD_WS.THREAD_LIST_UPDATE, _personalHandlers[THREAD_WS.THREAD_LIST_UPDATE]);

  // 3. thread_updated — metadata changed (title / avatar / settings)
  _personalHandlers[THREAD_WS.THREAD_UPDATED] = (data) => {
    const stateUpdate = { id: data.thread_id };
    if (data.title       != null) stateUpdate.title       = data.title;
    if (data.description != null) stateUpdate.description = data.description;
    if (data.avatar      != null) stateUpdate.avatar      = data.avatar;
    if (data.tags        != null) stateUpdate.tags        = data.tags;
    if (data.max_members != null) stateUpdate.max_members = data.max_members;
    if (data.requires_approval != null) stateUpdate.requires_approval = data.requires_approval;

    addOrUpdateThreadInList(stateUpdate);

    import('./thread.render.js').then(({ rerenderThreadListItem }) => {
      rerenderThreadListItem(data.thread_id);
    });

    if (data.thread_id === threadState.activeThreadId) {
      import('./thread.render.js').then(({ renderThreadHeader }) => {
        const thread     = threadState.threadList.get(data.thread_id);
        const userStatus = { your_role: thread?.your_role };
        if (thread) renderThreadHeader(thread, userStatus);
      });
    }
  };
  socket.on(THREAD_WS.THREAD_UPDATED, _personalHandlers[THREAD_WS.THREAD_UPDATED]);

  // 4. thread_joined — user was approved or accepted an invite
  _personalHandlers[THREAD_WS.THREAD_JOINED] = (data) => {
    if (!data.thread) return;

    addOrUpdateThreadInList({
      ...data.thread,
      your_role:    data.thread.your_role ?? 'member',
      unread_count: 0,
    });

    const existing = document.querySelector(`[data-thread-id="${data.thread.id}"]`);
    if (existing) {
      import('./thread.render.js').then(({ rerenderThreadListItem }) => {
        rerenderThreadListItem(data.thread.id);
      });
    } else {
      import('./thread.render.js').then(({ renderThreadList }) => {
        renderThreadList('loaded');
      });
    }

    if (typeof window.showToast === 'function') {
      window.showToast(`You joined "${data.thread.title}"!`, 'success');
    }
  };
  socket.on(THREAD_WS.THREAD_JOINED, _personalHandlers[THREAD_WS.THREAD_JOINED]);
}


// ─── Private: thread-room handlers ───────────────────────────────────────────

function _registerThreadHandlers(socket, threadId, token) {

  _threadHandlers[THREAD_WS.ROOM_JOINED] = (data) => {
    addOrUpdateThreadInList({ id: threadId, your_role: data.your_role });
  };
  socket.on(THREAD_WS.ROOM_JOINED, _threadHandlers[THREAD_WS.ROOM_JOINED]);

  _threadHandlers[THREAD_WS.ERROR] = (data) => {
    if (typeof window.showToast === 'function') {
      window.showToast(data?.message ?? 'Real-time error', 'error');
    }
  };
  socket.on(THREAD_WS.ERROR, _threadHandlers[THREAD_WS.ERROR]);

  _threadHandlers[THREAD_WS.MSG_ERROR] = (data) => {
    const { client_temp_id } = data ?? {};
    if (client_temp_id) {
      failPendingMessage(client_temp_id);
      import('./thread.render.js').then(({ markMessageFailed }) => {
        markMessageFailed?.(client_temp_id);
      });
    }
    if (typeof window.showToast === 'function') {
      window.showToast(data?.message ?? 'Failed to send message', 'error');
    }
  };
  socket.on(THREAD_WS.MSG_ERROR, _threadHandlers[THREAD_WS.MSG_ERROR]);

  _threadHandlers[THREAD_WS.NEW_MESSAGE] = (data) => {
    if (data.is_ai_response) {
      document.getElementById('thread-learnora-typing')?.remove();
    }

    const wasAdded = addMessage(data);

    if (wasAdded) {
      import('./thread.render.js').then(({ renderNewMessage, moveThreadToTop }) => {
        renderNewMessage(data);
        moveThreadToTop?.(data.thread_id);
      });

      if (data.sender_id !== threadState.currentUser?.id && data.id) {
        if (data.thread_id === threadState.activeThreadId) {
          emitMarkRead(data.thread_id);
        } else {
          emitDelivered(data.id);
        }
      }
    }

    addOrUpdateThreadInList({
      id:           data.thread_id,
      last_message: {
        text:      (data.text_content ?? '').slice(0, 80) ||
                   (data.attachment_url || data.attachments?.length ? '📎 Attachment' : ''),
        sender:    data.sender?.name ?? '',
        sender_id: data.sender_id,
        sent_at:   data.sent_at,
        status:    data.status,
      },
      last_activity: data.sent_at,
    });
  };
  socket.on(THREAD_WS.NEW_MESSAGE, _threadHandlers[THREAD_WS.NEW_MESSAGE]);

  // BUG-C5: toast lives here only, not in render layer
  _threadHandlers[THREAD_WS.MESSAGE_SENT] = (data) => {
    const { client_temp_id, id, sent_at, status } = data;
    confirmOptimisticMessage(client_temp_id, { id, sent_at, status: status ?? MSG_STATUS.SENT });
    import('./thread.render.js').then(({ confirmOptimisticMessage: renderConfirm }) => {
      renderConfirm(client_temp_id, data);
    });
  };
  socket.on(THREAD_WS.MESSAGE_SENT, _threadHandlers[THREAD_WS.MESSAGE_SENT]);

  _threadHandlers[THREAD_WS.MESSAGE_EDITED] = (data) => {
    const msg = threadState.messages.find((m) => m.id === data.message_id);
    if (msg) {
      msg.text_content = data.text_content;
      msg.is_edited    = true;
      msg.edited_at    = data.edited_at;
    }
    import('./thread.render.js').then(({ renderMessageEdit }) => {
      renderMessageEdit(data.message_id, data.text_content);
    });
  };
  socket.on(THREAD_WS.MESSAGE_EDITED, _threadHandlers[THREAD_WS.MESSAGE_EDITED]);

  _threadHandlers[THREAD_WS.MESSAGE_DELETED] = (data) => {
    const msg = threadState.messages.find((m) => m.id === data.message_id);
    if (msg) { msg.is_deleted = true; msg.text_content = '[deleted]'; }
    import('./thread.render.js').then(({ renderMessageDelete }) => {
      renderMessageDelete(data.message_id);
    });
  };
  socket.on(THREAD_WS.MESSAGE_DELETED, _threadHandlers[THREAD_WS.MESSAGE_DELETED]);

  _threadHandlers[THREAD_WS.MESSAGE_PINNED] = (data) => {
    const msg = threadState.messages.find((m) => m.id === data.message_id);
    if (msg) msg.is_pinned = true;
    if (typeof window.showToast === 'function') window.showToast('Message pinned', 'success');
    import('./thread.render.js').then(({ renderPinUpdate, renderPinnedBanner }) => {
      renderPinUpdate(data.message_id, true);
      const pinned = threadState.messages.filter((m) => m.is_pinned && !m.is_deleted);
      renderPinnedBanner(pinned);
    });
  };
  socket.on(THREAD_WS.MESSAGE_PINNED, _threadHandlers[THREAD_WS.MESSAGE_PINNED]);

  _threadHandlers[THREAD_WS.MESSAGE_UNPINNED] = (data) => {
    const msg = threadState.messages.find((m) => m.id === data.message_id);
    if (msg) msg.is_pinned = false;
    if (typeof window.showToast === 'function') window.showToast('Message unpinned', 'info');
    import('./thread.render.js').then(({ renderPinUpdate, renderPinnedBanner }) => {
      renderPinUpdate(data.message_id, false);
      const pinned = threadState.messages.filter((m) => m.is_pinned && !m.is_deleted);
      renderPinnedBanner(pinned);
    });
  };
  socket.on(THREAD_WS.MESSAGE_UNPINNED, _threadHandlers[THREAD_WS.MESSAGE_UNPINNED]);

  _threadHandlers[THREAD_WS.REACTION_UPDATED] = (data) => {
    const msg = threadState.messages.find((m) => m.id === data.message_id);
    if (msg) msg.reactions = data.reactions;
    import('./thread.render.js').then(({ renderReactionUpdate }) => {
      renderReactionUpdate(data.message_id, data.reactions);
    });
  };
  socket.on(THREAD_WS.REACTION_UPDATED, _threadHandlers[THREAD_WS.REACTION_UPDATED]);

  _threadHandlers[THREAD_WS.USER_TYPING_START] = (data) => {
    if (data.user_id === threadState.currentUser?.id) return;
    setUserTyping(data.user_id, data.user_name, true, () => {
      import('./thread.render.js').then(({ renderTypingIndicator }) => {
        renderTypingIndicator();
      });
    });
  };
  socket.on(THREAD_WS.USER_TYPING_START, _threadHandlers[THREAD_WS.USER_TYPING_START]);

  _threadHandlers[THREAD_WS.USER_TYPING_STOP] = (data) => {
    if (data.user_id === threadState.currentUser?.id) return;
    setUserTyping(data.user_id, null, false, () => {
      import('./thread.render.js').then(({ renderTypingIndicator }) => {
        renderTypingIndicator();
      });
    });
  };
  socket.on(THREAD_WS.USER_TYPING_STOP, _threadHandlers[THREAD_WS.USER_TYPING_STOP]);

  _threadHandlers[THREAD_WS.USER_ONLINE] = (data) => {
    setUserOnline(data.user_id, true);
    import('./thread.render.js').then(({ updateOnlineBadge }) => {
      updateOnlineBadge?.(data.user_id, true);
    });
  };
  socket.on(THREAD_WS.USER_ONLINE, _threadHandlers[THREAD_WS.USER_ONLINE]);

  _threadHandlers[THREAD_WS.USER_OFFLINE] = (data) => {
    setUserOnline(data.user_id, false);
    import('./thread.render.js').then(({ updateOnlineBadge }) => {
      updateOnlineBadge?.(data.user_id, false);
    });
  };
  socket.on(THREAD_WS.USER_OFFLINE, _threadHandlers[THREAD_WS.USER_OFFLINE]);

  _threadHandlers[THREAD_WS.READ_ACK] = () => {
    addOrUpdateThreadInList({ id: threadId, unread_count: 0 });
    import('./thread.render.js').then(({ updateUnreadBadge }) => {
      updateUnreadBadge?.(threadId, 0);
    });
  };
  socket.on(THREAD_WS.READ_ACK, _threadHandlers[THREAD_WS.READ_ACK]);

  // BUG FIX: store username so @mention works for newly joined members
  _threadHandlers[THREAD_WS.MEMBER_JOINED] = (data) => {
    const current = threadState.threadList.get(threadId);
    addOrUpdateThreadInList({
      id:           threadId,
      member_count: (current?.member_count ?? 0) + 1,
    });
    if (data.user) {
      setMember(data.user.id, {
        name:     data.user.name,
        username: data.user.username,  // ← BUG FIX: was missing
        avatar:   data.user.avatar,
        role:     'member',
      });
    }
    import('./thread.render.js').then(({ showSystemMessage }) => {
      showSystemMessage(`${data.user?.name ?? 'Someone'} joined the thread`);
    });
  };
  socket.on(THREAD_WS.MEMBER_JOINED, _threadHandlers[THREAD_WS.MEMBER_JOINED]);

  _threadHandlers[THREAD_WS.MEMBER_REMOVED] = (data) => {
    if (data.user_id === threadState.currentUser?.id) {
      if (typeof window.showToast === 'function') {
        window.showToast('You were removed from this thread', 'error');
      }
      import('./thread.events.js').then(({ handleBackToList }) => handleBackToList());
    } else {
      import('./thread.render.js').then(({ showSystemMessage }) => {
        showSystemMessage('A member was removed from the thread');
      });
    }
  };
  socket.on(THREAD_WS.MEMBER_REMOVED, _threadHandlers[THREAD_WS.MEMBER_REMOVED]);

  _threadHandlers[THREAD_WS.THREAD_DELETED] = () => {
    if (typeof window.showToast === 'function') {
      window.showToast('This thread was deleted', 'error');
    }
    import('./thread.events.js').then(({ handleBackToList }) => handleBackToList());
  };
  socket.on(THREAD_WS.THREAD_DELETED, _threadHandlers[THREAD_WS.THREAD_DELETED]);

  // Updated: passes personality name to showLearnoraBotTyping
  _threadHandlers[THREAD_WS.LEARNORA_THINKING] = (data) => {
    import('./thread.render.js').then(({ showLearnoraBotTyping }) => {
      showLearnoraBotTyping(data?.personality ?? 'Learnora');
    });
  };
  socket.on(THREAD_WS.LEARNORA_THINKING, _threadHandlers[THREAD_WS.LEARNORA_THINKING]);
}
