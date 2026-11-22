import { connectionContainer } from './connection.constants.js';
import { connectionState } from './connection.state.js';
import {
  createConnectedConnectionCard,
  createReceivedConnectionCard,
  createSentConnectionCard,
  createSugguestionConnectionCard, // ✅ FIXED TYPO
  createDiscoveryConnectionCard
} from './connection.templates.js';
import { getLoadingSkeleton, showEmptyState, escapeHtml } from './connection.utils.js';

// ============================================================================
// UPDATE UI ELEMENTS
// ============================================================================
export function renderHelpBroadcastSent(helpRequestId, subject, notifiedCount) {
  const results = document.getElementById('find-help-results');
  results.innerHTML = `
    <div style="padding: 16px;">
      <div style="text-align: center; padding: 20px 0 24px;">
        <div style="font-size: 36px; margin-bottom: 8px;">📡</div>
        <h4 style="margin: 0 0 4px 0; font-weight: 600;">Request Sent!</h4>
        <p style="margin: 0; font-size: 13px; opacity: 0.7;">
          Notified ${notifiedCount} connection${notifiedCount !== 1 ? 's' : ''} about your <strong>${escapeHtml(subject)}</strong> request
        </p>
      </div>

      <div id="volunteer-list" style="display: flex; flex-direction: column; gap: 10px;">
        <p style="text-align: center; font-size: 13px; opacity: 0.5; padding: 12px 0;">
          Waiting for volunteers...
        </p>
      </div>
    </div>
  `;

  // Store request id on the container for the socket handler to use
  results.dataset.helpRequestId = helpRequestId;
}

/**
 * Appends a single volunteer card to the volunteer list.
 * Called both on initial load and when socket emits help_volunteer_joined.
 */
export function appendVolunteerCard(volunteer) {
  const list = document.getElementById('volunteer-list');
  if (!list) return;

  // Remove "waiting" placeholder if still there
  const placeholder = list.querySelector('p');
  if (placeholder) placeholder.remove();

  // Avoid duplicate cards
  if (list.querySelector(`[data-volunteer-id="${volunteer.user_id}"]`)) return;
  const emptyState = document.getElementById('volunteers-empty-state');
  if (emptyState) emptyState.style.display = 'none';
  const countBadge = document.getElementById('volunteers-modal-count');
  if (countBadge) {
    countBadge.textContent = parseInt(countBadge.textContent || '0') + 1;
  }

  const card = document.createElement('div');
  card.dataset.volunteerId = volunteer.user_id;
  card.style.cssText = `
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--bg-secondary, #f8f8f8);
  `;
  card.innerHTML = `
    <img
      src="${escapeHtml(volunteer.avatar || '/static/default-avatar.png')}"
      alt="${escapeHtml(volunteer.name)}"
      style="width: 40px; height: 40px; border-radius: 50%; object-fit: cover; flex-shrink: 0;"
    />
    <div style="flex: 1; min-width: 0;">
      <p style="margin: 0; font-weight: 600; font-size: 14px;">${escapeHtml(volunteer.name)}</p>
      <p style="margin: 0; font-size: 12px; opacity: 0.6;">@${escapeHtml(volunteer.username)}</p>
    </div>
    <button
      data-action="message-volunteer"
      data-user-id="${volunteer.user_id}"
      data-user-name="${escapeHtml(volunteer.name)}"
      class="btn btn-sm btn-primary"
      style="flex-shrink: 0;"
    >
      Message
    </button>
  `;
  list.appendChild(card);
}

export function updateFilterButtons(currentTab) {
  if (!connectionContainer) return;
  
  const tabs = connectionContainer.querySelectorAll('.tab-btn');
  tabs.forEach(tab => {
    if (tab.dataset.tab === currentTab) {
      tab.classList.add('active');
    } else {
      tab.classList.remove('active');
    }
  });
}

export function updateContainerVisibility(currentTab) {
  if (!connectionContainer) return;
  
  const sections = connectionContainer.querySelectorAll('.tab-content');
  sections.forEach(section => {
    const sectionType = section.id.replace('connections-', '');
    if (sectionType === currentTab) {
      section.classList.remove('hidden');
    } else {
      section.classList.add('hidden');
    }
  });
  
  const advancedOptions = connectionContainer.querySelector('#connections-filter-bar');
  if (advancedOptions) {
    if (currentTab === 'connected') {
      advancedOptions.classList.remove('hidden');
    } else {
      advancedOptions.classList.add('hidden');
    }
  }
}

export function updateBadgeCounts() {
  if (!connectionContainer) return;
  
  const badges = connectionState.getAllBadges();
  
  const receivedBadge = connectionContainer.querySelector('.tab-btn[data-tab="received"] .tab-count');
  const sentBadge = connectionContainer.querySelector('.tab-btn[data-tab="sent"] .tab-count');
  
  if (receivedBadge) {
    receivedBadge.textContent = badges.received || '';
    receivedBadge.style.display = badges.received > 0 ? 'inline-block' : 'none';
  }
  
  if (sentBadge) {
    sentBadge.textContent = badges.sent || '';
    sentBadge.style.display = badges.sent > 0 ? 'inline-block' : 'none';
  }
}

// ============================================================================
// RENDER CONNECTION TAB
// ============================================================================

export function renderConnectionTab(tab) {
  if (!connectionContainer) {
    console.error("Connection container not found");
    return;
  }
  
  const section = connectionContainer.querySelector(`#connections-${tab}`);
  if (!section) {
    console.error(`Section not found for tab: ${tab}`);
    return;
  }
  
  const connections = connectionState.getConnections(tab);
  if(tab == 'discovery'){
  }
  
  if (!connections || connections.length === 0) {
    section.innerHTML = showEmptyState(tab);
    return;
  }
  
  
  let html = '';
  
  switch (tab) {
    case 'connected':
      html = connections.map(conn => createConnectedConnectionCard(conn)).join('');
      break;
    
    case 'received':
      html = connections.map(conn => createReceivedConnectionCard(conn)).join('');
      break;
    
    case 'sent':
      html = connections.map(conn => createSentConnectionCard(conn)).join('');
      break;
    
    case 'suggestions':
      html = renderSugguestionsGrouped(connections);
      break;
    
    case 'discovery':
      html = connections.map(conn => createDiscoveryConnectionCard(conn)).join('');
      break;
    
    default:
      html = showEmptyState(tab);
  }
  
  section.innerHTML = html;
}

function renderSugguestionsGrouped(suggestionsData) {
  // Check if data is null/undefined
  
  if (!suggestionsData) {
    return showEmptyState('suggestions');
  }

  
  
  
  // Check if data is an array (flat structure)
  if (Array.isArray(suggestionsData)) {
    if (suggestionsData.length === 0) {
      return showEmptyState('suggestions');
    }
    // ✅ FIXED: Use correct function name
    return suggestionsData.map(conn => createSugguestionConnectionCard(conn)).join('');
  }
  
  let html = '';
  
  // Helper function to transform suggestion data
  const transformSuggestion = (item) => {
    return {
      user: {
        ...item.user,
        onboarding_details: item.onboarding_details || {}
      },
      category: item.category,
      mutuals_count: item.mutuals_count || 0,
      match_score: item.match_score || 0,
      reasons: item.reasons || []
    };
  };
  
  // Study Partners
  if (suggestionsData.study_partners && Array.isArray(suggestionsData.study_partners) && suggestionsData.study_partners.length > 0) {
    
    
    html += `
      <div class="suggestions-section">
        <h3 class="section-header">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
            <circle cx="8.5" cy="7" r="4"></circle>
            <line x1="20" y1="8" x2="20" y2="14"></line>
            <line x1="23" y1="11" x2="17" y2="11"></line>
          </svg>
          Study Partners
          <span class="section-count">${suggestionsData.study_partners.length}</span>
        </h3>
        <div class="suggestions-grid">
          ${suggestionsData.study_partners.map(item => createSugguestionConnectionCard(transformSuggestion(item))).join('')}
        </div>
      </div>
    `;
  }
  
  // Mentors
  if (suggestionsData.mentors && Array.isArray(suggestionsData.mentors) && suggestionsData.mentors.length > 0) {
    html += `
      <div class="suggestions-section">
        <h3 class="section-header">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 2L2 7l10 5 10-5-10-5z"></path>
            <path d="m2 17 10 5 10-5"></path>
            <path d="m2 12 10 5 10-5"></path>
          </svg>
          Mentors
          <span class="section-count">${suggestionsData.mentors.length}</span>
        </h3>
        <div class="suggestions-grid">
          ${suggestionsData.mentors.map(item => createSugguestionConnectionCard(transformSuggestion(item))).join('')}
        </div>
      </div>
    `;
  }
  
  if(!html){
  }
  
  return html || showEmptyState('suggestions');
}

export function renderHelpResults(helpers, subject) {
  const results = document.getElementById('find-help-results');
  
  const html = helpers.map(helper => {
  const expertiseLevel = helper.expertise_level || 0;

  const badge = expertiseLevel >= 3
    ? `
      <span style="
        background:#ede9fe !important;
        color:#6d28d9 !important;
        font-size:11px !important;
        font-weight:600 !important;
        padding:2px 8px !important;
        border-radius:999px !important;
        margin-right:6px !important;
      ">
        ⭐ Expert
      </span>
    `
    : `
      <span style="
        background:#ecfeff !important;
        color:#0e7490 !important;
        font-size:11px !important;
        font-weight:600 !important;
        padding:2px 8px !important;
        border-radius:999px !important;
        margin-right:6px !important;
      ">
        ✓ Experienced
      </span>
    `;

  return `
    <div
      data-action="connect-from-help"
      data-user-id="${helper.user.id}"
      data-user-name="${escapeHtml(helper.user.name)}"
      style="
        display:flex !important;
        align-items:center !important;
        gap:12px !important;
        padding:12px !important;
        border-radius:12px !important;
        background:#ffffff !important;
        box-shadow:0 4px 12px rgba(0,0,0,0.06) !important;
        cursor:pointer !important;
        transition:all 0.2s ease !important;
        margin-bottom:10px !important;
      "
      onmouseover="this.style.background='#f5f3ff'"
      onmouseout="this.style.background='#ffffff'"
    >

      <img
        data-action="view-avatar"
        src="${escapeHtml(helper.user.avatar || '/static/default-avatar.png')}"
        alt="${escapeHtml(helper.user.name)}"
        style="
          width:48px !important;
          height:48px !important;
          border-radius:999px !important;
          object-fit:cover !important;
          border:2px solid #ede9fe !important;
        "
      />

      <div style="flex:1 !important; min-width:0 !important;">
        
        <h4 style="
          margin:0 0 4px 0 !important;
          font-weight:600 !important;
          font-size:14px !important;
          color:#1e1b4b !important;
        ">
          ${escapeHtml(helper.user.name)}
        </h4>

        <div style="
          font-size:12px !important;
          color:#6b7280 !important;
          display:flex !important;
          flex-wrap:wrap !important;
          align-items:center !important;
          gap:4px !important;
        ">
          ${badge}
          ${escapeHtml(helper.user.department || '')}
          ${helper.user.class_level ? '• ' + escapeHtml(helper.user.class_level) : ''}
        </div>

        <p style="
          margin:4px 0 0 0 !important;
          font-size:12px !important;
          color:#9ca3af !important;
        ">
          ${escapeHtml(helper.reason || `Can help with ${subject}`)}
        </p>

      </div>

      <button
        data-action="open-connection-request"
        data-user-id="${helper.user.id}"
        data-type='find-help'
        data-user-name="${escapeHtml(helper.user.username)}"
        style="
          background:linear-gradient(135deg,#6366f1,#8b5cf6,#ec4899) !important;
          color:#ffffff !important;
          border:none !important;
          padding:6px 14px !important;
          border-radius:8px !important;
          font-size:12px !important;
          font-weight:600 !important;
          cursor:pointer !important;
          box-shadow:0 4px 12px rgba(139,92,246,0.35) !important;
          flex-shrink:0 !important;
        "
        onmouseover="this.style.filter='brightness(1.1)'"
        onmouseout="this.style.filter='brightness(1)'"
      >
        Connect
      </button>

    </div>
  `;
}).join('');


  
  results.innerHTML = html;
}

// ============================================================================
// SHOW LOADING
// ============================================================================

export function showLoadingInTab(tab) {
  if (!connectionContainer) return;
  
  const section = connectionContainer.querySelector(`#connections-${tab}`);
  if (section) {
    section.innerHTML = getLoadingSkeleton();
  }
}