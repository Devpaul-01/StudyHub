/**
 * thread.delegation.js
 * Single root-level event delegation for the entire thread UI.
 *
 * CHANGES:
 *  - Issue 1: File input generates previewUrl for images (URL.createObjectURL).
 *    _renderAttachmentStrip redesigned with thumbnail cards.
 *    URL.revokeObjectURL called on remove and clear-all.
 *    pendingAttachments.length used for send-button enable check.
 *  - Issue 4: thread-edit-settings handler opens openEditThreadModal().
 *  - Issue 5: thread-tab handler + _switchTab() helper retained.
 *  - NEW: thread-ai-action handler — emits thread_ai_action WS event.
 *  - NEW: thread-cancel-request handler — calls handleCancelMyRequest.
 *  - NEW: thread-meeting-notes handler — calls handleOpenMeetingNotes.
 *  - NEW: thread-copy-code handler — copies fenced code block via DOM ID.
 *  - All previous fixes retained (FE-01, FE-03, FE-05, FE-06, FEAT-02).
 */

import { threadState, resetThreadSession } from './thread.state.js';


// ─── Bootstrap ────────────────────────────────────────────────────────────────

export function initThreadDelegation() {
  // click and submit use the CAPTURE phase. In the feed, app.unified.js
  // attaches a click/submit listener to document.body that calls
  // event.stopPropagation() unconditionally, so a bubble-phase listener on
  // document never receives those events (every data-action button inside
  // Threads was dead). Capturing on document runs before body.
  document.addEventListener('click',               _onClick,           { capture: true });
  document.addEventListener('keydown',             _onKeydown,         { capture: false });
  document.addEventListener('submit',              _onSubmit,          { capture: true });
  document.addEventListener('input',               _onInput,           { capture: false });
  document.addEventListener('thread:open-options', _onLongPressOptions);
  document.addEventListener('thread:save-edit',    _onSaveEditThread);
}

export function destroyThreadDelegation() {
  document.removeEventListener('click',               _onClick);
  document.removeEventListener('keydown',             _onKeydown);
  document.removeEventListener('submit',              _onSubmit);
  document.removeEventListener('input',               _onInput);
  document.removeEventListener('thread:open-options', _onLongPressOptions);
  document.removeEventListener('thread:save-edit',    _onSaveEditThread);
}

function _onLongPressOptions(e) {
  const { messageId } = e.detail ?? {};
  if (messageId) _openOptionsSheet(messageId);
}


// ─── Helpers ──────────────────────────────────────────────────────────────────

function _closest(target, selector) {
  return target.closest(selector);
}


// ─── Issue 5: Tab switching ───────────────────────────────────────────────────

function _switchTab(tab) {
  threadState.activeTab = tab;

  const messagesContent = document.getElementById('thread-tab-messages');
  const invitesContent  = document.getElementById('thread-tab-invites');
  const searchBar       = document.getElementById('thread-search-bar');
  const tabMsgs         = document.getElementById('tab-messages');
  const tabInv          = document.getElementById('tab-invites');

  const isMessages = tab === 'messages';

  messagesContent?.classList.toggle('hidden', !isMessages);
  invitesContent?.classList.toggle('hidden',   isMessages);
  searchBar?.classList.toggle('hidden',         !isMessages);

  if (tabMsgs) {
    tabMsgs.classList.toggle('border-indigo-600', isMessages);
    tabMsgs.classList.toggle('text-indigo-600',   isMessages);
    tabMsgs.classList.toggle('border-transparent',!isMessages);
    tabMsgs.classList.toggle('text-gray-500',     !isMessages);
  }
  if (tabInv) {
    tabInv.classList.toggle('border-indigo-600', !isMessages);
    tabInv.classList.toggle('text-indigo-600',   !isMessages);
    tabInv.classList.toggle('border-transparent', isMessages);
    tabInv.classList.toggle('text-gray-500',      isMessages);
  }

  if (!isMessages) {
    const badge = document.getElementById('thread-invites-badge');
    badge?.classList.add('hidden');
    badge?.classList.remove('flex');
  }
}


// ─── Issue 4: thread:save-edit custom-event handler ──────────────────────────

async function _onSaveEditThread(e) {
  const { threadId, ...fields } = e.detail ?? {};
  if (!threadId) return;
  import('./thread.events.js').then(({ handleSaveThreadEdit }) => {
    handleSaveThreadEdit(threadId, fields);
  });
}


// ─── Input delegation ─────────────────────────────────────────────────────────

let _searchDebounce     = null;
let _listSearchDebounce = null;
let _mentionDebounce    = null;

function _onInput(e) {
  const el = e.target;

  if (el.id === 'thread-message-input' || el.matches("[data-role='thread-input']")) {
    if (threadState.activeThreadId) {
      import('./thread.events.js').then(({ handleInputTyping }) => handleInputTyping());
    }
    const btn = document.getElementById('thread-send-btn');
    if (btn) btn.disabled = !el.value.trim() && !(threadState.pendingAttachments?.length);

    _handleMentionInput(el);
    return;
  }

  if (el.id === 'thread-search-input' || el.matches("[data-role='thread-search-input']")) {
    clearTimeout(_searchDebounce);
    const q = el.value.trim();
    if (!q) {
      import('./thread.events.js').then(({ handleClearSearch }) => handleClearSearch());
      return;
    }
    _searchDebounce = setTimeout(() => {
      import('./thread.events.js').then(({ handleThreadSearch }) => handleThreadSearch(q));
    }, 300);
    return;
  }

  if (el.id === 'thread-list-search' || el.matches("[data-role='thread-list-search']")) {
    clearTimeout(_listSearchDebounce);
    const q = el.value;
    const clearBtn = document.querySelector("[data-action='clear-thread-list-search']");
    if (clearBtn) clearBtn.classList.toggle('hidden', !q);
    _listSearchDebounce = setTimeout(() => _filterThreadListInline(q), 150);
  }
}

function _filterThreadListInline(query) {
  const q       = (query ?? '').toLowerCase().trim();
  const threads = Array.from(threadState.threadList.values());
  const filtered = q
    ? threads.filter((t) =>
        t.title?.toLowerCase().includes(q) ||
        t.description?.toLowerCase().includes(q) ||
        (t.tags ?? []).some((tag) => tag.toLowerCase().includes(q)) ||
        t.department?.toLowerCase().includes(q)
      )
    : threads;

  const container = document.getElementById('thread-list-container')
                 ?? document.querySelector("[data-role='thread-list']");
  if (!container) return;

  if (!filtered.length) {
    container.innerHTML = `<p class="text-center text-sm text-gray-400 py-8">No threads match.</p>`;
    return;
  }

  import('./thread.templates.js').then(({ threadListItemTemplate }) => {
    const sorted = [...filtered].sort(
      (a, b) => new Date(b.last_activity) - new Date(a.last_activity)
    );
    container.innerHTML = sorted
      .map((t) => threadListItemTemplate(t, threadState.currentUser?.id))
      .join('');
  });
}


// ─── @mention autocomplete ────────────────────────────────────────────────────

function _handleMentionInput(inputEl) {
  clearTimeout(_mentionDebounce);
  _mentionDebounce = setTimeout(() => {
    const val        = inputEl.value;
    const cursorPos  = inputEl.selectionStart ?? val.length;
    const textBefore = val.slice(0, cursorPos);
    const match      = textBefore.match(/@([a-zA-Z0-9_]*)$/);

    if (match) {
      _showMentionSuggestions(match[1].toLowerCase(), textBefore.lastIndexOf('@'), cursorPos);
    } else {
      _hideMentionSuggestions();
    }
  }, 80);
}

function _showMentionSuggestions(query, atPos, cursorPos) {
  const members = Array.from(threadState.memberMap.entries())
    .filter(([id]) => id !== threadState.currentUser?.id)
    .filter(([, m]) =>
      // BUG FIX: filter by username (not just display name)
      m.username?.toLowerCase().includes(query) ||
      m.name?.toLowerCase().includes(query)
    )
    .slice(0, 5);

  const learnora   = { id: 'learnora', name: 'Learnora', username: 'learnora', isBot: true };
  const showBot    = query === '' || 'learnora'.startsWith(query);
  const candidates = showBot
    ? [['learnora', learnora], ...members]
    : members;

  if (!candidates.length) { _hideMentionSuggestions(); return; }

  let box = document.getElementById('thread-mention-suggestions');
  if (!box) {
    box = document.createElement('div');
    box.id        = 'thread-mention-suggestions';
    box.className =
      'absolute bottom-full left-0 right-0 mb-1 bg-white rounded-xl shadow-lg ' +
      'border border-gray-200 overflow-hidden z-30 max-h-48 overflow-y-auto';
    box.setAttribute('role', 'listbox');
    const composeArea = document.getElementById('thread-message-input')?.closest('.flex.items-end');
    if (composeArea) {
      composeArea.style.position = 'relative';
      composeArea.appendChild(box);
    } else {
      document.body.appendChild(box);
    }
  }

  box.innerHTML = candidates.map(([, m]) => {
    const isBot  = !!m.isBot;
    // BUG FIX: use m.username (not m.name) as the insertion value
    const uname  = _esc(m.username ?? m.name ?? '');
    const name   = _esc(m.name ?? uname);
    const avatar = isBot
      ? `<div class="w-7 h-7 rounded-full bg-violet-100 text-violet-700 text-xs flex items-center justify-center">🤖</div>`
      : m.avatar
        ? `<img src="${_escAttr(m.avatar)}" class="w-7 h-7 rounded-full object-cover">`
        : `<div class="w-7 h-7 rounded-full bg-indigo-100 text-indigo-700 text-xs flex items-center justify-center font-bold">${name.charAt(0)}</div>`;
    return `
      <button class="flex items-center gap-2 w-full px-3 py-2 hover:bg-indigo-50 transition-colors text-left"
              data-action="thread-insert-mention"
              data-username="${_escAttr(uname)}"
              data-at-pos="${atPos}"
              data-cursor-pos="${cursorPos}">
        ${avatar}
        <span class="text-sm text-gray-800">${name}</span>
        <span class="text-xs text-gray-400">@${uname}</span>
      </button>`;
  }).join('');

  box.classList.remove('hidden');
}

function _hideMentionSuggestions() {
  document.getElementById('thread-mention-suggestions')?.classList.add('hidden');
}


// ─── Click delegation ─────────────────────────────────────────────────────────

function _onClick(e) {
  const t = e.target;
  if (!t) return;

  // ── @mention insert ───────────────────────────────────────────────────────
  const mentionBtn = _closest(t, "[data-action='thread-insert-mention']");
  if (mentionBtn) {
    const username  = mentionBtn.dataset.username;
    const atPos     = Number(mentionBtn.dataset.atPos);
    const cursorPos = Number(mentionBtn.dataset.cursorPos);
    const input     = document.getElementById('thread-message-input');
    if (input && username !== undefined) {
      const before   = input.value.slice(0, atPos);
      const after    = input.value.slice(cursorPos);
      input.value    = `${before}@${username} ${after}`;
      input.focus();
      const newCursor = atPos + username.length + 2;
      input.setSelectionRange(newCursor, newCursor);
      input.dispatchEvent(new Event('input'));
    }
    _hideMentionSuggestions();
    return;
  }

  // ── Issue 5: Tab switching ────────────────────────────────────────────────
  const tabBtn = _closest(t, "[data-action='thread-tab']");
  if (tabBtn) {
    e.preventDefault();
    _switchTab(tabBtn.dataset.tab);
    return;
  }

  // ── Open thread ───────────────────────────────────────────────────────────
  const threadItem = _closest(t, "[data-action='open-thread']");
  if (threadItem) {
    const threadId = Number(threadItem.dataset.threadId);
    if (threadId) {
      e.preventDefault();
      import('./thread.events.js').then(({ handleOpenThread }) => handleOpenThread(threadId));
    }
    return;
  }

  // ── Back ──────────────────────────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-back']")) {
    e.preventDefault();
    import('./thread.events.js').then(({ handleBackToList }) => handleBackToList());
    return;
  }

  // ── Send message ──────────────────────────────────────────────────────────
  if (
    _closest(t, "[data-action='thread-send']")        ||
    _closest(t, "[data-action='thread-send-message']") ||
    _closest(t, '#thread-send-btn')
  ) {
    e.preventDefault();
    _hideMentionSuggestions();
    import('./thread.events.js').then(({ handleSendMessage }) =>
      handleSendMessage().catch(() => showToast('Failed to send message', 'error'))
    );
    return;
  }

  // ── NEW: Copy code block ──────────────────────────────────────────────────
  const copyCodeBtn = _closest(t, "[data-action='thread-copy-code']");
  if (copyCodeBtn) {
    e.preventDefault();
    const codeId = copyCodeBtn.dataset.codeId;
    const preEl  = codeId ? document.getElementById(codeId) : null;
    const code   = preEl?.textContent ?? '';
    if (code) {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(code)
          .then(() => showToast('Code copied!', 'success'))
          .catch(() => showToast('Copy failed', 'error'));
      } else {
        const ta = document.createElement('textarea');
        ta.value = code;
        ta.style.cssText = 'position:fixed;opacity:0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        showToast('Code copied!', 'success');
      }
    }
    return;
  }

  // ── NEW: AI action on message ─────────────────────────────────────────────
  const aiActionBtn = _closest(t, "[data-action='thread-ai-action']");
  if (aiActionBtn) {
    e.preventDefault();
    _closeOptionsSheet();
    const msgId  = Number(aiActionBtn.dataset.messageId);
    const action = aiActionBtn.dataset.aiAction;
    if (!msgId || !action) return;

    const _emit = (targetLang) => {
      import('./thread.init.js').then(({ socket }) => {
        socket?.emit('thread_ai_action', {
          token:      api.getToken(),
          thread_id:  threadState.activeThreadId,
          message_id: msgId,
          action,
          ...(targetLang && { target_lang: targetLang }),
        });
        showToast('Asking Learnora…', 'info');
      });
    };

    if (action === 'translate') {
      const lang = window.prompt('Translate to which language?', 'Spanish');
      if (lang) _emit(lang);
    } else {
      _emit(null);
    }
    return;
  }

  // ── NEW: Meeting notes ────────────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-meeting-notes']")) {
    e.preventDefault();
    import('./thread.events.js').then(({ handleOpenMeetingNotes }) =>
      handleOpenMeetingNotes().catch(() => showToast('Failed to open meeting notes', 'error'))
    );
    return;
  }

  // ── NEW: Cancel my join request ───────────────────────────────────────────
  const cancelReqBtn = _closest(t, "[data-action='thread-cancel-request']");
  if (cancelReqBtn) {
    e.preventDefault();
    const requestId = Number(cancelReqBtn.dataset.requestId);
    if (requestId) {
      import('./thread.events.js').then(({ handleCancelMyRequest }) =>
        handleCancelMyRequest(requestId)
          .catch(() => showToast('Failed to cancel request', 'error'))
      );
    }
    return;
  }

  // ── Open create thread modal ──────────────────────────────────────────────
  if (_closest(t, "[data-action='open-create-thread-modal']")) {
    e.preventDefault();
    const modal = document.getElementById('thread-create-modal');
    if (modal) {
      ['thread-title', 'thread-description', 'thread-tags'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = '';
      });
      const maxEl  = document.getElementById('thread-max-members');
      if (maxEl) maxEl.value = '10';
      const apprEl = document.getElementById('thread-requires-approval');
      if (apprEl) apprEl.checked = true;
      modal.classList.remove('hidden');
      modal.classList.add('flex');
    }
    return;
  }

  // ── Close create thread modal ─────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-close-create-modal']")) {
    e.preventDefault();
    const modal = document.getElementById('thread-create-modal');
    if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
    return;
  }

  // ── Submit create thread ──────────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-submit-create']")) {
    e.preventDefault();
    const titleEl = document.getElementById('thread-title');
    const descEl  = document.getElementById('thread-description');
    const maxEl   = document.getElementById('thread-max-members');
    const apprEl  = document.getElementById('thread-requires-approval');
    const tagsEl  = document.getElementById('thread-tags');

    import('./thread.events.js').then(({ handleCreateThread }) => {
      handleCreateThread({
        title:             titleEl?.value?.trim()   ?? '',
        description:       descEl?.value?.trim()    ?? '',
        max_members:       parseInt(maxEl?.value ?? '10') || 10,
        requires_approval: apprEl?.checked ?? true,
        tags:              (tagsEl?.value ?? '').split(',').map((s) => s.trim()).filter(Boolean),
      }).catch(() => showToast('Failed to create thread', 'error'));
    });
    return;
  }

  // ── Close info / AI / generic modals ─────────────────────────────────────
  if (_closest(t, "[data-action='thread-close-info-modal']")) {
    e.preventDefault();
    document.getElementById('thread-info-modal')?.classList.add('hidden');
    return;
  }
  if (_closest(t, "[data-action='thread-close-ai-modal']")) {
    e.preventDefault();
    document.getElementById('thread-ask-ai-modal')?.classList.add('hidden');
    return;
  }
  if (_closest(t, "[data-action='close-modal']")) {
    // 'close-modal' is also a feed action. Now that this listener sees every
    // click on the page, only touch dialogs that belong to Threads and leave
    // the rest to the feed's own handler.
    const dialog = t.closest("[role='dialog'], .fixed.inset-0");
    if (dialog?.id?.startsWith('thread-')) {
      e.preventDefault();
      dialog.classList.add('hidden');
    }
    return;
  }

  // ── Submit AI question (Ask Learnora modal) ───────────────────────────────
  if (_closest(t, "[data-action='thread-submit-ai']")) {
    e.preventDefault();
    const modal    = document.getElementById('thread-ask-ai-modal');
    const question = modal?.querySelector('#thread-ai-question')?.value?.trim();
    const mode     = modal?.querySelector("input[name='thread-ai-mode']:checked")?.value ?? '';

    if (question && threadState.activeThreadId) {
      import('./thread.init.js').then(({ socket: sock }) => {
        sock?.emit('request_ai_response', {
          token:     api.getToken(),
          thread_id: threadState.activeThreadId,
          question,
          mode,
        });
        showToast('Asking Learnora…', 'info');
      });
    }
    if (modal) modal.classList.add('hidden');
    return;
  }

  // ── Confirm modal ─────────────────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-confirm-cancel']")) {
    e.preventDefault();
    document.getElementById('thread-confirm-modal')?.classList.add('hidden');
    return;
  }

  // ── Issue 1: Attach file (multiple) ──────────────────────────────────────
  if (_closest(t, "[data-action='thread-attach-file']")) {
    e.preventDefault();
    let fileInput = document.getElementById('thread-file-input-hidden');
    if (!fileInput) {
      fileInput           = document.createElement('input');
      fileInput.id        = 'thread-file-input-hidden';
      fileInput.type      = 'file';
      fileInput.multiple  = true;
      fileInput.accept    = 'image/*,video/*,.pdf,.doc,.docx,.txt,.csv,.xlsx,.pptx';
      fileInput.style.display = 'none';
      document.body.appendChild(fileInput);

      fileInput.addEventListener('change', (ev) => {
        const files    = Array.from(ev.target.files ?? []);
        const maxBytes = 25 * 1024 * 1024;
        const maxCount = 5;

        const valid = [];
        for (const file of files) {
          if (file.size > maxBytes) {
            showToast(`${file.name} is too large (max 25 MB)`, 'error');
            continue;
          }
          // Generate preview URL for images
          const previewUrl = file.type.startsWith('image/')
            ? URL.createObjectURL(file)
            : null;
          valid.push({ file, name: file.name, type: file.type, size: file.size, previewUrl });
        }

        if (!valid.length) return;

        const existing  = threadState.pendingAttachments ?? [];
        const remaining = maxCount - existing.length;
        const toAdd     = valid.slice(0, remaining);

        if (valid.length > remaining) {
          showToast(`Max ${maxCount} attachments per message (added ${toAdd.length})`, 'error');
        }

        threadState.pendingAttachments = [...existing, ...toAdd];
        _renderAttachmentStrip(threadState.pendingAttachments);

        const btn = document.getElementById('thread-send-btn');
        if (btn) btn.disabled = false;
        fileInput.value = '';
      });
    }
    fileInput.click();
    return;
  }

  // ── Issue 1: Remove individual attachment ─────────────────────────────────
  const removeAttBtn = _closest(t, "[data-action='thread-remove-attachment']");
  if (removeAttBtn) {
    e.preventDefault();
    const idx = Number(removeAttBtn.dataset.attachIndex);
    if (!isNaN(idx)) {
      const att = threadState.pendingAttachments[idx];
      if (att?.previewUrl) URL.revokeObjectURL(att.previewUrl);
      threadState.pendingAttachments.splice(idx, 1);
      _renderAttachmentStrip(threadState.pendingAttachments);
      const btn   = document.getElementById('thread-send-btn');
      const input = document.getElementById('thread-message-input');
      if (btn && input) btn.disabled = !input.value.trim() && !threadState.pendingAttachments.length;
    }
    return;
  }

  // ── Clear all attachments ─────────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-clear-attachment']")) {
    e.preventDefault();
    (threadState.pendingAttachments ?? []).forEach((att) => {
      if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
    });
    threadState.pendingAttachments = [];
    _renderAttachmentStrip([]);
    const btn   = document.getElementById('thread-send-btn');
    const input = document.getElementById('thread-message-input');
    if (btn && input) btn.disabled = !input.value.trim();
    return;
  }

  // ── Message options sheet ─────────────────────────────────────────────────
  const optBtn = _closest(t, "[data-action='thread-open-options']");
  if (optBtn) {
    e.preventDefault();
    e.stopPropagation();
    const msgId = Number(optBtn.dataset.messageId);
    if (msgId) _openOptionsSheet(msgId);
    return;
  }

  if (t.id === 'thread-options-backdrop') { _closeOptionsSheet(); return; }

  // ── Pin banner navigation ─────────────────────────────────────────────────
  const pinNavBtn = _closest(t, '.pin-nav-btn');
  if (pinNavBtn) {
    e.stopPropagation();
    const banner = pinNavBtn.closest('.thread-pinned-banner');
    if (!banner) return;
    let pins;
    try { pins = JSON.parse(banner.dataset.pins ?? '[]'); } catch { return; }
    if (!pins.length) return;

    let idx = parseInt(banner.dataset.pinIndex ?? '0', 10);
    idx = (idx + parseInt(pinNavBtn.dataset.pinDir, 10) + pins.length) % pins.length;
    banner.dataset.pinIndex = String(idx);

    const pin     = pins[idx];
    const content = banner.querySelector('.pin-content');
    if (content) {
      content.dataset.messageId = String(pin.id);
      content.setAttribute('data-message-id', String(pin.id));
      const senderEl = content.querySelector('.pin-sender');
      const textEl   = content.querySelector('.pin-text');
      if (senderEl) senderEl.textContent = pin.sender;
      if (textEl)   textEl.textContent   = pin.text;
    }
    const iconBtn = banner.querySelector('.pin-icon-btn');
    if (iconBtn) iconBtn.dataset.messageId = String(pin.id);
    return;
  }

  // ── Requires-approval toggle ──────────────────────────────────────────────
  const apprToggle = _closest(t, "[data-action='thread-toggle-approval']");
  if (apprToggle) {
    const threadId         = Number(apprToggle.dataset.threadId ?? threadState.activeThreadId);
    const requiresApproval = apprToggle.checked;
    if (threadId) {
      import('./thread.api.js').then(({ updateThreadSettings }) => {
        updateThreadSettings(threadId, { requires_approval: requiresApproval })
          .then(() => showToast(`Approval ${requiresApproval ? 'enabled' : 'disabled'}`, 'success'))
          .catch(() => {
            showToast('Failed to update setting', 'error');
            apprToggle.checked = !requiresApproval;
          });
      });
    }
    return;
  }

  // ── Issue 4: Edit thread settings ─────────────────────────────────────────
  const editSettingsBtn = _closest(t, "[data-action='thread-edit-settings']");
  if (editSettingsBtn) {
    const threadId = Number(editSettingsBtn.dataset.threadId ?? threadState.activeThreadId);
    const thread   = threadState.threadList.get(threadId);
    if (thread) {
      import('./thread.modals.js').then(({ openEditThreadModal }) => {
        openEditThreadModal(thread);
      });
    }
    return;
  }

  // ── Retry loading the thread list (error state in thread.render.js) ───────
  if (_closest(t, "[data-action='reload-threads']")) {
    e.preventDefault();
    import('./thread.events.js').then(({ handleLoadThreadList }) =>
      handleLoadThreadList().catch(() => showToast('Failed to load thread list. Please refresh.', 'error'))
    );
    return;
  }

  // ── Retry failed message ──────────────────────────────────────────────────
  const retryBtn = _closest(t, "[data-action='thread-retry']");
  if (retryBtn) {
    const tempId = retryBtn.dataset.tempId;
    if (tempId) {
      import('./thread.events.js').then(({ handleRetryMessage }) =>
        handleRetryMessage(tempId).catch(() => showToast('Failed to send message', 'error'))
      );
    }
    return;
  }

  // ── Delete message ────────────────────────────────────────────────────────
  const deleteBtn = _closest(t, "[data-action='thread-delete-message']");
  if (deleteBtn) {
    const msgId = Number(deleteBtn.dataset.messageId);
    if (msgId) {
      _closeOptionsSheet();
      import('./thread.events.js').then(({ handleDeleteMessage }) =>
        handleDeleteMessage(msgId).catch(() => showToast('Failed to delete message', 'error'))
      );
    }
    return;
  }

  // ── Edit message ──────────────────────────────────────────────────────────
  const editBtn = _closest(t, "[data-action='thread-edit-message']");
  if (editBtn) {
    const msgId = Number(editBtn.dataset.messageId);
    if (msgId) { _closeOptionsSheet(); _startInlineEdit(msgId); }
    return;
  }

  // ── Save inline edit ──────────────────────────────────────────────────────
  const saveEditBtn = _closest(t, "[data-action='thread-save-edit']");
  if (saveEditBtn) {
    const msgId    = Number(saveEditBtn.dataset.messageId);
    const wrap     = document.querySelector(`[data-message-id="${msgId}"]`);
    const textarea = wrap?.querySelector('.inline-edit-textarea');
    if (msgId && textarea) {
      import('./thread.events.js').then(({ handleEditMessage }) => {
        handleEditMessage(msgId, textarea.value)
          .catch(() => showToast('Failed to update message', 'error'));
      });
      _cancelInlineEdit(msgId);
    }
    return;
  }

  // ── Cancel inline edit ────────────────────────────────────────────────────
  const cancelEditBtn = _closest(t, "[data-action='thread-cancel-edit']");
  if (cancelEditBtn) {
    const msgId = Number(cancelEditBtn.dataset.messageId);
    if (msgId) _cancelInlineEdit(msgId);
    return;
  }

  // ── Pin message ───────────────────────────────────────────────────────────
  const pinBtn = _closest(t, "[data-action='thread-pin-message']");
  if (pinBtn) {
    const msgId = Number(pinBtn.dataset.messageId);
    if (msgId) {
      _closeOptionsSheet();
      import('./thread.events.js').then(({ handlePinMessage }) =>
        handlePinMessage(msgId).catch(() => showToast('Failed to pin message', 'error'))
      );
    }
    return;
  }

  // ── FE-06: Copy message text ──────────────────────────────────────────────
  const copyBtn = _closest(t, "[data-action='thread-copy-message']");
  if (copyBtn) {
    const msgId = Number(copyBtn.dataset.messageId);
    const msg   = threadState.messages.find((m) => m.id === msgId);
    if (msg?.text_content) {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(msg.text_content)
          .then(() => showToast('Copied to clipboard', 'success'))
          .catch(() => showToast('Copy not supported', 'error'));
      } else {
        const ta = document.createElement('textarea');
        ta.value = msg.text_content;
        ta.style.cssText = 'position:fixed;opacity:0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        showToast('Copied to clipboard', 'success');
      }
    }
    _closeOptionsSheet();
    return;
  }

  // ── React ─────────────────────────────────────────────────────────────────
  const emojiBtn = _closest(t, "[data-action='thread-react']");
  if (emojiBtn) {
    const msgId = Number(emojiBtn.dataset.messageId);
    const emoji  = emojiBtn.dataset.emoji;
    if (msgId && emoji) {
      _closeOptionsSheet();
      _closeEmojiPicker();
      import('./thread.events.js').then(({ handleReaction }) =>
        handleReaction(msgId, emoji).catch(() => showToast('Failed to add reaction', 'error'))
      );
    }
    return;
  }

  // ── Emoji picker ──────────────────────────────────────────────────────────
  const openEmojiBtn = _closest(t, "[data-action='thread-open-emoji-picker']");
  if (openEmojiBtn) {
    const msgId = Number(openEmojiBtn.dataset.messageId);
    if (msgId) _openEmojiPicker(msgId);
    return;
  }
  if (_closest(t, "[data-action='thread-close-emoji-picker']")) {
    _closeEmojiPicker();
    return;
  }

  // ── Reply ─────────────────────────────────────────────────────────────────
  const replyBtn = _closest(t, "[data-action='thread-reply']");
  if (replyBtn) {
    const msgId = Number(replyBtn.dataset.messageId);
    if (msgId) {
      _closeOptionsSheet();
      import('./thread.events.js').then(({ handleReply }) => handleReply(msgId));
    }
    return;
  }

  // ── Cancel reply ──────────────────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-cancel-reply']")) {
    import('./thread.events.js').then(({ handleCancelReply }) => handleCancelReply());
    return;
  }

  // ── Scroll to message ─────────────────────────────────────────────────────
  const scrollTarget = _closest(t, "[data-action='thread-scroll-to-message']");
  if (scrollTarget) {
    const msgId = Number(scrollTarget.dataset.messageId);
    if (msgId) {
      import('./thread.events.js').then(({ handleScrollToMessage }) =>
        handleScrollToMessage(msgId)
      );
    }
    return;
  }

  // ── Thread info ───────────────────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-info']")) {
    import('./thread.events.js').then(({ handleOpenInfo }) => handleOpenInfo());
    return;
  }

  // ── Pinned messages panel ─────────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-open-pinned-list']")) {
    e.preventDefault();
    import('./thread.events.js').then(({ handleOpenPinnedList }) => handleOpenPinnedList());
    return;
  }

  // ── Media / attachments viewer ────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-open-attachments']")) {
    e.preventDefault();
    import('./thread.events.js').then(({ handleOpenAttachments }) => handleOpenAttachments());
    return;
  }

  // ── Thread search open/close ──────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-search']")) {
    e.preventDefault();
    const panel = document.getElementById('thread-search-panel');
    if (panel) {
      panel.classList.remove('hidden');
      panel.classList.add('flex');
      setTimeout(() => document.getElementById('thread-search-input')?.focus(), 50);
    }
    return;
  }
  if (_closest(t, "[data-action='thread-close-search']")) {
    e.preventDefault();
    const panel = document.getElementById('thread-search-panel');
    if (panel) { panel.classList.add('hidden'); panel.classList.remove('flex'); }
    import('./thread.events.js').then(({ handleClearSearch }) => handleClearSearch());
    return;
  }

  // ── Thread avatar upload trigger ──────────────────────────────────────────
  if (_closest(t, "[data-action='thread-avatar-upload']")) {
    e.preventDefault();
    document.getElementById('thread-avatar-file-input')?.click();
    return;
  }

  // ── Join thread ───────────────────────────────────────────────────────────
  const joinBtn = _closest(t, "[data-action='thread-join']");
  if (joinBtn) {
    const threadId = Number(joinBtn.dataset.threadId);
    if (threadId) {
      import('./thread.events.js').then(({ handleJoinThread }) =>
        handleJoinThread(threadId).catch(() => showToast('Failed to join thread', 'error'))
      );
    }
    return;
  }

  // ── Leave thread ──────────────────────────────────────────────────────────
  const leaveBtn = _closest(t, "[data-action='thread-leave']");
  if (leaveBtn) {
    const threadId = Number(leaveBtn.dataset.threadId ?? threadState.activeThreadId);
    if (threadId) {
      import('./thread.events.js').then(({ handleLeaveThread }) =>
        handleLeaveThread(threadId).catch(() => showToast('Failed to leave thread', 'error'))
      );
    }
    return;
  }

  // ── FEAT-02: Close / Reopen thread ───────────────────────────────────────
  const closeThreadBtn = _closest(t, "[data-action='thread-close-thread']");
  if (closeThreadBtn) {
    const threadId = Number(closeThreadBtn.dataset.threadId ?? threadState.activeThreadId);
    const thread   = threadState.threadList.get(threadId);
    if (threadId) {
      if (!thread) { showToast('Thread state not found — please refresh.', 'error'); return; }
      const handler = thread.is_open ? 'handleCloseThread' : 'handleReopenThread';
      import('./thread.events.js').then((m) =>
        m[handler]?.(threadId).catch(() => showToast('Failed to update thread', 'error'))
      );
    }
    return;
  }

  // ── FEAT-02: Delete thread ────────────────────────────────────────────────
  const deleteThreadBtn = _closest(t, "[data-action='thread-delete-thread']");
  if (deleteThreadBtn) {
    const threadId = Number(deleteThreadBtn.dataset.threadId ?? threadState.activeThreadId);
    if (threadId) {
      import('./thread.events.js').then(({ handleDeleteThread }) =>
        handleDeleteThread(threadId).catch(() => showToast('Failed to delete thread', 'error'))
      );
    }
    return;
  }

  // ── Add members ──────────────────────────────────────────────────────────
  const addMembersBtn = _closest(t, "[data-action='thread-add-members']");
  if (addMembersBtn) {
    e.preventDefault();
    const threadId = Number(addMembersBtn.dataset.threadId ?? threadState.activeThreadId);
    if (threadId) {
      import('./thread.events.js').then(({ handleAddMembers }) =>
        handleAddMembers(threadId)
          .catch(() => showToast('Failed to open member picker', 'error'))
      );
    }
    return;
  }

  // ── Remove member ─────────────────────────────────────────────────────────
  const removeBtn = _closest(t, "[data-action='thread-remove-member']");
  if (removeBtn) {
    const userId   = Number(removeBtn.dataset.userId);
    const threadId = Number(removeBtn.dataset.threadId ?? threadState.activeThreadId);
    if (userId && threadId) {
      import('./thread.events.js').then(({ handleRemoveMember }) =>
        handleRemoveMember(threadId, userId)
          .catch(() => showToast('Failed to remove member', 'error'))
      );
    }
    return;
  }

  // ── Promote / demote ──────────────────────────────────────────────────────
  const promoteBtn = _closest(t, "[data-action='thread-promote-member']");
  if (promoteBtn) {
    const userId   = Number(promoteBtn.dataset.userId);
    const threadId = Number(promoteBtn.dataset.threadId ?? threadState.activeThreadId);
    if (userId && threadId) {
      import('./thread.events.js').then(({ handleChangeMemberRole }) =>
        handleChangeMemberRole(threadId, userId, 'moderator')
          .catch(() => showToast('Failed to promote member', 'error'))
      );
    }
    return;
  }
  const demoteBtn = _closest(t, "[data-action='thread-demote-member']");
  if (demoteBtn) {
    const userId   = Number(demoteBtn.dataset.userId);
    const threadId = Number(demoteBtn.dataset.threadId ?? threadState.activeThreadId);
    if (userId && threadId) {
      import('./thread.events.js').then(({ handleChangeMemberRole }) =>
        handleChangeMemberRole(threadId, userId, 'member')
          .catch(() => showToast('Failed to demote member', 'error'))
      );
    }
    return;
  }

  // ── Approve / reject join request ─────────────────────────────────────────
  const approveBtn = _closest(t, "[data-action='thread-approve-request']");
  if (approveBtn) {
    const threadId  = Number(approveBtn.dataset.threadId);
    const requestId = Number(approveBtn.dataset.requestId);
    if (threadId && requestId) {
      import('./thread.events.js').then(({ handleApproveRequest }) =>
        handleApproveRequest(threadId, requestId)
          .catch(() => showToast('Failed to approve request', 'error'))
      );
    }
    return;
  }
  const rejectBtn = _closest(t, "[data-action='thread-reject-request']");
  if (rejectBtn) {
    const threadId  = Number(rejectBtn.dataset.threadId);
    const requestId = Number(rejectBtn.dataset.requestId);
    if (threadId && requestId) {
      import('./thread.events.js').then(({ handleRejectRequest }) =>
        handleRejectRequest(threadId, requestId)
          .catch(() => showToast('Failed to reject request', 'error'))
      );
    }
    return;
  }

  // ── Accept / decline invite ───────────────────────────────────────────────
  const acceptInviteBtn = _closest(t, "[data-action='thread-accept-invite']");
  if (acceptInviteBtn) {
    const inviteId = Number(acceptInviteBtn.dataset.inviteId);
    if (inviteId) {
      import('./thread.events.js').then(({ handleAcceptInvite }) =>
        handleAcceptInvite(inviteId).catch(() => showToast('Failed to accept invite', 'error'))
      );
    }
    return;
  }
  const declineInviteBtn = _closest(t, "[data-action='thread-decline-invite']");
  if (declineInviteBtn) {
    const inviteId = Number(declineInviteBtn.dataset.inviteId);
    if (inviteId) {
      import('./thread.events.js').then(({ handleDeclineInvite }) =>
        handleDeclineInvite(inviteId).catch(() => showToast('Failed to decline invite', 'error'))
      );
    }
    return;
  }

  // ── Load more ─────────────────────────────────────────────────────────────
  if (_closest(t, "[data-action='thread-load-more']")) {
    import('./thread.events.js').then(({ handleLoadMoreMessages }) => handleLoadMoreMessages());
    return;
  }

  // ── Clear thread list search ──────────────────────────────────────────────
  if (_closest(t, "[data-action='clear-thread-list-search']")) {
    const input = document.getElementById('thread-list-search');
    if (input) {
      input.value = '';
      _filterThreadListInline('');
      input.focus();
      document.querySelector("[data-action='clear-thread-list-search']")?.classList.add('hidden');
    }
    return;
  }
}


// ─── Keyboard delegation ──────────────────────────────────────────────────────

function _onKeydown(e) {
  if (e.key === 'Escape') {
    const mentionBox = document.getElementById('thread-mention-suggestions');
    if (mentionBox && !mentionBox.classList.contains('hidden')) {
      _hideMentionSuggestions();
      return;
    }
    const sheet = document.getElementById('thread-message-options-sheet');
    if (sheet && !sheet.classList.contains('hidden')) { _closeOptionsSheet(); return; }
    const picker = document.getElementById('thread-reaction-picker');
    if (picker && !picker.classList.contains('hidden')) { _closeEmojiPicker(); return; }
    if (_replyIsActive()) {
      import('./thread.events.js').then(({ handleCancelReply }) => handleCancelReply());
      return;
    }
    const searchPanel = document.getElementById('thread-search-panel');
    if (searchPanel && !searchPanel.classList.contains('hidden')) {
      searchPanel.classList.add('hidden');
      searchPanel.classList.remove('flex');
      return;
    }
    if (threadState.activeThreadId) {
      import('./thread.events.js').then(({ handleBackToList }) => handleBackToList());
    }
    return;
  }

  if (e.key === 'Enter' && !e.shiftKey) {
    const active = document.activeElement;
    if (active?.matches('#thread-message-input, [data-role=\'thread-input\']')) {
      const box = document.getElementById('thread-mention-suggestions');
      if (box && !box.classList.contains('hidden')) {
        const first = box.querySelector('button');
        if (first) { first.click(); return; }
      }
      e.preventDefault();
      _hideMentionSuggestions();
      import('./thread.events.js').then(({ handleSendMessage }) =>
        handleSendMessage().catch(() => showToast('Failed to send message', 'error'))
      );
    }
  }
}


// ─── Form delegation ──────────────────────────────────────────────────────────

function _onSubmit(e) {
  if (e.target.matches("[data-form='thread-search']")) {
    e.preventDefault();
    const q = e.target.querySelector('input')?.value?.trim();
    import('./thread.events.js').then(({ handleThreadSearch }) => handleThreadSearch(q));
    return;
  }
  if (e.target.matches("[data-form='thread-create']")) {
    e.preventDefault();
    import('./thread.events.js').then(({ handleCreateThread }) => handleCreateThread(e.target));
    return;
  }
  if (e.target.matches("[data-form='thread-settings']")) {
    e.preventDefault();
    import('./thread.events.js').then(({ handleSaveSettings }) =>
      handleSaveSettings(e.target).catch(() => showToast('Failed to save settings', 'error'))
    );
  }
}


// ─── Issue 1: Attachment strip renderer with thumbnails ──────────────────────

function _renderAttachmentStrip(attachments) {
  const strip = document.getElementById('thread-attachment-strip');
  if (!strip) return;

  if (!attachments?.length) {
    strip.innerHTML = '';
    strip.classList.add('hidden');
    return;
  }

  strip.classList.remove('hidden');

  strip.innerHTML = attachments.map((att, idx) => {
    const isImage = att.type?.startsWith('image/');
    const sizeKb  = Math.round((att.size ?? 0) / 1024);

    const preview = isImage && att.previewUrl
      ? `<img src="${_escAttr(att.previewUrl)}"
              class="w-full h-full object-cover rounded-lg" alt="${_esc(att.name)}"
              loading="lazy">`
      : `<div class="flex flex-col items-center justify-center w-full h-full gap-1">
           <span class="text-2xl">${_fileEmoji(att.type)}</span>
           <span class="text-[9px] text-gray-500 font-medium text-center px-1 truncate w-full">
             ${_esc((att.name ?? '').split('.').pop()?.toUpperCase() ?? 'FILE')}
           </span>
         </div>`;

    return `
      <div class="relative flex-shrink-0 w-20 h-20 rounded-xl border-2 border-gray-200
                  bg-gray-50 overflow-hidden group/att cursor-default"
           data-attach-index="${idx}" title="${_esc(att.name)}">
        ${preview}
        <button data-action="thread-remove-attachment"
                data-attach-index="${idx}"
                class="absolute top-1 right-1 w-5 h-5 rounded-full bg-gray-900/70 text-white
                       flex items-center justify-center text-[10px] leading-none
                       opacity-0 group-hover/att:opacity-100 transition-opacity z-10"
                aria-label="Remove attachment">✕</button>
        <div class="absolute bottom-1 left-1 text-[9px] text-white bg-black/50
                    rounded px-1 leading-tight pointer-events-none">
          ${sizeKb}K
        </div>
      </div>`;
  }).join('');
}

function _fileEmoji(mimeType) {
  if (!mimeType) return '📎';
  if (mimeType.startsWith('video/'))                                  return '🎬';
  if (mimeType.includes('pdf'))                                       return '📄';
  if (mimeType.includes('word'))                                      return '📝';
  if (mimeType.includes('excel') || mimeType.includes('sheet'))      return '📊';
  if (mimeType.includes('presentation') || mimeType.includes('powerpoint')) return '📋';
  if (mimeType.startsWith('text/'))                                   return '📃';
  return '📎';
}


// ─── Message options bottom sheet ─────────────────────────────────────────────

function _openOptionsSheet(messageId) {
  const sheet = document.getElementById('thread-message-options-sheet');
  const panel = document.getElementById('thread-options-panel');
  const body  = document.getElementById('thread-msg-options-body');
  if (!sheet || !panel || !body) return;

  const msg = threadState.messages.find((m) => m.id === messageId);
  if (!msg) return;

  const isOwn      = msg.sender_id === threadState.currentUser?.id;
  const isPinned   = msg.is_pinned;
  const isDeleted  = msg.is_deleted;
  const hasText    = !isDeleted && msg.text_content && msg.text_content !== '[deleted]';
  const threadInfo = threadState.threadList.get(msg.thread_id ?? threadState.activeThreadId);
  const userRole   = threadInfo?.your_role ?? '';
  const canMod     = userRole === 'creator' || userRole === 'moderator';

  const btnCls = 'flex items-center gap-3 w-full px-5 py-4 text-sm transition-colors active:bg-gray-50';

  const standardRows = [
    !isDeleted ? `<button class="${btnCls} text-gray-800 hover:bg-gray-50"
      data-action="thread-reply" data-message-id="${messageId}">
      <span class="text-xl w-7 text-center">↩️</span><span class="font-medium">Reply</span>
    </button>` : '',

    !isDeleted ? `<button class="${btnCls} text-gray-800 hover:bg-gray-50"
      data-action="thread-open-emoji-picker" data-message-id="${messageId}">
      <span class="text-xl w-7 text-center">😊</span><span class="font-medium">React</span>
    </button>` : '',

    hasText ? `<button class="${btnCls} text-gray-800 hover:bg-gray-50"
      data-action="thread-copy-message" data-message-id="${messageId}">
      <span class="text-xl w-7 text-center">📋</span><span class="font-medium">Copy message</span>
    </button>` : '',

    isOwn && !isDeleted ? `<button class="${btnCls} text-gray-800 hover:bg-gray-50"
      data-action="thread-edit-message" data-message-id="${messageId}">
      <span class="text-xl w-7 text-center">✏️</span><span class="font-medium">Edit</span>
    </button>` : '',

    (canMod || isOwn) && !isDeleted ? `<button class="${btnCls} text-gray-800 hover:bg-gray-50"
      data-action="thread-pin-message" data-message-id="${messageId}">
      <span class="text-xl w-7 text-center">📌</span>
      <span class="font-medium">${isPinned ? 'Unpin' : 'Pin'}</span>
    </button>` : '',

    (isOwn || canMod) && !isDeleted ? `<button class="${btnCls} text-red-600 hover:bg-red-50"
      data-action="thread-delete-message" data-message-id="${messageId}">
      <span class="text-xl w-7 text-center">🗑️</span><span class="font-medium">Delete</span>
    </button>` : '',
  ].filter(Boolean);

  // AI actions section (shown for any non-deleted message with text)
  const aiRows = hasText ? `
    <div class="border-t border-gray-100 pt-1">
      <p class="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-5 pt-2 pb-1">
        AI Actions
      </p>
      <button class="${btnCls} text-violet-700 hover:bg-violet-50"
        data-action="thread-ai-action" data-message-id="${messageId}" data-ai-action="explain">
        <span class="text-xl w-7 text-center">💡</span><span class="font-medium">Explain</span>
      </button>
      <button class="${btnCls} text-violet-700 hover:bg-violet-50"
        data-action="thread-ai-action" data-message-id="${messageId}" data-ai-action="summarize">
        <span class="text-xl w-7 text-center">📝</span><span class="font-medium">Summarize</span>
      </button>
      <button class="${btnCls} text-violet-700 hover:bg-violet-50"
        data-action="thread-ai-action" data-message-id="${messageId}" data-ai-action="translate">
        <span class="text-xl w-7 text-center">🌍</span><span class="font-medium">Translate</span>
      </button>
      <button class="${btnCls} text-violet-700 hover:bg-violet-50"
        data-action="thread-ai-action" data-message-id="${messageId}" data-ai-action="to_code">
        <span class="text-xl w-7 text-center">💻</span><span class="font-medium">Convert to Code</span>
      </button>
      ${!isOwn && !msg.is_ai_response ? `
      <button class="${btnCls} text-blue-600 hover:bg-blue-50"
        data-action="thread-ai-action" data-message-id="${messageId}" data-ai-action="fact_check">
        <span class="text-xl w-7 text-center">🔍</span><span class="font-medium">Fact Check</span>
      </button>` : ''}
    </div>` : '';

  body.innerHTML = `<div class="py-1 divide-y divide-gray-100">
    <div>${standardRows.join('')}</div>
    ${aiRows}
  </div>`;

  sheet.classList.remove('hidden');
  requestAnimationFrame(() => {
    requestAnimationFrame(() => { panel.classList.remove('translate-y-full'); });
  });
}

function _closeOptionsSheet() {
  const sheet = document.getElementById('thread-message-options-sheet');
  const panel = document.getElementById('thread-options-panel');
  if (!sheet || !panel) return;
  panel.classList.add('translate-y-full');
  setTimeout(() => sheet.classList.add('hidden'), 260);
}


// ─── Inline edit ─────────────────────────────────────────────────────────────

function _startInlineEdit(messageId) {
  const wrap = document.querySelector(`[data-message-id="${messageId}"]`);
  if (!wrap) return;
  const textEl = wrap.querySelector('.msg-text');
  if (!textEl) return;
  const current = textEl.textContent ?? '';
  textEl.style.display = 'none';

  textEl.insertAdjacentHTML('afterend', `
    <div class="inline-edit-wrap mt-1">
      <textarea class="inline-edit-textarea w-full text-sm bg-white/20 text-inherit
                       rounded-xl border border-white/40 px-3 py-2 resize-none outline-none
                       focus:ring-2 focus:ring-white/50 leading-relaxed"
                rows="2" maxlength="5000">${_esc(current)}</textarea>
      <div class="flex gap-2 mt-1.5 justify-end">
        <button data-action="thread-cancel-edit" data-message-id="${messageId}"
                class="text-xs px-2.5 py-1 rounded-lg bg-white/20 hover:bg-white/30
                       transition-colors font-medium">Cancel</button>
        <button data-action="thread-save-edit" data-message-id="${messageId}"
                class="text-xs px-2.5 py-1 rounded-lg bg-white hover:bg-gray-100
                       text-indigo-700 transition-colors font-semibold">Save</button>
      </div>
    </div>`);

  const textarea = wrap.querySelector('.inline-edit-textarea');
  if (textarea) {
    textarea.focus();
    textarea.selectionStart = textarea.value.length;
  }
}

function _cancelInlineEdit(messageId) {
  const wrap = document.querySelector(`[data-message-id="${messageId}"]`);
  if (!wrap) return;
  wrap.querySelector('.inline-edit-wrap')?.remove();
  const textEl = wrap.querySelector('.msg-text');
  if (textEl) textEl.style.display = '';
}


// ─── Emoji picker ─────────────────────────────────────────────────────────────

const QUICK_EMOJIS = ['👍','❤️','😂','😮','😢','🔥','👏','🎉','🤔','✅','😍','💯'];

function _openEmojiPicker(messageId) {
  _closeOptionsSheet();

  let picker = document.getElementById('thread-reaction-picker');
  if (!picker) {
    picker    = document.createElement('div');
    picker.id = 'thread-reaction-picker';
    document.body.appendChild(picker);
  }

  picker.className = 'fixed bottom-0 left-0 right-0 z-[60] bg-white rounded-t-2xl shadow-2xl px-4 py-4';
  picker.innerHTML = `
    <div class="flex justify-between items-center mb-3">
      <span class="text-sm font-semibold text-gray-700">React</span>
      <button data-action="thread-close-emoji-picker"
              class="w-7 h-7 rounded-full bg-gray-100 text-gray-500 hover:bg-gray-200
                     flex items-center justify-center text-xs transition-colors">✕</button>
    </div>
    <div class="grid grid-cols-6 gap-2">
      ${QUICK_EMOJIS.map((em) => `
        <button class="flex items-center justify-center h-11 text-2xl rounded-xl
                       bg-gray-50 hover:bg-indigo-50 active:scale-90 transition-all"
                data-action="thread-react"
                data-message-id="${messageId}"
                data-emoji="${em}">${em}</button>`).join('')}
    </div>
    <div class="pb-2"></div>`;
  picker.classList.remove('hidden');
}

function _closeEmojiPicker() {
  document.getElementById('thread-reaction-picker')?.classList.add('hidden');
}


// ─── Helpers ─────────────────────────────────────────────────────────────────

function _replyIsActive() {
  const preview = document.getElementById('thread-reply-preview');
  return preview && !preview.classList.contains('hidden') && preview.innerHTML.trim() !== '';
}

function _esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _escAttr(str) {
  return String(str ?? '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showToast(message, type = 'info') {
  if (typeof window.showToast === 'function') window.showToast(message, type);
}
