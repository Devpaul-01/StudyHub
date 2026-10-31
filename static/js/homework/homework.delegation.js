/**
 * ============================================================================
 * HOMEWORK HANDLERS FOR UNIFIED DELEGATION
 * Exports click handlers to be merged into app.unified.js
 * ============================================================================
 */

import {
  switchTab, refreshCurrentTab,
  handleFilterMyHelpRequests, handleFilterHelpingWith,
} from './homework.render.js';
import {
  openCreateHomeworkModal,
  closeHomeworkModal,
  handleCreateHomework,
  openHomeworkOptions,
  handleResourceUpload,
  removeResource,
  viewResource,
  downloadResource,
  openEditHomeworkModal,      // ADD THIS
  handleEditHomework,          // ADD THIS
  removeEditResource,         
  openHomeworkDetailsModal
} from './homework.modals.js';
import {
  handleStartHomework,
  handleCompleteHomework,
  handleShareHomeworkForHelp,
  handleUnshareHomework,
  handleDeleteHomework,
  handleOfferHelp,
  handleViewHomeworkHelpers,
  handleViewSubmissionDetails
} from './homework.events.js';
//import { openStatsDashboard } from './homework.stats.js';
import {
  openSubmissionDetailModal,
  handleSubmitSolution,
  handleSubmitFeedback,
  handleCancelSubmission
} from './homework.submission.js';
import {
  handleQuickAction,
  enableBulkMode,
  disableBulkMode,
  handleBulkAction,
  selectAllAssignments,
  deselectAllAssignments,
  updateSelectedCount
} from './homework.quick-actions.js';

/**
 * All homework click handlers
 * These will be spread into UNIFIED_ACTIONS in app.unified.js
 */
export const HomeworkHandlers = {
  'feedback-reaction-btn': (target, event) => {
  event.preventDefault();
  const form = document.getElementById('hw-give-feedback-form');
  if (!form) return;

  // Toggle selection
  form.querySelectorAll('.hw-reaction-btn').forEach(btn => btn.classList.remove('selected'));
  const reactionInput = form.querySelector('#hw-selected-reaction');

  if (reactionInput.value === target.dataset.reaction) {
    // Deselect if clicking same
    reactionInput.value = '';
  } else {
    target.classList.add('selected');
    reactionInput.value = target.dataset.reaction;
  }
},
  /**
   * Switch homework tab
   */
  'switch-homework-tab': (target, event) => {
    const tab = target.dataset.tab;
    if (tab) {
      switchTab(tab);
    }
  },
  'edit-homework': (target, event) => {
    event.preventDefault();
    const assignmentId = target.dataset.assignmentId;
    if (assignmentId) {
      openEditHomeworkModal(assignmentId);
    }
  },

  /**
   * Submit edit homework - ADD THIS NEW HANDLER
   */
  'submit-edit-homework': async (target, event) => {
    await handleEditHomework(target, event);
  },

  /**
   * Upload edit resource - ADD THIS NEW HANDLER
   */
  'upload-edit-resource': (target, event) => {
    const input = document.getElementById('hw-edit-resource-input');
    if (input) {
      input.click();
    }
  },

  /**
   * Remove edit resource - ADD THIS NEW HANDLER
   */
  'remove-edit-resource': (target, event) => {
    event.preventDefault();
    const index = parseInt(target.dataset.resourceIndex);
    if (!isNaN(index)) {
      removeEditResource(index);
    }
  },

  /**
   * Open create homework modal
   */
  'open-create-homework-modal': (target, event) => {
    event.preventDefault();
    openCreateHomeworkModal();
  },

  /**
   * Close homework modal
   */
  'close-homework-modal': (target, event) => {
    event.preventDefault();
    const modalId = target.dataset.modalId;
    if (modalId) {
      closeHomeworkModal(modalId);
    }
  },

  /**
   * Submit create homework form
   * FIXED: Now passes both target (form) and event
   */
  'submit-create-homework': async (target, event) => {
    await handleCreateHomework(target, event);
  },

  /**
   * Toggle homework options menu
   */
  'toggle-homework-options': (target, event) => {
    event.stopPropagation();
    const assignmentId = target.dataset.assignmentId;
    if (assignmentId) {
      openHomeworkOptions(assignmentId, event);
    }
  },

  /**
   * Start homework
   */
  'start-homework': async (target, event) => {
    event.preventDefault();
    const assignmentId = target.dataset.assignmentId;
    if (assignmentId) {
      await handleStartHomework(assignmentId);
    }
  },

  /**
   * Complete homework
   */
  'complete-homework': async (target, event) => {
    event.preventDefault();
    const assignmentId = target.dataset.assignmentId;
    if (assignmentId) {
      await handleCompleteHomework(assignmentId);
    }
  },



  /**
   * Share homework for help
   */
  'share-homework-for-help': async (target, event) => {
    event.preventDefault();
    const assignmentId = target.dataset.assignmentId;
    if (assignmentId) {
      await handleShareHomeworkForHelp(assignmentId);
    }
  },

  /**
   * Unshare homework
   */
  'unshare-homework': async (target, event) => {
    event.preventDefault();
    const assignmentId = target.dataset.assignmentId;
    if (assignmentId) {
      await handleUnshareHomework(assignmentId);
    }
  },

  /**
   * Delete homework
   */
  'delete-homework': async (target, event) => {
    event.preventDefault();
    const assignmentId = target.dataset.assignmentId;
    if (assignmentId) {
      await handleDeleteHomework(assignmentId);
      
      // Close options menu if open
      const menu = document.querySelector('.hw-options-menu');
      if (menu) menu.remove();
    }
  },

  /**
   * Edit homework
   */
  'edit-homework': (target, event) => {
    event.preventDefault();
    const assignmentId = target.dataset.assignmentId;
    if (assignmentId) {
      // TODO: Implement edit modal
      console.log('Edit homework:', assignmentId);
    }
  },

  /**
   * View homework helpers
   */
  'view-homework-helpers': async (target, event) => {
    event.preventDefault();
    const assignmentId = target.dataset.assignmentId;
    if (assignmentId) {
      await handleViewHomeworkHelpers(assignmentId);
    }
  },
  'reload-activity-feed': async (target, event) => {
  const { loadActivityFeed } = await import('./homework.activity_feed.js');
  await loadActivityFeed();
},

'close-streak-warning': async (target, event) => {
  const { closeStreakWarning } = await import('./homework.streak.js');
  closeStreakWarning();
},

'give-reaction': async (target, event) => {
  const submissionId = target.dataset.submissionId;
  const reactionType = target.dataset.reaction;
  
  if (!submissionId || !reactionType) return;
  
  // Import submission module
  const { handleReactionClick } = await import('./homework.submission.js');
  await handleReactionClick(submissionId, reactionType);
},

  /**
   * Offer help with homework
   */
  'offer-help-homework': async (target, event) => {
    event.preventDefault();
    const homeworkId = target.dataset.homeworkId;
    if (homeworkId) {
      await handleOfferHelp(homeworkId);
    }
  },

  /**
   * View homework details
   */
   'view-homework-details': async (target, event) => {
  event.preventDefault();
  const homeworkId = target.dataset.homeworkId;
  const submissionId = target.dataset.submissionId || null;
  if (homeworkId) {
    await openHomeworkDetailsModal(homeworkId, submissionId);
  }
},


  /**
   * View my help submission
   */
  'view-my-help-submission': async (target, event) => {
  event.preventDefault();
  const submissionId = target.dataset.submissionId;
  if (submissionId) {
    await openSubmissionDetailModal(submissionId);
  }
},

'open-submit-solution': async (target, event) => {
  event.preventDefault();
  const submissionId = target.dataset.submissionId;
  if (submissionId) {
    await openSubmissionDetailModal(submissionId);
  }
},

  /**
   * Upload homework resource
   * Triggers the hidden file input
   */
  'upload-homework-resource': async (target, event) => {
    const input = document.getElementById('hw-resource-upload-input');
    if (input) {
      input.click();
    }
  },

  /**
   * Remove resource
   */
  'remove-resource': (target, event) => {
    event.preventDefault();
    const index = parseInt(target.dataset.resourceIndex);
    if (!isNaN(index)) {
      removeResource(index);
    }
  },

  /**
   * View resource
   */
  'view-resource': (target, event) => {
    event.preventDefault();
    const url = target.dataset.resourceUrl;
    const type = target.dataset.resourceType;
    if (url) {
      viewResource(url, type);
    }
  },

  /**
   * Download resource
   */
  'download-resource': (target, event) => {
    event.preventDefault();
    const url = target.dataset.url;
    if (url) {
      downloadResource(url);
    }
  },

  /**
   * View submission details
   */
  'view-submission-details': async (target, event) => {
    event.preventDefault();
    const submissionId = target.dataset.submissionId;
    if (submissionId) {
      await handleViewSubmissionDetails(submissionId);
    }
  },

  /**
   * Reload my homework
   */
  'reload-my-homework': async (target, event) => {
    event.preventDefault();
    refreshCurrentTab();
  },

  // Batch 3: "Offers on my work" / "Helping Others"

  'open-submission-detail': async (target, event) => {
    event.preventDefault();
    const submissionId = target.dataset.submissionId;
    if (submissionId) {
      await openSubmissionDetailModal(submissionId);
    }
  },

  'reload-my-help-requests': async (target, event) => {
    event.preventDefault();
    refreshCurrentTab();
  },

  'reload-helping-with': async (target, event) => {
    event.preventDefault();
    refreshCurrentTab();
  },

  'filter-my-help-requests': async (target, event) => {
    event.preventDefault();
    const status = target.dataset.status || 'all';
    await handleFilterMyHelpRequests(status);
  },

  'filter-helping-with': async (target, event) => {
    event.preventDefault();
    const status = target.dataset.status || 'all';
    await handleFilterHelpingWith(status);
  },

  /**
   * Reload connections homework
   */
  'reload-connections-homework': async (target, event) => {
    event.preventDefault();
    refreshCurrentTab();
  },

  /**
   * Reload stats
   */
  'reload-stats': async (target, event) => {
    event.preventDefault();
    const { loadStatsTab } = await import('./homework.stats.js');
    await loadStatsTab();
  },

  /**
   * Open stats dashboard
   */
  'open-stats-dashboard': async (target, event) => {
    event.preventDefault();
    await openStatsDashboard();
  },

  /**
   * View submission detail (enhanced)
   */
  'view-submission-detail': async (target, event) => {
    event.preventDefault();
    const submissionId = target.dataset.submissionId;
    if (submissionId) {
      await openSubmissionDetailModal(submissionId);
    }
  },

  /**
   * Submit solution form
   */
  'submit-solution-form': async (target, event) => {
    const submissionId = target.dataset.submissionId;
    if (submissionId) {
      await handleSubmitSolution(event, submissionId);
    }
  },

  /**
   * Submit feedback form
   */
  'submit-feedback-form': async (target, event) => {
    // submissionId may be on the button directly OR on the parent form container
    let submissionId = target.dataset.submissionId;
    if (!submissionId) {
      const form = document.getElementById('hw-give-feedback-form');
      submissionId = form?.dataset.submissionId;
    }
    if (submissionId) {
      await handleSubmitFeedback(event, submissionId);
    }
  },

  /**
   * Cancel submission
   */
  'cancel-submission': async (target, event) => {
    event.preventDefault();
    const submissionId = target.dataset.submissionId;
    if (submissionId) {
      await handleCancelSubmission(submissionId);
    }
  },

  /**
   * Quick action
   */
  'quick-action': async (target, event) => {
    event.preventDefault();
    const assignmentId = target.dataset.assignmentId;
    const action = target.dataset.quickAction;
    if (assignmentId && action) {
      await handleQuickAction(assignmentId, action);
    }
  },

  /**
   * Enable bulk mode
   */
  'enable-bulk-mode': (target, event) => {
    event.preventDefault();
    enableBulkMode();
  },

  /**
   * Cancel bulk mode
   */
  'cancel-bulk-mode': (target, event) => {
    event.preventDefault();
    disableBulkMode();
  },

  /**
   * Bulk action
   */
  'bulk-action': async (target, event) => {
    event.preventDefault();
    const action = target.dataset.bulkAction;
    if (action) {
      await handleBulkAction(action);
    }
  },

  /**
   * Select all homework
   */
  'select-all-homework': (target, event) => {
    event.preventDefault();
    selectAllAssignments();
  },

  /**
   * Deselect all homework
   */
  'deselect-all-homework': (target, event) => {
    event.preventDefault();
    deselectAllAssignments();
  },

  /**
   * Trigger solution resource upload
   * Uses a dedicated file input so it doesn't conflict with the create-homework upload
   */
  'trigger-solution-resource-upload': (target, event) => {
    let input = document.getElementById('hw-solution-resource-input');
    if (!input) {
      input = document.createElement('input');
      input.type = 'file';
      input.id = 'hw-solution-resource-input';
      input.style.display = 'none';
      input.accept = 'image/*,video/*,.pdf,.doc,.docx,.txt,.ppt,.pptx,.xls,.xlsx';
      document.body.appendChild(input);
    }

    // Remove old listener and attach a fresh one each time
    const freshInput = input.cloneNode(true);
    input.parentNode.replaceChild(freshInput, input);

    freshInput.addEventListener('change', async function () {
      const file = freshInput.files[0];
      if (!file) return;

      const MAX_FILE_SIZE = 50 * 1024 * 1024;
      if (file.size > MAX_FILE_SIZE) {
        const { showHomeworkToast } = await import('./homework.utils.js');
        showHomeworkToast(`File too large (max 50MB)`, 'error');
        return;
      }

      try {
        const { showHomeworkToast } = await import('./homework.utils.js');
        showHomeworkToast('Uploading file...', 'info');

        const { homeworkAPI } = await import('./homework.api.js');
        const response = await homeworkAPI.uploadResource(file);

        if (response.status === 'success') {
          const { homeworkState } = await import('./homework.state.js');
          homeworkState.addUploadedResource(response.data);

          // Update the solution resources preview
          const preview = document.getElementById('hw-solution-resources-preview');
          if (preview) {
            const { renderResourceItem } = await import('./homework.templates.js');
            const resources = homeworkState.getUploadedResources();
            preview.classList.remove('hidden');
            preview.innerHTML = `
              <div class="hw-resources-list">
                <div class="hw-resources-header">Attached Files (${resources.length}):</div>
                ${resources.map((r, i) => renderResourceItem(r, i)).join('')}
              </div>`;
          }

          showHomeworkToast('File attached! ✅', 'success');
        } else {
          throw new Error(response.message || 'Upload failed');
        }
      } catch (err) {
        console.error('Solution resource upload error:', err);
        const { showHomeworkToast } = await import('./homework.utils.js');
        showHomeworkToast(err.message || 'Failed to upload file', 'error');
      }

      // Reset so same file can be re-selected
      freshInput.value = '';
    });

    freshInput.click();
  },

  /**
    const input = document.getElementById('hw-resource-upload-input');
    if (input) {
      input.click();
    }
  },

  /**
   * Open submit solution modal (legacy - opens enhanced detail instead)
   */
  'open-submit-solution-modal': async (target, event) => {
    event.preventDefault();
    const submissionId = target.dataset.submissionId;
    if (submissionId) {
      await openSubmissionDetailModal(submissionId);
    }
  },

  /**
   * Open give feedback modal (legacy - opens enhanced detail instead)
   */
  'open-give-feedback-modal': async (target, event) => {
    event.preventDefault();
    const submissionId = target.dataset.submissionId;
    if (submissionId) {
      await openSubmissionDetailModal(submissionId);
    }
  }
};
