/**
 * ============================================================================
 * HOMEWORK SUBMISSION DETAIL (ENHANCED)
 * Full submission view with timeline, solution editor, and feedback
 * ============================================================================
 */

import { homeworkState } from './homework.state.js';
import { homeworkAPI } from './homework.api.js';
import { showHomeworkToast, formatDate, getStatusBadgeClass, getStatusDisplayText, escapeHtml, safeUrl } from './homework.utils.js';
import { renderResourceItem } from './homework.templates.js';

/**
 * Render timeline view
 */
function renderSubmissionTimeline(submission) {
  const timeline = [];

  // Help Offered
  timeline.push({
    status: 'completed',
    icon: '🤝',
    title: 'Help Offered',
    time: formatDate(submission.created_at),
    description: `${escapeHtml(submission.helper?.name)} offered to help`
  });

  // Solution Submitted
  if (submission.submitted_at) {
    timeline.push({
      status: 'completed',
      icon: '📝',
      title: 'Solution Submitted',
      time: formatDate(submission.submitted_at),
      description: 'Solution ready for review'
    });
  } else if (submission.status === 'pending') {
    timeline.push({
      status: 'pending',
      icon: '⏳',
      title: 'Solution Pending',
      time: null,
      description: 'Waiting for solution'
    });
  }

  // Feedback Given
  if (submission.feedback_at) {
    timeline.push({
      status: 'completed',
      icon: '⭐',
      title: 'Feedback Received',
      time: formatDate(submission.feedback_at),
      description: 'Student reviewed solution'
    });
  } else if (submission.status === 'submitted' || submission.status === 'reviewed') {
    timeline.push({
      status: 'pending',
      icon: '💬',
      title: 'Feedback Pending',
      time: null,
      description: 'Waiting for student review'
    });
  }

  // Completed
  if (submission.status === 'completed') {
    timeline.push({
      status: 'completed',
      icon: '✅',
      title: 'Completed',
      time: submission.feedback_at ? formatDate(submission.feedback_at) : null,
      description: 'Help request completed'
    });
  }

  return `
    <div class="hw-timeline">
      <h4 class="hw-timeline-title">📅 Timeline</h4>
      <div class="hw-timeline-items">
        ${timeline.map((item, index) => `
          <div class="hw-timeline-item ${item.status}">
            <div class="hw-timeline-marker">
              <div class="hw-timeline-icon">${item.icon}</div>
              ${index < timeline.length - 1 ? '<div class="hw-timeline-line"></div>' : ''}
            </div>
            <div class="hw-timeline-content">
              <div class="hw-timeline-item-title">${item.title}</div>
              ${item.time ? `<div class="hw-timeline-item-time">${item.time}</div>` : ''}
              <div class="hw-timeline-item-description">${item.description}</div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

/**
 * Render submission detail as HELPER
 */
export function renderSubmissionDetailHelper(submission) {
  return `
    <div class="hw-submission-detail">
      <!-- Back Button -->
      <button class="hw-back-btn" data-action="close-homework-modal" data-modal-id="hw-submission-detail-modal">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="19" y1="12" x2="5" y2="12"/>
          <polyline points="12 19 5 12 12 5"/>
        </svg>
        Back to Helping
      </button>

      <!-- Header -->
      <div class="hw-submission-header">
        <h2 class="hw-submission-title">${escapeHtml(submission.title)}</h2>
        
        <div class="hw-submission-meta">
          <div class="hw-submission-student">
            <img 
              src="${escapeHtml(safeUrl(submission.requester?.avatar) || '/static/default-avatar.png')}" 
              alt="${escapeHtml(submission.requester?.name)}"
              class="hw-submission-avatar"
            />
            <div>
              <div class="hw-submission-student-name">👤 Student: ${escapeHtml(submission.requester?.name)}</div>
              ${submission.requester?.department ? `<div class="hw-submission-student-dept">${escapeHtml(submission.requester.department)}</div>` : ''}
            </div>
          </div>
          
          <div class="hw-submission-status-row">
            <span class="hw-badge ${getStatusBadgeClass(submission.status)}">
              ${getStatusDisplayText(submission.status)}
            </span>
            ${submission.assignment?.due_date ? `
              <div class="hw-submission-due">
                Assignment Due: ${formatDate(submission.assignment.due_date)}
              </div>
            ` : ''}
          </div>
        </div>
      </div>

      <div class="hw-submission-divider"></div>

      <!-- Assignment Details -->
      <div class="hw-submission-section">
        <h3 class="hw-section-title">📋 Assignment Details</h3>
        <div class="hw-assignment-details-box">
          ${submission.difficulty ? `<div class="hw-detail-item"><strong>Difficulty:</strong> ${escapeHtml(submission.difficulty)}</div>` : ''}
          ${submission.subject ? `<div class="hw-detail-item"><strong>Subject:</strong> ${escapeHtml(submission.subject)}</div>` : ''}
          ${submission.assignment?.estimated_hours ? `<div class="hw-detail-item"><strong>Estimated Time:</strong> ${submission.assignment.estimated_hours} hours</div>` : ''}
          
          ${submission.description ? `
            <div class="hw-detail-description">
              <strong>Description:</strong>
              <p>${escapeHtml(submission.description)}</p>
            </div>
          ` : ''}
        </div>
      </div>

      ${submission.status === 'pending' ? `
        <!-- Submit Solution Form -->
        <div class="hw-submission-section">
          <h3 class="hw-section-title">📝 Submit Your Solution</h3>
          
          <div id="hw-submit-solution-form"  data-submission-id="${submission.id}">
            <div class="hw-form-group">
              <label class="hw-form-label">Solution *</label>
              <textarea 
                class="hw-form-textarea hw-solution-editor" 
                id="hw-solution-text"
                placeholder="Explain your solution step by step..."
                rows="10"
                required
              ></textarea>
              <div class="hw-form-hint">Be detailed and clear. Include step-by-step explanations.</div>
            </div>

            <div class="hw-form-group">
              <label class="hw-form-label">📎 Attach Files (optional)</label>
              <div class="hw-upload-area" data-action="trigger-solution-resource-upload">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                  <polyline points="17 8 12 3 7 8"/>
                  <line x1="12" y1="3" x2="12" y2="15"/>
                </svg>
                <p>Drag and drop or click to upload</p>
                <span class="hw-upload-hint">PDF, images, spreadsheets (max 50MB)</span>
              </div>
              
              <div id="hw-solution-resources-preview" class="hw-resources-preview hidden"></div>
            </div>

            <div class="hw-form-actions">
              <button type="button" class="hw-btn hw-btn-secondary" data-action="close-homework-modal" data-modal-id="hw-submission-detail-modal">
                Cancel
              </button>
              <button data-submission-id="${submission.id}" data-action='submit-solution-form' class="hw-btn hw-btn-primary">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
                Submit Solution
              </button>
            </div>
          </div>
        </div>
      ` : ''}

      ${submission.solution?.text ? `
        <!-- Submitted Solution (Read-only) -->
        <div class="hw-submission-section">
          <h3 class="hw-section-title">✅ Your Solution</h3>
          <div class="hw-solution-display">
            <p>${escapeHtml(submission.solution.text)}</p>
            
            ${submission.solution.resources && submission.solution.resources.length > 0 ? `
              <div class="hw-resources-list">
                <div class="hw-resources-header">Attached Files:</div>
                ${submission.solution.resources.map((r, i) => renderResourceItem(r, i)).join('')}
              </div>
            ` : ''}
          </div>
          <div class="hw-solution-submitted-time">
            Submitted ${formatDate(submission.submitted_at)}
          </div>
        </div>
      ` : ''}

      ${submission.feedback?.text ? `
        <!-- Student Feedback -->
        <div class="hw-submission-section">
          <h3 class="hw-section-title">💬 Student Feedback</h3>
          <div class="hw-feedback-display">
            <p>${escapeHtml(submission.feedback.text)}</p>
          </div>
          <div class="hw-feedback-time">
            Received ${formatDate(submission.feedback_at)}
          </div>
        </div>
      ` : ''}

      <!-- Timeline -->
      <div class="hw-submission-section">
        ${renderSubmissionTimeline(submission)}
      </div>
    </div>
  `;
}

/**
 * Render submission detail as REQUESTER (student)
 */
export function renderSubmissionDetailRequester(submission) {
  return `
    <div class="hw-submission-detail">
      <!-- Back Button -->
      <button class="hw-back-btn" data-action="close-homework-modal" data-modal-id="hw-submission-detail-modal">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="19" y1="12" x2="5" y2="12"/>
          <polyline points="12 19 5 12 12 5"/>
        </svg>
        Back to Help Requests
      </button>

      <!-- Header -->
      <div class="hw-submission-header">
        <h2 class="hw-submission-title">${escapeHtml(submission.title)}</h2>
        
        <div class="hw-submission-meta">
          <div class="hw-submission-student">
            <img 
              src="${escapeHtml(safeUrl(submission.helper?.avatar) || '/static/default-avatar.png')}" 
              alt="${escapeHtml(submission.helper?.name)}"
              class="hw-submission-avatar"
            />
            <div>
              <div class="hw-submission-student-name">👤 Helper: ${escapeHtml(submission.helper?.name)}</div>
              ${submission.helper?.department ? `<div class="hw-submission-student-dept">${escapeHtml(submission.helper.department)}</div>` : ''}
            </div>
          </div>
          
          <div class="hw-submission-status-row">
            <span class="hw-badge ${getStatusBadgeClass(submission.status)}">
              ${getStatusDisplayText(submission.status)}
            </span>
            ${submission.assignment?.due_date ? `
              <div class="hw-submission-due">
                Assignment Due: ${formatDate(submission.assignment.due_date)}
              </div>
            ` : ''}
          </div>
        </div>
      </div>

      <div class="hw-submission-divider"></div>

      <!-- Your Assignment -->
      <div class="hw-submission-section">
        <h3 class="hw-section-title">📋 Your Assignment</h3>
        <div class="hw-assignment-details-box">
          ${submission.difficulty ? `<div class="hw-detail-item"><strong>Difficulty:</strong> ${escapeHtml(submission.difficulty)}</div>` : ''}
          ${submission.subject ? `<div class="hw-detail-item"><strong>Subject:</strong> ${escapeHtml(submission.subject)}</div>` : ''}
          
          ${submission.description ? `
            <div class="hw-detail-description">
              <p>${escapeHtml(submission.description)}</p>
            </div>
          ` : ''}
        </div>
      </div>

      ${submission.solution?.text ? `
        <!-- Helper's Solution -->
        <div class="hw-submission-section">
          <h3 class="hw-section-title">✅ ${escapeHtml(submission.helper?.name)}'s Solution</h3>
          <div class="hw-solution-submitted-time">
            Submitted ${formatDate(submission.submitted_at)}
          </div>
          
          <div class="hw-solution-display">
            <p>${escapeHtml(submission.solution.text)}</p>
            
            ${submission.solution.resources && submission.solution.resources.length > 0 ? `
              <div class="hw-resources-list">
                <div class="hw-resources-header">Attached Files:</div>
                ${submission.solution.resources.map((r, i) => renderResourceItem(r, i)).join('')}
              </div>
            ` : ''}
          </div>
        </div>

        ${!submission.feedback?.text && (submission.status === 'submitted' || submission.status === 'reviewed') ? `
  <div class="hw-submission-section">
    <h3 class="hw-section-title">💬 Give Feedback</h3>
    
    <div id="hw-give-feedback-form" data-submission-id="${submission.id}">
      <!-- Reaction Picker -->
      <div class="hw-form-group">
        <label class="hw-form-label">Quick Reaction <span class="hw-form-optional">(optional)</span></label>
        <div class="hw-reaction-picker">
          <button type="button" class="hw-reaction-btn" data-action="feedback-reaction-btn" data-reaction="thanks">
  <span class="hw-reaction-emoji">🙏</span>
  <span class="hw-reaction-label">Thanks</span>
</button>
<button type="button" class="hw-reaction-btn" data-action="feedback-reaction-btn" data-reaction="lifesaver">
  <span class="hw-reaction-emoji">🔥</span>
  <span class="hw-reaction-label">Lifesaver</span>
</button>
<button type="button" class="hw-reaction-btn" data-action="feedback-reaction-btn" data-reaction="mindblown">
  <span class="hw-reaction-emoji">🧠</span>
  <span class="hw-reaction-label">Mind Blown</span>
</button>
<button type="button" class="hw-reaction-btn" data-action="feedback-reaction-btn" data-reaction="perfect">
  <span class="hw-reaction-emoji">⭐</span>
  <span class="hw-reaction-label">Perfect</span>
</button>
        </div>
        <input type="hidden" id="hw-selected-reaction" value="" />
      </div>

      <div class="hw-form-group">
        <label class="hw-form-label">Feedback Message *</label>
        <textarea 
          class="hw-form-textarea" 
          id="hw-feedback-text"
          placeholder="Thank your helper and share how their solution helped you..."
          rows="6"
          required
        ></textarea>
      </div>

      <div class="hw-form-group">
        <label class="hw-form-checkbox">
          <input type="checkbox" id="hw-is-helpful" checked />
          <span>This solution was helpful</span>
        </label>
      </div>

      <div class="hw-form-group">
        <label class="hw-form-checkbox">
          <input type="checkbox" id="hw-mark-complete" checked />
          <span>Mark this help request as completed</span>
        </label>
      </div>

      <div class="hw-form-actions">
        <button type="button" class="hw-btn hw-btn-secondary" data-action="close-homework-modal" data-modal-id="hw-submission-detail-modal">
          Cancel
        </button>
        <button data-action="submit-feedback-form" data-submission-id="${submission.id}" class="hw-btn hw-btn-primary">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          Send Feedback
        </button>
      </div>
    </div>
  </div>
` : ''}
        ${submission.feedback?.text ? `
          <!-- Your Feedback (Read-only) -->
          <div class="hw-submission-section">
            <h3 class="hw-section-title">💬 Your Feedback</h3>
            <div class="hw-feedback-display">
              <p>${escapeHtml(submission.feedback.text)}</p>
            </div>
            <div class="hw-feedback-time">
              Given ${formatDate(submission.feedback_at)}
            </div>
          </div>
        ` : ''}
      ` : `
        <!-- Waiting for Solution -->
        <div class="hw-submission-section">
          <div class="hw-waiting-state">
            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <polyline points="12 6 12 12 16 14"/>
            </svg>
            <h3>Waiting for Solution</h3>
            <p>${escapeHtml(submission.helper?.name)} is working on your assignment</p>
          </div>
        </div>
      `}

      <!-- Timeline -->
      <div class="hw-submission-section">
        ${renderSubmissionTimeline(submission)}
      </div>

      ${submission.status === 'pending' ? `
        <!-- Cancel Request Option -->
        <div class="hw-submission-section">
          <button class="hw-btn hw-btn-secondary hw-btn-danger" data-action="cancel-submission" data-submission-id="${submission.id}">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
            Cancel Help Request
          </button>
        </div>
      ` : ''}
    </div>
  `;
}

/**
 * Open submission detail modal
 */
export async function openSubmissionDetailModal(submissionId) {
  const modal = document.getElementById('hw-submission-detail-modal');
  
  if (!modal) {
    console.error('Submission detail modal not found');
    return;
  }

  const modalBody = modal.querySelector('.hw-modal-body');
  
  // Show loading
  modalBody.innerHTML = `
    <div class="hw-loading-state">
      <div class="hw-spinner"></div>
      <p>Loading submission details...</p>
    </div>
  `;

  // Show modal
  modal.classList.remove('hidden');
  modal.classList.add('active');
  document.body.style.overflow = 'hidden';

  try {
    const response = await homeworkAPI.getSubmissionDetails(submissionId);

    if (response.status === 'success') {
      homeworkState.setCurrentSubmission(response.data);
      
      // Render based on user role
      if (response.data.i_am_helper) {
        modalBody.innerHTML = renderSubmissionDetailHelper(response.data);
      } else {
        modalBody.innerHTML = renderSubmissionDetailRequester(response.data);
      }
    } else {
      throw new Error(response.message || 'Failed to load submission');
    }
  } catch (error) {
    console.error('Error loading submission:', error);
    modalBody.innerHTML = `
      <div class="hw-error-state">
        <p>${escapeHtml(error.message || 'Failed to load submission details')}</p>
        <button class="hw-btn hw-btn-primary" data-action="close-homework-modal" data-modal-id="hw-submission-detail-modal">
          Close
        </button>
      </div>
    `;
  }
}

/**
 * Handle solution submission form
 */
export async function handleSubmitSolution(event, submissionId) {
  event.preventDefault();

  const form = document.getElementById('hw-submit-solution-form');
  if (!form) {
    showHomeworkToast('Could not find solution form', 'error');
    return;
  }

  const submitBtn = form.querySelector('[data-action="submit-solution-form"]');
  const solutionText = form.querySelector('#hw-solution-text').value.trim();

  if (!solutionText) {
    showHomeworkToast('Please enter a solution', 'error');
    return;
  }

  // Disable submit button
  submitBtn.disabled = true;
  submitBtn.textContent = 'Submitting...';

  try {
    const response = await homeworkAPI.submitSolution(submissionId, {
      solution_text: solutionText,
      resources: homeworkState.getUploadedResources()
    });

    if (response.status === 'success') {
      showHomeworkToast(response.message || 'Solution submitted!', 'success');
      
      // Reload submission detail
      await openSubmissionDetailModal(submissionId);
    } else {
      throw new Error(response.message || 'Failed to submit solution');
    }
  } catch (error) {
    console.error('Error submitting solution:', error);
    showHomeworkToast(error.message || 'Failed to submit solution', 'error');
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Submit Solution';
    }
  }
}

/**
 * Handle feedback submission form
 */
 export async function handleSubmitFeedback(event, submissionId) {
  event.preventDefault();

  const form = document.getElementById('hw-give-feedback-form');
  const submitBtn = form.querySelector('button[data-action="submit-feedback-form"]');
  const feedbackText = form.querySelector('#hw-feedback-text').value.trim();
  const markComplete = form.querySelector('#hw-mark-complete').checked;
  // Explicit: the server no longer assumes "helpful" (audit F-06)
  const isHelpful = form.querySelector('#hw-is-helpful')?.checked ?? false;
  const reactionInput = form.querySelector('#hw-selected-reaction');
  const reactionType = reactionInput?.value || null;

  if (!feedbackText) {
    showHomeworkToast('Please enter feedback', 'error');
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Sending...';

  try {
    const response = await homeworkAPI.giveFeedback(submissionId, {
      feedback_text: feedbackText,
      mark_complete: markComplete,
      is_helpful: isHelpful,
      ...(reactionType && { reaction_type: reactionType })
    });

    if (response.status === 'success') {
      showHomeworkToast(response.message || 'Feedback sent! 🎉', 'success');
      await openSubmissionDetailModal(submissionId);
    } else {
      throw new Error(response.message || 'Failed to send feedback');
    }
  } catch (error) {
    console.error('Error sending feedback:', error);
    showHomeworkToast(error.message || 'Failed to send feedback', 'error');
    submitBtn.disabled = false;
    submitBtn.textContent = 'Send Feedback';
  }
}

/**
 * Handle submission cancellation
 */
export async function handleCancelSubmission(submissionId) {
  if (!confirm('Are you sure you want to cancel this help request?')) {
    return;
  }

  try {
    const response = await homeworkAPI.cancelSubmission(submissionId);

    if (response.status === 'success') {
      showHomeworkToast(response.message || 'Help request cancelled', 'success');
      
      // Close modal and refresh
      const modal = document.getElementById('hw-submission-detail-modal');
      if (modal) {
        modal.classList.remove('active');
        modal.classList.add('hidden');
        document.body.style.overflow = '';
      }
      
      // Refresh current view
      if (typeof refreshCurrentTab === 'function') {
        refreshCurrentTab();
      }
    } else {
      throw new Error(response.message || 'Failed to cancel request');
    }
  } catch (error) {
    console.error('Error cancelling submission:', error);
    showHomeworkToast(error.message || 'Failed to cancel request', 'error');
  }
}