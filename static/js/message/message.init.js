/**
 * Message System Initialization \u2014 PRODUCTION
 *
 * Bootstrap order:
 *  1. Fetch current user from REST \u2192 store in messageState (no window globals)
 *  2. Connect WebSocket (authenticated event also sets user_id in state as backup)
 *  3. Load conversations when #messages becomes active (MutationObserver)
 *  4. Set up network monitoring
 *  5. Set up input observers (send \u2194 mic toggle)
 */

import * as messageApi   from './message.api.js';
import * as messageState from './message.state.js';
import { messageWS }     from './message.websocket.js';
import * as render       from './message.render.js';
import { watchNetworkStatus, isOnline } from './message.utils.js';

const _toast = (msg, type) => { const fn = window.showToast || globalThis.showToast; fn?.(msg, type); };

// ============================================================================
// ENTRY POINT
// ============================================================================

let isInitialized = false;

export async function initializeMessaging() {
  // Prevent re-initialization
  if (isInitialized) return;
  isInitialized = true;

  console.log('\ud83d\ude80 Initialising messaging system\u2026');

  try {
    // 1. Fetch and store current user
    await _bootstrapCurrentUser();

    // 2. Connect WebSocket using token from cookie
    
  
    messageWS.connect();
    

    // 3. Load initial conversation data
    await loadInitialData();

    // 4. Network monitoring
    _setupNetworkMonitoring();

    // 5. Input toggle observer & search (covers future opens too)
    _setupInputObserver();
    _setupConversationSearch();

    console.log('\u2705 Messaging system ready');
  } catch (error) {
    console.error('Failed to initialise messaging:', error);
    _toast('Messaging initialisation failed: ' + error.message, 'error');
    // Allow re-try on next activation if init failed
    isInitialized = false;
  }
}

// ============================================================================
// LOAD CONVERSATIONS
// ============================================================================

export async function loadInitialData() {
  try {
    messageState.setLoadingConversations(true);
    render.renderConversationList(); // shows skeleton

    const conversations = await messageApi.fetchConversations();
    messageState.setConversations(conversations);
    render.renderConversationList();

    messageWS.updateUnreadBadge();
  } catch (error) {
    console.error('Failed to load conversations:', error);
    _toast('Failed to load conversations', 'error');
  } finally {
    messageState.setLoadingConversations(false);
  }
}

// ============================================================================
// PRIVATE HELPERS
// ============================================================================

async function _bootstrapCurrentUser() {
  // Try the REST endpoint first
  try {
    const user = await messageApi.fetchCurrentUser();
    if (user?.id) {
      messageState.setCurrentUser(user);
      console.log(`\ud83d\udc64 Current user: ${user.name} (${user.id})`);
      return;
    }
  } catch {
    // Endpoint might not exist yet \u2014 fall through to window fallback
  }

  // Graceful fallback: if the app exposes window.currentUser, use it
  if (window.currentUser?.id) {
    messageState.setCurrentUser(window.currentUser);
    console.warn('\u26a0\ufe0f  Using window.currentUser as fallback \u2014 prefer REST endpoint');
  } else {
    console.warn('\u26a0\ufe0f  Current user unknown \u2014 will be set on WS authenticated event');
  }
}

function _setupNetworkMonitoring() {
  messageState.setOnlineStatus(isOnline());

  if (!isOnline()) render.showOfflineBanner();

  watchNetworkStatus(
    () => {
      messageState.setOnlineStatus(true);
      render.hideOfflineBanner();
      _toast('Back online', 'success');

      // ✅ FIX: Just reconnect - backend reads cookie
      messageWS.connect();  // ← Remove token parameter
    },
    () => {
      messageState.setOnlineStatus(false);
      render.showOfflineBanner();
      _toast('You are offline', 'warning');
    }
  );
}

/**
 * Observe the message input for value changes so the send \u2194 mic button
 * stays in sync even when the value is changed programmatically.
 */
function _setupInputObserver() {
  // #messages is always in the DOM (just hidden until opened) so this
  // listener survives conversation open/close cycles safely.
  const messagesSection = document.getElementById('messages');
  if (!messagesSection) return;

  messagesSection.addEventListener('input', (event) => {
    const textarea = event.target.closest('[data-action="handle-message-input"]');
    if (!textarea) return;

    const sendBtn = document.getElementById('msg-send-btn');
    if (!sendBtn) return;

    const hasContent = textarea.value.trim().length > 0 ||
                       messageState.getPendingAttachments().length > 0;

    sendBtn.disabled = !hasContent;
    sendBtn.classList.toggle('opacity-50', !hasContent);
    sendBtn.classList.toggle('cursor-not-allowed', !hasContent);
  });
}

function _setupConversationSearch() {
  const input = document.getElementById('conversation-search');
  if (!input) return;

  input.addEventListener('input', () => {
    const query = input.value.trim().toLowerCase();
    const items = document.querySelectorAll('#conversations-list .conversation-item');

    items.forEach(item => {
      const name    = item.querySelector('.font-semibold')?.textContent?.toLowerCase() || '';
      const preview = item.querySelector('.text-gray-500, .text-gray-800')?.textContent?.toLowerCase() || '';
      const matches = !query || name.includes(query) || preview.includes(query);
      item.classList.toggle('hidden', !matches);
    });
  });
}

// Cookie helper
function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(';').shift();
  return null;
}

// ============================================================================
// OBSERVE #messages FOR active CLASS \u2014 INIT ON ACTIVATION
// ============================================================================
document.addEventListener('DOMContentLoaded', () => {
  const messagesSection = document.getElementById('messages');

  if (!messagesSection) {
    console.error('#messages element not found');
    return;
  }

  // If already active on load, init immediately
  if (messagesSection.classList.contains('active')) {
    initializeMessaging();
  }

  // Watch for the active class being added/removed
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
        if (messagesSection.classList.contains('active')) {
          initializeMessaging();
        }
      }
    }
  });

  observer.observe(messagesSection, {
    attributes: true,
    attributeFilter: ['class'],
  });
});

// Expose for external/manual calls (without auto-invoking)
window.initializeMessaging = initializeMessaging;