// ============================================================================
// CONNECTION SYSTEM INITIALIZATION
// ============================================================================

import { connectionContainer, POLLING_INTERVAL } from './connection.constants.js';
import { ConnectionAPI } from './connection.api.js';
import { connectionState } from './connection.state.js';
import {setupSearchInput,setupFindHelpListeners, setupPullToRefresh} from './connection.delegation.js';
import {
  ConnectionEventListeners
} from './connection.events.js'


import {
  renderConnectionTab,
  updateFilterButtons,
  updateContainerVisibility,
  updateBadgeCounts,
  showLoadingInTab
} from './connection.render.js';

// ============================================================================
// LOAD ALL CONNECTION DATA
// ============================================================================

export async function loadAllConnectionData() {
  try {
    const data = await ConnectionAPI.loadAllConnectionData();
    
    connectionState.setConnections('received', data.received);
    connectionState.setConnections('sent', data.sent);
    connectionState.setConnections('connected', data.connected);
    connectionState.setConnections('suggestions', data.suggestions);
    connectionState.setConnections('discovery', data.discovery);
    
    return data;
  } catch (error) {
    console.error('Load all connection data error:', error);
    showToast('Failed to load connections', 'error');
    throw error;
  }
}

// ============================================================================
// LOAD CONNECTION BADGES
// ============================================================================

export async function loadConnectionBadges() {
  try {
    const badges = await ConnectionAPI.loadConnectionBadges();
    
    connectionState.setBadge('received', badges.received);
    connectionState.setBadge('sent', badges.sent);
    
    updateBadgeCounts();
  } catch (error) {
    console.error('Load badges error:', error);
  }
}

// Batch 3: lightweight badge next to the "Active Now" toggle — see the
// comment on CONNECTION_ENDPOINTS.ONLINE_COUNT.
export async function loadOnlineCountBadge() {
  const el = document.getElementById('connections-online-count-badge');
  if (!el) return;
  try {
    const response = await ConnectionAPI.getOnlineCount();
    const count = response?.data?.count || 0;
    el.textContent = count > 0 ? count : '';
  } catch (error) {
    console.error('Load online count error:', error);
  }
}

// ============================================================================
// LOAD CONNECTION TAB
// ============================================================================

export async function loadConnectionTab(tab) {
  // Update UI
  updateFilterButtons(tab);
  updateContainerVisibility(tab);
  connectionState.switchTab(tab);
  
  // Check if tab is already loaded
  if (connectionState.isFilterLoaded(tab)) {
    renderConnectionTab(tab);
  } else {
    // Should not happen since we load all data on init
    showLoadingInTab(tab);
    await loadAllConnectionData();
    console.warn(`Tab ${tab} not preloaded`);
  }
  
  // Mark as seen when switching tabs
  markTabAsSeen(tab);
}

// ============================================================================
// MARK TAB AS SEEN
// ============================================================================

async function markTabAsSeen(tab) {
  try {
    if (tab === 'received') {
      await ConnectionAPI.markReceivedSeen();
      connectionState.setBadge('received', 0);
      updateBadgeCounts();
    } else if (tab === 'sent') {
      await ConnectionAPI.markSentSeen();
      connectionState.setBadge('sent', 0);
      updateBadgeCounts();
    }
  } catch (error) {
    console.error('Mark seen error:', error);
  }
}

// ============================================================================
// POLL CONNECTION DATA
// ============================================================================

let pollingInterval = null;

function startPolling() {
  if (pollingInterval) {
    clearInterval(pollingInterval);
  }
  
  pollingInterval = setInterval(async () => {
    try {
      // Only reload if on connections section
      const connectionsSection = document.getElementById('connections');
      if (!connectionsSection || !connectionsSection.classList.contains('active')) {
        return;
      }
      
      // Reload data silently
      await loadAllConnectionData();
      await loadConnectionBadges();
      
      // Re-render current tab
      const currentTab = connectionState.getCurrentTab();
      renderConnectionTab(currentTab);
      
      console.log('✓ Connections data refreshed');
    } catch (error) {
      console.error('Polling error:', error);
    }
  }, POLLING_INTERVAL);
}

function stopPolling() {
  if (pollingInterval) {
    clearInterval(pollingInterval);
    pollingInterval = null;
  }
}

// ============================================================================
// INITIALIZE CONNECTION SYSTEM
// ============================================================================

let isInitialized = false;

export async function initConnectionSystem() {
  if (!connectionContainer) {
    console.error('Connection container not found');
    return;
  }

  // Prevent re-initialization
  if (isInitialized) return;
  isInitialized = true;
  
  console.log('🔄 Initializing connection system...');
  
  try {
    // Load all data
    await loadAllConnectionData();
    await loadConnectionBadges();
    loadOnlineCountBadge(); // Batch 3 — fire and forget, not critical-path
    
    // Load default tab (received)
    loadConnectionTab('received');
    setupSearchInput();
    setupPullToRefresh();
    setupFindHelpListeners();
    ConnectionEventListeners();
    
    // Start polling
    startPolling();
    
    console.log('✓ Connection system initialized');
  } catch (error) {
    console.error('Init connection system error:', error);
    showToast('Failed to initialize connections', 'error');
  }
}

// ============================================================================
// OBSERVE #connections FOR active CLASS — INIT ON ACTIVATION
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
  const connectionsSection = document.getElementById('connections');

  if (!connectionsSection) {
    console.error('#connections element not found');
    return;
  }

  // If already active on load, init immediately
  if (connectionsSection.classList.contains('active')) {
    initConnectionSystem();
  }

  // Watch for the active class being added
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
        const isActive = connectionsSection.classList.contains('active');

        if (isActive) {
          initConnectionSystem();
          startPolling();
        } else {
          stopPolling();
        }
      }
    }
  });

  observer.observe(connectionsSection, {
    attributes: true,
    attributeFilter: ['class'],
  });
});

// Stop polling when tab/window is hidden, resume if #connections is still active
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    stopPolling();
  } else {
    const connectionsSection = document.getElementById('connections');
    if (connectionsSection && connectionsSection.classList.contains('active')) {
      startPolling();
    }
  }
});
