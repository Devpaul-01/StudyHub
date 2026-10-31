// ============================================================================
// CONNECTION CARD TEMPLATES
// ============================================================================

import { getReputationIcon, getHealthColor, formatTimeAgo } from './connection.utils.js';
function renderSubjects(subjects) {
  if (!subjects || subjects.length === 0) {
    return '<span class="empty-text">No subjects listed</span>';
  }
  return subjects.slice(0, 5).map(s => `<span class="tag">${s}</span>`).join('');
}

// Helper: Render schedule
function renderSchedule(schedule) {
  if (!schedule || Object.keys(schedule).length === 0) {
    return '<span class="empty-text">No availability shared</span>';
  }
  return Object.entries(schedule).slice(0, 3).map(([day, times]) => 
    `<span class="availability-badge">${day}: ${Array.isArray(times) ? times.join(', ') : times}</span>`
  ).join('');
}


// Helper: Render onboarding subjects


// ============================================================================
// CONNECTED CARD
// ============================================================================
export function createConnectedConnectionCard(connection) {
  const user = connection.user;
  const onboarding = user.onboarding_details || {};
  const repIcon = getReputationIcon(user.reputation_level);
  const healthColor = getHealthColor(connection.health_score || 0);
  
  
  return `
    <div class="connection-card" data-connection-id="${connection.id}" data-user-id="${user.id}">
      
      <!-- Header -->
      <div class="card-header">
        <div class="avatar-container" data-action="view-avatar" data-src="${user.avatar || '/static/default-avatar.png'}">
          <img src="${user.avatar || '/static/default-avatar.png'}"  class="user-avatar" loading="lazy" alt="${user.name}">
        
          ${user.is_online 
            ? '<span class="status-dot online" title="Online now"></span>' 
            : `<span class="status-dot offline">${user.last_active || 'Offline'}</span>`
          }
        </div>

        <div class="user-basic-info">
          <h3 class="user-name" data-action="view-profile" data-user-id="${user.id}">${user.name}</h3>
          <p class="user-username">@${user.username}</p>
          <div class="user-meta-inline">
            ${user.department ? `<span class="meta-badge">${user.department}</span>` : ''}
            ${user.class_level ? `<span class="meta-badge">${user.class_level}</span>` : ''}
          </div>
        </div>

        <div class="advanced-options-wrapper">
          <button class="advanced-options-toggle" data-action="toggle-advanced-connected-options">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="12" cy="5" r="2"></circle>
              <circle cx="12" cy="12" r="2"></circle>
              <circle cx="12" cy="19" r="2"></circle>
            </svg>
          </button>
          <div class="advanced-options hidden">
  

  <button data-action="create-study-session" data-user-id="${user.id}">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M8 2v4"/>
      <path d="M16 2v4"/>
      <rect width="18" height="18" x="3" y="4" rx="2"/>
      <path d="M3 10h18"/>
      <path d="M10 16h4"/>
      <path d="M12 14v4"/>
    </svg>
    Create Study Session
  </button>

  <button data-action="view-study-sessions" data-user-id="${user.id}">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>
      <path d="M8 7h8"/>
      <path d="M8 11h8"/>
    </svg>
    View Study Sessions
  </button>

  <button data-action="view-connection-notes" data-connection-id="${connection.id}">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>
      <path d="M14 2v4a2 2 0 0 0 2 2h4"/>
      <path d="M9 15h6"/>
      <path d="M12 12v6"/>
    </svg>
    Connection Notes
  </button>

  <button data-action="form-thread-with-author" data-user-id="${user.id}">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>
      <path d="M8 12h8"/>
      <path d="M12 8v8"/>
    </svg>
    Form Thread
  </button>

  <button data-action="view-mutual-connections" data-user-id="${user.id}">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
      <circle cx="9" cy="7" r="4"/>
      <path d="M22 21v-2a4 4 0 0 0-3-3.87"/>
      <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
    </svg>
    Mutual Connections
  </button>

  <!-- Batch 2: "Remove connection" was entirely missing from the UI —
       DELETE /connections/remove/<user_id> already existed on the backend
       with nothing calling it, so an unwanted connection could only be
       blocked (which also prevents future contact), never just quietly
       removed. Only on this card — createConnectedConnectionCard — since
       a pending received/sent request isn't a connection yet. -->
  <button data-action="remove-connection" data-user-id="${user.id}" class="danger">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M18 6 6 18"/>
      <path d="m6 6 12 12"/>
    </svg>
    Remove Connection
  </button>

  <button data-action="block-user" data-user-id="${user.id}" class="danger">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="10"/>
      <path d="m4.9 4.9 14.2 14.2"/>
    </svg>
    Block User
  </button>
</div>

        </div>
      </div>

      <!-- Stats -->
      <div class="card-stats-compact">
        <div class="stat-item">
          <span class="stat-value" style="color: ${healthColor}">${connection.health_score || 0}%</span>
          <span class="stat-label">Health</span>
        </div>
        <div class="stat-item">
          <span class="stat-icon">${repIcon}</span>
          <span class="stat-value">${user.reputation}</span>
        </div>
      </div>

      <!-- Primary Actions -->
      <div class="card-actions-primary">
      <button 
  class="btn btn-primary"
  data-action="message-author"
  data-user-id="${user.id}"
  style="
    display:inline-flex !important;
    align-items:center !important;
    gap:6px !important;
    background:linear-gradient(135deg,#6366f1,#8b5cf6,#ec4899) !important;
    color:#ffffff !important;
    border:none !important;
    padding:8px 14px !important;
    border-radius:10px !important;
    font-size:13px !important;
    font-weight:600 !important;
    cursor:pointer !important;
    box-shadow:0 4px 14px rgba(139,92,246,0.35) !important;
    transition:all 0.2s ease !important;
  "
  onmouseover="this.style.filter='brightness(1.1)'"
  onmouseout="this.style.filter='brightness(1)'"
>
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
  </svg>
  Message
</button>
      </div>

      <!-- Toggle Details -->
      <button class="btn-toggle-details" data-action="toggle-details">
        <span class="toggle-text">Show Details</span>
        <svg class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>

      <!-- Collapsible Details -->
      <div class="card-details-expandable hidden">
        <div class="detail-section">
          ${user.bio ? `<p class="user-bio">${user.bio}</p>` : ''}
        </div>

        ${onboarding.strong_subjects && onboarding.strong_subjects.length > 0 ? `
        <div class="detail-section">
          <h4 class="section-title">
            <svg class="icon-sm" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
            </svg>
            Strong In
          </h4>
          <div class="tags-list">
            ${renderSubjects(onboarding.strong_subjects)}
          </div>
        </div>
        ` : ''}

        ${onboarding.study_schedule ? `
        <div class="detail-section">
          <h4 class="section-title">
            <svg class="icon-sm" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="12 6 12 12 16 14"></polyline>
            </svg>
            Usually Available
          </h4>
          <div class="availability-grid">
            ${renderSchedule(onboarding.study_schedule)}
          </div>
        </div>
        ` : ''}

        ${connection.suggestion ? `
        <div class="detail-section">
          <p class="health-suggestion">${connection.suggestion}</p>
        </div>
        ` : ''}
      </div>
    </div>
  `;
}

// ============================================================================
// RECEIVED CARD
// ============================================================================
export function createReceivedConnectionCard(connection) {
  const user = connection.user;
  const onboarding = user.onboarding_details || {};
  const repIcon = getReputationIcon(user.reputation_level);
  
  return `
    <div class="connection-card" data-request-id="${connection.request_id}" data-user-id="${user.id}">
      
      <!-- Header -->
      <div class="card-header">
        <div class="avatar-container" data-action="view-avatar" data-src="${user.avatar || '/static/default-avatar.png'}" >
          <img src="${user.avatar || '/static/default-avatar.png'}"  class="user-avatar" loading="lazy" alt="${user.name}">
          ${user.is_online 
            ? '<span class="status-dot online"></span>' 
            : `<span class="status-dot offline">${user.last_active || 'Offline'}</span>`
          }
        </div>

        <div class="user-basic-info">
          <h3 class="user-name">${user.name}</h3>
          <p class="user-username">@${user.username}</p>
          <div class="user-meta-inline">
            ${user.department ? `<span class="meta-badge">${user.department}</span>` : ''}
            ${user.class_level ? `<span class="meta-badge">${user.class_level}</span>` : ''}
          </div>
        </div>

        <div class="advanced-options-wrapper">
          <button class="advanced-options-toggle" data-action="toggle-advanced-options">⋯</button>
          <div class="advanced-options hidden">
  
  <button data-action="view-mutual-connections" data-user-id="${user.id}">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
      <circle cx="9" cy="7" r="4"></circle>
      <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
      <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
    </svg>
    Mutual Connections
  </button>
  
  <button data-action="block-user" data-user-id="${user.id}" class="danger">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="10"></circle>
      <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line>
    </svg>
    Block
  </button>
</div>
        </div>
      </div>

      <!-- Message from requester -->
      ${connection.message ? `
      <div class="connection-message">
        <p>"${connection.message}"</p>
        <small>${formatTimeAgo(connection.requested_at)}</small>
      </div>
      ` : ''}

      <!-- Stats -->
      <div class="card-stats-compact">
        <div class="stat-item">
          <span class="stat-icon">${repIcon}</span>
          <span class="stat-value">${user.reputation}</span>
        </div>
        ${connection.mutuals_count > 0 ? `
        <div class="stat-item">
          <span class="stat-label">${connection.mutuals_count} mutual${connection.mutuals_count !== 1 ? 's' : ''}</span>
        </div>
        ` : ''}
      </div>

      <!-- Primary Actions -->
      <div class="card-actions-primary">
        <button class="btn btn-secondary" data-action="reject-request" data-connection-id="${connection.request_id}">
          Reject
        </button>
        <button class="btn btn-primary" data-action="accept-request" data-connection-id="${connection.request_id}">
          Accept
        </button>
      </div>

      <!-- Toggle Details -->
      <button class="btn-toggle-details" data-action="toggle-details">
        <span class="toggle-text">Show Details</span>
        <svg class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>

      <!-- Collapsible Details -->
      <div class="card-details-expandable hidden">
        <div class="detail-section">
          ${user.bio ? `<p class="user-bio">${user.bio}</p>` : ''}
        </div>

        ${onboarding.strong_subjects && onboarding.strong_subjects.length > 0 ? `
        <div class="detail-section">
          <h4 class="section-title">Strong In</h4>
          <div class="tags-list">
            ${renderSubjects(onboarding.strong_subjects)}
          </div>
        </div>
        ` : ''}

        ${onboarding.study_schedule ? `
        <div class="detail-section">
          <h4 class="section-title">Usually Available</h4>
          <div class="availability-grid">
            ${renderSchedule(onboarding.study_schedule)}
          </div>
        </div>
        ` : ''}
      </div>
    </div>
  `;
}

// ============================================================================
// SENT CARD
// ============================================================================
export function createSentConnectionCard(connection) {
  const user = connection.user;
  const onboarding = user.onboarding_details || {};
  const repIcon = getReputationIcon(user.reputation_level);
  
  return `
    <div class="connection-card" data-request-id="${connection.request_id}" data-user-id="${user.id}">
      
      <!-- Header -->
      <div class="card-header">
        <div class="avatar-container" data-action="view-avatar" data-src="${user.avatar || '/static/default-avatar.png'}" >
          <img src="${user.avatar || '/static/default-avatar.png'}"  class="user-avatar" loading="lazy" alt="${user.name}">
          ${user.is_online 
            ? '<span class="status-dot online"></span>' 
            : `<span class="status-dot offline">${user.last_active || 'Offline'}</span>`
          }
        </div>

        <div class="user-basic-info">
          <h3 class="user-name">${user.name}</h3>
          <p class="user-username">@${user.username}</p>
          <div class="user-meta-inline">
            ${user.department ? `<span class="meta-badge">${user.department}</span>` : ''}
            ${user.class_level ? `<span class="meta-badge">${user.class_level}</span>` : ''}
          </div>
        </div>

        <div class="advanced-options-wrapper">
          <button class="advanced-options-toggle" data-action="toggle-advanced-options">⋯</button>
          <div class="advanced-options hidden">
  
  
  <button data-action="block-user" data-user-id="${user.id}" class="danger">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="10"></circle>
      <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line>
    </svg>
    Block
  </button>
</div>
        </div>
      </div>

      <!-- Your message -->
      ${connection.your_message ? `
      <div class="connection-message">
        <p>Your message: "${connection.your_message}"</p>
        <small>Sent ${formatTimeAgo(connection.requested_at)}</small>
      </div>
      ` : ''}

      <!-- Stats -->
      <div class="card-stats-compact">
        <div class="stat-item">
          <span class="stat-icon">${repIcon}</span>
          <span class="stat-value">${user.reputation}</span>
        </div>
      </div>

      <!-- Primary Actions -->
      <div class="card-actions-primary">
        <button class="btn btn-secondary" data-action="cancel-request" data-connection-id="${connection.request_id}">
          Cancel Request
        </button>
      </div>

      <!-- Toggle Details -->
      <button class="btn-toggle-details" data-action="toggle-details">
        <span class="toggle-text">Show Details</span>
        <svg class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>

      <!-- Collapsible Details -->
      <div class="card-details-expandable hidden">
        <div class="detail-section">
          ${user.bio ? `<p class="user-bio">${user.bio}</p>` : ''}
        </div>
      </div>
    </div>
  `;
}

// ============================================================================
// TEMPLATE FIXES
// ============================================================================

// Fix 1: Discovery Card - The issue is the card layout doesn't match others
export function createDiscoveryConnectionCard(connection) {
  const user = connection.user;
  const onboarding = user.onboarding_details || {};
  const repIcon = getReputationIcon(user.reputation_level);
  const sampleMutuals = connection.sample_mutuals || [];
  
  return `
    <div class="connection-card" data-user-id="${user.id}">
      
      <!-- Header -->
      <div class="card-header">
        <div class="avatar-container" data-action="view-avatar" data-src="${user.avatar || '/static/default-avatar.png'}">
          <img src="${user.avatar || '/static/default-avatar.png'}" class="user-avatar" loading="lazy" alt="${user.name}">
          ${user.is_online 
            ? '<span class="status-dot online"></span>' 
            : `<span class="status-dot offline"></span>`
          }
        </div>

        <div class="user-basic-info">
          <h3 class="user-name" data-action="view-profile" data-user-id="${user.id}">${user.name}</h3>
          <p class="user-username">@${user.username}</p>
          <div class="user-meta-inline">
            ${user.department ? `<span class="meta-badge">${user.department}</span>` : ''}
            ${user.class_level ? `<span class="meta-badge">${user.class_level}</span>` : ''}
          </div>
        </div>

        <div class="advanced-options-wrapper">
          <button class="advanced-options-toggle" data-action="toggle-advanced-options">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="12" cy="5" r="2"></circle>
              <circle cx="12" cy="12" r="2"></circle>
              <circle cx="12" cy="19" r="2"></circle>
            </svg>
          </button>
          <div class="advanced-options hidden">
            
            
            <button data-action="view-mutual-connections" data-user-id="${user.id}">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
                <circle cx="9" cy="7" r="4"></circle>
                <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
                <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
              </svg>
              View All Mutuals
            </button>
          </div>
        </div>
      </div>

      <!-- Mutual connections preview -->
      ${sampleMutuals.length > 0 ? `
      <div class="mutual-preview">
        <p class="mutual-label">${connection.mutuals_count} mutual connection${connection.mutuals_count !== 1 ? 's' : ''}</p>
        <div class="mutual-avatars">
          ${sampleMutuals.map(m => `
            <img data-action='view-avatar' data-username=${m.username} src="${m.avatar || '/static/default-avatar.png'}" alt="${m.name}" class="mutual-avatar" title="${m.name}">
          `).join('')}
        </div>
      </div>
      ` : ''}

      <!-- Stats -->
      <div class="card-stats-compact">
        <div class="stat-item">
          <span class="stat-icon">${repIcon}</span>
          <span class="stat-value">${user.reputation}</span>
          <span class="stat-label">Reputation</span>
        </div>
      </div>

      <!-- Primary Actions -->
      <div class="card-actions-primary">
        <button class="btn btn-primary" data-user-name="${user.username}" data-action="open-connection-request" data-user-id="${user.id}">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
            <circle cx="8.5" cy="7" r="4"></circle>
            <line x1="20" y1="8" x2="20" y2="14"></line>
            <line x1="23" y1="11" x2="17" y2="11"></line>
          </svg>
          Connect
        </button>
      </div>

      <!-- Toggle Details -->
      <button class="btn-toggle-details" data-action="toggle-details">
        <span class="toggle-text">Show Details</span>
        <svg class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>

      <!-- Collapsible Details -->
      <div class="card-details-expandable hidden">
        <div class="detail-section">
          ${user.bio ? `<p class="user-bio">${user.bio}</p>` : ''}
        </div>

        ${onboarding.strong_subjects && onboarding.strong_subjects.length > 0 ? `
        <div class="detail-section">
          <h4 class="section-title">Strong In</h4>
          <div class="tags-list">
            ${renderSubjects(onboarding.strong_subjects)}
          </div>
        </div>
        ` : ''}
      </div>
    </div>
  `;
}

export function createSugguestionConnectionCard(connection) {
  const user = connection.user;
  const onboarding = user.onboarding_details || {};
  const repIcon = getReputationIcon(user.reputation_level);
  const reasons = connection.reasons || [];
  
  return `
    <div class="connection-card" data-user-id="${user.id}">
      
      <!-- Header -->
      <div class="card-header">
        <div class="avatar-container" data-action="view-avatar" data-src="${user.avatar || '/static/default-avatar.png'}">
          <img src="${user.avatar || '/static/default-avatar.png'}" class="user-avatar" loading="lazy" alt="${user.name}">
          ${user.is_online 
            ? '<span class="status-dot online"></span>' 
            : `<span class="status-dot offline"></span>`
          }
        </div>

        <div class="user-basic-info">
          <h3 class="user-name" data-action="view-profile" data-user-id="${user.id}">${user.name}</h3>
          <p class="user-username">@${user.username}</p>
          <div class="user-meta-inline">
            ${user.department ? `<span class="meta-badge">${user.department}</span>` : ''}
            ${user.class_level ? `<span class="meta-badge">${user.class_level}</span>` : ''}
          </div>
        </div>

        <div class="advanced-options-wrapper">
          <button class="advanced-options-toggle" data-action="toggle-advanced-options">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="12" cy="5" r="2"></circle>
              <circle cx="12" cy="12" r="2"></circle>
              <circle cx="12" cy="19" r="2"></circle>
            </svg>
          </button>
          <div class="advanced-options hidden">
            
          </div>
        </div>
      </div>

      <!-- Match score -->
      ${connection.match_score ? `
      <div class="match-score">
        <span class="match-label">Match:</span>
        <span class="match-value">${connection.match_score}%</span>
      </div>
      ` : ''}

      <!-- Reasons -->
      ${reasons.length > 0 ? `
      <div class="suggestion-reasons">
        ${reasons.map(r => `<span class="reason-tag">${r}</span>`).join('')}
      </div>
      ` : ''}

      <!-- Stats -->
      <div class="card-stats-compact">
        <div class="stat-item">
          <span class="stat-icon">${repIcon}</span>
          <span class="stat-value">${user.reputation}</span>
          <span class="stat-label">Reputation</span>
        </div>
        ${connection.mutuals_count > 0 ? `
        <div class="stat-item">
          <span class="stat-value">${connection.mutuals_count}</span>
          <span class="stat-label">Mutual${connection.mutuals_count !== 1 ? 's' : ''}</span>
        </div>
        ` : ''}
      </div>

      <!-- Primary Actions -->
      <div class="card-actions-primary">
        <button class="btn btn-primary"data-user-name="${user.username}" data-action="open-connection-request" data-user-id="${user.id}">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
            <circle cx="8.5" cy="7" r="4"></circle>
            <line x1="20" y1="8" x2="20" y2="14"></line>
            <line x1="23" y1="11" x2="17" y2="11"></line>
          </svg>
          Connect
        </button>
      </div>

      <!-- Toggle Details -->
      <button class="btn-toggle-details" data-action="toggle-details">
        <span class="toggle-text">Show Details</span>
        <svg class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>

      <!-- Collapsible Details -->
      <div class="card-details-expandable hidden">
        <div class="detail-section">
          ${user.bio ? `<p class="user-bio">${user.bio}</p>` : ''}
        </div>

        ${onboarding.subjects && onboarding.subjects.length > 0 ? `
        <div class="detail-section">
          <h4 class="section-title">Subjects</h4>
          <div class="tags-list">
            ${onboarding.subjects.map(s => `<span class="tag">${s}</span>`).join('')}
          </div>
        </div>
        ` : ''}

      

        ${onboarding.study_style ? `
        <div class="detail-section">
          <h4 class="section-title">Study Style</h4>
          <p class="detail-text">${onboarding.study_style}</p>
        </div>
        ` : ''}
      </div>
    </div>
  `;
}




// ============================================================================
// BLOCKED USER CARD
// ============================================================================
export function createBlockedUserCard(user) {
  const repIcon = getReputationIcon(user.reputation_level);
  const blockedDate = user.blocked_at ? new Date(user.blocked_at).toLocaleDateString() : '—';
  
  return `
    <div class="connection-card blocked" data-user-id="${user.id}" data-connection-id="${user.connection_id}">
      
      <!-- Header -->
      <div class="card-header">
        <div class="avatar-container">
          <img src="${user.avatar}" class="user-avatar" loading="lazy" alt="${user.name}">
          <span class="status-dot blocked" title="Blocked"></span>
        </div>

        <div class="user-basic-info">
          <h3 class="user-name">${user.name}</h3>
          <p class="user-username">@${user.username}</p>
          <div class="user-meta-inline">
            ${user.department ? `<span class="meta-badge">${user.department}</span>` : ''}
            ${user.class_level ? `<span class="meta-badge">${user.class_level}</span>` : ''}
          </div>
        </div>
      </div>

      <!-- Info -->
      <div class="card-details-expandable">
        <div class="detail-section">
          ${user.bio ? `<p class="user-bio">${user.bio}</p>` : ''}
        </div>

        <div class="detail-section muted">
          <small>Blocked on ${blockedDate}</small>
        </div>
      </div>

      <!-- Stats -->
      <div class="card-stats-compact">
        <div class="stat-item">
          <span class="stat-icon">${repIcon}</span>
          <span class="stat-value">${user.reputation}</span>
        </div>
      </div>

      <!-- Actions -->
      <div class="card-actions-primary">
        <button class="btn btn-secondary" data-action="unblock-request" data-user-id="${user.id}" data-connection-id="${user.connection_id}">
          Unblock
        </button>
      </div>
    </div>
  `;
}

// ============================================================================
// SEARCH RESULT CARD
// ============================================================================
export function createSearchResultCard(user) {
  const repIcon = getReputationIcon(user.reputation_level);
  
  return `
    <div class="connection-card search-result" data-user-id="${user.id}">
      
      <!-- Header -->
      <div class="card-header">
        <div class="avatar-container" data-action="view-avatar" data-src="${user.avatar}">
          <img src="${user.avatar}" class="user-avatar" loading="lazy" alt="${user.name}">
          ${user.is_online 
            ? '<span class="status-dot online"></span>' 
            : `<span class="status-dot offline">${user.last_active || 'Offline'}</span>`
          }
        </div>

        <div class="user-basic-info">
          <h3 class="user-name">${user.name}</h3>
          <p class="user-username">@${user.username}</p>
          <div class="user-meta-inline">
            ${user.department ? `<span class="meta-badge">${user.department}</span>` : ''}
            ${user.class_level ? `<span class="meta-badge">${user.class_level}</span>` : ''}
          </div>
        </div>
      </div>

      <!-- Bio -->
      <div class="detail-section">
        ${user.bio ? `<p class="user-bio">${user.bio}</p>` : ''}
      </div>

      <!-- Stats -->
      <div class="card-stats-compact">
        <div class="stat-item">
          <span class="stat-icon">${repIcon}</span>
          <span class="stat-value">${user.reputation}</span>
        </div>
      </div>

      <!-- Actions based on connection status -->
      <div class="card-actions-primary">
        ${renderConnectionButton(user)}
      </div>
    </div>
  `;
}

// Helper: Render connection button based on status
function renderConnectionButton(user) {
  const indigoGradient = "background: linear-gradient(135deg, #6366f1, #8b5cf6, #ec4899) !important; color: #ffffff !important; border: none !important; border-radius: 10px !important; padding: 8px 16px !important; font-weight: 600 !important; cursor: pointer !important; box-shadow: 0 4px 14px rgba(139, 92, 246, 0.35) !important; transition: all 0.2s ease !important;";

  switch (user.connection_status) {
    case 'connected':
      return `<button data-action='message-author' data-user-id='${user.id}' class="btn btn-primary" style="${indigoGradient}">Message</button>`;
    
    case 'pending_sent':
      return `<button class="btn btn-secondary" data-action="cancel-request" data-connection-id="${user.connection_id}" style="${indigoGradient}">Cancel Request</button>`;
    
    case 'pending_received':
      return `
        <button class="btn btn-secondary" data-action="reject-request" data-connection-id="${user.connection_id}" style="${indigoGradient}">Reject</button>
        <button class="btn btn-primary" data-action="accept-request" data-connection-id="${user.connection_id}" style="${indigoGradient}">Accept</button>
      `;
    
    case 'blocked':
      return `<button class="btn btn-secondary" disabled style="${indigoGradient}">Blocked</button>`;
    
    default:
      return `<button data-user-name="${user.username}" class="btn btn-primary" data-action="open-connection-request" data-user-id="${user.id}" style="${indigoGradient}">Connect</button>`;
  }
}

// ============================================================================
// MUTUAL CONNECTION CARD (for modal)
// ============================================================================
export function createMutualConnectionCard(connection) {
  const user = connection.user;
  const repIcon = getReputationIcon(user.reputation_level);
  
  return `
    <div class="connection-card mutual" data-connection-id="${connection.id}" data-user-id="${user.id}">
      
      <div class="card-header">
        <div class="avatar-container" data-action="view-avatar" src="${connection.avatar || '/static/default-avatar.png'}">
          <img src="${connection.avatar || '/static/default-avatar.png'}"  class="user-avatar" loading="lazy" alt="${user.name}">
          ${user.is_online 
            ? '<span class="status-dot online"></span>' 
            : `<span class="status-dot offline">${user.last_active || 'Offline'}</span>`
          }
        </div>
        

        <div class="user-basic-info">
          <h3 class="user-name">${user.name}</h3>
          <p class="user-username">@${user.username}</p>
          <div class="user-meta-inline">
            <span class="meta-badge">${user.department || '—'}</span>
          </div>
        </div>
      </div>

      <div class="card-stats-compact">
        <div class="stat-item">
          <span class="stat-icon">${repIcon}</span>
          <span class="stat-value">${user.reputation}</span>
        </div>
      </div>

      <div class="card-actions-primary">
        <button class="btn btn-primary" data-action="message-author" data-user-id="${user.id}">
          Message
        </button>
      </div>
    </div>
  `;
}