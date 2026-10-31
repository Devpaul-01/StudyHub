/**
 * ============================================================================
 * HOMEWORK TEMPLATES
 * HTML templates for homework section components
 * ============================================================================
 */

import {
  formatTimeUntilDue,
  getUrgencyColorClass,
  getDifficultyBadgeClass,
  getStatusBadgeClass,
  getStatusDisplayText,
  getDifficultyEmoji,
  formatDate,
  getFileTypeIcon,
  renderUserStatus,
  truncateText
} from './homework.utils.js';
function renderResourcesSection(resources) {
  if (!resources || resources.length === 0) return '';
  
  return `
    <div class="hw-card-resources">
      <div class="hw-resources-label">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>
          <polyline points="13 2 13 9 20 9"/>
        </svg>
        ${resources.length} ${resources.length === 1 ? 'Resource' : 'Resources'}
      </div>
      <div class="hw-resources-grid">
        ${resources.slice(0, 3).map(resource => `
          <button 
            class="hw-resource-thumb" 
            data-action="view-resource" 
            data-resource-url="${resource.url}" 
            data-resource-type="${resource.type}"
            title="${resource.filename || 'Resource'}"
          >
            ${resource.type === 'image' ? `
              <img src="${resource.url}" alt="${resource.filename}" />
            ` : `
              <div class="hw-resource-icon">
                ${getFileTypeIcon(resource.type)}
              </div>
            `}
          </button>
        `).join('')}
        ${resources.length > 3 ? `
          <div class="hw-resource-more">+${resources.length - 3}</div>
        ` : ''}
      </div>
    </div>
  `;
}

/**
 * Pagination footer shared by "My Work" and "Connections" lists.
 * - If there are more pages: renders an (invisible) scroll sentinel the
 *   IntersectionObserver watches, plus a "loading more" indicator.
 * - If there are no more pages: renders a simple end-of-list message.
 * - If the list is empty: renders nothing (empty state handles that case).
 *
 * `prefix` is 'my' or 'connections' — used to build predictable element ids
 * that homework.render.js hooks into (hw-{prefix}-sentinel / hw-{prefix}-load-more).
 */
function renderPaginationFooter(prefix, hasMore, itemCount) {
  if (!itemCount) return '';

  if (hasMore) {
    return `
      <div id="hw-${prefix}-sentinel" class="hw-scroll-sentinel" style="height:1px;width:100%;"></div>
      <div id="hw-${prefix}-load-more" class="hw-load-more hidden" style="display:flex;align-items:center;justify-content:center;gap:8px;padding:16px 0;color:var(--text-secondary,#6b7280);font-size:0.85rem;">
        <div class="hw-spinner hw-spinner-sm" style="width:16px;height:16px;flex-shrink:0;"></div>
        <span>Loading more...</span>
      </div>
    `;
  }

  return `<div class="hw-list-end" style="text-align:center;padding:16px 0;color:var(--text-secondary,#9ca3af);font-size:0.82rem;">You're all caught up 🎉</div>`;
}

/**
 * Main homework section template
 */
export function renderHomeworkSection() {
  return `
    <div class="hw-container">
      <!-- Header -->
      <div class="hw-header">
        <h1 class="hw-title">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
            <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
          </svg>
          Homework
        </h1>
        <button class="hw-create-btn" data-action="open-create-homework-modal">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          New Assignment
        </button>
      </div>

      <!-- Tabs -->
      <div class="hw-tabs">
        <button class="hw-tab active" data-action="switch-homework-tab" data-tab="my-homework">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
            <line x1="16" y1="13" x2="8" y2="13"/>
            <line x1="16" y1="17" x2="8" y2="17"/>
            <polyline points="10 9 9 9 8 9"/>
          </svg>
          My Homework
        </button>
        <button class="hw-tab" data-action="switch-homework-tab" data-tab="connections-homework">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
            <circle cx="9" cy="7" r="4"/>
            <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
            <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
          </svg>
          Connections Homework
        </button>
        <button class="hw-tab" data-action="switch-homework-tab" data-tab="stats">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="20" x2="18" y2="10"></line>
            <line x1="12" y1="20" x2="12" y2="4"></line>
            <line x1="6" y1="20" x2="6" y2="14"></line>
          </svg>
          Stats
        </button>
      </div>

      <!-- Tab Content -->
      <div class="hw-tab-content">
        <!-- My Homework Tab -->
        <div class="hw-tab-panel active" data-tab-panel="my-homework">
          <div id="my-homework-container"></div>
        </div>

        <!-- Connections Homework Tab -->
        <div class="hw-tab-panel" data-tab-panel="connections-homework">
          <div id="connections-homework-container"></div>
        </div>

        <!-- Stats Tab -->
        <div class="hw-tab-panel" data-tab-panel="stats">
          <div id="stats-container"></div>
        </div>
      </div>
    </div>
  `;
}

/**
 * My homework list template
 * @param {Array} assignments - current set of loaded assignments (may span multiple pages)
 * @param {Object} stats - status counts (always reflects the FULL filtered set, not just loaded pages)
 * @param {boolean} hasMore - whether more pages are available to infinite-scroll load
 */
export function renderMyHomeworkList(assignments, stats, hasMore = false) {
  if (!assignments || assignments.length === 0) {
    return renderEmptyState('my-homework');
  }

  return `
    <!-- Stats Bar -->
    <div class="hw-stats-bar">
      <div class="hw-stat">
        <span class="hw-stat-value">${stats.not_started || 0}</span>
        <span class="hw-stat-label">Not Started</span>
      </div>
      <div class="hw-stat">
        <span class="hw-stat-value">${stats.in_progress || 0}</span>
        <span class="hw-stat-label">In Progress</span>
      </div>
      <div class="hw-stat">
        <span class="hw-stat-value">${stats.overdue || 0}</span>
        <span class="hw-stat-label">Overdue</span>
      </div>
      <div class="hw-stat">
        <span class="hw-stat-value">${stats.completed || 0}</span>
        <span class="hw-stat-label">Completed</span>
      </div>
    </div>

    <!-- Assignments List -->
    <div class="hw-list" id="hw-my-list">
      ${assignments.map(assignment => renderMyHomeworkCard(assignment)).join('')}
    </div>

    ${renderPaginationFooter('my', hasMore, assignments.length)}
  `;
}

/**
 * My homework card template
 */
export function renderMyHomeworkCard(assignment) {
  const urgencyClass = getUrgencyColorClass(assignment.urgency_level);
  const difficultyClass = getDifficultyBadgeClass(assignment.difficulty);
  const statusClass = getStatusBadgeClass(assignment.status);
  
  return `
    <div class="hw-card ${assignment.is_overdue ? 'hw-card-overdue' : ''}" data-assignment-id="${assignment.id}">
      <div class="hw-card-header">
        <div class="hw-card-title-row">
          <h3 class="hw-card-title">${assignment.title}</h3>
          <button class="hw-card-options-btn" data-action="toggle-homework-options" data-assignment-id="${assignment.id}">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="1"/>
              <circle cx="12" cy="5" r="1"/>
              <circle cx="12" cy="19" r="1"/>
            </svg>
          </button>
        </div>
        
        <div class="hw-card-meta">
          ${assignment.subject ? `<span class="hw-card-subject">${assignment.subject}</span>` : ''}
          <span class="hw-badge ${difficultyClass}">
            ${getDifficultyEmoji(assignment.difficulty)} ${assignment.difficulty}
          </span>
          <span class="hw-badge ${statusClass}">
            ${getStatusDisplayText(assignment.status)}
          </span>
        </div>
      </div>

      ${assignment.description ? `
        <p class="hw-card-description">${truncateText(assignment.description, 120)}</p>
      ` : ''}
      ${renderResourcesSection(assignment.resources)}

      <div class="hw-card-footer">
        <div class="hw-card-info">
          <div class="hw-info-item ${urgencyClass}">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <polyline points="12 6 12 12 16 14"/>
            </svg>
            <span>${formatTimeUntilDue(assignment.hours_until_due)}</span>
          </div>
          
          <div class="hw-info-item">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>
              <line x1="16" y1="2" x2="16" y2="6"/>
              <line x1="8" y1="2" x2="8" y2="6"/>
              <line x1="3" y1="10" x2="21" y2="10"/>
            </svg>
            <span>${formatDate(assignment.due_date)}</span>
          </div>

          ${assignment.is_shared ? `
            <div class="hw-info-item hw-shared-indicator">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
                <circle cx="9" cy="7" r="4"/>
                <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
                <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
              </svg>
              <span>${assignment.help_requests_count} ${assignment.help_requests_count === 1 ? 'helper' : 'helpers'}</span>
            </div>
          ` : ''}
        </div>

        <div class="hw-card-actions">
          ${assignment.status === 'not_started' ? `
            <button class="hw-btn hw-btn-secondary" data-action="start-homework" data-assignment-id="${assignment.id}">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polygon points="5 3 19 12 5 21 5 3"/>
              </svg>
              Start
            </button>
          ` : assignment.status === 'in_progress' ? `
            <button class="hw-btn hw-btn-success" data-action="complete-homework" data-assignment-id="${assignment.id}">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
              Mark Complete
            </button>
          ` : `
          
          `}
          
          ${assignment.is_shared ? `
            <button class="hw-btn hw-btn-primary" data-action="view-homework-helpers" data-assignment-id="${assignment.id}">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
                <circle cx="9" cy="7" r="4"/>
                <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
                <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
              </svg>
              View Helpers (${assignment.help_requests_count})
            </button>
          ` : `
            <button class="hw-btn hw-btn-primary" data-action="share-homework-for-help" data-assignment-id="${assignment.id}">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>
                <polyline points="16 6 12 2 8 6"/>
                <line x1="12" y1="2" x2="12" y2="15"/>
              </svg>
              Get Help
            </button>
          `}
        </div>
      </div>
    </div>
  `;
}

/**
 * Connections homework list template
 * @param {Array} homework - current set of loaded homework items (may span multiple pages)
 * @param {boolean} hasMore - whether more pages are available to infinite-scroll load
 */
export function renderConnectionsHomeworkList(homework, hasMore = false) {
  if (!homework || homework.length === 0) {
    return renderEmptyState('connections-homework');
  }

  return `
    <div class="hw-list" id="hw-connections-list">
      ${homework.map(hw => renderConnectionsHomeworkCard(hw)).join('')}
    </div>
    ${renderPaginationFooter('connections', hasMore, homework.length)}
  `;
}

/**
 * Connections homework card template
 */
 
export function renderConnectionsHomeworkCard(hw) {
  const urgencyClass = getUrgencyColorClass(hw.urgency_level);
  const difficultyClass = getDifficultyBadgeClass(hw.difficulty);

  return `
<div class="hw-card hw-card-connection ${hw.is_overdue ? 'hw-card-overdue' : ''}" data-homework-id="${hw.id}">
  
  <div class="hw-card-header">
    <div class="hw-card-title-row">
      <h3 class="hw-card-title">${hw.title}</h3>
    </div>

    <div class="hw-card-meta">  
      ${hw.subject ? `<span class="hw-card-subject">${hw.subject}</span>` : ''}  
      <span class="hw-badge ${difficultyClass}">  
        ${getDifficultyEmoji(hw.difficulty)} ${hw.difficulty}  
      </span>  
    </div>  
  </div>  

  ${hw.description ? `  
    <p class="hw-card-description">${truncateText(hw.description, 120)}</p>  
  ` : ''}  

  ${renderResourcesSection(hw.resources)}  

  <div class="hw-student-info">  
    <img   
      src="${hw.student?.avatar || '/static/default-avatar.png'}"   
      alt="${hw.student?.name}"  
      data-action="view-avatar"  
      class="hw-student-avatar"  
    />  

    <div class="hw-student-details">  
      <div class="hw-student-name">${hw.student?.name || 'Unknown'}</div>  

      <div class="hw-student-meta">  
        ${hw.student?.department ? `<span>${hw.student.department}</span> • ` : ''}  
        ${renderUserStatus(hw.student?.active_details)}  
      </div>  
    </div>  
  </div>  

  <div class="hw-card-footer">  

    <div class="hw-card-info">  

      <div class="hw-info-item ${urgencyClass}">  
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">  
          <circle cx="12" cy="12" r="10"/>  
          <polyline points="12 6 12 12 16 14"/>  
        </svg>  
        <span>${formatTimeUntilDue(hw.hours_until_due)}</span>  
      </div>  

      <div class="hw-info-item">  
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">  
          <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>  
          <line x1="16" y1="2" x2="16" y2="6"/>  
          <line x1="8" y1="2" x2="8" y2="6"/>  
          <line x1="3" y1="10" x2="21" y2="10"/>  
        </svg>  
        <span>${formatDate(hw.due_date)}</span>  
      </div>  

      ${hw.help_count > 0 ? `  
      <div class="hw-info-item">  
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">  
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>  
          <circle cx="9" cy="7" r="4"/>  
          <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>  
          <path d="M16 3.13a4 4 0 0 1 0 7.75"/>  
        </svg>  
        <span>${hw.help_count} ${hw.help_count === 1 ? 'helper' : 'helpers'}</span>  
      </div>  
      ` : ''}

    </div>

    <div class="hw-card-actions">

      ${hw.already_helping ? `

        ${hw.my_help_status === 'pending' ? `

          <button class="hw-btn hw-btn-primary" data-action="open-submit-solution" data-submission-id="${hw.my_submission_id}">
            Submit Solution
          </button>

          <button class="hw-btn hw-btn-danger-outline" data-action="cancel-submission" data-submission-id="${hw.my_submission_id}">
            Cancel
          </button>

        ` : `

          <button class="hw-btn hw-btn-success" data-action="view-my-help-submission" data-submission-id="${hw.my_submission_id}">
            View My Submission
          </button>

        `}

      ` : `

        <button class="hw-btn hw-btn-primary" data-action="offer-help-homework" data-homework-id="${hw.id}">
          Offer Help
        </button>

      `}

    </div>  

  </div>  

</div>  
`;
}

// ============================================================================
// BATCH 3: "Offers on my work" / "Helping Others" tabs
// ============================================================================
// Both back GET /homework/my-help-requests and GET /homework/helping,
// which already existed with no tab in the UI calling either — there was
// no inbox for incoming help offers, and no "what am I currently helping
// with" list.

const STATUS_FILTERS = ['all', 'pending', 'submitted', 'reviewed', 'completed'];

/**
 * Status filter chip row, shared by both new tabs.
 */
export function renderStatusFilterChips(activeStatus, action) {
  return `
    <div class="hw-status-filter-chips" style="display:flex;gap:8px;overflow-x:auto;
         padding:0 0 1rem;-webkit-overflow-scrolling:touch;">
      ${STATUS_FILTERS.map(s => `
        <button type="button" class="hw-status-chip${s === activeStatus ? ' active' : ''}"
                data-action="${action}" data-status="${s}"
                style="flex:0 0 auto;padding:0.35rem 0.75rem;border-radius:999px;font-size:0.78rem;
                       text-transform:capitalize;cursor:pointer;
                       border:1px solid ${s === activeStatus ? 'var(--accent,#0057FF)' : 'var(--border)'};
                       background:${s === activeStatus ? 'var(--accent,#0057FF)' : 'transparent'};
                       color:${s === activeStatus ? '#fff' : 'var(--text-secondary)'};">
          ${s}
        </button>`).join('')}
    </div>`;
}

/**
 * "Offers on my work" list — people who offered to help on my
 * assignments. Each row opens the existing submission-detail modal
 * (openSubmissionDetailModal, in homework.submission.js), which already
 * renders the correct helper-vs-requester view and already has the
 * submit/feedback/cancel actions built in — this tab is just the
 * missing entry point into all of that.
 */
export function renderMyHelpRequestsList(helpRequests) {
  if (!helpRequests || helpRequests.length === 0) {
    return `<div class="hw-empty-state"><p>No one has offered to help with your assignments yet.</p></div>`;
  }
  return helpRequests.map(req => `
    <div class="hw-card" data-action="open-submission-detail" data-submission-id="${req.id}" style="cursor:pointer;">
      <div class="hw-card-header">
        <div class="hw-card-title-row">
          <h3 class="hw-card-title">${req.title || 'Untitled assignment'}</h3>
          <span class="hw-badge ${getStatusBadgeClass(req.status)}">${getStatusDisplayText(req.status)}</span>
        </div>
        <div class="hw-card-meta">
          ${req.subject ? `<span class="hw-card-subject">${req.subject}</span>` : ''}
        </div>
      </div>
      <div class="hw-student-info">
        <img src="${req.helper?.avatar || '/static/default-avatar.png'}" alt="${req.helper?.name || ''}"
             data-action="view-avatar" class="hw-student-avatar" />
        <div class="hw-student-details">
          <div class="hw-student-name">${req.helper?.name || 'Unknown'} offered to help</div>
          <div class="hw-student-meta">
            ${req.helper?.department ? `<span>${req.helper.department}</span> • ` : ''}
            ${renderUserStatus(req.helper?.active_details)}
          </div>
        </div>
      </div>
      <div class="hw-card-footer">
        <div class="hw-card-info">
          <div class="hw-info-item">
            <span>${req.solution_submitted ? '✅ Solution submitted' : '⏳ Awaiting solution'}</span>
          </div>
          ${req.feedback_received ? `<div class="hw-info-item"><span>💬 Feedback given</span></div>` : ''}
        </div>
      </div>
    </div>`
  ).join('');
}

/**
 * "Helping Others" list — assignments I'm currently helping with. Same
 * reuse of the submission-detail modal as above.
 */
export function renderHelpingWithList(helpingWith) {
  if (!helpingWith || helpingWith.length === 0) {
    return `<div class="hw-empty-state"><p>You're not currently helping with anyone's assignment.</p></div>`;
  }
  return helpingWith.map(hw => `
    <div class="hw-card" data-action="open-submission-detail" data-submission-id="${hw.id}" style="cursor:pointer;">
      <div class="hw-card-header">
        <div class="hw-card-title-row">
          <h3 class="hw-card-title">${hw.title || 'Untitled assignment'}</h3>
          <span class="hw-badge ${getStatusBadgeClass(hw.status)}">${getStatusDisplayText(hw.status)}</span>
        </div>
        <div class="hw-card-meta">
          ${hw.subject ? `<span class="hw-card-subject">${hw.subject}</span>` : ''}
          ${hw.difficulty ? `<span class="hw-badge ${getDifficultyBadgeClass(hw.difficulty)}">${getDifficultyEmoji(hw.difficulty)} ${hw.difficulty}</span>` : ''}
        </div>
      </div>
      ${hw.description ? `<p class="hw-card-description">${truncateText(hw.description, 120)}</p>` : ''}
      <div class="hw-student-info">
        <img src="${hw.student?.avatar || '/static/default-avatar.png'}" alt="${hw.student?.name || ''}"
             data-action="view-avatar" class="hw-student-avatar" />
        <div class="hw-student-details">
          <div class="hw-student-name">Helping ${hw.student?.name || 'Unknown'}</div>
          <div class="hw-student-meta">
            ${hw.student?.department ? `<span>${hw.student.department}</span> • ` : ''}
            ${renderUserStatus(hw.student?.active_details)}
          </div>
        </div>
      </div>
      <div class="hw-card-footer">
        <div class="hw-card-info">
          ${hw.assignment_due_date ? `
            <div class="hw-info-item">
              <span>Due ${formatDate(hw.assignment_due_date)}</span>
            </div>` : ''}
          <div class="hw-info-item">
            <span>${hw.solution_submitted ? '✅ Solution submitted' : '⏳ Not submitted yet'}</span>
          </div>
          ${hw.feedback_given ? `<div class="hw-info-item"><span>💬 Feedback received</span></div>` : ''}
        </div>
      </div>
    </div>`
  ).join('');
}

/**
 * Empty state template
 */
export function renderEmptyState(type) {
  if (type === 'my-homework') {
    return `
      <div class="hw-empty-state">
        <svg width="80" height="80" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
          <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
        </svg>
        <h3>No Homework Yet</h3>
        <p>Create your first assignment to get started!</p>
        <button class="hw-btn hw-btn-primary" data-action="open-create-homework-modal">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          Create Assignment
        </button>
      </div>
    `;
  } else {
    return `
      <div class="hw-empty-state">
        <svg width="80" height="80" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
          <circle cx="9" cy="7" r="4"/>
          <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
          <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
        </svg>
        <h3>No Homework Available</h3>
        <p>Your connections haven't shared any homework for help yet.</p>
      </div>
    `;
  }
}

/**
 * Loading state template
 */
export function renderLoadingState() {
  return `
    <div class="hw-loading-state">
      <div class="hw-spinner"></div>
      <p>Loading homework...</p>
    </div>
  `;
}

/**
 * Resource item template
 */
// UPDATE homework_templates.js
// REPLACE renderResourceItem function (line 428-452) with this:
// ============================================================================

export function renderResourceItem(resource, index, isEdit = false) {
  const removeAction = isEdit ? 'remove-edit-resource' : 'remove-resource';
  
  return `
    <div class="hw-resource-item" data-resource-index="${index}">
      <div class="hw-resource-icon">${getFileTypeIcon(resource.type)}</div>
      <div class="hw-resource-info">
        <div class="hw-resource-name">${resource.filename || resource.name}</div>
        <div class="hw-resource-size">${resource.type}</div>
      </div>
      <div class="hw-resource-actions">
        <button class="hw-resource-view-btn" data-action="view-resource" data-resource-url="${resource.url}" data-resource-type="${resource.type}">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
            <circle cx="12" cy="12" r="3"/>
          </svg>
        </button>
        <button class="hw-resource-remove-btn" data-action="${removeAction}" data-resource-index="${index}">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"/>
            <line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>
    </div>
  `;
}
