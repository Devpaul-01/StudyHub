/**
 * thread.render.js — Tailwind edition
 * DOM rendering functions for the thread chat view.
 *
 * CHANGES:
 *  - showLearnoraBotTyping now accepts personalityName param.
 *  - renderMessageEdit uses innerHTML + renderMessageText for rich formatting.
 *  - renderThreadHeader includes Meeting Notes (📋) button.
 *  - rerenderThreadListItem debounced single-item update (Issue 6).
 *  - All previous fixes retained (BUG-C2, BUG-C3, BUG-C5, HIDDEN-08, FE-05).
 */

import {
  threadState,
  getTypingUsers,
  getMember,
} from './thread.state.js';

import {
  threadMessageTemplate,
  systemMessageTemplate,
  typingIndicatorTemplate,
  threadListItemTemplate,
  searchResultItemTemplate,
  pinnedMessagesBannerTemplate,
  renderMessageText,
} from './thread.templates.js';

import { MSG_STATUS } from './thread.constants.js';


// ─── Toast (HIDDEN-08) ────────────────────────────────────────────────────────

export { showToast };

function showToast(message, type = 'info') {
  if (typeof window.showToast === 'function') {
    window.showToast(message, type);
  }
}


// ─── View switching ───────────────────────────────────────────────────────────

export function showThreadList() {
  document.getElementById('thread-list-panel')?.classList.remove('hidden');
  document.getElementById('thread-chat-panel')?.classList.add('hidden');
}

export function showThreadView(threadId) {
  document.getElementById('thread-list-panel')?.classList.add('hidden');
  const chatView = document.getElementById('thread-chat-panel');
  if (chatView) {
    chatView.classList.remove('hidden');
    chatView.setAttribute('data-thread-id', String(threadId));
  }
}


// ─── Thread list ─────────────────────────────────────────────────────────────

export function renderThreadList(state) {
  const container = document.getElementById('thread-list-container')
                 ?? document.querySelector("[data-role='thread-list']");
  if (!container) return;

  if (state === 'loading') {
    const skeleton = `
      <div class="flex items-center gap-3 px-4 py-3 animate-pulse border-b border-line-light">
        <div class="w-12 h-12 rounded-full bg-surface-hover flex-shrink-0"></div>
        <div class="flex-1 space-y-2">
          <div class="h-3.5 bg-surface-hover rounded-full w-2/3"></div>
          <div class="h-3 bg-surface-raised rounded-full w-5/6"></div>
        </div>
      </div>`;
    container.innerHTML = skeleton.repeat(5);
    return;
  }

  if (state === 'error') {
    container.innerHTML = `
      <div class="flex flex-col items-center gap-2 py-12 px-4">
        <span class="text-2xl">⚠️</span>
        <p class="text-sm text-ink-secondary text-center">Failed to load threads.</p>
        <button data-action="reload-threads"
                class="text-sm text-accent font-semibold hover:underline">
          Retry
        </button>
      </div>`;
    return;
  }

  const threads = Array.from(threadState.threadList.values()).sort(
    (a, b) => new Date(b.last_activity) - new Date(a.last_activity)
  );

  if (!threads.length) {
    container.innerHTML = `
      <div class="flex flex-col items-center gap-3 py-16 px-4 text-center">
        <span class="text-4xl">💬</span>
        <p class="text-sm text-ink-secondary">No threads yet.</p>
        <button data-action="open-create-thread-modal"
                class="text-sm font-semibold text-white bg-accent hover:bg-accent-hover
                       active:scale-95 rounded-xl px-4 py-2 transition-all">
          Create a Thread
        </button>
      </div>`;
    return;
  }

  container.innerHTML = threads
    .map((t) => threadListItemTemplate(t, threadState.currentUser?.id))
    .join('');
}

/**
 * BUG-C2 FIX: Move a thread list item to the top without re-rendering all items.
 */
export function moveThreadToTop(threadId) {
  const container = document.getElementById('thread-list-container')
                 ?? document.querySelector("[data-role='thread-list']");
  if (!container) return;

  const item = container.querySelector(`[data-thread-id="${threadId}"]`);
  if (item && container.firstElementChild !== item) {
    container.prepend(item);
  }

  const thread  = threadState.threadList.get(threadId);
  const lastMsg = thread?.last_message;
  if (!item || !lastMsg) return;

  const previewEl = item.querySelector('.thread-last-message');
  if (previewEl) {
    const prefix = lastMsg.sender_id === threadState.currentUser?.id
      ? ''
      : lastMsg.sender ? `${lastMsg.sender.split(' ')[0]}: ` : '';
    previewEl.textContent = prefix + (lastMsg.text ?? '').slice(0, 55);
  }
}


// ─── Issue 6: rerenderThreadListItem ─────────────────────────────────────────

const _rerenderDebounce = new Map();

/**
 * Re-render a single thread list item in-place (debounced 50 ms).
 * O(1) DOM update — does not touch any other list items.
 */
export function rerenderThreadListItem(threadId) {
  clearTimeout(_rerenderDebounce.get(threadId));
  _rerenderDebounce.set(threadId, setTimeout(() => {
    _doRerenderThreadListItem(threadId);
    _rerenderDebounce.delete(threadId);
  }, 50));
}

function _doRerenderThreadListItem(threadId) {
  const container = document.getElementById('thread-list-container')
                 ?? document.querySelector("[data-role='thread-list']");
  if (!container) return;

  const thread = threadState.threadList.get(threadId);
  if (!thread) return;

  const newHtml      = threadListItemTemplate(thread, threadState.currentUser?.id);
  const existingItem = container.querySelector(`[data-thread-id="${threadId}"]`);

  if (existingItem) {
    existingItem.outerHTML = newHtml;
  } else {
    container.insertAdjacentHTML('afterbegin', newHtml);
  }
}


// ─── Thread header ────────────────────────────────────────────────────────────

export function renderThreadHeader(thread, userStatus) {
  const header = document.getElementById('thread-chat-header');
  if (!header) return;

  const avatarHtml = thread.avatar
    ? `<img src="${_escAttr(thread.avatar)}"
            class="w-9 h-9 rounded-full object-cover flex-shrink-0" alt="${_esc(thread.title)}">`
    : `<div class="w-9 h-9 rounded-full bg-accent-subtle text-accent text-sm font-bold
                   flex items-center justify-center flex-shrink-0 select-none">
         ${_esc(thread.title.charAt(0).toUpperCase())}
       </div>`;

  const closedBadge = !thread.is_open
    ? `<span class="text-[10px] font-semibold text-red-400 bg-red-500/10 rounded px-1.5 py-0.5 ml-1">
         Closed
       </span>`
    : '';

  header.innerHTML = `
    <div class="flex items-center gap-2 min-w-0 flex-1">
      ${avatarHtml}
      <div class="min-w-0">
        <h2 class="text-sm font-bold text-ink-primary truncate leading-tight">
          ${_esc(thread.title)}${closedBadge}
        </h2>
        <p class="text-xs text-ink-tertiary leading-tight">
          ${thread.member_count} member${thread.member_count !== 1 ? 's' : ''}
        </p>
      </div>
    </div>

    <div class="flex items-center gap-0.5 flex-shrink-0">
      <button data-action="thread-meeting-notes" title="AI Meeting Notes"
              class="w-9 h-9 rounded-full flex items-center justify-center text-ink-tertiary
                     hover:text-emerald-400 hover:bg-emerald-500/10 active:bg-emerald-500/15
                     transition-colors text-base">
        📋
      </button>
      <button data-action="thread-search" title="Search"
              class="w-9 h-9 rounded-full flex items-center justify-center text-ink-tertiary
                     hover:text-accent hover:bg-accent-subtle active:bg-accent-subtle transition-colors">
        <svg width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
      </button>
      <button data-action="thread-open-pinned-list" title="Pinned"
              class="w-9 h-9 rounded-full flex items-center justify-center text-ink-tertiary
                     hover:text-amber-400 hover:bg-amber-500/10 active:bg-amber-500/15 transition-colors text-base">
        📌
      </button>
      <button data-action="thread-info" title="Thread info"
              class="w-9 h-9 rounded-full flex items-center justify-center text-ink-tertiary
                     hover:text-accent hover:bg-accent-subtle active:bg-accent-subtle transition-colors">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="16" x2="12" y2="12"/>
          <line x1="12" y1="8" x2="12.01" y2="8"/>
        </svg>
      </button>
    </div>`;
}


// ─── Messages ─────────────────────────────────────────────────────────────────

export function renderMessages(messages) {
  const container = _msgContainer();
  if (!container) return;

  Array.from(container.children).forEach((child) => {
    if (child.id !== 'thread-top-sentinel') child.remove();
  });

  if (!messages.length) {
    container.insertAdjacentHTML('beforeend', `
      <div class="flex flex-col items-center justify-center h-full gap-2 py-16">
        <span class="text-3xl">👋</span>
        <p class="text-sm text-ink-tertiary">No messages yet. Say hello!</p>
      </div>`);
    return;
  }

  container.insertAdjacentHTML(
    'beforeend',
    messages.map((m) => threadMessageTemplate(m, threadState.currentUser?.id)).join('')
  );

  scrollToBottom(container);
  _applyCodeHighlighting(container);
}

export function prependMessages(messages) {
  const container = _msgContainer();
  if (!container || !messages.length) return;

  const prevHeight = container.scrollHeight;
  const html = messages
    .map((m) => threadMessageTemplate(m, threadState.currentUser?.id))
    .join('');

  const sentinel = document.getElementById('thread-top-sentinel');
  if (sentinel) {
    sentinel.insertAdjacentHTML('afterend', html);
  } else {
    container.insertAdjacentHTML('afterbegin', html);
  }
  container.scrollTop += container.scrollHeight - prevHeight;
  _applyCodeHighlighting(container);
}

export function renderNewMessage(message) {
  const container = _msgContainer();
  if (!container) return;

  if (message.id && document.querySelector(`[data-message-id="${message.id}"]`)) return;
  if (message.client_temp_id && document.querySelector(`[data-temp-id="${message.client_temp_id}"]`)) return;

  const atBottom = _isNearBottom(container);
  const html = threadMessageTemplate(message, threadState.currentUser?.id);
  container.insertAdjacentHTML('beforeend', html);

  _applyCodeHighlighting(container);

  if (atBottom || message.sender_id === threadState.currentUser?.id) {
    scrollToBottom(container);
  }

  _updateListItemPreview(message);
}

/**
 * BUG-C3 FIX: inject ⋯ options button after confirmation.
 * BUG-C5 FIX: no showToast here.
 * ISSUE-3 FIX: visibility handled by CSS only.
 */
export function confirmOptimisticMessage(clientTempId, serverData) {
  const el = document.querySelector(`[data-temp-id="${clientTempId}"]`);
  if (!el) return;

  if (serverData.id) el.setAttribute('data-message-id', String(serverData.id));
  el.removeAttribute('data-temp-id');

  if (serverData.id && !el.querySelector('.msg-options-btn')) {
    const bubbleCol = el.querySelector('.msg-bubble-col');
    const isMine    = el.classList.contains('mine');
    if (bubbleCol) {
      const posClass = isMine ? 'left-0 -translate-x-full' : 'right-0 translate-x-full';
      bubbleCol.insertAdjacentHTML('afterbegin', `
        <button class="msg-options-btn absolute ${posClass} top-0
                       w-7 h-7 rounded-full bg-surface-card shadow-sm border border-line
                       text-ink-secondary hover:text-accent hover:border-accent-border
                       flex items-center justify-center text-xs select-none"
                data-action="thread-open-options"
                data-message-id="${serverData.id}"
                aria-label="Message options">⋯</button>`);
    }
  }

  const statusEl = el.querySelector('.msg-status-icon');
  if (statusEl) statusEl.innerHTML = _statusIconSVG(serverData.status ?? MSG_STATUS.SENT);
  el.classList.remove('opacity-70', 'message-pending');
  el.classList.add('message-confirmed');
}

export function renderRetryPending(clientTempId) {
  const el = document.querySelector(`[data-temp-id="${clientTempId}"]`);
  if (!el) return;
  el.classList.remove('message-failed');
  el.classList.add('message-pending', 'opacity-70');
  const statusEl = el.querySelector('.msg-status-icon');
  if (statusEl) statusEl.innerHTML = _statusIconSVG(MSG_STATUS.PENDING);
  el.querySelector('.msg-retry-btn')?.remove();
}

export function markMessageFailed(clientTempId) {
  const el = document.querySelector(`[data-temp-id="${clientTempId}"]`);
  if (!el) return;
  el.classList.remove('message-pending', 'opacity-70');
  el.classList.add('message-failed');

  const statusEl = el.querySelector('.msg-status-icon');
  if (statusEl) statusEl.innerHTML = _statusIconSVG(MSG_STATUS.FAILED);

  if (!el.querySelector("[data-action='thread-retry']")) {
    const meta = el.querySelector('.msg-meta');
    meta?.insertAdjacentHTML('beforeend', `
      <button class="msg-retry-btn text-xs text-red-400 hover:text-red-300
                     underline transition-colors"
              data-action="thread-retry"
              data-temp-id="${clientTempId}">
        Retry
      </button>`);
  }
}

export function removeMessageFromDOM(messageId, clientTempId) {
  const sel = messageId
    ? `[data-message-id="${messageId}"]`
    : `[data-temp-id="${clientTempId}"]`;
  document.querySelector(sel)?.remove();
}

/**
 * Updated: uses innerHTML + renderMessageText for rich formatting with
 * mention highlighting and code syntax.
 */
export function renderMessageEdit(messageId, newText) {
  const el = document.querySelector(`[data-message-id="${messageId}"]`);
  if (!el) return;
  const textEl = el.querySelector('.msg-text');
  if (textEl) {
    textEl.innerHTML = renderMessageText(newText);
    _applyCodeHighlighting(el);
  }
  const editedEl = el.querySelector('.msg-edited-label');
  if (editedEl) {
    editedEl.classList.remove('hidden');
  } else {
    textEl?.insertAdjacentHTML(
      'afterend',
      `<span class="msg-edited-label text-[10px] opacity-60 ml-1">edited</span>`
    );
  }
}

export function renderMessageDelete(messageId) {
  const el = document.querySelector(`[data-message-id="${messageId}"]`);
  if (!el) return;
  const textEl = el.querySelector('.msg-text');
  if (textEl) {
    textEl.textContent = '[deleted]';
    textEl.classList.add('italic', 'opacity-50');
  }
  el.querySelector('.msg-reactions')?.remove();
  el.querySelector('.msg-options-btn')?.remove();
  el.querySelector('.msg-quick-reply-btn')?.remove();
}

export function renderPinUpdate(messageId, isPinned) {
  const el = document.querySelector(`[data-message-id="${messageId}"]`);
  if (!el) return;
  el.classList.toggle('message-pinned', isPinned);
  const pinIcon = el.querySelector('.msg-pin-icon');
  if (pinIcon) {
    pinIcon.classList.toggle('hidden', !isPinned);
    if (isPinned) pinIcon.textContent = '📌';
  }
}

export function renderReactionUpdate(messageId, reactions) {
  const el = document.querySelector(`[data-message-id="${messageId}"]`);
  if (!el) return;

  const bubbleCol = el.querySelector('.msg-bubble-col');
  if (!bubbleCol) return;

  const hasReactions = Object.keys(reactions ?? {}).length > 0;
  let rxnContainer = bubbleCol.querySelector('.msg-reactions');

  if (!hasReactions) { rxnContainer?.remove(); return; }

  if (!rxnContainer) {
    rxnContainer = document.createElement('div');
    rxnContainer.className = 'msg-reactions flex flex-wrap gap-1 mt-1';
    const metaEl = bubbleCol.querySelector('.msg-meta');
    if (metaEl) {
      bubbleCol.insertBefore(rxnContainer, metaEl);
    } else {
      bubbleCol.appendChild(rxnContainer);
    }
  }

  const currentUserId = threadState.currentUser?.id;
  rxnContainer.innerHTML = Object.values(reactions).map((r) => {
    const mine = Array.isArray(r.users) && r.users.includes(currentUserId);
    return `<button class="reaction-pill flex items-center gap-1 text-xs rounded-full px-2 py-0.5
                     transition-colors ${mine
                       ? 'bg-accent-subtle text-accent ring-1 ring-accent-border'
                       : 'bg-surface-raised text-ink-secondary hover:bg-surface-hover'}"
               data-action="thread-react"
               data-message-id="${messageId}"
               data-emoji="${_escAttr(r.emoji)}">
               ${_esc(r.emoji)} <span>${r.count}</span>
             </button>`;
  }).join('');
}


// ─── Status icons ─────────────────────────────────────────────────────────────

export function updateStatusIcons(messageIds, status) {
  for (const id of messageIds) {
    const el = document.querySelector(`[data-message-id="${id}"]`);
    if (!el) continue;
    const statusEl = el.querySelector('.msg-status-icon');
    if (statusEl) statusEl.innerHTML = _statusIconSVG(status);
  }
}

function _statusIconSVG(status) {
  switch (status) {
    case MSG_STATUS.PENDING:
      return `<svg class="w-3.5 h-3.5 opacity-50" viewBox="0 0 16 16" fill="none">
        <circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2 2"/>
      </svg>`;
    case MSG_STATUS.FAILED:
      return `<svg class="w-3.5 h-3.5 text-red-300" viewBox="0 0 16 16" fill="none">
        <circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.5"/>
        <line x1="8" y1="5" x2="8" y2="9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <circle cx="8" cy="11" r="0.7" fill="currentColor"/>
      </svg>`;
    case MSG_STATUS.SENT:
      return `<svg class="w-3.5 h-2.5 opacity-70" viewBox="0 0 16 10" fill="none">
        <path d="M1.5 5 L5.5 9 L14.5 1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
      </svg>`;
    case MSG_STATUS.DELIVERED:
      return `<svg class="w-4.5 h-2.5 opacity-70" viewBox="0 0 20 10" fill="none">
        <path d="M1.5 5 L5.5 9 L14.5 1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
        <path d="M6.5 5 L10.5 9 L19.5 1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
      </svg>`;
    case MSG_STATUS.READ:
      return `<svg class="w-4.5 h-2.5" viewBox="0 0 20 10" fill="none">
        <path d="M1.5 5 L5.5 9 L14.5 1" stroke="#a5b4fc" stroke-width="1.6" stroke-linecap="round"/>
        <path d="M6.5 5 L10.5 9 L19.5 1" stroke="#a5b4fc" stroke-width="1.6" stroke-linecap="round"/>
      </svg>`;
    default:
      return '';
  }
}


// ─── Typing indicator ─────────────────────────────────────────────────────────

export function renderTypingIndicator() {
  const container = _msgContainer();
  if (!container) return;

  let indicator   = document.getElementById('thread-typing-indicator');
  const typingIds = getTypingUsers();

  if (!typingIds.length) { indicator?.remove(); return; }

  const names = typingIds.map((id) => {
    const member = getMember(id);
    if (member?.name) return member.name;
    const msg = threadState.messages.find((m) => m.sender_id === id);
    return msg?.sender?.name ?? 'Someone';
  }).filter(Boolean);

  const text = names.length === 1
    ? `${names[0]} is typing…`
    : `${names.slice(0, 2).join(', ')} are typing…`;

  if (!indicator) {
    container.insertAdjacentHTML('beforeend', typingIndicatorTemplate(text));
  } else {
    indicator.querySelector('.typing-text')
      ?.replaceChildren(document.createTextNode(text));
  }
}

/**
 * Show Learnora / AI personality "thinking" indicator.
 * @param {string} personalityName — display name of the AI (e.g. "TeacherAI")
 */
export function showLearnoraBotTyping(personalityName = 'Learnora') {
  const container = _msgContainer();
  if (!container) return;

  if (document.getElementById('thread-learnora-typing')) return;

  container.insertAdjacentHTML('beforeend', `
    <div id="thread-learnora-typing"
         class="flex items-center gap-2 px-4 py-2">
      <div class="w-7 h-7 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600
                  flex items-center justify-center flex-shrink-0">
        <span class="text-xs">🤖</span>
      </div>
      <div class="flex flex-col">
        <span class="text-[10px] text-violet-300 font-semibold mb-0.5">${_esc(personalityName)}</span>
        <div class="flex items-center gap-0.5 bg-surface-raised rounded-full px-3 py-1.5">
          <span class="w-1.5 h-1.5 bg-violet-400 rounded-full animate-bounce"
                style="animation-delay:0ms"></span>
          <span class="w-1.5 h-1.5 bg-violet-400 rounded-full animate-bounce"
                style="animation-delay:150ms"></span>
          <span class="w-1.5 h-1.5 bg-violet-400 rounded-full animate-bounce"
                style="animation-delay:300ms"></span>
        </div>
      </div>
    </div>`);

  scrollToBottom(container);
  setTimeout(() => document.getElementById('thread-learnora-typing')?.remove(), 30000);
}


// ─── Pinned banner ────────────────────────────────────────────────────────────

export function renderPinnedBanner(pinnedMessages) {
  const container = document.getElementById('thread-pinned-banner');
  if (!container) return;
  container.innerHTML = pinnedMessages.length
    ? pinnedMessagesBannerTemplate(pinnedMessages)
    : '';
}


// ─── List utilities ───────────────────────────────────────────────────────────

export function updateUnreadBadge(threadId, count) {
  const el = document.querySelector(`[data-thread-id="${threadId}"] .thread-unread-badge`);
  if (!el) return;
  el.textContent = count > 99 ? '99+' : String(count);
  el.classList.toggle('hidden', count === 0);
}

export function updateOnlineBadge(userId, online) {
  document.querySelectorAll(`[data-user-id="${userId}"] .online-dot`)
    .forEach((dot) => dot.classList.toggle('online', online));
}

export function updateThreadAvatar(threadId, url) {
  const el = document.querySelector(`[data-thread-id="${threadId}"] .thread-avatar`);
  if (el?.tagName === 'IMG') el.src = url;
}


// ─── Search ───────────────────────────────────────────────────────────────────

export function renderSearchResults(results, query) {
  const container = document.getElementById('thread-search-results')
                 ?? document.querySelector("[data-role='thread-search-results']");
  if (!container) return;

  if (!results.length) {
    container.innerHTML = `
      <div class="py-12 text-center">
        <p class="text-sm text-ink-tertiary">No results for "<em>${_esc(query)}</em>"</p>
      </div>`;
    return;
  }
  container.innerHTML = results.map((r) => searchResultItemTemplate(r, query)).join('');
}

export function clearSearchResults() {
  const container = document.getElementById('thread-search-results')
                 ?? document.querySelector("[data-role='thread-search-results']");
  if (container) {
    container.innerHTML = `
      <div class="py-12 text-center text-sm text-ink-tertiary">
        Start typing to search messages…
      </div>`;
  }
  const input = document.getElementById('thread-search-input');
  if (input) input.value = '';
}


// ─── Attachment viewer ────────────────────────────────────────────────────────

export function openAttachmentViewer(attachments) {
  let modal = document.getElementById('thread-attachment-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id        = 'thread-attachment-modal';
    modal.className = 'fixed inset-0 z-50 flex-col bg-black/95 overflow-hidden';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    document.body.appendChild(modal);
  }

  const closeBtn = `
    <button data-action="close-modal"
            aria-label="Close"
            class="absolute top-4 right-4 w-10 h-10 rounded-full bg-white/10 text-white
                   flex items-center justify-center hover:bg-white/20 transition-colors z-10">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
      </svg>
    </button>`;

  if (!attachments.length) {
    modal.innerHTML = `
      <div class="relative flex flex-col items-center justify-center h-full gap-3">
        ${closeBtn}
        <span class="text-4xl">📭</span>
        <p class="text-white/60 text-sm">No attachments in this thread yet.</p>
      </div>`;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    return;
  }

  const items = attachments.map((msg) => {
    const atts = msg.attachments?.length ? msg.attachments : (msg.attachment_url ? [{
      attachment_url:  msg.attachment_url,
      attachment_name: msg.attachment_name,
      attachment_type: msg.attachment_type,
    }] : []);

    return atts.map((att) => {
      const isImage = att.attachment_type === 'image';
      const isVideo = att.attachment_type === 'video';
      const sender  = _esc(msg.sender?.name ?? 'Unknown');
      const date    = new Date(msg.sent_at).toLocaleDateString();
      const aUrl    = _escAttr(att.attachment_url);
      const aName   = _esc(att.attachment_name ?? 'File');

      const preview = isImage
        ? `<img src="${aUrl}" loading="lazy" alt="${aName}"
                class="w-full h-36 object-cover rounded-xl">`
        : isVideo
        ? `<video src="${aUrl}" class="w-full rounded-xl" controls preload="none"></video>`
        : `<div class="flex items-center gap-2 p-4 bg-white/10 rounded-xl">
             <span class="text-xl">📎</span>
             <span class="text-white text-sm truncate">${aName}</span>
           </div>`;

      return `
        <div class="flex flex-col gap-1">
          <a href="${aUrl}" target="_blank" rel="noopener noreferrer"
             class="block hover:opacity-90 transition-opacity">${preview}</a>
          <p class="text-[11px] text-white/50 px-1">${sender} · ${date}</p>
          <a href="${aUrl}" download="${aName}" target="_blank" rel="noopener noreferrer"
             class="inline-flex items-center gap-1 text-xs text-white/70 hover:text-white
                    bg-white/10 hover:bg-white/20 rounded-lg px-2.5 py-1 transition-colors mt-1 self-start">
            ⬇ Download
          </a>
        </div>`;
    }).join('');
  }).join('');

  modal.innerHTML = `
    <div class="relative flex flex-col h-full">
      ${closeBtn}
      <div class="px-5 pt-5 pb-3 flex-shrink-0">
        <h3 class="text-white font-bold text-base">Media &amp; Files (${attachments.length})</h3>
      </div>
      <div class="grid grid-cols-2 gap-3 px-5 pb-6 overflow-y-auto flex-1">${items}</div>
    </div>`;
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}


// ─── Reply preview ────────────────────────────────────────────────────────────

export function renderReplyPreview(context) {
  const container = document.getElementById('thread-reply-preview');
  if (!container) return;
  container.innerHTML = `
    <div class="flex items-center gap-2 bg-accent-subtle border-l-2 border-accent-border
                rounded-r-lg px-3 py-2 mx-3 mb-1">
      <div class="flex-1 min-w-0">
        <span class="block text-xs font-semibold text-accent">${_esc(context.sender)}</span>
        <span class="block text-xs text-ink-secondary truncate">
          ${_esc((context.text ?? '').slice(0, 80))}
        </span>
      </div>
      <button data-action="thread-cancel-reply" aria-label="Cancel reply"
              class="flex-shrink-0 w-6 h-6 rounded-full text-ink-tertiary hover:text-ink-secondary
                     hover:bg-surface-hover flex items-center justify-center text-xs transition-colors">
        ✕
      </button>
    </div>`;
  container.classList.remove('hidden');
}

export function clearReplyPreview() {
  const container = document.getElementById('thread-reply-preview');
  if (container) { container.innerHTML = ''; container.classList.add('hidden'); }
}


// ─── System / error messages ──────────────────────────────────────────────────

export function showSystemMessage(text) {
  const container = _msgContainer();
  if (!container) return;
  container.insertAdjacentHTML('beforeend', systemMessageTemplate(text));
  scrollToBottom(container);
}

export function showThreadError(message) {
  showToast(message, 'error');
}


// ─── Scroll helpers ───────────────────────────────────────────────────────────

export function scrollToBottom(container) {
  if (!container) return;
  container.scrollTo({ top: container.scrollHeight, behavior: 'instant' });
}

export function scrollToMessage(messageId) {
  const el = document.querySelector(`[data-message-id="${messageId}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.classList.add('message-highlight');
  setTimeout(() => el.classList.remove('message-highlight'), 2000);
}


// ─── Code highlighting helper ─────────────────────────────────────────────────

/**
 * Apply highlight.js to any un-highlighted code blocks within a container.
 * Called after rendering new messages. Safe to call multiple times — hljs
 * skips already-highlighted blocks.
 */
function _applyCodeHighlighting(container) {
  if (!window.hljs || !container) return;
  try {
    container.querySelectorAll('pre code:not(.hljs)').forEach((block) => {
      window.hljs.highlightElement(block);
    });
  } catch { /* ignore highlighting errors */ }
}


// ─── Internals ────────────────────────────────────────────────────────────────

function _isNearBottom(container, threshold = 150) {
  return container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
}

function _msgContainer() {
  return document.getElementById('thread-messages-list')
      ?? document.querySelector("[data-role='thread-messages']");
}

function _updateListItemPreview(message) {
  const el = document.querySelector(
    `[data-thread-id="${message.thread_id}"] .thread-last-message`
  );
  if (!el) return;
  el.textContent =
    (message.text_content ?? '').slice(0, 80) ||
    (message.attachment_url || message.attachments?.length ? '📎 Attachment' : '');
}

function _esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _escAttr(str) {
  return String(str ?? '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
