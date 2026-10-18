import { connectionContainer } from './connection.constants.js';
import { connectionState } from './connection.state.js';
import { loadConnectionTab } from './connection.init.js';
import { startAuthorOverviewStream } from '../feed/feed.modals.js';
import {
  
  acceptRequest,
  rejectRequest,
  cancelRequest,
  blockRequest,
  removeConnectionRequest,
  openConnectionActionsModal,
  withdrawStudySession,
  showStudySessions,
  unblockRequest,
  submitDeclineStudySession,
  viewBlockedUsers,
  showMutualConnections,
  sendConnectionRequest,
  
  viewUserOverview,

  performConnectionSearch,
  clearConnectionSearchInput,
  toggleConnectionDetails,
  closeAdvancedOptionModals,
  showAdvancedOptions,
  openStudySessionModal,
  showUserAvatar,
  showActiveConnections,
  toggleConnectionSetting,
  formThread,
  viewConnectionNotes,
  rescheduleStudySession,
  declineStudySession,
  handleCreateStudySession,
  submitCancelStudySession,
  addRescheduleTimeSlot,
  submitRescheduleSession,
  createOrUpdateNote,
  closeStudySessionResourcePreview,
  showStudySessionResourcePreview,
  showCreateNoteUI,
  addTimeSlot,
  removeTimeSlot,
  confirmStudySession,
  cancelStudySession,
  filterSessions
} from './connection.events.js';

import {renderHelpResults, appendVolunteerCard,renderConnectionTab} from './connection.render.js';


// ============================================================================
// CONNECTION ACTION HANDLERS
// ============================================================================

export const ConnectionHandlers = {
  'switch-connection-tab': (target, event) => {
    event.stopPropagation();
    const tab = target.dataset.tab;
    loadConnectionTab(tab);
  },
  'close-study-session-resource-modal': (target, event) => {
    event.stopPropagation();
    closeStudySessionResourcePreview();
    
  },
  

  

  'close-session-preview-modal': (target, event) => {
    event.stopPropagation();
    closeStudySessionResourcePreview();
  },
  
  'preview-study-session-resource': (target, event) => {
    const url = target.dataset.url;
    const name = target.dataset.name;
    const type = target.dataset.type;
    
    showStudySessionResourcePreview(url, name, type);
    
  },
  'close-notification': (target, event) => {
    if (target) {
      const toast = target.closest('.notification-toast');
      if (toast) {
        toast.classList.remove('notification-toast-show');
        setTimeout(() => {
          toast.remove();
        }, 300);
      }
    }
  },
  'add-reschedule-time-slot':(target, event) => {
    event.stopPropagation();
    addRescheduleTimeSlot();
  },
  'submit-reschedule-study-session': (target, event) => {
    event.stopPropagation();
    submitRescheduleSession();
  },
  'remove-reschedule-time-slot': (target, event) => {
    const timeSlot = target.closest('.reschedule-time-slot');
    const container = document.getElementById('reschedule-proposed-times-container');
    timeSlot.remove();

    
  },
  
  // ============================================================================
  // STUDY SESSION HANDLERS (FIXED)
  // ============================================================================
  
  'submit-create-session': (target, event) => {
    event.preventDefault();
    handleCreateStudySession();
  },
  

  'save-session-changes': (target, event) => {
    event.stopPropagation();
    saveSessionChanges();
  },
  'create-study-session': (target, event) => {
    event.stopPropagation();
    const userId = parseInt(target.dataset.userId);
    openStudySessionModal(userId);
  },
  
  'view-study-sessions': (target, event) => {
    event.stopPropagation();
    const userId = parseInt(target.dataset.userId);
    showStudySessions(userId);
  },
  
  
  'add-time-slot': (target, event) => {
    event.stopPropagation();
    addTimeSlot();
  },
  
  'remove-time-slot': (target, event) => {
    event.stopPropagation();
    removeTimeSlot(target);
  },
  'submit-decline-session-reason': (target, event) => {
    submitDeclineStudySession();
  },
  'upload-reschedule-study-session-resource': (target, event) => {
    document.getElementById('reschedule-session-upload-input').click();
  },
  'submit-cancel-session-reason': (target, event) => {
    submitCancelStudySession();
  },
  'upload-create-study-session-resource': (target, event) => {
    document.getElementById('create-sesion-upload-input').click();
  },
  
  'confirm-study-session': (target, event) => {
    event.stopPropagation();
    const sessionId = parseInt(target.dataset.sessionId);
    confirmStudySession(sessionId);
  },
  
  'cancel-study-session': (target, event) => {
    event.stopPropagation();
    const sessionId = parseInt(target.dataset.sessionId);
    cancelStudySession(sessionId);
  },
  'decline-study-session': (target, event) => {
    event.stopPropagation();
    const sessionId = parseInt(target.dataset.sessionId);
    declineStudySession(sessionId);
  },
  'reschedule-study-session': (target, event) => {
    event.stopPropagation();
    const sessionId = parseInt(target.dataset.sessionId);
    rescheduleStudySession(sessionId);
  },
  
  'filter-sessions': (target, event) => {
    event.stopPropagation();
    filterSessions(target);
  },

  // ============================================================================
  // CONNECTION REQUEST HANDLERS
  // ============================================================================
  
  'connect-request': (target, event) => {
    event.stopPropagation();
    const userId = parseInt(target.dataset.userId);
    connectRequest(userId, target);
  },

  'accept-request': (target, event) => {
    event.stopPropagation();
    const connectionId = parseInt(target.dataset.connectionId);
    acceptRequest(connectionId, target);
  },

  'reject-request': (target, event) => {
    event.stopPropagation();
    const connectionId = parseInt(target.dataset.connectionId);
    rejectRequest(connectionId, target);
  },

  'cancel-request': (target, event) => {
    event.stopPropagation();
    const connectionId = parseInt(target.dataset.connectionId);
    cancelRequest(connectionId, target);
  },

  'block-user': (target, event) => {
    event.stopPropagation();
    const userId = parseInt(target.dataset.userId);
    blockRequest(userId, target);
  },

  'remove-connection': (target, event) => {
    event.stopPropagation();
    const userId = parseInt(target.dataset.userId);
    removeConnectionRequest(userId, target);
  },

  'unblock-request': (target, event) => {
    event.stopPropagation();
    const userId = parseInt(target.dataset.userId);
    unblockRequest(userId, target);
  },

  'view-blocked-users': (target, event) => {
    event.stopPropagation();
    viewBlockedUsers();
  },

  'view-mutual-connections': (target, event) => {
    event.stopPropagation();
    const userId = parseInt(target.dataset.userId);
    showMutualConnections(userId);
  },

  'view-overview': (target, event) => {
    event.stopPropagation();
    const userId = parseInt(target.dataset.userId);
    startAuthorOverviewStream(userId);
  },

  'toggle-details': (target, event) => {
    event.stopPropagation();
    toggleConnectionDetails(target);
  },

  'toggle-advanced-options': (target, event) => {
    event.stopPropagation();
    showAdvancedOptions(target);
  },
  

  'clear-search-input': (target, event) => {
    event.stopPropagation();
    clearConnectionSearchInput();
  },

  'filter-connections': (target, event) => {
    event.stopPropagation();
    if (target.checked) {
      showActiveConnections();
    } else {
      loadConnectionTab('connected');
    }
  },

  'toggle-connection-notification': (target, event) => {
    event.stopPropagation();
    toggleConnectionSetting(target);
  },

  'view-avatar': (target, event) => {
    event.stopPropagation();
    const src = target.dataset.src;
    showUserAvatar(src);
  },
  
  'withdraw-study-session': (target, event) => {
    const sessionId = target.dataset.sessionId;
    withdrawStudySession(sessionId)
  },

  

  'form-thread': (target, event) => {
    event.stopPropagation();
    const userId = parseInt(target.dataset.userId);
    formThread(userId);
  },

  'view-connection-notes': (target, event) => {
    event.stopPropagation();
    const connectionId = parseInt(target.dataset.connectionId);
    viewConnectionNotes(connectionId);
  },
  
  'toggle-advanced-connected-options': (target, event) => {
    openConnectionActionsModal(target);
    
    
  },
  

  'save-note': (target, event) => {
    event.stopPropagation();
    createOrUpdateNote();
  },

  'create-note': (target, event) => {
    event.stopPropagation();
    showCreateNoteUI();
  },

  'message-user': (target, event) => {
    event.stopPropagation();
    console.log('Message user - handled by messaging system');
  },

  'view-profile': (target, event) => {
    event.stopPropagation();
    console.log('View profile - handled by profile system');
  },
    'open-connection-request': (target, event) => {
    event.stopPropagation();
    const userId = parseInt(target.dataset.userId);
    const userName = target.dataset.userName;
    
    // Store for later use
    connectionState.setPendingConnection(userId, userName);
    
    // Update modal title
    document.getElementById('connection-modal-title').textContent = `Connect with ${userName}`;
    
    // Clear and setup textarea
    const textarea = document.getElementById('connection-message-input');
    const charCount = document.getElementById('connection-char-count');
    
    textarea.value = '';
    charCount.textContent = '0';
    
    openModal('connection-request-message-modal');
    setTimeout(() => textarea.focus(), 100);
    closeModal('find-help-modal');
  },

  'send-connection-with-message': async (target, event) => {
    event.stopPropagation();
    
    const pending = connectionState.getPendingConnection();
    if (!pending) return;
    
    const textarea = document.getElementById('connection-message-input');
    const message = textarea.value.trim();
    sendConnectionRequest(pending.user_id, message, target);
    closeModal('connection-request-message-modal');
  },
    
    
    
    

  'open-find-help': (target, event) => {
    event.stopPropagation();
    
    // Reset modal
    const input = document.getElementById('help-subject-input');
    const results = document.getElementById('find-help-results');
    
    input.value = '';
    results.innerHTML = `
      <div class="empty-state" style="text-align: center; padding: 40px 20px;">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin: 0 auto 16px; opacity: 0.3;">
          <circle cx="11" cy="11" r="8"></circle>
          <path d="m21 21-4.35-4.35"></path>
        </svg>
        <p>Search for a subject to find students who can help</p>
      </div>
    `;
    
    openModal('find-help-modal');
    setTimeout(() => input.focus(), 100);
  },
  
  'search-subject': (target, event) => {
  event.stopPropagation();
  const subject = target.dataset.subject;
  
  const input = document.getElementById('help-subject-input');
  input.value = subject;
  
  // Trigger the search directly instead of clicking the button
  const searchBtn = document.getElementById('help-search-btn');
  const searchEvent = new Event('click', { bubbles: true, cancelable: true });
  searchBtn.dispatchEvent(searchEvent);
},


// ============================================================
// FIX 2: search-help handler - Refined loader (line 401-442)
// ============================================================
// Replace the existing handler with this:
'search-help': async (target, event) => {
  event.stopPropagation();

  const input = document.getElementById('help-subject-input');
  const results = document.getElementById('find-help-results');
  const subject = input.value.trim();

  if (!subject) {
    showToast('Please enter a subject', 'error');
    return;
  }

  results.innerHTML = `
    <div style="display: flex; justify-content: center; align-items: center; padding: 60px 20px;">
      <div style="width: 24px; height: 24px; border: 3px solid #f3f3f3; border-top: 3px solid var(--primary-color); border-radius: 50%; animation: spin 1s linear infinite;"></div>
    </div>
  `;

  try {
    const response = await api.post('/connections/help/find', { subject });

    if (response.status === 'success' && response.data.helpers.length > 0) {
      renderHelpResults(response.data.helpers, subject);
    } else {
      // No helpers found — show broadcast option instead
      results.innerHTML = `
        <div style="text-align: center; padding: 32px 20px;">
          <div style="font-size: 36px; margin-bottom: 12px;">🔍</div>
          <p style="font-weight: 500; margin: 0 0 6px 0;">No matches found for "${subject}"</p>
          <p style="font-size: 13px; opacity: 0.6; margin: 0 0 20px 0;">Broadcast to your connections instead</p>

        </div>
      `;
    }
  } catch (error) {
    console.error('Search help error:', error);
    showToast('Failed to search for help', 'error');
  }
},

'broadcast-help': async (target, event) => {
  event.stopPropagation();

  const subject = target.dataset.subject || document.getElementById('help-subject-input')?.value?.trim();
  if (!subject) {
    showToast('No subject provided', 'error');
    return;
  }

  target.disabled = true;
  target.textContent = 'Sending...';

  try {
    const response = await ConnectionAPI.broadcastHelpRequest(subject);

    if (response.status === 'success') {
      const { help_request_id, notified_count } = response.data;

      // Store the active request id so socket can reference it
      window._activeHelpRequestId = help_request_id;

      renderHelpBroadcastSent(help_request_id, subject, notified_count);
      const viewRequestBtn = document.getElementById('view-my-request-btn');
      if (viewRequestBtn) viewRequestBtn.classList.remove('hidden');
      // Pre-fill the volunteers modal subject line
      const modalSubject = document.getElementById('volunteers-modal-subject');
      if (modalSubject) modalSubject.textContent = subject;
      startExpiryCountdown(new Date(Date.now() + 2 * 60 * 60 * 1000));
      showToast(`Request sent to ${notified_count} connection${notified_count !== 1 ? 's' : ''}`, 'success');
    } else {
      showToast('Failed to broadcast request', 'error');
      target.disabled = false;
      target.textContent = '📡 Broadcast to My Network';
    }
  } catch (error) {
    console.error('Broadcast help error:', error);
    showToast('Failed to broadcast request', 'error');
    target.disabled = false;
    target.textContent = '📡 Broadcast to My Network';
  }
},

'message-volunteer': (target, event) => {
  event.stopPropagation();

  const userId = parseInt(target.dataset.userId);
  const userName = target.dataset.userName;

  // Close the help modal first
  closeModal('find-help-modal');

  // TODO: wire this up to your messaging system when it's built
  // When your messaging system is ready, replace with something like:
  // navigateToMessages(userId);
  // or: openMessageModal(userId, userName);
},


 
  'connect-from-help': (target, event) => {
    event.stopPropagation();
    const userId = parseInt(target.dataset.userId);
    const userName = target.dataset.userName;
    
    closeModal('find-help-modal');
    
    // Trigger connection request modal
    connectionState.setPendingConnection(userId, userName);
    document.getElementById('connection-modal-title').textContent = `Connect with ${userName}`;
    
    const textarea = document.getElementById('connection-message-input');
    textarea.value = '';
    document.getElementById('connection-char-count').textContent = '0';
    
    openModal('connection-request-message-modal');
    setTimeout(() => textarea.focus(), 100);
  }
};

// ============================================================================
// SETUP EVENT DELEGATION
// ============================================================================
/*
export function setupConnectionDelegation() {
  if (!connectionContainer) {
    console.error('Connection container not found');
    return;
  }

  // Click events
  connectionContainer.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action]');
    if (!target) return;

    const action = target.dataset.action;
    const handler = ConnectionHandlers[action];

    if (handler) {
      handler(target, event);
    } else {
      console.warn(`No handler for action: ${action}`);
    }
  });

  // Change events
  connectionContainer.addEventListener('change', (event) => {
    const target = event.target.closest('[data-action]');
    if (!target) return;

    const action = target.dataset.action;
    const handler = ConnectionHandlers[action];

    if (handler) {
      handler(target, event);
    }
  });

  // Setup search input
  setupSearchInput();

  // Setup pull-to-refresh
  setupPullToRefresh();
}
*/
// ============================================================================
// SEARCH INPUT HANDLER
// ============================================================================

let searchTimeout = null;

export function setupSearchInput() {
  const searchInput = connectionContainer.querySelector('#connections-search-input');
  const searchClearBtn = connectionContainer.querySelector('#search-clear-btn');
  const searchResults = connectionContainer.querySelector('#connections-search-results');

  if (!searchInput) return;

  searchInput.addEventListener('input', (e) => {
    const query = e.target.value.trim();

    // Show/hide clear button
    if (searchClearBtn) {
      if (query.length > 0) {
        searchClearBtn.classList.remove('hidden');
      } else {
        searchClearBtn.classList.add('hidden');
      }
    }

    // Debounce search
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      if (query.length > 0) {
        performConnectionSearch(query);
      } else {
        if (searchResults) {
          searchResults.classList.add('hidden');
          searchResults.innerHTML = '';
        }
      }
    }, 300);
  });
}

// ============================================================================
// PULL-TO-REFRESH
// ============================================================================

class PullToRefresh {
  constructor(container, onRefresh) {
    this.container = container;
    this.onRefresh = onRefresh;
    this.indicator = container.querySelector('.pull-to-refresh-indicator');
    
    this.startY = 0;
    this.currentY = 0;
    this.pulling = false;
    this.threshold = 150;
    this.maxPull = 120;
    
    this.init();
  }
  
  init() {
    // Find the actual scrollable ancestor (e.g. .content-area) since the
    // #connections section itself does not scroll — its parent does.
    this.scrollParent = this._getScrollParent(this.container);

    this.container.addEventListener('touchstart', this.handleTouchStart.bind(this), { passive: true });
    this.container.addEventListener('touchmove', this.handleTouchMove.bind(this), { passive: false });
    this.container.addEventListener('touchend', this.handleTouchEnd.bind(this), { passive: true });
  }

  /** Walk up the DOM to find the nearest vertically-scrollable ancestor. */
  _getScrollParent(el) {
    let node = el.parentElement;
    while (node) {
      const { overflowY } = window.getComputedStyle(node);
      if ((overflowY === 'auto' || overflowY === 'scroll') && node.scrollHeight > node.clientHeight) {
        return node;
      }
      node = node.parentElement;
    }
    return document.scrollingElement || document.documentElement;
  }
  
  handleTouchStart(e) {
    const scrollTop = this.scrollParent ? this.scrollParent.scrollTop : this.container.scrollTop;
    if (scrollTop === 0) {
      this.startY = e.touches[0].clientY;
      this.pulling = true;
    }
  }
  
  handleTouchMove(e) {
    if (!this.pulling) return;
    
    this.currentY = e.touches[0].clientY;
    const pullDistance = this.currentY - this.startY;
    const scrollTop = this.scrollParent ? this.scrollParent.scrollTop : this.container.scrollTop;
    
    if (pullDistance > 0 && scrollTop === 0) {
      e.preventDefault();
      
      const cappedDistance = Math.min(pullDistance, this.maxPull);
      
      this.indicator.classList.remove('hidden');
      this.indicator.style.transform = `translateY(${cappedDistance}px)`;
      this.indicator.style.opacity = Math.min(cappedDistance / this.threshold, 1);
      
      const indicatorText = this.indicator.querySelector('span');
      if (pullDistance >= this.threshold) {
        if (indicatorText) indicatorText.textContent = 'Release to refresh';
        this.indicator.classList.add('ready');
      } else {
        if (indicatorText) indicatorText.textContent = 'Pull to refresh';
        this.indicator.classList.remove('ready');
      }
    }
  }
  
  handleTouchEnd(e) {
    if (!this.pulling) return;
    
    const pullDistance = this.currentY - this.startY;
    this.pulling = false;
    
    if (pullDistance >= this.threshold) {
      this.indicator.style.transform = 'translateY(60px)';
      const indicatorText = this.indicator.querySelector('span');
      if (indicatorText) indicatorText.textContent = 'Refreshing...';
      this.indicator.classList.add('refreshing');
      
      this.onRefresh().then(() => {
        setTimeout(() => this.hideIndicator(), 500);
      }).catch((error) => {
        console.error('Refresh failed:', error);
        this.hideIndicator();
      });
    } else {
      this.hideIndicator();
    }
    
    this.startY = 0;
    this.currentY = 0;
  }
  
  hideIndicator() {
    this.indicator.style.transition = 'transform 0.3s ease, opacity 0.3s ease';
    this.indicator.style.transform = 'translateY(0)';
    this.indicator.style.opacity = '0';
    
    setTimeout(() => {
      this.indicator.classList.add('hidden');
      this.indicator.classList.remove('ready', 'refreshing');
      this.indicator.style.transition = '';
      this.indicator.style.transform = '';
      this.indicator.style.opacity = '';
    }, 300);
  }
}
export function setupFindHelpListeners(){
  document.addEventListener('DOMContentLoaded', () => {
  const textarea = document.getElementById('connection-message-input');
  const charCount = document.getElementById('connection-char-count');
  
  if (textarea && charCount) {
    textarea.addEventListener('input', () => {
      charCount.textContent = textarea.value.length;
    });
  }
  
  // Enter key to search in find help
  const helpInput = document.getElementById('help-subject-input');
  if (helpInput) {
    helpInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        document.querySelector('[data-action="search-help"]')?.click();
      }
    });
  }
});
}
export function setupPullToRefresh() {
  const connectionsSection = document.getElementById('connections');
  const refreshIndicator = document.getElementById('connections-refresh-indicator');
  
  if (!connectionsSection || !refreshIndicator) return;
  
  new PullToRefresh(connectionsSection, async () => {
    // Refresh current tab
    const currentTab = connectionState.getCurrentTab();
    const { loadAllConnectionData } = await import('./connection.init.js');
    await loadAllConnectionData();
    loadConnectionTab(currentTab);
  });
}

