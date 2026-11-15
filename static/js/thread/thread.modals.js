/**
 * thread.modals.js — Tailwind edition
 *
 * CHANGES:
 *  - Issue 4: openEditThreadModal() — inline thread editing.
 *  - Issue 4: openInfoModal() creator section includes ✏️ Edit Thread button.
 *  - NEW: openMeetingNotesRangeModal() — range picker for AI meeting notes.
 *  - NEW: openMeetingNotesModal() — renders structured AI meeting notes.
 *  - HIDDEN-04 fix: backdrop listener attached once at creation.
 *  - FEAT-02 buttons retained.
 */

import { threadState } from './thread.state.js';


// ─── Utilities ────────────────────────────────────────────────────────────────

function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function escAttr(str) {
  return String(str ?? '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _formatDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}

const _listenersAttached = new Set();

function _openModal(id, html) {
  let modal = document.getElementById(id);
  if (!modal) {
    modal = document.createElement('div');
    modal.id        = id;
    modal.className =
      'hidden fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm px-4';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    document.body.appendChild(modal);
  }

  modal.innerHTML = html;
  modal.classList.remove('hidden');

  if (!_listenersAttached.has(id)) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closeModal(id);
    });
    _listenersAttached.add(id);
  }

  return modal;
}

function _closeModal(id) {
  document.getElementById(id)?.classList.add('hidden');
}

function _closeBtn(modalId) {
  return `
    <button class="absolute top-4 right-4 w-8 h-8 rounded-full flex items-center justify-center
                   text-ink-tertiary hover:text-ink-secondary hover:bg-surface-raised transition-colors"
            data-action="close-modal"
            aria-label="Close">
      <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
      </svg>
    </button>`;
}

function _btnPrimary(label, attrs = '') {
  return `<button ${attrs}
            class="flex-1 py-2.5 rounded-xl bg-accent hover:bg-accent-hover active:scale-95
                   text-sm font-semibold text-white transition-all duration-150 shadow-sm">
            ${label}
          </button>`;
}

function _btnSecondary(label, attrs = '') {
  return `<button ${attrs}
            class="flex-1 py-2.5 rounded-xl border border-line text-sm font-semibold
                   text-ink-secondary hover:bg-surface-raised active:bg-surface-raised transition-colors">
            ${label}
          </button>`;
}

function _inputClass() {
  return (
    'w-full px-4 py-2.5 rounded-xl border border-line bg-surface-raised ' +
    'focus:bg-surface-card focus:border-accent-border focus:ring-2 focus:ring-accent-border ' +
    'outline-none text-sm text-ink-primary placeholder-ink-tertiary transition-all'
  );
}


// ─── Member row ───────────────────────────────────────────────────────────────

function _memberRowHtml(member, isPrivileged, isCreator, threadId) {
  const userId = member.user_id ?? member.id;

  const avatarHtml = member.avatar
    ? `<img src="${escAttr(member.avatar)}"
            class="w-10 h-10 rounded-full object-cover" loading="lazy" alt="${esc(member.name)}">`
    : `<div class="w-10 h-10 rounded-full bg-accent-subtle text-accent text-sm font-bold
                   flex items-center justify-center select-none">
         ${esc((member.name ?? '?').charAt(0).toUpperCase())}
       </div>`;

  const roleBadge = member.role === 'creator'
    ? `<span class="text-[10px] font-semibold text-accent bg-accent-subtle rounded-full px-2 py-0.5">Creator</span>`
    : member.role === 'moderator'
    ? `<span class="text-[10px] font-semibold text-violet-300 bg-violet-500/15 rounded-full px-2 py-0.5">Mod</span>`
    : '';

  const onlineDot = member.online
    ? `<span class="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-emerald-400 ring-2 ring-surface-card" title="Online"></span>`
    : `<span class="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-surface-hover ring-2 ring-surface-card" title="Offline"></span>`;

  let actionBtns = '';
  if (isPrivileged && member.role !== 'creator') {
    const tid = threadId ?? threadState.activeThreadId;
    if (isCreator) {
      if (member.role === 'moderator') {
        actionBtns += `
          <button class="text-xs text-violet-300 hover:bg-violet-500/10 rounded-lg px-2 py-1 transition-colors font-medium"
                  data-action="thread-demote-member" data-user-id="${userId}" data-thread-id="${tid}">
            Remove Mod
          </button>`;
      } else {
        actionBtns += `
          <button class="text-xs text-accent hover:bg-accent-subtle rounded-lg px-2 py-1 transition-colors font-medium"
                  data-action="thread-promote-member" data-user-id="${userId}" data-thread-id="${tid}">
            Make Mod
          </button>`;
      }
    }
    actionBtns += `
      <button class="text-xs text-red-400 hover:bg-red-500/10 rounded-lg px-2 py-1 transition-colors font-medium"
              data-action="thread-remove-member" data-user-id="${userId}" data-thread-id="${tid}">
        Remove
      </button>`;
  }

  return `
    <div class="flex items-center justify-between gap-3 py-2.5 px-1 border-b border-line-light last:border-0"
         data-user-id="${userId}">
      <div class="flex items-center gap-3 min-w-0">
        <div class="relative flex-shrink-0">
          ${avatarHtml}
          ${onlineDot}
        </div>
        <div class="min-w-0">
          <span class="block text-sm font-semibold text-ink-primary truncate">
            ${esc(member.name ?? member.username ?? 'User')}
          </span>
          <span class="block text-xs text-ink-tertiary truncate">@${esc(member.username ?? '')}</span>
        </div>
      </div>
      <div class="flex items-center gap-1.5 flex-shrink-0">
        ${roleBadge}
        <span class="text-[11px] text-ink-tertiary">${member.messages_sent ?? 0} msgs</span>
        ${actionBtns}
      </div>
    </div>`;
}


// ─── Info modal ───────────────────────────────────────────────────────────────

export function openInfoModal(thread, members, user_status = {}) {
  const currentUserId = threadState.currentUser?.id;

  const currentRole =
    user_status.your_role ??
    members.find((m) => (m.user_id ?? m.id) === currentUserId)?.role ??
    'member';

  const isCreator =
    user_status.is_creator ??
    (thread.creator?.id != null && thread.creator.id === currentUserId) ??
    (thread.creator_id === currentUserId);

  const isModerator  = currentRole === 'moderator';
  const isPrivileged = isCreator || isModerator;
  const threadId     = thread.id;

  const avatarHtml = thread.avatar
    ? `<img src="${escAttr(thread.avatar)}"
            class="w-20 h-20 rounded-full object-cover ring-2 ring-accent-border"
            alt="${esc(thread.title)}">`
    : `<div class="w-20 h-20 rounded-full bg-accent-subtle text-accent text-2xl font-bold
                   flex items-center justify-center select-none">
         ${esc(thread.title.charAt(0).toUpperCase())}
       </div>`;

  const avatarUploadBtn = isCreator
    ? `<button class="absolute -bottom-1 -right-1 w-7 h-7 rounded-full bg-accent text-white
                       flex items-center justify-center shadow-sm hover:bg-accent-hover transition-colors"
               data-action="thread-avatar-upload" title="Change avatar">
         <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
           <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
           <polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
         </svg>
       </button>
       <input type="file" id="thread-avatar-file-input" class="hidden" accept="image/*">`
    : '';

  const deptHtml = thread.department
    ? `<span class="inline-flex items-center text-xs font-semibold text-accent
                    bg-accent-subtle rounded-full px-2.5 py-0.5">
         ${esc(thread.department)}
       </span>`
    : '';

  const tagsHtml = (thread.tags ?? []).length
    ? thread.tags.map((t) =>
        `<span class="text-xs text-ink-secondary bg-surface-raised rounded-full px-2 py-0.5">${esc(t)}</span>`
      ).join('')
    : '';

  const membersHtml = members.length
    ? members.map((m) => _memberRowHtml(m, isPrivileged, isCreator, threadId)).join('')
    : `<p class="text-sm text-ink-tertiary py-4 text-center">No members found.</p>`;

  const privilegedControls = isCreator
    ? `<div class="pt-3 border-t border-line-light space-y-3">
         <h4 class="text-xs font-bold text-ink-secondary uppercase tracking-widest mb-3">Settings</h4>
         <label class="flex items-center justify-between mb-2 cursor-pointer">
           <span class="text-sm text-ink-secondary">Requires Approval</span>
           <input type="checkbox"
                  id="thread-info-requires-approval"
                  data-action="thread-toggle-approval"
                  data-thread-id="${threadId}"
                  ${thread.requires_approval ? 'checked' : ''}
                  class="w-4 h-4 rounded accent-[#818CF8] cursor-pointer">
         </label>
         <div class="flex flex-wrap gap-2">
           <button data-action="thread-add-members" data-thread-id="${threadId}"
                   class="flex items-center gap-1.5 text-sm font-medium text-emerald-400
                          bg-emerald-500/10 hover:bg-emerald-500/15 rounded-xl px-3 py-2 transition-colors">
             ➕ Add Members
           </button>
           <button data-action="thread-edit-settings" data-thread-id="${threadId}"
                   class="flex items-center gap-1.5 text-sm font-medium text-accent
                          bg-accent-subtle hover:bg-accent-subtle rounded-xl px-3 py-2 transition-colors">
             ✏️ Edit Thread
           </button>
           <button data-action="thread-open-attachments"
                   class="flex items-center gap-1.5 text-sm font-medium text-ink-secondary
                          bg-surface-raised hover:bg-surface-hover rounded-xl px-3 py-2 transition-colors">
             📎 Media &amp; Files
           </button>
           <button data-action="thread-open-pinned-list"
                   class="flex items-center gap-1.5 text-sm font-medium text-ink-secondary
                          bg-surface-raised hover:bg-surface-hover rounded-xl px-3 py-2 transition-colors">
             📌 All Pins
           </button>
         </div>
         <div class="flex flex-wrap gap-2 pt-1">
           <button data-action="thread-close-thread" data-thread-id="${threadId}"
                   class="flex items-center gap-1.5 text-sm font-medium
                          ${thread.is_open
                            ? 'text-amber-400 bg-amber-500/10 hover:bg-amber-500/15'
                            : 'text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/15'}
                          rounded-xl px-3 py-2 transition-colors">
             ${thread.is_open ? '🔒 Close Thread' : '🔓 Reopen Thread'}
           </button>
           <button data-action="thread-delete-thread" data-thread-id="${threadId}"
                   class="flex items-center gap-1.5 text-sm font-medium text-red-400
                          bg-red-500/10 hover:bg-red-500/15 rounded-xl px-3 py-2 transition-colors">
             🗑 Delete Thread
           </button>
         </div>
       </div>`
    : isModerator
    ? `<div class="pt-3 border-t border-line-light space-y-3">
         <h4 class="text-xs font-bold text-ink-secondary uppercase tracking-widest mb-3">Moderator</h4>
         <div class="flex flex-wrap gap-2">
           <button data-action="thread-add-members" data-thread-id="${threadId}"
                   class="flex items-center gap-1.5 text-sm font-medium text-emerald-400
                          bg-emerald-500/10 hover:bg-emerald-500/15 rounded-xl px-3 py-2 transition-colors">
             ➕ Add Members
           </button>
           <button data-action="thread-open-attachments"
                   class="flex items-center gap-1.5 text-sm font-medium text-ink-secondary
                          bg-surface-raised hover:bg-surface-hover rounded-xl px-3 py-2 transition-colors">
             📎 Media &amp; Files
           </button>
           <button data-action="thread-open-pinned-list"
                   class="flex items-center gap-1.5 text-sm font-medium text-ink-secondary
                          bg-surface-raised hover:bg-surface-hover rounded-xl px-3 py-2 transition-colors">
             📌 All Pins
           </button>
           <button data-action="thread-leave" data-thread-id="${threadId}"
                   class="flex items-center gap-1.5 text-sm font-medium text-red-400
                          bg-red-500/10 hover:bg-red-500/15 rounded-xl px-3 py-2 transition-colors">
             Leave Thread
           </button>
         </div>
       </div>`
    : `<div class="pt-3 border-t border-line-light flex flex-wrap gap-2">
         <button data-action="thread-open-pinned-list"
                 class="flex items-center gap-1.5 text-sm font-medium text-ink-secondary
                        bg-surface-raised hover:bg-surface-hover rounded-xl px-3 py-2 transition-colors">
           📌 Pinned Messages
         </button>
         <button data-action="thread-open-attachments"
                 class="flex items-center gap-1.5 text-sm font-medium text-ink-secondary
                        bg-surface-raised hover:bg-surface-hover rounded-xl px-3 py-2 transition-colors">
           📎 Media &amp; Files
         </button>
         <button data-action="thread-leave" data-thread-id="${threadId}"
                 class="flex items-center gap-1.5 text-sm font-medium text-red-400
                        bg-red-500/10 hover:bg-red-500/15 rounded-xl px-3 py-2 transition-colors">
           Leave Thread
         </button>
       </div>`;

  const html = `
    <div class="relative bg-surface-card rounded-2xl w-full max-w-md shadow-2xl
                max-h-[85vh] flex flex-col overflow-hidden">
      ${_closeBtn('thread-info-modal')}
      <div class="flex flex-col items-center gap-2 px-5 pt-7 pb-5 text-center border-b border-line-light flex-shrink-0">
        <div class="relative">
          ${avatarHtml}
          ${avatarUploadBtn}
        </div>
        <h2 class="text-lg font-bold text-ink-primary mt-1">${esc(thread.title)}</h2>
        ${deptHtml}
        ${tagsHtml ? `<div class="flex flex-wrap justify-center gap-1.5">${tagsHtml}</div>` : ''}
        ${thread.description
          ? `<p class="text-sm text-ink-secondary leading-relaxed max-w-xs">${esc(thread.description)}</p>`
          : ''}
        <p class="text-xs text-ink-tertiary">
          Created ${_formatDate(thread.created_at)}
          · ${thread.member_count} / ${thread.max_members} members
          ${thread.is_open ? '' : `· <span class="text-red-400">Closed</span>`}
        </p>
      </div>
      <div class="flex-1 overflow-y-auto px-5 py-4">
        <h4 class="text-xs font-bold text-ink-secondary uppercase tracking-widest mb-3">
          Members (${members.length})
        </h4>
        <div>${membersHtml}</div>
      </div>
      <div class="flex-shrink-0 px-5 pb-6 pt-3">${privilegedControls}</div>
    </div>`;

  _openModal('thread-info-modal', html);
}


// ─── Edit thread modal ────────────────────────────────────────────────────────

export function openEditThreadModal(thread) {
  const html = `
    <div class="relative bg-surface-card rounded-2xl w-full max-w-md shadow-2xl">
      ${_closeBtn('thread-edit-modal')}
      <div class="flex items-center justify-between px-5 pt-6 pb-4 border-b border-line-light">
        <h3 class="text-base font-bold text-ink-primary">Edit Thread</h3>
      </div>
      <div class="px-5 py-4 space-y-4">
        <div>
          <label class="block text-xs font-semibold text-ink-secondary mb-1.5">
            Thread name <span class="text-red-400">*</span>
          </label>
          <input id="edit-thread-title" type="text" maxlength="100"
                 value="${escAttr(thread.title ?? '')}" class="${_inputClass()}">
        </div>
        <div>
          <label class="block text-xs font-semibold text-ink-secondary mb-1.5">Description</label>
          <textarea id="edit-thread-description" rows="2" maxlength="500"
                    class="${_inputClass()} resize-none">${esc(thread.description ?? '')}</textarea>
        </div>
        <div>
          <label class="block text-xs font-semibold text-ink-secondary mb-1.5">
            Max members
            <span class="text-ink-tertiary font-normal">(current: ${thread.member_count ?? 1})</span>
          </label>
          <input id="edit-thread-max-members" type="number"
                 min="${thread.member_count ?? 1}" max="50"
                 value="${thread.max_members ?? 10}" class="${_inputClass()}">
        </div>
        <div>
          <label class="block text-xs font-semibold text-ink-secondary mb-1.5">
            Tags <span class="text-ink-tertiary font-normal">(comma-separated)</span>
          </label>
          <input id="edit-thread-tags" type="text" maxlength="200"
                 value="${escAttr((thread.tags ?? []).join(', '))}" class="${_inputClass()}">
        </div>
        <label class="flex items-center justify-between cursor-pointer">
          <span class="text-sm text-ink-secondary">Requires Approval</span>
          <input id="edit-thread-requires-approval" type="checkbox"
                 ${thread.requires_approval ? 'checked' : ''}
                 class="w-4 h-4 rounded accent-[#818CF8] cursor-pointer">
        </label>
      </div>
      <div class="flex gap-3 px-5 pb-5">
        ${_btnSecondary('Cancel',
          `data-action="close-modal"`)}
        ${_btnPrimary('Save Changes', `id="edit-thread-save-btn"`)}
      </div>
    </div>`;

  const modal = _openModal('thread-edit-modal', html);

  modal.querySelector('#edit-thread-save-btn')?.addEventListener('click', () => {
    const title             = modal.querySelector('#edit-thread-title')?.value?.trim();
    const description       = modal.querySelector('#edit-thread-description')?.value?.trim() ?? '';
    const max_members       = Number(modal.querySelector('#edit-thread-max-members')?.value);
    const tags              = (modal.querySelector('#edit-thread-tags')?.value ?? '')
                               .split(',').map((s) => s.trim()).filter(Boolean);
    const requires_approval = modal.querySelector('#edit-thread-requires-approval')?.checked ?? true;

    if (!title || title.length < 5) {
      window.showToast?.('Thread name must be at least 5 characters', 'error');
      return;
    }

    const minMembers = thread.member_count ?? 1;
    if (max_members < minMembers || max_members > 50) {
      window.showToast?.(`Max members must be between ${minMembers} and 50`, 'error');
      return;
    }

    _closeModal('thread-edit-modal');
    document.dispatchEvent(new CustomEvent('thread:save-edit', {
      detail: { threadId: thread.id, title, description, max_members, tags, requires_approval },
      bubbles: false,
    }));
  });
}


// ─── Meeting Notes: range picker ──────────────────────────────────────────────

/**
 * Show a compact modal asking the user which message window to analyse.
 * @param {(range: number) => void} onGenerate  — called with 50 | 100 | 500
 */
export function openMeetingNotesRangeModal(onGenerate) {
  const html = `
    <div class="relative bg-surface-card rounded-2xl w-full max-w-xs shadow-2xl">
      ${_closeBtn('thread-notes-range-modal')}
      <div class="px-5 pt-6 pb-4 border-b border-line-light">
        <h3 class="text-base font-bold text-ink-primary">📋 Meeting Notes</h3>
        <p class="text-xs text-ink-secondary mt-1">How many recent messages to analyse?</p>
      </div>
      <div class="px-5 py-4 space-y-1">
        ${[50, 100, 500].map((n, i) => `
          <label class="flex items-center gap-3 cursor-pointer p-2.5 rounded-xl
                        hover:bg-surface-raised transition-colors">
            <input type="radio" name="notes-range" value="${n}"
                   class="accent-emerald-600 w-4 h-4 cursor-pointer"
                   ${i === 0 ? 'checked' : ''}>
            <span class="text-sm text-ink-secondary">
              Last <strong>${n}</strong> messages
            </span>
          </label>`).join('')}
      </div>
      <div class="flex gap-3 px-5 pb-5">
        ${_btnSecondary('Cancel',
          `data-action="close-modal"`)}
        ${_btnPrimary('Generate', `id="notes-range-confirm-btn"`)}
      </div>
    </div>`;

  const modal = _openModal('thread-notes-range-modal', html);
  modal.querySelector('#notes-range-confirm-btn')?.addEventListener('click', () => {
    const range = parseInt(
      modal.querySelector('input[name="notes-range"]:checked')?.value ?? '50'
    );
    _closeModal('thread-notes-range-modal');
    onGenerate(range);
  });
}


// ─── Meeting Notes: results viewer ───────────────────────────────────────────

/**
 * Display AI-generated meeting notes.
 * @param {{ topics_discussed, decisions_made, action_items, open_questions, summary }} notes
 * @param {number|null} messageCount
 */
export function openMeetingNotesModal(notes, messageCount) {
  const {
    topics_discussed = [],
    decisions_made   = [],
    action_items     = [],
    open_questions   = [],
    summary          = '',
  } = notes ?? {};

  const _section = (title, emoji, items, dotColor) => {
    if (!items.length) return '';
    return `
      <div class="mb-4">
        <h4 class="text-xs font-bold text-ink-secondary uppercase tracking-wide mb-2">
          ${emoji} ${esc(title)}
        </h4>
        <ul class="space-y-1.5">
          ${items.map((item) => `
            <li class="flex items-start gap-2 text-sm text-ink-secondary">
              <span class="w-1.5 h-1.5 rounded-full ${dotColor} flex-shrink-0 mt-2"></span>
              <span>${esc(item)}</span>
            </li>`).join('')}
        </ul>
      </div>`;
  };

  const hasContent = topics_discussed.length || decisions_made.length
                  || action_items.length    || open_questions.length;

  const html = `
    <div class="relative bg-surface-card rounded-2xl w-full max-w-md shadow-2xl
                max-h-[85vh] flex flex-col overflow-hidden">
      ${_closeBtn('thread-meeting-notes-modal')}
      <div class="px-5 pt-6 pb-4 border-b border-line-light flex-shrink-0">
        <h3 class="text-base font-bold text-ink-primary">📋 Meeting Notes</h3>
        ${messageCount
          ? `<p class="text-xs text-ink-tertiary mt-0.5">Based on ${messageCount} messages</p>`
          : ''}
      </div>
      ${summary ? `
        <div class="px-5 py-3 bg-emerald-500/10 border-b border-emerald-500/20 flex-shrink-0">
          <p class="text-sm text-emerald-300 leading-relaxed">${esc(summary)}</p>
        </div>` : ''}
      <div class="flex-1 overflow-y-auto px-5 py-4">
        ${_section('Topics Discussed', '💬', topics_discussed, 'bg-blue-400')}
        ${_section('Decisions Made',   '✅', decisions_made,   'bg-emerald-400')}
        ${_section('Action Items',     '📌', action_items,     'bg-amber-400')}
        ${_section('Open Questions',   '❓', open_questions,   'bg-red-400')}
        ${!hasContent
          ? `<p class="text-sm text-ink-tertiary text-center py-6">
               No structured content found in this conversation.
             </p>`
          : ''}
      </div>
      <div class="flex-shrink-0 px-5 pb-5 pt-3">
        ${_btnSecondary('Close',
          `data-action="close-modal"`)}
      </div>
    </div>`;

  _openModal('thread-meeting-notes-modal', html);
}


// ─── Pinned messages panel ────────────────────────────────────────────────────

export function openPinnedMessagesPanel(pinnedMessages) {
  const count = pinnedMessages?.length ?? 0;

  const itemsHtml = !count
    ? `<div class="flex flex-col items-center gap-2 py-12">
         <span class="text-3xl">📌</span>
         <p class="text-sm text-ink-tertiary">No pinned messages yet.</p>
       </div>`
    : pinnedMessages.map((msg) => {
        const senderName = esc(msg.sender?.name ?? 'Unknown');
        const text       = esc((msg.text_content ?? '📎 Attachment').slice(0, 120));
        const time       = msg.sent_at
          ? new Date(msg.sent_at).toLocaleDateString([], { month: 'short', day: 'numeric' })
          : '';
        const pinnedBy   = msg.pinned_by?.name
          ? `<span class="text-[10px] text-accent block mt-0.5">
               Pinned by ${esc(msg.pinned_by.name)}
             </span>`
          : '';
        return `
          <div class="px-5 py-3.5 hover:bg-surface-hover cursor-pointer border-b border-line-light last:border-0"
               data-action="thread-scroll-to-message" data-message-id="${msg.id}"
               role="button" tabindex="0">
            <div class="flex items-center justify-between mb-1">
              <span class="text-xs font-semibold text-ink-secondary">${senderName}</span>
              <span class="text-[11px] text-ink-tertiary">${time}</span>
            </div>
            <p class="text-sm text-ink-secondary leading-snug">${text}</p>
            ${pinnedBy}
          </div>`;
      }).join('');

  const html = `
    <div class="relative bg-surface-card rounded-2xl w-full max-w-sm shadow-2xl
                max-h-[80vh] flex flex-col overflow-hidden">
      ${_closeBtn('thread-pinned-panel')}
      <div class="px-5 pt-5 pb-4 border-b border-line-light flex-shrink-0">
        <h3 class="text-base font-bold text-ink-primary">
          📌 Pinned Messages
          <span class="ml-1 text-sm font-normal text-ink-tertiary">(${count})</span>
        </h3>
      </div>
      <div class="flex-1 overflow-y-auto">${itemsHtml}</div>
    </div>`;

  _openModal('thread-pinned-panel', html);
}


// ─── Join request modal ───────────────────────────────────────────────────────

export function openJoinRequestModal(thread, onConfirm) {
  const html = `
    <div class="relative bg-surface-card rounded-2xl w-full max-w-sm shadow-2xl">
      ${_closeBtn('thread-join-modal')}
      <div class="px-5 pt-6 pb-4 border-b border-line-light">
        <h3 class="text-base font-bold text-ink-primary mb-1">Join "${esc(thread.title)}"</h3>
        <p class="text-xs text-ink-tertiary">
          ${thread.member_count} / ${thread.max_members} members
          ${thread.requires_approval ? '· Requires approval' : '· Open join'}
        </p>
        ${thread.description
          ? `<p class="text-sm text-ink-secondary mt-2 leading-relaxed">
               ${esc(thread.description.slice(0, 160))}
             </p>`
          : ''}
      </div>
      <div class="px-5 py-4 space-y-3">
        ${thread.requires_approval ? `
          <div>
            <label class="block text-xs font-semibold text-ink-secondary mb-1.5">
              Introduction <span class="text-ink-tertiary font-normal">(optional)</span>
            </label>
            <textarea id="join-request-message"
                      class="${_inputClass()} resize-none"
                      placeholder="Tell the creator why you want to join…"
                      maxlength="300" rows="3"></textarea>
          </div>` : ''}
        <div class="flex gap-3 pt-1">
          ${_btnSecondary('Cancel',
            `data-action="close-modal"`)}
          ${_btnPrimary(
            thread.requires_approval ? 'Send Request' : 'Join Now',
            `id="join-modal-confirm-btn"`
          )}
        </div>
      </div>
    </div>`;

  const modal = _openModal('thread-join-modal', html);
  modal.querySelector('#join-modal-confirm-btn')?.addEventListener('click', () => {
    const message = modal.querySelector('#join-request-message')?.value?.trim() ?? '';
    _closeModal('thread-join-modal');
    onConfirm(message);
  });
}


// ─── Invite modal ─────────────────────────────────────────────────────────────

export function openInviteModal(invite, onAccept, onDecline) {
  const thread  = invite.thread ?? {};
  const inviter = invite.invited_by;

  const html = `
    <div class="relative bg-surface-card rounded-2xl w-full max-w-sm shadow-2xl">
      ${_closeBtn('thread-invite-modal')}
      <div class="px-5 pt-6 pb-4 border-b border-line-light">
        <h3 class="text-base font-bold text-ink-primary mb-1">Thread Invitation</h3>
        <p class="text-sm text-ink-secondary">
          ${inviter
            ? `<strong class="text-ink-primary">${esc(inviter.name)}</strong> invited you to:`
            : "You've been invited to:"}
        </p>
      </div>
      <div class="px-5 py-4 space-y-3">
        <div class="bg-accent-subtle rounded-xl p-3.5">
          <p class="font-bold text-ink-primary text-sm">${esc(thread.title ?? '')}</p>
          ${thread.description
            ? `<p class="text-xs text-ink-secondary mt-1 leading-snug">
                 ${esc(thread.description.slice(0, 120))}
               </p>`
            : ''}
          <p class="text-xs text-ink-tertiary mt-1.5">
            ${thread.member_count ?? 0} / ${thread.max_members ?? '?'} members
            ${thread.department ? `· ${esc(thread.department)}` : ''}
          </p>
        </div>
        ${invite.message && !invite.message.startsWith('[')
          ? `<p class="text-sm text-ink-secondary italic border-l-2 border-accent-border pl-3 py-1">
               "${esc(invite.message)}"
             </p>`
          : ''}
        <div class="flex gap-3 pt-1">
          ${_btnSecondary('Decline', `id="invite-decline-btn"`)}
          ${_btnPrimary('Accept Invite', `id="invite-accept-btn"`)}
        </div>
      </div>
    </div>`;

  const modal = _openModal('thread-invite-modal', html);
  modal.querySelector('#invite-accept-btn')?.addEventListener('click', () => {
    _closeModal('thread-invite-modal'); onAccept();
  });
  modal.querySelector('#invite-decline-btn')?.addEventListener('click', () => {
    _closeModal('thread-invite-modal'); onDecline();
  });
}


// ─── Add Members modal ────────────────────────────────────────────────────────
//
// Opens a searchable connection picker so the creator/moderator can select
// one or more connections to directly add as thread members.
// Already-members are pre-filtered out. Selection is confirmed in one tap.
//
// @param {object}   thread       – current thread object (id, title, member_count, max_members)
// @param {number[]} memberIds    – IDs of users already in the thread (to exclude)
// @param {Function} onConfirm    – called with the array of selected user IDs
// ─────────────────────────────────────────────────────────────────────────────

export async function openAddMembersModal(thread, memberIds, onConfirm) {
  const MODAL_ID = 'thread-add-members-modal';
  const slots    = (thread.max_members ?? 50) - (thread.member_count ?? 0);

  // Show a loading skeleton while we fetch connections
  _openModal(MODAL_ID, `
    <div class="relative bg-surface-card rounded-2xl w-full max-w-md shadow-2xl max-h-[85vh]
                flex flex-col overflow-hidden">
      ${_closeBtn(MODAL_ID)}
      <div class="px-5 pt-6 pb-4 border-b border-line-light flex-shrink-0">
        <h3 class="text-base font-bold text-ink-primary">➕ Add Members</h3>
        <p class="text-xs text-ink-tertiary mt-0.5">
          "${esc(thread.title)}" · ${slots} slot${slots !== 1 ? 's' : ''} available
        </p>
      </div>
      <div class="flex-1 flex items-center justify-center py-12">
        <div class="w-6 h-6 border-2 border-accent-border border-t-transparent rounded-full animate-spin"></div>
      </div>
    </div>`);

  // Fetch the admin's connections list
  let connections = [];
  try {
    const { fetchMyConnections } = await import('./thread.api.js');
    connections = await fetchMyConnections();
  } catch {
    connections = [];
  }

  const memberSet = new Set(memberIds ?? []);
  // Exclude people already in the thread
  const eligible  = connections.filter((u) => !memberSet.has(u.id));

  // ── Rebuild modal content with real data ──────────────────────────────
  const modal = document.getElementById(MODAL_ID);
  if (!modal) return;

  const selectedIds = new Set();

  function _rowHtml(user) {
    const avatarHtml = user.avatar
      ? `<img src="${escAttr(user.avatar)}"
              class="w-10 h-10 rounded-full object-cover flex-shrink-0"
              loading="lazy" alt="${esc(user.name)}">`
      : `<div class="w-10 h-10 rounded-full bg-accent-subtle text-accent text-sm font-bold
                     flex items-center justify-center flex-shrink-0 select-none">
           ${esc((user.name ?? '?').charAt(0).toUpperCase())}
         </div>`;
    const deptText = user.department
      ? `<span class="text-[10px] text-ink-tertiary">${esc(user.department)}</span>`
      : '';
    return `
      <label class="add-member-row flex items-center gap-3 px-4 py-3 hover:bg-accent-subtle
                     active:bg-accent-subtle cursor-pointer transition-colors border-b border-line-light
                     last:border-0 select-none"
             data-user-id="${user.id}"
             data-name="${escAttr((user.name ?? '') + ' ' + (user.username ?? ''))}">
        <div class="flex-shrink-0">${avatarHtml}</div>
        <div class="flex-1 min-w-0">
          <p class="text-sm font-semibold text-ink-primary truncate">${esc(user.name ?? '')}</p>
          <p class="text-xs text-ink-tertiary truncate">@${esc(user.username ?? '')} ${deptText}</p>
        </div>
        <input type="checkbox"
               class="add-member-cb w-4 h-4 rounded accent-[#818CF8] cursor-pointer flex-shrink-0"
               value="${user.id}">
      </label>`;
  }

  function _buildContent(list) {
    if (!list.length) {
      return `<div class="flex flex-col items-center gap-2 py-12 text-center px-5">
                <span class="text-3xl">👥</span>
                <p class="text-sm text-ink-secondary">
                  ${eligible.length
                    ? 'No connections match your search.'
                    : 'All your connections are already members of this thread.'}
                </p>
              </div>`;
    }
    return `<div id="add-member-list">${list.map(_rowHtml).join('')}</div>`;
  }

  modal.innerHTML = `
    <div class="relative bg-surface-card rounded-2xl w-full max-w-md shadow-2xl max-h-[85vh]
                flex flex-col overflow-hidden">
      ${_closeBtn(MODAL_ID)}
      <div class="px-5 pt-6 pb-4 border-b border-line-light flex-shrink-0">
        <h3 class="text-base font-bold text-ink-primary">➕ Add Members</h3>
        <p class="text-xs text-ink-tertiary mt-0.5">
          "${esc(thread.title)}" · ${slots} slot${slots !== 1 ? 's' : ''} available
        </p>
      </div>

      <div class="px-4 py-2.5 border-b border-line-light flex-shrink-0">
        <div class="relative">
          <svg class="absolute left-3 top-1/2 -translate-y-1/2 text-ink-tertiary pointer-events-none"
               width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"
               viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
          <input id="add-member-search"
                 type="search" placeholder="Search connections…" autocomplete="off"
                 class="w-full pl-8 pr-3 py-2 text-sm rounded-xl border border-line bg-surface-raised
                        focus:bg-surface-card focus:border-accent-border focus:ring-2 focus:ring-accent-border
                        outline-none transition-all placeholder-ink-tertiary text-ink-primary">
        </div>
      </div>

      <div class="flex-1 overflow-y-auto" id="add-member-scroll">
        ${_buildContent(eligible)}
      </div>

      <div class="flex-shrink-0 px-4 py-3 border-t border-line-light flex gap-3 items-center">
        <span id="add-member-count"
              class="text-xs text-ink-tertiary flex-1">No one selected</span>
        ${_btnSecondary('Cancel',
            `data-action="close-modal"`)}
        <button id="add-member-confirm"
                disabled
                class="flex-1 py-2.5 rounded-xl bg-accent hover:bg-accent-hover active:scale-95
                       text-sm font-semibold text-white transition-all duration-150 shadow-sm
                       disabled:opacity-40 disabled:cursor-not-allowed">
          Add Members
        </button>
      </div>
    </div>`;

  // ── Re-attach backdrop close listener (modal was re-built) ─────────────
  modal.addEventListener('click', (e) => {
    if (e.target === modal) _closeModal(MODAL_ID);
  }, { once: false });

  // ── Live search ─────────────────────────────────────────────────────────
  const searchEl   = modal.querySelector('#add-member-search');
  const scrollEl   = modal.querySelector('#add-member-scroll');
  const confirmBtn = modal.querySelector('#add-member-confirm');
  const countEl    = modal.querySelector('#add-member-count');

  function _updateCount() {
    const n = selectedIds.size;
    countEl.textContent  = n === 0 ? 'No one selected' : `${n} selected`;
    confirmBtn.disabled  = n === 0 || n > slots;
    if (n > slots) countEl.textContent = `Too many — only ${slots} slot${slots !== 1 ? 's' : ''} left`;
  }

  function _renderList(query) {
    const q    = (query ?? '').toLowerCase().trim();
    const list = q
      ? eligible.filter((u) =>
          (u.name ?? '').toLowerCase().includes(q) ||
          (u.username ?? '').toLowerCase().includes(q) ||
          (u.department ?? '').toLowerCase().includes(q)
        )
      : eligible;
    scrollEl.innerHTML = _buildContent(list);
    // Re-tick any already-selected checkboxes after re-render
    scrollEl.querySelectorAll('.add-member-cb').forEach((cb) => {
      if (selectedIds.has(Number(cb.value))) cb.checked = true;
    });
  }

  searchEl?.addEventListener('input', (e) => _renderList(e.target.value));

  // ── Checkbox delegation via scroll container ────────────────────────────
  scrollEl?.addEventListener('change', (e) => {
    const cb = e.target.closest('.add-member-cb');
    if (!cb) return;
    const uid = Number(cb.value);
    if (cb.checked) selectedIds.add(uid); else selectedIds.delete(uid);
    _updateCount();
  });

  // Allow clicking the whole row (not just the checkbox)
  scrollEl?.addEventListener('click', (e) => {
    const row = e.target.closest('.add-member-row');
    if (!row || e.target.closest('.add-member-cb')) return;
    const cb = row.querySelector('.add-member-cb');
    if (!cb) return;
    cb.checked = !cb.checked;
    cb.dispatchEvent(new Event('change', { bubbles: true }));
  });

  // ── Confirm ─────────────────────────────────────────────────────────────
  confirmBtn?.addEventListener('click', () => {
    if (!selectedIds.size) return;
    _closeModal(MODAL_ID);
    onConfirm([...selectedIds]);
  });
}
