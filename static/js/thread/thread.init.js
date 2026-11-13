/**
 * thread.init.js
 * Application entry point for the thread system.
 *
 * CHANGES:
 *  - Issue 2: reconnect handler calls handleLoadThreadList() to recover missed updates.
 *  - Issue 5: _loadPendingInvites calls loadAndRenderInvitesTab (3-section invites).
 *             window.focus listener refreshes all three invite lists.
 *  - NEW: attachThreadSwipe imported and attached to message list.
 *  - Import updated: renderInvitesList removed (replaced by loadAndRenderInvitesTab).
 *  - window._threadInitDone flag set after init so loadAndRenderInvitesTab
 *    only shows the toast on first load.
 *  - All previously applied fixes retained (HIDDEN-05, WS-04, ARCH-02).
 */

import { threadState, resetThreadSession }               from './thread.state.js';
import { initThreadDelegation, destroyThreadDelegation }  from './thread.delegation.js';
import { handleLoadThreadList, handleOpenThread }          from './thread.events.js';
import { fetchCurrentUser }                               from './thread.api.js';
import { renderThreadList }                               from './thread.render.js';
import { attachThreadLongPress }                          from './thread.longpress.js';
import { attachThreadSwipe, detachThreadSwipe }           from './thread.swipe.js';
import { io }                                             from 'https://cdn.socket.io/4.7.5/socket.io.esm.min.js';

// ─── Exports ──────────────────────────────────────────────────────────────────

/** Shared socket.io instance — imported by thread.events.js and thread.websocket.js */
export let socket = null;


// ─── Entry point ──────────────────────────────────────────────────────────────

let _initialised = false;

export async function threadInit() {
  if (_initialised) return;

  try {
    // 1. Resolve current user
    let currentUser;
    try {
      currentUser = await fetchCurrentUser();
    } catch {
      showToast('Could not resolve your session. Please log in again.', 'error');
      _renderAuthError();
      return;
    }

    if (!currentUser?.id) {
      showToast('Invalid user session. Please log in again.', 'error');
      _renderAuthError();
      return;
    }

    threadState.currentUser = currentUser;

    // 2. Connect socket.io
    if (typeof io !== 'undefined') {
      socket = io({
        transports:           ['polling', 'websocket'],
        auth:                 { token: api.getToken() },
        reconnection:         true,
        reconnectionAttempts: 5,
        reconnectionDelay:    1000,
      });
      _bindSocketLifecycle(socket);
    } else {
      showToast('Real-time disabled — socket.io not loaded.', 'error');
    }

    // 3. Event delegation
    initThreadDelegation();

    // 4. Load thread list
    try {
      await handleLoadThreadList();
    } catch {
      showToast('Failed to load thread list. Please refresh.', 'error');
    }

    // 5. Load pending invites (all three sections)
    await _loadPendingInvites();

    // 6. Wire send button enable/disable (handled by delegation _onInput)

    // 7. Wire avatar file input
    _initAvatarFileInput();

    // 8. Open thread from URL hash
    const hashId = _hashThreadId();
    if (hashId) {
      try {
        await handleOpenThread(hashId);
      } catch {
        showToast('Failed to open thread from URL.', 'error');
      }
    }

    // 9. Long-press / right-click on messages
    const listEl = document.getElementById('thread-messages-list');
    if (listEl) {
      attachThreadLongPress(listEl);
      // NEW: swipe-to-reply on touch devices
      attachThreadSwipe(listEl);
    }

    // 10. Block native context menu on message bubbles
    const containerEl = document.getElementById('threads');
    if (containerEl) {
      containerEl.addEventListener('contextmenu', (e) => {
        if (e.target.closest('.msg-bubble')) e.preventDefault();
      });
    }

    // 11. Refresh invites when user returns to tab/window
    window.addEventListener('focus', _onWindowFocus);

    // Mark init complete so subsequent loadAndRenderInvitesTab calls skip the toast
    window._threadInitDone = true;
    _initialised = true;

  } catch (err) {
    showToast(`Initialisation failed: ${err?.message ?? err}`, 'error');
    _renderInitError(err);
  }
}

export function threadDestroy() {
  resetThreadSession();
  socket?.disconnect();
  socket       = null;
  _initialised = false;
  destroyThreadDelegation?.();
  detachThreadSwipe();
  window.removeEventListener('focus', _onWindowFocus);
  delete window._threadInitDone;
}


// ─── Issue 5: window focus — refresh all invite lists ────────────────────────

function _onWindowFocus() {
  if (document.visibilityState === 'visible') {
    _loadPendingInvites();
  }
}


// ─── Avatar file input ────────────────────────────────────────────────────────

function _initAvatarFileInput() {
  document.addEventListener('change', (e) => {
    const input = e.target;
    if (
      input.id !== 'thread-avatar-file-input' &&
      !input.matches("[data-role='thread-avatar-input']")
    ) return;
    const file     = input.files?.[0];
    const threadId = threadState.activeThreadId;
    if (!file || !threadId) return;
    import('./thread.events.js').then(({ handleThreadAvatarUpload }) => {
      handleThreadAvatarUpload(threadId, file);
    });
    input.value = '';
  });
}


// ─── Issue 5: Pending invites loader (all three sections) ────────────────────

/**
 * Fetch invites + my requests + moderation queue in parallel.
 * Delegates rendering to loadAndRenderInvitesTab in thread.events.js.
 */
async function _loadPendingInvites() {
  try {
    const { loadAndRenderInvitesTab } = await import('./thread.events.js');
    await loadAndRenderInvitesTab();
  } catch {
    // Non-fatal — invites tab stays empty
  }
}


// ─── Socket lifecycle ─────────────────────────────────────────────────────────

let _hasConnectedOnce = false;

function _bindSocketLifecycle(sock) {

  sock.on('connect', () => {
    // HIDDEN-05: only show toast on reconnect, not initial connection
    if (_hasConnectedOnce) {
      showToast('Reconnected', 'success');
    }
    _hasConnectedOnce = true;
  });

  sock.on('disconnect', (reason) => {
    if (reason !== 'io client disconnect') {
      showToast('Connection lost — reconnecting…', 'error');
    }
    if (reason === 'io server disconnect') {
      setTimeout(() => sock.connect(), 1500);
    }
  });

  sock.on('connect_error', (err) => {
    if (_hasConnectedOnce) return;
    console.warn('[thread_init] Socket connection error:', err.message);
  });

  /**
   * Issue 2: On reconnect, reload thread list to recover missed updates,
   * then rejoin the active thread room.
   */
  sock.on('reconnect', () => {
    handleLoadThreadList().catch(() => {});

    const threadId = threadState.activeThreadId;
    if (threadId) {
      import('./thread.websocket.js').then(({ disconnectThreadWebSocket, initThreadWebSocket }) => {
        disconnectThreadWebSocket(threadId);
        initThreadWebSocket(sock, threadId);
      });
    }
  });
}


// ─── Helpers ─────────────────────────────────────────────────────────────────

function _hashThreadId() {
  const hash = window.location.hash;
  if (!hash?.startsWith('#thread-')) return NaN;
  const id = parseInt(hash.slice(8), 10);
  return isNaN(id) || id <= 0 ? NaN : id;
}

function showToast(message, type = 'info') {
  if (typeof window.showToast === 'function') window.showToast(message, type);
}

function _renderAuthError() {
  const root = document.getElementById('threads');
  if (root) {
    root.innerHTML = `
      <div class="flex flex-col items-center justify-center h-full gap-4 p-8 text-center">
        <span class="text-4xl">🔒</span>
        <p class="text-sm text-gray-600">You must be logged in to use threads.</p>
        <a href="/student/login"
           class="text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700
                  rounded-xl px-4 py-2 transition-colors">
          Log in
        </a>
      </div>`;
  }
}

function _renderInitError(err) {
  const root = document.getElementById('threads');
  if (root) {
    root.innerHTML = `
      <div class="flex flex-col items-center justify-center h-full gap-3 p-8 text-center">
        <span class="text-3xl">⚠️</span>
        <p class="text-sm text-gray-600">Failed to load threads. Please refresh.</p>
        <p class="text-xs text-gray-400">${String(err?.message ?? err)}</p>
        <button onclick="location.reload()"
                class="text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700
                       rounded-xl px-4 py-2 transition-colors">
          Refresh
        </button>
      </div>`;
  }
}


// ─── Lazy init ────────────────────────────────────────────────────────────────
// Threads is a full-screen overlay opened on demand (same as Messages), so it
// is initialised by _openThreadsOverlay() the first time it is shown rather
// than on every feed page load.

window.initThreads = threadInit;
