// ============================================================================
// CONNECTION EVENT HANDLERS
// ============================================================================

import { connectionContainer } from './connection.constants.js';
import { ConnectionAPI } from './connection.api.js';
import { connectionState } from './connection.state.js';
import {
  createSearchResultCard, createBlockedUserCard, createMutualConnectionCard,
  buildAvailableNowList, buildConnectionDetails,
} from './connection.templates.js';
import { getLoadingSkeleton, showEmptyState } from './connection.utils.js';
import { renderConnectionTab } from './connection.render.js';
import { loadConnectionTab } from './connection.init.js';

export function ConnectionEventListeners(){
  // study-sessions feature (and its upload listeners) removed from main for now
}


export async function sendConnectionRequest(userId, message = null, button = null) {
  // Store original button state if button provided
  let originalText = '';
  if (button) {
    button.disabled = true;
    originalText = button.textContent;
    button.textContent = 'Sending...';
  }
  
  try {
    const response = await ConnectionAPI.sendConnectionRequest(parseInt(userId), message);
    
    if (response.status === 'success') {
      // Handle instant connection
      if (response.data.is_instant) {
        showToast('🎉 Instant connection! You are now connected.', 'success');
        
        if (button) {
          button.textContent = 'Connected';
          button.classList.remove('btn-primary');
          button.classList.add('btn-secondary');
        }
        
        // Move card from suggestions to connected
        const card = button?.closest('.connection-card');
        if (card) {
          setTimeout(() => card.remove(), 1500);
        }
      } 
      // Handle regular connection request
      else {
        
        if (button) {
          button.textContent = 'Request Sent';
        }
        showToast("Request sent");
        
        // Move to sent tab
        const card = button?.closest('.connection-card');
        if (card) {
          setTimeout(() => card.remove(), 1500);
        }
        const card2 = target.closest('.user-card')
        if (card2) {
          setTimeout(() => card.remove(), 1500);
        }
      }
      
      // Refresh current tab
      const currentTab = connectionState.getCurrentTab();
      await loadAllConnectionData();
      renderConnectionTab(currentTab);
      
      return response;
    } else {
      showToast(response.message || 'Failed to send request', 'error');
      
      // Restore button state
      if (button) {
        button.disabled = false;
        button.textContent = originalText;
      }
      
      return response;
    }
  } catch (error) {
    
    
    // Restore button state
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
    
  
  }
}




export async function acceptRequest(connectionId, target) {
  target.disabled = true;
  const originalText = target.textContent;
  target.textContent = 'Accepting...';
  
  try {
    const response = await ConnectionAPI.acceptRequest(connectionId);
    
    if (response.status === 'success') {
      
      // Remove card with animation
      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to accept', 'error');
      target.disabled = false;
      target.textContent = originalText;
    }
  } catch (error) {
    console.error('Accept request error:', error);
    showToast('Failed to accept request', 'error');
    target.disabled = false;
    target.textContent = originalText;
  }
}

export async function rejectRequest(connectionId, target) {
  target.disabled = true;
  const originalText = target.textContent;
  target.textContent = 'Rejecting...';
  
  try {
    const response = await ConnectionAPI.rejectRequest(connectionId);
    
    if (response.status === 'success') {
      
      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to reject', 'error');
      target.disabled = false;
      target.textContent = originalText;
    }
  } catch (error) {
    console.error('Reject request error:', error);
    showToast('Failed to reject request', 'error');
    target.disabled = false;
    target.textContent = originalText;
  }
}

export async function cancelRequest(connectionId, target) {
  target.disabled = true;
  const originalText = target.textContent;
  target.textContent = 'Cancelling...';
  
  try {
    const response = await ConnectionAPI.cancelRequest(connectionId);
    
    if (response.status === 'success') {
      
      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to cancel', 'error');
      target.disabled = false;
      target.textContent = originalText;
    }
  } catch (error) {
    console.error('Cancel request error:', error);
    showToast('Failed to cancel request', 'error');
    target.disabled = false;
    target.textContent = originalText;
  }
}

// ============================================================================
// BLOCK/UNBLOCK
// ============================================================================



// ============================================================================
// BATCH 2: REMOVE CONNECTION ("unfriend")
// ============================================================================

export async function removeConnectionRequest(userId, target) {
  if (!confirm('Remove this connection? You can send a new request later if you change your mind.')) return;

  target.disabled = true;
  const originalText = target.textContent;
  target.textContent = 'Removing...';

  try {
    const response = await ConnectionAPI.removeConnection(userId);

    if (response.status === 'success') {
      showToast('Connection removed', 'success');

      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to remove connection', 'error');
      target.disabled = false;
      target.textContent = originalText;
    }
  } catch (error) {
    console.error('Remove connection error:', error);
    showToast('Failed to remove connection', 'error');
    target.disabled = false;
    target.textContent = originalText;
  }
}

export async function blockRequest(userId, target) {
  if (!confirm('Are you sure you want to block this user?')) return;
  
  try {
    const response = await ConnectionAPI.blockUser(userId);
    
    if (response.status === 'success') {
      showToast('User blocked', 'success');
      
      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to block user', 'error');
    }
  } catch (error) {
    console.error('Block user error:', error);
    showToast('Failed to block user', 'error');
  }
}

export async function unblockRequest(userId, target) {
  target.disabled = true;
  const originalText = target.textContent;
  target.textContent = 'Unblocking...';
  
  try {
    const response = await ConnectionAPI.unblockUser(userId);
    
    if (response.status === 'success') {
      
      const card = target.closest('.connection-card');
      if (card) {
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
      }
    } else {
      showToast(response.message || 'Failed to unblock', 'error');
      target.disabled = false;
      target.textContent = originalText;
    }
  } catch (error) {
    console.error('Unblock user error:', error);
    showToast('Failed to unblock user', 'error');
    target.disabled = false;
    target.textContent = originalText;
  }
}

export async function viewBlockedUsers() {
  const modal = connectionContainer.querySelector('.blocked-users-modal');
  if (!modal) {
    console.error('Blocked users modal not found');
    return;
  }
  
  const contentContainer = modal.querySelector('.modal-content');
  contentContainer.innerHTML = getLoadingSkeleton();
  
  modal.classList.remove('hidden');
  
  try {
    const blockedUsers = await ConnectionAPI.getBlockedUsers();
    
    if (!blockedUsers || blockedUsers.length === 0) {
      contentContainer.innerHTML = `
        <div class="empty-state">
          <p>No blocked users</p>
        </div>
      `;
      return;
    }
    
    contentContainer.innerHTML = blockedUsers.map(user => createBlockedUserCard(user)).join('');
  } catch (error) {
    console.error('View blocked users error:', error);
    contentContainer.innerHTML = `
      <div class="empty-state">
        <p>Failed to load blocked users</p>
      </div>
    `;
  }
}

// ============================================================================
// MUTUAL CONNECTIONS
// ============================================================================

export async function showMutualConnections(userId) {
  const modal = document.getElementById('mutual-connections-modal');;
  if (!modal) {
    console.error('Mutual connections modal not found');
    return;
  }
  
  const contentContainer = modal.querySelector('.modal-content');
  contentContainer.innerHTML = getLoadingSkeleton();
  
  openModal('mutual-connections-modal');
  
  try {
    const response = await ConnectionAPI.getMutualConnections(userId);
    
    if (!response.data.mutual_connections || response.data.mutual_connections.length === 0) {
      contentContainer.innerHTML = `
        <div class="empty-state">
          <p>No mutual connections</p>
        </div>
      `;
      return;
    }
  
    
    contentContainer.innerHTML = response.data.mutual_connections
      .map(connection => createMutualConnectionCard(connection))
      .join('');
  } catch (error) {
    console.error('Show mutual connections error:', error);
    showToast(error.message, 'error');
    
    contentContainer.innerHTML = `
      <div class="empty-state">
        <p>Failed to load mutual connections</p>
      </div>
    `;
  }
  finally{
    closeModal('userActionsModal');
  }
}
export async function viewUserOverview(userId) {
  const modal = connectionContainer.querySelector('.user-overview-modal');
  if (!modal) {
    console.error('User overview modal not found');
    return;
  }
  
  modal.classList.remove('hidden');
  
  const loadingSection = modal.querySelector('#loading-section');
  const aiSection = modal.querySelector('#ai-section');
  const aiText = modal.querySelector('#ai-text');
  const streamingIndicator = modal.querySelector('#streaming-indicator');
  const errorContainer = modal.querySelector('#error-container');
  
  // Reset state
  loadingSection.style.display = 'block';
  aiSection.style.display = 'none';
  aiText.textContent = '';
  errorContainer.innerHTML = '';
  streamingIndicator.style.display = 'inline-block';
  
  let fullResponse = '';
  
  try {
    const response = await ConnectionAPI.getOverviewStream(userId);
    
    if (!response.ok) {
      throw new Error('Failed to connect');
    }
    
    
    
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    
    // Show AI section
    loadingSection.style.display = 'none';
    aiSection.style.display = 'block';
    
    function readStream() {
      reader.read().then(({ done, value }) => {
        if (done) {
          streamingIndicator.style.display = 'none';
          return;
        }
        
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');
        
        lines.forEach(line => {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              
              // Handle content streaming
              if (data.content) {
                fullResponse += data.content;
                aiText.textContent = fullResponse;
              }
              
              // Handle completion
              if (data.type === 'done') {
                streamingIndicator.style.display = 'none';
                
                if (data.already_connected) {
                  const successMessage = modal.querySelector('#success-message');
                  if (successMessage) {
                    successMessage.innerHTML = '<div class="success-message">✓ You are already connected with this user</div>';
                  }
                }
              }
              
              // Handle errors
              if (data.error) {
                showError(data.error);
              }
            } catch (e) {
              console.error('Parse error:', e, line);
            }
          }
        });
        
        readStream();
      }).catch(err => {
        console.error('Stream error:', err);
        showError('Connection lost. Please try again.');
      });
    }
    
    readStream();
  } catch (error) {
    console.error('View overview error:', error);
    showError('Failed to load connection overview. Please try again.');
  }
  function showError(message) {
    errorContainer.innerHTML = `
      <div class="error-message">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
        <span>${message}</span>
      </div>
    `;
    loadingSection.style.display = 'none';
  }
}

// ============================================================================
// SEARCH
// ============================================================================

let searchTimeout = null;

export async function performConnectionSearch(query) {
  const searchResults = connectionContainer.querySelector('#connections-search-results');
  if (!searchResults) return;
  
  if (!query || query.length === 0) {
    searchResults.classList.add('hidden');
    searchResults.innerHTML = '';
    return;
  }
  
  searchResults.innerHTML = getLoadingSkeleton(2);
  searchResults.classList.remove('hidden');
  
  try {
    const results = await ConnectionAPI.searchConnections(query);
    
    if (!results.users || results.users.length === 0) {
      searchResults.innerHTML = `
        <div class="empty-state">
          <p>No results found for "${query}"</p>
        </div>
      `;
      return;
    }
    
    searchResults.innerHTML = results.users
      .map(user => createSearchResultCard(user))
      .join('');
  } catch (error) {
    console.error('Search error:', error);
    searchResults.innerHTML = `
      <div class="empty-state">
        <p>Search failed. Please try again.</p>
      </div>
    `;
  }
}

export function clearConnectionSearchInput() {
  const searchInput = connectionContainer.querySelector('#connections-search-input');
  const searchResults = connectionContainer.querySelector('#connections-search-results');
  const clearBtn = connectionContainer.querySelector('#search-clear-btn');
  
  if (searchInput) searchInput.value = '';
  if (searchResults) {
    searchResults.classList.add('hidden');
    searchResults.innerHTML = '';
  }
  if (clearBtn) clearBtn.classList.add('hidden');
}

// ============================================================================
// UI INTERACTIONS
// ============================================================================

export function toggleConnectionDetails(target) {
  const card = target.closest('.connection-card');
  if (!card) return;
  
  const details = card.querySelector('.card-details-expandable');
  const toggleIcon = target.querySelector('.toggle-icon');
  const toggleText = target.querySelector('.toggle-text');
  
  if (!details) return;
  
  const isHidden = details.classList.contains('hidden');
  
  if (isHidden) {
    details.classList.remove('hidden');
    if (toggleIcon) toggleIcon.style.transform = 'rotate(180deg)';
    if (toggleText) toggleText.textContent = 'Hide Details';
  } else {
    details.classList.add('hidden');
    if (toggleIcon) toggleIcon.style.transform = 'rotate(0deg)';
    if (toggleText) toggleText.textContent = 'Show Details';
  }
}

export function closeAdvancedOptionModals() {
  const allOptions = connectionContainer.querySelectorAll('.advanced-options');
  allOptions.forEach(option => option.classList.add('hidden'));
}

export function showAdvancedOptions(target) {
  closeAdvancedOptionModals();
  
  const option = target.nextElementSibling;
  if (!option) return;
  const isOpen = !option.classList.contains('hidden');
  if(isOpen){
    option.classList.add('hidden');
  }
  else{
    option.classList.remove('hidden');
    
  }
  
  // Add rotation effect to button for visual feedback
  target.classList.toggle('active');
}

export function showUserAvatar(src) {
  const avatarModal = document.getElementById('connection-avatar-modal');
  if (!avatarModal) return;
  
  const img = avatarModal.querySelector('.avatar-modal-image');
  img.src = src || '/static/default-avatar.png';
  
  
  // Show modal
  avatarModal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

// ============================================================================
// ACTIVE CONNECTIONS FILTER
// ============================================================================

export async function showActiveConnections() {
  const connectedSection = connectionContainer.querySelector('#connections-connected');
  if (!connectedSection) return;
  
  connectedSection.innerHTML = getLoadingSkeleton();
  
  try {
    const response = await ConnectionAPI.getOnlineConnections();
    
    if (!response.data || response.data.length === 0) {
      connectedSection.innerHTML = showEmptyState('online');
      return;
    }
    
    // Import createConnectedConnectionCard dynamically
    const { createConnectedConnectionCard } = await import('./connection.templates.js');
    
    connectedSection.innerHTML = response.data
      .map(connection => createConnectedConnectionCard(connection))
      .join('');
  } catch (error) {
    console.error('Show active connections error:', error);
    showToast('Failed to load active connections', 'error');
    connectedSection.innerHTML = showEmptyState('connected');
  }
}

// ============================================================================
// CONNECTION SETTINGS
// ============================================================================

export async function toggleConnectionSetting(target) {
  const isChecked = target.checked;
  
  try {
    await ConnectionAPI.toggleConnectionNotification(isChecked);
  } catch (error) {
    console.error('Toggle setting error:', error);
    showToast('Failed to update settings', 'error');
    target.checked = !isChecked; // Revert
  }
}

// ============================================================================
// FORM THREAD
// ============================================================================

export async function formThread(userId) {
  // Get user details for the thread creation modal
  const card = connectionContainer.querySelector(`[data-user-id="${userId}"]`);
  if (!card) return;
  
  const userName = card.querySelector('.user-name')?.textContent;
  
  // Pre-fill thread modal with member
  const threadModal = document.getElementById('create-thread-modal');
  if (!threadModal) {
    return;
  }
  const threadForm = threadModal.querySelector('#create-thread-form');
  
  threadModal.dataset.memberIds = JSON.stringify([userId]);
  
  
  // Open the modal
  openModal('create-thread-modal');
  
  // Show info toast
}

// ============================================================================
// CONNECTION NOTES
// ============================================================================

export async function viewConnectionNotes(connectionId) {
  const notesModal = connectionContainer.querySelector('.connection-notes-modal');
  if (!notesModal) {
    console.error('Notes modal not found');
    return;
  }
  
  const contentContainer = notesModal.querySelector('.notes-content');
  const notesInput = notesModal.querySelector('.notes-input');
  const saveBtn = notesModal.querySelector('.save-note-btn');
  const emptyState = notesModal.querySelector('.notes-empty-state');
  
  openModal('connection-notes-modal');
  notesModal.dataset.connectionId = connectionId;
  
  // Show loading
  if (contentContainer) contentContainer.style.opacity = '0.5';
  
  try {
    const notes = await ConnectionAPI.getConnectionNotes(connectionId);
    
    if (contentContainer) contentContainer.style.opacity = '1';
    
    // Safely check for notes content
    const hasNotes = notes != null && typeof notes === 'string' && notes.trim().length > 0;
    
    if (hasNotes) {
      // Notes exist - show edit mode
      if (notesInput) {
        notesInput.value = notes;
        notesInput.classList.remove('hidden');
      }
      if (saveBtn) saveBtn.classList.remove('hidden');
      if (emptyState) emptyState.classList.add('hidden');
    } else {
      // No notes - show empty state
      if (notesInput) {
        notesInput.value = '';
        notesInput.classList.add('hidden');
      }
      if (saveBtn) saveBtn.classList.add('hidden');
      if (emptyState) emptyState.classList.remove('hidden');
    }
  } catch (error) {
    console.error('View notes error:', error);
    if (contentContainer) contentContainer.style.opacity = '1';
    
    // Show error state
    if (notesInput) notesInput.classList.add('hidden');
    if (saveBtn) saveBtn.classList.add('hidden');
    if (emptyState) {
      emptyState.classList.remove('hidden');
      emptyState.innerHTML = '<p>Failed to load notes</p>';
    }
  }
}

export async function createOrUpdateNote() {
  const notesModal = connectionContainer.querySelector('.connection-notes-modal');
  if (!notesModal) return;
  
  const connectionId = notesModal.dataset.connectionId;
  const notesInput = notesModal.querySelector('.notes-input');
  const saveBtn = notesModal.querySelector('.save-note-btn');
  
  if (!connectionId || !notesInput) return;
  
  const notes = notesInput.value.trim();
  
  
  
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';
  }
  
  try {
    const response = await ConnectionAPI.createConnectionNote(connectionId, notes);
    
    if (response.status === 'success') {
      closeModal('connection-notes-modal');
    } else {
      showToast(response.message || 'Failed to save notes', 'error');
    }
  } catch (error) {
    console.error('Save notes error:', error);
    showToast('Failed to save notes', 'error');
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save';
    }
  }
}

export function showCreateNoteUI() {
  const notesModal = connectionContainer.querySelector('.connection-notes-modal');
  if (!notesModal) return;
  
  const notesInput = notesModal.querySelector('.notes-input');
  const saveBtn = notesModal.querySelector('.save-note-btn');
  const emptyState = notesModal.querySelector('.notes-empty-state');
  
  if (notesInput) {
    notesInput.classList.remove('hidden');
    notesInput.focus();
  }
  if (saveBtn) saveBtn.classList.remove('hidden');
  if (emptyState) emptyState.classList.add('hidden');
}


export function openConnectionActionsModal(target) {
  const modal = document.getElementById('userActionsModal');
  const container = document.getElementById('modalActionsContainer');
  const advancedOptions = target.nextElementSibling;
  
  if (!modal || !container || !advancedOptions) return;
  
  // Get user info from the card
  const card = target.closest('.connection-card');
  if (!card) return;
  
  const avatar = card.querySelector('.user-avatar')?.src || '/static/default-avatar.png';
  const name = card.querySelector('.user-name')?.textContent || 'User';
  const username = card.querySelector('.user-username')?.textContent || '@user';
  
  // Populate user info in modal
  const modalAvatar = document.getElementById('modalUserAvatar');
  const modalName = document.getElementById('modalUserName');
  const modalUsername = document.getElementById('modalUserUsername');
  
  if (modalAvatar) modalAvatar.src = avatar;
  if (modalName) modalName.textContent = name;
  if (modalUsername) modalUsername.textContent = username;
  
  // Clear and populate actions
  container.innerHTML = '';
  
  // Get all buttons from advanced options
  const buttons = advancedOptions.querySelectorAll('button');
  buttons.forEach(button => {
    const clonedButton = button.cloneNode(true);
    clonedButton.classList.add('user-action-item');
    container.appendChild(clonedButton);
  });
  
  // Show modal
  openModal('userActionsModal');
  document.body.style.overflow = 'hidden';
}

// ============================================================================
// BATCH 3: "Available Now" and "Connection Details"
// ============================================================================
// Both backing routes (GET /connections/available-now and
// GET /connections/<id>/details) already existed with no entry point in
// the UI. Neither has a static modal in feed2.html — like the reputation
// levels modal added in Batch 2, these are built and inserted into the
// page on demand (reusing the same p-modal-* classes other modals use)
// and removed again when closed, rather than adding two more large
// static blocks to the already-huge feed2.html for one-off info screens.

function _removeDynamicModal(id) {
  document.getElementById(id)?.remove();
}

function _openDynamicModal(id, title, bodyHtml, closeAction) {
  _removeDynamicModal(id); // avoid piling up duplicates across repeated opens
  const el = document.createElement('div');
  el.id = id;
  el.className = 'p-modal-overlay hidden';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.innerHTML = `
    <div class="p-modal-box" style="max-width:460px;">
      <div class="p-modal-header">
        <h3 class="p-modal-title">${title}</h3>
        <button data-action="${closeAction}" class="p-modal-close">✕</button>
      </div>
      ${bodyHtml}
    </div>
  `;
  document.body.appendChild(el);
  openModal(id);
}

export async function showAvailableNowModal(subject = '') {
  _openDynamicModal(
    'available-now-modal', '🟢 Available Now',
    `<div style="margin-bottom:0.75rem;">
      <input id="available-now-subject-input" type="text" value="${subject}"
             placeholder="Filter by subject (optional)"
             style="width:100%;padding:0.5rem 0.75rem;border:1px solid var(--border);
                    border-radius:8px;background:var(--bg-primary);color:var(--text-primary);
                    box-sizing:border-box;font-size:0.85rem;">
      <button data-action="refresh-available-now"
              style="margin-top:0.5rem;width:100%;padding:0.5rem;background:var(--accent,#0057FF);
                     color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:0.82rem;">
        Search
      </button>
    </div>
    <div id="available-now-results">Loading…</div>`,
    'close-available-now'
  );
  await refreshAvailableNowResults(subject);
}

export async function refreshAvailableNowResults(subject = '') {
  const resultsEl = document.getElementById('available-now-results');
  if (!resultsEl) return;
  resultsEl.innerHTML = getLoadingSkeleton();
  try {
    const response = await ConnectionAPI.getAvailableNow(subject);
    const list = response?.data?.available_now || [];
    resultsEl.innerHTML = buildAvailableNowList(list, subject);
  } catch (error) {
    console.error('Available now error:', error);
    resultsEl.innerHTML = `<div class="p-empty">Failed to load</div>`;
  }
}

export function closeAvailableNowModal() {
  closeModal('available-now-modal');
  _removeDynamicModal('available-now-modal');
}

export async function showConnectionDetailsModal(connectionId) {
  _openDynamicModal(
    'connection-details-modal', 'Connection Details',
    `<div id="connection-details-body">Loading…</div>`,
    'close-connection-details'
  );
  const bodyEl = document.getElementById('connection-details-body');
  try {
    const response = await ConnectionAPI.getConnectionDetails(connectionId);
    if (bodyEl) bodyEl.innerHTML = buildConnectionDetails(response?.data);
  } catch (error) {
    console.error('Connection details error:', error);
    if (bodyEl) bodyEl.innerHTML = `<div class="p-empty">Failed to load connection details</div>`;
  }
}

export function closeConnectionDetailsModal() {
  closeModal('connection-details-modal');
  _removeDynamicModal('connection-details-modal');
}
