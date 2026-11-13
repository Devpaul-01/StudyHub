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
import {
  initThreadWebSocket,
  disconnectThreadWebSocket,
  registerThreadPersonalHandlers,
  unregisterThreadPersonalHandlers,
}                                                         from './thread.websocket.js';
import { socket as sharedSocket }                         from '../core/socket.js';

// ─── Exports ──────────────────────────────────────────────────────────────────

/**
 * The app-wide socket.io instance from core/socket.js — the SAME connection
 * Messages uses. Imported by thread.events.js and thread.delegation.js.
 */
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

    // 2. Use the shared socket. Opening our own io() here would be a second
    //    connection for the same user: the server registers presence per
    //    socket and clears the user's active thread whenever ANY of their
    //    sockets disconnects, so two sockets step on each other (and on
    //    Messages).
    socket = sharedSocket;
    _bindSocketLifecycle(socket);
    registerThreadPersonalHandlers(socket);

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
  // The socket is shared with Messages: detach our listeners and leave our
  // room, but never disconnect it.
  const activeId = threadState.activeThreadId;
  if (activeId) disconnectThreadWebSocket(activeId);
  _unbindSocketLifecycle(socket);
  unregisterThreadPersonalHandlers();
  resetThreadSession();
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
const _lifecycle = {};

function _listen(sock, event, handler) {
  _lifecycle[event] = handler;
  sock.on(event, handler);
}

function _unbindSocketLifecycle(sock) {
  for (const [event, handler] of Object.entries(_lifecycle)) {
    sock?.off(event, handler);
    delete _lifecycle[event];
  }
}

function _bindSocketLifecycle(sock) {
  // The shared socket is usually already connected by the time we bind, so
  // its first 'connect' has fired and will not fire again.
  _hasConnectedOnce = sock.connected;

  _listen(sock, 'connect', () => {
    const isReconnect = _hasConnectedOnce;
    _hasConnectedOnce = true;

    // HIDDEN-05: only act on reconnect, not the initial connection
    if (!isReconnect) return;

    showToast('Reconnected', 'success');

    // A reconnect is a new server-side socket id, so it is no longer in any
    // thread room. Recover missed list updates and rejoin the open thread.
    // (This used to be sock.on('reconnect'), which Socket.IO v4 only emits
    // on the Manager, so it never ran.)
    handleLoadThreadList().catch(() => {});

    const threadId = threadState.activeThreadId;
    if (threadId) {
      disconnectThreadWebSocket(threadId);
      initThreadWebSocket(sock, threadId);
    }
  });

  _listen(sock, 'disconnect', (reason) => {
    if (reason !== 'io client disconnect') {
      showToast('Connection lost — reconnecting…', 'error');
    }
    if (reason === 'io server disconnect') {
      setTimeout(() => sock.connect(), 1500);
    }
  });

  _listen(sock, 'connect_error', (err) => {
    if (_hasConnectedOnce) return;
    console.warn('[thread_init] Socket connection error:', err.message);
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
