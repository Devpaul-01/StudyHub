/**
 * ============================================================================
 * HOMEWORK EVENT HANDLERS
 * Business logic for user interactions
 * ============================================================================
 */

import { homeworkState } from './homework.state.js';
import { homeworkAPI } from './homework.api.js';
import { showHomeworkToast, getStatusBadgeClass, getStatusDisplayText, escapeHtml, safeUrl } from './homework.utils.js';
import { refreshCurrentTab, updateAssignmentCard, removeAssignmentCard } from './homework.render.js';
import { renderResourceItem } from './homework.templates.js';
import {
  openCreateHomeworkModal,
  closeHomeworkModal,
  openHomeworkOptions,
  handleResourceUpload,
  removeResource,
  viewResource,
  downloadResource,
  openHomeworkDetailsModal
} from './homework.modals.js';

/**
 * Start homework (change status to in_progress)
 */
 /**
 * View homework helpers - REFINED VERSION
 * Calls the new API endpoint that returns assignment details and helpers
 */
export async function handleViewHomeworkHelpers(assignmentId) {
  try {
    const response = await homeworkAPI.getMyHelpRequests(assignmentId);

    
    const data = response.data;

    if (data.helpers && data.helpers.length === 0) {
        showHomeworkToast('No one is helping with this assignment yet', 'info');
        return;
    }
      // Open helpers modal with assignment and helpers data
    openHelpersModal(data.assignment, data.helpers, data.total_helpers);
     
  } catch (error) {
    console.error('Error loading helpers:', error);
    showHomeworkToast(error.message || 'Failed to load helpers', 'error');
  }
}

/**
 * Open helpers modal - REFINED VERSION
 * Displays assignment details at top and helper information
 */
function openHelpersModal(assignment, helpers, totalHelpers) {
  const modal = document.getElementById('hw-helpers-modal');
  if (!modal) return;

  // Update modal title to include assignment title
  const modalTitle = modal.querySelector('.hw-modal-title');
  if (modalTitle) {
    modalTitle.textContent = `People Helping You (${totalHelpers})`;
  }

  const helpersList = modal.querySelector('#hw-helpers-list');
  
  // Format due date if exists
  const dueDate = assignment.due_date 
    ? new Date(assignment.due_date).toLocaleDateString('en-US', { 
        month: 'short', 
        day: 'numeric', 
        year: 'numeric' 
      })
    : 'No due date';

  // Helper status formatting
  const formatStatus = (status) => {
    const statusMap = {
      'pending': 'Pending',
      'submitted': 'Submitted',
      'completed': 'Completed',
      'reviewed': 'Reviewed'
    };
    return statusMap[status] || status;
  };

  // Format response time
  const formatResponseTime = (seconds) => {
    if (!seconds) return 'N/A';
    
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    
    if (hours > 24) {
      const days = Math.floor(hours / 24);
      return `${days}d ${hours % 24}h`;
    } else if (hours > 0) {
      return `${hours}h ${minutes}m`;
    } else {
      return `${minutes}m`;
    }
  };

  // Format date/time
  const formatDateTime = (dateString) => {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit'
    });
  };

  // Build the HTML
  helpersList.innerHTML = `
    <!-- Assignment Details Section -->
    <div class="hw-assignment-details">
      <div class="hw-assignment-header">
        <h4 class="hw-assignment-title">${escapeHtml(assignment.title)}</h4>
        <span class="hw-badge hw-badge-${escapeHtml(assignment.status)}">${escapeHtml(formatStatus(assignment.status))}</span>
      </div>
      
      <div class="hw-assignment-meta">
        <div class="hw-meta-item">
          <span class="hw-meta-label">Subject:</span>
          <span class="hw-meta-value">${escapeHtml(assignment.subject || 'N/A')}</span>
        </div>
        <div class="hw-meta-item">
          <span class="hw-meta-label">Difficulty:</span>
          <span class="hw-meta-value hw-difficulty-${escapeHtml(assignment.difficulty)}">${escapeHtml(assignment.difficulty || 'N/A')}</span>
        </div>
        <div class="hw-meta-item">
          <span class="hw-meta-label">Due Date:</span>
          <span class="hw-meta-value">${dueDate}</span>
        </div>
        <div class="hw-meta-item">
          <span class="hw-meta-label">Total Helpers:</span>
          <span class="hw-meta-value">${totalHelpers}</span>
        </div>
      </div>
    </div>

    <div class="hw-helpers-divider"></div>

    <!-- Helpers List Section -->
    <div class="hw-helpers-section">
      <h5 class="hw-helpers-section-title">Helpers</h5>
      ${helpers.map(helper => `
        <div class="hw-helper-item">
          <div class="hw-helper-main">
            <img  data-action='view-avatar'
              src="${escapeHtml(safeUrl(helper.helper?.avatar) || '/static/default-avatar.png')}" 
              alt="${escapeHtml(helper.helper?.name)}"
              class="hw-helper-avatar"
            />
            <div class="hw-helper-info">
              <div class="hw-helper-name-row">
                <span class="hw-helper-name">${escapeHtml(helper.helper?.name || 'Unknown')}</span>
                ${helper.is_marked_helpful ? `
                  <span class="hw-helper-badge hw-badge-helpful" title="Marked as helpful">
                    ⭐ Helpful
                  </span>
                ` : ''}
                ${helper.reaction_type ? `
                  <span class="hw-helper-badge hw-badge-reaction" title="Reaction: ${escapeHtml(helper.reaction_type)}">
                    ${helper.reaction_type === 'lifesaver' ? '🏆' : '👍'}
                  </span>
                ` : ''}
              </div>
              <div class="hw-helper-username">@${escapeHtml(helper.helper?.username || 'unknown')}</div>
            </div>
          </div>

          <div class="hw-helper-details">
            <div class="hw-helper-detail-row">
              <span class="hw-detail-label">Status:</span>
              <span class="hw-badge hw-badge-${escapeHtml(helper.status)}">${escapeHtml(formatStatus(helper.status))}</span>
            </div>
            
            ${helper.subject ? `
              <div class="hw-helper-detail-row">
                <span class="hw-detail-label">Subject:</span>
                <span class="hw-detail-value">${escapeHtml(helper.subject)}</span>
              </div>
            ` : ''}

            ${helper.difficulty ? `
              <div class="hw-helper-detail-row">
                <span class="hw-detail-label">Difficulty:</span>
                <span class="hw-detail-value hw-difficulty-${escapeHtml(helper.difficulty)}">${escapeHtml(helper.difficulty)}</span>
              </div>
            ` : ''}

            <div class="hw-helper-detail-row">
              <span class="hw-detail-label">Offered:</span>
              <span class="hw-detail-value">${formatDateTime(helper.created_at)}</span>
            </div>

            ${helper.submitted_at ? `
              <div class="hw-helper-detail-row">
                <span class="hw-detail-label">Submitted:</span>
                <span class="hw-detail-value">${formatDateTime(helper.submitted_at)}</span>
              </div>
            ` : ''}

            ${helper.response_time_seconds ? `
              <div class="hw-helper-detail-row">
                <span class="hw-detail-label">Response Time:</span>
                <span class="hw-detail-value">${formatResponseTime(helper.response_time_seconds)}</span>
              </div>
            ` : ''}

            ${helper.feedback_at ? `
              <div class="hw-helper-detail-row">
                <span class="hw-detail-label">Feedback Given:</span>
                <span class="hw-detail-value">${formatDateTime(helper.feedback_at)}</span>
              </div>
            ` : ''}

            <div class="hw-helper-badges">
              ${helper.has_solution ? '<span class="hw-info-badge">📝 Has Solution</span>' : ''}
              ${helper.has_feedback ? '<span class="hw-info-badge">💬 Has Feedback</span>' : ''}
            </div>
          </div>

          <div class="hw-helper-actions">
            <button 
              class="hw-btn hw-btn-secondary hw-btn-sm" 
              data-action="view-submission-details" 
              data-submission-id="${helper.id}"
            >
              View Submission
            </button>
          </div>
        </div>
      `).join('')}
    </div>
  `;

  modal.classList.remove('hidden');
  modal.classList.add('active');
  document.body.style.overflow = 'hidden';
}

export async function handleStartHomework(assignmentId) {
  try {
    const response = await homeworkAPI.toggleAssignmentStatus(assignmentId, 'start_working');

    if (response.status === 'success') {
      refreshCurrentTab();
    } else {
      throw new Error(response.message || 'Failed to start assignment');
    }
  } catch (error) {
    console.error('Error starting homework:', error);
    showHomeworkToast(error.message || 'Failed to start assignment', 'error');
  }
}

/**
 * Complete homework
 */
export async function handleCompleteHomework(assignmentId) {
  if (!confirm('Mark this assignment as complete?')) {
    return;
  }

  try {
    const response = await homeworkAPI.toggleAssignmentStatus(assignmentId, 'mark_complete');

    if (response.status === 'success') {
      refreshCurrentTab();
    } else {
      throw new Error(response.message || 'Failed to complete assignment');
    }
  } catch (error) {
    console.error('Error completing homework:', error);
    showHomeworkToast(error.message || 'Failed to complete assignment', 'error');
  }
}

/**
 * Reopen homework
 */


/**
 * Share homework for help
 */
export async function handleShareHomeworkForHelp(assignmentId) {
  if (!confirm('Share this assignment with your connections for help?')) {
    return;
  }

  try {
    const response = await homeworkAPI.toggleAssignmentStatus(assignmentId, 'share_for_help');

    if (response.status === 'success') {
      refreshCurrentTab();
    } else {
      throw new Error(response.message || 'Failed to share assignment');
    }
  } catch (error) {
    console.error('Error sharing homework:', error);
    showHomeworkToast(error.message || 'Failed to share assignment', 'error');
  }
}

/**
 * Unshare homework
 */
export async function handleUnshareHomework(assignmentId) {
  if (!confirm('Stop sharing this assignment?')) {
    return;
  }

  try {
    const response = await homeworkAPI.toggleAssignmentStatus(assignmentId, 'unshare');

    if (response.status === 'success') {
      refreshCurrentTab();
    } else {
      throw new Error(response.message || 'Failed to unshare assignment');
    }
  } catch (error) {
    console.error('Error unsharing homework:', error);
    showHomeworkToast(error.message || 'Failed to unshare assignment', 'error');
  }
}

/**
 * Delete homework
 */
export async function handleDeleteHomework(assignmentId) {
  if (!confirm('Are you sure you want to delete this assignment? This cannot be undone.')) {
    return;
  }

  try {
    const response = await homeworkAPI.deleteAssignment(assignmentId);

    if (response.status === 'success') {
      showHomeworkToast('Assignment deleted', 'success');
      removeAssignmentCard(assignmentId);
    } else {
      throw new Error(response.message || 'Failed to delete assignment');
    }
  } catch (error) {
    console.error('Error deleting homework:', error);
    showHomeworkToast(error.message || 'Failed to delete assignment', 'error');
  }
}

/**
 * Offer help with homework
 */
export async function handleOfferHelp(homeworkId) {
  const homework = homeworkState.getHomeworkById(homeworkId);
  
  if (!homework) return;

  if (!confirm(`Offer to help ${homework.student?.name} with "${homework.title}"?`)) {
    return;
  }

  try {
    const response = await homeworkAPI.offerHelp(homeworkId);

    if (response.status === 'success') {
      refreshCurrentTab();
    } else {
      throw new Error(response.message || 'Failed to offer help');
    }
  } catch (error) {
    console.error('Error offering help:', error);
    showHomeworkToast(error.message || 'Failed to offer help', 'error');
  }
}




/**
 * View submission details
 */
export async function handleViewSubmissionDetails(submissionId) {
  try {
    const response = await homeworkAPI.getSubmissionDetails(submissionId);

    if (response.status === 'success') {
      homeworkState.setCurrentSubmission(response.data);
      openSubmissionDetailsModal(response.data);
    } else {
      throw new Error(response.message || 'Failed to load submission');
    }
  } catch (error) {
    console.error('Error loading submission:', error);
    showHomeworkToast(error.message || 'Failed to load submission', 'error');
  }
}
/**
 * Open submission details modal - REFINED VERSION
 * Displays complete submission details from backend
 */
function openSubmissionDetailsModal(submission) {
  const modal = document.getElementById('hw-submission-modal');
  

  if (!modal) return;
  const modalBody = modal.querySelector('.hw-modal-body');
  
  // Format dates
  const formatDateTime = (dateString) => {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit'
    });
  };

  // Format status
  const formatStatus = (status) => {
    const statusMap = {
      'pending': 'Pending',
      'submitted': 'Solution Submitted',
      'completed': 'Completed',
      'reviewed': 'Reviewed'
    };
    return statusMap[status] || status;
  };

  // Build the HTML
  modalBody.innerHTML = `
    <div class="hw-submission-details">
      <!-- Header Section -->
      <div class="hw-submission-header">
        <h3 class="hw-submission-title">${escapeHtml(submission.title)}</h3>
        <span class="hw-badge ${getStatusBadgeClass(submission.status)}">
          ${escapeHtml(formatStatus(submission.status))}
        </span>
      </div>

      <!-- Assignment Info (if available) -->
      ${submission.assignment ? `
        <div class="hw-submission-assignment-info">
          <div class="hw-assignment-info-row">
            <span class="hw-info-label">Assignment Due Date:</span>
            <span class="hw-info-value">${formatDateTime(submission.assignment.due_date)}</span>
          </div>
          <div class="hw-assignment-info-row">
            <span class="hw-info-label">Assignment Status:</span>
            <span class="hw-badge ${getStatusBadgeClass(submission.assignment.status)}">
              ${escapeHtml(formatStatus(submission.assignment.status))}
            </span>
          </div>
        </div>
      ` : ''}

      <!-- Parties Section -->
      <div class="hw-submission-parties">
        <div class="hw-party-card">
          <div class="hw-party-header">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
              <circle cx="12" cy="7" r="4"/>
            </svg>
            <strong>Student (Requester)</strong>
          </div>
          ${submission.requester ? `
            <div class="hw-party-info">
              <img 
                src="${escapeHtml(safeUrl(submission.requester.avatar) || '/static/images/default-avatar.png')}" 
                alt="${escapeHtml(submission.requester.name)}"
                class="hw-party-avatar"
              />
              <div class="hw-party-details">
                <div class="hw-party-name">${escapeHtml(submission.requester.name)}</div>
                <div class="hw-party-meta">@${escapeHtml(submission.requester.username)}</div>
                ${submission.requester.department ? `
                  <div class="hw-party-meta">${escapeHtml(submission.requester.department)}</div>
                ` : ''}
              </div>
            </div>
          ` : '<p class="hw-text-muted">Not available</p>'}
        </div>

        <div class="hw-party-card">
          <div class="hw-party-header">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
              <circle cx="8.5" cy="7" r="4"/>
              <polyline points="17 11 19 13 23 9"/>
            </svg>
            <strong>Helper</strong>
          </div>
          ${submission.helper ? `
            <div class="hw-party-info">
              <img 
                src="${escapeHtml(safeUrl(submission.helper.avatar) || '/static/images/default-avatar.png')}" 
                alt="${escapeHtml(submission.helper.name)}"
                class="hw-party-avatar"
              />
              <div class="hw-party-details">
                <div class="hw-party-name">${escapeHtml(submission.helper.name)}</div>
                <div class="hw-party-meta">@${escapeHtml(submission.helper.username)}</div>
                ${submission.helper.department ? `
                  <div class="hw-party-meta">${escapeHtml(submission.helper.department)}</div>
                ` : ''}
                ${submission.helper.active_details ? `
                  <div class="hw-party-status ${submission.helper.active_details.is_online ? 'hw-status-online' : ''}">
                    <span class="hw-status-dot"></span>
                    ${submission.helper.active_details.is_online ? 'Online' : 'Offline'}
                  </div>
                ` : ''}
              </div>
            </div>
          ` : '<p class="hw-text-muted">Not available</p>'}
        </div>
      </div>

      <!-- Subject & Difficulty -->
      <div class="hw-submission-meta-grid">
        ${submission.subject ? `
          <div class="hw-meta-card">
            <div class="hw-meta-label">Subject</div>
            <div class="hw-meta-value">${escapeHtml(submission.subject)}</div>
          </div>
        ` : ''}
        
        ${submission.difficulty ? `
          <div class="hw-meta-card">
            <div class="hw-meta-label">Difficulty</div>
            <div class="hw-meta-value hw-difficulty-${escapeHtml(submission.difficulty)}">
              ${escapeHtml(submission.difficulty)}
            </div>
          </div>
        ` : ''}

        <div class="hw-meta-card">
          <div class="hw-meta-label">Created</div>
          <div class="hw-meta-value">${formatDateTime(submission.created_at)}</div>
        </div>
      </div>

      <!-- Description Section -->
      ${submission.description ? `
        <div class="hw-submission-section">
          <div class="hw-section-header">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
              <line x1="16" y1="13" x2="8" y2="13"/>
              <line x1="16" y1="17" x2="8" y2="17"/>
              <line x1="10" y1="9" x2="8" y2="9"/>
            </svg>
            <h4>Description</h4>
          </div>
          <div class="hw-section-content">
            <p>${escapeHtml(submission.description)}</p>
          </div>
        </div>
      ` : ''}

      <!-- Solution Section -->
      ${submission.solution?.text ? `
        <div class="hw-submission-section hw-section-solution">
          <div class="hw-section-header">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            <h4>Solution</h4>
            ${submission.solution.submitted_at ? `
              <span class="hw-section-timestamp">${formatDateTime(submission.solution.submitted_at)}</span>
            ` : ''}
          </div>
          <div class="hw-section-content">
            <p>${escapeHtml(submission.solution.text)}</p>
            
            ${submission.solution.resources && submission.solution.resources.length > 0 ? `
              <div class="hw-resources-section">
                <div class="hw-resources-header">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>
                    <polyline points="13 2 13 9 20 9"/>
                  </svg>
                  <span>Solution Resources (${submission.solution.resources.length})</span>
                </div>
                <div class="hw-resources-list">
                  ${submission.solution.resources.map((resource, index) => renderResourceItem(resource, index)).join('')}
                </div>
              </div>
            ` : ''}
          </div>
        </div>
      ` : submission.status === 'pending' ? `
        <div class="hw-submission-section hw-section-empty">
          <div class="hw-empty-icon">⏳</div>
          <p class="hw-text-muted">Solution pending - waiting for helper to submit</p>
        </div>
      ` : ''}

      <!-- Feedback Section -->
      ${submission.feedback?.text ? `
        <div class="hw-submission-section hw-section-feedback">
          <div class="hw-section-header">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            <h4>Feedback</h4>
            ${submission.feedback.given_at ? `
              <span class="hw-section-timestamp">${formatDateTime(submission.feedback.given_at)}</span>
            ` : ''}
          </div>
          <div class="hw-section-content">
            <p>${escapeHtml(submission.feedback.text)}</p>
            
            ${submission.feedback.resources && submission.feedback.resources.length > 0 ? `
              <div class="hw-resources-section">
                <div class="hw-resources-header">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>
                    <polyline points="13 2 13 9 20 9"/>
                  </svg>
                  <span>Feedback Resources (${submission.feedback.resources.length})</span>
                </div>
                <div class="hw-resources-list">
                  ${submission.feedback.resources.map((resource, index) => renderResourceItem(resource, index)).join('')}
                </div>
              </div>
            ` : ''}
          </div>
        </div>
      ` : submission.status === 'submitted' ? `
        <div class="hw-submission-section hw-section-empty">
          <div class="hw-empty-icon">💬</div>
          <p class="hw-text-muted">Feedback pending - waiting for student to review</p>
        </div>
      ` : ''}

      <!-- Action Buttons -->
      <div class="hw-submission-actions">
        ${submission.i_am_helper && submission.status === 'pending' ? `
          <button 
            class="hw-btn hw-btn-primary" 
            data-action="open-submit-solution-modal" 
            data-submission-id="${submission.id}"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            Submit Solution
          </button>
        ` : ''}
        
        ${submission.i_am_requester && submission.status === 'submitted' ? `
          <button 
            class="hw-btn hw-btn-primary" 
            data-action="open-give-feedback-modal" 
            data-submission-id="${submission.id}"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            Give Feedback
          </button>
        ` : ''}

        ${submission.i_am_helper && submission.status === 'submitted' ? `
          <div class="hw-info-message">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <line x1="12" y1="16" x2="12" y2="12"/>
              <line x1="12" y1="8" x2="12.01" y2="8"/>
            </svg>
            <span>Waiting for student to review your solution</span>
          </div>
        ` : ''}
      </div>
    </div>
  `;

  modal.classList.remove('hidden');
  modal.classList.add('active');
  document.body.style.overflow = 'hidden';
}



// Export all event handlers
export {
  openCreateHomeworkModal,
  closeHomeworkModal,
  openHomeworkOptions,
  handleResourceUpload,
  removeResource,
  viewResource,
  downloadResource,
  openHomeworkDetailsModal
};
